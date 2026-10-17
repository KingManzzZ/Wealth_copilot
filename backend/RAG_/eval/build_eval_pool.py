"""候选池生成：把「评估集查询」变成「可人工标注的候选块清单」+ 无标注分数干跑。

**为什么需要这一步**

阈值校准需要「相关块的 rerank 分」与「无关块的 rerank 分」两类分布。要拿到这个，
必须先有人**看过候选块并判定相关与否**。而标注者的候选清单不能在系统排序下生成——
如果只标注 rerank 已经排好的前几条，会形成确认偏误：系统没排进前几名的好块根本不会
进入标注视野，评估集天然低估召回能力。

因此本脚本：

1. 用 ``use_rerank=False``（纯 RRF 融合排序）取候选，**候选深度独立于重排深度**；
2. 导出**正文全文**（标注者必须读全文才能判断「是否回答了问题」，摘要不够）；
3. 输出 ``labels.template.json``——按 ``qid/候选`` 两级组织的空模板，标注者填 0/1/2。

**打分口径（与 ``evaluation.py`` 的 chunk 级匹配保持一致）**

- ``2`` = 直接回答了问题（答案所需信息完整出现在该块内）
- ``1`` = 相关但不完整（同主题、提供背景，但单独不能回答问题）
- ``0`` = 无关

**用法**::

    # 第一步：生成候选池与标注模板（需先完成摄入）
    python eval/build_eval_pool.py --queries eval/queries.calibration.jsonl \\
        --pool-depth 10 --out eval/pool

    # 第二步：人工填写 eval/pool/labels.template.json → 另存为 labels.json
    # 第三步：合并成可评估的查询集
    python eval/apply_grades.py --pool eval/pool --labels eval/pool/labels.json \\
        --out eval/queries.graded.jsonl

    # 第四步：看分布、推荐阈值
    python eval/tune_rerank_threshold.py --queries eval/queries.graded.jsonl

**也可只做「无标注干跑」**：加 ``--dry-run`` 时只导出分数、不要求标注，用于提前确认
评估集可用（查询能召回东西、知识库已摄入、模型能加载）。无标注干跑可以给出
``top_rerank_score`` 的分布，但**给不出可分性结论**（那需要 0/1/2 标签）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from wealth_rag.config import load_settings
from wealth_rag.evaluation import is_forbidden, is_gold, load_queries
from wealth_rag.retrieval.hybrid import Retriever

#: 候选池里每个候选的正文截断长度（字符）。标注需要读全文，但要防止单条把终端打爆。
SNIPPET_CHARS = 700


def _candidate(res, rank: int) -> dict:
    """把一个检索结果转成标注者需要的最小信息集。"""
    text = " ".join((res.raw_text or "").split())
    return {
        "rank": rank,
        "chunk_id": res.chunk_id,
        "citation": res.citation,
        "heading_path": list(res.heading_path or []),
        "chunk_type": res.chunk_type,
        "recall_score": round(float(res.score), 6),
        "rerank_score": None if res.rerank_score is None else round(float(res.rerank_score), 6),
        "text": text,
        "text_truncated": len(text) > SNIPPET_CHARS,
    }


def build_pool(
    queries_path: str | Path,
    out_dir: str | Path,
    pool_depth: int = 10,
    config: str | None = None,
    dry_run: bool = False,
) -> dict:
    settings = load_settings(config)
    cases = load_queries(queries_path)
    if not cases:
        raise SystemExit(f"评估集为空: {queries_path}")

    # 候选池深度独立于重排：用 ``use_rerank=False`` 取「纯 RRF 融合排序」，
    # 候选顺序因此不受重排影响，标注视野也就不会被系统的既有排序所限制（避免确认偏误）。
    # 注意：候选最多只能取到 ``config.retrieval.fused_top``（默认 20）——那是进入重排的
    # 候选池上限；``--pool-depth`` 超过它不会报错，但拿不到更多候选。
    original_fused_top = settings.retrieval.fused_top
    if pool_depth > original_fused_top:
        print(
            f"提示：--pool-depth={pool_depth} 超过 config 的 fused_top={original_fused_top}，"
            f"每条查询实际最多只能标注 {original_fused_top} 个候选。"
        )
    retriever = Retriever(settings)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pool: list[dict] = []
    template: dict[str, dict] = {}
    dry_rows: list[dict] = []
    warnings: list[str] = []

    for case in cases:
        # 关闭重排：候选顺序 = 纯 RRF 融合顺序
        results = retriever.search(case.query, top_k=pool_depth, use_rerank=False)
        if not results:
            warnings.append(f"{case.id}: 未召回任何结果（知识库是否已摄入？）")
        cands = [_candidate(res, i) for i, res in enumerate(results, start=1)]

        # 标注模板：预填 0，标注者只需改需要改的；同时标出「标注集已有的 gold 标注」
        # 作为参考（标注者可推翻——`system_gold` 只是把 query 文件里已有的 gold_headings /
        # relevant 映射过来，纯确定性计算，与是否开启重排无关）。
        template[case.id] = {
            "query": case.query,
            "category": case.category,
            "note": case.note,
            "candidates": {
                c["chunk_id"]: {
                    "grade": 0,
                    "citation": c["citation"],
                    "system_gold": is_gold(res, case),
                }
                for c, res in zip(cands, results)
            },
        }

        pool.append(
            {
                "id": case.id,
                "query": case.query,
                "category": case.category,
                "answerable": case.answerable,
                "note": case.note,
                "candidates": cands,
            }
        )

        # 干跑：用重排分再看一眼（需要重排，单独跑一次）
        if dry_run:
            reranked = retriever.search(case.query, top_k=pool_depth, use_rerank=True)
            scores = [r.rerank_score for r in reranked if r.rerank_score is not None]
            top = max(scores) if scores else None
            top_rank = (scores.index(top) + 1) if top is not None else None
            dry_rows.append(
                {
                    "id": case.id,
                    "query": case.query,
                    "category": case.category,
                    "answerable": case.answerable,
                    "top_rerank_score": None if top is None else round(top, 6),
                    "top_rerank_rank": top_rank,
                    "n_returned": len(reranked),
                    "recall_top_score": round(float(results[0].score), 6) if results else None,
                    "forbidden_hits": [
                        i
                        for i, r in enumerate(reranked, start=1)
                        if case.must_not and is_forbidden(r, case)
                    ],
                }
            )

    (out_dir / "pool.jsonl").write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in pool) + "\n", encoding="utf-8"
    )
    (out_dir / "labels.template.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if dry_rows:
        (out_dir / "dry_run_scores.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in dry_rows) + "\n",
            encoding="utf-8",
        )

    _print_summary(pool, dry_rows, warnings, out_dir, pool_depth, original_fused_top)
    return {"pool": pool, "dry_rows": dry_rows, "warnings": warnings}


def _print_summary(pool, dry_rows, warnings, out_dir, pool_depth, original_fused_top) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    total_cands = sum(len(p["candidates"]) for p in pool)
    console.print(
        f"[bold]候选池已生成[/bold]：{len(pool)} 条查询 × 深度 {pool_depth} "
        f"= {total_cands} 个候选"
    )
    console.print(
        f"[dim]第一阶段 fused_top = {original_fused_top}（候选池深度由 --pool-depth 控制，"
        "标注视野不受重排影响）[/dim]"
    )

    # 分层统计：让使用者立刻看出评估集的类别配比是否失衡
    cat = Table(title="评估集类别分布")
    for col in ("类别", "n", "可答", "平均候选数"):
        cat.add_column(col, justify="right" if col != "类别" else "left")
    by_cat: dict[str, list[dict]] = {}
    for p in pool:
        by_cat.setdefault(p["category"], []).append(p)
    for c, rows in sorted(by_cat.items()):
        n_ans = sum(1 for r in rows if r["answerable"])
        avg = sum(len(r["candidates"]) for r in rows) / len(rows)
        cat.add_row(c, str(len(rows)), str(n_ans), f"{avg:.1f}")
    console.print(cat)

    if dry_rows:
        scores = [r["top_rerank_score"] for r in dry_rows if r["top_rerank_score"] is not None]
        if scores:
            ordered = sorted(scores)
            console.print(
                "[bold]无标注干跑：top_rerank_score 分布[/bold] "
                f"（n={len(scores)}）min={ordered[0]:.4f} "
                f"p25={ordered[len(ordered) // 4]:.4f} "
                f"p50={ordered[len(ordered) // 2]:.4f} "
                f"p75={ordered[3 * len(ordered) // 4]:.4f} "
                f"max={ordered[-1]:.4f}"
            )
            console.print(
                "[yellow]⚠️ 无标注干跑只能看分数是否落在合理区间，"
                "**不能**据此定阈值——可分性必须靠 0/1/2 标注。[/yellow]"
            )

    if warnings:
        console.print(f"[yellow]警告 {len(warnings)} 条：[/yellow]")
        for w in warnings[:15]:
            console.print(f"  {w}")

    console.print(
        "\n[bold]下一步[/bold]：\n"
        f"  1. 读 {out_dir / 'pool.jsonl'}，按 0/1/2 填写 {out_dir / 'labels.template.json'}，"
        "另存为 labels.json\n"
        f"  2. python eval/apply_grades.py --pool {out_dir} --labels "
        f"{out_dir / 'labels.json'} --out eval/queries.graded.jsonl\n"
        "  3. python eval/tune_rerank_threshold.py --queries eval/queries.graded.jsonl"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="生成 chunk 级评估的候选池与人工标注模板（阈值校准第一步）"
    )
    parser.add_argument("--config", default=None, help="配置文件路径（默认 ./config.yaml）")
    parser.add_argument("--queries", required=True, help="评估集 JSONL 路径")
    parser.add_argument("--out", default="eval/pool", help="输出目录")
    parser.add_argument(
        "--pool-depth",
        type=int,
        default=10,
        help="每条查询导出多少个候选供标注（建议 ≥ 最终评估窗口 k 的 2 倍）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="额外跑一遍重排并导出 top_rerank_score（不需要标注，但给不出可分性结论）",
    )
    args = parser.parse_args()

    if args.pool_depth < 5:
        raise SystemExit("--pool-depth 至少为 5：候选太少会让评估集系统性低估召回能力")

    build_pool(
        args.queries,
        args.out,
        pool_depth=args.pool_depth,
        config=args.config,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
