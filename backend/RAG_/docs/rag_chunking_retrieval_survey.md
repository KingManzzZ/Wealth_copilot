# RAG 文档切分与检索工程实践调研报告

> 调研范围：2024–2025 年 GitHub 主流 RAG 项目 / 官方文档 / 论文。
> 标注约定：**[核实]** = 我直接读取了一手源码或官方文档原文；**[推断]** = 我的工程判断，非原文结论；**[未核实]** = 我没能拿到一手材料。
> 直接相关背景：中文财富管理 RAG，Markdown 输入，自定义 chunker 存在"标题与正文被切开"问题，BM25（自实现）+ Qwen3-Embedding-0.6B + Qdrant + Qwen3-Reranker-0.6B。

---

## 0. 结论先行

**根因判断**：标题单独成 chunk、正文缺标题，通常来自三个具体实现选择：
1. 切分器 `strip_headers=True`（LangChain `MarkdownHeaderTextSplitter` 的**默认值**）——标题从正文剔除，只留在 metadata 里，而 metadata 默认**不参与 BM25 和 embedding**；
2. 标题行不满足 chunker 的最小长度阈值，被单独 flush 成一个 chunk；
3. breadcrumb 只写进 metadata，检索链路完全没读它。

**推荐架构**（本文 §7 给出伪代码）：**"标题层级路径注入正文 + 父（节）子（段）双层索引 + 标题独立文本字段参与 BM25 + RRF 融合"**。这个组合能以近乎零增量成本解决 90% 的标题-正文分离问题，且不需要 LLM 预处理。

---

## 1. 标题感知切分：各框架的实际行为（源码级）

### 1.1 LangChain

**`MarkdownHeaderTextSplitter`** — [源码 markdown.py](https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/text-splitters/langchain_text_splitters/markdown.py) **[核实]**

- 参数：`headers_to_split_on`、`return_each_line`、`strip_headers=True`（默认）、`custom_header_patterns`。
- 机制：维护 `header_stack`，遇新标题时按 `level` 弹栈（`header_stack[-1]["level"] >= current_header_level`）并同步清理 `initial_metadata`，因此每个 chunk 的 metadata 是**一路继承下来的扁平层级字典**（如 `{"Header 1": "第一章", "Header 2": "1.2 资产配置"}`）。
- **关键行为**：`strip_headers=True` 时 `current_content.append(stripped_line)` 不执行 → **标题文本不进正文**。这是"标题与正文分离"的头号嫌疑。
- `aggregate_lines_to_chunks` 有一个特殊合并分支：当上一块 metadata 层级更浅、且上一块末行以 `#` 开头、且 `strip_headers=False` 时，把当前行并入上一块并刷新 metadata——即**只有关掉 strip_headers 才有"标题+正文合并"的效果**。
- 注意它**不按 chunk_size 二次切分**：一个 `##` 下面 3000 字就是一整块。

**`MarkdownTextSplitter` / `RecursiveCharacterTextSplitter.from_language(MARKDOWN)`** — [character.py](https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/text-splitters/langchain_text_splitters/character.py) **[核实]**

- Markdown 分隔符表首位是 `"\n#{1,6} "`，`keep_separator=True`（默认）→ **标题行保留在 chunk 开头**。
- 但它是纯字符串递归，**不产生任何标题层级 metadata**，无法拼 breadcrumb。

### 1.2 LlamaIndex

**`MarkdownNodeParser`** — [源码 markdown.py](https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/node_parser/file/markdown.py) **[核实]**

- 关键代码：`current_section = "#" * header_level + f" {header_text}\n"` → **节点正文包含它自己的标题行**（这是与 LangChain 默认行为最重要的差异）。
- breadcrumb 写入 metadata：`node.metadata["header_path"] = separator + header_path + separator`，其中 `header_path = header_path_separator.join(h[1] for h in header_stack[:-1])`，**只含祖先标题、不含自身**，默认分隔符 `/`，形如 `/第一章/1.2 资产配置/`。空路径时为 `/`。
- `header_path_separator` 可配置（默认 `"/"`）。
- **致命短板**：完全不设 `chunk_size` 上限，超长小节会产生巨块；且 `header_path` **默认不进入 embedding 文本**（除非下游显式拼接）。**[推断]** 因此它解决了"标题-正文分离"，但没解决"块过大"。

**`SentenceWindowNodeParser`** — [源码 sentence_window.py](https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/node_parser/text/sentence_window.py) **[核实]**

- `window_size=3`（左右各 3 句），写入 `metadata["window"]`（拼接后的窗口文本）与 `metadata["original_text"]`（本句），并把这两个键加入 `excluded_embed_metadata_keys` 与 `excluded_llm_metadata_keys`。
- **检索侧**必须配合 `MetadataReplacementPostProcessor(target_metadata_key="window")` —— [源码](https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/postprocessor/metadata_replacement.py) **[核实]**：它把节点内容整体替换为 metadata 里的窗口文本。
- 语义：**用句子向量检索，返回句子窗口**。粒度比语义 chunk 更细，窗口大小直接控制上下文量。

### 1.3 RAGFlow —— 对"标题"投入最多的项目

RAGFlow 有两个独立的标题相关组件：

**(a) 流程式 `TitleChunker`** — [title_chunker/common.py](https://raw.githubusercontent.com/infiniflow/ragflow/74b44e1aa3ecd6687b3aa4ef731d0187720c3cb5/rag/flow/chunker/title_chunker/common.py) **[核实]**

参数：`levels`（正则族列表）、`hierarchy`（取第几级标题作为切分目标）、`include_heading_content`。
层级解析三条路径，按优先级：
1. **PDF outline**：`extract_pdf_outlines` 取 PDF 书签，要求 `len(outlines)/len(line_records) > 0.03`，用 `_outline_similarity > 0.8`（相邻字符 pair 的 Jaccard）匹配行；若占比不足 3% 直接放弃；
2. **正则族选择**：`select_level_group` 遍历候选正则族，**统计命中数最多的那一族**（避免混用不同族的模式导致层级语义混乱）；
3. **layout 兜底**：`re.search(r"(section|title|head)", layout, re.I)` 且 `not_title(...)` 判定为标题，否则 `BODY_LEVEL = sys.maxsize - 1`。
最终 `resolve_target_level(levels, hierarchy)` 从升序去重的标题层级里取第 `hierarchy` 个。

**(b) `rag/nlp/__init__.py` 的层级合并** — [源码](https://raw.githubusercontent.com/infiniflow/ragflow/163159b40d6f6cf32782e28bf1be7ff484d01ac6/rag/nlp/__init__.py) **[核实]**

- `BULLET_PATTERN` 是**四族中文/英文标题正则**，可直接复用：
  - 族 0：`第[零一二三四五六七八九十百0-9]+(分?编|部分|章|节|条)`、`[\(（][零一二三四五六七八九十百]+[\)）]`
  - 族 1：`第[0-9]+章`、`第[0-9]+节`、`[0-9]{,2}[\. 、]`、`[0-9]{,2}\.[0-9]{,2}[^a-zA-Z/%~-]`、三级/四级点号
  - 族 2：中文数字编号、`（一）（1）`
  - 族 3：`PART (ONE|TWO|...)`、`Chapter (I+V?|VI*|XI|IX|X)`、`Section N`、`Article N`
- `bullets_category(sections)` 用命中计数选出**该文档最匹配的一族**；`title_frequency()` 给每段打层级；`hierarchical_merge(bull, sections, depth)` 按层级把标题与其下属正文**合并进同一个 chunk**。
- 合并预算：`hierarchical_merge` 里单个纯标题块累计 token < 218 就继续并入后续内容，否则另起；`naive_merge(chunk_token_num=128, delimiter="\n。；！？", overlapped_percent)` 是兜底合并器，分隔符是**中文标点集**。
- 另有 `not_title()` / `not_bullet()` / `remove_contents_table()`（识别并删除"目录/目次/Table of Contents"区块）——**中文文档强烈建议照抄 `not_title` 的长度+标点启发式**。
- `title_frequency` 中 `levels[i] = bullets_size`（用 layout 判定的标题）与 `bullets_size + 1`（正文）分档，等价于"正则层级 + layout 层级"双通道。

### 1.4 Dify —— 有原生父子弹块 **[核实]**

[Dify 官方文档](https://docs.dify.ai/en/cloud/use-dify/knowledge/create-knowledge/chunking-and-cleaning-text)：

- **General 模式**：单一 delimiter + 最大字符长度 + chunk overlap；匹配到的 chunk 直接返回。
- **Parent-child 模式**：父块两种生成方式 —— `Paragraph`（同 delimiter + 最大长度切）或 `Full Doc`（整篇作为唯一父块，**只处理前 10,000 tokens，超出截断**，且创建后不可编辑）。子块用自己的 delimiter 和长度。命中子块 → 返回整块父块。仅 High Quality 索引支持。
- **重要陷阱**：文档明确说 *"Delimiters are removed during chunking"*，并举例 `A` 切 `CBACD` 得 `CB`、`CD`。若用 `\n## ` 之类含内容的字符作分隔符会发生信息丢失。

### 1.5 其它

- **Chonkie** — [Chunkers Overview](https://docs.chonkie.ai/oss/chunkers/overview.md) **[核实]**：`RecursiveChunker`、`SemanticChunker`、`LateChunker`、`NeuralChunker`（fine-tuned BERT 找语义转换点）、`SlumberChunker`（LLM agentic 分块）、`TokenChunker`、`SentenceChunker`、`TableChunker`（按行切 Markdown 表格并**保留表头**——Markdown 场景很实用）。统一接口 `chunk/chunk_batch/achunk`，全部 thread-safe。
- **Haystack / FastGPT / Quivr / Verbatim / txtai / RAGatouille / Kotaemon / LightRAG / GraphRAG / morphik**：**[未核实]** 本轮未拿到这些项目切分逻辑的一手源码，不做断言。RAGatouille 的价值主要在 ColBERT late-interaction 检索而非切分。

---

## 2. 父子分块 / small-to-big：机制与参数

| 方案 | 机制 | 关键参数（已核实） | 代价 |
|---|---|---|---|
| LangChain `ParentDocumentRetriever` | 父块存 docstore，子块入向量库并在 metadata 写 `id_key`（默认 `doc_id`）→ 父 id；检索命中子块后按 id 回查 docstore 返回父块 | `child_splitter`、`parent_splitter`、`docstore`、`vectorstore`、`id_key`、`child_metadata_fields`；官方示例 parent 2000 / child 400 | 需额外 docstore；子块 metadata 需裁剪 |
| LlamaIndex `HierarchicalNodeParser` | 递归用不同 size 切同一文档，显式挂 `NodeRelationship.PARENT/CHILD`，返回扁平节点列表 | `chunk_sizes=[2048, 512, 128]`（默认三级），`chunk_overlap=20` | 索引节点数约 1.2×；需配 AutoMerging |
| LlamaIndex `AutoMergingRetriever` | 检索后按父节点统计"命中子节点数/父节点总子数"，超阈值就把子节点替换为父节点；另有 `_fill_in_nodes` 用 `next_node/prev_node` 关系补全中间漏掉的节点 | `simple_ratio_thresh=0.5`（默认） | 多轮 while 合并，可能返回很大的块 |

来源：[ParentDocumentRetriever 源码](https://raw.githubusercontent.com/langchain-ai/langchain/master/libs/langchain/langchain_classic/retrievers/parent_document_retriever.py)、[hierarchical.py](https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/node_parser/relational/hierarchical.py)、[auto_merging_retriever.py](https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/retrievers/auto_merging_retriever.py) **[均核实]**

**与 SentenceWindow 的取舍** **[推断]**：
- SentenceWindow = "小块检索、定长窗口返回"，窗口大小固定，**不尊重文档结构**，标题层级会被窗口切碎；
- 父子（Hierarchical）= "小块检索、按文档结构返回"，回到父级就是完整一节，**天然带标题**。
- 对 Markdown 财富管理文档，**父子优于句子窗口**，因为章节边界就是语义边界；句子窗口适合对话记录、法律条文这类无明显标题的文本。
- `AutoMergingRetriever` 的 ratio 机制有一个隐患：父节点只有 2 个子节点时，命中 1 个 ratio=0.5 不触发（`>` 严格大于）；命中 2 个才合并。层级越浅越难触发。**[推断]**

---

## 3. Chunk 上下文增强

**Anthropic Contextual Retrieval** — [官方文章](https://www.anthropic.com/engineering/contextual-retrieval) **[核实]**：

- 机制：用 LLM 为每个 chunk 生成 50–100 token 的定位性上下文，**同时**前置到 embedding 输入和 BM25 索引文本（"Contextual Embeddings" + "Contextual BM25"）。
- 官方 prompt 原文（`<document>{{WHOLE_DOCUMENT}}</document>` + `<chunk>{{CHUNK_CONTENT}}</chunk>` + "give a short succinct context to situate this chunk within the overall document for the purposes of improving search retrieval"）。
- 效果数字：仅 contextual embeddings **失败率降 35%**（5.7%→3.7%）；加 contextual BM25 **降 49%**（→2.9%）；再加 rerank **降 67%**（→1.9%）。指标 = `1 - recall@20`。
- 成本：靠 prompt caching，**$1.02 / 百万文档 token**（按 800 token chunk、8k token 文档、100 token 上下文估算）。
- 官方明确说：**"给 chunk 加通用文档摘要"收益非常有限**，"summary-based indexing"评估下来性能也低——所以别做 chunk 摘要。
- 其它建议：chunk 大小/边界/重叠会影响效果；top-20 优于 top-10/top-5；rerank 输入 top-150 → 输出 top-20。

**breadcrumb 注入 vs LLM 上下文** **[推断]**：breadcrumb 是**确定性、零成本、零延迟**的上下文，覆盖"这段属于哪一章哪一节"；LLM 上下文额外覆盖"该 chunk 与全文其它部分的关系"（如跨节的指代消解："该公司"是谁）。**先上 breadcrumb，再用自建 eval 判断是否值得上 LLM 上下文**。

---

## 4. 混合检索中的字段加权

**BM25F** **[核实]**：Elasticsearch 的 `combined_fields` 查询官方文档明确说它 *"takes a principled approach to scoring based on the simple BM25F formula described in The Probabilistic Relevance Framework: BM25 and Beyond"*，并承认 *"makes some approximations and scores will not obey the BM25F model perfectly"*（[ES 文档](https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-combined-fields-query)）。要点：
- **term-centric**（operator / minimum_should_match 按 term 而非按 field 生效）：`+(combined("database", ["title","abstract"])) +(combined("systems", [...]))`；
- 字段 boost 用 `^`，**必须 ≥ 1.0**（可小数，如 `^1.5`），`title^2` 语义 = "title 里每个 term 在合成字段中出现两次"；
- 要求所有字段有**相同的 search analyzer**，且只支持 `text` 字段、只支持 BM25（不支持自定义/per-field similarity）；
- 对比：`multi_match` 的 `best_fields`/`most_fields` 是 **field-centric**，允许混合字段类型和不同 analyzer。

**Weaviate** **[核实]**：[Hybrid search 文档](https://docs.weaviate.io/weaviate/search/hybrid) 明说 *"combines the results of a vector search and a keyword (BM25F) search"*；[BM25 文档](https://docs.weaviate.io/weaviate/search/bm25) 明说 keyword search *"returns objects that have the highest BM25F scores"*，且支持 `Search operators`（`or`/`and`/`and_cross`，v1.31+）——`and` 要求所有 token 在**同一属性内**同时出现，这是属性级的作用域控制。

**Qdrant** **[核实]** — [Full-Text Search 文档](https://qdrant.tech/documentation/search/text-search/full-text-search/)：
- BM25 通过 **sparse vector + inference model** 实现：`"sparse_vectors": {"title-bm25": {"modifier": "idf"}}`，`"model": "qdrant/bm25"`；
- 可调参数：`k`（TF 饱和，默认 **1.2**）、`b`（长度归一，默认 **0.75**）、`avg_len`（默认 **256**）。**文档特别指出**：书名/标题通常远短于 256 词，应把 `avg_len` 设成实际字段平均长度（示例给 `avg_len: 5.0`）——这是**标题字段单独建稀疏索引时必须改的参数**；
- **原文关键句**：*"BM25F is the principled extension for multi-field text of varying length; Qdrant doesn't support it natively today, but the Multi-Representation Search tutorial shows the workaround: separate sparse vectors per field, fused via the Query API."*
- 这是本项目最直接的落地路径：**title 一个 sparse vector（小 `avg_len`）+ body 一个 sparse vector + dense 一个向量，三路 prefetch，Query API 融合**。
- 也支持 `SPLADE++`（`prithivida/splade_pp_en_v1`）与 `miniCOIL`；文档指出 SPLADE 的缺点：固定词表 → **无法匹配词表外的新词**（产品 ID、行业新术语），对财富管理领域的新产品/新指标名是真实风险。
- `modifier: "idf"` 的 IDF 统计默认在**被查询 shard 全量数据**上计算，多租户场景需参考 Per-Tenant IDF Statistics。IDF 修饰符细节见 [indexing 文档](https://qdrant.tech/documentation/manage-data/indexing/#idf-modifier)。

**RRF vs 加权融合** **[核实]**：
- Elasticsearch [RRF retriever](https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/rrf-retriever)：`rank_constant` **默认 60**（越大则低排名结果影响越大），`rank_window_size` 默认 10，每个子 retriever 可带 `weight`，最终 `rrf_score = Σ weight_i × rrf_score_i`。
- Qdrant [Hybrid Queries](https://qdrant.tech/documentation/search/hybrid-queries/)：提供 `rrf` 与 `dbsf` 两种融合。Qdrant 的公式为 `score(d) = Σ 1/(k + (r_d + 1)/w_r - 1)`，**`k` 默认 2**（注意与 ES 的 60 差异巨大，因为 Qdrant 用 0-based rank），`w_r` 默认 1；不显式设权时退化为 `Σ 1/(k + r_d)`。

**选择建议** **[推断]**：
- **RRF 适合"两路检索质量相近、分数量纲不可比"**的场景；它只看排名，天然免疫 BM25 分数与余弦相似度的量纲差异，也免疫某一路分数爆炸。基线建议 `k=60`（ES 惯例）。
- **加权（convex combination / alpha）适合"你能把两路分数归一化并愿意逐 query 调参"**的场景；Weaviate 的 `alpha` 就属此类。
- **关键判断：标题加权不应该交给 fusion 权重去调。** 正确的分层是——(1) 在 BM25 **内部**用字段/多稀疏向量给标题加权（这是 BM25F 的本职）；(2) 在融合层用 RRF 做 dense/sparse 的排序合并；(3) 用 reranker 做最终 top-K。把 title 权重混进 fusion alpha 会让调参维度互相污染。**[推断]**

---

## 5. 中文场景特殊考量

1. **Qdrant 原生 BM25 默认英文处理** **[核实]**：[文档](https://qdrant.tech/documentation/search/text-search/full-text-search/) 明确 *"BM25 defaults to English stemming and stopword removal"*，非英文必须配置 `language`；且对 CJK 这类不分词语言要用 `tokenizer: "multilingual"`（文档示例直接用了日文 `村上春樹`）。**中文必须**：`stemmer: {"type":"none"}` + `stopwords: {}` + `tokenizer: "multilingual"`（v1.19+ 的 language-neutral 组合）。**若沿用英文默认，中文检索会静默劣化。**
2. **jieba 分词** **[未核实]**：本轮未拿到 jieba + BM25 的权威工程文档。但有一个真实项目 issue 可作佐证：[datawhalechina/all-in-rag Issue #102「bm25检索器只会检索最后几个分块，需要添加分词处理中文」](https://github.com/datawhalechina/all-in-rag/issues/102) —— 标题本身说明**中文未分词的 BM25 检索器会出现召回异常**（我只读到标题与页面框架，未读到讨论正文，故仅作为现象佐证）。**[推断]** 自实现 BM25 时，中文字符级切分（unigram/bigram）是一个稳健的兜底方案，可避免 jieba 词典对财富管理专有名词的 OOV 问题。
3. **标题正则直接复用 RAGFlow 的 `BULLET_PATTERN`** **[核实]**（见 §1.3）：它已覆盖 `第X章/节/条`、`（一）`、`1.1.1`、`PART/Chapter/Section/Article` 四族，并且带 `bullets_category` 的"选命中最多的一族"策略——这正是中文标题形式多变的工程解法。
4. **中英混排** **[核实]**：RAGFlow 用 `is_english()`（正则 `[`a-zA-Z0-9\s.,':;/\"?<>!\(\)\-]` 全匹配占比 > 0.8）与 `is_chinese()`（`\u4e00-\u9fff` 占比 > 0.2）分别判定，并据此切换合并分隔符：中文 `"\n。；！？"`、英文 `". ; ! ?"`。财富管理文档（术语、产品名、指标代码）中英混排常见，**分隔符集应该取并集**。
5. **`cn2an` / `word2number` / `roman_numbers`** **[核实]**：RAGFlow 用它们把"第十五条"、"Part Three"、"IV"统一转成数字以做层级比较——中文标题层级判定建议照做。
6. **目录页必须删** **[核实]**：RAGFlow `remove_contents_table()` 用 `(contents|目录|目次|table of contents|致谢|acknowledge)$` 匹配并向后删除最长 128 段。财富管理白皮书的目录页会产生大量"标题堆叠"伪 chunk。

---

## 6. 其他值得借鉴的

**Chunk 大小与重叠的实证数据** —— [Chroma《Evaluating Chunking Strategies for Retrieval》(2024-07)](https://www.trychroma.com/research/evaluating-chunking) **[核实]**，token 级 IoU/Precision/Recall，n=5 检索，`text-embedding-3-large`：

| Chunking | Size | Overlap | Recall | Precision | Precision_Ω | IoU |
|---|---|---|---|---|---|---|
| Recursive | 800 (~661) | 400 | 85.4 | 1.5 | 6.7 | 1.5 |
| Recursive | 400 (~312) | 200 | 88.1 | 3.3 | 13.9 | 3.3 |
| Recursive | 400 (~276) | 0 | 89.5 | 3.6 | 17.7 | 3.6 |
| **Recursive** | **200 (~137)** | **0** | 88.1 | **7.0** | **29.9** | 6.9 |
| TokenText | 200 | 0 | 87.0 | 5.2 | 21.0 | 5.1 |
| Kamradt（语义，默认） | ~660 | 0 | 83.6 | 1.5 | 7.4 | 1.5 |
| ★KamradtModified | 300 (~397) | 0 | 87.1 | 2.1 | 10.5 | 2.1 |
| ★Cluster（语义） | 400 (~182) | 0 | 91.3 | 4.5 | 20.7 | 4.5 |
| ★Cluster（语义） | 200 (~103) | 0 | 87.3 | **8.0** | **34.0** | **8.0** |
| ★LLM(GPT-4o) | ~240 | 0 | **91.9** | 3.9 | 19.9 | 3.9 |

结论（原文）：默认 `RecursiveCharacterTextSplitter(200, overlap=0)` 表现稳健；**overlap 会拉低 IoU（重复 token 被惩罚）**；OpenAI Assistants 默认的 800/400 在 recall 之外各项**最差**；语义分块（Cluster）precision/IoU 最高但代价大。小 embedding 模型（all-MiniLM-L6-v2）下 overlap 对 recall 更重要（250/125 达 82.4，降到 overlap=0 掉到 77.1）——**这个结论对 0.6B 的小模型尤其相关**。

**语义分块的性价比反证** —— [《Is Semantic Chunking Worth the Computational Cost?》(arXiv 2410.13070)](https://arxiv.org/abs/2410.13070) **[核实]**：在 document retrieval、evidence retrieval、retrieval-based answer generation 三类任务上系统评测后，原文结论是 *"the computational costs associated with semantic chunking are not justified by consistent performance gains"*。→ **不建议**在本项目引入语义分块。

**Late Chunking** —— [arXiv 2409.04701](https://arxiv.org/abs/2409.04701)（Jina AI）**[核实]**：先用长上下文 embedding 模型编码整篇文档，**在 transformer 之后、mean pooling 之前**按 chunk 边界池化 token 向量，使每个 chunk 向量携带全文上下文，无需训练即可用于各种长上下文模型。Chonkie 已提供 `LateChunker` [核实]。**限制** **[推断]**：依赖 8k 上下文 embedding 模型（jina-v3），Qwen3-Embedding-0.6B 的上下文长度需自行确认；且它是**向量侧**方案，**对 BM25 完全无帮助**——所以不能替代 breadcrumb 注入。

---

## 7. 落地建议：breadcrumb 注入 + 父子分块（可直接抄）

### 7.1 核心设计

```
Markdown 文档
  └─ 解析为 (标题层级栈, 段落) 序列
       ├─ 父块 = 一个"节"（同级标题之间，含该标题及其全部下级内容）
       │    - 正文 = breadcrumb 前缀 + 本节标题 + 本节直挂正文 + 无子标题的表格/列表
       │    - 存 docstore，不建向量索引（或建一个粗粒度索引用于兜底）
       └─ 子块 = 父块内按中文标点递归切分（目标 ~150-250 token，overlap=0）
            - 正文 = breadcrumb 前缀 + 子块正文      ← 标题注入在这里
            - 元数据 = {doc_id, parent_id, header_path[], level, title}
            - 单独抽 title 文本字段用于 BM25
```

**三种标题注入方式的效果差异** **[推断]**：

| 方式 | 对 BM25 | 对稠密向量 | 成本 | 对"标题-正文分离"的解决程度 |
|---|---|---|---|---|
| A. breadcrumb 拼进 chunk 正文 | ✅ 直接命中标题词 | ✅ 语义偏置到该主题 | 零 | **≈95%** |
| B. 仅写 metadata | ❌ 默认不参与 | ❌ 默认不参与 | 零 | ~0%（除非框架显式支持字段检索） |
| C. 标题独立字段 + 字段加权 | ✅✅ 可控 boost | ❌ | 低 | ~85%（依赖 BM25F/多稀疏向量） |
| A + C 组合 | ✅✅ | ✅ | 低 | **≈99%** |

### 7.2 伪代码

```python
BULLET_PATTERNS = [                      # 摘自 RAGFlow rag_tools/nlp/__init__.py
    [r"第[零一二三四五六七八九十百0-9]+(分?编|部分)", r"第[零一二三四五六七八九十百0-9]+章",
     r"第[零一二三四五六七八九十百0-9]+节", r"第[零一二三四五六七八九十百0-9]+条",
     r"[\(（][零一二三四五六七八九十百]+[\)）]"],
    [r"第[0-9]+章", r"第[0-9]+节", r"[0-9]{,2}[\. 、]",
     r"[0-9]{,2}\.[0-9]{,2}[^a-zA-Z/%~-]", r"[0-9]{,2}\.[0-9]{,2}\.[0-9]{,2}"],
    [r"第[零一二三四五六七八九十百0-9]+章", r"第[零一二三四五六七八九十百0-9]+节",
     r"[零一二三四五六七八九十百]+[ 、]", r"[\(（][0-9]{,2}[\)）]"],
    [r"PART (ONE|TWO|...)", r"Chapter (I+V?|VI*|XI|IX|X)", r"Section [0-9]+", r"Article [0-9]+"],
]
BODY_LEVEL = 999
NOT_TITLE_PUNCT = re.compile(r"[,;，。；！!]")

def not_title(txt):                      # RAGFlow 启发式，中文友好
    if re.match(r"第[零一二三四五六七八九十百0-9]+条", txt): return False
    if len(txt.split()) > 12 or (txt.find(" ") < 0 and len(txt) >= 32): return True
    return bool(NOT_TITLE_PUNCT.search(txt))

def pick_pattern_family(lines):
    """选命中数最多的一族，避免混族导致层级错乱"""
    hits = [sum(1 for ln in lines if any(re.match(p, ln.strip()) for p in fam))
            for fam in BULLET_PATTERNS]
    best = max(range(len(hits)), key=lambda i: hits[i])
    return BULLET_PATTERNS[best] if hits[best] > 0 else None

def parse_blocks(md):
    """→ [(level, title_or_None, body_text)]，level 越小越顶层"""
    fam = pick_pattern_family(md.splitlines())
    out, stack, buf = [], [], []
    for line in md.splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        lvl, title = (len(m.group(1)), m.group(2).strip()) if m else (None, None)
        if lvl is None and fam:                                  # 中文编号标题
            for j, p in enumerate(fam):
                if re.match(p, line.strip()) and not not_title(line.strip()):
                    lvl, title = j + 1, line.strip(); break
        if lvl is not None:
            if buf: out.append((stack[-1] if stack else BODY_LEVEL, None, "\n".join(buf))); buf = []
            while stack and stack[-1][0] >= lvl: stack.pop()
            stack.append((lvl, title))
            out.append((lvl, title, ""))                         # 标题自身作为块，body 后面追加
        else:
            buf.append(line)
    if buf: out.append((stack[-1] if stack else BODY_LEVEL, None, "\n".join(buf)))
    return out

def build_parents(blocks):
    """每个标题开启一个父块，吸收所有更深层级的内容（= RAGFlow hierarchical_merge 的思路）"""
    parents, cur, crumbs = [], None, []
    for lvl, title, body in blocks:
        if title is not None:
            if cur: parents.append(cur)
            crumbs = [t for (l, t) in crumbs if l < lvl] + [(lvl, title)]
            cur = {"level": lvl, "breadcrumb": " > ".join(t for _, t in crumbs),
                   "title": title, "parts": [f"{'#'*lvl} {title}"]}
        elif cur is not None and body.strip():
            cur["parts"].append(body)
        # 游离在首个标题之前的正文：归入一个 level=0 的虚拟父块（按需）
    if cur: parents.append(cur)
    for p in parents:
        p["text"] = "\n\n".join(p["parts"])                      # 注意：父块含完整标题层级正文
    return parents

def split_children(parent, target=200, hard_max=400):
    """父块内按中文标点递归切；OVERLAP = 0（Chroma 数据支持）"""
    seps = ["\n\n", "\n", "。", "；", "！", "？", "，", ""]     # 中英混排：追加 [". ", "; ", "! ", "? "]
    return recursive_split(parent["text"], seps, target, hard_max, overlap=0)

def index(parents, doc_id):
    for p in parents:
        parent_id = f"{doc_id}#{p['breadcrumb']}"
        docstore[parent_id] = {"text": p["text"], "breadcrumb": p["breadcrumb"]}
        for ck in split_children(p):
            # ── 关键一步：breadcrumb 注入正文，而不是只写 metadata ──
            payload_text = f"[{p['breadcrumb']}]\n{ck}"
            upsert(
                id=sha1(parent_id + ck),
                dense=embed(payload_text),
                sparse_title=sparse(f"{p['breadcrumb']} {p['title']}"),   # 独立 title 稀疏向量
                sparse_body=sparse(payload_text),                          # 独立 body 稀疏向量
                payload={"parent_id": parent_id, "text": payload_text,
                         "title": p["title"], "header_path": p["breadcrumb"],
                         "level": p["level"], "doc_id": doc_id},
            )
```

### 7.3 Qdrant 侧参数建议 **[推断，除标注外]**

```python
sparse_vectors_config={
    "body_bm25":  models.SparseVectorParams(modifier=models.Modifier.IDF),
    "title_bm25": models.SparseVectorParams(modifier=models.Modifier.IDF),
}
# 标题字段长度远小于正文：必须给 title 单独设 avg_len
# title 侧 ingest/query 统一参数（Qdrant 文档强调 ingest 与 query 必须一致）：
TITLE_OPTS  = {"k": 1.2, "b": 0.75, "avg_len": 8.0,    # ← 按实际标题平均词数估
               "stemmer": {"type": "none"}, "stopwords": {}, "tokenizer": "multilingual"}
BODY_OPTS   = {"k": 1.2, "b": 0.75, "avg_len": 220.0}   # 中文无 stemming；avg_len 按实际统计
```
检索：三路 `prefetch`（`title_bm25` / `body_bm25` / `dense`，各取 50）→ `query={"rrf": {"k": 60}}` 或 `models.Rrf(rrf=models.Rrf(k=60))` → 取 top-30 → Qwen3-Reranker-0.6B → 取 top-5~8 → **按 `parent_id` 去重回 docstore 取父块文本**喂给 LLM。

### 7.4 参数与取舍速查 **[推断，除标注外]**

| 决策 | 建议 | 依据 |
|---|---|---|
| 子块目标大小 | 150–250 token，`overlap=0` | Chroma 表格中 200/0 的 Precision/IoU 最高 [核实] |
| 父块粒度 | 一个 Markdown 节（`##` 或中文"第X节"） | 结构性边界 = 语义边界 |
| 父块上限 | 1500–2000 token，超出再按 `###` 下钻一级 | LlamaIndex 默认 2048/512/128 [核实] |
| breadcrumb 注入位置 | **正文最前**，格式 `[第一章 > 1.2 资产配置]` | 对 BM25/向量同时生效 |
| 标题 boost | 靠独立稀疏向量字段，权重交给 RRF，**不要**塞进 fusion alpha | Qdrant 官方确认无原生 BM25F，多字段稀疏向量是官方 workaround [核实] |
| 是否上 LLM 上下文 | **先不上**，breadcrumb 覆盖后再看 eval | Anthropic 的 49% 收益中包含"跨节指代"成分 [核实] |
| 是否上语义分块 | **不上** | arXiv 2410.13070 结论为成本不划算 [核实] |
| 目录页 | 索引前删除 | RAGFlow `remove_contents_table` [核实] |

### 7.5 自建 eval 的消融顺序

对同一份标注集（query → 应命中的 chunk_id 集合），按顺序做消融，每步都记录 recall@20 / MRR / nDCG@10：**(0) 现状基线 → (1) 关掉标题剥离（标题留正文）→ (2) 加 breadcrumb 注入 → (3) 加父子（父块返回）→ (4) 加 title 独立稀疏向量 → (5) RRF `k` 扫描 {10, 60, 100} → (6) 上/不上 LLM 上下文**。这样可以精确定位每一步的边际收益，避免一次性改动无法归因。**[推断]**

---

## 8. 明确未核实 / 存疑项

1. **Haystack、FastGPT、Quivr、Verba、txtai、Kotaemon、LightRAG、GraphRAG、morphik** 的切分与标题处理实现——本轮**未获取一手源码**，报告中不做任何断言。
2. **jieba + BM25 的权威工程结论**——未找到一手材料。仅以 [all-in-rag Issue #102 标题](https://github.com/datawhalechina/all-in-rag/issues/102) 作为"中文未分词导致 BM25 召回异常"的现象佐证；**未读到该 issue 的讨论正文**。
3. **BM25F 原始论文**（Robertson & Zaragoza, *The Probabilistic Relevance Framework: BM25 and Beyond*）——我尝试直接抓取 PDF 被跨域重定向拦截，**未读到原文**。报告中 BM25F 的机制描述转引自 Elasticsearch `combined_fields` 官方文档对它的引用与实现说明 [核实]。
4. **RRF 原始论文**（Cormack et al., SIGIR 2009）——同因重定向未读到原文；`k=60` 的默认值转引自 Elasticsearch 官方 RRF retriever 文档 [核实]，Qdrant 用 `k=2` 是另一套约定 [核实]。
5. **LlamaIndex `MarkdownNodeParser` 的 `header_path` 是否会被某些 retriever 自动拼进 embedding**——我读了 parser 源码，确认它只写 metadata；**下游是否有配置能自动注入我未逐一核实**。报告中"默认不进入 embedding"的表述基于 `excluded_*` 机制与 `MarkdownNodeParser` 未把 path 拼进 `current_section` 这一源码事实。
6. **RAGFlow 检索侧的 keyword/vector 权重具体默认值**——我尝试抓取 `ragflow.io/docs` 对应页面返回 404，**未核实**。相关社区讨论（如 [Discussion #7895](https://github.com/orgs/infiniflow/discussions/7895)）存在但未读正文。
7. **Weaviate `alpha` 参数的具体语义与默认值**——[hybrid 文档](https://docs.weaviate.io/weaviate/search/hybrid) 确认可配置，但本轮抓取被截断，**具体默认值未核实**。

---

## 9. 一页速览：对你的项目先做这 4 件事

1. **立刻检查自定义 chunker 是否把标题行从正文剥离**（对应 LangChain `strip_headers=True` 这一默认陷阱）。若在剥离，改成"标题行保留在正文首行"。
2. **加 breadcrumb 注入**：把标题层级栈拼成 `第一章 > 1.2 资产配置 > 1.2.1 风险`，前置到每个 chunk 正文（不只是 metadata）。中文标题层级用 §7.2 的 `BULLET_PATTERNS` 四族 + `not_title` 启发式识别。
3. **改造成父子两层**：父 = 一个节（含完整标题路径正文），子 = 节内 150–250 token、overlap=0 的块；索引子块，命中后按 `parent_id` 回查父块文本送 LLM。
4. **给标题建独立 BM25 字段**：若继续用自实现 BM25，就做 BM25F 式字段化（title 词频独立统计 + title 自己的 `b`/长度归一）；若迁到 Qdrant sparse，就建 `title_bm25` + `body_bm25` 两个稀疏向量，各自设 `avg_len`，用 RRF 融合。**同时注意中文 tokenizer 与 stopword 配置**——沿用英文默认会静默劣化。
