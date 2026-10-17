"""临时脚本：诊断 heading_path 结构、面包屑质量、标题与正文关系。"""
import collections
import sys
from pathlib import Path

# 按脚本位置定位 src/，不按 CWD
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from wealth_rag.config import load_settings
from wealth_rag.store.qdrant_store import QdrantStore

s = load_settings()
store = QdrantStore(s)
client = store.client
name = store.collection

payloads = []
offset = None
while True:
    pts, offset = client.scroll(
        collection_name=name, limit=512, offset=offset,
        with_payload=True, with_vectors=False,
    )
    for p in pts:
        payloads.append(p.payload or {})
    if offset is None:
        break

print("TOTAL =", len(payloads))

# ---- 1. heading_path 深度分布 ----
depth = collections.Counter(len(p.get("heading_path") or []) for p in payloads)
print("\n== heading_path 深度分布 ==")
for k in sorted(depth):
    print(f"  depth={k}: {depth[k]}  ({depth[k]*100//len(payloads)}%)")

# ---- 2. 每个文档的深度分布 ----
print("\n== 按文档看深度 ==")
by_doc = collections.defaultdict(collections.Counter)
titles = {}
for p in payloads:
    by_doc[p.get("doc_id")][len(p.get("heading_path") or [])] += 1
    titles.setdefault(p.get("doc_id"), p.get("doc_title"))
for d, c in by_doc.items():
    print(f"  {titles.get(d):<22} {dict(sorted(c.items()))}")

# ---- 3. 面包屑 == doc_title（无信息量）的块 ----
same_as_title = 0
for p in payloads:
    hp = p.get("heading_path") or []
    if hp and hp == [p.get("doc_title")]:
        same_as_title += 1
print(f"\n== heading_path 恰好等于 doc_title（面包屑零信息量）: {same_as_title} ==")

# ---- 4. 高频重复 heading_path（疑似页眉页脚被当成标题） ----
cnt = collections.Counter(" > ".join(p.get("heading_path") or []) for p in payloads if p.get("heading_path"))
print("\n== 出现次数最多的 heading_path (Top 25) ==")
for hp, n in cnt.most_common(25):
    print(f"  {n:>4}  {hp[:90]}")

print(f"\n== 不同 heading_path 总数: {len(cnt)} / 有标题的块数: {sum(cnt.values())} ==")

# ---- 5. 小文档完整内容（看干净 Markdown 的表现） ----
for want in ("资产配置基础", "个人养老金制度要点摘录", "复利与长期投资"):
    print(f"\n{'='*70}\n### 文档：{want}\n{'='*70}")
    group = [p for p in payloads if p.get("doc_title") == want]
    for i, p in enumerate(group):
        print(f"\n[chunk {i}] type={p.get('chunk_type')} page={p.get('page')} parent={'Y' if p.get('parent_id') else 'N'}")
        print(f"  heading_path = {p.get('heading_path')}")
        print(f"  raw_text     = {(p.get('raw_text') or '')[:300]!r}")
        print(f"  text         = {(p.get('text') or '')[:300]!r}")

# ---- 6. raw_text 是否包含自身标题 ----
missing_title = 0
for p in payloads:
    hp = p.get("heading_path") or []
    if not hp:
        continue
    leaf = hp[-1]
    raw = p.get("raw_text") or ""
    if leaf and leaf not in raw:
        missing_title += 1
print(f"\n== raw_text 中不含自身标题文本的块: {missing_title} / {len(payloads)} ==")

# ---- 7. parent_text 是否含标题 ----
with_parent = [p for p in payloads if p.get("parent_text")]
print(f"== 有 parent_text 的块: {len(with_parent)} ==")
miss = 0
for p in with_parent:
    hp = p.get("heading_path") or []
    leaf = hp[-1] if hp else ""
    if leaf and leaf not in (p.get("parent_text") or ""):
        miss += 1
print(f"   parent_text 中不含标题的块: {miss} / {len(with_parent)}")
