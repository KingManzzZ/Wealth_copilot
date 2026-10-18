"""
一轮生成的取消令牌。

## 为什么需要它（不是"顺手加个开关"）

`api/routes/chat.py` 原来的清理（落库 + 释放会话锁 + 关流）挂在**同步生成器的
``finally``** 上。实测（Starlette 1.6.0）两条断连路径的行为并不一样：

- ASGI spec ≥ 2.4：``send`` 抛 OSError，生成器被正常关闭，``finally`` 立即执行；
- ASGI spec < 2.4：task group 取消，**同步生成器不会被 ``close()``**，
  ``finally`` 要等**循环 GC** 才跑（实测 0.6s 内不执行，``gc.collect()`` 后 0.85s 才跑）。

后者的代价是真实的三笔账：

1. **会话锁不释放** → 同一会话再发消息一直 409；
2. **半截回答不落库** → 前端显示过、库里没有，"记忆"两头不一致；
3. **``openai`` 的流不关** → 供应商那边的 completion 还在生成，token 继续计费。

所以取消不能依赖"生成器被终结"，必须是一个**显式、线程安全、可等待**的信号：
路由创建它，agent 循环在检查点读取它，worker 线程在 ``finally`` 里据此收敛并落库。

## 它不是错误

``TurnCancelled`` 是**控制流**，不是失败：路由不会把它变成 ``error`` 事件，
而是落一条 ``interrupted`` 消息 + 发一个 ``cancelled`` 事件。
"""

from __future__ import annotations

import threading
import time


class TurnCancelled(Exception):
    """本轮被用户中止或连接断开。**控制流异常，不是错误。**"""

    def __init__(self, reason: str = "client") -> None:
        super().__init__(f"turn cancelled: {reason}")
        self.reason = reason


class TurnCancel:
    """一轮生成的生命周期句柄：取消信号 + 完成信号。

    两个 Event 都是 ``threading`` 原语：agent 跑在 worker 线程里，
    取消请求来自 HTTP 线程池，完成信号由 worker 在 ``finally`` 里置位。
    """

    def __init__(self, turn_id: str, conversation_id: int) -> None:
        self.turn_id = turn_id
        self.conversation_id = conversation_id
        self._lock = threading.Lock()
        self._cancelled = threading.Event()
        self._done = threading.Event()
        self._reason = ""
        self._cancelled_at: float | None = None

    # ------------------------------------------------------------------
    # 只读状态
    # ------------------------------------------------------------------

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    @property
    def cancelled_at(self) -> float | None:
        with self._lock:
            return self._cancelled_at

    # ------------------------------------------------------------------
    # 状态迁移
    # ------------------------------------------------------------------

    def cancel(self, reason: str = "client") -> bool:
        """置位取消信号；返回"这次是否真的由我置位"（幂等）。

        先到先得：用户点了停止之后即使连接又断开，reason 仍保留 ``client``，
        便于区分"用户主动停"和"掉线"。
        """
        with self._lock:
            if self._cancelled.is_set():
                return False
            self._reason = reason
            self._cancelled_at = time.monotonic()
            self._cancelled.set()
            return True

    def check(self) -> None:
        """检查点：已取消就抛 ``TurnCancelled``。

        agent 循环在四个位置调用它：每轮开头、每个流式 chunk、每个工具执行前。
        """
        if self._cancelled.is_set():
            raise TurnCancelled(self._reason or "client")

    def mark_done(self) -> None:
        """worker 收尾完成（已落库、已释放锁）后置位。"""
        self._done.set()

    def wait_done(self, timeout: float) -> bool:
        """等待收尾完成；超时返回 False（不抛异常）。

        取消接口用它把"请求已受理"和"服务端真的停了"合并成一次同步返回，
        避免前端刚停下就重发导致 409。
        """
        return self._done.wait(timeout)
