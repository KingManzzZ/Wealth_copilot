# 切分与检索策略审查报告

> 审查对象：`wealth-rag`（个人财富规划 RAG 检索模块）
> 审查方法：源码通读 + **已入库 722 个真实 chunk 的形态导出** + **真实解析复现**（708 块）+ **真实检索实测**（6 条查询 × top-8）+ GitHub 主流项目源码级对比
> 结论一句话：**"标题与内容被切开"不是错觉，而是四个可定位、可复现的具体缺陷叠加；其中页眉/目录污染是检索不准的最大单一原因。**

---

## 一、结论摘要

| 级别 | 问题 | 硬证据 | 影响 |
| --- | --- | --- | --- |
| **P0-1** | PDF 页眉被当成标题、目录页整体入库 | 目录页块在「第一章 人民币主要讲了什么」查询中 **排第 1（score 0.7994）**；「存款保险制度的限额是多少」top-8 里 **3 个是页眉块** | 召回名额被污染，且页眉切断章节 |
| **P0-2** | 标题只进 `text`，被从 `raw_text` / `parent_text` 剔除 | **720/722** 块的 `raw_text` 不含自身标题（含标题者仅 **1/722 = 0.14%**）；**335/335** 的 `parent_text` 不含标题；48/48 实测结果全部 `含标题? False` | 交给 LLM 的证据正文没有标题，字面意义上的「标题和正文切开」 |
| **P0-3** | 标题层级完全塌陷；纯容器标题被丢弃 | PDF 的 **561 个标题全部是 H2**（实测 `{2: 561}`），故 `heading_path` **708/708 深度恒为 1**；`第二节 人民币防伪与反假` 等纯容器标题 **0 块**（被 `chunker.py:200` 丢弃） | 面包屑无层级、章节容器标题消失，跨节消歧能力为零 |
| **P0-3b** | 面包屑在同一语料内高度歧义 | `第一章摇 总摇 摇 则` 一个 `heading_path` 被 **6 部不同法律**复用（页 206/212/224/230…）；`三、 结息和计息方式` 19 块、`(三) 金融机构应履行的义务` 15 块 | 相同面包屑指向完全不同的内容，仅凭面包屑无法区分 |
| **P0-4** | 正文句子被误判为标题 | 实测 `heading_path = '机制。'`、`'防伪特征:'`、`'旅游购物报关单办理。'` | 语义连贯的正文被错误切碎，并产生错误面包屑 |
| **P0-5** | 评估集是文档级，指标失效 | `evaluation.py:71-77` 只匹配 `doc_id`/`doc_title`/`source_path`；全库仅 6 篇文档 | `Recall@10=1.000` 无法反映 chunk 质量，**任何切分改动都无法验证** |
| P1-1 | 短块/噪声块入库 | 30 个块正文 <40 字符，含 `'续表'`(2字)、`'66'`(目录页码)、`'<!-- image -->'`(14字) | 无信息块占用索引与召回名额 |
| P1-2 | 同一标题重复占满 top-k | 「存款保险」查询 top-8 中 5 个仅来自 2 个标题；「黄金期货」中 2 个同标题 | 召回多样性差，挤掉别的章节 |
| P1-3 | `rerank_floor` 掩盖"无相关结果" | 「什么是再贴现」最高重排分仅 **0.0066**，系统按 `hybrid.py:74-79` 回退 RRF，仍返回 8 条 | 答案不存在时也强行返回，且丢弃了重排器的正确判断 |
| P2-1 | `overlap_tokens` 是**死配置** | `config.py:59`、`config.yaml:48` 声明，但 `chunker.py` 全文未引用；`TokenCounter.tail()` 无人调用 | README 声称的能力实际不存在 |
| P2-2 | `parent_text` 冗余存储 | 335 个块各自重复存一份节全文 | 存储与传输放大 |
| P2-3 | `heading_max_level=4` 与 PyMuPDF 链路冲突 | PyMuPDF 通道实测产出 **H5/H6** 标题（分布 `{1:1, 2:2, 3:1, 4:2, 5:3, 6:14}`），全部 >4 → 会被整层丢弃 | 换解析引擎即静默劣化 |

---

## 二、项目现状

### 2.1 架构（实际行为，非文档声称）

```
解析(Docling 优先/ PyMuPDF 兜底)
  → MarkdownChunker 六步切分
     ① 标题树 → 节        ② split-then-merge → 512 token
     ③ 超限兜底           ④ 表格行切 + 表头重复
     ⑤ mom/text 父子块    ⑥ 面包屑注入 + 页码 + 实体保护
  → Qwen3-Embedding(稠密) + jieba(稀疏，Qdrant IDF)
  → Qdrant 本地库（dense + sparse 双命名向量）
检索：dense top-30 + sparse top-30 → Qdrant 原生 RRF top-20
      → Qwen3-Reranker 精排 → 与归一化 RRF 按 0.6/0.4 融合 → top-8
```

技术选型合理，工程完成度高：设备自适应、Qdrant 本地模式、中文 2-gram 兜底、金融实体保护、乱码字体过滤都是用心之作。

### 2.2 实测数据（本次审查导出）

| 指标 | 实测值 |
| --- | --- |
| 总块数 | **722**（PDF 708 / 98.1%，样例 14） |
| 块类型 | text 674、table 29、table_continued 19 |
| `heading_path` 深度 | **深度1 = 710（98.3%）**、深度2 = 7、深度3 = 4、深度0 = 1 |
| `raw_text` 不含自身标题 | **720/722**（含标题者仅 **1/722 = 0.14%**） |
| `parent_text` 不含标题 | **335/335** |
| PDF 的 Markdown 标题数/层级 | **561 个，全部 H2** |
| 不同 `heading_path` 数 | 455（对应 721 个有标题的块） |
| 最高频 `heading_path` | `金融知识普及读本 (第二版)` **57 次**（= PDF 页眉） |
| 正文 <40 字符的块 | 30 |

---

## 三、根因分析：标题到底是怎么被切开的

### 根因 1（最严重）：页眉与目录被当成标题，导致章节在每一页断裂

**证据 A —— 页眉成为 57 个块的面包屑：**

```
57  金融知识普及读本 (第二版)
19  三、 结息和计息方式
15  (三) 金融机构应履行的义务
```

页眉是每页重复出现的书名。Docling 把它识别为 H2 标题，于是：

```
第 128 页 → # 金融知识普及读本 (第二版)      ← 页眉被当标题，栈被压平
           记录等。- 4郾 查询信用报告的记录:...  ← 块面包屑 = 书名（错误！）
```

这个块的正文属于「(一) 查询机关」一节，但它的 `heading_path` 是书名。

**证据 B —— 真实检索后果：**

「存款保险制度的限额是多少」top-8：

| 排名 | heading_path | 页码 |
| --- | --- | --- |
| 1 | (二) 实行限额偿付 | 47 |
| 2 | 一、 存款保险制度的基本概念 | 44 |
| **3** | **金融知识普及读本 (第二版)** | **241** |
| 4 | 存款保险条例 | 238 |
| 5 | 存款保险条例 | 238 |
| 6 | (一) 实行强制保险 | 46 |
| **7** | **金融知识普及读本 (第二版)** | **239** |
| **8** | **金融知识普及读本 (第二版)** | **239** |

**3/8 的召回名额给了页眉块**，且它们本质是被页眉切断的零散片段。

**证据 C —— 目录页排第 1：**

查询「第一章 人民币主要讲了什么」，top-1 是**目录页表格**：

```
[1] score=0.7994 rerank=0.6657 type=table page=4
    heading_path = 目摇 摇 录
    raw_text >>> | 第一章摇 | 人民币 ……  | 1 | | 第一节摇 | 人民币概述 …… | 3 | ...
```

重排分 0.6657 是全场最高——因为目录页确实字面包含「第一章 人民币」。这类块在**任何**涉及章节目录的查询中都会抢占首位。

> **这一条同时解释了用户说的"标题和内容切开"**：页眉把本应连续的一节切成若干段，只有第一段带着真标题，后续段落的标题被页眉顶替 → 标题与内容在物理上就分家了。

### 根因 2：标题只注入 `text`，被从证据文本中剔除

`chunker.py:80-98`：

```python
breadcrumb = " > ".join(sec.heading_path) if self.cfg.breadcrumb else ""
prefix = f"{breadcrumb}\n" if breadcrumb else ""
...
embed_text = (prefix + piece.embed_text).strip()   # 标题 → 只进嵌入文本
...
Chunk(
    text=embed_text,          # 含面包屑 ✓（用于嵌入/稀疏/重排）
    raw_text=piece.raw_text,  # 不含标题 ✗（用于返回证据）
    ...
    parent_text=parent_text,  # 不含标题 ✗（用于上下文扩展）
)
```

而 `chunker.py:84-86` 组装父块时，也**只拼 `b.content`，不含标题**：

```python
parent_text = "\n\n".join(b.content for b in sec.blocks).strip() if parent_id else None
```

**后果**：嵌入与 BM25 侧其实用到了标题（这点做得对），但**证据侧完全丢失**。`cli.py:120` 的展示、`README.md:143` 的 `hit.raw_text`、以及后续 LLM 编排拿到的都是无标题正文。

实测：**48/48** 条检索结果的 `raw_text 含标题? False`。

### 根因 3：标题层级塌陷 + 纯容器标题被丢弃

**(a) 层级塌陷。** PDF 的 561 个标题**全是 H2**（实测 `{2: 561}`）。`chunker.py:170-172` 的栈逻辑本身正确：

```python
while stack and stack[-1][0] >= level:
    stack.pop()
stack.append((level, title))
```

但 `level` 恒为 2，栈永远只存一个元素 → **`heading_path` 深度恒为 1（708/708）**。

对比正常 Markdown（`data/samples/01_资产配置基础.md`）能正确产出三层：

```
资产配置基础 > 二、战略与战术资产配置 > 2.2 再平衡策略
```

所以**问题出在解析层，不在切分层**——切分逻辑对层级 Markdown 是对的，但从 PDF 拿到的输入没有层级。

**(b) 纯容器标题被丢弃。** `chunker.py:200`：

```python
return [s for s in sections if s.blocks]
```

标题下若没有直接正文（下一个标题紧随其后），整个 section 被丢弃。实测该 PDF 的结果：

| `heading_path` | 块数 | 说明 |
| --- | --- | --- |
| `第二节摇 人民币防伪与反假` | **0** | 纯容器标题，**整个从索引中消失** |
| `第一节摇 人民币概述` | 1 | 仅 65 字符的引子：「人民币大家都十分熟悉…您是否能正确地识别真假人民币?」 |
| `第一章 人民币` | 1 | 内容却是第 14 页「第四套人民币面额/图案」的碎片 |

**后两行是更隐蔽的伤害：标题存在，但内容与标题错配。** 检索实测印证：查「第一章 人民币主要讲了什么」，返回的是**目录页（排第 1）**与另一章的「第三章摇 人民币」（页码 208），而唯一那个「第一章 人民币」块只有 153 字符且是别处内容，挤不进 top-8。

**(c) 面包屑在语料内高度歧义。** 这本书的附录收录了多部法律，它们共享同一套章节标题：

```
6  第一章摇 总摇 摇 则     ← 分别来自《反洗钱法》(p224)、《中国人民银行法》(p206)、
                            《商业银行法》(p212)、《征信业管理条例》(p230) …
19  三、 结息和计息方式
15  (三) 金融机构应履行的义务
```

**一个 `heading_path` 指向 6 部不同法律。** 在深度恒为 1、且不含书名/章名的情况下，面包屑不仅没有层级，还失去了区分能力。

### 根因 4：正文句子被误判为标题

实测出现的伪标题：

| `heading_path` | 性质 |
| --- | --- |
| `机制。` | 正文句子片段（page 197） |
| `防伪特征:` | 正文逗号前片段（page 22） |
| `旅游购物报关单办理。` | 正文句子 |
| `盂固定人像水印` | OCR 乱码 + 正文 |
| `2郾 办理程序` | 编号被当作顶级标题 |

这些伪标题既污染面包屑，又把连贯正文切断。`chunker.py` 目前**没有任何 `not_title` 级别的启发式校验**——只要 Markdown 里是 `#`，就无条件当标题。

### 根因 5（附带）：极短 stub 块

```
[4] heading_path = (二) 期货交易的特点
    raw_text >>> 期货交易的基本特征可以归纳为以下几个方面:     ← 仅 21 字
```

标题块只剩一句引言，真正的 1–6 条特征全在子标题块里。这正是「标题和内容切开」的另一种表现。

---

## 四、与 GitHub 优质项目的对比

完整调研见 [`docs/rag_chunking_retrieval_survey.md`](docs/rag_chunking_retrieval_survey.md)（源码级核实 + URL 溯源）。核心对照：

| 维度 | 本项目现状 | 业界做法 | 差距 |
| --- | --- | --- | --- |
| **标题是否进正文** | 进 `text`，**不进** `raw_text`/`parent_text` | LlamaIndex `MarkdownNodeParser`：`current_section = "#"*level + f" {header_text}\n"`（**正文含自身标题**）；LangChain `MarkdownHeaderTextSplitter` 的 `strip_headers=True`（默认剔除）**正是本项目踩的同一个坑** | 需补齐证据侧 |
| **标题层级识别** | 只认 Markdown `#` 数量 | RAGFlow 有专用 `TitleChunker`：PDF outline → **中文标题正则族命中计数择优** → layout 兜底；`rag/nlp/__init__.py` 的 `BULLET_PATTERN` 四族正则覆盖 `第X章/节/条`、`（一）`、`1.1.1`、`PART/Chapter` | 差距最大的一项 |
| **目录页处理** | 无 | RAGFlow `remove_contents_table()` 用 `(contents\|目录\|目次\|table of contents)$` 匹配并向后删除最多 128 段 | 直接照抄 |
| **伪造标题过滤** | 无 | RAGFlow `not_title()`：长度 >12 词、无空格且 ≥32 字、含 `,;，。；！` 判为正文 | 直接照抄 |
| **父子块** | `parent_text` 存每块 payload，不含标题 | LangChain `ParentDocumentRetriever`（父存 docstore、子存向量库、按 id 回查）；LlamaIndex `AutoMergingRetriever`（命中子节点比例 >0.5 合并为父） | 机制对，存储与内容需改 |
| **标题字段加权** | 标题混在 `text` 里统一算 BM25 | Qdrant 官方原文：*"Qdrant doesn't support it natively today, but the Multi-Representation Search tutorial shows the workaround: **separate sparse vectors per field**, fused via the Query API."* | 可加 `title_bm25` 独立稀疏向量 |
| **中文 BM25 配置** | 自实现 jieba + 2-gram（稳健） | Qdrant 原生 BM25 **默认 English stemming + stopwords**，CJK 需 `tokenizer: "multilingual"` | 本项目自实现，**规避了这个坑** |
| **融合** | Qdrant RRF（`k` 默认 **2**） | Elasticsearch RRF `rank_constant` 默认 **60**；两套约定不同 | 需确认 Qdrant 侧 `k` 取值 |
| **块大小** | 512 token 上限 | Chroma 2024-07 实测：`Recursive(200, overlap=0)` 的 Precision/IoU 最高；OpenAI 默认 (800,400) 各项最差 | 建议增加小块实验 |
| **重叠** | `overlap_tokens: 0`（**且未实现**） | Chroma 结论：overlap 拉低 IoU；但**小模型下 overlap 对 recall 更重要**（0.6B 属小模型） | 值得实验 |
| **语义分块** | 未启用 | arXiv 2410.13070：*"computational costs ... are not justified by consistent performance gains"* | **维持不启用，正确决策** |
| **LLM 上下文增强** | 未启用 | Anthropic Contextual Retrieval：仅 contextual embeddings 失败率降 35%，$1.02/百万 token；且官方明确"**给 chunk 加通用文档摘要收益非常有限**" | 先做确定性 breadcrumb，再评估 |

**结论**：本项目的切分骨架（结构切分 + split-then-merge + 父子块 + 面包屑注入）方向与一线项目一致，**原创性不差**。差距集中在两处工程细节：**(1) 解析层的标题识别与噪声治理**；**(2) 标题在证据侧的留存**。

---

## 五、改进清单

### P0-1 治理页眉 / 目录 / 伪标题（收益最大）

在 `chunker.py` 的标题判定处（`chunker.py:163-176`）加一道**标题准入 + 页眉抑制**闸门。新增 `ingestion/heading_rules.py`：

```python
# 1) 目录页检测：命中即进入"跳过模式"，直到出现真正的章节标题
_CONTENTS_RE = re.compile(r"^(目\s*录|目\s*次|contents|table of contents)$", re.I)

# 2) 伪标题启发式（照抄 RAGFlow not_title 思路，中文友好）
_TITLE_PUNCT_RE = re.compile(r"[,;，。；！!：:]")

def not_title(text: str) -> bool:
    """True = 这不是标题，是正文。"""
    if re.match(r"^第[零一二三四五六七八九十百0-9]+条", text):
        return False                      # 第X条 一律当标题
    if len(text.split()) > 12:
        return True
    if " " not in text and len(text) >= 32:
        return True
    return bool(_TITLE_PUNCT_RE.search(text))
```

**页眉抑制**（针对 57 次重复的书名）：

```python
# 在 MarkdownChunker.__init__ 或首个 chunk() 调用时统计
# 规则：标题归一化后出现次数 ≥ N，且与 doc_title 高度相似 → 判为页眉，不建节
def _is_running_header(self, title: str, doc_title: str, seen: Counter) -> bool:
    norm = re.sub(r"[\s()（）第版]", "", title)
    dt = re.sub(r"[\s()（）第版]", "", doc_title)
    if norm and (norm in dt or dt in norm) and seen[title] >= 3:
        return True
    return False
```

页眉不建节时，应**把该行丢弃**（而不是当正文），从而让上下文跨页延续。

**目录页**：检测到后跳过，直到下一个通过校验的标题。

预期收益：消除 57 个错误面包屑块 + 目录污染，直接修掉「存款保险」3/8 名额浪费和「第一章 人民币」top-1 失败。

### P0-2 标题回归证据文本

改 `chunker.py:80-98`：

```python
prefix = f"{breadcrumb}\n" if breadcrumb else ""
...
# text：用于嵌入 / 稀疏 / 重排（保持现状）
embed_text = (prefix + piece.embed_text).strip()
# raw_text：用于返回给 LLM 的证据，补回标题（Markdown 语义）
raw_text = f"{'#' * 2} {sec.heading_path[-1]}\n\n{piece.raw_text.strip()}" if sec.heading_path else piece.raw_text.strip()
# parent_text：父块（节全文）含自身标题
parent_text = (
    f"{prefix}{'\n\n'.join(b.content for b in sec.blocks).strip()}" if parent_id else None
)
```

> 建议 `raw_text` 用**叶子标题**而非整条面包屑，避免重复；完整层级留在 `heading_path` 与 `citation` 供溯源。

### P0-3 重建标题层级（不依赖 Markdown `#` 数量）

核心思路：**从标题文本自身解析层级**，Markdown 层级只作参考。这是 RAGFlow `BULLET_PATTERN` 的做法。

```python
# ingestion/heading_rules.py
_LEVEL_RULES = [
    (1, re.compile(r"^第[零一二三四五六七八九十百0-9]+(分?编|部分|篇|章)\b")),
    (2, re.compile(r"^第[零一二三四五六七八九十百0-9]+节\b")),
    (3, re.compile(r"^[一二三四五六七八九十]+[、.．]")),
    (4, re.compile(r"^[（(][一二三四五六七八九十]+[)）]")),
    (5, re.compile(r"^[0-9]{1,2}[、.．][^0-9]")),
    (6, re.compile(r"^[（(][0-9]{1,2}[)）]")),
    (7, re.compile(r"^第[零一二三四五六七八九十百0-9]+条")),   # 条文视为叶子层
]

def semantic_level(title: str) -> int | None:
    for lvl, pat in _LEVEL_RULES:
        if pat.match(title.strip()):
            return lvl
    return None
```

在 `chunker.py:166-174` 改为：

```python
md_level = len(m.group(1))
sem_level = semantic_level(title)
# 语义层级优先（Docling 把所有标题压成 H2 时仍能还原层次）
level = sem_level if sem_level is not None else max(md_level, 1)
```

同时把 `heading_max_level` 的语义改为**"语义层级上限"**，并默认放宽（例如 7），避免 PyMuPDF 的 H5/H6 被丢弃。**或者更稳的做法**：不再用 `#` 数量过滤标题，改为 `not_title()` 过滤。

**额外：给面包屑作用域加上文书边界。** 针对 P0-3b（一个 `第一章 总则` 被 6 部法律复用），两条措施：

1. **面包屑前缀加入文档标题**：`breadcrumb = f"{doc_title} > " + " > ".join(heading_path)`。
   ⚠️ 注意：这会给所有块加一个**常量前缀**，轻微压缩稠密向量的区分度。建议做成配置开关并**用评估集实测**（见 §六消融），不要凭直觉启用。
2. **检测合订本文书边界并重置层级栈**：当出现「第X章」且上一行是形如 `中华人民共和国[\u4e00-\u9fff]+法` / `[\u4e00-\u9fff]+条例` 的独立行时，视为新文书，`stack.clear()` 并把该法名压入栈底。这样 `第一章 总则` 会变成 `反洗钱法 > 第一章 总则`，歧义即消。

**替代方案（更彻底）**：把「文书名」提取为独立字段 `document_section`，与 `heading_path` 分开存储，检索时可作为过滤条件（Qdrant payload filter），比塞进面包屑更干净。

### P0-4 丢弃前先合并：短块与噪声块

在 `chunk()` 出口加一道**后处理**：

```python
# 1) 纯噪声块直接丢弃
_NOISE_RE = re.compile(r"^(<!--\s*image\s*-->|续表|<!--\s*formula-not-decoded\s*-->|[\d\s]+)$")
# 2) < MIN_CHARS(建议 30) 的块并入同节相邻块；同节只有一个块则与前后节合并
```

注意：**不要简单丢弃短块**（会丢内容），优先**并入同节相邻块**。当前 `_merge_segments` 已按 512 token 贪心合并，短块主要来自「标题下只有一句话」或「页眉切断」，治理根因 1 后大部分会自然消失。

### P1-2 同节去重 / MMR

在 `hybrid.py:63` 返回前加多样性约束：

```python
def _dedupe_by_parent(self, results, final_k):
    seen, out = set(), []
    for r in results:
        key = r.parent_id or " > ".join(r.heading_path)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) == final_k:
            break
    return out
```

预期：把「存款保险」那种 5/8 名额集中在 2 个标题的情况摊开。

### P1-3 用阈值表达"无结果"，而非静默回退

`hybrid.py:74-79` 的 `rerank_floor` 目前会把重排器的"全都无关"判断丢弃。「什么是再贴现」实测最高重排分 **0.0066**——重排器其实答对了（库里确实没有"再贴现"的定义），但系统仍返回 8 条。

建议：

```python
if max_rerank >= r.rerank_floor:
    ...  # 现有融合逻辑
elif max_rerank < r.no_answer_threshold:      # 新增，建议 0.05
    return []                                  # 或标记 low_confidence，交由上层决定
else:
    ...  # 保留 RRF 保底
```

### P2 清理

1. **实现或删除 `overlap_tokens`**：`TokenCounter.tail()` 已就绪，在 `_merge_segments` 里用上一组尾部做前缀即可；若不打算用，就从 `config.yaml` 与 README 中删除，避免虚假能力声明。
2. **`parent_text` 去冗余**：把父块单独存为一个 point（`chunk_type="parent"`，不参与向量检索）或独立集合，子块只存 `parent_id`。顺带可让父块也参与召回做兜底。
3. **修正 README 的"512-token 上限精确"**：`_merge_segments` 的实体保护允许 `pull_limit = 1.25 × max_tokens = 640`，实测量到 612 字符块。表述应改为"上限 512，实体保护可放宽至 640"。

---

## 六、评估体系改造（必须先做，否则无法验证任何改动）

**当时 `eval/queries.example.jsonl` 只有 3 条示例，且 `evaluation.py:71-77` 只做文档级匹配。** 全库 6 篇文档，`Recall@10` 必然接近 1.000 —— 这个指标**对切分质量完全不敏感**，README 中"评估 6 条查询 = 1.000"不具备参考意义。

> **后续状态（2026-10-15）**：此问题已按 P0-7 修复——`evaluation.py` 重写为 chunk 级，
> 并建立 70 条评估集 `eval/queries.jsonl`；示例文件 `queries.example.jsonl` 已删除。

**改造方案**：

```jsonc
// eval/queries.jsonl —— chunk 级标注
{"query": "人民币防伪特征有哪些",
 "gold_chunks": ["<chunk_id>", "..."],          // 可选：精确到块
 "gold_headings": ["二、 人民币防伪特征", "一、 什么是假币"],   // 推荐：标注应命中的标题路径
 "must_not": ["目摇 摇 录"]}                     // 负例：目录页不应出现
```

指标改为 **chunk 级 Recall@k / MRR / nDCG@k**，并新增两个切分专属指标：

| 指标 | 定义 | 为什么需要 |
| --- | --- | --- |
| **标题覆盖率** | `raw_text` 含自身标题的块占比 | 当前 **1/722 = 0.14%**，目标 >95% |
| **面包屑歧义度** | 单一 `heading_path` 覆盖的最大块数 / 总块数 | 当前 `第一章 总则` 单值覆盖 6 部法律，目标按文档限定作用域 |
| **面包屑深度分布** | `len(heading_path)` 直方图 | 当前 98% 深度 1，目标以 2–4 为主 |
| **页眉污染率** | `heading_path` 等于书名的块占比 | 当前 **57/722 = 7.9%**，目标 0 |

**消融顺序**（每步单独记录，便于归因）：

```
(0) 现状基线
(1) + 页眉/目录/伪标题治理        ← 预期收益最大
(2) + 标题回归 raw_text/parent_text
(3) + 语义层级重建
(4) + 短块合并
(5) + 同节去重
(6) + title 独立稀疏向量（Qdrant 多 sparse vector）
(7) + overlap 扫描 {0, 64, 128}
```

---

## 七、建议实施顺序

| 阶段 | 内容 | 预估工作量 | 预期收益 |
| --- | --- | --- | --- |
| **第 1 步** | 建 chunk 级评估集（50–100 条真实问题） | 0.5–1 天 | 让后续所有改动可度量 |
| **第 2 步** | P0-1 页眉/目录/伪标题治理 | 0.5 天 | **最大单项收益** |
| **第 3 步** | P0-2 标题回归证据文本 | 0.5 小时 | 直接解决"标题和内容切开" |
| **第 4 步** | P0-3 语义层级重建 | 0.5 天 | 面包屑恢复章→节→条 |
| **第 5 步** | P0-4 + P1-2 + P1-3 | 1 天 | 去噪、多样性、诚实拒答 |
| **第 6 步** | P2 清理 + 文档修正 | 0.5 天 | 消除虚假能力声明 |
| **第 7 步** | title 独立稀疏向量实验 | 1 天 | 锦上添花 |

**不建议现在做的事**（有明确反证）：
- ❌ **语义分块**：arXiv 2410.13070 结论为成本不被性能提升证明；
- ❌ **LLM chunk 摘要/上下文**：Anthropic 官方明确"通用文档摘要收益非常有限"，且 $1.02/百万 token；
- ❌ **换更大模型**：当前问题在切分层，换模型解决不了标题缺失。

---

## 附录 A：证据复现方法

本次审查的所有数据均可复现。诊断脚本已归档到 [`docs/audit/`](docs/audit/)（见其 [README](docs/audit/README.md)），**均为只读**，未改动原库：

```powershell
# ⚠️ 路径已更新：模块现位于 backend/RAG_（原为 E:\Python_Projects\RAG_，见问题文档 P2-9）
cd E:\Python_Projects\Agent_study\backend\RAG_
$env:PYTHONIOENCODING = "utf-8"

python docs\audit\dump_chunks.py            # 导出 722 块形态（标题覆盖率、短块、页眉污染）
python docs\audit\diagnose_breadcrumb.py    # 面包屑深度分布与高频标题
python docs\audit\search_probe.py           # 6 条查询真实检索实测
python docs\audit\test_chunker_rules.py     # 切分规则单测 + PDF 标题层级
```

> 审查期间因环境限制，曾把 `db/qdrant` 复制到 `_review/` 以取得写权限；该副本已删除，脚本现在直接读取 `./config.yaml` 指向的库。

## 附录 B：关键代码位置索引

| 位置 | 作用 | 关联问题 |
| --- | --- | --- |
| `src/wealth_rag/ingestion/chunker.py:73-109` | `chunk()` 入口、面包屑注入 | P0-2 |
| `src/wealth_rag/ingestion/chunker.py:163-176` | 标题判定与层级栈 | P0-1 / P0-3 / P0-4 |
| `src/wealth_rag/ingestion/chunker.py:200` | 丢弃无正文的节 | P0-3（章标题消失） |
| `src/wealth_rag/ingestion/chunker.py:84-86` | 父块组装 | P0-2 / P2-2 |
| `src/wealth_rag/ingestion/chunker.py:299-321` | `_merge_segments` 合并与实体保护 | P2-1 / P2-3 |
| `src/wealth_rag/retrieval/hybrid.py:56-63` | 重排与融合入口 | P1-3 |
| `src/wealth_rag/retrieval/hybrid.py:65-79` | `_fuse()` 与 `rerank_floor` | P1-3 |
| `src/wealth_rag/evaluation.py:71-77` | 文档级相关性判定 | P0-5 |
| `src/wealth_rag/config.py:59` | `overlap_tokens` 声明 | P2-1 |
| `config.yaml:44-49` | 切分配置 | P0-3 / P2-1 |
