# RAG 检索评估前沿实践调研报告

**检索日期**：2026-10-15 ｜ **对象**：中文金融 RAG 检索模块（6 文档 / 664 子块 / 109 父块 / heading_path / 稠密+稀疏+RRF+Rerank / low_confidence）

**标注约定**：**[核实]** = 已读到一手资料原文；**[推断]** = 我的判断或数学推导，无一手引用支撑。

---

## 0. 三条最重要的判断

1. **你不需要池化标注。** 池化（pooling）是 TREC 那种 1.89M 文档语料的产物——NIST 只对"某参赛系统 top-k 返回过的文档"做判断 [核实 IR Book](https://nlp.stanford.edu/IR-book/html/htmledition/standard-test-collections-1.html)。664 个子块小到可以**穷举判断**，从而在根上消除"只标注了系统已召回的文档"这一偏差（Cranfield 即穷举判断的经典案例：1398 摘要 × 225 query 全量判断 [核实，同上]）。
2. **你的 3 条评估集不是"太小"，是"测错了对象"。** 6 篇文档下的文档级命中恒为 1.0 是必然的。要测的是块级排序 + 父块充分性 + 弃答判定，这是三套不同粒度的判断。
3. **若 rerank 深度 W = 报告窗口 k，则 rerank 的贡献在数学上恒等于 0**（见 §2.2）。你现在很可能正处在这个情形。

---

## 1. 评估集构建

### 1.1 池化与 pool bias
TREC 式池化直接产生 pool bias：召回池外文档的系统无法被正确打分，补救工具是 **bpref**（Buckley & Voorhees 2004）与 **inferred AP / infAP**（Yilmaz & Aslam 2006，[NIST PDF](https://trec.nist.gov/data/terabyte/06/inferredAP.pdf)），以及 Zobel/Moffat 等《Bias and the limits of pooling for large collections》（[Springer](https://link.springer.com/article/10.1007/s10791-007-9032-x) / [ACM](https://dl.acm.org/doi/10.1007/s10791-007-9032-x)）。**上述三篇正文我均未能读取（PDF/付费墙），其具体结论未能核实**，仅核实到题录。

**对你的含义**：评估集的候选块集合**必须独立于当前检索链路生成**（如 LLM 直接对全库 664 块判相关性）。否则一旦改切分，评估集既失效又偏向旧配置——而切分恰恰是你要测的东西。

### 1.2 合成查询生成：RAGAS 与 ARES 两条路线
- **RAGAS**（[arXiv:2309.15217](https://arxiv.org/abs/2309.15217)）走**知识图谱**路线：文档先被 splitter 切成层次节点（作者明确举例"金融文档可按 Income Statement / Balance Sheet / Cash Flow Statement 分节"），用 NERExtractor/KeyphraseExtractor 抽属性、Jaccard 建边；再由 **Scenario = 节点 × Query Length × Query Style × Persona** 组合场景，由 QuerySynthesizer 产出 `(query, reference_contexts, reference)` [核实 [testset generation 文档](https://docs.ragas.io/en/stable/concepts/test_data_generation/rag/)]。关键优点：`reference_contexts` **本身是块/节点集合**，且构造不依赖任何检索系统。
- **ARES**（[arXiv:2311.09476](https://arxiv.org/abs/2311.09476)）用 few-shot 让 FLAN-T5 XXL 从段落反推 question+answer，并**过滤低质 query**：只保留"该 query 能把源段落检索回 top-1"的样本 [核实]。注意该过滤器是**构造性偏差**——它系统性偏向与源段落措辞重合的 query，会抬高 BM25 与稠密检索的表观成绩 [推断]。负例分 weak（随机无关段 / 他段生成的答案）与 strong（同文档段；不足则用 BM25 top-10 相似段采样；答案负例由模型生成**矛盾答案**）[核实]。

[推断] 对中文金融域：采用 RAGAS 的 KG+Scenario 骨架（以 `heading_path` 叶节点为 graph node），但必须补一道**口语化中文改写**，否则 query 会过度贴合读本原文措辞。

### 1.3 人写 vs 机器生成
可核实的一手数据只有两处：RAGAS 的 WikiEval 由 ChatGPT 生成 query+answer，人工只做质量维度标注（50 页；两名标注者在 faithfulness/context relevance 上一致率约 95%、answer relevance 约 90%）[核实]；ARES 报告**约 150 条以上人工标注是 PPI 的最低要求**（作者扫描了 25–400），实验用 300 条，且发现**用 GPT-4 替代人工标注做 rectifier 效果更差** [核实]。

[推断] 对你的配比：**机器只用于生成候选 query、生成负例、预筛；gold 判定 100% 人工确认**。query 数量不足可用 bootstrap 补救，gold 错误无法用统计补救。

---

## 2. 指标选择

### 2.1 适用边界（依据 [IR Book 第 8 章](https://nlp.stanford.edu/IR-book/html/htmledition/evaluation-of-ranked-retrieval-results-1.html)）
- **Precision@k / Recall@k**：集合度量，用**无序集合**计算，即 order-insensitive [核实]。IR Book 明确指出 P@k 是"最不稳定的常用指标"、且"不善于平均"，因为每 query 相关文档数差异极大 [核实]。
- **MRR / RR@k**：位置敏感，只看首个相关 → 有多个可接受块的金融问答会被低估 [推断]。
- **MAP**：位置敏感；IR Book 称其"判别力与稳定性尤其好" [核实]。但需要**完整**相关集合；池化判断下未判断文档被当作不相关，MAP 会偏高 [推断]。
- **nDCG@k**：位置敏感且支持分级相关性（IR Book 公式 44）[核实]，是 rerank 的主指标。
- **Success@k / HitRate**：最粗但最稳，适合当回归红线。

### 2.2 为什么 rerank 场景必须用 nDCG 而非 Recall
设 reranker 输入候选集 R（|R| = W），它只重排 R、不改变 R：
- 若报告的 **k = W**，则"前 k 个"恒等于 R → **Recall@k 对 rerank 完全不敏感**，rerank 的边际收益必然显示为 0。
- 若 **k < W**，Recall@k 会变，但被 **Recall@W（第一阶段候选召回率）硬性封顶**；rerank 只能在该上界下重新分配前 k 的成员。
- 位置敏感指标（nDCG@k / MRR / P@1）不受此约束。这正是 IR Book"precision/recall 是无序集合度量，要评估排序结果必须扩展这些度量"的直接推论 [核实] → 结论本身 [推断]。

### 2.3 k 怎么选
[推断] 规则：**报告窗口 k ≤ rerank 深度 W，并同时报告 W 处的召回率作为"回召率上限"**。建议 W = 50（延迟允许则 100），报告 nDCG@5、nDCG@10、MRR@10 与 Recall@W。若只报 nDCG@10 而 W = 10，你测的是上限不是 rerank。工程锚点可参考 Anthropic：第一阶段 **top-150**、rerank 后 **top-20**，且试过 5/10/20 后认为 **20 最优** [核实 [Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)]。

---

## 3. 块级标注：chunk_id 脆弱 vs heading/span 稳定

- **chunk_id 标注是反模式** [推断]：它把评估集与切分参数（size/overlap/切分器版本）焊死，任何切分实验都会让评估集整体失效——而你的目标正是"对切分敏感"。你已有的 **`heading_path` 恰是业界缺的稳定锚点**。
- 粒度参考（均为 [核实] 摘要）：
  - **BEIR**（[arXiv:2104.08663](https://arxiv.org/abs/2104.08663)，NeurIPS 2021 D&B）：18 个异构数据集、10 个系统，定位 zero-shot/OOD 泛化，**本质是文档/段落级**，不宜直接照搬做块级任务。（主指标是否为 nDCG@10 未能从原文核实。）
  - **Qasper**（[arXiv:2105.03011](https://arxiv.org/abs/2105.03011)）：5,049 问 / 1,585 篇论文；**问题由只看过标题+摘要的人写**，另一组人回答并"provide supporting evidence" [核实]。
  - **SQuAD 2.0**（[arXiv:1806.03822](https://arxiv.org/abs/1806.03822)）：50,000+ 条**对抗式人工撰写的无答案问题**，刻意"look similar to answerable ones"；强模型从 86% F1 跌到 **66% F1** [核实]。

**落地（稳定三元锚点）** [推断]：每条样本记 `(query, gold_heading_path 集合, gold_span 集合)`。命中用 **heading_path 前缀匹配**（gold = `… > 第二节 人民币防伪与反假`，命中 `… > 第二节 … > 二、 人民币防伪特征`）；`gold_span` 用相对原 PDF 文本的字符偏移作为切分无关的次级证据；`chunk_id` 仅作运行期缓存，**永不进入评估集**。

---

## 4. 父子块 / 多粒度评估

业界无统一标准 [推断]。可核实的相关做法：**RAPTOR**（[arXiv:2401.18059](https://arxiv.org/abs/2401.18059)）递归聚类+摘要建树、从不同抽象层级检索，QuALITY 上配 GPT-4 绝对提升 20% [核实摘要]，但其评估是端到端 QA 正确率，**不分离子块命中与父块充分性** [推断]；**Anthropic** 用 **1 − recall@20** 为指标，并强调 chunk boundaries 影响性能、"给更多 chunk 有上限"（5/10/20 中 20 最好）[核实]——这是"父块扩展有增益也有噪声"的间接证据。

**必须分开评估的理由** [推断]：你的失败模式有两类——(a) 子块未命中（召回/排序问题）、(b) 子块命中但父块错配或过大（切分与父子映射问题），混在一个指标里无法归因。

**建议新增三个自研指标** [推断]（无现成标准）：
- `ParentCoverage@k`：命中子块所覆盖的 gold 父块集合比例。你的父块仅 109 个，可**全量人工标注父子映射**。
- `ParentNoise@k`：回查父块后引入的非 gold 父块数 / 返回父块总数，量化上下文扩展的代价。
- `Parent Mismatch Rate`：子块命中但父块不属于 gold 父块集合的比例，专用于监控映射 bug。

---

## 5. 拒答 / 无答案检测评估

**SQuAD 2.0 的可迁移要点** [核实]：无答案问题必须**对抗式人工撰写**且形似可回答问题；论文明确批评当时数据集用的是"automatically generated unanswerable questions that are easy to identify"。→ 你的 `low_confidence` 负例集**不能只用 LLM 生成**。

**负例分层**（借用 ARES 的 weak/strong 思路 [核实]）[推断]：weak = 域外问题（如"纳斯达克最新收盘点数"）；strong = 同域但读本未覆盖的产品问题；另加**目录页/页眉页脚/版权页查询**（同时是切分质量负例）与数值幻觉诱导题。

**指标**（选择性预测的标准做法）[推断]：把 `low_confidence` 视为二分类器，报告阈值扫描下的 **risk–coverage 曲线**、**误拒率 FRR**（可回答 query 被判 low_confidence 的比例）、**漏拒率 FPR**（无答案 query 未被判 low_confidence 的比例）、给定阈值下的 coverage 与 accepted-answer precision，单一数字用 **AUC 或 balanced accuracy**（无答案 query 占比会严重扭曲 accuracy）。相关但**未核实**：Probing-RAG（[arXiv:2410.13339](https://arxiv.org/abs/2410.13339)，NAACL 2025 Findings）做自适应检索必要性判断并减少冗余检索步数 [核实摘要]，其"拒答率"具体定义未读到；Self-RAG、CRAG 亦未核实。

---

## 6. 统计严谨性

**小样本 CI 量级** [推断/算术]：比例型指标（Success@k、Precision、low_confidence 准确率）在 n 条 query 下，95% 正态近似 CI 半宽 ≈ 1.96·√(0.25/n)：n=50 → **±13.9 pp**；n=100 → **±9.8 pp**；n=150 → **±8.0 pp**。含义：**50–100 条 query 下"两个配置差 3 个点"这类结论统计上无意义**。这与 ARES 把 150 定为人工标注下限属同一量级 [核实]。

**正确做法**：
1. **Bootstrap CI**：**按 query 重采样**（不是按块），报 percentile CI。ARES 用 **PPI**（prediction-powered inference，Angelopoulos et al. 2023）在 300 条人工标注 + 大量未标注预测上得到 95% CI，并以 **CI 中点排序系统** [核实]。你可以借用同一思路：把"LLM 对全库 664 块的判定"当作未标注预测，用少量人工标注做 rectifier，在不增加人工的前提下收窄 CI。
2. **配对检验**：paired bootstrap / **Fisher 随机化检验**（每 query 随机交换两系统结果，10⁴ 次得 Δ 的零分布）/ **Wilcoxon signed-rank**（连续指标、不假设正态）。这是 IR 的标准做法族——我核实到文献存在：Ferro & Sanderson, *Statistical Significance Testing in IR: Theory and Practice*（[SIGIR 2014 tutorial](https://dl.acm.org/doi/pdf/10.1145/2600428.2602292)）、Carterette, *Multiple testing in statistical analysis of systems-based IR experiments*（[TOIS 2012](https://dl.acm.org/doi/epdf/10.1145/2094072.2094076)）、Sakai（[EVIA 2013](https://research.nii.ac.jp/ntcir/workshop/OnlineProceedings10/pdf/EVIA/01-EVIA2013-SakaiT.pdf)）、*Agreement Among Statistical Significance Tests for IR Evaluation at Varying Sample Sizes*（[CIIR PDF](https://ciir-publications.cs.umass.edu/getpdf.php?id=885)）——**但均为 PDF/付费墙，本环境 web_fetch 不支持 PDF，故其具体建议（检验选择、样本量阈值）未能核实**。
3. **多重比较校正**：Carterette 2012 的主题即"比较多个系统时的多重检验"。6 个配置 = 15 组两两比较，须用 Holm/Bonferroni，或预先指定单一 baseline 只做 baseline-vs-others [推断]。

**为什么消融必须配对** [推断/统计学常识]：所有配置在**同一批 query** 上评测，query 难度是共同方差。配对检验先算每 query 的 d_i = metric_A(q_i) − metric_B(q_i) 再对 d 检验，消掉 query 主效应，功效显著高于比较两个均值；反之，比较均值会把"评估集里恰好有几条特别难的 query"误读为配置差异。所以**报告必须给"均值 + Δ 的配对 CI + 配对 p 值"，而不是只给均值表**。

---

## 7. 消融实验框架

[推断]（ColBERT / SPLADE 的消融正文我未读到，以下为基于第 6 节统计要求的设计）：

1. **冻结变量**：语料快照 hash、切分器版本、评估集版本、随机种子、rerank 深度 W、k 集合——写入每次 run 的产物。
2. **两级消融**：
   - **OFAT**：每次只关一个组件——(a) 仅稠密；(b) 仅稀疏；(c) RRF → 加权求和/单路；(d) 去 rerank；(e) 变切分参数；(f) 去 heading_path 前缀注入（若检索输入用了它）。
   - **累加**：dense → +sparse → +RRF → +rerank，报**每一步的边际 Δ 及其配对 CI**。这才是"每一步可归因"。
3. **报告格式**：每配置一行——`nDCG@10`、`nDCG@10 的 bootstrap 95% CI`、`Δ vs baseline`、`Δ 的配对 CI`、`配对随机化 p`、`Recall@W（上限）`、`MRR@10`。缺任一列都会导致过度解读。
4. **耦合陷阱**：关掉一路时，另一路的候选深度应保持不变，否则你测到的是"深度变化"而非"融合收益" [推断]。

---

## 8. 切分质量本身的指标

- **HOPE（Holistic Passage Evaluation）**（[arXiv:2505.02171](https://arxiv.org/abs/2505.02171)，SIGIR 2025）是直接命中的新工作：把切分特征分三层——**intrinsic passage properties / extrinsic passage properties / passages–document coherence**，聚合成跨域自动指标；跨 7 域显示其与 RAG 指标显著相关（原文写作 "correlates significantly (p > 0.13)"，不等号方向疑为摘要笔误，此处按原文引用）；关键结论是"**passage 之间的语义独立性**对系统性能至关重要，最高带来 **56.2%** factual correctness 与 **21.1%** answer correctness 提升；相反，**传统上'保持 passage 内概念统一'的假设影响很小**" [核实摘要]。→ 这直接挑战你"按 `heading_path` 追求概念统一"的隐含假设：更该关注**块的自我完备性**。
- **经典切分指标**：**Pk**（Beeferman et al. 1999）与 **WindowDiff**（Pevzner & Hearst 2002，*A Critique and Improvement of an Evaluation Metric for Text Segmentation*, CL 28(1):19–36, [ACL Anthology](https://aclanthology.org/J02-1002/)）。我核实到该文题录与卷期页码，但 **Pk 的缺陷与 WindowDiff 的定义未能读到**（PDF 不可抓取）。[推断] 这类指标度量滑动窗口内的边界重合，适合对比两套切分方案，但需要人工标注真实边界。
- **对你这套系统的直接度量** [推断]：① **标题覆盖率**（664 块中带非空 `heading_path` 的比例、路径深度分布）；② **边界孤块率**（过短、无完整句、属于目录/页眉/表格残片的块占比）；③ **passage 自完备率**（HOPE 式：只给该块，问 LLM 能否回答该块对应问题）。
- **RAGAS 的 context precision/recall 能否做切分诊断？** [推断] 部分可以，但有陷阱：RAGAS **Context Precision 是排序敏感**的（`Σ(Precision@k × v_k) / 前 K 中相关项总数`；官方文档演示"不相关块放第 2 位分数不变、放第 1 位分数减半，0.9999 → 0.4999"）[核实]，用它做切分诊断等于在重复测排序。**Context Recall 必须有 reference**（"calculating context recall always requires a reference to compare against"，实现是把 reference 拆成 claims 看有多少能被检索上下文支持）[核实]。想归因到切分，应改用 **NonLLMContextRecall**（`检索到的相关上下文数 / reference 上下文总数`）或 **IDBasedContextRecall**（`|reference ids ∩ retrieved ids| / |reference ids|`）[核实]，并把 id 定义成 **heading_path 而非 chunk_id**。

---

## 9. RAG 端到端评估（简述）

- **RAGAS**：论文定位 **reference-free**（不需人工标注）；三维度——**Faithfulness** F = |V|/|S|（被上下文支持的陈述数/总陈述数）、**Answer Relevance** AR = 由答案反推 n 个问题后与原问题嵌入相似度的均值、**Context Relevance** CR = 抽取关键句数/上下文总句数 [核实 [arXiv:2309.15217v2](https://arxiv.org/html/2309.15217v2)]。自建 WikiEval（50 页）上与人工判断一致率：**Ragas 0.95/0.78/0.70** vs GPT-Score 0.72/0.52/0.63 vs GPT-Ranking 0.54/0.40/0.52；作者自承 **context relevance 最难，"ChatGPT often struggles with the task of selecting the sentences from the context that are crucial, especially for longer contexts"** [核实]。
  - **陷阱**：论文版 "context relevance" 与当前库里的 "context precision/recall" **不是一回事**——后者 `ContextPrecision` 文档写明 "Use this when you have a reference answer available" [核实 [文档](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/context_precision/)]。
- **ARES**：不靠手写 prompt，而是**为每个 RAG 组件定制 judge**（DeBERTa-v3-Large 二分类微调）+ **PPI 置信区间**；在 KILT+SuperGLUE 上比 RAGAS 的 context relevance 准确率高 **59.9 pp**、answer relevance 高 **14.4 pp**，Kendall τ 平均高 0.065 / 0.132，且比"每系统抽 150 条人工标注（9 系统共 1350 条）"的基线**少用 78% 标注**；mock 系统按 2.5% 精度间隔自 70% 造到 90%（9 个）[核实]。
- **"无参考答案"的边界** [推断]：RAGAS/ARES 的 reference-free 是**生成侧**的解放；**检索侧 reference 无法省**——ARES 自己仍需 ~150 条人工标注才能给出可信 CI [核实]。你做纯检索，不要指望 reference-free 指标替代相关性判断。

---

## 10. 落地建议（可直接执行）

**A. 评估集**
1. **80–120 条 query**，其中 **20–30% 无答案**（约 20–30 条）。依据：n=100 时比例指标 CI 半宽 ≈ ±9.8 pp；<50 条时 >±14 pp，消融无法得出结论 [推断/算术]。
2. 664 子块 → **穷举判断，不做池化**。
3. 流程：① 以 `heading_path` 叶节点为 node 建 RAGAS 式 KG，生成候选 query 池 ~300 条（具体/抽象 × 单跳/多跳）；② **人工筛选并改写为真实用户口吻**的 80–120 条（去掉"根据所给上下文"类痕迹）；③ 对每条 query **全库判定** gold 块，记 `gold_heading_path`（前缀匹配）与 `gold_span`（字符偏移），**不记 chunk_id**；④ 无答案 query 按 SQuAD 2.0 原则**对抗式人工撰写** [核实]。
4. 负例分层：weak（域外）+ strong（同域未覆盖 + 目录/页眉/版权页）。**能测出阈值的只有 strong** [推断]。

**B. 指标组合（固定报告这套）**
- 检索：`nDCG@5`、`nDCG@10`、`MRR@10`、`Recall@50`（= **回召率上限**）、`Precision@3`（下游上下文纯度）。
- 多粒度：`ParentCoverage@10`、`ParentNoise@10`、`Parent Mismatch Rate`（自研，§4）。
- 拒答：阈值扫描下的 **FRR / FPR / coverage** + **AUC**。
- 切分诊断：标题覆盖率、边界孤块率、passage 自完备率（HOPE 式 [核实]）。
- **禁止**只报文档级命中率；**禁止**在 rerank 深度 = k 时用 Recall@k 论证 rerank 收益。

**C. k 与深度**：固定 W（建议 50，延迟允许则 100），报告 k ∈ {5,10} 的 nDCG 与 MRR@10，并同时报 `Recall@W`。若 `Recall@W` 已经低，先修第一阶段，不要调 rerank。

**D. 显著性检验（每个数字都带）**
1. 每指标报**按 query 重采样的 bootstrap 95% CI**（10,000 次）。
2. 每个消融对比报 **Δ + Δ 的配对 bootstrap CI + Fisher 随机化检验 p**，必要时加 Wilcoxon。
3. 多配置比较用 **Holm 校正**，或预先声明单一 baseline。
4. 报告写清 n、评估集版本、语料与切分快照 hash。

**E. 消融排序（按可归因性）**
1. **切分层**（你最需要）：固定检索链路，只变切分 → 块级 nDCG + 切分诊断指标。这是唯一能证明"切分改动有效"的实验。
2. **父子映射层**：只变"命中子块→回查父块"规则（父块粒度、是否拼接兄弟块），看 `ParentCoverage/ParentNoise` 与最终 `nDCG@10`。
3. **召回层**：dense-only / sparse-only / RRF，同时报各自 `Recall@W`，避免融合收益与深度收益混淆。
4. **重排层**：同 W 下 rerank on/off，只看位置敏感指标。
5. **low_confidence 阈值层**：单独在无答案集上扫阈值，不要与上面混在一张表。

---

## 11. 未能核实 / 局限

1. **本环境 web_fetch 不支持 `application/pdf`**，故以下一手资料**正文未读**，仅核实题录存在：Zobel/Moffat 等 pooling 论文；Buckley & Voorhees 2004（bpref）；Yilmaz & Aslam 2006（infAP）；Ferro & Sanderson SIGIR 2014；Carterette TOIS 2012；Sakai EVIA 2013；*Agreement Among Statistical Significance Tests…*；Pevzner & Hearst 2002（Pk / WindowDiff 定义）；NIST [Common Evaluation Measures](http://trec.nist.gov/pubs/trec11/appendices/MEASURES.pdf)。
2. **trec_eval 的具体度量定义**（`ndcg_cut`、`recip_rank`、`recall_100`、`judged.10`、`num_rel_ret`）未取得权威正文：`raw.githubusercontent.com` 及多个 CDN 在本环境不可达或返回非文本类型，NIST PDF 不可抓取；`ir_measures` 文档站返回 404。
3. **BEIR 主指标（nDCG@10）与判断粒度细节**未从原文表格核实；仅核实摘要（18 数据集 / 10 系统 / BM25 稳健 / rerank 与 late-interaction 零样本最佳）。
4. **MTEB**：仅核实摘要（8 类任务、58 数据集、112 语言、33 模型）；Retrieval 任务指标列表未核实（HuggingFace 博客页返回空内容）。
5. **TruLens 的 RAG triad 精确定义**未核实（未取得可用页面）。
6. **DeepEval Contextual Precision 的计算公式**未核实：只读到"reference-based、LLM-as-a-judge、聚焦 reranker 排序"，必需参数为 `input / actual_output / expected_output / retrieval_context` [核实到该部分]，"How Is It Calculated" 段落因页面截断未取到。
7. **LlamaIndex retrieval eval 指标清单与 AutoMergingRetriever 的评估方式**未核实：`docs.llamaindex.ai` 301 至 `developers.llamaindex.ai`，页面导航占满并截断；源码经 CDN 返回 `application/octet-stream`。
8. **"回召率上限 / recall ceiling" 作为标准术语**未找到一手定义；§2.2 结论是我的数学推导（[推断]），依据是 IR Book 关于 precision/recall 为集合度量的论述 [核实]。
9. **ColBERT / SPLADE 的消融报告范式**正文未读；§7 为我的设计。
10. **Qasper 的 evidence 具体粒度**（段落 vs 句子）未从摘要确认，摘要仅说 "provide supporting evidence to answers"。
11. **Probing-RAG / Self-RAG / CRAG 的拒答率指标定义**未读正文；仅核实 Probing-RAG 摘要。
12. HOPE 摘要中 `(p > 0.13)` 的不等号方向疑为原文笔误，按原文引用未做修正。

**其他已核实但要留意**：NarrativeQA（[arXiv:1712.07040](https://arxiv.org/abs/1712.07040)）与本报告中 Qasper/SQuAD 2.0 的粒度差异，我只核实了 Qasper 与 SQuAD 2.0 的摘要，NarrativeQA 未单独核实。
