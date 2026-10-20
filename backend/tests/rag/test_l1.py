"""L1（单次调用）上下文管理的契约测试。

覆盖三件事：

1. **稳定前缀**：system prompt 不含日期（否则每天一次缓存全量 miss），
   日期注入到最末的 user 消息；
2. **工具结果截断**：单条 tool 结果超上限时保留头尾，且只动 tool 消息；
3. **度量**：请求规模分段统计 + usage 解析。

对应 ``Investigation/上下文机制/01-L1-单次调用上下文.md`` 与
``问题文档/Agent-上下文管理.md`` 的 C2-7。
"""

from __future__ import annotations

from types import SimpleNamespace

import _support  # noqa: F401
from run_tests import eq, in_, true

from core.agent import agent, context, l1
from core.agent.prompt import SYSTEM_PROMPT


# ======================================================================
# 稳定前缀
# ======================================================================


def check_system_prompt_is_stable():
    """system 必须是模块常量（缓存前缀）；日期不能混进去。"""
    eq(agent._system_content(), SYSTEM_PROMPT)
    true("当前日期：" not in agent._system_content(), "system 里不应有日期")
    eq(agent._system_content(), agent._system_content(), "两次调用必须完全一致")


def check_user_message_carries_date():
    """日期注入到当前 user 消息，且不影响用户原话。"""
    text = agent._user_content("记一笔午餐 35 元")
    in_("当前日期", text, "应带日期")
    in_("星期", text, "应带星期，模型才分得清工作日")
    true(text.endswith("记一笔午餐 35 元"), "用户原话应在末尾")


# ======================================================================
# 工具结果截断
# ======================================================================


def check_short_tool_content_untouched():
    content = "x" * 100
    new, cut = l1.truncate_tool_content(content, max_chars=1000)
    eq(cut, False)
    eq(new, content)


def check_long_tool_content_keeps_head_and_tail():
    content = "头" * 100 + "中" * 5000 + "尾" * 100
    new, cut = l1.truncate_tool_content(content, max_chars=1000)
    true(cut, "超长结果应被截断")
    in_("已截断", new, "应说明被截断")
    true(new.startswith("头"), "应保留开头")
    true(new.endswith("尾"), "应保留结尾")
    true(len(new) <= 1200, f"截断后不应继续膨胀（实际 {len(new)}）")


def check_truncate_only_touches_tool_messages():
    messages = [
        {"role": "tool", "content": "x" * 5000, "tool_call_id": "c1"},
        {"role": "assistant", "content": "y" * 5000},
    ]
    out, count = l1.truncate_tool_messages(messages, max_chars=1000)
    eq(count, 1, "只应截断 tool 消息")
    eq(out[1], messages[1], "assistant 消息不能被改")
    in_("已截断", out[0]["content"])


def check_prepare_history_truncates_recent_tools():
    """回放历史时也要做 L1 截断：最近一条不掩码，但可能单条就很大。"""
    rows = [
        {
            "id": 1,
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "name": "search_knowledge",
                    "arguments": {},
                    "result": {"text": "x" * 20000},
                }
            ],
        }
    ]
    history, info = context.prepare_history(rows, budget=100000)
    tool_messages = [m for m in history if m.get("role") == "tool"]
    eq(len(tool_messages), 1)
    true(len(tool_messages[0]["content"]) < 5000, "tool 内容应被截断")
    eq(info["truncated_tool_results"], 1)


# ======================================================================
# 度量
# ======================================================================


def check_request_stats_segments():
    stats = l1.request_stats(
        [
            {"role": "system", "content": "a" * 10},
            {"role": "user", "content": "b" * 20},
            {"role": "tool", "content": "c" * 30, "tool_call_id": "c1"},
        ],
        tools=[{"type": "function", "function": {"name": "x"}}],
    )
    eq(stats["system_chars"], 10)
    eq(stats["history_chars"], 20)
    eq(stats["tool_result_chars"], 30)
    true(stats["tools_chars"] > 0, "工具 schema 应计入固定成本")
    eq(stats["tool_count"], 1)
    eq(
        stats["total_chars"],
        stats["system_chars"] + stats["tools_chars"]
        + stats["history_chars"] + stats["tool_result_chars"],
    )
    in_("tools", l1.format_stats(stats), "格式化结果应可读")


def check_usage_parsing():
    chunk = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=80),
        )
    )
    usage = l1.usage_from_chunk(chunk)
    eq(usage["prompt_tokens"], 100)
    eq(usage["completion_tokens"], 20)
    eq(usage["cached_tokens"], 80, "应能读出缓存命中")

    eq(l1.usage_from_chunk(SimpleNamespace(usage=None)), None)
    eq(
        l1.usage_from_chunk(
            SimpleNamespace(usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0))
        ),
        None,
    )
