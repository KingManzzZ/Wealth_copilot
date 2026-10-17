"""端到端：真实驱动 ``/api/chat``，校验 SSE 事件流。

**这是唯一能验证「融合真的通了」的测试。** 单元测试各自用替身，只能证明"每个零件对"；
本测试把 FastAPI 应用、SSE 序列化、Agent 循环、工具路由、证据适配层串起来，
用**假 LLM**（不联网）跑完整条链路。

**覆盖的融合点**

1. `main.py` 的 lifespan 能正常启动（RAG 预热失败也不能让服务起不来）
2. `POST /api/chat` 返回 `text/event-stream`
3. 事件按 `data: {...}\\n\\n` 分帧，中文不转义
4. 工具调用轮次产生 `tool_call → tool_start → tool_result`
5. 未知工具 / RAG 不可用时 `status=error`，且**流不中断**
6. 流以字面量 `data: [DONE]` 收尾（前端据此关闭连接）

对应 `问题文档/问题与解决方案.md` 的 P2-16 与 P0-9（本测试的存在就是为了让
"能不能跑"不再依赖人工点页面）。
"""

from __future__ import annotations

import json

import _support
from run_tests import Skip, eq, in_, true


# ======================================================================
# 假 LLM
# ======================================================================
#
# 实现已收敛到 ``_support.py``——与 test_agent_events 共用同一份，不再各抄一份。
# 这里只做薄薄一层别名，保持本文件原有的调用写法；脚本仍比第 6 层简单：
# 回答轮不带思考内容，工具轮的 call id 用共享实现的 call_1（本层不断言 id）。

FakeOpenAI = _support.FakeOpenAI
_delta = _support.Delta
_chunk = _support.chunk
_tool_delta = _support.tool_call_delta


def _answer_script():
    """一轮：回答文本 + 思考内容，无工具调用。

    共享实现每轮带一个 reasoning chunk（第 6 层据此断言 ``thinking`` 事件）；
    本层不断言事件内容，保留即可。
    """
    return _support.answer_script()[:1]


def _tool_script(tool="this_tool_does_not_exist", args="{}"):
    """两轮：第一轮调工具，第二轮给出回答。"""
    return _support.tool_round_script(tool, args)


def _search_script():
    """让模型调用真实的 search_knowledge（RAG 未就绪时会返回 error 载荷）。"""
    return _tool_script("search_knowledge", json.dumps({"query": "个人养老金缴费上限"}))


# ======================================================================
# 应用装配
# ======================================================================


def _make_client(script):
    """构造 TestClient，并把 agent 模块的 LLM 客户端换成假客户端。

    lifespan 会真实执行（含 RAG 预热），因此顺带验证了「预热失败不影响启动」。
    """
    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # noqa: BLE001
        raise Skip(f"缺少 fastapi/httpx（TestClient 不可用）：{type(exc).__name__}: {exc}")

    try:
        import main as main_mod
    except Exception as exc:  # noqa: BLE001
        raise Skip(f"无法 import main（缺少依赖？）：{type(exc).__name__}: {exc}")

    try:
        from core.agent import agent as agent_mod
    except Exception as exc:  # noqa: BLE001
        raise Skip(f"无法 import agent：{type(exc).__name__}: {exc}")

    agent_mod.client = FakeOpenAI(script)
    # TestClient 作为上下文管理器才会触发 lifespan
    return TestClient(main_mod.app), agent_mod


def _parse_sse(text: str) -> tuple[list[dict], bool]:
    """把 SSE 文本解析成事件列表，并返回是否收到 [DONE] 收尾帧。"""
    events: list[dict] = []
    done = False
    for block in text.split("\n\n"):
        block = block.strip()
        if not block.startswith("data:"):
            continue
        payload = block[len("data:") :].strip()
        if payload == "[DONE]":
            done = True
            continue
        events.append(json.loads(payload))
    return events, done


def _chat(client, message: str = "你好", history=None):
    resp = client.post("/api/chat", json={"message": message, "history": history or []})
    return resp


# ======================================================================
# 用例
# ======================================================================

def check_app_starts_and_answers():
    client, _ = _make_client(_answer_script())
    with client:
        resp = _chat(client)
        eq(resp.status_code, 200, "POST /api/chat 应返回 200")
        in_("text/event-stream", resp.headers.get("content-type", ""), "应为 SSE 流")
        events, done = _parse_sse(resp.text)

    true(done, "流应以 [DONE] 收尾")
    types = [e["type"] for e in events]
    in_("round", types)
    in_("answer", types)
    eq(types[-1], "done", "最后一个应用层事件应是 done")


def check_startup_survives_rag_unavailable():
    """RAG 不可用（很可能是当前真实状态）时服务仍必须能起来。

    这是 P0-9 的直接后果：本会话无法执行命令，所以 RAG 依赖/知识库是否就绪
    由使用者环境决定。lifespan 里的预热必须吞异常。
    """
    client, _ = _make_client(_answer_script())
    with client:  # 进入上下文即执行 lifespan，抛异常则本用例失败
        resp = client.get("/api/health")
    eq(resp.status_code, 200, "lifespan 应正常完成且服务可响应")


def check_tool_round_emits_full_sequence():
    client, _ = _make_client(_tool_script())
    with client:
        resp = _chat(client, "帮我查个东西")
        events, done = _parse_sse(resp.text)

    types = [e["type"] for e in events]
    for name in ("tool_call", "tool_start", "tool_result"):
        in_(name, types, f"工具轮次应产生 {name}")
    order = [types.index(n) for n in ("tool_call", "tool_start", "tool_result")]
    true(order == sorted(order), f"工具事件顺序应为 call<start<result（实际 {order}）")
    true(done, "工具轮次后仍应以 [DONE] 收尾")
    eq(types.count("done"), 1, "done 只应出现一次")


def check_search_knowledge_end_to_end():
    """真实调用 search_knowledge 的端到端链路。

    不论 RAG 是否就绪，都断言：
    - 流不中断（仍以 done + [DONE] 收尾）
    - tool_result 带 status 字段
    - 若返回 error，content 里应含可读的中文说明
    """
    client, _ = _make_client(_search_script())
    with client:
        resp = _chat(client, "个人养老金的缴费上限是多少？")
        events, done = _parse_sse(resp.text)

    results = [e for e in events if e["type"] == "tool_result"]
    true(len(results) >= 1, "应至少有一个 tool_result")
    r = results[0]
    in_("status", r, "tool_result 应带 status")
    true(r["status"] in ("success", "error"), f"status 取值异常：{r['status']}")
    true(done, "即便 RAG 不可用，流也必须正常收尾")
    eq([e["type"] for e in events][-1], "done")

    if r["status"] == "error":
        notice = str(r["result"].get("notice", ""))
        true(len(notice) > 0, "错误载荷应带 notice 供模型改口")
    else:
        true(
            r["result"].get("directive") in ("answer", "answer_with_caveat", "abstain"),
            "成功载荷的 directive 应是三个约定值之一",
        )


def check_error_does_not_break_stream():
    """工具报错不能让整个流崩掉（chat.py 有 try/except，但要有回归测试）。"""
    client, _ = _make_client(_tool_script("nope", "{}"))
    with client:
        resp = _chat(client, "q")
        events, done = _parse_sse(resp.text)
    true(done, "工具报错后流仍应以 [DONE] 收尾")
    errs = [e for e in events if e["type"] == "error"]
    eq(errs, [], "单个工具失败不应升级为流级 error 事件")


def check_chinese_not_escaped():
    client, _ = _make_client(_answer_script())
    with client:
        resp = _chat(client, "你好")
    in_("WealthPilot", resp.text)
    true("\\u" not in resp.text, "中文与文本不应被转义成 \\u 形式（ensure_ascii=False）")


def check_history_accepted():
    client, agent_mod = _make_client(_answer_script())
    history = [
        {"role": "user", "content": "上一轮问题"},
        {"role": "assistant", "content": "上一轮回答"},
    ]
    with client:
        resp = _chat(client, "这一轮问题", history=history)
    events, done = _parse_sse(resp.text)
    true(done, "带 history 的请求应正常完成")
    msgs = _find_second_request_messages(agent_mod)
    if msgs:
        contents = [m.get("content") for m in msgs if m.get("role") == "user"]
        in_("上一轮问题", contents, "history 应被带入 LLM 请求")
        in_("这一轮问题", contents, "当前输入应被带入 LLM 请求")


def _find_second_request_messages(agent_mod):
    """取假客户端的请求 messages（若可访问）。"""
    client_obj = getattr(agent_mod, "client", None)
    reqs = getattr(client_obj, "requests", None)
    if not reqs:
        return None
    return reqs[-1]["messages"]


def check_health_endpoint():
    """顺带确认服务的基础健康检查可用（端到端最基本的连通性）。"""
    client, _ = _make_client(_answer_script())
    with client:
        resp = client.get("/api/health")
    if resp.status_code == 404:
        raise Skip("未注册 /api/health")
    eq(resp.status_code, 200)


# ======================================================================
# 服务端权威路径（传 conversation_id）
# ======================================================================


def _assert_pairing(messages) -> None:
    """结构性自检：assistant.tool_calls 必须与其后的 tool 消息一一对应。"""
    pending = None
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            true(pending is None, "上一条 assistant 的 tool_calls 未被应答")
            pending = {c["id"] for c in m["tool_calls"]}
        elif m["role"] == "tool":
            true(pending is not None, "孤儿 tool 消息")
            true(m.get("tool_call_id") in pending, f"{m.get('tool_call_id')} 不在待应答集合")
            pending.discard(m["tool_call_id"])
            if not pending:
                pending = None
        else:
            true(pending is None, "tool_calls 未应答就出现了新消息")
    true(pending is None, f"仍有未应答：{pending}")


def check_conversation_id_persists_and_replays():
    """传 conversation_id → 后端自己读库、跑完落库，且历史可被正确回放。

    这条用例覆盖 B 的核心：**历史的权威从客户端移到服务端**。
    断言四件事：
    1. 请求成功且流正常收尾；
    2. 库里留下 user + assistant 两条（服务端写的，前端不再写）；
    3. assistant 带上本轮工具调用**及其结果**；
    4. 把库里的历史交给 ``prepare_history`` 能产出配对完整的协议消息（否则下次续聊 400）。

    注意：需要**可写**的 SQLite。若环境不允许写库（沙箱 / 只读挂载），
    本用例会 Skip 而不是报错——那属于环境限制，不是代码缺陷。
    """
    import sqlite3

    # 先构造客户端：缺 fastapi/httpx 时这里就会 Skip，避免在无谓的环境里碰数据库
    client, _ = _make_client(_tool_script())

    from core.agent.context import prepare_history
    from core.services.conversations import (
        create_conversation,
        delete_conversation,
        get_conversation_messages,
    )

    try:
        conv = create_conversation("测试-服务端权威")
    except sqlite3.OperationalError as exc:
        raise Skip(f"数据库不可写，跳过落库路径实测：{exc}")

    cid = conv["id"]
    try:
        with client:
            resp = client.post(
                "/api/chat",
                json={"message": "帮我查个东西", "conversation_id": cid},
            )
            eq(resp.status_code, 200, "带 conversation_id 的请求应成功")
            _events, done = _parse_sse(resp.text)
            true(done, "流应以 [DONE] 收尾")

            rows = get_conversation_messages(cid) or []
            eq([r["role"] for r in rows], ["user", "assistant"],
               f"应落 user + assistant 两条，实际 {[r['role'] for r in rows]}")
            eq(rows[0]["content"], "帮我查个东西", "用户消息应原样落库")

            calls = rows[1].get("tool_calls") or []
            true(len(calls) >= 1, "assistant 应带上本轮工具调用")
            for c in calls:
                true(c.get("result") is not None, f"工具结果应落库：{c.get('id')}")
                true(c.get("status") in ("success", "error"), "状态应是终态，不能是 running")

            # 回放：这轮历史必须能展开成配对完整的协议消息
            history, _info = prepare_history(rows)
            _assert_pairing(history)
            eq(history[0]["role"], "user")
    finally:
        delete_conversation(cid)


def check_unknown_conversation_returns_404():
    """不存在的会话应明确 404，而不是静默新建一个。"""
    client, _ = _make_client(_answer_script())
    with client:
        resp = client.post(
            "/api/chat",
            json={"message": "hi", "conversation_id": 2_000_000_000},
        )
    eq(resp.status_code, 404, "未知会话应返回 404")
