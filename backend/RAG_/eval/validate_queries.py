"""评估集标签校验：确认每条 gold / must_not 都能在索引中找到对应块。

**为什么必须有这一步**

如果 gold 标题写错（拼写、空格、章号），检索再准也永远不会命中——评估结果会显示
"检索失败"，而真实原因是**标签错误**。两者必须能区分开。

用法（项目根目录）::

    python eval/validate_queries.py [--queries eval/queries.jsonl]
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wealth_rag.config import load_settings  # noqa: E402
from wealth_rag.evaluation import _norm, load_queries  # noqa: E402
from wealth_rag.store.qdrant_store import QdrantStore  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="校验评估集标签是否与索引一致")
    ap.add_argument("--config", default=None)
    ap.add_argument("--queries", default=str(ROOT / "eval" / "queries.jsonl"))
    args = ap.parse_args()

    settings = load_settings(args.config)
    store = QdrantStore(settings)

    paths: list[str] = []
    offset = None
    while True:
        pts, offset = store.client.scroll(
            store.collection, limit=512, offset=offset,
            with_payload=["heading_path", "chunk_type"], with_vectors=False,
        )
        for p in pts:
            pl = p.payload or {}
            if pl.get("chunk_type") != "parent":
                paths.append(_norm(" > ".join(pl.get("heading_path") or [])))
        if offset is None:
            break

    cases = load_queries(args.queries)
    print(f"索引子块 {len(paths)} 个（按标题路径归一化）")
    print(f"评估集 {len(cases)} 条")

    bad_gold: list[tuple[str, str]] = []
    bad_must: list[tuple[str, str]] = []
    multi = 0

    for c in cases:
        for g in c.gold_headings:
            ng = _norm(g)
            n_match = sum(1 for h in paths if ng and ng in h)
            if n_match == 0:
                bad_gold.append((c.id, g))
            elif n_match > 1:
                multi += 1
        for m in c.must_not:
            nm = _norm(m)
            if not any(nm and nm in h for h in paths):
                bad_must.append((c.id, m))

    cats = collections.Counter(c.category for c in cases)
    ans = sum(1 for c in cases if c.answerable)
    print(f"可答 {ans} 条 / 不可答 {len(cases) - ans} 条")
    print("分类: " + ", ".join(f"{k}×{v}" for k, v in sorted(cats.items())))

    print(f"\n=== gold 标签问题：{len(bad_gold)} 处（0 个块匹配 → 标签写错或该小节不存在） ===")
    for cid, g in bad_gold:
        print(f"  {cid}  gold={g!r}")

    print(f"\n=== must_not 标签：{len(bad_must)} 处无匹配对象 ===")
    for cid, m in bad_must:
        print(f"  {cid}  must_not={m!r}")
    if bad_must:
        print("  [信息] 这**不是错误**——说明该污染源（如目录页）已被切分阶段剔除，")
        print("        约束暂时落空。保留它是为了在回归时能立刻发现污染复现。")

    if multi:
        print(f"\n[提示] 有 {multi} 个 gold 匹配到多个块（同一小节的多个子块，属正常）")

    # 只有 gold 标签错误才算失败：写错 gold 会让「检索失败」与「标签错误」无法区分
    ok = not bad_gold
    print("\n" + ("标签校验通过" if ok else "gold 标签存在问题，请修正后再评估"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
