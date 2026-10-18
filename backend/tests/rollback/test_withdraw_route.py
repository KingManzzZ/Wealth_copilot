"""撤回接口的路由层测试（需要 fastapi + httpx）。"""

import asyncio

import _support
from run_tests import Skip, eq, true

try:
    import httpx
    from fastapi import FastAPI

    from api.routes.conversations import router as conversations_router
except Exception as exc:  # noqa: BLE001
    raise Skip(f"需要 fastapi + httpx：{type(exc).__name__}: {exc}")

from core.services import conversations as conv
from core.services import turn_state
from core.services.transactions import add_transaction, list_transactions


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(conversations_router, prefix="/api")
    return app


def check_withdraw_endpoint_roundtrip():
    async def scenario():
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=30
        ) as client:
            # 必须用一次性数据库：路由测试也能写到真实账本，之前漏了这一层
            with _support.temp_database():
                cid = conv.create_conversation()["id"]
                before = turn_state.begin("t-route", cid)
                add_transaction("expense", 42, "餐饮", "2026-10-18T12:00:00")
                turn_state.finish("t-route", cid, before, "completed")
                conv.append_messages(
                    cid, [{"role": "user", "content": "记一笔 42", "turn_id": "t-route"}]
                )
                conv.append_messages(
                    cid, [{"role": "assistant", "content": "已记录", "turn_id": "t-route"}]
                )

                resp = await client.post(
                    f"/api/conversations/{cid}/withdraw", json={"turn_id": "t-route"}
                )
                eq(resp.status_code, 200, "撤回应返回 200")
                payload = resp.json()
                eq(payload["user_text"], "记一笔 42", "应回填用户原话")
                eq(payload["rollback"]["compensated"], 1, "应撤销一条写")
                eq(list_transactions(), [], "数据应还原")

                listed = await client.get(f"/api/conversations/{cid}/messages")
                eq(listed.json(), [], "撤回后消息列表应为空")

                again = await client.post(
                    f"/api/conversations/{cid}/withdraw", json={"turn_id": "t-route"}
                )
                eq(again.status_code, 404, "重复撤回应 404")

                missing = await client.post(
                    f"/api/conversations/{cid}/withdraw", json={"turn_id": "nope"}
                )
                eq(missing.status_code, 404, "未知轮次应 404")

    asyncio.run(scenario())


def check_withdraw_unknown_conversation_is_404():
    async def scenario():
        transport = httpx.ASGITransport(app=_app())
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=30
        ) as client:
            resp = await client.post(
                "/api/conversations/9999/withdraw", json={"turn_id": "x"}
            )
            eq(resp.status_code, 404, "会话不存在应 404")

    asyncio.run(scenario())
