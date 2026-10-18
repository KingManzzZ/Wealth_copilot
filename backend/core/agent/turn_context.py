"""
当前轮次的线程上下文。

## 为什么需要它

`request_withdraw` 这类**提案工具**要回答"撤回哪一轮"，但工具签名里没有
`conversation_id`：那是请求级信息，不该让模型每次传一遍（也传不对）。

worker 线程在开始生成前把 `(conversation_id, current_turn_id)` 绑定到线程本地，
工具通过 `snapshot()` 读取。线程结束即失效，不需要清理全局状态。

## 为什么用线程本地而不是参数

`tools_route.route(name, arguments)` 的签名是固定的（测试与契约都依赖它），
给每个工具加一个隐藏参数会污染工具契约。线程本地是"环境信息"，与工具参数正交。
"""

from __future__ import annotations

import threading

_state = threading.local()


def bind(conversation_id: int, turn_id: str) -> None:
    _state.conversation_id = conversation_id
    _state.turn_id = turn_id


def clear() -> None:
    for attr in ("conversation_id", "turn_id"):
        if hasattr(_state, attr):
            delattr(_state, attr)


def snapshot() -> tuple[int, str] | None:
    """返回 (conversation_id, current_turn_id)；不在轮次内时返回 None。"""
    conversation_id = getattr(_state, "conversation_id", None)
    turn_id = getattr(_state, "turn_id", None)
    if conversation_id is None or turn_id is None:
        return None
    return int(conversation_id), str(turn_id)
