"""
聊天接口（路由层）。

## 两种模式（会话历史归谁管）

**服务端权威（推荐，传 ``conversation_id``）**

- 后端从 ``messages`` 表读历史 → ``replay`` 展开成协议形态 → ``context`` 裁剪；
- 用户消息在**请求开始**就落库（断流不丢输入），助手消息在**流结束时**落库；
- 前端不再需要自己拼历史，也无法伪造历史（这是安全边界，见下）。

**兼容模式（只传 ``history``，无 ``conversation_id``）**

- 旧前端仍在用：把前端给的 history 归一化后直接用，**不读库、不落库**；
- 每次请求打一条告警，便于统计还有多少流量在旧路径。

## 为什么"历史必须由服务端提供"

``history`` 是客户端可控输入。若后端直接采信，客户端可以塞一条伪造的
``role="tool"`` 消息——它**不经过** ``tools_route.route()``、不触发参数校验、
不进未知工具熔断，等于绕过整套工具安全边界。

## 并发

同一会话同时只允许一个生成中的流（进程内注册表）。不加保护时两个流会交错追加，
导致 ``assistant(tool_calls)`` 与 ``tool`` 消息错配，回放时配对校验失败。

## 取消（停止生成）

**为什么不能把收尾挂在生成器的 ``finally`` 上**：``StreamingResponse`` 会把同步生成器
丢进线程池（``iterate_in_threadpool``），而它**没有** ``finally: iterator.close()``。
实测（Starlette 1.6.0，task group 取消路径）客户端断开后生成器不会被关闭，
``finally`` 要等**循环 GC** 才执行——期间会话锁一直占着（后续消息 409）、
半截回答不落库、``openai`` 的流也没关（token 继续计费）。

所以改成 **worker 线程生产 / async 消费** 的结构：

```
worker 线程：chat_stream(...) → queue  ──┐
                                          ├─ async 生成器：queue → SSE
finally：_persist + _unregister + mark_done
```

线程的 ``finally`` 是确定性的：线程结束就是结束，不受 GC 影响。

取消有三个触发源，都要接：

1. **显式接口** ``POST /api/chat/cancel {turn_id}``（用户点"停止"，确定性最强）；
2. **断连探测**：后台任务轮询 ``request.is_disconnected()``（关标签页 / 断网）；
3. **消费者退出**：SSE 生成器的 ``finally`` 再兜一次（客户端先断的情况）。

三者都只是 ``TurnCancel.cancel()``，幂等，先到先得。
"""

import asyncio
import json
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core import chat_stream
from core.agent import turn_context
from core.agent.cancel import TurnCancel, TurnCancelled
from core.agent.context import prepare_history
from core.services.conversations import (
    append_messages,
    create_conversation,
    get_conversation_messages,
)
from core.services import turn_state
from core.services.replay import INTERRUPTED_PAYLOAD, normalize_history

router = APIRouter()

_DONE_FRAME = "data: [DONE]\n\n"

#: 取消接口最多等多久让 worker 收尾（落库 + 释放锁）。
#: 取 5s：正常轮次远小于这个值；若 worker 正卡在一次 RAG 检索（1~3s）里，
#: 也足够等到它退出检查点。
CANCEL_WAIT_SECONDS = 5.0

#: 断连轮询间隔。取 0.2s：比它更密没有收益（用户感知不到），
#: 更疏则断连后要多烧一点 token。
DISCONNECT_POLL_SECONDS = 0.2

#: conversation_id → 当前进行中的轮次（用于 409 互斥）
_ACTIVE: dict[int, TurnCancel] = {}
#: turn_id → 当前进行中的轮次（用于取消接口精确定位）
_TURNS: dict[str, TurnCancel] = {}
_REGISTRY_LOCK = threading.Lock()


def sse_(event: dict) -> str:
    """把一个事件序列化成 SSE 帧。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


class ChatRequest(BaseModel):
    message: str
    #: 会话 id。**新前端必传**；为空时走兼容模式或新建会话（见下）。
    conversation_id: int | None = None
    #: 本轮 id，由前端生成（UUID）。取消接口按它定位；缺省时后端补一个。
    turn_id: str | None = None
    #: 已废弃：仅兼容旧前端。带 ``conversation_id`` 时被忽略。
    history: list | None = None


class CancelRequest(BaseModel):
    turn_id: str


# ---------------------------------------------------------------------------
# 轮次注册表
# ---------------------------------------------------------------------------


def _register(turn: TurnCancel) -> TurnCancel | None:
    """登记一轮生成；该会话已有进行中的轮次时返回那一个（调用方据此返回 409）。"""
    with _REGISTRY_LOCK:
        existing = _ACTIVE.get(turn.conversation_id)
        if existing is not None:
            return existing
        _ACTIVE[turn.conversation_id] = turn
        _TURNS[turn.turn_id] = turn
        return None


def _unregister(turn: TurnCancel) -> None:
    with _REGISTRY_LOCK:
        _TURNS.pop(turn.turn_id, None)
        if _ACTIVE.get(turn.conversation_id) is turn:
            _ACTIVE.pop(turn.conversation_id, None)


def _lookup(turn_id: str) -> TurnCancel | None:
    with _REGISTRY_LOCK:
        return _TURNS.get(turn_id)


def stop_turn(conversation_id: int, reason: str = "withdraw") -> bool:
    """让某会话正在进行的一轮停下来，并等它收尾（落库 + 写集 + 解锁）。

    返回 ``True`` 表示该会话当前没有进行中的轮次（或已收尾），可以安全撤回。
    撤回接口先调它，再读 `turn_writes`——否则可能读到写集的中间状态。
    """
    with _REGISTRY_LOCK:
        turn = _ACTIVE.get(conversation_id)
    if turn is None:
        return True
    turn.cancel(reason)
    return turn.wait_done(CANCEL_WAIT_SECONDS)


def _silence_task(task: "asyncio.Task") -> None:
    """把后台任务的异常消费掉，避免 "Task exception was never retrieved" 噪音。

    断连探测是尽力而为的：它失败只影响"掉线能不能自动停"，不该在日志里刷栈。
    """
    if task.cancelled():
        return
    task.exception()


def _now_ms() -> int:
    return int(time.time() * 1000)


class _AssistantTurn:
    """把 SSE 事件累积成一条可落库的 assistant 消息（**UI 形态**）。

    落库存 UI 形态是刻意的：前端要靠它做工具卡片折叠。
    模型回放时再由 ``replay.expand_to_model_messages`` 展开成协议形态——
    两种用途各用各的形态，不必二选一。
    """

    def __init__(self) -> None:
        self._content: list = []
        self._calls: dict = {}
        self.rounds = 0

    def feed(self, event: dict) -> None:
        etype = event.get("type")

        if etype == "answer":
            self._content.append(event.get("content") or "")
        elif etype == "round":
            self.rounds = max(self.rounds, int(event.get("round") or 0))
        elif etype == "tool_call":
            call_id = event.get("id")
            if not call_id:
                return
            self._calls[call_id] = {
                "id": call_id,
                "name": event.get("name") or "",
                "arguments": event.get("arguments") or {},
                "status": "running",
                "startedAt": _now_ms(),
            }
        elif etype == "tool_result":
            call = self._calls.get(event.get("id"))
            if call is None:
                return
            call["result"] = event.get("result")
            call["status"] = event.get("status") or "error"
            call["endedAt"] = _now_ms()

    @property
    def content_chars(self) -> int:
        return len("".join(self._content))

    @property
    def tool_count(self) -> int:
        return len(self._calls)

    def to_message(self) -> dict | None:
        """产出待落库的 assistant 消息；空壳返回 None（不落空行）。"""
        content = "".join(self._content)
        calls = list(self._calls.values())
        if not content and not calls:
            return None

        # 断流兜底：仍停在 running（或压根没有 result）的调用收敛成"未完成"。
        # 若把 running 原样落库，下次续聊就会产生"有 tool_calls、无 tool 应答"的历史 → 400，
        # 且用户只看到"请求失败"，真实原因完全不可见。
        now = _now_ms()
        for call in calls:
            if call["status"] == "running" or not call.get("result"):
                call["status"] = "error"
                call["result"] = INTERRUPTED_PAYLOAD
                call["endedAt"] = now

        message: dict = {"role": "assistant", "content": content}
        if calls:
            message["tool_calls"] = calls
        return message


def _persist(
    conversation_id: int,
    turn: _AssistantTurn,
    cancel: TurnCancel,
    turn_id: str,
) -> None:
    """落库助手消息。失败只记日志——不能影响已经发给用户的流。

    被中止的轮次会带上 ``interrupted`` / ``stop_reason``：它仍要落库（UI 要展示），
    但回放时会整轮跳过（见 ``services/replay.py``）。
    ``turn_id`` 是"撤回这一轮"的锚点：用户消息与助手消息共用它。
    """
    message = turn.to_message()
    if message is None:
        return
    message["turn_id"] = turn_id
    if cancel.cancelled:
        message["interrupted"] = True
        message["stop_reason"] = cancel.reason or "client"
    try:
        append_messages(conversation_id, [message])
    except Exception as exc:  # noqa: BLE001
        print(
            f"[chat] ⚠️ 会话 {conversation_id} 助手消息落库失败："
            f"{type(exc).__name__}: {exc}"
        )


def _streaming_response(generator_factory) -> StreamingResponse:
    return StreamingResponse(
        generator_factory(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


def _legacy_response(request: ChatRequest) -> StreamingResponse:
    """兼容模式：用前端给的 history，不读库、不落库。"""
    history = normalize_history(request.history or [])
    print(
        "[chat] ⚠️ 收到无 conversation_id 的 history 请求，走兼容路径（不落库）；"
        "前端应改为传 conversation_id"
    )

    def event_stream():
        try:
            for event in chat_stream(request.message, history):
                yield sse_(event)
            yield _DONE_FRAME
        except Exception as exc:  # noqa: BLE001
            yield sse_({"type": "error", "message": str(exc)})
            yield _DONE_FRAME

    return _streaming_response(event_stream)


@router.post("/chat")
async def chat(request: Request, payload: ChatRequest):
    """聊天入口。

    ``async def`` 是必要的：只有异步上下文才能 ``await request.is_disconnected()``
    （断连探测）并调度 worker 线程产出的队列。耗时的生成仍在线程里跑，
    不占事件循环。
    """
    conversation_id = payload.conversation_id

    # 无会话 id 但带了 history：旧前端，走兼容路径
    if conversation_id is None and payload.history is not None:
        return _legacy_response(payload)

    # 两者都没有：新会话（由后端创建）
    #
    # SQLite 是阻塞 I/O，而这里已经是 async 上下文 —— 必须丢进线程池，
    # 否则一次毫秒级的写锁等待会卡住整个事件循环（旧实现是同步 def，
    # 靠 FastAPI 自动把它放进线程池；改成 async def 后这个保护就没了）。
    if conversation_id is None:
        conversation_id = (await asyncio.to_thread(create_conversation))["id"]
    elif (await asyncio.to_thread(get_conversation_messages, conversation_id)) is None:
        raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")

    turn_id = (payload.turn_id or "").strip()[:64] or uuid.uuid4().hex
    turn = TurnCancel(turn_id=turn_id, conversation_id=conversation_id)

    if _register(turn) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"会话 {conversation_id} 正在生成中，请等待上一轮完成或先取消",
        )

    try:
        # 顺序很重要：先读历史（**不含本轮用户消息**，chat_stream 会自己追加），
        # 再把用户消息落库。这样断流时用户输入不丢。
        rows = await asyncio.to_thread(get_conversation_messages, conversation_id) or []
        history, info = prepare_history(rows)
        print(
            f"[chat] 会话 {conversation_id} 上下文：行 {info['rows']} "
            f"掩码 {info['masked']} 丢弃单元 {info['dropped_units']} "
            f"估算 {info['tokens']}/{info['budget']}"
        )
        await asyncio.to_thread(
            append_messages,
            conversation_id,
            [{"role": "user", "content": payload.message, "turn_id": turn_id}],
        )
        # 轮前快照：撤回时靠它把这个轮次写过的行还原。
        # 快照失败不阻断生成——只是这一轮之后不可撤回数据（记日志即可）。
        try:
            before = await asyncio.to_thread(
                turn_state.begin, turn_id, conversation_id
            )
        except Exception as exc:  # noqa: BLE001
            print(
                f"[chat] turn={turn_id} ⚠️ 轮前快照失败，本轮将不可撤回数据："
                f"{type(exc).__name__}: {exc}"
            )
            before = None
    except Exception:
        _unregister(turn)
        raise

    # ------------------------------------------------------------------
    # worker 线程 → asyncio.Queue → SSE
    # ------------------------------------------------------------------
    loop = asyncio.get_running_loop()
    events: asyncio.Queue = asyncio.Queue()

    def push(item) -> None:
        """跨线程投递事件。事件循环已关闭（进程退出中）时静默丢弃。"""
        try:
            loop.call_soon_threadsafe(events.put_nowait, item)
        except RuntimeError:
            pass

    state = _AssistantTurn()

    def worker() -> None:
        # 提案工具（request_withdraw）靠线程本地上下文知道"当前会话是哪一轮"
        turn_context.bind(conversation_id, turn_id)
        started = time.monotonic()
        try:
            for event in chat_stream(payload.message, history, cancel=turn):
                state.feed(event)
                push(("event", event))
        except TurnCancelled as exc:
            # 控制流，不是错误：不产生 error 事件
            push(("cancelled", {"reason": exc.reason}))
        except Exception as exc:  # noqa: BLE001
            print(f"[chat] turn={turn_id} 生成失败：{type(exc).__name__}: {exc}")
            push(("error", str(exc)))
        finally:
            # 收尾放在线程里，**不依赖生成器被 close**（见模块文档）。
            # 嵌套 try/finally 保证：即使落库本身抛错，锁也一定释放、done 一定置位，
            # 否则前端会一直 409，且取消接口要等到超时才返回。
            try:
                _persist(conversation_id, state, turn, turn_id)
            finally:
                # 轮后差异：把这一轮写过的行记进 turn_writes，撤回时按它还原。
                # 放在 mark_done 之前——撤回接口等到 done 之后就能读到完整的写集。
                if before is not None:
                    try:
                        turn_state.finish(
                            turn_id,
                            conversation_id,
                            before,
                            "interrupted" if turn.cancelled else "completed",
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(
                            f"[chat] turn={turn_id} ⚠️ 写集记录失败："
                            f"{type(exc).__name__}: {exc}"
                        )
                _unregister(turn)
                turn.mark_done()
                turn_context.clear()
                print(
                    f"[chat] turn={turn_id} 会话={conversation_id} "
                    f"{'中止' if turn.cancelled else '完成'} "
                    f"轮次={state.rounds} 工具={state.tool_count} "
                    f"字符={state.content_chars} 耗时={time.monotonic() - started:.1f}s "
                    f"reason={turn.reason or '-'}"
                )
                push(("__end__", None))

    threading.Thread(target=worker, name=f"turn-{turn_id}", daemon=True).start()

    async def watch_disconnect() -> None:
        """轮询断连。覆盖"关标签页/断网"这类用户不会点取消的场景。"""
        try:
            while not turn.done:
                if await request.is_disconnected():
                    turn.cancel("disconnect")
                    return
                await asyncio.sleep(DISCONNECT_POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 探测本身失败不该影响生成：交给 SSE 生成器的 finally 兜底
            return

    watcher = asyncio.create_task(watch_disconnect(), name=f"watch-{turn_id}")
    watcher.add_done_callback(_silence_task)

    async def sse():
        try:
            # 首帧带上 turn_id：即使客户端没传，也能从流里拿到用于取消的 id
            yield sse_({"type": "turn", "turn_id": turn_id})
            while True:
                kind, data = await events.get()
                if kind == "event":
                    yield sse_(data)
                elif kind == "cancelled":
                    yield sse_(
                        {
                            "type": "cancelled",
                            "turn_id": turn_id,
                            "reason": data.get("reason") or "client",
                        }
                    )
                elif kind == "error":
                    yield sse_({"type": "error", "message": data})
                else:
                    break
            yield _DONE_FRAME
        finally:
            # 消费者先退出（客户端断开）：让 worker 尽快在下一个检查点收敛。
            # 正常结束时 worker 已 mark_done，这里的 cancel 只影响已完成的任务，无害。
            turn.cancel("disconnect")
            watcher.cancel()

    return StreamingResponse(
        sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post("/chat/cancel")
def cancel_chat(payload: CancelRequest):
    """取消一轮生成（幂等）。

    返回 ``done``：worker 是否**已经收尾**（落库 + 释放锁）。前端拿到 ``done=true``
    再允许发送下一条，否则立刻重发会撞上 409。
    """
    turn_id = (payload.turn_id or "").strip()
    if not turn_id:
        raise HTTPException(status_code=400, detail="turn_id 必填")

    turn = _lookup(turn_id)
    if turn is None:
        # 已经结束 / 从未存在：幂等返回，不报错
        return {
            "cancelled": False,
            "done": True,
            "turn_id": turn_id,
            "reason": "not_running",
        }

    turn.cancel("client")
    done = turn.wait_done(CANCEL_WAIT_SECONDS)
    return {
        "cancelled": True,
        "done": done,
        "turn_id": turn_id,
        "reason": turn.reason or "client",
    }
