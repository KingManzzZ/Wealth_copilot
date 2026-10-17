"""检索引擎集成逻辑测试（``tools_hub/_rag_impl.py``）。

**注入替身，不加载模型。** 集成层用 duck typing 访问 Retriever，因此这里能完整验证：

- 三档质量判定是否到位，以及 ``directive`` 是否与之匹配
- 检索异常是否被翻译成 ``{"error": ...}``（Agent 靠它判定 ``status="error"``）
- 可调参数（``fused_top`` / ``rerank_instruct`` / ``max_length``）是否真的落到了 settings
- 预热是否吞异常（可选能力不能拖垮整个服务）
- 自检能否区分「配置缺失」「依赖加载失败」「索引为空」三种失败
- 并发调用是否安全

对应 `问题文档/问题与解决方案.md` 的 P0-11 / P0-12 / P2-15。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import _support
from run_tests import close, eq, in_, not_in, true

from core.tools.tools_hub.rag_tools import impl as _rag_impl
from core.tools.tools_hub.rag_tools.impl import RagEngine, RetrieveOptions

FakeRetriever = _support.FakeRetriever
FakeSettings = _support.FakeSettings
FakeStore = _support.FakeStore
FakeRetrievalConfig = _support.FakeRetrievalConfig
FakeRerankConfig = _support.FakeRerankConfig
make_results = _support.make_results


def _tmp_config() -> Path:
    """造一个占位 config.yaml（只为通过 exists() 检查，内容不会被真正解析）。"""
    p = Path(tempfile.mkdtemp()) / "config.yaml"
    p.write_text("paths:\n  data_dir: ./data\n", encoding="utf-8")
    return p


def _engine(retriever=None, config_path=None) -> RagEngine:
    """构造一个使用替身 Retriever 的引擎。

    ``config_path`` 指向真实存在的文件，避免在 ``_build`` 之前的路径检查就失败；
    实际构造被 ``retriever_factory`` 接管，不会真的加载模型。
    """
    if config_path is None:
        config_path = _support.rag_config_path()  # 真实路径，仅用于 exists() 检查
        if not config_path.exists():
            config_path = _tmp_config()

    def factory(_cfg):
        return retriever if retriever is not None else FakeRetriever()

    return RagEngine(config_path=config_path, retriever_factory=factory)


# ======================================================================
# 质量分级与 directive
# ======================================================================

def check_search_low_quality_directs_abstain():
    r = FakeRetriever(results=make_results(3, rerank_score=0.001))
    payload = _engine(r).search_knowledge("量子纠缠是什么")
    eq(payload["evidence_quality"], "low")
    eq(payload["directive"], "abstain", "低质量应指示拒答")
    in_("notice", payload, "低质量应带 notice 明确要求不要编造")
    in_("未检索到", payload["notice"])
    eq(payload["result_count"], 3, "低质量仍应返回证据（供人工核查），不是空集")


def check_search_high_quality_directs_answer():
    r = FakeRetriever(results=make_results(3, rerank_score=0.85))
    payload = _engine(r).search_knowledge("个人养老金缴费上限")
    eq(payload["evidence_quality"], "high")
    eq(payload["directive"], "answer")
    close(payload["top_rerank_score"], 0.85)
    eq(payload["result_count"], 3)


def check_search_medium_quality_directs_caveat():
    r = FakeRetriever(results=make_results(2, rerank_score=0.12))
    payload = _engine(r).search_knowledge("组合型问题")
    eq(payload["evidence_quality"], "medium")
    eq(payload["directive"], "answer_with_caveat", "中间档应要求声明证据有限")


def check_search_empty_index_is_low():
    r = FakeRetriever(results=[])
    payload = _engine(r).search_knowledge("任何问题")
    eq(payload["evidence_quality"], "low")
    eq(payload["directive"], "abstain")
    eq(payload["result_count"], 0)


def check_search_no_rerank_scores_is_medium():
    """未开启重排时不能判 low——那会导致误拒。"""
    r = FakeRetriever(results=make_results(2, rerank_score=None))
    payload = _engine(r).search_knowledge("没有重排信号")
    eq(payload["evidence_quality"], "medium")
    eq(payload["directive"], "answer_with_caveat")


def check_search_query_echoed():
    """``query_used`` 回显实际检索串——调试追问改写是否生效的关键字段。"""
    r = FakeRetriever()
    payload = _engine(r).search_knowledge("个人养老金与税优健康险能否叠加抵扣")
    eq(payload["query_used"], "个人养老金与税优健康险能否叠加抵扣")
    eq(r.calls[-1]["query"], "个人养老金与税优健康险能否叠加抵扣", "查询应原样传给检索器")


# ======================================================================
# 异常翻译
# ======================================================================

def check_search_exception_becomes_error():
    """检索抛异常 → 返回 error 载荷，而不是让 Agent 循环整体失败。"""
    r = FakeRetriever(raise_on_search=RuntimeError("CUDA out of memory"))
    payload = _engine(r).search_knowledge("会失败的问题")
    in_("error", payload, "异常必须翻译成 error 字段")
    in_("CUDA out of memory", payload["error"], "原始异常信息应保留")
    eq(payload["directive"], "abstain", "检索失败应指示拒答")
    in_("不可用", payload["notice"])
    json.dumps(payload, ensure_ascii=False)  # 必须可序列化


def check_missing_config_is_error_payload():
    """配置文件不存在时不抛到 Agent 层，而是返回可读的错误。"""
    engine = RagEngine(config_path="E:/definitely/not/here/config.yaml")
    payload = engine.search_knowledge("任意查询")
    in_("error", payload)
    in_("配置文件不存在", payload["error"])
    eq(engine.stats["errors"], 1, "错误计数应递增")


def check_error_payload_shape_matches_contract():
    """错误载荷的字段与成功载荷保持同构，避免前端/模型遇到两种形态。"""
    r = FakeRetriever(raise_on_search=ValueError("boom"))
    payload = _engine(r).search_knowledge("q")
    for key in ("error", "query_used", "evidence_quality", "directive", "results", "notice"):
        in_(key, payload, f"错误载荷应含 {key}")
    eq(payload["results"], [])


# ======================================================================
# 参数落到 settings
# ======================================================================

def check_options_applied_to_settings():
    """``fused_top`` / ``rerank_max_length`` / ``rerank_instruct`` 应真的落到配置上。"""
    settings = FakeSettings(
        retrieval=FakeRetrievalConfig(fused_top=20),
        rerank=FakeRerankConfig(max_length=4096, instruct="原始指令"),
    )
    r = FakeRetriever(settings=settings)
    engine = _engine(r)

    engine.search_knowledge("q", top_k=3)
    # 默认走 AGENT_RERANK_INSTRUCT（若被置为 None 则保留 config 值）
    if _rag_impl.AGENT_RERANK_INSTRUCT:
        eq(settings.rerank.instruct, _rag_impl.AGENT_RERANK_INSTRUCT, "重排指令应切到 Agent 版本")
        eq(r.reranker.cfg.instruct, _rag_impl.AGENT_RERANK_INSTRUCT, "已实例化的 reranker 也应更新")
    # fused_top 未显式指定时不应被改动
    eq(settings.retrieval.fused_top, 20, "未指定 fused_top 时不应改动")


def check_options_explicit_values_applied():
    settings = FakeSettings(retrieval=FakeRetrievalConfig(fused_top=20))
    r = FakeRetriever(settings=settings)
    engine = _engine(r)
    engine._ensure(  # noqa: SLF001 —— 直接验证参数应用逻辑
        RetrieveOptions(fused_top=8, rerank_max_length=1024)
    )
    eq(settings.retrieval.fused_top, 8, "fused_top 应被应用")
    eq(settings.rerank.max_length, 1024, "rerank_max_length 应被应用")


def check_top_k_forwarded():
    r = FakeRetriever()
    engine = _engine(r)
    engine.search_knowledge("q", top_k=2)
    eq(r.calls[-1]["top_k"], 2, "top_k 应传给检索器")


def check_engine_reuses_retriever():
    """单例语义：多次调用只构造一次 Retriever（P2-15）。"""
    built = {"n": 0}
    settings = FakeSettings()

    def factory(_cfg):
        built["n"] += 1
        return FakeRetriever(settings=settings)

    engine = RagEngine(config_path=_tmp_config(), retriever_factory=factory)

    for _ in range(3):
        engine.search_knowledge("q")
    eq(built["n"], 1, "Retriever 应只构造一次")


# ======================================================================
# 预热
# ======================================================================

def check_prewarm_success():
    r = FakeRetriever()
    info = _engine(r).prewarm()
    eq(info["ok"], True)
    true(r.loaded, "预热应触发 embedder/reranker 的 load()")


def check_prewarm_swallows_failure():
    """预热失败必须吞异常——RAG 是可选能力，不能拖垮整个服务启动。

    还要保证 prewarm 不抛异常（返回值里带错误），否则 main.py 的 lifespan 会中断。
    """
    engine = RagEngine(config_path="E:/not/here/config.yaml")
    info = engine.prewarm()  # 不应抛
    eq(info["ok"], False)
    in_("error", info)


def check_prewarm_failure_then_search_still_usable():
    """预热失败不应把引擎置于不可恢复状态（例如后续 config 修好后仍可用）。"""
    engine = _engine()
    engine._retriever = None  # noqa: SLF001
    engine._config_path = "E:/not/here/config.yaml"  # noqa: SLF001
    eq(engine.prewarm()["ok"], False)
    # 修好路径后应能恢复
    engine._config_path = _engine().config_path()  # noqa: SLF001
    payload = engine.search_knowledge("q")
    not_in("error", payload, "路径修好后应恢复正常")


# ======================================================================
# 自检
# ======================================================================

def check_selfcheck_missing_config():
    engine = RagEngine(config_path="E:/not/here/config.yaml")
    report = engine.selfcheck()
    eq(report["ok"], False)
    eq(report["stage"], "config")


def check_selfcheck_empty_index():
    r = FakeRetriever(store=FakeStore(chunks=0, docs=0))
    report = _engine(r).selfcheck()
    eq(report["ok"], False)
    eq(report["stage"], "empty_index")
    in_("hint", report, "索引为空应给出摄入提示")
    in_("ingest", report["hint"])


def check_selfcheck_ready():
    r = FakeRetriever(store=FakeStore(chunks=663, docs=6), results=make_results(1, 0.9))
    report = _engine(r).selfcheck()
    eq(report["ok"], True)
    eq(report["stage"], "ready")
    eq(report["chunks"], 663)
    eq(report["docs"], 6)


def check_selfcheck_load_failure():
    class Boom(FakeRetriever):
        def __init__(self):
            raise RuntimeError("torch 未安装")

    def factory(_cfg):
        return Boom()

    report = RagEngine(config_path=_tmp_config(), retriever_factory=factory).selfcheck()
    eq(report["ok"], False)
    eq(report["stage"], "load")
    in_("torch 未安装", report["error"])


# ======================================================================
# 并发安全
# ======================================================================

def check_concurrent_calls_are_serialized():
    """并发调用不应报错，且参数不互相覆盖（P2-15）。"""
    import threading

    settings = FakeSettings(retrieval=FakeRetrievalConfig(fused_top=20))
    r = FakeRetriever(settings=settings)
    engine = _engine(r)

    errors: list[str] = []
    seen: list[int] = []

    def worker(k: int):
        try:
            engine.search_knowledge(f"查询{k}", top_k=1 + (k % 5))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    eq(errors, [], f"并发调用不应报错：{errors}")
    eq(len(r.calls), 8, "每次调用都应到达检索器")
    # top_k 各自独立：8 次调用应产生多个不同的 top_k
    true(len({c["top_k"] for c in r.calls}) > 1, "各次调用的 top_k 不应被覆盖成同一个值")
    seen = [c["top_k"] for c in r.calls]
    eq(sorted(seen), sorted(1 + (i % 5) for i in range(8)), "top_k 应与各自请求一致")


# ======================================================================
# 单例与统计
# ======================================================================

def check_singleton_reset():
    fake = RagEngine()
    _rag_impl.reset_engine(fake)
    true(_rag_impl.get_engine() is fake, "reset_engine 应替换单例")
    _rag_impl.reset_engine()
    true(_rag_impl.get_engine() is not fake, "传 None 应清空单例")


def check_stats_counting():
    r = FakeRetriever(raise_on_search=RuntimeError("x"))
    engine = _engine(r)
    engine.search_knowledge("a")
    engine.search_knowledge("b")
    eq(engine.stats["calls"], 2)
    eq(engine.stats["errors"], 2)
    true(engine.stats["last_ms"] is not None, "应记录最近一次耗时")


def check_demo_payload_available_without_index():
    p = _rag_impl.demo_payload()
    eq(p["result_count"], 1)
    in_("自检", p["notice"])
