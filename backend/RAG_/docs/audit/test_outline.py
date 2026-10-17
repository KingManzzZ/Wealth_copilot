"""回归测试：章级大纲重建（outline.py）。

用法（项目根目录）：
    python docs\\audit\\test_outline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from wealth_rag.config import load_settings  # noqa: E402
from wealth_rag.ingestion.outline import (  # noqa: E402
    build_outline,
    extract_running_chapters,
    extract_toc,
    inject_chapter_headings,
)

settings = load_settings(str(ROOT / "config.yaml"))
pdfs = sorted(settings.paths.data_dir.glob("*.pdf"))
if not pdfs:
    print("data_dir 下没有 PDF，跳过")
    sys.exit(0)
pdf = pdfs[0]
print(f"样本: {pdf.name}")

import pymupdf  # noqa: E402

doc = pymupdf.open(str(pdf))
print(f"页数 {doc.page_count}；书签 {len(doc.get_toc(simple=True) or [])} 条")

toc = extract_toc(doc)
chaps = [e for e in toc if e.level == 1]
print(f"\n[A] 目录解析：共 {len(toc)} 条，其中章 {len(chaps)} 个")
for c in chaps:
    print(f"    {c.number} {c.title:<28} 印刷页 {c.printed_page}")

hdr = extract_running_chapters(doc)
print(f"\n[B] 页眉信号：{len(hdr)} 页带章号；不同章 {len(set(hdr.values()))} 个")
doc.close()

outline = build_outline(pdf)
print(f"\n[C] 大纲构建：source={outline.source}，覆盖 {len(outline.page_chapter)} 页")
seen = []
for pno in sorted(outline.page_chapter):
    ch = outline.page_chapter[pno]
    if not seen or seen[-1] is not ch:
        seen.append(ch)
        print(f"    p{pno:<4} → {ch.heading}")
print(f"    共 {len(seen)} 章")

# 断言（对应实测已知事实）
ok = True
def chk(label, cond):
    global ok
    ok = ok and cond
    print(f"  [{'OK ' if cond else 'FAIL'}] {label}")

print("\n[D] 断言")
chk("识别出 12 章", len(seen) == 12)
chk("第二章首现于 p32", outline.page_chapter.get(32) and outline.page_chapter[32].number == "第二章")
chk("第一章覆盖 p10", outline.page_chapter.get(10) and outline.page_chapter[10].number == "第一章")
chk("覆盖页数 ≥ 240", len(outline.page_chapter) >= 240)

# 注入测试
md = "\n".join(
    ["<!--PAGEBREAK-->", "正文第1页"]
    + sum([["<!--PAGEBREAK-->", f"正文第{i}页"] for i in range(2, 40)], [])
)
merged, n = inject_chapter_headings(md, outline, "<!--PAGEBREAK-->")
print(f"\n[E] 注入：{n} 个章标题")
for ln in merged.split("\n"):
    if ln.startswith("# "):
        print(f"    {ln}")

print("\n" + ("ALL PASSED" if ok else "FAILED"))
sys.exit(0 if ok else 1)
