"""临时脚本：真实检索观测（只读副本），检查标题缺失与同节重复问题。"""
import sys
import collections
from pathlib import Path

# 按脚本位置定位 src/，不按 CWD
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from wealth_rag.config import load_settings
from wealth_rag.retrieval.hybrid import Retriever

s = load_settings()
print("device =", s.resolve_device(), flush=True)
r = Retriever(s)

QUERIES = [
    "人民币防伪特征有哪些",
    "个人信用报告怎么查询",
    "存款保险制度的限额是多少",
    "第一章 人民币主要讲了什么",
    "什么是再贴现",
    "黄金期货交易的特点",
]

for q in QUERIES:
    print("\n" + "=" * 78)
    print(f"查询：{q}")
    print("=" * 78, flush=True)
    try:
        hits = r.search(q, top_k=8)
    except Exception as e:
        import traceback
        traceback.print_exc()
        break
    print(f"命中 {len(hits)} 条")
    hp_counter = collections.Counter()
    for i, h in enumerate(hits, 1):
        hp = " > ".join(h.heading_path)
        hp_counter[hp] += 1
        starts_with_title = hp and (h.raw_text or "").strip().startswith(hp.split(" > ")[-1])
        print(f"\n [{i}] score={h.score:.4f} rerank={h.rerank_score if h.rerank_score is None else round(h.rerank_score,4)} "
              f"type={h.chunk_type} page={h.page}")
        print(f"     citation    = {h.citation[:110]}")
        print(f"     heading_path= {hp[:110]}")
        print(f"     raw_text 含标题? {starts_with_title}   parent_text? {'Y' if h.parent_text else 'N'}")
        print(f"     raw_text >>> {' '.join((h.raw_text or '').split())[:150]}")
    dup = {k: v for k, v in hp_counter.items() if v > 1}
    if dup:
        print(f"\n   [!] 同一 heading_path 重复命中: {[(k[:40], v) for k, v in dup.items()]}")
    else:
        print("\n  （无同节重复）")
