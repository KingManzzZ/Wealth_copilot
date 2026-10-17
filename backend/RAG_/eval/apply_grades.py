"""把人工标注（0/1/2）合并进评估集，并校验标注覆盖率。

**为什么要单独一步，而不是直接读 labels.json**

``evaluation.py`` 已经实现了完整的 chunk 级评估（Hit/Recall/Precision/MRR/nDCG/MAP +
bootstrap 置信区间 + 配对 bootstrap 检验）。因此**不要另写一套评估器**——把人工标注
翻译成 ``evaluation.py`` 认识的查询集格式，让它去算指标即可。这样：

- 指标口径只有一处实现，不会两边算得不一样；
- 消融对比（rerank 开/关、`fused_top` 变化）直接复用 ``--json`` + ``--compare-with`` 的配对检验；
- 评估集文件本身是自描述的（含 ``grades``），可归档、可复现。

**输出格式**（``evaluation.py::load_queries`` 兼容的超集）::

    {"id","query","category","note","grades":{chunk_id:0|1|2},
     "gold_headings":[...],"must_not":[...]}

``grades`` 是新增字段；``apply_grades`` 会依据它**同时生成** ``gold_headings``
（grade ≥ 1 的候选的标题路径），这样现成的评估器无需改动就能做 chunk 级匹配。
``build_eval_pool`` 导出的 ``candidates[].chunk_id`` 与检索返回的 ``chunk_id``
是同一套 ID（Qdrant 点 ID），直接可用。

**覆盖率校验是本脚本的核心价值**：未标注（仍是 0 但从未被人看过）与「标注为 0（无关）」
在文件里长得一样。若不加区分，一个「只标了 3 条、其余全是默认 0」的标签文件会被
当成高精度评估集，得出完全错误的阈值。因此要求标注者把**看过的**候选填进
``reviewed`` 列表，本脚本据此计算真实覆盖率。

**候选池与标签必须对应**：本脚本固定读 ``pool_dir/pool.jsonl``。如果传入的 labels.json
来自另一个候选池（或该查询的 ``system_gold`` 未落盘），本查询的 grade 会**全部取自默认值**，
静默产出"整题无关"的结果。因此当同一查询的标签与池**完全无交集**时直接报错——
宁可停下，也不要产出一份看起来正常却错误的标注。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

GRADE_LABELS = {0: "无关", 1: "相关但不完整", 2: "直接回答问题"}

#: 低于该覆盖率只警告不阻断（阈值校准的样本质量由使用者负责）
WARN_COVERAGE = 0.6
#: 低于该覆盖率直接报错——此时任何分布结论都不可信
MIN_COVERAGE = 0.3


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def merge_grades(
    pool_dir: str | Path,
    labels_path: str | Path,
    out_path: str | Path,
    min_relevant: int = 1,
    seed_from_gold: bool = False,
) -> dict:
    """把标注合并进评估集。

    ``seed_from_gold=True`` 时，对**未被人工标注**的候选启用「由 gold 派生」的初值：
    ``system_gold=True`` 的候选记 ``grade=2``，其余记 ``grade=0``，并把这些候选
    计入 ``reviewed``（表示「已由 gold 派生确认」）。

    这不是免费午餐——派生的 grade 只是二值 gold 的投影，**无法区分「直接回答」与
    「相关但不完整」**，因此：

    - ``rerank_floor``（依赖 partial 档）仍需真实的人工 grade=1 标注；
    - ``no_answer_threshold``（依赖 positive vs negative 的顶分分离）可以先用派生值跑出
      第一版，因为 ``system_gold`` 的正例/负例划分是可信的。

    实现在这里是为了避免让使用者手工把 ``system_gold`` 翻译成 0/2。
    """
    pool_dir = Path(pool_dir)
    pool = [
        json.loads(line)
        for line in (pool_dir / "pool.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    labels = load_json(Path(labels_path))

    # 保留原评估集的 must_not / answerable 等元信息（这些不在 pool.jsonl 里）
    out_rows: list[dict] = []
    stats = {
        "queries": 0,
        "candidates": 0,
        "reviewed": 0,
        "derived": 0,
        "grade_hist": {0: 0, 1: 0, 2: 0},
        "queries_without_relevant": [],
        "missing_label_queries": [],
        "unknown_chunk_ids": [],
    }

    for entry in pool:
        qid = entry["id"]
        lab = labels.get(qid)
        stats["queries"] += 1
        if lab is None:
            stats["missing_label_queries"].append(qid)
            continue

        cand_grades: dict[str, int] = {}
        reviewed = set(lab.get("reviewed") or [])
        pool_ids = {c["chunk_id"] for c in entry["candidates"]}
        label_cands = lab.get("candidates") or {}

        # 候选池与标签必须对应。二者一旦错位（例如标签文件是从另一个 pool 生成的，
        # 或 label_cands 整体为空），本查询的 grade 会**全部取自默认值**，
        # 静默产出"整题无关"的结果——而且因为没有异常，使用者不会察觉。
        # 这里把错位变成显式错误：宁可停下，也不要产出看起来正常却错误的标注。
        if pool_ids and not (pool_ids & set(label_cands)):
            raise SystemExit(
                f"{qid}: 标签文件与本查询的候选池完全不匹配"
                f"（标签里有 {len(label_cands)} 个 chunk_id，池里有 {len(pool_ids)} 个，无一交集）。\n"
                f"  最可能的原因：labels.json 不是由当前的 {pool_dir / 'pool.jsonl'} 生成的，"
                "或该查询的 system_gold 未落盘。\n"
                "  请重新生成候选池与模板：\n"
                "    python eval/build_eval_pool.py --queries <评估集> --out <pool 目录>"
            )

        for c in entry["candidates"]:
            cid = c["chunk_id"]
            rec = label_cands.get(cid) or {}
            touched = cid in reviewed or int(rec.get("grade", 0)) != 0
            if not touched and seed_from_gold:
                # 未被人工确认 → 用 system_gold 派生（仅二值：2 或 0）
                grade = 2 if rec.get("system_gold") else 0
                reviewed.add(cid)
                stats["derived"] += 1
            else:
                grade = int(rec.get("grade", 0))
            if grade not in GRADE_LABELS:
                raise SystemExit(f"{qid}/{cid}: grade 必须是 0/1/2，收到 {grade!r}")
            cand_grades[cid] = grade
            stats["candidates"] += 1
            # 「被看过」= 显式列入 reviewed，或填了非 0 分（非 0 必然看过）
            if cid in reviewed or grade != 0:
                stats["reviewed"] += 1
            stats["grade_hist"][grade] += 1

        # 标签文件里出现了候选池之外的 chunk_id → 说明候选池与标签不同步
        for extra in label_cands:
            if extra not in cand_grades:
                stats["unknown_chunk_ids"].append(f"{qid}/{extra}")

        # 标签文件里出现了候选池之外的 chunk_id → 说明候选池与标签不同步
        for extra in (lab.get("candidates") or {}):
            if extra not in cand_grades:
                stats["unknown_chunk_ids"].append(f"{qid}/{extra}")

        relevant = [cid for cid, g in cand_grades.items() if g >= 1]
        if len(relevant) < min_relevant:
            stats["queries_without_relevant"].append(qid)

        # 生成 evaluation.py 原生认识的 gold_headings —— 保持指标口径单一实现
        gold_headings: list[str] = []
        for c in entry["candidates"]:
            if cand_grades.get(c["chunk_id"], 0) >= 1 and c["heading_path"]:
                hp = " > ".join(c["heading_path"])
                if hp and hp not in gold_headings:
                    gold_headings.append(hp)

        out_rows.append(
            {
                "id": qid,
                "query": entry["query"],
                "category": entry.get("category", "未分类"),
                "note": entry.get("note", ""),
                "grades": cand_grades,
                "gold_headings": gold_headings,
                # 供 tune_rerank_threshold.py 直接消费，无需回头再读 pool.jsonl
                "candidate_order": [c["chunk_id"] for c in entry["candidates"]],
            }
        )

    coverage = stats["reviewed"] / stats["candidates"] if stats["candidates"] else 0.0
    stats["coverage"] = coverage

    if coverage < MIN_COVERAGE:
        raise SystemExit(
            f"标注覆盖率仅 {coverage:.1%}（{stats['reviewed']}/{stats['candidates']}），"
            f"低于 {MIN_COVERAGE:.0%} 下限：此时分数分布几乎全部来自默认值 0，"
            "任何阈值结论都不可信。请补全标注后重试。"
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in out_rows) + "\n",
        encoding="utf-8",
    )

    _print_report(stats, out_path, coverage)
    return stats


def _print_report(stats, out_path, coverage) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    hist = Table(title="标注分布")
    hist.add_column("分值")
    hist.add_column("含义")
    hist.add_column("n", justify="right")
    hist.add_column("占比", justify="right")
    total = sum(stats["grade_hist"].values()) or 1
    for g in (2, 1, 0):
        n = stats["grade_hist"][g]
        hist.add_row(str(g), GRADE_LABELS[g], str(n), f"{n / total:.1%}")
    console.print(hist)

    color = "green" if coverage >= WARN_COVERAGE else "yellow"
    console.print(
        f"[{color}]标注覆盖率 {coverage:.1%}[/{color}]"
        f"（{stats['reviewed']}/{stats['candidates']} 个候选被确认）"
    )
    if stats.get("derived"):
        console.print(
            f"[yellow]{stats['derived']} 个候选的 grade 由 system_gold 派生"
            "（仅二值：gold→2 / 其余→0）。这足以划分 positive 与 negative，"
            "但**区分不出 grade=1**，因此 rerank_floor 仍需真实人工标注。[/yellow]"
        )
    if coverage < WARN_COVERAGE:
        console.print(
            "[yellow]⚠️ 覆盖率偏低：未确认的候选被当作「无关」，"
            "会高估精确率、低估召回率。建议补全后再做阈值决策。[/yellow]"
        )

    n_relevant = stats["grade_hist"][1] + stats["grade_hist"][2]
    if n_relevant == 0:
        console.print("[red]❌ 没有任何 grade ≥ 1 的候选：无法计算可分性。[/red]")

    if stats["missing_label_queries"]:
        console.print(
            f"[yellow]未提供标签的查询 {len(stats['missing_label_queries'])} 条"
            f"（已跳过）：{', '.join(stats['missing_label_queries'][:10])}[/yellow]"
        )
    if stats["queries_without_relevant"]:
        console.print(
            f"[yellow]{len(stats['queries_without_relevant'])} 条查询没有任何相关候选"
            "（若这些是负例则正常；否则说明该问题库里确实没有答案）："
            f"{', '.join(stats['queries_without_relevant'][:10])}[/yellow]"
        )
    if stats["unknown_chunk_ids"]:
        console.print(
            f"[red]标签里出现候选池之外的 chunk_id {len(stats['unknown_chunk_ids'])} 个"
            "——候选池与标签不同步，请重新生成候选池：[/red]"
        )
        for x in stats["unknown_chunk_ids"][:10]:
            console.print(f"  {x}")

    console.print(
        f"\n[bold]已写出[/bold] {out_path}（{stats['queries']} 条查询）\n"
        "[bold]下一步[/bold]：python eval/tune_rerank_threshold.py "
        f"--queries {out_path}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="合并人工标注并校验覆盖率")
    parser.add_argument("--pool", default="eval/pool", help="build_eval_pool.py 的输出目录")
    parser.add_argument("--labels", required=True, help="人工填写后的 labels.json")
    parser.add_argument("--out", default="eval/queries.graded.jsonl", help="输出评估集")
    parser.add_argument(
        "--seed-from-gold",
        action="store_true",
        help=(
            "对未被人工标注的候选，用 system_gold 派生 grade（gold→2，其余→0）。"
            "可立刻得到可用的 positive/negative 划分，用于校准 no_answer_threshold；"
            "但派生值区分不出 grade=1，rerank_floor 仍需真实人工标注。"
        ),
    )
    args = parser.parse_args()
    merge_grades(
        args.pool, args.labels, args.out, seed_from_gold=args.seed_from_gold
    )


if __name__ == "__main__":
    main()
