"""历史回放与上下文裁剪的契约测试（``core/services/replay.py`` + ``core/agent/context.py``）。

**为什么值得单独测**

回放的正确性由**四条配对不变量**决定，而违反它们不会在本地"看起来出问题"——
症状是**下一次续聊时** API 返回 400，用户只看到"请求失败"。
所以这里把不变量做成可断言的形状，并提供了 ``assert_pairing`` 这一条结构性自检，
用在每个用例末尾（比逐字段断言更可靠）。

裁剪同理：它一旦把 ``assistant(tool_calls)`` 和它的 ``tool`` 消息拆到两侧，
配对就断了。所以"裁剪只能在单元边界发生"必须被测试守住。

对应 ``Investigation/上下文机制/06-前置件详解.md`` 与 ``问题文档/Agent-上下文管理.md``。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import uuid
from pathlib import Path

# ⚠️ 必须最先导入：``_support`` 会在 import 时把 ``backend/`` 插入 sys.path，
# 否则下面 ``from core...`` 会 ImportError（runner 的 sys.path[0] 是 tests/rag）。
import _support  # noqa: F401
from run_tests import eq, in_, true

from core.agent import compaction, context
from core.database import get_connection
from core.services import conversations, replay


# ======================================================================
# 结构性自检：配对不变量
# ======================================================================


def assert_pairing(messages) -> None:
    """I1/I2 的结构性自检。

    任何 ``assistant.tool_calls`` 的 id 集合，必须与**紧随其后**的 tool 消息集合完全相等。
    空集合（无 tool_calls）时两侧都必须为空。

    注意 ``pending`` 清空后要回落成 ``None``——否则下一组 assistant 会误判成
    "上一条还没被应答"（空 set 不是 None）。
    """
    pending = None
    for m in messages:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            true(pending is None, "上一条 assistant 的 tool_calls 还没被应答")
            pending = {c["id"] for c in m["tool_calls"]}
        elif role == "tool":
            true(pending is not None, f"孤儿 tool 消息：{m.get('tool_call_id')}")
            true(
                m["tool_call_id"] in pending,
                f"{m['tool_call_id']} 不在待应答集合 {pending}",
            )
            pending.discard(m["tool_call_id"])
            if not pending:
                pending = None
        else:
            true(pending is None, "tool_calls 未应答就出现了新消息")
    true(pending is None, f"仍有未应答的 tool_call：{pending}")


def _assistant(content: str, calls: list) -> dict:
    return {"role": "assistant", "content": content, "tool_calls": calls}


# ======================================================================
# 展开：正向
# ======================================================================


def check_expand_single_call():
    """单个工具调用 → 1 条 assistant + 1 条 tool，且 id 配对。"""
    rows = [
        {"role": "user", "content": "查资产"},
        _assistant(
            "好的",
            [{"id": "c1", "name": "get_assets", "arguments": {}, "result": {"ok": 1}}],
        ),
    ]
    msgs = replay.expand_to_model_messages(rows)
    assert_pairing(msgs)
    eq([m["role"] for m in msgs], ["user", "assistant", "tool"])
    eq(msgs[2]["tool_call_id"], "c1")
    in_("ok", msgs[2]["content"], "tool 内容应是 result 的 JSON")


def check_expand_parallel_calls():
    """并行多个调用 → 顺序与 calls 一致，逐条补 tool。"""
    rows = [
        _assistant(
            "",
            [
                {"id": "c1", "name": "get_assets", "arguments": {}, "result": {}},
                {"id": "c2", "name": "get_debts", "arguments": {}, "result": {}},
                {"id": "c3", "name": "get_goals", "arguments": {}, "result": {}},
            ],
        )
    ]
    msgs = replay.expand_to_model_messages(rows)
    assert_pairing(msgs)
    eq([m["tool_call_id"] for m in msgs[1:]], ["c1", "c2", "c3"])


def check_expand_arguments_are_strings():
    """I3：``arguments`` 必须是 JSON 字符串（存储里是对象）。"""
    rows = [
        _assistant(
            "",
            [
                {
                    "id": "c1",
                    "name": "search_knowledge",
                    "arguments": {"query": "个人养老金"},
                    "result": {},
                }
            ],
        )
    ]
    msg = replay.expand_to_model_messages(rows)[0]
    args = msg["tool_calls"][0]["function"]["arguments"]
    true(isinstance(args, str), f"arguments 应是字符串，实际 {type(args).__name__}")
    eq(json.loads(args), {"query": "个人养老金"})


def check_expand_no_tool_calls():
    """没有工具调用的 assistant 原样通过，不补 tool 消息。"""
    rows = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好，我是 WealthPilot"},
    ]
    msgs = replay.expand_to_model_messages(rows)
    assert_pairing(msgs)
    eq(len(msgs), 2)


# ======================================================================
# 展开：反向（最重要）
# ======================================================================


def check_expand_synthesizes_missing_result():
    """I4：缺 ``result`` 也必须补一条 tool 消息，且内容可读。

    省略它 → assistant.tool_calls 失去应答 → 该会话之后再也无法续聊（400）。
    """
    rows = [
        _assistant(
            "查一下",
            [{"id": "c1", "name": "search_knowledge", "arguments": {"query": "x"}, "status": "running"}],
        )
    ]
    msgs = replay.expand_to_model_messages(rows)
    assert_pairing(msgs)
    eq(len(msgs), 2, "缺 result 时仍应有 1 条 assistant + 1 条 tool")
    payload = json.loads(msgs[1]["content"])
    eq(payload["error_kind"], "interrupted")
    eq(payload["retryable"], True, "应告诉模型可以重试")


def check_expand_drops_nameless_call_in_pairs():
    """I1/I2：没有 name 的调用无法构造合法 ``tool_calls`` → 连同其 tool 消息**成对丢弃**。

    这正是前端旧实现（``buildHistory``）的漏洞：两侧分别过滤，
    导致 tool 消息成为孤儿（``tool_call_id`` 不在前一条 assistant 的 ``tool_calls`` 里）。
    """
    rows = [
        _assistant(
            "",
            [
                {"id": "c1", "name": "", "arguments": {}, "result": {"x": 1}},
                {"id": "c2", "name": "get_assets", "arguments": {}, "result": {}},
            ],
        )
    ]
    msgs = replay.expand_to_model_messages(rows)
    assert_pairing(msgs)
    eq(len(msgs), 2, "无名调用应被成对丢弃，只剩 c2 的一对")
    eq(msgs[0]["tool_calls"][0]["id"], "c2")
    eq(msgs[1]["tool_call_id"], "c2")


def check_expand_result_as_string():
    """``result`` 本身是字符串时也能正确进 content（不二次序列化）。"""
    rows = [
        _assistant("", [{"id": "c1", "name": "get_assets", "arguments": {}, "result": "raw-text"}])
    ]
    msgs = replay.expand_to_model_messages(rows)
    assert_pairing(msgs)
    eq(msgs[1]["content"], "raw-text")


# ======================================================================
# normalize_history（兼容期）
# ======================================================================


def check_normalize_passes_protocol_through():
    """已是协议形态（含 ``function`` 键 / tool 消息）→ 原样返回，不重复转换。"""
    protocol = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "get_assets", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "{}"},
    ]
    eq(replay.normalize_history(protocol), protocol)


def check_normalize_expands_ui_rows():
    """UI 形态 → 展开成协议形态。"""
    ui = [
        _assistant("", [{"id": "c1", "name": "get_assets", "arguments": {}, "result": {}}])
    ]
    msgs = replay.normalize_history(ui)
    assert_pairing(msgs)
    in_("function", msgs[0]["tool_calls"][0], "应被包成协议结构")


# ======================================================================
# 观察掩码
# ======================================================================


def _long_history(tool_count: int) -> list:
    """构造含 N 条工具输出、且每条都很长的历史（保证超预算/可掩码）。"""
    rows = [{"role": "user", "content": "开始"}]
    for i in range(tool_count):
        rows.append(
            _assistant(
                f"第{i}步",
                [{"id": f"c{i}", "name": "search_knowledge", "arguments": {}, "result": "x" * 500}],
            )
        )
    return rows


def check_mask_keeps_recent_full():
    """只掩码较早的，最近 N 条保持原文。"""
    msgs = replay.expand_to_model_messages(_long_history(12))
    masked, count = context.mask_old_tool_results(msgs, keep=8)
    eq(count, 4, "12 条工具输出、保留 8 条 → 应掩码 4 条")
    assert_pairing(masked)
    tool_msgs = [m for m in masked if m["role"] == "tool"]
    eq(tool_msgs[0]["content"], context.MASKED_PLACEHOLDER)
    true(tool_msgs[-1]["content"].startswith("x"), "最近一条应保持原文")


def check_mask_keeps_pairing():
    """掩码只清 content，不动 ``tool_call_id`` —— 配对必须仍然完整。"""
    msgs = replay.expand_to_model_messages(_long_history(10))
    masked, _ = context.mask_old_tool_results(msgs, keep=2)
    assert_pairing(masked)
    for m in masked:
        if m["role"] == "tool":
            true(bool(m.get("tool_call_id")), "tool 消息必须保留 tool_call_id")


def check_mask_noop_when_under_keep():
    """工具输出不超过保留数时，原样返回、不计数。"""
    msgs = replay.expand_to_model_messages(_long_history(3))
    masked, count = context.mask_old_tool_results(msgs, keep=8)
    eq(count, 0)
    eq(masked, msgs)


# ======================================================================
# 滑动窗口
# ======================================================================


def check_trim_respects_units():
    """裁剪绝不能把 assistant 与它的 tool 消息拆到两侧。"""
    msgs = replay.expand_to_model_messages(_long_history(12))
    trimmed, dropped = context.trim_to_budget(msgs, budget=2000, keep_first=1)
    assert_pairing(trimmed)
    true(dropped > 0, "预算 2000 下应丢弃若干单元")


def check_trim_keeps_first_unit():
    """第一个单元（会话开头的用户目标）永不丢弃——那是"忘了最初要干嘛"的防线。"""
    msgs = replay.expand_to_model_messages(_long_history(12))
    trimmed, _ = context.trim_to_budget(msgs, budget=200, keep_first=1)
    assert_pairing(trimmed)
    eq(trimmed[0]["role"], "user")
    eq(trimmed[0]["content"], "开始")


def check_trim_never_empties():
    """预算极小也至少保留 1 个单元，不能裁成空列表。"""
    msgs = replay.expand_to_model_messages(_long_history(5))
    trimmed, _ = context.trim_to_budget(msgs, budget=1, keep_first=1)
    true(len(trimmed) > 0, "不应把历史裁空")


# ======================================================================
# 组装入口
# ======================================================================


def check_prepare_history_reports_metrics():
    """``prepare_history`` 应返回可观测的度量，供日志统计。"""
    rows = _long_history(12)
    history, info = context.prepare_history(rows, budget=3000)
    assert_pairing(history)
    for key in ("rows", "masked", "dropped_units", "pre_tokens", "tokens", "budget", "compacted"):
        in_(key, info, f"度量里应含 {key}")
    eq(info["budget"], 3000)
    true(info["tokens"] <= 3000, f"裁剪后应落在预算内（实际 {info['tokens']}）")


def check_prepare_history_empty():
    """新会话（无历史）应返回空 history 而不是报错。"""
    history, info = context.prepare_history([])
    eq(history, [])
    eq(info["rows"], 0)
    eq(info["masked"], 0)


def check_estimate_tokens_grows():
    """估算应随内容单调增长（做预算判断的最低要求）。"""
    small = [{"role": "user", "content": "短"}]
    big = [{"role": "user", "content": "长" * 1000}]
    true(
        context.estimate_tokens(big) > context.estimate_tokens(small),
        "更长的内容应估出更多 token",
    )


def check_masked_placeholder_is_actionable():
    """占位符不能是空串——模型会误以为工具什么都没返回。"""
    true(bool(context.MASKED_PLACEHOLDER.strip()), "占位符不能为空")
    in_("省略", context.MASKED_PLACEHOLDER, "占位符应说明输出被省略")


# ======================================================================
# 压缩（compaction）
# ======================================================================


def _turns_rows(turns: int) -> list:
    """纯内存的 10 轮消息行（带 id），用于测划界与组装。"""
    rows = []
    next_id = 1
    for i in range(turns):
        rows.append({"id": next_id, "role": "user", "content": f"目标{i}"})
        next_id += 1
        rows.append({"id": next_id, "role": "assistant", "content": f"回答{i}"})
        next_id += 1
    return rows


def check_plan_scopes_old_turns():
    """划界：锚点之后、最近 6 轮之前的消息进入压缩范围。"""
    rows = _turns_rows(10)
    p = compaction.plan(rows, {"summary": None, "until": None, "anchor": None}, keep_recent=6)
    true(p is not None, "10 轮应可压缩")
    eq(p["anchor"], rows[0]["id"], "锚点应是第一条用户消息")
    eq(p["keep_from"], rows[8]["id"], "保留边界应是倒数第 6 条用户消息")
    # 锚点只保留第一条用户消息；它的助手回答之后才进入压缩范围
    eq(len(p["scope"]), 7, "应覆盖第 0 轮的助手回答到第 3 轮（7 条消息）")
    eq(
        [r["role"] for r in p["scope"]],
        ["assistant", "user", "assistant", "user", "assistant", "user", "assistant"],
    )


def check_plan_needs_enough_turns():
    """轮数不足 keep_recent 时不压缩——否则会把最近几轮也摘要掉。"""
    rows = _turns_rows(5)
    eq(compaction.plan(rows, {"summary": None, "until": None, "anchor": None}, keep_recent=6), None)


def check_transcript_includes_tool_results():
    """工具结果必须进 transcript（否则摘要会丢"查到了什么"）。"""
    scope = [
        {"id": 2, "role": "assistant", "content": "我查一下",
         "tool_calls": [{"id": "c1", "name": "get_assets", "arguments": {}, "result": {"net": 1}}]},
    ]
    text = compaction.build_transcript(scope)
    in_("工具 get_assets", text, "应包含工具名")
    in_("net", text, "应包含工具结果")


def check_prepare_history_injects_summary():
    """有摘要时：锚点原文 + 摘要 system + 未被覆盖的最近消息。"""
    rows = _turns_rows(8)
    state = {
        "summary": "用户目标：5 年攒 50 万。",
        "until": rows[7]["id"],  # 覆盖到第 3 轮
        "anchor": rows[0]["id"],
    }
    history, info = context.prepare_history(rows, budget=8000, state=state)
    true(info["compacted"], "应标记 compacted")
    eq(history[0]["content"], "目标0", "锚点必须原文保留")
    eq(history[1]["role"], "system", "摘要应紧跟在锚点后")
    in_("50 万", history[1]["content"], "摘要内容应注入")
    true(all("回答1" not in (m.get("content") or "") for m in history), "被覆盖的消息不应再出现")
    true(any("回答7" in (m.get("content") or "") for m in history), "未覆盖的最近消息应保留")
    assert_pairing(history)


@contextlib.contextmanager
def _temp_db():
    """一次性数据库（不碰 backend/db/data.db）。

    默认落在仓库 ``.tmp/context-tests``：Windows 沙箱下 ``%TEMP%`` 里的文件
    SQLite 打不开（目录可写但 create 被拒），所以不能用 ``tempfile.mkdtemp``。
    ``WP_TEST_TMP`` 可覆盖。
    """
    import core.database.connection as connection

    base = os.environ.get("WP_TEST_TMP")
    root = Path(base) if base else Path(__file__).resolve().parents[3] / ".tmp" / "context-tests"
    root.mkdir(parents=True, exist_ok=True)
    db_dir = root / f"db-{uuid.uuid4().hex}"
    db_dir.mkdir(parents=True, exist_ok=True)

    prev_path, prev_checked = connection.DB_PATH, connection._schema_checked
    connection.DB_PATH = str(db_dir / "data.db")
    connection._schema_checked = False
    try:
        yield
    finally:
        connection.DB_PATH, connection._schema_checked = prev_path, prev_checked
        shutil.rmtree(db_dir, ignore_errors=True)


def _seed_turns(turns: int) -> int:
    cid = conversations.create_conversation()["id"]
    conn = get_connection()
    try:
        for i in range(turns):
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, turn_id) VALUES (?, 'user', ?, ?)",
                (cid, f"第{i}轮目标：" + "目" * 30, f"t{i}"),
            )
            conn.execute(
                "INSERT INTO messages (conversation_id, role, content, turn_id) VALUES (?, 'assistant', ?, ?)",
                (cid, f"回答{i}：" + "答" * 30, f"t{i}"),
            )
        conn.commit()
    finally:
        conn.close()
    return cid


def check_maybe_compact_persists_summary_and_audit():
    """压缩成功：写 conversations.summary + compactions 审计行，之后组装带摘要。"""
    with _temp_db():
        cid = _seed_turns(10)
        rows = conversations.get_conversation_messages(cid)
        state = compaction.load_state(cid)
        calls = []

        result = compaction.maybe_compact(
            cid,
            rows,
            state,
            current_tokens=10**9,
            summarize=lambda text: (calls.append(text) or "摘要：用户目标 5 年攒 50 万"),
            force=True,
        )

        true(result is not None, "应执行压缩")
        eq(len(calls), 1, "只应调用一次摘要模型")
        in_("第1轮目标", calls[0], "压缩范围应包含锚点之后的轮次")
        true("第0轮目标" not in calls[0], "锚点不参与摘要")

        state2 = compaction.load_state(cid)
        eq(state2["summary"], "摘要：用户目标 5 年攒 50 万")
        eq(state2["anchor"], rows[0]["id"], "锚点应落库")
        true(state2["until"] is not None, "覆盖位置应落库")

        conn = get_connection()
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM compactions WHERE conversation_id = ?", (cid,)
            ).fetchone()[0]
        finally:
            conn.close()
        eq(count, 1, "应记录一条压缩审计")

        history, info = context.prepare_history(rows, state=state2)
        true(info["compacted"], "之后组装应走压缩路径")
        eq(history[0]["content"].startswith("第0轮目标"), True, "锚点仍在最前")


def check_maybe_compact_respects_threshold():
    """未过阈值不压缩（默认 75%）。"""
    with _temp_db():
        cid = _seed_turns(10)
        rows = conversations.get_conversation_messages(cid)
        result = compaction.maybe_compact(
            cid, rows, current_tokens=1, summarize=lambda _: "不应被调用"
        )
        eq(result, None)
        eq(compaction.load_state(cid)["summary"], None)


def check_maybe_compact_can_be_disabled():
    """``CONTEXT_COMPACTION=0`` 等价开关：enabled=False 时完全不动作。"""
    with _temp_db():
        cid = _seed_turns(10)
        rows = conversations.get_conversation_messages(cid)
        result = compaction.maybe_compact(
            cid, rows, current_tokens=10**9, summarize=lambda _: "x", enabled=False
        )
        eq(result, None)
        eq(compaction.load_state(cid)["summary"], None)


def check_second_compaction_needs_new_messages():
    """没有新消息时第二次压缩应无范围可压（返回 None），不会重复摘要同一段。"""
    with _temp_db():
        cid = _seed_turns(10)
        rows = conversations.get_conversation_messages(cid)
        compaction.maybe_compact(
            cid, rows, current_tokens=10**9, summarize=lambda _: "摘要", force=True
        )
        state = compaction.load_state(cid)
        again = compaction.maybe_compact(
            cid, rows, state, current_tokens=10**9, summarize=lambda _: "再次", force=True
        )
        eq(again, None, "没有新消息时不应重复压缩")

