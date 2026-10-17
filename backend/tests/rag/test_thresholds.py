"""阈值判定纯逻辑测试（``wealth_rag.thresholds``）。

**为什么单独测这一层**：这两个阈值决定「拒答还是硬答」。在财富规划领域，
编造一条监管规定的代价远高于一次误拒，所以阈值判定的数学必须能独立验证，
不能只靠"跑一遍评估集看看"。

本模块不加载任何模型，毫秒级完成。
"""

from __future__ import annotations

import _support  # noqa: F401  —— 副作用：把 backend/ 与 RAG_/src 加进 sys.path

from run_tests import Skip, close, eq, raises, true

from wealth_rag.thresholds import (
    choose_no_answer_threshold,
    choose_rerank_floor,
    classify_query,
    percentile,
)


# ======================================================================
# percentile
# ======================================================================

def check_percentile_basic():
    s = [0.0, 1.0, 2.0, 3.0, 4.0]
    close(percentile(s, 0.0), 0.0, msg="p0 应等于最小值")
    close(percentile(s, 1.0), 4.0, msg="p100 应等于最大值")
    close(percentile(s, 0.5), 2.0, msg="p50 应等于中位数")
    close(percentile(s, 0.25), 1.0, msg="p25 线性插值")
    close(percentile(s, 0.75), 3.0, msg="p75 线性插值")


def check_percentile_edge():
    close(percentile([7.0], 0.3), 7.0, msg="单元素集任意分位都是它自己")
    v = percentile([], 0.5)
    true(v != v, "空集应返回 nan（nan != nan）")  # noqa: PLR0124
    # 两点插值
    close(percentile([0.0, 1.0], 0.5), 0.5, msg="两点中位插值")


# ======================================================================
# classify_query —— 判定优先级是重点
# ======================================================================

def check_classify_by_grades():
    eq(classify_query("policy", {"a": 2}), "positive", "含 grade=2 → positive")
    eq(classify_query("policy", {"a": 1}), "partial", "仅 grade=1 → partial")
    eq(classify_query("policy", {"a": 0, "b": 0}), "negative", "全 0 → negative")
    eq(classify_query("policy", {}), "negative", "无标注 → negative")
    eq(classify_query("fact", {"a": 1, "b": 2}), "positive", "grade2 与 1 混合 → positive")


def check_classify_prefix_wins():
    """类别前缀优先于分级标注——否则拒答阈值的正负样本集会被污染。"""
    eq(
        classify_query("负例-荐股", {"a": 2}),
        "negative",
        "「负例」前缀必须优先于 grade=2",
    )
    eq(classify_query("负例-域外", {"a": 1, "b": 2}), "negative", "负例 + 多档标注仍为 negative")
    eq(classify_query("", {"a": 2}), "positive", "空 category 不影响按 grade 判定")
    eq(classify_query(None, {"a": 2}), "positive", "None category 不应崩")


# ======================================================================
# choose_no_answer_threshold —— 主规则是分位规则
# ======================================================================

def check_threshold_separable():
    """完全可分：positive 0.60~1.00，negative 0.00~0.02。"""
    pos = [0.60 + 0.01 * i for i in range(41)]
    neg = [0.0005 * i for i in range(41)]
    th, th_con, f1p, curve = choose_no_answer_threshold(pos, neg, min_cover=0.95)

    # min_cover=0.95 → 取 positive 顶分的 5% 分位 ≈ 0.62
    close(th, 0.62, tol=0.005, msg="可分场景阈值应落在 positive 低端")
    true(th > max(neg), f"阈值应高于全部 negative 顶分（th={th}, max(neg)={max(neg)}）")
    true(th_con is not None and th_con >= th, "保守阈值应 ≥ 主阈值")
    true(len(curve) > 0, "扫阈曲线不应为空")
    true(all("feasible" in c for c in curve), "曲线每点都应带 feasible 标记")
    true(f1p is None or "f1" in f1p, "F1 报告点若存在应含 f1 字段")


def check_threshold_cover_raises_threshold():
    """``min_cover`` 越低（允许的误拒越多），阈值越高。

    直觉容易反：``min_cover=0.95`` 表示"95% 的有答案查询不许被误拒"，
    这要求阈值**低**（更容易判为有证据）；放宽到 0.80 就可以把阈值**抬高**
    （更敢拒答）。所以方向是 **min_cover ↓ → 阈值 ↑**。
    （早期断言写成"放宽应抬高阈值"却把期望值配反了，实测 0.80→0.68 / 0.95→0.62 才暴露。）
    """
    pos = [0.60 + 0.01 * i for i in range(41)]
    neg = [0.0005 * i for i in range(41)]
    th_95, _, _, _ = choose_no_answer_threshold(pos, neg, min_cover=0.95)
    th_80, _, _, _ = choose_no_answer_threshold(pos, neg, min_cover=0.80)
    true(
        th_80 > th_95,
        f"放宽覆盖率约束应抬高阈值（min_cover=0.80 → {th_80}，0.95 → {th_95}）",
    )
    # 具体量级：0.95 → 5% 分位 ≈ 0.62；0.80 → 20% 分位 ≈ 0.68
    close(th_95, 0.62, tol=0.005, msg="min_cover=0.95 应取 p5")
    close(th_80, 0.68, tol=0.005, msg="min_cover=0.80 应取 p20")


def check_threshold_overlapping():
    """完全重叠：仍应给出阈值且不崩。

    保守值的语义是"更少误拒"，因此**不得高于**主阈值——高于就意味着更激进。
    （这条断言在早前版本里写成 `>=`，方向反了。）
    """
    pos = [0.1 * i for i in range(11)]
    neg = [0.1 * i for i in range(11)]
    th, th_con, _, _ = choose_no_answer_threshold(pos, neg, min_cover=0.95)
    true(th is not None, "重叠场景仍应给出阈值")
    true(
        th_con is not None and th_con <= th + 1e-9,
        f"重叠场景保守阈值不应高于主阈值（th_con={th_con}, th={th}）",
    )
    true(
        th_con < max(pos),
        f"保守阈值必须严格小于 positive 顶分最大值（th_con={th_con}）",
    )


def check_threshold_missing_class():
    """缺样本时的降级行为。"""
    pos = [0.7, 0.8, 0.9]
    neg = [0.01, 0.02]
    th, _, _, _ = choose_no_answer_threshold([], neg, 0.95)
    eq(th, None, "无 positive 样本 → 无法定阈值（返回 None）")
    th2, _, _, _ = choose_no_answer_threshold(pos, [], 0.95)
    true(th2 is not None, "无 negative 样本 → 仍可给出阈值")
    # 单点 positive
    th3, _, _, _ = choose_no_answer_threshold([0.5], [0.01], 0.95)
    close(th3, 0.5, msg="单点 positive 阈值为该点")


# ======================================================================
# choose_rerank_floor —— 必须保证三档不退化成两档
# ======================================================================

def check_rerank_floor_keeps_gap():
    """核心不变量：rerank_floor ≥ no_answer_threshold + min_gap。

    `Retriever._fuse` 的三档判定要求 low < floor；不满足时中间档永远走不到。
    """
    # partial 顶分很低，若直接取分位会低于 no_answer_threshold
    floor = choose_rerank_floor([0.06, 0.07, 0.08], no_answer_threshold=0.10, min_gap=0.05)
    close(floor, 0.15, msg="低分位时应被抬到 no_answer_threshold + min_gap")

    # partial 顶分足够高，直接取分位。
    # [0.40, 0.41, ..., 0.59] 共 20 个点，p25 的位置 = 0.25 × 19 = 4.75，
    # 即索引 4（0.44）与索引 5（0.45）之间插值 → 0.4475。
    # （早期期望值写成 0.4，把"分位点位置"误当成了"最小值的索引 0"。）
    values = [0.4 + 0.01 * i for i in range(20)]
    floor2 = choose_rerank_floor(values, no_answer_threshold=0.10, min_gap=0.05)
    true(floor2 > 0.10 + 0.05, f"高分位时无需抬高（floor={floor2}）")
    close(floor2, 0.4475, tol=1e-6, msg="应取 partial 顶分的 p25（线性插值）")


def check_rerank_floor_no_partial():
    eq(
        choose_rerank_floor([], no_answer_threshold=0.05),
        None,
        "无 partial 样本 → 返回 None（应沿用 config 现值）",
    )


def check_rerank_floor_no_threshold():
    """没有 no_answer_threshold 时只取分位，不做抬高。"""
    floor = choose_rerank_floor([0.1, 0.2, 0.3, 0.4], no_answer_threshold=None)
    true(floor is not None and 0.1 <= floor <= 0.4, f"应在合理范围（got {floor}）")


# ======================================================================
# 不变量：三档判定与集成层 classify_quality 必须一致
# ======================================================================

def check_three_tier_invariant():
    """集成层的 classify_quality 与本模块的阈值规则必须同构。

    这里不 import 集成层（避免耦合），而是把两者的**判定契约**写下来做交叉验证：
    给定 (th_low, th_high) = (0.05, 0.25)，三分点的期望档位是固定的。
    """
    th_low, th_high = 0.05, 0.25
    cases = [
        (0.01, "low"),
        (0.04, "low"),
        (0.05, "medium"),  # 边界：>= th_low 且 < th_high
        (0.24, "medium"),
        (0.25, "high"),  # 边界：>= th_high
        (0.90, "high"),
    ]
    for score, expected in cases:
        got = "low" if score < th_low else ("high" if score >= th_high else "medium")
        eq(got, expected, f"score={score} 应判为 {expected}")


def check_threshold_conservative_not_more_aggressive():
    """**保守选项不得比主推荐更激进。**

    主规则（分位）允许 ``1 − min_cover`` 的误拒；保守选项的语义是"更少误拒"。
    因此必须满足 ``保守阈值 ≤ 主阈值 ≤ max(positive 顶分)``。
    早期实现里这个不等式被写反了（断言写成 ≥），会让使用者在"选保守"时
    反而拿到更激进的阈值——方向性错误，危害大于数值误差。
    """
    # 完全重叠：最容易踩到边界抖动
    pos = [0.1 * i for i in range(11)]
    th, th_con, _, _ = choose_no_answer_threshold(pos, pos, min_cover=0.95)
    true(th_con is not None and th is not None, "重叠场景应给出两个阈值")
    true(
        th_con <= th + 1e-9,
        f"保守阈值不应高于主阈值（th_con={th_con}, th={th}）",
    )
    true(
        th_con < max(pos),
        f"保守阈值必须严格小于 positive 顶分最大值，否则会拒掉全部查询（th_con={th_con}）",
    )

    # 单点 positive：唯一可行阈值就是该点本身。
    # 实现用 nextafter(0.42, -inf) 做上界，它与 0.42 的差约 5.6e-17 ——
    # **远小于 double 的显示精度**，所以打印出来仍是 "0.42"。
    # 断言因此不能写成 `< 0.42`（那会因为差值为负而误报），
    # 而应检查「确实不大于该点，且差值在浮点意义上是向下的」。
    single = 0.42
    th2, th2_con, _, _ = choose_no_answer_threshold([single], [0.9], min_cover=0.95)
    close(th2, single, msg="单点场景主阈值")
    true(
        th2_con is not None and th2_con <= single,
        f"单点场景保守阈值不应高于该点（got {th2_con!r}）",
    )
    true(
        th2_con is None or single - th2_con < 1e-12,
        f"单点场景保守阈值应紧贴该点（got {th2_con!r}，差 {single - (th2_con or 0):.3e}）",
    )

    # 完全可分：保守值与主值都落在 positive 低端
    pos3 = [0.6 + 0.01 * i for i in range(41)]
    neg3 = [0.0005 * i for i in range(41)]
    th3, th3_con, _, _ = choose_no_answer_threshold(pos3, neg3, min_cover=0.95)
    true(th3_con <= th3 + 1e-9, f"可分场景保守阈值不应更高（{th3_con} vs {th3}）")
    true(th3_con > max(neg3), "可分场景保守阈值应仍高于全部 negative")


def check_raises_helper_works():
    """验证 runner 的 raises 工具本身可用（元测试，防止测试框架失效而不自知）。"""
    raises(ValueError, lambda: int("not-a-number"), "int('x') 应抛 ValueError")
    try:
        raises(ValueError, lambda: 1 + 1, "不该通过")
    except AssertionError:
        return
    raise Skip("raises 工具的失败路径未按预期抛 AssertionError")
