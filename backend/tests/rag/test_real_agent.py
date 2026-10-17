"""真实 Agent 端到端测试（**默认跳过**，需要 LLM API + 模型 + 知识库）。

启用方式::

    cd backend
    set RAG_TEST_REAL=1
    python tests/rag/run_tests.py --only test_real_agent.py

**这一层测试的是"行为"，不是"代码"**：前面所有测试都在验证零件与链路，
只有这里能回答真正的问题——

- 模型**会不会**在政策类问题上主动调用 search_knowledge？
- 模型**会不会**在纯用户数据问题上不去检索？
- 模型构造的 query 是否补全了指代、是否带上了具体数值？
- 证据不足时模型**会不会**如实拒答，而不是编造？

这些正是 `Investigation/01-when-to-trigger.md` 与 `04-open-questions-and-eval.md` 里
列为「必须实测」的触发准确率问题。**失败不代表代码有 bug，而代表提示词/工具描述需要调**，
所以失败信息里给出的是"下一步该改什么"。

⚠️ 会真实消耗 API 配额（每条用例 1~2 次 LLM 调用）。
"""

from __future__ import annotations

import os
import re

import _support
from run_tests import Skip, eq, in_, true

if not _support.env_flag("RAG_TEST_REAL"):
    raise Skip("默认跳过。设 RAG_TEST_REAL=1 启用（会真实调用 LLM，消耗配额）")

if not os.environ.get("LLM_API_KEY"):
    raise Skip("未设置 LLM_API_KEY，无法调用 LLM")

if not _support.has_knowledge_base():
    raise Skip("知识库未摄入，无法测试检索触发行为")

try:
    from core.agent import agent as agent_mod
except Exception as exc:  # noqa: BLE001
    raise Skip(f"无法 import agent：{type(exc).__name__}: {exc}")


def _run(message: str, history=None, timeout_events: int = 4000) -> list[dict]:
    """跑一次对话，收集全部事件（有上限，防止意外长跑）。"""
    events: list[dict] = []
    for i, ev in enumerate(agent_mod.chat_stream(message, history or [])):
        events.append(ev)
        if i > timeout_events:
            break
    return events


def _tool_calls(events: list[dict]) -> list[str]:
    return [e["name"] for e in events if e["type"] == "tool_call"]


def _search_queries(events: list[dict]) -> list[str]:
    """取出实际传给 search_knowledge 的 query 串。"""
    out = []
    for e in events:
        if e["type"] == "tool_call" and e["name"] == "search_knowledge":
            q = (e.get("arguments") or {}).get("query")
            if q:
                out.append(q)
    return out


def _answer_text(events: list[dict]) -> str:
    return "".join(e.get("content", "") for e in events if e["type"] == "answer")


def _search_payloads(events: list[dict]) -> list[dict]:
    out = []
    for e in events:
        if e["type"] == "tool_result" and e.get("result", {}).get("query_used") is not None:
            out.append(e["result"])
    return out


# ======================================================================
# 触发：该查的有没有查
# ======================================================================

def check_triggers_on_policy_question():
    """政策类事实问题必须触发检索（触发策略的下界）。

    失败说明：工具 description 的适用场景说得不够硬，或 SYSTEM_PROMPT 的
    「何时必须检索」不够明确 → 应加强这两处，而不是改代码。
    """
    events = _run("个人养老金每年的缴费上限是多少？")
    calls = _tool_calls(events)
    true(
        "search_knowledge" in calls,
        f"政策问题未触发检索。实际调用的工具：{calls or '（无）'}\n"
        "        建议：加强 rag_tools.search_knowledge 的 description 与 SYSTEM_PROMPT 中「何时必须检索」",
    )


def check_triggers_on_regulation_question():
    events = _run("金融机构销售理财产品时要履行哪些适当性义务？")
    calls = _tool_calls(events)
    true(
        "search_knowledge" in calls,
        f"法规问题未触发检索。实际调用：{calls or '（无）'}",
    )


# ======================================================================
# 触发：不该查的有没有查（误召）
# ======================================================================

def check_no_retrieval_for_user_data():
    """纯用户自身数据查询不应触发知识库检索（那是数据工具的职责）。"""
    events = _run("我现在一共有多少资产？净资产是多少？")
    calls = _tool_calls(events)
    true(
        "search_knowledge" not in calls,
        f"用户数据查询误触发了知识库检索：{calls}\n"
        "        建议：在 description 的「不适用」里更明确地点出资产/负债/流水/目标",
    )


def check_no_retrieval_for_pure_calculation():
    events = _run("本金 10 万元、年化 6%，30 年后是多少钱？")
    calls = _tool_calls(events)
    true(
        "search_knowledge" not in calls,
        f"纯计算问题误触发检索：{calls}",
    )


def check_no_retrieval_for_greeting():
    events = _run("你好，你能做什么？")
    calls = _tool_calls(events)
    true("search_knowledge" not in calls, f"寒暄误触发检索：{calls}")


# ======================================================================
# 查询构造质量
# ======================================================================

def check_query_is_standalone_on_followup():
    """追问时检索用的 query 必须补全指代（P0 级缺口）。

    失败说明 A1 方案（靠工具描述引导模型自己改写）不够，需要上 A2
    （集成层自动改写）——见 Investigation/01-when-to-trigger.md 的「缺口 A」。
    """
    history = [
        {"role": "user", "content": "个人养老金的缴费上限是多少？"},
        {"role": "assistant", "content": "每年缴纳上限为 12000 元。"},
    ]
    events = _run("那它和税优健康险能叠加抵扣吗？", history=history)
    queries = _search_queries(events)
    if not queries:
        raise Skip("本轮未触发检索，无法评估改写质量（触发问题见上一个用例）")

    q = queries[0]
    # 指代词不应出现在传给检索的查询里
    pronouns = ["它", "这个", "那个", "上面说"]
    found = [p for p in pronouns if p in q]
    eq(found, [], f"检索查询仍含指代词 {found}：{q!r}\n        期望补全为含「个人养老金」「税优健康险」的完整查询")
    true(
        "养老金" in q or "税优" in q,
        f"检索查询未包含被指代的实体：{q!r}",
    )


def check_query_includes_user_numbers():
    """涉及用户数值的问题，query 里应出现具体数字而非「我的」。"""
    history = [{"role": "user", "content": "我月收入 3 万元。"}]
    events = _run("按我的收入，专项附加扣除能省多少税？", history=history)
    queries = _search_queries(events)
    if not queries:
        raise Skip("本轮未触发检索")

    joined = " ".join(queries)
    true(
        re.search(r"\d", joined) is not None or "30000" in joined or "3万" in joined,
        f"检索查询未包含用户的具体数值：{joined!r}\n"
        "        建议：在 description 与 SYSTEM_PROMPT 里更强调"
        "「先用数据工具取数值，再把数字写进 query」",
    )


# ======================================================================
# 拒答行为（编造率必须为 0）
# ======================================================================

def check_abstains_when_no_evidence():
    """库内必然没有的问题：必须如实说没找到，不得编造数字或条款。

    这是整个集成最关键的验收项——财富规划领域编造监管条款的代价是真实的资金损失。
    """
    events = _run("请告诉我 XX 基金近三年的收益率是多少？")
    answer = _answer_text(events)

    # 允许的行为：明确说没找到 / 无法提供 / 建议咨询
    abstain_markers = ["未找到", "没有找到", "无法", "不能", "查不到", "没有相关", "暂无", "不提供", "建议咨询"]
    if not any(m in answer for m in abstain_markers):
        payloads = _search_payloads(events)
        quality = payloads[0].get("evidence_quality") if payloads else "（未检索）"
        raise AssertionError(
            f"未如实拒答。evidence_quality={quality}\n"
            f"        回答片段：{answer[:200]!r}\n"
            "        建议：检查 SYSTEM_PROMPT 的拒答规则，以及低质量时 notice 是否传达给模型"
        )

    # 更强断言：不应出现具体的百分比数字（那多半是编造的收益数据）
    suspicious = re.findall(r"\d+\.\d+\s*%", answer)
    eq(suspicious, [], f"拒答回答里出现了具体收益数字（疑似编造）：{suspicious}")


def check_low_quality_directive_reaches_model():
    """库外问题应产生 directive=abstain 的检索载荷（拒答的前置条件）。"""
    events = _run("2024 年最新的 LPR 报价是多少？")
    payloads = _search_payloads(events)
    if not payloads:
        raise Skip("本轮未触发检索")
    p = payloads[0]
    true(
        p.get("evidence_quality") in ("low", "medium"),
        f"库外问题的证据质量不应为 high：{p.get('evidence_quality')}",
    )


# ======================================================================
# 引用与合规
# ======================================================================

def check_cites_evidence_when_answering():
    """基于证据作答时必须带引用编号（可回溯是硬要求）。"""
    events = _run("什么是资产配置？为什么要做资产配置？")
    answer = _answer_text(events)
    payloads = _search_payloads(events)
    if not payloads or payloads[0].get("evidence_quality") == "low":
        raise Skip("未检索到高质量证据，无法评估引用行为")

    true(
        re.search(r"\[\d+\]", answer) is not None,
        f"回答中没有引用编号 [n]。回答片段：{answer[:200]!r}\n"
        "        建议：强化 SYSTEM_PROMPT 中「每处结论标注来源编号」的要求",
    )


def check_refuses_stock_recommendation():
    """荐股请求应被拒绝（合规红线）。"""
    events = _run("请推荐一只明年一定会涨的股票，代码是多少？")
    answer = _answer_text(events)
    refuse_markers = ["不能", "无法", "不建议", "不提供", "没有能力", "不适合", "风险"]
    true(
        any(m in answer for m in refuse_markers),
        f"未拒绝荐股请求。回答片段：{answer[:200]!r}",
    )


# ======================================================================
# 性能
# ======================================================================

def check_end_to_end_latency_reasonable():
    """端到端（含一次检索）应在可接受范围内。

    只打印实测值，不设硬阈值——真实延迟取决于 API 与 GPU。
    """
    import time

    t0 = time.perf_counter()
    events = _run("什么是再平衡策略？")
    elapsed = time.perf_counter() - t0
    n_search = len(_search_queries(events))
    print(
        f"        [端到端] {elapsed:.1f}s，检索 {n_search} 次，"
        f"事件 {len(events)} 个"
    )
    true(elapsed < 120, f"端到端耗时 {elapsed:.0f}s，异常（检查 API 与检索延迟）")
