"""章级大纲重建：PDF 没有书签时的兜底策略（P0-8）。

**为什么需要它**

本项目 PDF（259 页）**没有任何书签**，RAGFlow `TitleChunker` 依赖的
``extract_pdf_outlines`` 在此完全失效。更糟的是：第 2–12 章的标题文字
**在文本层里根本不存在**——章首页是空白页（实测第 31 页无文本、无图片、
无绘制指令），正文直接从「第一节」开始。因此无论 Docling 还是 PyMuPDF，
都不可能凭空"识别"出这些章标题。

**用什么替代**

实测发现两条稳定可用的信号：

1. **目录页**（第 5–7 页）：列出全部 12 章及印刷页码，版式极规整——
   标题行 / 页码行 / 省略号行 各占一行；
2. **页眉**：``第X章`` 以独立成行的形式出现在 94 页上（**行内只有章号、没有章名**，
   这使它与真正的章标题可区分），前向填充后可归属 **250/259 页**，12 章齐全。

用「目录 → 章名」+「页眉 → 页归属」拼出 (物理页 → 章) 映射，再把合成标题
注入 Markdown 的页码标记之后，切分器即可据此恢复正确的章级层级。
这是 PDF outline 缺失时的通用工程做法。

**与 RAGFlow 的对应关系**

- RAGFlow 优先级：PDF outline → 正则族命中计数 → layout 兜底；
- 本模块把它扩成：PDF outline → **目录 + 页眉重建** → （交给 heading_rules）语义层级。
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DEFAULT_GARBLED_CHARS

_CN = r"零一二三四五六七八九十百0-9"

_CHAP_LINE = re.compile(rf"^第\s*([{_CN}]+)\s*章\s*(.*)$")
_SEC_LINE = re.compile(rf"^第\s*([{_CN}]+)\s*节\s*(.*)$")
#: 页眉形态：整行**只有**章号，没有章名
_CHAP_BARE = re.compile(rf"^第\s*[{_CN}]+\s*章\s*$")
_BARE_NUM = re.compile(r"^\d{1,4}$")

_GARBLED_RE = (
    re.compile(f"[{re.escape(DEFAULT_GARBLED_CHARS)}]") if DEFAULT_GARBLED_CHARS else None
)


def _clean(text: str) -> str:
    """剥离乱码字符并归一化空白（目录/页眉里的「摇」等）。"""
    t = text or ""
    if _GARBLED_RE is not None:
        t = _GARBLED_RE.sub(" ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


@dataclass
class OutlineEntry:
    level: int  # 1 = 章，2 = 节
    number: str  # 「第二章」
    title: str  # 「存贷款」
    printed_page: int | None  # 目录标注的印刷页码
    phys_page: int | None = None  # 物理页码（1 起）
    source: str = "toc"  # bookmark | toc | header

    @property
    def heading(self) -> str:
        return f"{self.number} {self.title}".strip()


@dataclass
class Outline:
    entries: list[OutlineEntry] = field(default_factory=list)
    #: 物理页码 → 该页所属的章条目
    page_chapter: dict[int, OutlineEntry] = field(default_factory=dict)
    source: str = "none"

    @property
    def ok(self) -> bool:
        return bool(self.page_chapter)


# ======================================================================
# 三路信号
# ======================================================================

def _page_lines(page) -> list[tuple[float, str]]:
    """(字号, 行文本)；字号取该行最大 span 字号。"""
    out: list[tuple[float, str]] = []
    for blk in page.get_text("dict").get("blocks", []):
        for ln in blk.get("lines", []):
            spans = ln.get("spans", [])
            if not spans:
                continue
            text = "".join(sp.get("text", "") for sp in spans).strip()
            if not text:
                continue
            out.append((round(max(sp.get("size", 0) for sp in spans), 1), text))
    return out


def extract_bookmarks(doc) -> list[OutlineEntry]:
    """PDF 书签 → 大纲。无书签返回空列表。"""
    try:
        toc = doc.get_toc(simple=True) or []
    except Exception:
        return []
    entries: list[OutlineEntry] = []
    for lvl, title, page in toc:
        t = _clean(title)
        if not t:
            continue
        m = _CHAP_LINE.match(t) or _SEC_LINE.match(t)
        entries.append(
            OutlineEntry(
                level=max(1, min(int(lvl), 2)),
                number=(m.group(1) and m.group(0).split(m.group(2))[0].strip()) if m else "",
                title=(m.group(2).strip() if m else t),
                printed_page=int(page),
                phys_page=int(page),
                source="bookmark",
            )
        )
    return entries


def extract_toc(doc, max_scan: int = 30, min_per_page: int = 3) -> list[OutlineEntry]:
    """目录页 → [(章号, 章名, 印刷页码)]。

    判据：一行是「第X章 <章名>」，且其**后两行内**出现一个纯数字（印刷页码）。

    目录通常跨多页（本项目是第 5–7 页），因此**不能首頁即停**。为避免把正文里的
    「第一节 xxx」误当目录，只接受**条目数 ≥ ``min_per_page``** 的页面——
    目录页每页都有十几条，正文页几乎不会有。
    """
    entries: list[OutlineEntry] = []
    seen: set[tuple[int, str, int | None]] = set()

    for pno in range(1, min(max_scan, doc.page_count) + 1):
        lines = [t for _, t in _page_lines(doc[pno - 1])]
        page_entries: list[OutlineEntry] = []
        for i, raw in enumerate(lines):
            t = _clean(raw)
            m = _CHAP_LINE.match(t)
            level = 1
            if not m or not m.group(2).strip():
                m = _SEC_LINE.match(t)
                level = 2
            if not m or not m.group(2).strip():
                continue
            # 后两行内找纯数字 → 印刷页码
            printed = None
            for j in (i + 1, i + 2):
                if j < len(lines) and _BARE_NUM.match(lines[j].strip()):
                    printed = int(lines[j].strip())
                    break
            if printed is None:
                continue
            num = t[: t.index("章") + 1] if level == 1 else t[: t.index("节") + 1]
            page_entries.append(
                OutlineEntry(
                    level=level,
                    number=num.replace(" ", ""),
                    title=m.group(2).strip(),
                    printed_page=printed,
                )
            )
        if len(page_entries) >= min_per_page:  # 只接受「像目录页」的页面
            for e in page_entries:
                key = (e.level, e.number, e.printed_page)
                if key in seen:
                    continue
                seen.add(key)
                entries.append(e)
    return entries


def extract_running_chapters(doc) -> dict[int, str]:
    """页眉信号 → {物理页: 「第X章」}。

    判据很干净：**整行只有章号、没有章名**。真正的章标题一定带章名，
    目录行也带章名，因此这一条不会误伤。
    """
    mapping: dict[int, str] = {}
    for pno, page in enumerate(doc, 1):
        for _, raw in _page_lines(page):
            t = _clean(raw)
            if _CHAP_BARE.match(t):
                mapping[pno] = t.replace(" ", "")
                break
    return mapping


# ======================================================================
# 组装
# ======================================================================

def build_outline(pdf_path: str | Path) -> Outline:
    """构建 (物理页 → 章) 大纲。优先书签，其次目录 + 页眉。"""
    try:
        import pymupdf
    except Exception:
        return Outline()

    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception:
        return Outline()

    try:
        # 1) 书签优先
        entries = extract_bookmarks(doc)
        if entries:
            page_map: dict[int, OutlineEntry] = {}
            chapters = [e for e in entries if e.level == 1]
            for idx, ch in enumerate(chapters):
                start = ch.phys_page or 1
                end = (chapters[idx + 1].phys_page or doc.page_count + 1) if idx + 1 < len(chapters) else doc.page_count + 1
                for p in range(start, min(end, doc.page_count + 1)):
                    page_map[p] = ch
            if page_map:
                return Outline(entries=entries, page_chapter=page_map, source="bookmark")

        # 2) 目录 + 页眉重建
        toc = extract_toc(doc)
        chapters = [e for e in toc if e.level == 1]
        if not chapters:
            return Outline()

        by_number = {c.number: c for c in chapters}
        headers = extract_running_chapters(doc)

        # 前向填充：页眉只出现在部分页上，其余页沿用最近一次出现的章
        page_map = {}
        cur: OutlineEntry | None = None
        for pno in range(1, doc.page_count + 1):
            num = headers.get(pno)
            if num and num in by_number:
                cur = by_number[num]
            if cur is not None:
                page_map[pno] = cur

        # 记录各章首现物理页，便于注入
        seen: set[str] = set()
        for pno in sorted(page_map):
            ch = page_map[pno]
            if ch.number not in seen:
                ch.phys_page = pno
                seen.add(ch.number)

        return Outline(entries=toc, page_chapter=page_map, source="toc+header")
    finally:
        doc.close()


# ======================================================================
# 注入 Markdown
# ======================================================================

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _norm_key(text: str) -> str:
    return re.sub(r"[\s\u3000]", "", text or "")


def _section_to_chapter(outline: Outline) -> dict[str, OutlineEntry]:
    """按目录顺序，把「第X节」归到它前面最近的「第X章」下。

    只保留**目录中唯一**的节标题——重名节（如多个章都有「第一节 概述」）无法
    可靠归属，宁可不注入也不要注错。
    """
    counts = collections.Counter(
        _norm_key(f"{e.number} {e.title}") for e in outline.entries if e.level == 2
    )
    mapping: dict[str, OutlineEntry] = {}
    cur: OutlineEntry | None = None
    for e in outline.entries:
        if e.level == 1:
            cur = e
        elif e.level == 2 and cur is not None:
            # 键必须与 Markdown 标题的形态一致（「第X节 + 节名」），否则查不到
            key = _norm_key(f"{e.number} {e.title}")
            if counts[key] == 1:
                mapping[key] = cur
    return mapping


def inject_chapter_headings(
    markdown: str,
    outline: Outline,
    page_marker: str,
    existing_headings: set[str] | None = None,
    max_inject: int = 64,
) -> tuple[str, int]:
    """把重建出的章标题插入 Markdown。**两路锚点互补**：

    1. **页码锚点**：章的首个物理页（依赖页码标记与阅读顺序一致）；
    2. **内容锚点**：该章第一个「节」标题所在处——Docling 的阅读顺序并不总是
       与页码一致（实测第十二章的 5 个节出现在页码锚点之前，导致 42 个块
       失去章级祖先），因此必须补这一路。

    已在 Markdown 中作为标题存在的章不重复注入。

    返回 ``(新 markdown, 注入条数)``。
    """
    if not outline.ok or not page_marker or page_marker not in markdown:
        return markdown, 0

    existing = {_norm_key(h) for h in (existing_headings or set())}

    # 页码锚点：章的首个物理页 → 需要注入的章
    inject_at: dict[int, str] = {}
    for pno in sorted(outline.page_chapter):
        ch = outline.page_chapter[pno]
        if ch.phys_page != pno or _norm_key(ch.heading) in existing:
            continue
        inject_at[pno] = _norm_key(ch.heading)

    chapter_by_key = {_norm_key(e.heading): e for e in outline.entries if e.level == 1}
    section_chapter = {
        k: v
        for k, v in _section_to_chapter(outline).items()
        if _norm_key(v.heading) not in existing
    }
    if not inject_at and not section_chapter:
        return markdown, 0

    out: list[str] = []
    page = 0
    injected = 0
    current: OutlineEntry | None = None

    def emit(ch: OutlineEntry) -> None:
        nonlocal injected, current
        if injected >= max_inject or ch is current:
            return
        out.append("")
        out.append(f"# {ch.heading}")
        out.append("")
        injected += 1
        current = ch

    for line in markdown.split("\n"):
        stripped = line.strip()

        if stripped == page_marker:
            out.append(line)
            page += 1
            key = inject_at.get(page)
            if key:
                ch = chapter_by_key.get(key)
                if ch is not None:
                    emit(ch)
            continue

        m = _MD_HEADING.match(stripped)
        if m:
            ch = section_chapter.get(_norm_key(m.group(2)))
            if ch is not None:
                emit(ch)

        out.append(line)

    return "\n".join(out), injected
