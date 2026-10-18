"""真 ASGI 路由层的取消行为：409、取消接口、断连探测、落库、锁释放。

这一层是唯一能验证"worker 线程 + asyncio.Queue + 断连 watcher"真的接线正确的地方。
用 ``httpx.ASGITransport`` 起真 FastAPI，用假 LLM 控制节奏；不联网、不碰真实库。

**依赖**：需要 ``fastapi`` 与 ``httpx``（后者用于 ASGI 客户端）。两者都不在时，
整份文件跳过并在报告里说明原因——不要让"没装测试依赖"表现为 12 个错误。
"""

import asyncio
import json

import _support
from run_tests import Skip, eq, true

try:
    import httpx
    from fastapi import FastAPI
    from starlette.requests import Request

    from api.routes.chat import router as chat_router
    from core.services import conversations as conv
except Exception as exc:  # noqa: BLE001
    raise Skip(f"需要 fastapi + httpx：{type(exc).__name__}: {exc}")


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(chat_router, prefix="/api")
    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        timeout=30.0,
    )


def _slow_round(n: int = 40):
    """一轮慢速回答：每个 chunk 之间隔 10ms（由 FakeOpenAI 的 delay 控制）。"""
    return [[_support.chunk(_support.delta(content=f"字{i}")) for i in range(n)]]


def _events(body: str) -> list[dict]:
    out = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            continue
        try:
            out.append(json.loads(payload))
        except json.JSONDecodeError:
            pass
    return out


def _count(body: str, etype: str) -> int:
    return sum(1 for event in _events(body) if event.get("type") == etype)


def check_running_turn_blocks_same_conversation():
    async def scenario() -> None:
        with _support.temp_database():
            conversation_id = conv.create_conversation()["id"]
            fake = _support.FakeOpenAI(_slow_round(40), delay=0.01)
            restore = _support.install_fake_client(fake)
            try:
                async with _client(_make_app()) as client:
                    chat = asyncio.create_task(
                        client.post(
                            "/api/chat",
                            json={
                                "message": "慢慢说",
                                "conversation_id": conversation_id,
                                "turn_id": "turn-409",
                            },
                        )
                    )
                    await asyncio.sleep(0.15)
                    busy = await client.post(
                        "/api/chat",
                        json={
                            "message": "插一条",
                            "conversation_id": conversation_id,
                            "turn_id": "turn-busy",
                        },
                    )
                    eq(busy.status_code, 409, "同会话并发应返回 409")

                    await client.post("/api/chat/cancel", json={"turn_id": "turn-409"})
                    response = await chat
                    eq(response.status_code, 200, "被取消的流仍应以 200 正常收尾")
            finally:
                restore()

    asyncio.run(scenario())


def check_explicit_cancel_truncates_and_persists():
    async def scenario() -> None:
        with _support.temp_database():
            conversation_id = conv.create_conversation()["id"]
            fake = _support.FakeOpenAI(_slow_round(40), delay=0.01)
            restore = _support.install_fake_client(fake)
            try:
                async with _client(_make_app()) as client:
                    chat = asyncio.create_task(
                        client.post(
                            "/api/chat",
                            json={
                                "message": "慢慢说",
                                "conversation_id": conversation_id,
                                "turn_id": "turn-cancel",
                            },
                        )
                    )
                    await asyncio.sleep(0.15)

                    cancelled = await client.post(
                        "/api/chat/cancel", json={"turn_id": "turn-cancel"}
                    )
                    payload = cancelled.json()
                    eq(cancelled.status_code, 200, "取消接口应返回 200")
                    eq(payload["cancelled"], True, "应报告已取消")
                    eq(payload["done"], True, "应等到 worker 收尾（落库 + 解锁）")
                    eq(payload["reason"], "client", "用户主动取消的 reason 应为 client")

                    response = await chat
                    events = _events(response.text)
                    eq(events[0].get("type"), "turn", "首帧应是 turn（带 turn_id）")
                    eq(events[0].get("turn_id"), "turn-cancel", "首帧应回显 turn_id")

                    answers = _count(response.text, "answer")
                    true(0 < answers < 40, f"回答应被截断，实际 {answers} 个 chunk")
                    eq(_count(response.text, "cancelled"), 1, "应有一条 cancelled 事件")
                    eq(fake.call_count, 1, "取消后不应再发起 LLM 请求")

            finally:
                restore()

            rows = conv.get_conversation_messages(conversation_id)
            assistants = [row for row in rows if row["role"] == "assistant"]
            eq(len(assistants), 1, "应落库一条 assistant（被中止的也要落）")
            eq(assistants[0]["interrupted"], True, "interrupted 应为 True")
            eq(assistants[0]["stop_reason"], "client", "stop_reason 应为 client")
            true(bool(assistants[0]["content"]), "已生成的内容应保留")

    asyncio.run(scenario())


def check_cancel_then_new_turn_is_allowed():
    async def scenario() -> None:
        with _support.temp_database():
            conversation_id = conv.create_conversation()["id"]
            restore = _support.install_fake_client(
                _support.FakeOpenAI(_slow_round(40), delay=0.01)
            )
            try:
                async with _client(_make_app()) as client:
                    chat = asyncio.create_task(
                        client.post(
                            "/api/chat",
                            json={
                                "message": "慢慢说",
                                "conversation_id": conversation_id,
                                "turn_id": "turn-a",
                            },
                        )
                    )
                    await asyncio.sleep(0.15)
                    await client.post("/api/chat/cancel", json={"turn_id": "turn-a"})
                    await chat

                    # 取消完成后立刻开新一轮：锁必须已经释放
                    restore()
                    restore = _support.install_fake_client(
                        _support.FakeOpenAI(_support.answer_script("好"))
                    )
                    again = await client.post(
                        "/api/chat",
                        json={
                            "message": "再来",
                            "conversation_id": conversation_id,
                            "turn_id": "turn-b",
                        },
                    )
                    eq(again.status_code, 200, "取消后应立即允许新一轮（不再 409）")
                    true(_count(again.text, "done") >= 1, "新一轮应正常结束")
            finally:
                restore()

    asyncio.run(scenario())


def check_cancel_unknown_turn_is_idempotent():
    async def scenario() -> None:
        async with _client(_make_app()) as client:
            response = await client.post(
                "/api/chat/cancel", json={"turn_id": "turn-never-existed"}
            )
            eq(response.status_code, 200, "未知 turn 也应幂等返回 200")
            payload = response.json()
            eq(payload["cancelled"], False, "未在运行：cancelled 应为 False")
            eq(payload["done"], True, "未在运行：done 应为 True")
            eq(payload["reason"], "not_running", "应说明原因")

    asyncio.run(scenario())


def check_disconnect_watcher_cancels_turn():
    async def scenario() -> None:
        flag = {"on": False}
        original = Request.is_disconnected

        async def fake_is_disconnected(self):  # noqa: ANN001
            return flag["on"]

        Request.is_disconnected = fake_is_disconnected
        try:
            with _support.temp_database():
                conversation_id = conv.create_conversation()["id"]
                fake = _support.FakeOpenAI(_slow_round(40), delay=0.01)
                restore = _support.install_fake_client(fake)
                try:
                    async with _client(_make_app()) as client:
                        chat = asyncio.create_task(
                            client.post(
                                "/api/chat",
                                json={
                                    "message": "慢慢说",
                                    "conversation_id": conversation_id,
                                    "turn_id": "turn-disc",
                                },
                            )
                        )
                        await asyncio.sleep(0.15)
                        flag["on"] = True

                        response = await chat
                        answers = _count(response.text, "answer")
                        true(answers < 40, f"断连后应提前结束，实际 {answers} 个 chunk")
                        cancelled = [
                            e for e in _events(response.text) if e["type"] == "cancelled"
                        ]
                        eq(len(cancelled), 1, "应有一条 cancelled 事件")
                        eq(cancelled[0]["reason"], "disconnect", "reason 应为 disconnect")
                        eq(fake.call_count, 1, "断连后不应再发起 LLM 请求")
                finally:
                    restore()

                rows = conv.get_conversation_messages(conversation_id)
                assistants = [row for row in rows if row["role"] == "assistant"]
                eq(assistants[0]["interrupted"], True, "断连同样要标记 interrupted")
                eq(assistants[0]["stop_reason"], "disconnect", "stop_reason 应为 disconnect")
        finally:
            Request.is_disconnected = original

    asyncio.run(scenario())
