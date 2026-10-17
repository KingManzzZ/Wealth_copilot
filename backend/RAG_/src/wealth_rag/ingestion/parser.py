"""文档解析：统一产出「标准 Markdown + 结构元数据」。

- PDF / Word / PPT / Excel / HTML → Docling（MIT；统一多格式，保留标题层级与表格结构）
- Markdown / TXT → 直接读取

页码溯源：PDF 经 Docling 导出时插入 page_break_placeholder 标记，
由切分器按出现次序换算页码（1 起）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings

SUPPORTED_EXTS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".md",
    ".markdown",
    ".txt",
    ".html",
    ".htm",
}

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

#: 乱码串：同一汉字连续出现 ≥5 次（装饰边框/占位字形），仅匹配汉字，不触碰数字
_CJK_RUN_RE = re.compile(r"([\u4e00-\u9fff])\1{4,}")

#: 字体级过滤判定：某字体全书渲染字符数 ≥100、不同汉字数 ≤50，且占比 ≤2% → 视为装饰/乱码字体。
#: （乱码字体形状单一：E-BZ 渲染 23468 字却只有 23 种汉字；正常字体种类多，如正文 136258 字/1657 种。
#:  绝对上限 50 用于挡住正常字体被误判——正文占比也只有 1.2%，单看占比会误伤。）
_BAD_FONT_MIN_CHARS = 100
_BAD_FONT_MAX_DISTINCT = 50
_BAD_FONT_DISTINCT_RATIO = 0.02

#: 汉字清理判定：正常字体中的占比 ≤2%（即近乎只由乱码字体渲染）才视为乱码字并清理。
#: 该阈值保护「玉/译/页」等在正文中偶有合法出现的字，同时清理「摇/抒/郾/冶」等纯乱码字。
_GARBAGE_GOOD_RATIO = 0.02


def sanitize_markdown(markdown: str) -> str:
    """出口清洗：剔除「同一汉字连缀 ≥5」的乱码串（占位字形/装饰边框）。

    策略（保守）：
    - 整行纯乱码（去空白后仅由同一汉字重复组成）→ 整行删除；
    - 行内乱码串（同一汉字连续 ≥5）→ 仅删除该串，保留其余正文。
    只识别汉字，避免误伤数字/英文/正常标点；空白行与正文段落结构保持不变。
    """
    kept: list[str] = []
    for line in markdown.split("\n"):
        stripped = line.strip()
        if (
            len(stripped) >= 5
            and len(set(stripped)) == 1
            and "\u4e00" <= stripped[0] <= "\u9fff"
        ):
            continue  # 纯乱码行 → 整行丢弃
        kept.append(_CJK_RUN_RE.sub("", line))
    return "\n".join(kept)


def strip_chars(markdown: str, chars: set[str]) -> str:
    """把「仅出现在乱码字体中」的汉字整体替换为空格（数字/英文/标点不受影响）。

    连续多个乱码字符折叠为一个空格，兼顾「第五章摇反洗钱 → 第五章 反洗钱」的可读性。
    """
    if not chars:
        return markdown
    cls = "".join(re.escape(c) for c in sorted(chars))
    return re.sub(f"[{cls}]+", " ", markdown)


#: 行首因乱码替换产生的多余空格
_LEAD_SPACE_RE = re.compile(r"[ \t]{2,}")


def normalize_garbled(markdown: str, garbled: str) -> str:
    """【P1-1】把 PDF 乱码字符整体替换为空格，恢复词边界。

    与 ``sanitize_markdown``（处理「同一汉字连续 ≥5 次」的重复型噪声）不同，
    这里处理的是**替换型**噪声：CID→Unicode 映射表损坏后，某个字形被系统性地
    解码成同一个错误汉字，并插在词与词之间。

    - 对**分词**的影响：「第一章摇 总摇 摇 则」分词得到 ``总摇``，
      而用户查询「第一章 总则」得到 ``总则`` → BM25 必然失配。
    - 对**标题正则**的影响：``第一章摇 总则`` 不匹配 ``^第X章``。

    归一化后：「第一章 总 则」「1 办理程序」「银发 2015 74 号」。

    ⚠️ 字符表是语料相关的。若某字在该 PDF 中也是合法正文用字，会被误删；
    建议用 ``parser._garbage_chars`` 的字体统计思路确认后再固化字符表。
    """
    if not garbled:
        return markdown
    cls = "".join(re.escape(c) for c in sorted(set(garbled)))
    out = re.sub(f"[{cls}]+", " ", markdown)
    out = _LEAD_SPACE_RE.sub(" ", out)  # 折叠连续空格
    # 行首空格（乱码常在行首/行尾）与行尾空格一并去掉，避免污染标题判定
    return "\n".join(ln.strip() if ln.strip() else ln for ln in out.split("\n"))


@dataclass
class ParsedDoc:
    doc_id: str
    title: str
    markdown: str
    source_path: str  # 相对 data_dir 的可读相对路径（正斜杠）


def make_doc_id(rel_path: str) -> str:
    """文档稳定 ID：相对路径的哈希（同一文件重复摄入时保持不变）。"""
    norm = rel_path.replace("\\", "/").lower()
    return hashlib.md5(norm.encode("utf-8")).hexdigest()[:12]


class DocumentParser:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._converter = None  # Docling 按需加载（首次使用会下载版面分析模型）

    # ------------------------------------------------------------------
    def parse(self, path: Path, data_dir: Path) -> ParsedDoc:
        try:
            rel = path.resolve().relative_to(data_dir.resolve()).as_posix()
        except ValueError:
            rel = path.name
        doc_id = make_doc_id(rel)

        ext = path.suffix.lower()
        if ext in {".md", ".markdown", ".txt"}:
            markdown = path.read_text(encoding="utf-8", errors="ignore")
        else:
            if ext == ".pdf" and self.settings.parsing.pdf_engine in {"pymupdf", "auto"}:
                markdown = self._convert_pdf(path)
            else:
                markdown = self._convert_with_docling(path)
            markdown = sanitize_markdown(markdown)  # 出口清洗：剔除装饰/占位字形残留

        # 【P1-1】乱码字符归一化：必须放在所有解析分支**汇合之后**。
        # 早期版本把清洗只挂在 PyMuPDF 分支上，导致走 Docling 通道时完全裸奔——
        # 「第一章摇 总摇 摇 则」这样的文本会让分词、标题正则双双失配。
        markdown = normalize_garbled(markdown, self.settings.parsing.garbled_chars)

        # 【P0-8】章级大纲重建：PDF 没有书签、且章标题在文本层中缺失时，
        # 用「目录页 + 页眉」重建 (物理页 → 章) 映射，再把合成章标题注入 Markdown。
        # 放在乱码归一化之后——目录/页眉里的「摇」必须先被清掉才能匹配。
        if ext == ".pdf" and self.settings.parsing.rebuild_outline:
            markdown = self._rebuild_outline(path, markdown)

        title = self._extract_title(markdown) or path.stem
        return ParsedDoc(doc_id=doc_id, title=title, markdown=markdown, source_path=rel)

    # ------------------------------------------------------------------
    def _rebuild_outline(self, path: Path, markdown: str) -> str:
        """用 PDF 书签 / （目录 + 页眉）恢复章级标题并注入 Markdown。"""
        try:
            from .outline import build_outline, inject_chapter_headings
        except Exception:  # 依赖缺失时静默跳过，不影响主流程
            return markdown

        try:
            outline = build_outline(path)
        except Exception:
            return markdown
        if not outline.ok:
            return markdown

        # 收集 Markdown 中已存在的标题文本，避免为同一章重复注入
        existing = {
            m.group(2).strip()
            for m in (
                _HEADING_RE.match(ln.strip()) for ln in markdown.split("\n")
            )
            if m
        }
        merged, injected = inject_chapter_headings(
            markdown, outline, self.settings.parsing.page_break_placeholder, existing
        )
        if injected:
            import logging

            logging.getLogger(__name__).info(
                "章级大纲重建（%s）：注入 %d 个章标题", outline.source, injected
            )
        return merged

    # ------------------------------------------------------------------
    def _convert_with_docling(self, path: Path) -> str:
        if self._converter is None:
            from docling.document_converter import DocumentConverter

            self._converter = DocumentConverter()
        result = self._converter.convert(str(path))
        doc = result.document
        try:
            return doc.export_to_markdown(
                page_break_placeholder=self.settings.parsing.page_break_placeholder
            )
        except TypeError:  # 旧版 Docling 不支持占位符
            return doc.export_to_markdown()

    def _convert_pdf(self, path: Path) -> str:
        """PDF 解析：pymupdf 引擎直接走快速通道；auto 引擎先试 Docling、失败自动回退。"""
        if self.settings.parsing.pdf_engine == "pymupdf":
            return self._convert_with_pymupdf(path)
        try:
            return self._convert_with_docling(path)
        except Exception as exc:  # 模型不可用/网络异常 → 回退快速通道
            import logging

            logging.getLogger(__name__).warning(
                "Docling 解析 PDF 失败（%s），回退 PyMuPDF 快速通道: %s", type(exc).__name__, exc
            )
            return self._convert_with_pymupdf(path)

    def _convert_with_pymupdf(self, path: Path) -> str:
        """PyMuPDF 快速通道（备选）：逐页转 Markdown，插入页码标记。

        字体级过滤（β）：部分 PDF 使用 Identity-H 字体但缺失 CJK 的 CID→Unicode 映射，
        其 CJK 字形被解码成同一批错误汉字（空格占位「摇」、装饰边框「抒」、句点「郾」等），
        而 ASCII / 数字映射正常。这里按字体统计识别这类乱码字体，收集「只出现在乱码字体、
        正常字体中从不出现」的汉字，再从 Markdown 中整体替换为空格——从源头消除乱码，
        同时完整保留数字、英文、标点。
        """
        import pymupdf

        doc = pymupdf.open(str(path))
        try:
            bad_fonts = self._detect_bad_fonts(doc)
            garbage = self._garbage_chars(doc, bad_fonts)
        finally:
            doc.close()

        markdown = self._markdown_via_pymupdf4llm(path)
        return strip_chars(markdown, garbage)

    @staticmethod
    def _detect_bad_fonts(doc) -> set[str]:
        """识别装饰/乱码字体：渲染量 ≥100、不同汉字数 ≤50 且占比 ≤2%。

        这类字体缺失 CJK 映射，会把大量字形解码成同几个错误汉字（如 E-BZ 渲染 23468 字
        只有 23 种汉字），与正文正常字体（种类随量增长，如 136258 字/1657 种）显著区分。
        绝对上限 50 用于挡住正常字体被误判——正文占比也只有 1.2%，单看占比会误伤。
        """
        stats: dict[str, dict] = {}
        for page in doc:
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        st = stats.setdefault(span.get("font", ""), {"total": 0, "chars": set()})
                        text = span.get("text", "")
                        st["total"] += len(text)
                        st["chars"].update(ch for ch in text if "\u4e00" <= ch <= "\u9fff")
        bad: set[str] = set()
        for font, st in stats.items():
            total = st["total"]
            distinct = len(st["chars"])
            if (
                total >= _BAD_FONT_MIN_CHARS
                and distinct <= _BAD_FONT_MAX_DISTINCT
                and distinct <= total * _BAD_FONT_DISTINCT_RATIO
            ):
                bad.add(font)
        return bad

    @staticmethod
    def _garbage_chars(doc, bad_fonts: set[str]) -> set[str]:
        """乱码字体「主导」的汉字：出现次数主要落在乱码字体中的汉字。

        采用「近乎独占」判定：某汉字在正常字体中的占比 ≤2% 才清理。正文常见字几乎不会
        出现在乱码字体中，故不受影响；「冶」等只由乱码字体渲染（含未达阈值的零星小乱码
        字体）的字符会被清理，而「玉/译/页」等在正文偶有合法出现的字予以保留。
        """
        if not bad_fonts:
            return set()
        bad: dict[str, int] = {}
        good: dict[str, int] = {}
        for page in doc:
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        target = bad if span.get("font") in bad_fonts else good
                        for ch in span.get("text", ""):
                            if "\u4e00" <= ch <= "\u9fff":
                                target[ch] = target.get(ch, 0) + 1
        return {
            ch
            for ch, n in bad.items()
            if good.get(ch, 0) <= _GARBAGE_GOOD_RATIO * (n + good.get(ch, 0))
        }

    def _markdown_via_pymupdf4llm(self, path: str | Path) -> str:
        """pymupdf4llm 逐页转 Markdown，并在每页前插入页码占位符。"""
        import pymupdf4llm

        pages = pymupdf4llm.to_markdown(str(path), page_chunks=True)
        marker = self.settings.parsing.page_break_placeholder
        parts = []
        for page in pages:
            text = page["text"] if isinstance(page, dict) else str(page)
            parts.append(f"{marker}\n{text}")
        return "\n\n".join(parts)

    @staticmethod
    def _extract_title(markdown: str) -> str:
        for line in markdown.split("\n"):
            m = _HEADING_RE.match(line.strip())
            if m:
                return m.group(2).strip()
            if line.strip():
                break
        return ""


def discover_files(data_dir: Path) -> list[Path]:
    """递归发现支持的知识文档（按路径排序，保证摄入顺序稳定）。"""
    files = [
        p
        for p in data_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS and not p.name.startswith("~$")
    ]
    return sorted(files)