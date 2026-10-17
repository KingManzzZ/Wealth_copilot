"""评估侧自检与口径一致性测试。

覆盖 `backend/RAG_/eval/` 与 `wealth_rag.evaluation` 的**结构性契约**——
不跑真实检索（那需要模型），只验证"评估口径与集成口径没有分叉"。

**为什么需要这一层**

集成层用 `rag_evidence.classify_quality` 判定三档，而阈值校准用
`thresholds.choose_no_answer_threshold` 定阈值。两者若语义不一致，
会出现"照校准结果改了配置，线上行为却没变"这种最难查的问题。
本层把这种一致性变成可自动检测的断言。

另外把 `eval/selftest_calibration.py`（标注合并、分级 nDCG 等）纳入统一套件，
避免"两个测试入口，只跑其中一个"。
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

import _support
from run_tests import Skip, close, eq, in_, true


# ======================================================================
# 与集成层的口径一致性
# ======================================================================

def check_three_tier_semantics_match_integration():
    """校准侧的阈值语义必须与集成层的 ``classify_quality`` 完全一致。

    约定：``顶分 < no_answer_threshold → low``；
    ``no_answer_threshold ≤ 顶分 < rerank_floor → medium``；
    ``顶分 ≥ rerank_floor → high``。

    这里用**写死的阈值** ``(0.05, 0.25)`` 做边界探测，而不是从数据算出来的阈值。
    原因：从数据算出的 ``no_answer_threshold`` 可能恰好落在某个探测点上
    （早前版本正因此失败：th_low 算出来是 0.7075，恰好是探测值之一，
     而它又 ≥ rerank_floor=0.25 → 按契约应判 high，与"期望 medium"矛盾）。
    契约测试要测的是**边界语义**，不是阈值本身怎么算出来——那由
    ``test_thresholds.py`` 负责。
    """
    from core.tools.tools_hub.rag_tools.evidence import EvidenceItem, classify_quality

    th_low, th_high = 0.05, 0.25
    probes = [
        (0.04, "low"),  # 严格小于 th_low
        (0.05, "medium"),  # 边界：不小于 th_low，且小于 th_high
        (0.24, "medium"),
        (0.25, "high"),  # 边界：不小于 th_high
        (0.90, "high"),
    ]
    for score, expected in probes:
        got, _ = classify_quality(
            [EvidenceItem(raw_text="x", rerank_score=score)], th_low, th_high
        )
        eq(got, expected, f"顶分 {score}（阈值 {th_low}/{th_high}）应判为 {expected}")

    # 反例保护：确认契约不是"永远返回 high"
    got_low, _ = classify_quality([EvidenceItem(raw_text="x", rerank_score=0.001)], th_low, th_high)
    eq(got_low, "low", "极低顶分必须判 low（否则拒答能力失效）")


def check_unanswerable_prefix_is_shared():
    """「负例」前缀是评估集与阈值校准共用的约定，必须只有一处定义值。"""
    from wealth_rag.evaluation import UNANSWERABLE_PREFIX as EVAL_PREFIX
    from wealth_rag.thresholds import UNANSWERABLE_PREFIX as TH_PREFIX
    from wealth_rag.calibration import classify_query as calib_classify

    eq(TH_PREFIX, EVAL_PREFIX, "两处负例前缀定义必须一致")

    class Case:
        def __init__(self, category, grades):
            self.category = category
            self.grades = grades

    # 校准侧的分类：负例前缀优先
    eq(
        calib_classify(Case(f"{EVAL_PREFIX}-域外", {"a": 2})),
        "negative",
        "负例前缀应优先于 grade",
    )


def check_evaluation_imports_without_models():
    """评估模块应可在不加载模型的情况下被 import（供测试与离线分析使用）。

    `evaluation.py` 顶部 import 了 Retriever，但 Retriever 的模型是**懒加载**的，
    因此 import 本身不应触发 from_pretrained。若这条失败，说明有人把模型加载
    提到了模块顶层，会让所有离线工具失效。
    """
    try:
        import wealth_rag.evaluation  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"import wealth_rag.evaluation 失败（不应需要模型）：{type(exc).__name__}: {exc}"
        )


def check_evaluation_accepts_grades_field():
    """带 grades 的查询集应能被 load_queries 解析（分级标注是校准的前提）。"""
    from wealth_rag.evaluation import load_queries

    rows = [
        {
            "id": "q1",
            "query": "测试问题",
            "category": "policy",
            "grades": {"c1": 2, "c2": 1, "c3": 0},
            "gold_headings": ["第三章 > 养老"],
        }
    ]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "q.jsonl"
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        cases = load_queries(p)

    eq(len(cases), 1)
    eq(cases[0].grades, {"c1": 2, "c2": 1, "c3": 0}, "grades 应被解析")
    eq(cases[0].grade_of("c1"), 2)
    eq(cases[0].grade_of("未标注的块"), 0, "未标注的块按 0 处理")
    eq(cases[0].answerable, True, "有 gold_headings 即可答")


def check_graded_ndcg_math():
    """分级 nDCG 的数学性质（gain = 2^grade − 1）。"""
    import math

    from wealth_rag.evaluation import _GAIN, _graded_ndcg

    class Res:
        def __init__(self, cid):
            self.chunk_id = cid

    class Case:
        def __init__(self, grades):
            self.grades = grades

        def grade_of(self, cid):
            return int(self.grades.get(cid, 0))

    eq(_GAIN[2], 3.0)
    eq(_GAIN[1], 1.0)
    eq(_GAIN[0], 0.0)

    grades = {"g2": 2, "g1": 1, "g0": 0}
    # 完美排序 → 1
    close(_graded_ndcg([Res("g2"), Res("g1"), Res("g0")], Case(grades), 3), 1.0)
    # 全漏 → 0
    close(_graded_ndcg([Res("g0"), Res("gx"), Res("gy")], Case(grades), 3), 0.0)
    # 只返回 grade1：分母必须含未召回的 grade2（IDCG 用标注算）
    v = _graded_ndcg([Res("g1")], Case(grades), 1)
    expected = (1.0 / math.log2(2)) / (3.0 / math.log2(2))
    close(v, expected, tol=1e-9, msg="IDCG 应按标注而非返回结果计算")


def check_query_case_answerable_rules():
    """可答判定：负例前缀优先，其次看 gold。"""
    from wealth_rag.evaluation import QueryCase

    ok = QueryCase(id="a", query="q", gold_headings=["H"])
    eq(ok.answerable, True)
    neg = QueryCase(id="b", query="q", category="负例-域外", gold_headings=["H"])
    eq(neg.answerable, False, "负例类别即使有 gold 也不可答")
    empty = QueryCase(id="c", query="q")
    eq(empty.answerable, False, "无 gold 不可答")


# ======================================================================
# 纳入 eval/selftest_calibration.py（避免两个测试入口）
# ======================================================================

def check_eval_selftest_calibration_passes():
    """把 `RAG_/eval/selftest_calibration.py` 的全部断言并入本套件。

    该脚本已经覆盖「分位计算、阈值规则、查询分类、分级 nDCG、标注合并与覆盖率校验」。
    与其维护两份，不如在这里调用它——**单一事实来源**。
    """
    path = _support.BACKEND_DIR / "RAG_" / "eval" / "selftest_calibration.py"
    if not path.exists():
        raise Skip(f"未找到 {path}")

    spec = importlib.util.spec_from_file_location("_eval_selftest", path)
    if spec is None or spec.loader is None:
        raise Skip("无法加载 eval/selftest_calibration.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(f"加载失败：{type(exc).__name__}: {exc}")

    failures = mod.main()
    eq(failures, 0, f"eval/selftest_calibration.py 报告 {failures} 项失败（详见其输出）")


# ======================================================================
# 评估集完备性（离线可跑的部分）
# ======================================================================

def check_eval_query_set_is_wellformed():
    """现有评估集的结构必须合法（能在不检索的前提下校验）。"""
    from wealth_rag.evaluation import load_queries

    path = _support.BACKEND_DIR / "RAG_" / "eval" / "queries.jsonl"
    if not path.exists():
        raise Skip(f"评估集不存在：{path}")

    cases = load_queries(path)
    true(len(cases) >= 20, f"评估集应有足够样本（实际 {len(cases)} 条）")

    ids = [c.id for c in cases]
    eq(len(ids), len(set(ids)), "查询 id 必须唯一（标签文件按 id 索引）")

    for c in cases:
        true(bool(c.query.strip()), f"{c.id} 的 query 不应为空")
        if not c.category.startswith("负例"):
            true(
                bool(c.gold_headings) or bool(c.relevant),
                f"{c.id} 是可答查询却没有 gold 标注（{c.category}）",
            )

    n_neg = sum(1 for c in cases if not c.answerable)
    true(n_neg >= 5, f"负例样本应有足够数量（实际 {n_neg} 条）")
    # 负例少于 8 条时阈值估计不稳，这里只做提示不做失败
    if n_neg < 8:
        print(f"        [提示] 负例仅 {n_neg} 条（< 8），拒答率估计会不稳定；"
              "可并入 eval/queries.calibration.jsonl 的补充负例")


def check_calibration_extras_are_valid_jsonl():
    """补充负例文件必须能被解析，且 id 不与主评估集冲突。"""
    from wealth_rag.evaluation import load_queries

    main_path = _support.BACKEND_DIR / "RAG_" / "eval" / "queries.jsonl"
    extra_path = _support.BACKEND_DIR / "RAG_" / "eval" / "queries.calibration.jsonl"
    if not extra_path.exists():
        raise Skip(f"未找到 {extra_path}")

    extras = load_queries(extra_path)
    true(len(extras) > 0, "补充负例文件不应为空")
    for c in extras:
        true(c.category.startswith("负例"), f"{c.id} 应为负例（category={c.category!r}）")

    if main_path.exists():
        main_ids = {c.id for c in load_queries(main_path)}
        clash = sorted({c.id for c in extras} & main_ids)
        eq(clash, [], f"补充负例的 id 与主评估集冲突：{clash}")
