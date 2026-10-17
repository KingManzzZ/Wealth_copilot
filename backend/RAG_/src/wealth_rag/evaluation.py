"""检索评估框架：chunk 级指标 + 负例指标 + 统计显著性（P0-7）。

**为什么重写**

原实现只做**文档级**匹配（比对 ``doc_id``/``doc_title``/``source_path``），而全库只有 6 篇文档，
因此 Recall@10 恒为 1.000——**指标对切分质量结构性失敏**，是本次审查中 15 个问题被掩盖
5 天的直接原因。评估粒度必须与被优化的粒度一致：系统优化 chunk，就必须做 chunk 级标注。

**设计要点**

1. **标签锚定标题路径，不锚定 ``chunk_id``**——重新切分后同一份评估集仍然可用。
   匹配规则：``gold`` 归一化后出现在结果的 ``heading_path`` 归一化串中即为命中。
2. **区分可答 / 不可答查询**，并分别报告：
   - 可答：Hit@k、Recall@k（小节覆盖率）、Precision@k、MRR、nDCG@k、MAP@k
   - 不可答：**拒答准确率**（系统应给出 ``low_confidence``）；可答查询上的**误拒率**
3. **``must_not`` 负例**：显式断言「这类块不该出现」（如目录页），报告违反率。
4. **统计严谨性**：所有指标给 **bootstrap 95% 置信区间**；A/B 比较用**配对 bootstrap**
   报 p 值——小评估集（几十条）下比较均值而不做配对检验，得到的"改进"多数是噪声。
5. **消融友好**：``--json`` 落盘一次运行的全部逐条结果，``--compare-with`` 直接与基线做配对检验。

用法::

    python -m wealth_rag.evaluation --queries eval/queries.jsonl --top-k 10
    python -m wealth_rag.evaluation --no-rerank --json runs/no_rerank.json
    python -m wealth_rag.evaluation --compare-with runs/baseline.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import load_settings
from .retrieval.hybrid import Retriever

#: 归一化时剔除的字符（空白含全角空格）
_WS_RE = re.compile(r"[\s\u3000\u00a0]+")

#: 不可答查询的类别前缀约定
UNANSWERABLE_PREFIX = "负例"

#: 指标名 → 逐条向量名（供 bootstrap 取向量）
_METRIC_VEC = {
    "Hit": "hit",
    "Recall": "recall",
    "Precision": "precision",
    "MRR": "rr",
    "nDCG": "ndcg",
    "MAP": "ap",
    "误拒率(可答)": "abstain",
    "拒答准确率(不可答)": "abstain",
    "must_not违反率": "violation",
}


def _norm(text: str) -> str:
    return _WS_RE.sub("", text or "")


def _r6(value: float | None) -> float | None:
    """可选浮点的六位舍入（None 透传）。"""
    return None if value is None else round(float(value), 6)


# ======================================================================
# 评估集
# ======================================================================

@dataclass
class QueryCase:
    id: str
    query: str
    category: str = "未分类"
    gold_headings: list[str] = field(default_factory=list)
    must_not: list[str] = field(default_factory=list)
    note: str = ""
    relevant: list[str] = field(default_factory=list)  # 兼容旧的文档级格式
    #: chunk_id → 分级相关性（2=直接回答 / 1=相关但不完整 / 0=无关）。
    #: 由 ``eval/apply_grades.py`` 从人工标注生成。**给定时 nDCG 用分级增益计算**，
    #: 否则退回二值增益（旧行为，保持不变）。
    grades: dict[str, int] = field(default_factory=dict)

    @property
    def answerable(self) -> bool:
        """可答 = 有 gold（标题级或文档级），且不属于「负例-*」类别。"""
        if self.category.startswith(UNANSWERABLE_PREFIX):
            return False
        return bool(self.gold_headings or self.relevant)

    def grade_of(self, chunk_id: str) -> int:
        """取得该块的标注等级；未标注按 0（无关）处理。"""
        return int(self.grades.get(chunk_id, 0))


def load_queries(path: str | Path) -> list[QueryCase]:
    """读取 JSONL 评估集。

    支持三种行格式：

    - 新版（chunk 级 + 分级标注）：
      ``{"id","query","category","gold_headings":[...],"grades":{chunk_id:0|1|2}}``
    - 新版（chunk 级，无分级）：``{"id","query","category","gold_headings":[...]}``
    - 旧版（文档级）：``{"query","relevant":[...]}`` —— 仍可用作兜底，但**不敏感于切分**
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"评估集不存在: {path}（参考 eval/queries.jsonl 的格式创建）"
        )
    cases: list[QueryCase] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno} JSON 解析失败: {exc}") from exc
        cases.append(
            QueryCase(
                id=str(raw.get("id") or f"q{lineno:03d}"),
                query=raw["query"],
                category=raw.get("category", "未分类"),
                gold_headings=list(raw.get("gold_headings") or []),
                must_not=list(raw.get("must_not") or []),
                note=raw.get("note", ""),
                relevant=list(raw.get("relevant") or []),
                grades={str(k): int(v) for k, v in (raw.get("grades") or {}).items()},
            )
        )
    return cases


# ======================================================================
# 命中判定
# ======================================================================

def _haystack(result) -> str:
    return _norm(" > ".join(result.heading_path or []) + " " + (result.source_path or ""))


def is_gold(result, case: QueryCase) -> bool:
    """该结果是否命中任一 gold 标注。

    - 标题级：gold 归一化后出现在结果的 heading_path 中；
    - 文档级（旧格式兜底）：doc_id / doc_title / source_path 任一匹配。
    """
    hay = _haystack(result)
    for g in case.gold_headings:
        if g and _norm(g) in hay:
            return True
    if case.gold_headings:
        return False
    for rel in case.relevant:
        if rel == result.doc_id or rel == result.doc_title:
            return True
        if rel and rel in (result.source_path or ""):
            return True
    return False


def is_forbidden(result, case: QueryCase) -> bool:
    """结果是否撞上 ``must_not``（如目录页、页眉）——这类命中就是污染。"""
    hay = _haystack(result)
    return any(m and _norm(m) in hay for m in case.must_not)


#: 分级增益：gain(grade) = 2^grade − 1 → grade 2 得 3 分，grade 1 得 1 分，grade 0 得 0 分。
#: 用 2 的幂而非线性，是为了让「直接回答问题」相对「相关但不完整」的优势更陡——
#: 重排的目标正是把前者顶上去。
_GAIN = {0: 0.0, 1: 1.0, 2: 3.0}


def _graded_ndcg(results: list, case: QueryCase, top_k: int) -> float:
    """按 chunk_id 分级标注计算 nDCG@k。

    IDCG 用「标注中增益最高的 k 个」计算，而不是「返回结果里的最高增益」——
    否则一个把所有高分块都漏掉的系统会因为分母同样变小而拿到虚高的 nDCG。
    """
    gains = [_GAIN.get(case.grade_of(r.chunk_id), 0.0) for r in results[:top_k]]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sorted((_GAIN.get(g, 0.0) for g in case.grades.values()), reverse=True)[:top_k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


# ======================================================================
# 逐条结果
# ======================================================================

@dataclass
class QueryOutcome:
    case: QueryCase
    ranks: list[int] = field(default_factory=list)  # 命中的排名（1 起）
    n_gold: int = 0
    n_matched: int = 0
    violation_ranks: list[int] = field(default_factory=list)
    low_confidence: bool = False
    ndcg: float = 0.0
    ap: float = 0.0
    rr: float = 0.0
    #: 父子块诊断（多粒度检索专用，见下方 `parent_coverage` / `parent_redundancy`）
    n_child: int = 0  # 返回的块数
    n_parent_ok: int = 0  # 其中成功回查到父块全文的块数
    n_parent_slots: int = 0  # 带 parent_id 的块数
    n_parent_distinct: int = 0  # 去重后的父块数

    #: rerank 分数采集（阈值校准专用，见 `tune_rerank_threshold.py`）。
    #: ``None`` 表示该次运行关闭了重排——此时**不能**用于阈值校准。
    top_rerank_score: float | None = None  # 本次结果集里的最高重排分
    top_rerank_rank: int | None = None  # 最高分所在的排名（诊断「重排第 1 名是否就是 gold」）
    max_relevant_rerank: float | None = None  # 相关块（grade≥1）中的最高重排分
    max_irrelevant_rerank: float | None = None  # 无关块（grade=0）中的最高重排分（假阳性的上沿）
    max_grade2_rerank: float | None = None  # 直接回答问题（grade=2）的最高分
    max_grade1_rerank: float | None = None  # 相关但不完整（grade=1）的最高分
    rerank_scores: list[float] = field(default_factory=list)  # 按返回顺序的全部重排分

    @property
    def hit(self) -> bool:
        return bool(self.ranks)

    @property
    def margin(self) -> float | None:
        """相关块与无关块的重排分**间隔**。

        ``> 0`` 表示本次检索的排序是「对的」（相关块确实排在无关块之上）；
        ``<= 0`` 表示存在假阳性压过真相关——这是重排不可信的直接证据。
        样本内无法计算（缺一类）时返回 ``None``。
        """
        if self.max_relevant_rerank is None or self.max_irrelevant_rerank is None:
            return None
        return self.max_relevant_rerank - self.max_irrelevant_rerank


def evaluate(
    settings,
    queries_path: str | Path,
    top_k: int = 10,
    use_rerank: bool = True,
    retriever: Retriever | None = None,
) -> "EvalReport":
    cases = load_queries(queries_path)
    retriever = retriever or Retriever(settings)
    rows: list[QueryOutcome] = []

    for case in cases:
        results = retriever.search(case.query, top_k=top_k, use_rerank=use_rerank)
        ranks: list[int] = []
        viol: list[int] = []
        # rerank 分数采集：相关块与无关块要分开记，才能看出排序质量与阈值可分性
        rel_scores: list[float] = []
        irr_scores: list[float] = []
        g2_scores: list[float] = []
        g1_scores: list[float] = []
        for i, res in enumerate(results[:top_k], start=1):
            gold = is_gold(res, case)
            if gold:
                ranks.append(i)
            if case.must_not and is_forbidden(res, case):
                viol.append(i)
            if res.rerank_score is not None:
                s = float(res.rerank_score)
                (rel_scores if gold else irr_scores).append(s)
                # 分级（grades 存在时才有意义）
                g = case.grade_of(res.chunk_id)
                if g == 2:
                    g2_scores.append(s)
                elif g == 1:
                    g1_scores.append(s)

        # 注意：``ranks`` 统计的是**命中的块数**，而 Recall 的分母是**gold 标注条数**，
        # 两者单位不同（同一小节常被切成多个子块）。因此 Recall 必须按
        # 「有多少条 gold 标注被覆盖」计算，否则会出现 Recall > 1 的荒谬值。
        if case.gold_headings:
            matched_units = sum(
                1
                for g in case.gold_headings
                if any(_norm(g) in _haystack(res) for res in results[:top_k])
            )
            n_gold = len(case.gold_headings)
        else:
            matched_units = 1 if ranks else 0
            n_gold = len(case.relevant)

        outcome = QueryOutcome(
            case=case,
            ranks=ranks,
            n_gold=n_gold,
            n_matched=matched_units,
            violation_ranks=viol,
            low_confidence=bool(results) and all(r.low_confidence for r in results),
            n_child=len(results),
            n_parent_ok=sum(1 for r in results if r.parent_text),
            n_parent_slots=sum(1 for r in results if r.parent_id),
            n_parent_distinct=len({r.parent_id for r in results if r.parent_id}),
            rerank_scores=[
                float(r.rerank_score)
                for r in results[:top_k]
                if r.rerank_score is not None
            ],
            max_relevant_rerank=max(rel_scores) if rel_scores else None,
            max_irrelevant_rerank=max(irr_scores) if irr_scores else None,
            max_grade2_rerank=max(g2_scores) if g2_scores else None,
            max_grade1_rerank=max(g1_scores) if g1_scores else None,
        )
        if outcome.rerank_scores:
            best = max(outcome.rerank_scores)
            outcome.top_rerank_score = best
            # 取首个达到最高分的排名（并列时给最靠前的那个）
            outcome.top_rerank_rank = next(
                i for i, s in enumerate(outcome.rerank_scores, start=1) if s == best
            )

        if ranks:
            outcome.rr = 1.0 / ranks[0]
            outcome.ndcg = 1.0 / math.log2(ranks[0] + 1)  # 单目标时 IDCG=1
            outcome.ap = 1.0 / ranks[0]
        if n_gold > 1:
            dcg = sum(1.0 / math.log2(r + 1) for r in ranks)
            idcg = sum(
                1.0 / math.log2(i + 1) for i in range(1, min(n_gold, top_k) + 1)
            )
            outcome.ndcg = dcg / idcg if idcg else 0.0
            hits = 0
            prec_sum = 0.0
            for i, res in enumerate(results[:top_k], start=1):
                if is_gold(res, case):
                    hits += 1
                    prec_sum += hits / i
            outcome.ap = prec_sum / min(n_gold, top_k) if n_gold else 0.0

        # 有分级标注时，nDCG 改用**分级增益**（gain = 2^grade − 1）。
        # 二值 nDCG 把「直接回答问题」和「相关但不完整」当成一样好，这在
        # rerank 评估里恰恰抹掉了最需要衡量的差别——重排的价值就在于把 grade=2
        # 顶到 grade=1 前面。
        if case.grades:
            outcome.ndcg = _graded_ndcg(results[:top_k], case, top_k)

        rows.append(outcome)

    return EvalReport(rows=rows, top_k=top_k, use_rerank=use_rerank)


# ======================================================================
# 报告与统计
# ======================================================================

def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


@dataclass
class EvalReport:
    rows: list[QueryOutcome]
    top_k: int = 10
    use_rerank: bool = True
    #: 第一阶段候选深度 W（= config.retrieval.fused_top）。用于报「回召率上限」。
    recall_w: int = 0

    @property
    def answerable(self) -> list[QueryOutcome]:
        return [r for r in self.rows if r.case.answerable]

    @property
    def unanswerable(self) -> list[QueryOutcome]:
        return [r for r in self.rows if not r.case.answerable]

    def vec(self, name: str, subset: str = "answerable") -> list[float]:
        """取逐条指标向量（bootstrap 与配对检验的最小单位）。"""
        rows = {
            "answerable": self.answerable,
            "unanswerable": self.unanswerable,
            "all": self.rows,
        }[subset]
        out: list[float] = []
        for r in rows:
            if name == "hit":
                out.append(1.0 if r.hit else 0.0)
            elif name == "precision":
                out.append(min(r.n_matched, self.top_k) / self.top_k)
            elif name == "recall":
                out.append(r.n_matched / r.n_gold if r.n_gold else 0.0)
            elif name == "rr":
                out.append(r.rr)
            elif name == "ndcg":
                out.append(r.ndcg)
            elif name == "ap":
                out.append(r.ap)
            elif name == "abstain":
                out.append(1.0 if r.low_confidence else 0.0)
            elif name == "violation":
                out.append(1.0 if r.violation_ranks else 0.0)
        return out

    def metric(self, name: str, subset: str = "answerable") -> float:
        return _mean(self.vec(name, subset))

    def summary(self) -> dict[str, float]:
        s = {
            f"Hit@{self.top_k}": self.metric("hit"),
            f"Recall@{self.top_k}": self.metric("recall"),
            f"Precision@{self.top_k}": self.metric("precision"),
            f"MRR@{self.top_k}": self.metric("rr"),
            f"nDCG@{self.top_k}": self.metric("ndcg"),
            f"MAP@{self.top_k}": self.metric("ap"),
            "误拒率(可答)": self.metric("abstain"),
        }
        if self.unanswerable:
            s["拒答准确率(不可答)"] = self.metric("abstain", "unanswerable")
        s["must_not违反率"] = self.metric("violation", "all")
        # 父子块诊断：区分「子块没召回」与「父块回查失败」两类失败
        s["父块回查成功率"] = self.parent_coverage()
        s["同父冗余度"] = self.parent_redundancy()
        return s

    # ---- 多粒度（父子块）诊断 ----
    def ceilling_recall(self, w: int) -> float:
        """``Recall@W``（W = 第一阶段候选深度）——**重排收益的硬上界**。

        若报告窗口 k 等于重排深度 W，则"前 k 名"恒等于重排输入集，
        Recall@k 对重排**在数学上完全不敏感**。因此必须另行报告 W 处的召回率，
        否则会把"第一阶段召回不行"误读成"重排没用"。
        """
        return _mean(
            [
                1.0 if (r.ranks and r.ranks[0] <= w) else 0.0
                for r in self.answerable
            ]
        )

    def parent_coverage(self) -> float:
        """父块回查成功率 = 成功取到父块全文的块 / 带 parent_id 的块。

        该项掉下来说明父子映射或回查链路坏了，与召回质量无关。
        """
        slots = sum(r.n_parent_slots for r in self.answerable)
        ok = sum(min(r.n_parent_ok, r.n_parent_slots) for r in self.answerable)
        return ok / slots if slots else 1.0

    def parent_redundancy(self) -> float:
        """同父冗余度 = 1 − 去重父块数 / 带 parent_id 的块数。

        接近 0 表示命中块分散在不同小节（多样性好）；接近 1 表示大量命中挤在
        同一节的多个子块上（`dedupe_by_parent` 正在处理的问题）。
        """
        slots = sum(r.n_parent_slots for r in self.answerable)
        distinct = sum(r.n_parent_distinct for r in self.answerable)
        return 1.0 - (distinct / slots) if slots else 0.0

    def by_category(self) -> dict[str, dict[str, float]]:
        cats: dict[str, list[QueryOutcome]] = {}
        for r in self.rows:
            cats.setdefault(r.case.category, []).append(r)
        return {
            cat: {
                "n": len(rows),
                "Hit": _mean([1.0 if r.hit else 0.0 for r in rows]),
                "MRR": _mean([r.rr for r in rows]),
                "nDCG": _mean([r.ndcg for r in rows]),
            }
            for cat, rows in sorted(cats.items())
        }

    def to_dict(self) -> dict:
        """落盘结构：**逐条保留 hit / rr / ndcg**，以便与另一次运行做配对检验。

        只存均值无法做配对检验——这是消融可归因的前提。
        """
        return {
            "top_k": self.top_k,
            "use_rerank": self.use_rerank,
            "summary": self.summary(),
            "by_category": self.by_category(),
            "rows": [
                {
                    "id": r.case.id,
                    "query": r.case.query,
                    "category": r.case.category,
                    "answerable": r.case.answerable,
                    "hit": r.hit,
                    "ranks": r.ranks,
                    "n_gold": r.n_gold,
                    "n_matched": r.n_matched,
                    "violations": r.violation_ranks,
                    "low_confidence": r.low_confidence,
                    "ndcg": round(r.ndcg, 6),
                    "rr": round(r.rr, 6),
                    "ap": round(r.ap, 6),
                    # rerank 分数（阈值校准与失败复盘用；关闭重排时为 null）
                    "top_rerank_score": _r6(r.top_rerank_score),
                    "top_rerank_rank": r.top_rerank_rank,
                    "max_relevant_rerank": _r6(r.max_relevant_rerank),
                    "max_irrelevant_rerank": _r6(r.max_irrelevant_rerank),
                    "margin": _r6(r.margin),
                }
                for r in self.rows
            ],
        }


def bootstrap_ci(
    values: list[float], n_boot: int = 2000, alpha: float = 0.05, seed: int = 20261015
) -> tuple[float, float]:
    """percentile bootstrap 置信区间。

    评估集只有几十条时，**单个均值没有意义**——必须同时报告区间宽度，
    否则无法区分"真的变好"与"抽样噪声"。
    """
    if len(values) < 2:
        v = values[0] if values else 0.0
        return (v, v)
    rng = random.Random(seed)
    n = len(values)
    means = sorted(
        sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot)
    )
    lo = means[int((alpha / 2) * n_boot)]
    hi = means[max(0, int((1 - alpha / 2) * n_boot) - 1)]
    return lo, hi


def paired_bootstrap_pvalue(
    a: list[float], b: list[float], n_boot: int = 5000, seed: int = 20261015
) -> float:
    """配对 bootstrap 检验（H0：两次运行的**逐条差值**均值为 0）。

    同一查询在两次运行里的难度是强相关的，配对能消掉这部分方差。
    小样本下比较平均分而不做配对检验，是"改完看着变好了"最常见的假阳性来源。
    """
    if not a or len(a) != len(b):
        return float("nan")
    diffs = [x - y for x, y in zip(a, b)]
    observed = abs(_mean(diffs))
    if observed == 0.0:
        return 1.0
    rng = random.Random(seed)
    n = len(diffs)
    count = sum(
        1
        for _ in range(n_boot)
        if abs(sum(d if rng.random() < 0.5 else -d for d in diffs) / n) >= observed
    )
    return count / n_boot


# ======================================================================
# 呈现
# ======================================================================

def render_report(report: EvalReport) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(
        title=f"检索评估（chunk 级 · top-{report.top_k} · rerank={'开' if report.use_rerank else '关'}）"
    )
    table.add_column("指标")
    table.add_column("值", justify="right")
    table.add_column("95% CI（bootstrap）", justify="right")

    unanswerable_metrics = {"拒答准确率(不可答)"}
    for k, v in report.summary().items():
        if k == "must_not违反率":
            vec_name, subset = "violation", "all"
        elif k.startswith("误拒率"):
            vec_name, subset = "abstain", "answerable"
        elif k.startswith("拒答准确率"):
            vec_name, subset = "abstain", "unanswerable"
        elif k in ("父块回查成功率", "同父冗余度"):
            table.add_row(k, f"{v:.4f}", "—")   # 链路健康度，不适用抽样区间
            continue
        else:
            vec_name, subset = _METRIC_VEC[k.split("@")[0].strip()], "answerable"
        lo, hi = bootstrap_ci(report.vec(vec_name, subset))
        table.add_row(k, f"{v:.4f}", f"[{lo:.4f}, {hi:.4f}]")
    console.print(table)

    if report.recall_w:
        console.print(
            f"[cyan]回召率上限 Recall@{report.recall_w}（第一阶段候选深度 W） = "
            f"{report.ceilling_recall(report.recall_w):.4f}[/cyan]  "
            "[dim]重排只能在 W 内重排；k < W 时位置敏感指标才反映重排收益[/dim]"
        )

    # 分层命中率：Hit@10 常常饱和（本例 = 1.000），此时它不具区分度，
    # 必须看更小的 k 与 MRR/nDCG 才能发现改进。
    layers = Table(title="分层命中率（可答查询）")
    layers.add_column("k", justify="right")
    layers.add_column("Hit@k", justify="right")
    layers.add_column("95% CI", justify="right")
    for kk in (1, 3, 5, report.top_k):
        if kk > report.top_k:
            continue
        vals = [1.0 if (r.ranks and r.ranks[0] <= kk) else 0.0 for r in report.answerable]
        lo, hi = bootstrap_ci(vals)
        layers.add_row(str(kk), f"{_mean(vals):.4f}", f"[{lo:.4f}, {hi:.4f}]")
    console.print(layers)

    missed_unans = [r for r in report.unanswerable if not r.low_confidence]
    if missed_unans:
        console.print(
            f"[red]不可答查询未被识别为低置信 {len(missed_unans)} 条（拒答失败）：[/red]"
        )
        for r in missed_unans:
            console.print(f"  {r.case.id} [{r.case.category}] {r.case.query}")

    cat = Table(title="分类表现")
    for col in ("类别", "n", "Hit", "MRR", "nDCG"):
        cat.add_column(col, justify="right" if col != "类别" else "left")
    for c, m in report.by_category().items():
        cat.add_row(c, str(m["n"]), f"{m['Hit']:.3f}", f"{m['MRR']:.3f}", f"{m['nDCG']:.3f}")
    console.print(cat)

    misses = [r for r in report.answerable if not r.hit]
    if misses:
        console.print(f"[yellow]未命中 {len(misses)} 条：[/yellow]")
        for r in misses[:12]:
            console.print(f"  {r.case.id} [{r.case.category}] {r.case.query}")

    viol = [r for r in report.rows if r.violation_ranks]
    if viol:
        console.print(f"[red]must_not 违反 {len(viol)} 条：[/red]")
        for r in viol[:12]:
            console.print(f"  {r.case.id} {r.case.query} → 位置 {r.violation_ranks}")

    bad = [r for r in report.answerable if r.low_confidence]
    if bad:
        console.print(f"[yellow]可答查询被误判为无证据（误拒）{len(bad)} 条：[/yellow]")
        for r in bad[:12]:
            console.print(f"  {r.case.id} {r.case.query}")


def compare_with_baseline(report: EvalReport, baseline_path: str | Path) -> None:
    """与保存的基线做配对 bootstrap 检验（消融归因的核心工具）。"""
    from rich.console import Console
    from rich.table import Table

    base = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    base_rows = {r["id"]: r for r in base.get("rows", [])}

    console = Console()
    table = Table(title=f"对比基线 {Path(baseline_path).name}（配对 bootstrap）")
    for col in ("指标", "基线", "本次", "Δ", "p 值"):
        table.add_column(col, justify="right" if col != "指标" else "left")

    for key, label, getter in (
        ("hit", "Hit@k", lambda r: float(1.0 if r.hit else 0.0)),
        ("rr", "MRR", lambda r: r.rr),
        ("ndcg", "nDCG", lambda r: r.ndcg),
        ("ap", "MAP", lambda r: r.ap),
    ):
        a, b = [], []
        for r in report.rows:
            if not r.case.answerable:
                continue
            br = base_rows.get(r.case.id)
            if br is None:
                continue
            a.append(float(br["hit"]) if key == "hit" else float(br[key]))
            b.append(getter(r))
        if not a:
            continue
        p = paired_bootstrap_pvalue(b, a)
        table.add_row(label, f"{_mean(a):.4f}", f"{_mean(b):.4f}", f"{_mean(b) - _mean(a):+.4f}", f"{p:.4f}")
    console.print(table)
    console.print("[dim]p < 0.05 才可认为有显著差异；n 很小请结合置信区间判断。[/dim]")


def main() -> None:
    parser = argparse.ArgumentParser(description="chunk 级检索评估（含统计显著性）")
    parser.add_argument(
        "--config", default=None, help="配置文件路径（默认 RAG_/config.yaml，与 CWD 无关）"
    )
    parser.add_argument("--queries", default="eval/queries.jsonl", help="评估集 JSONL 路径")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--no-rerank", action="store_true", help="关闭重排再评估")
    parser.add_argument("--json", default=None, help="把本次运行逐条结果落盘（供消融对比）")
    parser.add_argument("--compare-with", default=None, help="与保存的基线做配对检验")
    args = parser.parse_args()

    settings = load_settings(args.config)
    report = evaluate(
        settings, args.queries, top_k=args.top_k, use_rerank=not args.no_rerank
    )
    # 第一阶段候选深度：重排只能在该集合内重排，故它是重排收益的硬上界
    report.recall_w = int(getattr(settings.retrieval, "fused_top", 0) or 0)
    render_report(report)

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已保存: {args.json}")
    if args.compare_with:
        compare_with_baseline(report, args.compare_with)


if __name__ == "__main__":
    main()
