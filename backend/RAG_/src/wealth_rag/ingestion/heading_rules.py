"""标题层级识别与噪声治理规则。

本模块集中承载「结构感知切分」中所有与标题有关的判定逻辑，供 chunker 调用。
解决的已定位问题（编号见 问题文档/问题与解决方案.md）：

- P0-1  PDF 页眉被当成标题，导致章节跨页断裂      → is_running_header
- P0-2  目录页整体入库并抢占首位                  → is_contents_title / looks_like_toc_table
- P0-4  标题层级塌陷（解析器把所有标题压成同一级）  → semantic_level
- P0-5  面包屑歧义（合订本中不同文书共享章节标题）  → is_document_start
- P0-6  正文句子被误判为标题                      → not_title
- P1-2  短块与噪声块入库                          → is_noise_only

设计原则：

1. **层级以标题文本自身的中文编号体系为准**，Markdown 的 ``#`` 数量只作兜底——
   不同解析引擎对 ``#`` 的用法差异极大（Docling 全给 H2，PyMuPDF 用 H5/H6）。
2. **标题判定同时具备正例与负例规则**：正例是编号体系（第X章/一、/1.1），
   负例是 ``not_title``。只有正例会让假阳性无法收敛。
3. **页眉/目录属于「导航元数据」而非内容**，识别后**直接丢弃**，
   不降级为正文——降级会把书名/页码插进段落中间污染正文。
"""

from __future__ import annotations

import re
from collections import Counter

from ..config import DEFAULT_GARBLED_CHARS

# ======================================================================
# 标题文本归一化
# ======================================================================

#: 归一化时剔除的字符：空白、括号、版次、各种标点与连接符
_NORM_DROP_RE = re.compile(r"[\s()（）\[\]【】{}·、,，.。:：;；\-—_/\\|~`'\"“”‘’]|第|版")

#: 中文数字 + 阿拉伯数字（标题编号用）
_CN_NUM = r"零一二三四五六七八九十百千0-9"

#: 乱码字符匹配（防御性清理用：正常链路里 parser.normalize_garbled 已处理）
_GARBLED_RE = (
    re.compile(f"[{re.escape(DEFAULT_GARBLED_CHARS)}]") if DEFAULT_GARBLED_CHARS else None
)


def norm_title(text: str) -> str:
    """标题归一化，仅用于频次统计与相似度比较，不用于展示。

    「金融知识普及读本 (第二版)」→「金融知识普及读本二」
    「第一章 人民币」          →「一章人民币」
    """
    return _NORM_DROP_RE.sub("", text or "")


def count_headings(lines: list[str], heading_re: re.Pattern) -> Counter:
    """预扫描：统计各标题文本出现次数（用于页眉识别）。

    ``heading_re`` 需含两个分组：``group(1)`` = ``#`` 串，``group(2)`` = 标题文本。
    """
    counter: Counter = Counter()
    for line in lines:
        m = heading_re.match(line.strip())
        if m:
            counter[norm_title(m.group(2))] += 1
    return counter


# ======================================================================
# P0-1 页眉识别
# ======================================================================

def is_running_header(
    title: str,
    doc_title: str,
    heading_freq: Counter,
    min_count: int = 3,
) -> bool:
    """页眉判定：与文档标题高度相似，且在文中反复出现。

    针对「金融知识普及读本 (第二版)」这类每页重复的书名/页眉——它被解析器
    识别为标题后会**重置标题栈**，把原本连续的一节切成若干段，只有第一段
    带着真标题，后续段落丢失章节上下文。
    """
    norm = norm_title(title)
    if not norm:
        return False
    doc = norm_title(doc_title)
    if not doc:
        return False
    similar = norm in doc or doc in norm
    return similar and heading_freq.get(norm, 0) >= min_count


# ======================================================================
# P0-2 目录识别
# ======================================================================

_CONTENTS_TITLE_RE = re.compile(
    r"^(目\s*录|目\s*次|目\s*錄|contents|table\s+of\s+contents)$", re.I
)

#: 目录条目特征：省略号引导符，或行尾页码
_TOC_ENTRY_RE = re.compile(r"…|\.{3,}|·{3,}|(?:^|\s)\d{1,4}\s*$")


def is_contents_title(title: str) -> bool:
    """判定「目录 / 目次 / Contents」标题。

    顺带剥离乱码字符：正常链路里 parser 已做归一化，但这里做防御，
    使「目摇 摇 录」这类未归一化的输入同样能识别。
    """
    t = title or ""
    if _GARBLED_RE is not None:
        t = _GARBLED_RE.sub("", t)
    compact = re.sub(r"[\s\u3000\u00a0]+", "", t)
    return bool(_CONTENTS_TITLE_RE.match(compact))


def looks_like_toc_table(rows: list[list[str]]) -> bool:
    """目录表格判定：多数行呈现「标题 …… 页码」形态。

    目录页字面包含全书章节标题，索引它等于人为制造一个
    「对任何章节查询都高分、但永远答不对」的干扰项。
    """
    if len(rows) < 3:
        return False
    hits = sum(1 for r in rows if _TOC_ENTRY_RE.search(" ".join(r)))
    return hits >= max(3, int(len(rows) * 0.6))


# ======================================================================
# P0-4 语义层级识别
# ======================================================================

#: (层级, 正则)。层级越小越顶层；按顺序匹配，首个命中即返回。
#: 注意顺序即优先级——更具体的模式必须排在更宽泛的模式之前
#: （例如 ``2.1`` 必须排在 ``1.`` 之前，否则会被误判为「第 2 项」）。
_LEVEL_RULES: list[tuple[int, re.Pattern]] = [
    # 篇 / 编 / 部分 / 卷 / 章
    (1, re.compile(rf"^第[{_CN_NUM}]+\s*(分?编|部分|篇|卷|章)")),
    # 节
    (2, re.compile(rf"^第[{_CN_NUM}]+\s*节")),
    # 一、 二、 三、
    (3, re.compile(r"^[一二三四五六七八九十]+\s*[、.．]")),
    # （一）（二）
    (4, re.compile(r"^[（(]\s*[一二三四五六七八九十]+\s*[)）]")),
    # 2.1 三级点号
    (4, re.compile(r"^[0-9]{1,2}\s*\.\s*[0-9]{1,2}\s*[^0-9.]")),
    # 2.1.1 四级点号
    (5, re.compile(r"^[0-9]{1,2}\s*\.\s*[0-9]{1,2}\s*\.\s*[0-9]{1,2}")),
    # 1. / 1、 / 1）
    (5, re.compile(r"^[0-9]{1,2}\s*[、.．]\s*\S")),
    # 乱码分隔符被归一化为空格后的「1 办理程序」
    (5, re.compile(r"^[0-9]{1,2}\s+\S")),
    # （1）（2）
    (6, re.compile(r"^[（(]\s*[0-9]{1,2}\s*[)）]")),
    # 第X条（条文视为叶子层）
    (7, re.compile(rf"^第[{_CN_NUM}]+\s*条")),
]


def semantic_level(title: str) -> int | None:
    """从标题文本推断层级；无法判断返回 ``None``。

    Docling 会把 PDF 的全部标题压成 H2，PyMuPDF 则输出 H5/H6——两条通道都拿不到
    正确层级。而**真正的层级信息始终存在于标题文本自身**（第一章 > 第一节 > 一、），
    本函数读的就是这个信号。
    """
    t = (title or "").strip()
    if not t:
        return None
    # 防御性清理：若上游未做乱码归一化（见 parser.normalize_garbled），
    # 「第一章摇 总则」这类文本会让编号正则失配
    if _GARBLED_RE is not None:
        t = _GARBLED_RE.sub(" ", t)
        t = re.sub(r"\s{2,}", " ", t).strip()
    for level, pattern in _LEVEL_RULES:
        if pattern.match(t):
            return level
    return None


# ======================================================================
# P0-4b 编号序号提取（用于检测「编号重启」）
# ======================================================================

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

#: 按顺序提取标题的「序号」：第一章→1、第一节→1、一、→1、（一）→1、2.1→2
_SEQ_PATTERNS = [
    re.compile(rf"^第\s*([{_CN_NUM}]+)\s*(?:分?编|部分|篇|卷|章|节|条)"),
    re.compile(r"^([一二三四五六七八九十]+)\s*[、.．]"),
    re.compile(r"^[（(]\s*([一二三四五六七八九十]+)\s*[)）]"),
    re.compile(r"^([0-9]{1,2})\s*(?:[、.．]|\s)"),
    re.compile(r"^[（(]\s*([0-9]{1,2})\s*[)）]"),
]


def _cn_to_int(s: str) -> int | None:
    """中文数字 → int（支持 一 ~ 九十九 及纯阿拉伯数字）。"""
    s = (s or "").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if "十" in s:
        head, _, tail = s.partition("十")
        tens = _CN_DIGITS.get(head, 1) if head else 1
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones
    total = 0
    for ch in s:
        d = _CN_DIGITS.get(ch)
        if d is None:
            return None
        total = total * 10 + d
    return total


def sequence_number(title: str) -> int | None:
    """提取标题的序号；无法提取返回 ``None``。

    用途：检测**编号重启**。当「第一节」在「第三节」之后再次出现时，说明已经
    进入了一个新的上级作用域——即使那个上级标题在解析过程中被丢掉了。
    """
    t = (title or "").strip()
    if not t:
        return None
    if _GARBLED_RE is not None:
        t = _GARBLED_RE.sub(" ", t)
        t = re.sub(r"\s{2,}", " ", t).strip()
    for pattern in _SEQ_PATTERNS:
        m = pattern.match(t)
        if m:
            n = _cn_to_int(m.group(1))
            if n is not None:
                return n
    return None


# ======================================================================
# P0-5 文书边界识别
# ======================================================================

#: 合订本中的独立文书名。刻意收窄：只认「中华人民共和国XX法/条例/办法/规定/通则」
#: 与「XX条例」，避免把「个人金融信息保护法律规定」这类普通小节标题误判为文书起始。
_DOCUMENT_TITLE_RE = re.compile(
    r"^(?:"
    rf"中华人民共和国[\u4e00-\u9fff]{{2,15}}(?:法|条例|办法|规定|通则)"
    rf"|[\u4e00-\u9fff]{{2,12}}条例"
    r")$"
)


def is_document_start(line: str) -> str | None:
    """若该行是一份独立文书的标题，返回文书名；否则返回 ``None``。

    法律汇编、年报合集、论文集这类**合订本**中，多份文书会共享同一套章节标题
    （实测：一个 ``第一章 总则`` 指向 6 部不同法律）。识别文书边界后重置层级栈，
    面包屑即可带上文书作用域。
    """
    t = (line or "").strip()
    if not t or len(t) > 40:
        return None
    return t if _DOCUMENT_TITLE_RE.match(t) else None


# ======================================================================
# P0-6 伪标题过滤（负例规则）
# ======================================================================

_SENT_PUNCT_RE = re.compile(r"[,;，。；！!：:]")
_ARTICLE_RE = re.compile(rf"^第[{_CN_NUM}]+\s*条")


def not_title(text: str) -> bool:
    """``True`` = 这不是标题，而是正文。

    解析器（尤其是基于版面分析/OCR 的）会用字体大小、加粗等**视觉特征猜测**标题，
    正文中的强调句、独立成行的短句很容易被误判——实测出现过 ``机制。``、
    ``防伪特征:``、``旅游购物报关单办理。`` 这类面包屑。
    """
    t = (text or "").strip()
    if not t:
        return True
    # 法条一律当标题
    if _ARTICLE_RE.match(t):
        return False
    # 强结构信号（命中编号体系）豁免标点检查，避免误伤「一、什么是资产配置?」这类标题
    if semantic_level(t) is not None:
        return False
    # 过长 → 多半是正文
    if len(t.split()) > 12:
        return True
    # 中文长句且不含空格 → 正文
    if " " not in t and len(t) >= 32:
        return True
    # 含句读标点 → 正文
    return bool(_SENT_PUNCT_RE.search(t))


# ======================================================================
# P1-2 噪声块判定
# ======================================================================

_NOISE_ONLY_RE = re.compile(
    r"^(?:<!--.*?-->|续表|formula-not-decoded|[\d\s.、,，;；:：\-—_]+)$",
    re.I,
)


def is_noise_only(text: str) -> bool:
    """整块只有噪声/占位符/页码时返回 ``True``。

    实测噪声块：``续表``(2 字)、``66``(目录页码)、``<!-- image -->``(14 字)。
    """
    t = (text or "").strip()
    return (not t) or bool(_NOISE_ONLY_RE.match(t))
