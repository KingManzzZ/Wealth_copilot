"""真实检索测试（**默认跳过**，需要模型 + 已摄入的知识库）。

启用方式::

    cd backend
    set RAG_TEST_REAL=1
    python tests/rag/run_tests.py --only test_real_rag.py

**为什么这些测试默认跳过**

它们会加载两个 0.6B 模型（合计约 2.3GB），首次运行约 15s，且要求知识库已摄入。
把它们混进默认套件会让"跑测试"变成"等模型"，反而降低测试被执行的频率。
但它们**必须存在**——替身测试只能证明逻辑对，证明不了模型与真实索引真的能用。

若依赖或知识库缺失，模块级会 raise Skip 并在报告里明确写出原因与修复命令。
"""

from __future__ import annotations

import json
import time

import _support
from run_tests import Skip, close, eq, in_, not_in, true

# ======================================================================
# 前置条件（模块级跳过）
# ======================================================================

if not _support.env_flag("RAG_TEST_REAL"):
    raise Skip("默认跳过。设 RAG_TEST_REAL=1 启用（需模型 + 已摄入知识库）")

if not _support.has_rag_deps():
    raise Skip("缺少 torch / transformers / qdrant_client，无法做真实检索")

if not _support.rag_config_path().exists():
    raise Skip(f"RAG 配置不存在：{_support.rag_config_path()}")

if not _support.has_knowledge_base():
    raise Skip(
        "知识库未摄入。先执行：\n"
        "        cd backend/RAG_\n"
        "        python -m wealth_rag.cli ingest ./data --rebuild"
    )


from core.tools.tools_hub.rag_tools import impl as _rag_impl  # noqa: E402
from core.tools.tools_hub.rag_tools.impl import RagEngine, RetrieveOptions  # noqa: E402

#: 复用同一个引擎，避免每个用例都重新加载模型
_ENGINE: RagEngine | None = None


def _engine() -> RagEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RagEngine()
        info = _ENGINE.prewarm()
        if not info.get("ok"):
            raise Skip(f"预热失败：{info.get('error')}")
    return _ENGINE


# ======================================================================
# 前置：预热与自检
# ======================================================================

def check_prewarm_reports_cold_start():
    engine = RagEngine()
    t0 = time.perf_counter()
    info = engine.prewarm()
    elapsed = time.perf_counter() - t0
    eq(info["ok"], True, f"预热应成功：{info.get('error')}")
    true(info["cold_start_ms"] is not None, "应记录冷启动耗时")
    # 冷启动应显著（模型加载），但不该离谱
    true(elapsed < 180, f"冷启动耗时异常：{elapsed:.1f}s")


def check_selfcheck_ready():
    report = _engine().selfcheck()
    eq(report["ok"], True, f"自检应通过：{report}")
    true(report.get("chunks", 0) > 0, "索引应有切块")
    true(report.get("docs", 0) > 0, "索引应有文档")


def check_warm_search_is_fast():
    """热查询应在秒级（冷启动已被预热吸收）。"""
    engine = _engine()
    engine.search_knowledge("资产配置")  # 预热一次
    t0 = time.perf_counter()
    engine.search_knowledge("什么是再平衡策略")
    elapsed = time.perf_counter() - t0
    true(elapsed < 10, f"热查询耗时 {elapsed:.2f}s，超过 10s 阈值（GPU 上应 <3s）")


# ======================================================================
# 真实检索质量
# ======================================================================

def check_search_returns_evidence_with_citation():
    payload = _engine().search_knowledge("什么是再平衡策略", top_k=5)
    not_in("error", payload, f"检索不应报错：{payload.get('error')}")
    true(payload["result_count"] >= 1, "应至少返回 1 条证据")

    for r in payload["results"]:
        true(bool(r["text"].strip()), "证据正文不应为空")
        true(bool(r["citation"].strip()), "证据必须带 citation（可溯源是硬要求）")
        true(isinstance(r["index"], int) and r["index"] >= 1, "应带从 1 开始的编号")


def check_search_in_corpus_is_not_low():
    """库内确有答案的问题不应被判定为「无证据」。

    这条是**拒答阈值校准的下界**：若连明确在库里的问题都判 low，
    说明 no_answer_threshold 定得太高，会造成大面积误拒。
    """
    probes = [
        "什么是再平衡策略",
        "什么是资产配置",
        "存款保险的偿付限额是多少",
        "什么是个人金融信息",
    ]
    lows = []
    for q in probes:
        p = _engine().search_knowledge(q, top_k=5)
        if p.get("evidence_quality") == "low":
            lows.append((q, p.get("top_rerank_score")))
    eq(lows, [], f"这些库内问题被误判为无证据（阈值可能过高）：{lows}")


def check_search_out_of_corpus_is_low():
    """库内明确没有答案的问题应被判定为 low（这是拒答能力的基础）。

    ⚠️ 这条可能失败——那正是有价值的信息：说明 `no_answer_threshold` 偏低
    （会硬答库里没有的内容），应上调。失败时请看 top_rerank_score 的量级，
    并按 `eval/tune_rerank_threshold.py` 的推荐值调整 config.yaml。
    """
    probes = [
        "帮我推荐一只明年涨幅最大的股票",
        "今天的上证指数收盘点位是多少",
        "如何用 Python 计算夏普比率",
    ]
    fails = []
    for q in probes:
        p = _engine().search_knowledge(q, top_k=5)
        if p.get("evidence_quality") != "low":
            fails.append((q, p.get("evidence_quality"), p.get("top_rerank_score")))
    eq(
        fails,
        [],
        f"这些库外问题未被判为 low（no_answer_threshold 可能偏低）：{fails}",
    )


def check_search_no_internal_fields_leak():
    """真实链路也不得泄露内部字段与本地路径。"""
    payload = _engine().search_knowledge("什么是资产配置", top_k=3)
    raw = json.dumps(payload, ensure_ascii=False)
    for leaked in ("chunk_id", "parent_id", "source_path", "rerank_scores"):
        not_in(leaked, raw, f"{leaked} 不应出现在给 LLM 的载荷中")
    not_in("backend", raw.replace("backend/RAG_", ""), "不应泄露本地目录结构")


def check_budget_respected_on_real_data():
    """真实证据（含长节）也要守住字符预算。"""
    payload = _engine().search_knowledge("个人养老金 缴费 税收优惠", top_k=8)
    size = len(json.dumps(payload, ensure_ascii=False))
    true(size <= 6000 + 500, f"载荷 {size} 字符超出默认预算（含 notice 余量 500）")


def check_top_k_respected():
    for k in (1, 3, 5):
        p = _engine().search_knowledge("资产配置", top_k=k)
        if "error" in p:
            continue
        true(
            p["result_count"] <= k,
            f"top_k={k} 时返回了 {p['result_count']} 条（去重可能使其更少，但不应更多）",
        )


def check_include_parent_adds_context_or_stays_within_budget():
    p_without = _engine().search_knowledge("什么是再平衡策略", top_k=3, include_parent=False)
    p_with = _engine().search_knowledge("什么是再平衡策略", top_k=3, include_parent=True)
    if "error" in p_with:
        raise Skip("检索不可用")
    # 开父块后体积不应减少
    s1 = len(json.dumps(p_without, ensure_ascii=False))
    s2 = len(json.dumps(p_with, ensure_ascii=False))
    true(s2 >= s1 * 0.5, "开启父块后体积不应异常缩减")


def check_query_echo_matches():
    q = "个人养老金的缴费上限是多少"
    p = _engine().search_knowledge(q, top_k=3)
    eq(p["query_used"], q, "query_used 应回显传给检索的查询")


# ======================================================================
# 延迟画像（输出实测数字，供阈值决策参考）
# ======================================================================

def check_latency_profile():
    """跑 5 条查询，打印 P50 延迟画像。

    不设严格阈值——它的价值是把实测数字暴露出来（用于判断 rerank 是否值那 1~3s）。
    """
    engine = _engine()
    queries = [
        "什么是资产配置",
        "再平衡有哪几种触发方式",
        "存款保险的偿付限额是多少",
        "什么是最大回撤",
        "个人信用报告怎么查询",
    ]
    times = []
    for q in queries:
        t0 = time.perf_counter()
        engine.search_knowledge(q, top_k=5)
        times.append(time.perf_counter() - t0)
    times.sort()
    p50 = times[len(times) // 2]
    print(f"        [延迟画像] P50={p50:.2f}s  min={times[0]:.2f}s  max={times[-1]:.2f}s")
    true(p50 < 15, f"P50 延迟 {p50:.2f}s 过高（CPU 上重排很慢，考虑关闭 rerank）")


# ======================================================================
# 与评估框架的一致性
# ======================================================================

def check_matches_direct_retriever_output():
    """集成层不应改变检索结果本身（只做形态转换）。

    用同一个 query 直接调 Retriever 与经工具调用，结果条数与顶层正文应一致。
    这能发现"适配层意外改了排序或丢条"这类问题。
    """
    engine = _engine()
    with engine._lock:  # noqa: SLF001
        engine._load_locked()  # noqa: SLF001
        engine._apply_options(RetrieveOptions(top_k=3, rerank_instruct=_rag_impl.AGENT_RERANK_INSTRUCT))  # noqa: SLF001
        direct = engine._retriever.search("什么是资产配置", top_k=3)  # noqa: SLF001

    payload = engine.search_knowledge("什么是资产配置", top_k=3)
    eq(
        payload["result_count"],
        len(direct),
        "经适配层后条数应与直接调用一致",
    )
    for a, b in zip(payload["results"], direct):
        eq(a["text"], b.raw_text, "证据正文应原样来自 raw_text（不被截断或改写）")
