"""临时脚本：验证切分器的标题处理行为 + 检查 PDF 真实标题层级。"""
import sys
from pathlib import Path

# 按脚本位置定位 src/，不按 CWD
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from wealth_rag.config import load_settings
from wealth_rag.ingestion.chunker import MarkdownChunker

s = load_settings()
ch = MarkdownChunker(s)

MD = """# 第一章 总则

## 第一节 目的

### 1.1 适用范围

本条适用于所有商业银行。

#### 1.1.1 例外

政策性银行除外。

##### 1.1.1.1 深度五级标题

这一行属于 H5 标题的正文。

###### 1.1.1.1.1 深度六级标题

这一行属于 H6 标题的正文。
"""

print("=" * 70)
print("测试 1：标题层级处理（heading_max_level =", s.chunking.heading_max_level, "）")
print("=" * 70)
for c in ch.chunk(MD, "doc1", "t.md", "测试文档"):
    print(f"\n  heading_path = {c.heading_path}")
    print(f"  chunk_type   = {c.chunk_type}")
    print(f"  raw_text     = {c.raw_text!r}")
    print(f"  text         = {c.text!r}")
    print(f"  parent_text  = {c.parent_text!r}")

print("\n" + "=" * 70)
print("测试 2：overlap_tokens 是否生效")
print("=" * 70)
big = "# 标题\n\n" + "".join(f"第{i}句测试内容，用于填充长度。" for i in range(200))
s2 = load_settings()
s2.chunking.overlap_tokens = 128
ch2 = MarkdownChunker(s2)
cs = ch2.chunk(big, "doc2", "b.md", "重叠测试")
print(f"  overlap_tokens=128 时切出 {len(cs)} 块")
for i, c in enumerate(cs):
    print(f"   块{i}: len={len(c.raw_text)} 头={c.raw_text[:24]!r} 尾={c.raw_text[-24:]!r}")
# 若重叠生效，块 i 的尾部应出现在块 i+1 的头部

print("\n" + "=" * 70)
print("测试 3：PDF 真实标题层级（PyMuPDF 通道，取前 30 页）")
print("=" * 70)
try:
    import pymupdf4llm

    pdfs = sorted(s.paths.data_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError(f"{s.paths.data_dir} 下没有 PDF，跳过测试 3")
    print(f"  样本：{pdfs[0].name}")
    pages = pymupdf4llm.to_markdown(str(pdfs[0]), page_chunks=True, pages=list(range(0, 30)))
    import re
    heads = []
    for pg in pages:
        for line in (pg["text"] if isinstance(pg, dict) else str(pg)).split("\n"):
            m = re.match(r"^(#{1,6})\s+(.*)$", line.strip())
            if m:
                heads.append((len(m.group(1)), m.group(2).strip()))
    from collections import Counter
    print(f"  前 30 页检出标题 {len(heads)} 个，层级分布 {dict(Counter(l for l, _ in heads))}")
    for l, t in heads[:60]:
        print(f"    H{l}  {t[:70]}")
except Exception as e:
    import traceback
    traceback.print_exc()
