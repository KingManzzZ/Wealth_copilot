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

这也是本项目"工具调用兜底"（``问题文档/Agent-工具调用可靠性.md``）的隐含前提：
所有校验都假设"工具结果来自 ``route()``"。服务端读库才能关闭这个后门。

## 并发

同一会话同时只允许一个生成中的流（进程内锁）。不加保护时两个流会交错追加，
导致 ``assistant(tool_calls)`` 与 ``tool`` 消息错配，回放时配对校验失败。

> 已知局限：进程内锁只在单进程有效。本项目单用户、单 worker 部署，
> 足够；将来上多 worker 需换成数据库行锁或 Redis 锁。
"""

import json
import threading
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core import chat_stream
from core.agent.context import prepare_history
from core.services.conversations import (
    append_messages,
    create_conversation,
    get_conversation_messages,
)
from core.services.replay import INTERRUPTED_PAYLOAD, normalize_history

router = APIRouter()

_DONE_FRAME = "data: [DONE]\n\n"

#: 正在生成中的会话 id。进程内互斥，见模块文档的"并发"一节。
_ACTIVE: set = set()
_ACTIVE_LOCK = threading.Lock()


def sse_(event: dict) -> str:
    """把一个事件序列化成 SSE 帧。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


class ChatRequest(BaseModel):
    message: str
    #: 会话 id。**新前端必传**；为空时走兼容模式或新建会话（见下）。
    conversation_id: int | None = None
    #: 已废弃：仅兼容旧前端。带 ``conversation_id`` 时被忽略。
    history: list | None = None


def _acquire(conversation_id: int) -> bool:
    with _ACTIVE_LOCK:
        if conversation_id in _ACTIVE:
            return False
        _ACTIVE.add(conversation_id)
        return True


def _release(conversation_id: int) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE.discard(conversation_id)


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

    def feed(self, event: dict) -> None:
        etype = event.get("type")

        if etype == "answer":
            self._content.append(event.get("content") or "")
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


def _persist(conversation_id: int, turn: _AssistantTurn) -> None:
    """落库助手消息。失败只记日志——不能影响已经发给用户的流。"""
    message = turn.to_message()
    if message is None:
        return
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
def chat(request: ChatRequest):
    """聊天入口。同步 def —— SQLite 访问是阻塞 I/O，交给 FastAPI 线程池执行。"""
    conversation_id = request.conversation_id

    # 无会话 id 但带了 history：旧前端，走兼容路径
    if conversation_id is None and request.history is not None:
        return _legacy_response(request)

    # 两者都没有：新会话（由后端创建）
    if conversation_id is None:
        conversation_id = create_conversation()["id"]
    elif get_conversation_messages(conversation_id) is None:
        raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")

    if not _acquire(conversation_id):
        raise HTTPException(
            status_code=409,
            detail=f"会话 {conversation_id} 正在生成中，请等待上一轮完成",
        )

    try:
        # 顺序很重要：先读历史（**不含本轮用户消息**，chat_stream 会自己追加），
        # 再把用户消息落库。这样断流时用户输入不丢。
        rows = get_conversation_messages(conversation_id) or []
        history, info = prepare_history(rows)
        print(
            f"[chat] 会话 {conversation_id} 上下文：行 {info['rows']} "
            f"掩码 {info['masked']} 丢弃单元 {info['dropped_units']} "
            f"估算 {info['tokens']}/{info['budget']}"
        )
        append_messages(conversation_id, [{"role": "user", "content": request.message}])
    except Exception:
        _release(conversation_id)
        raise

    def event_stream():
        turn = _AssistantTurn()
        try:
            for event in chat_stream(request.message, history):
                turn.feed(event)
                yield sse_(event)
            yield _DONE_FRAME
        except Exception as exc:  # noqa: BLE001
            yield sse_({"type": "error", "message": str(exc)})
            yield _DONE_FRAME
        finally:
            # 不在 finally 里 yield（客户端断开会触发 GeneratorExit，yield 会报错），
            # 只做收尾：落库 + 释放锁。覆盖正常结束、异常、以及断流三种路径。
            _persist(conversation_id, turn)
            _release(conversation_id)

    return _streaming_response(event_stream)
