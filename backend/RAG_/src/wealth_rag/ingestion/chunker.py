"""结构感知切分器（核心模块）。

实现来源：源码级模仿 RAGFlow 并适配（详见 PLAN.md 4.2），六步策略：

① 结构切分   Markdown 标题树 → 「节」（对齐 rag_tools/flow/chunker/title_chunker）
② 节内切分   split-then-merge，贪心合并至 max_tokens（对齐 rag_tools/app/naive.py::naive_merge）
③ 超限兜底   单句超限 → 硬切分（对齐 TitleChunker hard split）
④ 表格处理   原子块；超限按行切分 + 表头重复 + 续表标记（对齐 rag_tools/app/table.py）
⑤ 父子块组装 mom（节全文）/ text（子块）两级结构（对齐 TokenChunker 的 mom/text）
⑥ 元数据组装 面包屑注入 + 页码溯源 + 金融实体保护

标题相关的全部判定逻辑集中在 ``heading_rules``，本模块只负责编排。
相对早期版本的关键修正（编号见 问题文档/问题与解决方案.md）：

- P0-1 页眉被当成标题  → 预扫描标题频次，识别后**整行丢弃**
- P0-2 目录页入库      → 目录标题进入跳过模式 + 目录表格识别
- P0-3 标题未进证据文本 → ``raw_text`` 前置叶子标题，``parent_text`` 前置完整面包屑
- P0-4 标题层级塌陷    → 以标题文本的语义层级为准，Markdown 的 ``#`` 数量仅兜底
- P0-5 面包屑歧义      → 文档标题压栈底 + 合订本文书边界重置层级栈
- P0-6 正文误判为标题  → 引入 ``not_title`` 负例规则
- P1-2 短块与噪声块    → 出口过滤噪声 + 同节内合并过短块
- P2-1 overlap 死配置  → 真正实现块间重叠
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field

from ..config import Settings
from ..models import Chunk
from .heading_rules import (
    count_headings,
    is_contents_title,
    is_document_start,
    is_noise_only,
    is_running_header,
    looks_like_toc_table,
    norm_title,
    not_title,
    semantic_level,
    sequence_number,
)
from .tokenizer import TokenCounter

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MARKER_RE = re.compile(r"^<!--\s*(?P<tag>PAGEBREAK|page:\s*(?P<num>\d+))\s*-->$", re.IGNORECASE)

#: 目录区最多吞掉的行数（防御失控；对齐 RAGFlow remove_contents_table 的 128 段上限）
_CONTENTS_MAX_LINES = 128

#: 层级栈中「文档标题」与「文书标题」的基准层级。
#: 取负值与 0，保证它们不会被其后的一级标题（章 = 1）弹栈弹掉。
_LEVEL_DOC_TITLE = -1
_LEVEL_DOCUMENT = 0

# 金融实体保护（⑥）：块尾「悬空实体」与下一段「实体延续」模式
_END_DANGLING = re.compile(r"(第\s*\d+|\d+(?:\.\d+)?)\s*$")
_NEXT_CONT = re.compile(r"^\s*(条|款|项|章|节|条第一款|％|%|年|月|日|元|万元|亿元|个基点|BP|bp)")

# 英文句子边界（③ 超限兜底用）：句末标点 + 空白 + 大写/数字起始。
# 要求句号后必须跟空白，因此「4.5%」「U.S. market」等不会被误切；中文句末标点不在此列，不受影响。
_SENT_BOUNDARY = re.compile(r'(?<=[.!?])\s+(?=[A-Z0-9"“(])')


@dataclass
class _Block:
    kind: str  # para | table
    content: str
    page: int | None


@dataclass
class _Section:
    heading_path: list[str]
    blocks: list[_Block] = field(default_factory=list)


@dataclass
class _Piece:
    """切分产物（面包屑在组装阶段统一注入）。"""

    embed_text: str
    raw_text: str
    chunk_type: str
    page: int | None


def _uuid_from(text: str) -> str:
    """md5 → 标准 UUID 字符串（Qdrant 点 ID 约束），同一输入稳定不变。"""
    h = hashlib.md5(text.encode("utf-8")).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _dedupe_adjacent(path: list[str]) -> list[str]:
    """去除面包屑中相邻的重复标题（文档标题在 Markdown 里再次出现时）。

    例：``[金融知识普及读本, 金融知识普及读本]`` → ``[金融知识普及读本]``
    """
    out: list[str] = []
    for t in path:
        if out and norm_title(t) == norm_title(out[-1]):
            continue
        out.append(t)
    return out


class MarkdownChunker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.cfg = settings.chunking
        self.counter = TokenCounter(settings.paths.cache_dir)
        self._delim_set = set(self.cfg.delimiters)
        # 每次 chunk() 调用时填充
        self._doc_title = ""
        self._heading_freq: Counter = Counter()

    # ==================================================================
    # 对外入口
    # ==================================================================
    def chunk(self, markdown: str, doc_id: str, source_path: str, doc_title: str) -> list[Chunk]:
        cfg = self.cfg
        self._doc_title = doc_title or ""

        # 【P0-1】预扫描标题频次：页眉的特征是「与文档标题相似且反复出现」
        self._heading_freq = (
            count_headings(markdown.split("\n"), _HEADING_RE) if cfg.title_rules else Counter()
        )

        sections = self._parse_sections(markdown)
        chunks: list[Chunk] = []

        for si, sec in enumerate(sections):
            pieces = self._section_pieces(sec)
            # 【P1-2】同一节内：过滤噪声块 + 合并过短块
            pieces = self._merge_short_pieces(pieces, cfg.min_chunk_chars)
            if not pieces:
                continue

            breadcrumb = " > ".join(sec.heading_path) if cfg.breadcrumb else ""
            prefix = f"{breadcrumb}\n" if breadcrumb else ""
            leaf = sec.heading_path[-1] if sec.heading_path else ""

            # ⑤ 父子块：本节产出多个子块时，节全文作为 mom 块存入每个子块元数据
            has_parent = len(pieces) > 1
            parent_id = _uuid_from(f"{doc_id}|parent|{si}") if has_parent else None
            parent_text = None
            if has_parent:
                parent_body = "\n\n".join(b.content for b in sec.blocks).strip()
                # 【P0-3】父块（节全文）必须自带面包屑，否则上下文扩展同样丢失标题
                parent_text = f"{prefix}{parent_body}" if prefix else parent_body

            for piece in pieces:
                body = piece.raw_text.strip()
                # text：用于嵌入 / 稀疏 / 重排（含完整面包屑）
                embed_text = (prefix + piece.embed_text).strip()
                if not embed_text:
                    continue
                # 【P0-3】raw_text：返回给 LLM 的证据，补回叶子标题。
                # 用叶子标题而非整条面包屑，避免与 parent_text 重复；
                # 完整层级仍保留在 heading_path 与 citation 中供溯源。
                raw_text = f"## {leaf}\n\n{body}" if leaf else body

                idx = len(chunks)
                # 点 ID 需为 UUID 格式（Qdrant 约束），由 md5 稳定派生
                chunk_id = _uuid_from(f"{doc_id}|{idx}")
                chunks.append(
                    Chunk(
                        id=chunk_id,
                        text=embed_text,
                        raw_text=raw_text,
                        doc_id=doc_id,
                        doc_title=doc_title,
                        source_path=source_path,
                        page=piece.page,
                        heading_path=list(sec.heading_path),
                        chunk_type=piece.chunk_type,
                        parent_id=parent_id,
                        parent_text=parent_text,
                    )
                )
        return chunks

    # ==================================================================
    # ① 结构切分：Markdown → 标题树 → 节
    # ==================================================================
    def _parse_sections(self, md: str) -> list[_Section]:
        cfg = self.cfg
        sections: list[_Section] = []

        # 【P0-5】把文档标题压入栈底，让每个块都带上文档上下文。
        # 层级取负值，保证不会被其后的一级标题（章 = 1）弹掉。
        base: list[tuple[int, str]] = []
        if cfg.include_doc_title and self._doc_title:
            base.append((_LEVEL_DOC_TITLE, self._doc_title))
        stack: list[tuple[int, str]] = list(base)

        def path_of(st: list[tuple[int, str]]) -> list[str]:
            return _dedupe_adjacent([t for _, t in st])

        current = _Section(heading_path=path_of(stack))
        sections.append(current)

        lines = md.split("\n")
        n = len(lines)
        in_code = False
        page: int | None = None
        breaks = 0

        in_contents = False
        contents_budget = 0

        buf: list[str] = []
        buf_page: int | None = None

        def flush_para() -> None:
            nonlocal buf, buf_page
            text = "\n".join(buf).strip()
            if text:
                current.blocks.append(_Block("para", text, buf_page))
            buf = []
            buf_page = None

        i = 0
        while i < n:
            line = lines[i]
            stripped = line.strip()

            # 页码标记（PDF 由 Docling 插入）
            if not in_code:
                m = _MARKER_RE.match(stripped)
                if m:
                    if m.group("num"):
                        page = int(m.group("num"))
                    else:
                        breaks += 1
                        page = breaks + 1
                    i += 1
                    continue

            # 代码块整体视为普通段落内容
            if stripped.startswith("```"):
                in_code = not in_code
                if not buf:
                    buf_page = page
                buf.append(line)
                i += 1
                continue

            if not in_code:
                # ---------- 【P0-2】目录区跳过模式 ----------
                if in_contents:
                    hm = _HEADING_RE.match(stripped)
                    cand = hm.group(2).strip() if hm else None
                    leave = bool(
                        cand
                        and not is_contents_title(cand)
                        and not not_title(cand)
                        and norm_title(cand) != norm_title(self._doc_title)
                    )
                    if not leave and contents_budget < _CONTENTS_MAX_LINES:
                        contents_budget += 1
                        i += 1
                        continue
                    in_contents = False
                    contents_budget = 0
                    # 落到下面按正常规则处理本行

                # ---------- 标题判定 ----------
                m = _HEADING_RE.match(stripped)
                if m:
                    title = m.group(2).strip()

                    # 【P0-2】目录标题 → 进入跳过模式，不建节
                    if cfg.drop_contents and is_contents_title(title):
                        flush_para()
                        in_contents = True
                        contents_budget = 0
                        i += 1
                        continue

                    # 【P0-1 / P1-2】页眉与纯噪声标题 → 整行丢弃。
                    # 不降级为正文：页眉降到正文会把书名插进段落中间；
                    # 噪声标题（如「续表」）若建节，会成为后续块的**错误父级**。
                    if cfg.title_rules and (
                        is_noise_only(title)
                        or is_running_header(
                            title,
                            self._doc_title,
                            self._heading_freq,
                            cfg.running_header_min_count,
                        )
                    ):
                        flush_para()
                        i += 1
                        continue

                    # 【P0-6】伪标题 → 降级为正文，并剥掉 # 标记避免噪声进索引
                    if cfg.title_rules and not_title(title):
                        if not buf:
                            buf_page = page
                        buf.append(title)
                        i += 1
                        continue

                    # 建节
                    flush_para()

                    # 【P0-5】合订本文书边界 → 重置层级栈并压入文书名
                    doc_name = is_document_start(title) if cfg.merge_documents else None
                    if doc_name:
                        stack = list(base) + [(_LEVEL_DOCUMENT, doc_name)]
                    else:
                        md_level = len(m.group(1))
                        sem = semantic_level(title) if cfg.title_rules else None
                        # 【P0-4】语义层级优先；Markdown 的 # 数量仅作兜底
                        level = sem if sem is not None else md_level
                        if level > cfg.heading_max_level:
                            # 超出允许层级 → 视为正文（默认 7，正常不会触发）
                            if not buf:
                                buf_page = page
                            buf.append(title)
                            i += 1
                            continue

                        # 【P0-4b】编号重启检测：同级编号回到 ≤ 上一次的编号，
                        # 说明进入了**新的上级作用域**。
                        # 典型场景：解析器丢失了「第二章」这类上级标题（本项目 PDF 实际
                        # 丢失了第 2–5 章），若不处理，后面所有「第X节」都会被错误地
                        # 挂到「第一章」下面（实测污染 38% 的块）。
                        # 处理方式：把上级条目一并弹出，**不臆造新的上级名**。
                        restart = False
                        if cfg.title_rules and level == 2:
                            for lv, txt in reversed(stack):
                                if lv == level:
                                    prev_num = sequence_number(txt)
                                    new_num = sequence_number(title)
                                    restart = (
                                        prev_num is not None
                                        and new_num is not None
                                        and new_num <= prev_num
                                    )
                                    break
                                if lv < level:
                                    break

                        while stack and stack[-1][0] >= level:
                            stack.pop()
                        if restart:
                            # 弹出所有 ≥1 级条目（保留文档标题 -1 与文书作用域 0）
                            while stack and stack[-1][0] >= 1:
                                stack.pop()
                        stack.append((level, title))

                    current = _Section(heading_path=path_of(stack))
                    sections.append(current)
                    i += 1
                    continue

                # ---------- 表格块 ----------
                if self._is_table_start(lines, i):
                    flush_para()
                    rows: list[str] = []
                    while i < n and lines[i].strip().startswith("|"):
                        rows.append(lines[i].strip())
                        i += 1
                    # 【P0-2】目录表格：多数行是「标题 …… 页码」形态 → 丢弃
                    if cfg.drop_contents and looks_like_toc_table(
                        [self._split_row(r) for r in rows]
                    ):
                        continue
                    current.blocks.append(_Block("table", "\n".join(rows), page))
                    continue

                # ---------- 空行 → 段落边界 ----------
                if not stripped:
                    flush_para()
                    i += 1
                    continue

            if not buf:
                buf_page = page
            buf.append(line)
            i += 1

        flush_para()
        return [s for s in sections if s.blocks]

    @staticmethod
    def _is_table_start(lines: list[str], i: int) -> bool:
        line = lines[i].strip()
        if not line.startswith("|") or i + 1 >= len(lines):
            return False
        nxt = lines[i + 1].strip()
        if not nxt.startswith("|"):
            return False
        return "-" in nxt and set(nxt) <= set("|-: ")

    # ==================================================================
    # ②③④ 节内处理
    # ==================================================================
    def _section_pieces(self, sec: _Section) -> list[_Piece]:
        """把节内块序列转为切分产物：连续段落合并处理，表格独立处理。"""
        pieces: list[_Piece] = []
        run: list[_Block] = []

        def flush_run() -> None:
            nonlocal run
            if run:
                text = "\n\n".join(b.content for b in run)
                pieces.extend(self._text_pieces(text, run[0].page))
                run = []

        for block in sec.blocks:
            if block.kind == "para":
                run.append(block)
            else:
                flush_run()
                pieces.extend(self._table_pieces(block))
        flush_run()
        return pieces

    # ---------------- 【P1-2】噪声过滤 + 短块合并 ----------------
    @staticmethod
    def _merge_short_pieces(pieces: list[_Piece], min_chars: int) -> list[_Piece]:
        """同一节内：纯噪声块丢弃，过短块并入前一个块。

        区分「无信息」与「信息少」——前者丢弃，后者**合并**（直接丢弃会丢内容）。
        只在本节内合并，跨节合并会破坏面包屑语义。
        """
        kept: list[_Piece] = []
        for p in pieces:
            if is_noise_only(p.raw_text) and is_noise_only(p.embed_text):
                continue
            if kept and len(p.raw_text.strip()) < max(1, min_chars):
                prev = kept[-1]
                kept[-1] = _Piece(
                    embed_text=f"{prev.embed_text}\n\n{p.embed_text}".strip(),
                    raw_text=f"{prev.raw_text}\n\n{p.raw_text}".strip(),
                    chunk_type=prev.chunk_type,
                    page=prev.page,
                )
            else:
                kept.append(p)
        return kept

    # ---------------- ② + ③ 文本：split-then-merge ----------------
    def _text_pieces(self, text: str, page: int | None) -> list[_Piece]:
        max_tokens = self.cfg.max_tokens
        if self.counter.count(text) <= max_tokens:
            return [_Piece(text, text, "text", page)]

        segments = self._split_segments(text)
        # ③ 超限兜底：单段仍超限 → 先按句子边界拆分（英文），单句仍超限再硬切分
        expanded: list[str] = []
        for seg in segments:
            if self.counter.count(seg) > max_tokens:
                expanded.extend(self._expand_oversized(seg, max_tokens))
            else:
                expanded.append(seg)

        groups = self._merge_segments(expanded)
        return [_Piece(g, g, "text", page) for g in groups]

    def _expand_oversized(self, seg: str, max_tokens: int) -> list[str]:
        """超限单元展开：优先按句子边界拆分（英文），单句仍超限时才硬切分。
        展开后的单元由 _merge_segments 按上限重新贪心合并，保证块边界落在句子上。"""
        sentences = self._split_sentences(seg)
        if len(sentences) <= 1:
            return self.counter.hard_split(seg, max_tokens)
        units: list[str] = []
        for sentence in sentences:
            if self.counter.count(sentence) > max_tokens:
                units.extend(self.counter.hard_split(sentence, max_tokens))
            else:
                units.append(sentence)
        return units

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        """按英文句子边界拆分，句间空白归属前句（与 _split_segments 同思路）。"""
        segments: list[str] = []
        last = 0
        for match in _SENT_BOUNDARY.finditer(text):
            segments.append(text[last : match.end()])
            last = match.end()
        if last < len(text):
            segments.append(text[last:])
        return [s for s in segments if s.strip()]

    def _split_segments(self, text: str) -> list[str]:
        """按分隔符切分，分隔符归属前一段（对齐 naive 的 delimiter 切分）。"""
        delim = "".join(re.escape(c) for c in sorted(self._delim_set))
        parts = re.split(f"([{delim}])", text)
        segments: list[str] = []
        buf = ""
        for part in parts:
            if not part:
                continue
            if len(part) == 1 and part in self._delim_set:
                buf += part
                segments.append(buf)
                buf = ""
            else:
                buf += part
        if buf.strip():
            segments.append(buf)
        return [s for s in segments if s.strip()]

    def _merge_segments(self, segments: list[str]) -> list[str]:
        """贪心合并至 max_tokens（对齐 naive_merge）；合并边界做金融实体保护。

        【P2-1】真正实现块间重叠：开新组时用上一组的尾部 token 作为前缀。
        重叠同时作用于 text 与 raw_text（两者同源），保持单一事实来源。
        """
        max_tokens = self.cfg.max_tokens
        overlap = max(0, int(self.cfg.overlap_tokens))
        pull_limit = int(max_tokens * 1.25)
        groups: list[str] = []
        cur = ""
        cur_tokens = 0
        for seg in segments:
            t = self.counter.count(seg)
            if cur and cur_tokens + t > max_tokens:
                # ⑥ 实体保护：不把「第 24 | 条」「4.5 | %」拆到两块
                if self._should_pull(cur, seg) and cur_tokens + t <= pull_limit:
                    cur += seg
                    cur_tokens += t
                    continue
                groups.append(cur)
                tail = self.counter.tail(cur, overlap) if overlap > 0 else ""
                cur = tail + seg
                cur_tokens = self.counter.count(cur)
            else:
                cur += seg
                cur_tokens += t
        if cur.strip():
            groups.append(cur)
        return [g.strip() for g in groups if g.strip()]

    @staticmethod
    def _should_pull(cur: str, nxt: str) -> bool:
        return bool(_END_DANGLING.search(cur.rstrip())) and bool(_NEXT_CONT.match(nxt))

    # ---------------- ④ 表格处理 ----------------
    def _table_pieces(self, block: _Block) -> list[_Piece]:
        max_tokens = self.cfg.max_tokens
        lines = block.content.split("\n")
        rows = [self._split_row(ln) for ln in lines]
        # 去掉 Markdown 对齐行（|---|---|）
        header = rows[0] if rows else []
        data = [r for idx, r in enumerate(rows) if idx > 0 and not self._is_separator(r)]
        if not header:
            return []

        if self.counter.count(block.content) <= max_tokens:
            return [_Piece(self._textify_table(header, data), block.content, "table", block.page)]

        # 行级切分 + 表头重复 + 续表标记
        header_md = self._row_to_md(header)
        sep_md = "|" + "|".join("---" for _ in header) + "|"
        base_tokens = self.counter.count(header_md) + self.counter.count(sep_md)

        pieces: list[_Piece] = []
        cur_rows: list[list[str]] = []
        cur_tokens = 0

        def flush(first: bool) -> None:
            nonlocal cur_rows, cur_tokens
            if not cur_rows:
                return
            raw = "\n".join([header_md, sep_md] + [self._row_to_md(r) for r in cur_rows])
            ctype = "table" if first else "table_continued"
            pieces.append(_Piece(self._textify_table(header, cur_rows), raw, ctype, block.page))
            cur_rows, cur_tokens = [], 0

        for row in data:
            t = self.counter.count(self._row_to_md(row))
            if cur_rows and base_tokens + cur_tokens + t > max_tokens:
                flush(first=len(pieces) == 0)
            # 单行本身超限（极端）：硬拆该行
            if t > max_tokens:
                flush(first=len(pieces) == 0)
                for piece_text in self.counter.hard_split(self._row_to_md(row), max_tokens):
                    ctype = "table" if not pieces else "table_continued"
                    pieces.append(_Piece(piece_text, piece_text, ctype, block.page))
                continue
            cur_rows.append(row)
            cur_tokens += t
        flush(first=len(pieces) == 0)

        if not pieces:  # 仅有表头
            return [_Piece(self._textify_table(header, []), block.content, "table", block.page)]
        return pieces

    @staticmethod
    def _split_row(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    @staticmethod
    def _is_separator(cells: list[str]) -> bool:
        joined = "".join(cells)
        return bool(joined) and set(joined) <= set("-: ") and "-" in joined

    @staticmethod
    def _row_to_md(cells: list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    @staticmethod
    def _textify_table(header: list[str], rows: list[list[str]]) -> str:
        """表格文本化（供嵌入与关键词匹配）：「表头: 值」逐行拼接。"""
        lines = []
        for row in rows:
            pairs = []
            for j, cell in enumerate(row):
                if not cell:
                    continue
                head = header[j] if j < len(header) and header[j] else f"列{j + 1}"
                pairs.append(f"{head}: {cell}")
            if pairs:
                lines.append("；".join(pairs))
        if not lines and header:
            lines.append("；".join(h for h in header if h))
        return "\n".join(lines)
