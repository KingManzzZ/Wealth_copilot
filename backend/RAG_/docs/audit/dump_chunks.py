"""临时脚本：通过 Qdrant API 导出已入库 chunk 的真实形态（只读副本）。"""
import collections
import re
import sys

sys.path.insert(0, "src")
from wealth_rag.config import load_settings
from wealth_rag.store.qdrant_store import QdrantStore

s = load_settings()  # 走 WEALTH_RAG_CONFIG
store = QdrantStore(s)
client = store.client
name = store.collection

ids, payloads = [], []
offset = None
while True:
    pts, offset = client.scroll(
        collection_name=name, limit=512, offset=offset,
        with_payload=True, with_vectors=False,
    )
    for p in pts:
        ids.append(str(p.id))
        payloads.append(p.payload or {})
    if offset is None:
        break

print(f"TOTAL POINTS = {len(payloads)}")

titles = {}
for p in payloads:
    titles.setdefault(p.get("doc_id"), p.get("doc_title"))
print("\n== points per doc ==")
for k, v in collections.Counter(p.get("doc_id") for p in payloads).most_common():
    print(f"  {k}  n={v:<6} {titles.get(k)}")

print("\n== chunk_type ==", dict(collections.Counter(p.get("chunk_type") for p in payloads)))

# 面包屑统计
def body_of(p):
    t = (p.get("text") or "").strip()
    hp = p.get("heading_path") or []
    if hp:
        bc = " > ".join(hp)
        if t.startswith(bc):
            t = t[len(bc):].strip()
    return t

with_hp = [p for p in payloads if p.get("heading_path")]
no_hp = [p for p in payloads if not p.get("heading_path")]
print(f"\n== heading_path 非空: {len(with_hp)} / 空: {len(no_hp)} ==")

# raw_text 与 text 的差异（面包屑是否进入 raw_text）
def strip_bc(p):
    t = (p.get("text") or "").strip()
    hp = p.get("heading_path") or []
    if hp:
        bc = " > ".join(hp)
        if t.startswith(bc):
            return t[len(bc):].strip()
    return t

same = sum(1 for p in payloads if strip_bc(p) == (p.get("raw_text") or "").strip())
print(f"== text 去掉面包屑后 == raw_text 的块: {same} / {len(payloads)}")
print("   → 说明 raw_text 是否含标题")

lens = sorted(len(body_of(p)) for p in payloads)
if lens:
    import statistics
    print(f"\n== 正文长度（字符）min={lens[0]} p50={lens[len(lens)//2]} p90={lens[int(len(lens)*0.9)]} max={lens[-1]}")

short = sorted(((len(body_of(p)), p, body_of(p)) for p in payloads), key=lambda x: x[0])
print(f"\n== 正文<40 字符的块: {sum(1 for x in short if x[0] < 40)} ==")
for ln, p, b in short[:30]:
    print(f"  [{ln:>4}] hp={' > '.join(p.get('heading_path') or [])[:70]!r}")
    print(f"        body={b[:90]!r}")

print("\n== 抽样 30 块 ==")
step = max(1, len(payloads) // 30)
for i in range(0, len(payloads), step):
    p = payloads[i]
    hp = " > ".join(p.get("heading_path") or [])
    txt = (p.get("text") or "").replace("\n", "\\n")
    raw = (p.get("raw_text") or "").replace("\n", "\\n")
    print(f"\n--- [{i}] type={p.get('chunk_type')} page={p.get('page')} len_text={len(p.get('text') or '')} parent={'Y' if p.get('parent_id') else 'N'}")
    print(f"    hp  = {hp[:110]}")
    print(f"    text>>> {txt[:180]}")
    print(f"    raw >>> {raw[:180]}")

has_hash = [p for p in payloads if re.search(r"(?m)^#{1,6}\s", p.get("raw_text") or "")]
print(f"\n== raw_text 残留 Markdown 标题的块: {len(has_hash)} ==")
for p in has_hash[:5]:
    print("   ", repr((p.get("raw_text") or "")[:160]))
