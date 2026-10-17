"""rerank 阈值校准：用实测分数分布为 ``no_answer_threshold`` / ``rerank_floor`` 定值。

**为什么必须实测**

``config.yaml`` 里两个阈值（``no_answer_threshold``=0.05、``rerank_floor``=0.25）来自
开发阶段的经验取值，**从未用本项目语料校准**。它们决定了两件截然不同的事：

- ``no_answer_threshold``：**要不要拒答**。定高了会在有答案时误拒（用户拿不到本可回答的
  内容）；定低了会在库里没答案时强行作答 → 编造监管条款。
- ``rerank_floor``：**信不信重排器**。定高了会频繁退回关键词排序（重排白装）；定低了会
  在不该信的时候信。

两类错误的代价不对称，所以不能靠"看着像 0.05 就行"。

**三类查询（对应 CRAG 的三档动作）**

============  ============================================  ==============================
类别           判据（按分级标注自动归类）                      阈值应对它做什么
============  ============================================  ==============================
``positive``   至少 1 个候选 grade=2（直接回答问题）            **必须不拒答**
``partial``    无 grade=2，但有 grade=1（相关但不完整）         中间档：可作答但要降级声明
``negative``   全部 grade=0，或 category 以「负例」开头         **应当拒答**
============  ============================================  ==============================

**推荐规则**

1. ``no_answer_threshold`` = ``positive`` 顶分分布的 ``1 − min_cover`` 分位。
   语义直接：允许至多 ``1 − min_cover`` 的有答案查询被误拒。
   同时给出「覆盖率约束内的最大阈值」作为保守备选——分布不可分时它偏向拒答，
   这在财富规划领域是正确的偏向。
2. ``rerank_floor`` = ``partial`` 顶分的低分位（默认 25%），语义是「重排器对这个查询
   本来就没把握」的下沿；并强制保证它高于 ``no_answer_threshold``（否则三档判定退化
   成两档，``Retriever._fuse`` 的中间档永远走不到）。

**为什么不拿「最大 F1」当决策规则**：扫阈最大化「positive 保留率 × negative 拒答率」的
调和平均看似合理，但 ``positive 保留率`` 只统计「顶分 ≥ 阈值」，**不检查顶分是否真的来自
相关块**。一个库里没有答案、却因噪声块拿到高分的 positive 查询同样被计成"保留成功"，
于是 F1 被噪声抬高、选出偏低阈值（偏向硬答）。因此 F1 只作报告项。

**输出**：分数分布表、可分性判定、阈值推荐、现配置 vs 推荐配置的三类判定对比，
以及可选的 rerank 开/关块级 A/B（配对 bootstrap 检验）。

用法::

    from wealth_rag.calibration import calibrate

    calibrate("eval/queries.graded.jsonl", do_ab=True)

命令行入口见 ``eval/tune_rerank_threshold.py``（脚本）与 ``rag_tools calibrate``（CLI）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_settings
from .evaluation import (
    bootstrap_ci,
    evaluate,
    load_queries,
    paired_bootstrap_pvalue,
)
from .thresholds import (
    CLASS_DESC,
    CLASSES,
    choose_no_answer_threshold,
    choose_rerank_floor,
    classify_query as _classify,
    percentile as _percentile,
)


def classify_query(case) -> str:
    """按分级标注 + 类别前缀归类（薄适配：从 QueryCase 取字段）。

    真正的判定逻辑在 ``thresholds.classify_query``——放在那里是为了让
    「校准用什么档位语义」与「线上实际判定用什么档位语义」共用一处实现，
    避免两边悄悄不一致。
    """
    return _classify(getattr(case, "category", "") or "", getattr(case, "grades", None) or {})


# ======================================================================
# 工具函数
# ======================================================================

def _r6(v):
    return None if v is None else round(float(v), 6)


def describe(values: list[float]) -> dict:
    """一组分数的分布统计。"""
    if not values:
        return {"n": 0}
    s = sorted(values)
    return {
        "n": len(s),
        "min": round(s[0], 6),
        "p5": round(_percentile(s, 0.05), 6),
        "p25": round(_percentile(s, 0.25), 6),
        "p50": round(_percentile(s, 0.50), 6),
        "p75": round(_percentile(s, 0.75), 6),
        "p95": round(_percentile(s, 0.95), 6),
        "max": round(s[-1], 6),
        "mean": round(sum(s) / len(s), 6),
    }


# ======================================================================
# 采集
# ======================================================================

def collect(queries_path: str | Path, top_k: int, config: str | None):
    """跑一遍检索，采集每题的顶分、分级相关块分、无关块分。"""
    settings = load_settings(config)
    cases = load_queries(queries_path)
    if not cases:
        raise SystemExit(f"评估集为空: {queries_path}")
    if not any(c.grades for c in cases):
        raise SystemExit(
            "评估集里没有任何 grades 分级标注，无法做阈值校准。\n"
            "阈值校准依赖「相关块 vs 无关块」的分级标签。完整流程：\n"
            "  1) python eval/build_eval_pool.py --queries <你的评估集> --out eval/pool\n"
            "  2) 人工按 0/1/2 填写 eval/pool/labels.template.json，另存为 labels.json\n"
            "  3) python eval/apply_grades.py --pool eval/pool "
            "--labels eval/pool/labels.json\n"
            "只想先看分数分布（不做校准）：加 --dry-run 跑第 1 步。"
        )

    report = evaluate(settings, queries_path, top_k=top_k, use_rerank=True)

    rows: list[dict] = []
    for outcome in report.rows:
        case = outcome.case
        rows.append(
            {
                "id": case.id,
                "query": case.query,
                "category": case.category,
                "klass": classify_query(case),
                "top_score": outcome.top_rerank_score,
                "top_rank": outcome.top_rerank_rank,
                "max_grade2_score": outcome.max_grade2_rerank,
                "max_grade1_score": outcome.max_grade1_rerank,
                "max_relevant_score": outcome.max_relevant_rerank,
                "max_irrelevant_score": outcome.max_irrelevant_rerank,
                "margin": outcome.margin,
                "hit": outcome.hit,
                "rr": round(outcome.rr, 6),
                "ndcg": round(outcome.ndcg, 6),
                "low_confidence": outcome.low_confidence,
                "n_returned": outcome.n_child,
            }
        )
    return settings, report, rows


# ======================================================================
# 阈值推荐
# ======================================================================
# 推荐规则本身实现在 ``thresholds.py``（纯逻辑、无第三方依赖、可独立测试）。
# 这里只做 import，避免同一套档位语义有两份实现。
#   choose_no_answer_threshold —— positive 顶分的 (1−min_cover) 分位
#   choose_rerank_floor        —— partial 顶分的 floor_pct 分位，且强制高于前者


# ======================================================================
# 主流程
# ======================================================================

def calibrate(
    queries_path: str | Path,
    top_k: int = 10,
    config: str | None = None,
    min_cover: float = 0.95,
    floor_pct: float = 0.25,
    min_floor_gap: float = 0.05,
    do_ab: bool = False,
    json_out: str | None = None,
    render: bool = True,
) -> dict:
    """跑一次完整校准，返回结果字典（并可选渲染/落盘）。"""
    settings, report, rows = collect(queries_path, top_k, config)

    by_class: dict[str, list[dict]] = {c: [] for c in CLASSES}
    for r in rows:
        by_class[r["klass"]].append(r)

    dist = {
        c: describe([r["top_score"] for r in by_class[c] if r["top_score"] is not None])
        for c in CLASSES
    }
    dist["relevant_blocks"] = describe(
        [r["max_relevant_score"] for r in rows if r["max_relevant_score"] is not None]
    )
    dist["irrelevant_blocks"] = describe(
        [r["max_irrelevant_score"] for r in rows if r["max_irrelevant_score"] is not None]
    )
    dist["grade2_blocks"] = describe(
        [r["max_grade2_score"] for r in rows if r["max_grade2_score"] is not None]
    )
    dist["grade1_blocks"] = describe(
        [r["max_grade1_score"] for r in rows if r["max_grade1_score"] is not None]
    )

    pos_scores = [r["top_score"] for r in by_class["positive"] if r["top_score"] is not None]
    neg_scores = [r["top_score"] for r in by_class["negative"] if r["top_score"] is not None]
    partial_scores = [r["top_score"] for r in by_class["partial"] if r["top_score"] is not None]

    th_low, th_low_conservative, th_low_f1, curve = choose_no_answer_threshold(
        pos_scores, neg_scores, min_cover
    )

    th_high = choose_rerank_floor(
        partial_scores, th_low, floor_pct=floor_pct, min_gap=min_floor_gap
    )

    recommendations = {
        "no_answer_threshold": _r6(th_low),
        "no_answer_threshold_conservative": _r6(th_low_conservative),
        "rerank_floor": _r6(th_high),
        "current": {
            "no_answer_threshold": settings.retrieval.no_answer_threshold,
            "rerank_floor": settings.retrieval.rerank_floor,
        },
        "basis": {
            "positive_n": len(pos_scores),
            "partial_n": len(partial_scores),
            "negative_n": len(neg_scores),
            "min_cover": min_cover,
            "floor_pct": floor_pct,
            "min_floor_gap": min_floor_gap,
            "rule": (
                "no_answer_threshold 取 positive 顶分的 (1 − min_cover) 分位；"
                "rerank_floor 取 partial 顶分的 floor_pct 分位，且保证高于前者"
            ),
            # 仅报告，不作决策依据（理由见模块文档）
            "max_f1_point": th_low_f1,
        },
    }

    verdict = _verdict_table(pos_scores, neg_scores, partial_scores, recommendations)
    ab = _run_ab(settings, queries_path, top_k, report) if do_ab else None

    result = {
        "queries": str(queries_path),
        "top_k": top_k,
        "device": settings.resolve_device(),
        "distribution": dist,
        "recommendations": recommendations,
        "verdict": verdict,
        "margin": _margin_summary(rows),
        "sweep_curve": curve,
        "per_query": rows,
        "ab": ab,
    }

    if render:
        _render(result, ab)
    if json_out:
        p = Path(json_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已保存: {json_out}")
    return result


def _margin_summary(rows: list[dict]) -> dict:
    """相关块分 − 无关块分 的间隔统计（> 0 表示排序方向正确）。"""
    margins = [r["margin"] for r in rows if r["margin"] is not None]
    if not margins:
        return {"n": 0}
    bad = [m for m in margins if m <= 0]
    return {
        **describe(margins),
        "n_negative_margin": len(bad),
        "negative_margin_rate": round(len(bad) / len(margins), 4),
    }


def _verdict_table(pos, neg, partial, rec) -> dict:
    """用现配置与推荐阈值各判一次，看三类的判对率。"""

    def judge(scores, th_low):
        """顶分 < th_low → 判为拒答；返回拒答率。"""
        if not scores or th_low is None:
            return None
        return round(sum(1 for s in scores if s < th_low) / len(scores), 4)

    out = {}
    for name, th_low in (
        ("current", rec["current"]["no_answer_threshold"]),
        ("recommended", rec["no_answer_threshold"]),
        ("conservative", rec["no_answer_threshold_conservative"]),
    ):
        out[name] = {
            "no_answer_threshold": th_low,
            "positive_false_reject_rate": judge(pos, th_low),
            "negative_reject_rate": judge(neg, th_low),
            "partial_reject_rate": judge(partial, th_low),
        }
    return out


def _run_ab(settings, queries_path, top_k, report_with) -> dict:
    """rerank 开/关的块级 A/B（配对 bootstrap 检验）。"""
    report_without = evaluate(settings, queries_path, top_k=top_k, use_rerank=False)

    def vec(rows, attr):
        return [getattr(r, attr) for r in rows]

    a_rows, b_rows = report_with.answerable, report_without.answerable
    if len(a_rows) != len(b_rows):
        return {"error": "两次运行的逐条结果数不一致，无法配对检验"}

    out = {}
    pairs = {
        "hit": ([1.0 if r.hit else 0.0 for r in a_rows], [1.0 if r.hit else 0.0 for r in b_rows]),
        "rr": (vec(a_rows, "rr"), vec(b_rows, "rr")),
        "ndcg": (vec(a_rows, "ndcg"), vec(b_rows, "ndcg")),
        "ap": (vec(a_rows, "ap"), vec(b_rows, "ap")),
    }
    for name, (a, b) in pairs.items():
        if not a:
            continue
        lo_a, hi_a = bootstrap_ci(a)
        lo_b, hi_b = bootstrap_ci(b)
        p = paired_bootstrap_pvalue(a, b)
        out[name] = {
            "with_rerank": round(sum(a) / len(a), 4),
            "with_rerank_ci": [round(lo_a, 4), round(hi_a, 4)],
            "without_rerank": round(sum(b) / len(b), 4),
            "without_rerank_ci": [round(lo_b, 4), round(hi_b, 4)],
            "delta": round(sum(a) / len(a) - sum(b) / len(b), 4),
            "p_value": round(p, 4),
            "significant": bool(p < 0.05),
        }
    return out


# ======================================================================
# 呈现
# ======================================================================

def _pct(v) -> str:
    return "—" if v is None else f"{v:.1%}"


def _render(result: dict, ab: dict | None) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()

    # 1) 三类分布
    t = Table(title="rerank 顶分分布（按查询类别）")
    for col in ("类别", "判据", "n", "min", "p25", "p50", "p75", "max"):
        t.add_column(col, justify="right" if col not in ("类别", "判据") else "left")
    for c in CLASSES:
        d = result["distribution"][c]
        if not d.get("n"):
            t.add_row(c, CLASS_DESC[c], "0", *["—"] * 5)
            continue
        t.add_row(
            c, CLASS_DESC[c], str(d["n"]), f"{d['min']:.4f}", f"{d['p25']:.4f}",
            f"{d['p50']:.4f}", f"{d['p75']:.4f}", f"{d['max']:.4f}",
        )
    console.print(t)

    # 2) 可分性
    sep = Table(title="可分性：块级分数（决定单一阈值能否成立）")
    for col in ("分组", "n", "p5", "p25", "p50", "p75", "p95"):
        sep.add_column(col, justify="right" if col != "分组" else "left")
    groups = (
        ("grade=2 直接回答", result["distribution"]["grade2_blocks"]),
        ("grade=1 相关不完整", result["distribution"]["grade1_blocks"]),
        ("grade≥1 合并", result["distribution"]["relevant_blocks"]),
        ("grade=0 无关", result["distribution"]["irrelevant_blocks"]),
    )
    for label, d in groups:
        if not d.get("n"):
            sep.add_row(label, "0", *["—"] * 5)
            continue
        sep.add_row(
            label, str(d["n"]), f"{d['p5']:.4f}", f"{d['p25']:.4f}",
            f"{d['p50']:.4f}", f"{d['p75']:.4f}", f"{d['p95']:.4f}",
        )
    console.print(sep)

    rel, irr = result["distribution"]["relevant_blocks"], result["distribution"]["irrelevant_blocks"]
    if rel.get("n") and irr.get("n"):
        if rel["p5"] < irr["p95"]:
            console.print(
                f"[yellow]⚠️ 相关块 p5={rel['p5']:.4f} < 无关块 p95={irr['p95']:.4f}"
                "：分数**存在重叠**，单一阈值无法同时做到不误拒与不漏答。"
                "此时应优先「不编造」（阈值偏保守），并在提示词里强制声明不确定性。[/yellow]"
            )
        else:
            console.print(
                f"[green]✅ 相关块 p5={rel['p5']:.4f} ≥ 无关块 p95={irr['p95']:.4f}"
                "：两类分数分离良好，单一阈值即可有效区分。[/green]"
            )

    m = result["margin"]
    if m.get("n"):
        console.print(
            f"[dim]排序方向：相关块分 − 无关块分 的间隔均值 {m['mean']:+.4f}；"
            f"间隔 ≤ 0（假阳性压过真相关）占 {m['negative_margin_rate']:.1%}（n={m['n']}）[/dim]"
        )

    # 3) 推荐值
    rec = result["recommendations"]
    rt = Table(title="阈值推荐（基于实测分布）")
    for col in ("参数", "当前值", "推荐值", "依据"):
        rt.add_column(col, justify="right" if col not in ("参数", "依据") else "left")
    rt.add_row(
        "no_answer_threshold",
        str(rec["current"]["no_answer_threshold"]),
        str(rec["no_answer_threshold"]),
        f"positive 顶分的 {1 - rec['basis']['min_cover']:.0%} 分位"
        f"（允许 ≤{1 - rec['basis']['min_cover']:.0%} 的有答案查询被误拒）",
    )
    rt.add_row(
        "no_answer_threshold（保守）",
        "—",
        str(rec["no_answer_threshold_conservative"]),
        "覆盖率约束内的最大阈值；分布不可分时偏向拒答",
    )
    rt.add_row(
        "rerank_floor",
        str(rec["current"]["rerank_floor"]),
        str(rec["rerank_floor"]),
        f"partial 顶分的 p{int(rec['basis']['floor_pct'] * 100)}，"
        f"且 ≥ no_answer_threshold + {rec['basis']['min_floor_gap']}",
    )
    console.print(rt)

    f1p = rec["basis"].get("max_f1_point")
    if f1p and f1p.get("negative_rejected") is not None:
        console.print(
            f"[dim]参考（不作决策依据）：扫阈曲线最大 F1 在阈值 {f1p['threshold']:.4f}，"
            f"F1={f1p.get('f1')}（positive 保留 {f1p['positive_kept']:.1%}，"
            f"negative 拒答 {f1p['negative_rejected']:.1%}）。"
            "该点不校验顶分是否来自相关块，易被噪声抬高。[/dim]"
        )

    b = rec["basis"]
    console.print(
        f"[dim]样本：positive={b['positive_n']} / partial={b['partial_n']} / "
        f"negative={b['negative_n']}[/dim]"
    )
    if b["negative_n"] < 8:
        console.print(
            "[yellow]⚠️ negative 样本 < 8：拒答阈值的估计极不稳定。"
            "建议补充「库内确实没有答案」的真实问题作为负例后重跑。[/yellow]"
        )
    if b["positive_n"] < 8:
        console.print("[yellow]⚠️ positive 样本 < 8：误拒率估计不可靠。[/yellow]")

    # 4) 判定对比
    vt = Table(title="判定效果对比（顶分 < 阈值 → 拒答）")
    for col in ("配置", "阈值", "positive 误拒率", "partial 拒答率", "negative 拒答率"):
        vt.add_column(col, justify="right" if col != "配置" else "left")
    for name, label in (
        ("current", "当前"),
        ("recommended", "推荐"),
        ("conservative", "推荐(保守)"),
    ):
        v = result["verdict"][name]
        if v["no_answer_threshold"] is None:
            continue
        vt.add_row(
            label,
            f"{v['no_answer_threshold']:.4f}",
            _pct(v["positive_false_reject_rate"]),
            _pct(v["partial_reject_rate"]),
            _pct(v["negative_reject_rate"]),
        )
    console.print(vt)
    console.print(
        "[dim]目标：positive 误拒率尽量 0（有答案必须能答）；negative 拒答率尽量高"
        "（没答案不许编）。partial 落到哪一档取决于阈值高低，属于设计选择。[/dim]"
    )

    # 5) A/B
    if ab:
        if ab.get("error"):
            console.print(f"[red]A/B 未完成：{ab['error']}[/red]")
        else:
            at = Table(title="rerank 开 vs 关（块级，可答查询，配对 bootstrap）")
            for col in ("指标", "开", "关", "Δ", "p 值", "显著"):
                at.add_column(col, justify="right" if col != "指标" else "left")
            for k, v in ab.items():
                at.add_row(
                    k, f"{v['with_rerank']:.4f}", f"{v['without_rerank']:.4f}",
                    f"{v['delta']:+.4f}", f"{v['p_value']:.4f}",
                    "✅" if v["significant"] else "—",
                )
            console.print(at)
            rr = ab.get("rr")
            if rr:
                if rr["significant"] and rr["delta"] > 0.05:
                    console.print(
                        f"[green]rerank 对 MRR 有显著提升（Δ={rr['delta']:+.4f}, "
                        f"p={rr['p_value']:.4f}）：值得默认开启。[/green]"
                    )
                elif rr["significant"] and rr["delta"] <= 0:
                    console.print(
                        "[red]rerank 对 MRR 无正增益甚至变差：考虑关闭或降低 fused_top。[/red]"
                    )
                else:
                    console.print(
                        f"[yellow]rerank 的 MRR 增益不显著（Δ={rr['delta']:+.4f}, "
                        f"p={rr['p_value']:.4f}）：样本量可能不足，或增益确实有限。"
                        "不要仅凭均值大小下结论。[/yellow]"
                    )

    # 6) 建议写回的配置
    console.print("\n[bold]建议写入 config.yaml[/bold]")
    if rec["no_answer_threshold"] is None:
        console.print(
            "[yellow]未给出阈值建议：缺少 positive 类样本"
            "（需至少 1 条含 grade=2 标注的查询）。[/yellow]"
        )
    else:
        console.print(
            "[cyan]retrieval:\n"
            f"  no_answer_threshold: {rec['no_answer_threshold']}"
            f"   # 实测校准；positive 误拒率 "
            f"{_pct(result['verdict']['recommended']['positive_false_reject_rate'])}\n"
            f"  rerank_floor: {rec['rerank_floor']}"
            "   # 实测校准；partial 顶分下沿[/cyan]"
        )


def main() -> None:
    """独立命令行入口（等价于 ``rag_tools calibrate``）。"""
    parser = argparse.ArgumentParser(
        description="用实测 rerank 分数分布校准 no_answer_threshold / rerank_floor"
    )
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径（默认 RAG_/config.yaml，与 CWD 无关）",
    )
    parser.add_argument(
        "--queries", default="eval/queries.graded.jsonl", help="带 grades 的评估集"
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--min-cover", type=float, default=0.95, help="positive 覆盖率下限（默认 0.95）"
    )
    parser.add_argument(
        "--floor-pct", type=float, default=0.25, help="rerank_floor 取 partial 的哪个分位"
    )
    parser.add_argument(
        "--min-floor-gap",
        type=float,
        default=0.05,
        help="强制 rerank_floor ≥ no_answer_threshold + 该间隔（默认 0.05）",
    )
    parser.add_argument("--ab", action="store_true", help="额外跑 rerank 开/关块级 A/B")
    parser.add_argument("--json", default=None, help="把结果落盘供 git 追踪")
    args = parser.parse_args()

    calibrate(
        args.queries,
        top_k=args.top_k,
        config=args.config,
        min_cover=args.min_cover,
        floor_pct=args.floor_pct,
        min_floor_gap=args.min_floor_gap,
        do_ab=args.ab,
        json_out=args.json,
    )


if __name__ == "__main__":
    main()
