"""阈值判定的纯逻辑（无第三方依赖，可独立测试）。

从 ``calibration.py`` 中抽出，原因有两条：

1. **可测试性**：``calibration.py`` 需要 ``rich``（表格渲染）与 `wealth_rag.evaluation`
   （会 import Retriever → torch / qdrant）。而「阈值该怎么选」这段数学与渲染、
   与模型都无关，抽出来之后可以在**不加载任何模型**的前提下被完整验证。
2. **单一实现**：``evaluation.py`` 里的 ``low_confidence`` 判定与
   ``calibration.py`` 的推荐规则必须用**同一套**档位语义，
   否则"校准出来的阈值"和"线上实际判定"会悄悄不一致。

约定：**顶分 < 阈值 → 判为「无相关证据」**。
"""

from __future__ import annotations

import math

#: 三类查询的判定与呈现顺序
CLASSES = ("positive", "partial", "negative")

CLASS_DESC = {
    "positive": "至少 1 个 grade=2（直接回答问题）",
    "partial": "无 grade=2，但有 grade=1（相关但不完整）",
    "negative": "全部 grade=0 或 category 以「负例」开头",
}

#: 不可答查询的类别前缀（与 ``evaluation.UNANSWERABLE_PREFIX`` 保持一致）
UNANSWERABLE_PREFIX = "负例"


def percentile(sorted_vals: list[float], q: float) -> float:
    """线性插值百分位（``q ∈ [0,1]``）。空集返回 ``nan``。

    要求入参**已排序**——调用方通常要对同一组值取多个分位，
    排序一次比每次内部排序更省。
    """
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def classify_query(category: str, grades: dict[str, int]) -> str:
    """按分级标注 + 类别前缀把查询归入 positive / partial / negative。

    **判定优先级**：类别前缀 > 分级标注。

    理由：标注为「负例-*」的查询**按定义**就是「库里没有答案」，
    即使某次检索恰好命中了某个块并把它标成了 grade=2，也不应改变它作为负例的身份——
    否则拒答阈值的正负样本集会被污染。

    为什么要这个函数而不是硬编码类型清单：真实评估集用的 ``category`` 是
    中文自由文本（``枚举`` / ``事实`` / ``定义`` / ``负例-时效外`` …），
    用「前缀约定」表达负例可以兼容任意细分类型，新增类型不需要改代码。
    """
    if (category or "").startswith(UNANSWERABLE_PREFIX):
        return "negative"
    values = set((grades or {}).values())
    if 2 in values:
        return "positive"
    if 1 in values:
        return "partial"
    return "negative"


def choose_no_answer_threshold(
    pos_scores: list[float],
    neg_scores: list[float],
    min_cover: float = 0.95,
):
    """选 ``no_answer_threshold``。

    **主规则（分位规则）**：阈值放在 positive 顶分分布的 ``1 − min_cover`` 分位。
    语义直接：允许至多 ``1 − min_cover`` 的有答案查询被误拒。

    **保守备选**：覆盖率约束内的最大阈值。因为分位规则本身允许
    ``1 − min_cover`` 的误拒，所以保守值**不应高于**主值——高于它就意味着
    "更激进"，与"保守"语义相反。返回值已做浮点兜底，保证：

        ``保守阈值 ≤ 主阈值 ≤ max(positive 顶分)``

    最后一项确保至少保留一条 positive（不会把有答案的查询全部拒掉）。

    **为什么不拿「最大 F1」当决策规则**：扫阈最大化
    「positive 保留率 × negative 拒答率」的调和平均看似合理，但
    ``positive 保留率`` 只统计「顶分 ≥ 阈值」，**不检查顶分是否真的来自相关块**。
    一个库里没有答案、却因噪声块拿到高分的 positive 查询同样被计成"保留成功"，
    于是 F1 被噪声抬高、选出偏低阈值（偏向硬答）。因此 F1 只作报告项。

    返回 ``(主阈值, 保守阈值, 最大 F1 报告点, 扫阈曲线)``。
    缺 positive 样本时前两项为 ``None``（无法定阈值）。
    """
    if not pos_scores:
        return None, None, None, []

    th_quantile = percentile(sorted(pos_scores), max(0.0, 1.0 - min_cover))

    # 扫阈曲线：给出覆盖率与负例拒答率的完整权衡，便于人工复核
    curve: list[dict] = []
    if neg_scores:
        lo = min(min(pos_scores), min(neg_scores))
        hi = max(max(pos_scores), max(neg_scores))
    else:
        lo, hi = min(pos_scores), max(pos_scores)
    if hi > lo:
        steps = 200
        for i in range(steps + 1):
            th = lo + (hi - lo) * i / steps
            pos_kept = sum(1 for x in pos_scores if x >= th) / len(pos_scores)
            neg_rej = (
                sum(1 for x in neg_scores if x < th) / len(neg_scores) if neg_scores else None
            )
            curve.append(
                {
                    "threshold": round(th, 6),
                    "positive_kept": round(pos_kept, 4),
                    "negative_rejected": None if neg_rej is None else round(neg_rej, 4),
                    "feasible": pos_kept >= min_cover,
                }
            )

    th_conservative = max((c["threshold"] for c in curve if c["feasible"]), default=None)

    # 不变量：保守阈值 ≤ 主阈值 ≤ max(positive 顶分)。
    #
    # 前一个不等号由构造保证：分位规则允许 (1−min_cover) 的误拒，而网格上的
    # "覆盖率 ≥ min_cover" 恰好是该分位点本身，故最大可行网格点不会超过它。
    # 后一个不等号**需要显式兜底**：网格点可能正好落在 max(positive) 上，
    # 而那意味着"所有 positive 的顶分都 < 阈值"→ 全部被拒答。用下一个浮点
    # 表示数做上界，确保至少保留一条。
    if th_conservative is not None:
        upper = math.nextafter(max(pos_scores), -math.inf)
        th_conservative = min(th_conservative, upper)

    best_f1 = None
    if neg_scores and curve:
        scored = []
        for c in curve:
            nr = c["negative_rejected"] or 0.0
            if c["positive_kept"] + nr > 0:
                f1 = 2 * c["positive_kept"] * nr / (c["positive_kept"] + nr)
                scored.append((f1, c))
        if scored:
            scored.sort(key=lambda kv: (kv[0], kv[1]["threshold"]))
            best_f1 = {**scored[-1][1], "f1": round(scored[-1][0], 4)}

    return _r6(th_quantile), _r6(th_conservative), best_f1, curve


def choose_rerank_floor(
    partial_scores: list[float],
    no_answer_threshold: float | None,
    floor_pct: float = 0.25,
    min_gap: float = 0.05,
) -> float | None:
    """选 ``rerank_floor``：partial 顶分的 ``floor_pct`` 分位。

    并强制 ``rerank_floor ≥ no_answer_threshold + min_gap``：
    ``Retriever._fuse`` 的三档判定要求 ``low < floor``，
    否则中间档（partial）永远走不到，三档退化成两档。

    无 partial 样本时返回 ``None``（该参数应沿用 config 现值）。
    """
    if not partial_scores:
        return None
    raw = percentile(sorted(partial_scores), floor_pct)
    if math.isnan(raw):
        return None
    if no_answer_threshold is not None and raw - no_answer_threshold < min_gap:
        return _r6(no_answer_threshold + min_gap)
    return _r6(raw)


def _r6(v):
    return None if v is None else round(float(v), 6)


__all__ = [
    "CLASSES",
    "CLASS_DESC",
    "UNANSWERABLE_PREFIX",
    "choose_no_answer_threshold",
    "choose_rerank_floor",
    "classify_query",
    "percentile",
]
