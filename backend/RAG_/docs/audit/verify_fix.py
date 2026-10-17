"""验收检查：修复后的索引是否达到预期指标。

与 dump_chunks.py 的区别：本脚本**区分父块与子块**，并按新版数据格式
（``raw_text = "## 叶子标题\\n\\n正文"``、父块独立存储）做判定。

用法（项目根目录）：
    python docs\\audit\\verify_fix.py
"""
from __future__ import annotations

import collections
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from wealth_rag.config import load_settings  # noqa: E402
from wealth_rag.ingestion.tokenizer import TokenCounter  # noqa: E402
from wealth_rag.store.qdrant_store import QdrantStore  # noqa: E402

settings = load_settings()
store = QdrantStore(settings)

payloads: list[dict] = []
offset = None
while True:
    pts, offset = store.client.scroll(
        store.collection, limit=512, offset=offset, with_payload=True, with_vectors=False
    )
    payloads.extend(p.payload or {} for p in pts)
    if offset is None:
        break

parents = [p for p in payloads if p.get("chunk_type") == "parent"]
children = [p for p in payloads if p.get("chunk_type") != "parent"]

print(f"索引点总数 {len(payloads)} = 子块 {len(children)} + 父块 {len(parents)}")
print(f"chunk_type 分布: {dict(collections.Counter(p.get('chunk_type') for p in payloads))}")


def leaf_of(p: dict) -> str:
    hp = p.get("heading_path") or []
    return hp[-1] if hp else ""


def body_of(p: dict) -> str:
    """去掉前置的 ``## 叶子标题``，取正文。"""
    raw = (p.get("raw_text") or "").strip()
    m = re.match(r"^#{1,6}\s+(.*?)\n+(.*)$", raw, re.S)
    return (m.group(2) if m else raw).strip()


# ---------- 1. 标题进入证据文本（P0-3） ----------
missing = [p for p in children if leaf_of(p) and f"## {leaf_of(p)}" not in (p.get("raw_text") or "")]
print(f"\n[P0-3] 子块 raw_text 未前置叶子标题: {len(missing)} / {len(children)}")

# 父块已改为独立点存储：必须**回查**才能验证（不能看子块 payload）
with_parent = [p for p in children if p.get("parent_id")]
print(f"[P2-2] 带 parent_id 的子块: {len(with_parent)}；去重后父块点: {len(parents)}")
print(f"       子块 payload 是否残留 parent_text: "
      f"{sum(1 for p in children if p.get('parent_text'))}（应为 0）")
ids = sorted({p["parent_id"] for p in with_parent})
got = store.get_parents(ids) if ids else {}
print(f"       回查成功: {len(got)} / {len(ids)}")
bad = [pid for pid in ids if pid in got and leaf_of(next(p for p in with_parent if p["parent_id"] == pid)) not in got[pid]]
print(f"       父块文本缺叶子标题: {len(bad)}")

# ---------- 2. 面包屑质量 ----------
depth = collections.Counter(len(p.get("heading_path") or []) for p in children)
print(f"\n[P0-4] 子块 heading_path 深度分布: {dict(sorted(depth.items()))}")
print(f"       深度为 1 的占比: {round(100 * depth.get(1, 0) / max(1, len(children)), 1)}%")

doc_titles = {p.get("doc_id"): p.get("doc_title") for p in children}
hdr = [p for p in children if (p.get("heading_path") or []) == [doc_titles.get(p.get("doc_id"))]]
print(f"[P0-1] 面包屑等于文档标题（页眉污染 / 文档级引言）: {len(hdr)}")
toc = [p for p in children if re.search(r"目\s*录", " ".join(p.get("heading_path") or []))]
print(f"[P0-2] 目录块: {len(toc)}")

# ---------- 3. 块大小 ----------
tc = TokenCounter(settings.paths.cache_dir)
tok = sorted(tc.count(p.get("raw_text") or "") for p in children)
n = len(tok)
limit = settings.chunking.max_tokens
print(f"\n[P2-4] 子块 token: min={tok[0]} p50={tok[n//2]} p90={tok[int(n*0.9)]} max={tok[-1]}"
      f"；上限 {limit}（放宽 {int(limit*1.25)}），超出 {sum(1 for t in tok if t > int(limit*1.25))} 个")

bodies = sorted(len(body_of(p)) for p in children)
print(f"       子块正文字符: min={bodies[0]} p50={bodies[n//2]} p90={bodies[int(n*0.9)]} max={bodies[-1]}")
print(f"       其中正文 <40 字符: {sum(1 for b in bodies if b < 40)}"
      f"（文档本身含大量「一个标题 + 一句话」的细粒度小节，属正常）")

# ---------- 4. 噪声（严格判定） ----------
NOISE = re.compile(r"^(?:<!--.*?-->|续表|[\d\s.、,，;；:：\-—_]+)$", re.I)
noise = [p for p in children if NOISE.match((p.get("raw_text") or "").strip())]
print(f"\n[P1-2] 纯噪声块（整块仅为占位符/页码/续表）: {len(noise)}")

# ---------- 5. 文书作用域 ----------
roots = collections.Counter(
    (p.get("heading_path") or ["?"])[1] for p in children if len(p.get("heading_path") or []) >= 2
)
law = {k: v for k, v in roots.items() if k.endswith(("法", "条例", "办法", "规定"))}
print(f"\n[P0-5] 文书作用域: {law}")

# ---------- 6. 面包屑冗余提示 ----------
dup = sum(
    1 for p in children
    if len(p.get("heading_path") or []) >= 2
    and p["heading_path"][0] == p.get("doc_title")
)
print(f"\n[提示] 面包屑首层与 doc_title 重复的块: {dup} / {len(children)}"
      f"（由 include_doc_title=true 造成，如觉冗余可设为 false）")

# ---------- 7. 【P0-8】章级覆盖：重建出的 12 章是否真的进了面包屑 ----------
CN = r"零一二三四五六七八九十百0-9"
chap_re = re.compile(rf"^第\s*[{CN}]+\s*章\b")
chap_of = collections.Counter()
for p in children:
    for seg in (p.get("heading_path") or []):
        if chap_re.match(seg.strip()):
            chap_of[seg.strip()] += 1
            break
print(f"\n[P0-8] 面包屑中出现「第X章」的块: {sum(chap_of.values())} / {len(children)}"
      f"（{round(100*sum(chap_of.values())/max(1,len(children)),1)}%）")
print(f"       覆盖到的章: {len(chap_of)} 个")
for c, n in sorted(chap_of.items()):
    print(f"         {n:>4}  {c}")

