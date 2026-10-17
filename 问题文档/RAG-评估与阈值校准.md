# RAG 评估与阈值校准 · 问题与解决方案

> **模块定位**：衡量检索质量、并给"拒答阈值"定值。覆盖 `backend/RAG_/src/wealth_rag/evaluation.py`
> / `thresholds.py` / `calibration.py` 与 `backend/RAG_/eval/`（评估集、候选池、标注、校准工具链）。
>
> **本文件是拆分自 [`问题与解决方案.md`](./问题与解决方案.md) 的模块文档**，
> 索引与跨模块经验见 [`README.md`](./README.md)。
>
> **为什么这些卡片单独成文**：阈值（`no_answer_threshold` / `rerank_floor`）决定
> 「拒答还是硬答」，而财富规划领域编造一条监管规定的代价远高于一次误拒。
> 因此这一模块的问题都围绕"**指标能不能回答我要问的问题**"展开。

## 问题索引

| 编号 | 问题 | 严重度 | 状态 | 子模块 |
| :--- | :--- | :--- | :--- | :--- |
| P0-7 | 评估集失效，无法验证任何改动 | 🔴 前置条件 | ✅ 已修复（有实测证据：70 条 chunk 级评估集 + 框架重写） | 评估 |
| P0-10 | 文档声称评估为「文档级」，实际代码已是 chunk 级 | 🔴 高 | ⚠️ 部分修复（问题文档已更正；`RAG_/README.md` 与 `TECH_SUMMARY.md` 仍写「文档级」，未改） | 评估 |
| P0-11 | 评估框架不采集 rerank 分数，导致阈值不可校准 | 🔴 高 | ✅ 已修复（代码，未实测——已采集 7 个 rerank 分数字段） | 评估 |
| P0-12 | 评估只有二值标注，无法校准 `rerank_floor` | 🔴 高 | ✅ 已修复（代码，未实测——已加 grades 分级标注 + 分级 nDCG） | 标注 |
| P1-6 | 候选池用重排后的排序生成，引入确认偏误 | 🟠 中高 | ✅ 已修复（代码，未实测——候选池已改为关闭重排取候选） | 评估 |
| P1-7 | 「未标注」与「确认无关」在标签文件里长得一样 | 🟠 中高 | ✅ 已修复（代码，未实测——已加 `reviewed` 列表 + 覆盖率硬门槛） | 标注 |
| P1-8 | 扫阈最大化 F1 不是有效的阈值选择规则 | 🟠 中高 | ✅ 已修复（有实测证据：`selftest_calibration.py` 已跑通） | 阈值 |
| P1-9 | 重排收益的上界没有被测量（`Recall@W` 缺失） | 🟠 中高 | ⚠️ 部分修复（`ceilling_recall()` 已实现，但 CLI 与脚本两条路径仍未设置 `recall_w`，指标未出现在输出里） | 评估 |
| P2-7 | 旧查询集硬编码 `qtype`，字段名与实现不符 | 🔵 低 | ✅ 已修复（改为按 category 前缀判定，不硬编码类型清单） | 标注 |

---

### P0-7 · 评估集失效，无法验证任何改动

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 系统审查（审阅 `evaluation.py` 的相关性判定实现） |
| **代码引入** | 2026-10-10（初始提交） |
| **严重度** | 🔴 前置条件（不修则其余改动无法验证） |
| **状态** | ✅ 已修复（有实测证据：70 条 chunk 级评估集 + 框架重写） |
| **所属环节** | 评估 |
| **代码位置** | `src/wealth_rag/evaluation.py:71-77` |

#### 🔍 现象

`README.md` 声称「评估：6 条样例查询 Recall@10 / MRR / nDCG@10 = 1.000」，但这个 1.000 **对切分质量完全不敏感**，不能作为基线。

#### 🧪 证据

`evaluation.py:71-77` 只做**文档级**相关性判定：

```python
def is_relevant(result, relevant: list[str]) -> bool:
    for rel in relevant:
        if rel == result.doc_id or rel == result.doc_title:
            return True
        if rel and rel in result.source_path:
            return True
    return False
```

全库只有 6 篇文档、722 个块。文档级 Recall@10 意味着「10 个块里有一个来自正确文档即算命中」——**即使切分完全错乱也会接近 1.0**。而当时的 `eval/queries.example.jsonl` 只有 3 条示例（该文件已于 2026-10-15 清理，被 70 条真实评估集 `eval/queries.jsonl` 取代）。

#### 🎯 根因

**评估粒度与优化粒度不匹配。**

系统优化的对象是 **chunk**（切分策略、面包屑、父子块），评估的对象却是 **document**。粒度错配导致指标对优化目标**结构性失敏**——无论怎么改切分，指标都不动。

更深一层：**指标失效比没有指标更危险**。没有指标时人会保持警惕；有一个恒为 1.000 的指标，反而会制造"系统已经很好"的虚假信心，掩盖 P0-1~P0-6 这些真实缺陷长达 5 天。

#### ✅ 解决方案

**这是所有其它改动的前置条件**：没有可信指标，就无法判断 P0-1~P0-6 的改动是变好还是变坏。

**(a) 建 chunk 级评估集** `eval/queries.jsonl`（50–100 条真实问题）：

```jsonc
{"query": "人民币防伪特征有哪些",
 "gold_headings": ["二、 人民币防伪特征", "一、 什么是假币", "(一) 2005 年版第五套人民币100 元纸币"],
 "must_not": ["目 录"]}
```

- `gold_headings`：标注应命中的**标题路径**（比 chunk_id 稳定，重新切分后仍可用）。
- `must_not`：负例，用于直接检测目录页/页眉污染。

**(b) 改造 `evaluation.py`**，把 `is_relevant` 升级为标题/chunk 级匹配，并新增切分专属指标：

| 指标 | 定义 | 当前值 | 目标 |
| :--- | :--- | :--- | :--- |
| 标题覆盖率 | `raw_text` 含自身标题的块占比 | 0.14% | > 95% |
| 面包屑深度分布 | `len(heading_path)` 直方图 | 98.3% 深度 1 | 以 2–4 层为主 |
| 页眉污染率 | `heading_path` 等于书名的块占比 | 7.9% | 0 |
| 目录污染率 | 命中 `must_not` 的查询占比 | — | 0 |

**(c) 消融顺序**（每步单独记录，便于归因）：

```
(0) 现状基线
(1) + 页眉治理（P0-1）
(2) + 目录过滤（P0-2）
(3) + 标题回归证据文本（P0-3）
(4) + 语义层级重建（P0-4 / P0-5 / P0-6）
(5) + 乱码字符归一化（P1-1）
(6) + 短块合并（P1-2）
(7) + 同节去重（P1-3）
(8) + overlap 扫描 {0, 64, 128}（P2-1）
```

#### ✔️ 验证

基线本身即可验证：用新的 chunk 级评估集跑**改动前**的代码，指标**不应**是 1.000。若是 1.000，说明标注仍有问题。

#### 💡 学习要点

1. **评估粒度必须与优化粒度一致。** 优化 chunk 就要 chunk 级标注；优化排序就要排序级标注。粒度错配 = 指标失敏。
2. **指标失效比没有指标更危险。** 1.000 的假指标会让人停止怀疑，这是它最大的危害。
3. **用"标题路径"而非 chunk_id 做标注**，可以在重新切分后复用同一份评估集——切分策略迭代时这一点极其重要。
4. **负例（`must_not`）比正例更能暴露污染问题**，因为它直接编码了"这类东西不该出现"。

---

### P0-10 · 文档声称评估为「文档级」，实际代码已是 chunk 级

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 调研时逐行读 `evaluation.py`，发现实现与 README / TECH_SUMMARY 的描述完全不符 |
| **严重度** | 🔴 高（会误导设计决策） |
| **状态** | ⚠️ 部分修复（问题文档已更正；`RAG_/README.md` 与 `TECH_SUMMARY.md` 仍写「文档级」，未改） |
| **所属环节** | 文档 / 评估 |

#### 🔍 现象

`README.md` 与 `TECH_SUMMARY.md` 都写着：

> ⚠️ 该指标为**文档级**（`evaluation.py` 只匹配 doc_id / doc_title / source_path），
> **对切分质量不敏感，不能作为切分优劣的依据**；需另建 chunk 级评估集

而实际代码（`evaluation.py`）**早在 P0-7 修复时就已重写**为 chunk 级，并且能力远超文档描述。

#### 🧪 证据

实际实现与文档描述的对照：

| 文档说没有 | 代码实际有 | 位置 |
| :--- | :--- | :--- |
| chunk 级匹配 | ✅ 标签锚定**标题路径**，重新切分后仍可用 | `is_gold` |
| — | ✅ 可答/不可答分开报告，含拒答准确率与误拒率 | `EvalReport.answerable` |
| — | ✅ `must_not` 负例断言 | `is_forbidden` |
| — | ✅ **bootstrap 95% 置信区间** | `bootstrap_ci` |
| — | ✅ **配对 bootstrap 检验 + p 值** | `paired_bootstrap_pvalue` |
| — | ✅ 父子块诊断（回查成功率、同父冗余度） | `parent_coverage` / `parent_redundancy` |
| — | ✅ 重排收益的硬件上界 `Recall@W` | `ceilling_recall` |

**证据文件**：`eval/runs/baseline.json` 里已含 `误拒率(可答)`、`拒答准确率(不可答)`、
`must_not违反率`、`父节点诊断` 等字段——这些指标**在文档所述的"文档级实现"里根本不存在**。

#### 🎯 根因

**P0-7 修复时只更新了「修复状态」表格里的一行，没有回头修订描述评估能力的正文段落。**

这类"文档滞后"有特定的危险模式：**过时的描述比没有描述更糟**。因为它不是空白，
而是一个**明确的错误断言**——本次调研就是照着这句话得出「必须从零新建 chunk 级评估集」的错误结论，
直到读代码才发现 `eval/queries.jsonl` 里**已经躺着 70 条 chunk 级标注**。

这与 P2-4（文档与实现不符）是同一类问题，但危害更大：
P2-4 是描述不准，本条是**让后来者重复做已经做完的工作**。

#### ✅ 解决方案

1. **已修正调研文档**：在 `Investigation/README.md` 的「现状摘要」与
   `Investigation/04-open-questions-and-eval.md` 开头都加了显式更正，
   并在 README 里标注「模块自身的 README / TECH_SUMMARY 仍写文档级，**已过时**——以代码为准」。
2. **建议同步修订** `backend/RAG_/README.md` 第 248 行与 `TECH_SUMMARY.md` 的
   「已知限制」第 5 条与「验证与实测结果」表格——把「评估为文档级」改为
   「评估为 chunk 级，含 bootstrap CI / 配对检验 / 拒答指标」。
3. **建立机制**：把「文档里每个能力断言都必须能指向一个代码位置」作为文档评审项。

#### ✔️ 验证

```powershell
# 看实际存在的指标字段
python -c "import json;print(list(json.load(open('eval/runs/baseline.json',encoding='utf-8'))['summary'].keys()))"
# 期望看到：误拒率(可答) / 拒答准确率(不可答) / must_not违反率 / 父块回查成功率 ...
```

#### 💡 学习要点

1. **修复问题时，"变更清单"要包含所有描述该行为的文档。** P0-7 的修复表格更新了，
   但正文段落没更新，于是留下了一个"看起来权威的错误断言"。
2. **过时文档的危害 > 缺失文档。** 缺失会让人去查证；错误断言会让人直接照着做错事。
3. **调研结论必须追溯到代码，不能只读文档。** 本次如果只读 README，会得出完全错误的
   工作量估计（"从零建评估集 + 从零写评估器" vs 实际"复用已有 70 条 + 只补分数采集"）。

---

### P0-11 · 评估框架不采集 rerank 分数，导致阈值不可校准

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 为实现阈值校准而审阅 `evaluation.py`，发现 `QueryOutcome` 里没有任何 rerank 分数字段 |
| **严重度** | 🔴 高（校准工作的直接阻塞） |
| **状态** | ✅ 已修复（代码，未实测——已采集 7 个 rerank 分数字段） |
| **所属环节** | 评估 |

#### 🔍 现象

`config.yaml` 里的两个阈值（`no_answer_threshold: 0.05`、`rerank_floor: 0.25`）
决定「拒答还是硬答」，但**评估框架从不记录 rerank 分数**，
因此**没有任何数据可以用来判断这两个值是否合适**。

#### 🧪 证据

原 `QueryOutcome` 的字段只有：`ranks` / `n_gold` / `n_matched` / `violation_ranks` /
`low_confidence` / `ndcg` / `ap` / `rr` / 父子块诊断若干。

`rr`、`ndcg` 这些是**由排名推导出的指标**，而阈值判定需要的是**分数的原始分布**：

```
要判断「0.05 是否合适」，需要：
  positive 查询的 top_rerank_score 分布   ← 完全没有
  negative 查询的 top_rerank_score 分布   ← 完全没有
  相关块的 rerank_score 分布              ← 完全没有
  无关块的 rerank_score 分布              ← 完全没有

实际采集到的：
  排名、命中数、nDCG ……                    ← 与阈值无关
```

**换句话说：框架测量的是"排得好不好"，而校准需要的是"分数分不分得开"——
两者是不同的量。** 排名指标对阈值的任何变化都不敏感（只要相对顺序不变）。

#### 🎯 根因

**评估指标的选择沿用了"检索质量"的思路（排序好不好），而阈值校准属于"判定质量"
（分数能不能分开两类），二者的度量对象不同。**

更一般地：**当框架只输出"聚合后的指标值"时，就无法回答任何"该把某个阈值定在哪里"的问题**——
因为定阈值需要分布，而聚合值已经把分布丢掉了。

#### ✅ 解决方案

在 `QueryOutcome` 上新增 7 个字段（`evaluation.py`）：

```python
top_rerank_score: float | None      # 本次结果集里的最高重排分（阈值判定的输入）
top_rerank_rank: int | None        # 最高分所在排名（诊断「重排第1名是否就是 gold」）
max_relevant_rerank: float | None  # 相关块（grade≥1）中的最高分
max_irrelevant_rerank: float | None# 无关块（grade=0）中的最高分（假阳性上沿）
max_grade2_rerank: float | None    # 直接回答问题的最高分
max_grade1_rerank: float | None    # 相关但不完整的最高分
rerank_scores: list[float]         # 按返回顺序的全部重排分

@property
def margin(self) -> float | None:
    """相关块分 − 无关块分。> 0 表示排序方向正确；≤ 0 表示假阳性压过真相关。"""
```

`margin` 这个派生量特别有价值：它直接回答了**「重排器在这个查询上判断对不对」**，
比任何聚合指标都直观。实测中它可以用来看「有多大比例的查询存在假阳性压过真相关」。

同时在 `to_dict()` 的逐条结果里落盘这些字段（`top_rerank_score` / `top_rerank_rank` /
`max_relevant_rerank` / `max_irrelevant_rerank` / `margin`），
使 `--json` 产物**自带校准所需的原始数据**，不必为校准单独跑一遍。

#### ✔️ 验证

```powershell
cd E:\Python_Projects\Agent_study\backend\RAG_
python eval\run_eval.py --queries eval\queries.jsonl --top-k 10 --json runs\with_scores.json
python -c "import json;r=json.load(open('runs/with_scores.json',encoding='utf-8'))['rows'][0];print(r['top_rerank_score'], r['margin'])"
# 期望：打印出两个浮点数（而不是 None）
```

#### 💡 学习要点

1. **"聚合指标"与"分布数据"是两种产物，不能互相替代。** 想调阈值就必须保留分布；
   只有均值/排名时，任何阈值讨论都是空谈。
2. **同一个评估框架要同时服务两类问题**：排序质量（看指标）与判定质量（看分布）。
   设计时只想到前者，后者就永远做不了。
3. **`margin`（相关块分 − 无关块分）是被忽视的好指标。** 它把"排序是否正确"从
   聚合指标还原成每个查询上可观察的事实，比 MRR 更适合定位"重排在哪里失效了"。

---

### P0-12 · 评估只有二值标注，无法校准 `rerank_floor`

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 设计阈值推荐规则时，发现 `rerank_floor` 需要的「中间档」在标注体系里不存在 |
| **严重度** | 🔴 高 |
| **状态** | ✅ 已修复（代码，未实测——已加 grades 分级标注 + 分级 nDCG） |
| **所属环节** | 评估 / 标注体系 |

#### 🔍 现象

现有评估集的标注是**二值**的：一个块要么在 `gold_headings` 里（命中），要么不在（未命中）。
但三档阈值判定需要一个**中间档**：

```
no_answer_threshold (0.05)  ← 划出 negative  （库里没答案）
rerank_floor        (0.25)  ← 划出 partial   （相关但不完整）  ← 二值标注给不出这一档
                            ← 之上是 positive（直接回答问题）
```

#### 🧪 证据

`Retriever._fuse()` 的三档逻辑（`retrieval/hybrid.py:105-126`）：

```python
if max_rerank < r.no_answer_threshold:        # negative
    → 全部标记 low_confidence
elif max_rerank >= r.rerank_floor:            # positive
    → RRF 归一化分与 rerank 分加权融合
else:                                         # partial ← 这一档
    → 保留 RRF 排序（泛查询保底）
```

中间档的语义是「重排器对这些结果没把握」，**而二值标注下，一个块要么算相关要么算无关，
不存在"相关但不足以回答问题"这个状态**。因此：

- `partial` 类的查询样本数为 **0**；
- `rerank_floor` 的推荐值无从计算（分位分母为空）。

#### 🎯 根因

**标注粒度与判定粒度不匹配——这是 P0-7「评估粒度与优化粒度不匹配」的同类问题，
只是换了一层。**

P0-7 是「优化 chunk、评估 document」；本条是「判定三档、标注二值」。
两次都是**下游需要的分辨力，上游标注没有提供**。

更具体地说：二值标注回答的是"这个块和问题有关吗"，
三档判定问的是"这个块能不能**独立回答**问题"。这是两个不同的问题。

#### ✅ 解决方案

**引入分级标注（0/1/2）**，与 CRAG 论文的三档动作对齐：

| 分值 | 含义 | 对应档位 |
| :--- | :--- | :--- |
| **2** | 直接回答问题——答案所需信息**完整**出现在该块内 | positive |
| **1** | 相关但不完整——同主题、提供背景，但单独不能回答问题 | partial |
| **0** | 无关 | negative |

实现（`evaluation.py`）：

```python
@dataclass
class QueryCase:
    grades: dict[str, int] = field(default_factory=dict)  # chunk_id → 0|1|2

    def grade_of(self, chunk_id: str) -> int:
        return int(self.grades.get(chunk_id, 0))
```

并让 **nDCG 在给定 `grades` 时改用分级增益**：

```python
_GAIN = {0: 0.0, 1: 1.0, 2: 3.0}   # gain = 2^grade − 1

def _graded_ndcg(results, case, top_k):
    gains = [_GAIN.get(case.grade_of(r.chunk_id), 0.0) for r in results[:top_k]]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    # IDCG 用「标注中增益最高的 k 个」——不能用返回结果里的最高增益，
    # 否则漏掉高分块的系统会因分母同样变小而拿到虚高 nDCG
    ideal = sorted((_GAIN.get(g, 0.0) for g in case.grades.values()), reverse=True)[:top_k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0
```

**为什么必须改 nDCG**：二值 nDCG 把 grade=2 与 grade=1 当成一样好，
而**重排的价值恰恰在于把 grade=2 顶到 grade=1 前面**。
用二值 nDCG 衡量重排效果，等于把要测的差异事先抹平了。

#### ✔️ 验证

新增 `eval/selftest_calibration.py`，专门验证分级 nDCG 的数学性质（不加载模型）：

```powershell
python eval\selftest_calibration.py
# 期望覆盖：
#   完美排序（2,1,0）→ nDCG = 1
#   最差排序（0,1,2）→ 0 < nDCG < 1
#   全部漏掉        → nDCG = 0
#   只返回 grade1   → nDCG = 1/3（分母含未召回的 grade2）
```

最后一条是**关键的防错断言**：它确保 IDCG 用的是标注而非返回结果。

#### 💡 学习要点

1. **判定分几档，标注就得分几级。** `rerank_floor` 的存在本身就要求标注能区分"相关"与"可用"。
2. **二值标注会把要测的差异提前抹平。** 用二值 nDCG 评估重排，测不出重排贡献——
   因为重排做的正是"在同为相关的块之间重新排序"。
3. **这是 P0-7 的同类问题在不同层级的复现**（优化粒度 vs 评估粒度 → 判定粒度 vs 标注粒度）。
   说明"粒度匹配"需要作为一条**逐层检查项**，而不是修一次就完事。

---

### P1-6 · 候选池用重排后的排序生成，引入确认偏误

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 设计标注流程时，推演"标注者会看到哪些候选" |
| **严重度** | 🟠 中高（会让评估集系统性低估召回能力） |
| **状态** | ✅ 已修复（代码，未实测——候选池已改为关闭重排取候选） |
| **所属环节** | 评估方法 |

#### 🔍 现象

如果候选池直接取 `Retriever.search()` 的默认输出（`use_rerank=True`），
标注者看到的候选就是**系统已经排好序的前 N 名**。
重排器没排进前列的相关块，**根本不会进入标注者的视野**。

#### 🧪 证据

推演两种候选生成方式的后果：

| 方式 | 标注者看到的候选 | 后果 |
| :--- | :--- | :--- |
| 重排后 top-10 | rerank 认为最相关的 10 个 | 若某个真相关块被 rerank 排到第 15 位，它**永远不会被标注为相关** |
| 纯 RRF 排序 top-10 | 融合召回的 10 个 | 视野不由重排决定；重排排错的情况能被标注**发现** |

前者会造成一个隐蔽的循环论证：

```
用 rerank 的结果定义"什么是相关的"
  → 再评估 rerank
  → nDCG 自然很高
  → 得出"rerank 效果很好"的错误结论
```

#### 🎯 根因

**用被评估系统的输出作为评估的输入基准。**

这是评估设计中的经典错误，与 P0-7 的"指标失敏"不同——这里的危害是**指标虚高**。
而且它极难被发现，因为**没有任何异常信号**：指标很高，看起来系统很好。

#### ✅ 解决方案

`eval/build_eval_pool.py` 固定用 **`use_rerank=False`** 取候选：

```python
# 候选池深度独立于重排：用 use_rerank=False 取「纯 RRF 融合排序」，
# 候选顺序因此不受重排影响，标注视野也就不会被系统的既有排序所限制（避免确认偏误）。
results = retriever.search(case.query, top_k=pool_depth, use_rerank=False)
```

同时把 `system_gold`（由已有 `gold_headings` 映射）作为**参考**写入模板，
并明确标注「**标注者可推翻**」——避免标注者把系统判断当权威。

另外加了深度提示：候选最多只能取到 `config.retrieval.fused_top`（默认 20），
`--pool-depth` 超过它不会报错但拿不到更多候选——**静默截断是最容易出错的配置行为**，
所以显式打印提示。

#### ✔️ 验证

```powershell
python eval\build_eval_pool.py --queries eval\queries.jsonl --out eval\pool --pool-depth 10
# 检查：pool.jsonl 里每个候选的 rerank_score 应为 null（证明关闭了重排）
python -c "import json;c=json.loads(open('eval/pool/pool.jsonl',encoding='utf-8').readline())['candidates'][0];print(c['rerank_score'], c['recall_score'])"
# 期望：None  <某个浮点数>
```

#### 💡 学习要点

1. **候选池必须独立于被评估系统的排序。** 否则评估变成自我实现预言。
2. **这类错误没有异常信号，最难发现。** 指标虚高时人只会高兴，不会怀疑——
   所以在**设计阶段**就要问："这个评估的输入是从我要评估的东西里来的吗？"
3. **"标注者可推翻系统判断"必须显式声明。** 否则 `system_gold` 这个便利字段会变成锚定效应。

---

### P1-7 · 「未标注」与「确认无关」在标签文件里长得一样

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 设计标签文件格式时发现：预填 0 与其他原因导致的 0 无法区分 |
| **严重度** | 🟠 中高（会让不完整的标注被当成高精度标注） |
| **状态** | ✅ 已修复（代码，未实测——已加 `reviewed` 列表 + 覆盖率硬门槛） |
| **所属环节** | 评估方法 |

#### 🔍 现象

标注模板预先为每个候选填 `grade: 0`（省去标注者手填多数项）。
但这样一来：

| 情况 | 文件里的样子 |
| :--- | :--- |
| 标注者看过，确认无关 | `{"grade": 0}` |
| 标注者根本没看 | `{"grade": 0}` |

**两者完全无法区分。**

#### 🧪 证据

推演危害：一个「只标了 3 条、其余 260 条全是默认 0」的标签文件，
在合并后会被当成**精确率极高的评估集**：

```
被"确认"的候选：263 个
其中相关：3 个
→ 精确率 = 3/263 = 1.1%   ← 看起来很低，但被解读为"系统精确率差"
```

而实际语义是「263 个里有 260 个根本没人看过」。这个误解会导向**完全错误的优化方向**
（去优化精确率，而问题其实是标注没做完）。

#### 🎯 根因

**默认值与"人工确认的取值"共用同一个表示。**

这在数据标注中是普遍陷阱：凡是"预填 + 人工修改"的流程，都会遇到
"这个值是默认的，还是人改的？"。解决办法只有一条——**把"是否被确认"变成显式数据**，
不能靠推断。

#### ✅ 解决方案

**(a) 标签模板里显式声明 `reviewed` 列表**，由 `apply_grades.py` 强制校验：

```python
# 「被看过」= 显式列入 reviewed，或填了非 0 分（非 0 必然看过）
if cid in reviewed or grade != 0:
    stats["reviewed"] += 1
```

**(b) 覆盖率硬门槛**：

```python
WARN_COVERAGE = 0.6   # 低于此值告警
MIN_COVERAGE = 0.3    # 低于此值直接报错并退出

if coverage < MIN_COVERAGE:
    raise SystemExit(
        f"标注覆盖率仅 {coverage:.1%}（{reviewed}/{candidates}），"
        f"低于 {MIN_COVERAGE:.0%} 下限：此时分数分布几乎全部来自默认值 0，"
        "任何阈值结论都不可信。请补全标注后重试。"
    )
```

**(c) 提供 `--seed-from-gold` 作为"诚实的捷径"**：与其让未标注项**伪装成**已确认，
不如显式地把"由已有 gold 派生"这一步做成一个**有名字的操作**，
并明确说明它的能力边界：

```python
# 未被人工确认 → 用 system_gold 派生（仅二值：2 或 0）
grade = 2 if rec.get("system_gold") else 0
stats["derived"] += 1
```

同时在输出里显式提示：**「派生值区分不出 grade=1，因此 `rerank_floor` 仍需真实人工标注」**。

#### ✔️ 验证

`selftest_calibration.py` 覆盖三个断言：

```powershell
python eval\selftest_calibration.py
#   覆盖率 100% 时不报错
#   覆盖率过低时抛 SystemExit
#   非法 grade（如 5）抛 SystemExit
```

#### 💡 学习要点

1. **默认值与人工值必须可区分。** 凡"预填 + 人工修改"的流程都要显式记录"哪些被改过"。
2. **"诚实的捷径"优于"伪装成完整的标注"。** `--seed-from-gold` 让派生这一步可见、可审计、
   有明确的能力边界；而让它伪装成人工标注则会污染全部下游结论。
3. **覆盖率应该是一个会阻断流程的硬门槛，而不是一个提示。** 低于门槛时任何统计结论都不可信，
   此时继续往下跑只会产生错误决策。

---

### P1-8 · 扫阈最大化 F1 不是有效的阈值选择规则

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 实现阈值推荐时，推演"如果按 F1 最大选，会选出什么" |
| **严重度** | 🟠 中高（会系统性地选出偏向硬答的阈值） |
| **状态** | ✅ 已修复（有实测证据：`selftest_calibration.py` 已跑通） |
| **所属环节** | 评估方法 |

#### 🔍 现象

初始实现用「扫一遍阈值、取 F1 最大」的规则推荐 `no_answer_threshold`：

```python
pos_kept = sum(1 for s in pos_scores if s >= th) / len(pos_scores)
neg_rejected = sum(1 for s in neg_scores if s < th) / len(neg_scores)
f1 = 2 * pos_kept * neg_rejected / (pos_kept + neg_rejected)
```

但 `pos_kept` 的定义有问题。

#### 🧪 证据

`pos_kept` 统计的是「**positive 查询的顶分 ≥ 阈值**」——
它**不检查那个顶分是否真的来自相关块**。

于是出现这种情况：

| 查询 | 真实情况 | 顶分来源 | 被 F1 如何计入 |
| :--- | :--- | :--- | :--- |
| A | 库里有答案 | 相关块，0.85 | ✅ 保留成功 |
| B | 库里有答案 | 相关块，0.30 | ✅ 保留成功 |
| C | 库里**没**答案（但被误标为 positive） | **噪声块**，0.70 | ✅ **也算保留成功** ← 问题所在 |

第 C 行的噪声顶分会**抬高 F1**，使扫阈结果偏向"更低的阈值"
（低阈值让更多查询被判定为"有答案"）——即**偏向硬答**。

在财富规划领域这是危险的方向：**编造一条监管规定的代价，远高于一次误拒。**

#### 🎯 根因

**用了一个代理指标，但代理指标与真实目标之间存在系统性偏差。**

真实目标是"有答案时别拒答（且答案确实来自相关块）"，
而 `pos_kept` 只捕获了"有答案时别拒答"，丢掉了括号里的条件。
当噪声块的分数分布与相关块重叠时，这个偏差就会系统性地把阈值推向低端。

#### ✅ 解决方案

**改用分位规则作为主推荐**——语义直接、不依赖任何代理指标：

```python
def choose_no_answer_threshold(pos_scores, neg_scores, min_cover):
    """主规则：阈值放在 positive 顶分分布的 (1 − min_cover) 分位。
    语义直接：允许至多 (1 − min_cover) 的有答案查询被误拒。"""
    th_quantile = _percentile(sorted(pos_scores), max(0.0, 1.0 - min_cover))
    ...
```

同时给出**保守备选**（覆盖率约束内的最大阈值）——分布不可分时它偏向拒答，
正是本项目需要的偏向。

**F1 点降级为报告项**，打印出来供参考，但**不作决策依据**，
并在输出里写明原因：

```
参考（不作决策依据）：扫阈曲线最大 F1 在阈值 0.0431，F1=0.9231
（positive 保留 96.8%，negative 拒答 88.2%）。
该点不校验顶分是否来自相关块，易被噪声抬高。
```

#### ✔️ 验证

`selftest_calibration.py` 用合成分布验证规则行为：

```powershell
python eval\selftest_calibration.py
#   完全可分场景（positive 0.60~1.00 vs negative 0.00~0.02）
#     → 阈值应落在 positive 低端（≈0.62）且高于全部 negative
#   完全重叠场景 → 仍应给出阈值且不崩溃；保守阈值 ≥ 主阈值
#   缺 positive  → 返回 None（无法定阈值）
#   无 negative  → 仍给出阈值（但无拒答依据）
```

#### 💡 学习要点

1. **代理指标要与真实目标对齐，否则会系统性偏向一侧。** F1 在这里的偏差方向恰好是
   最危险的一侧（偏向硬答）。
2. **能直接算的，就不要用代理指标去凑。** 分位规则语义明确（"允许 5% 误拒"），
   比"最大化 F1"更容易审查、更不容易出错。
3. **保守备选与主推荐应当并列给出。** 当数据不可分时，
   "偏向拒答"和"偏向作答"是两个不同的业务选择，应该让使用者看到并决定，而不是由算法替他决定。

---

### P1-9 · 重排收益的上界没有被测量（`Recall@W` 缺失）

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 审阅 `EvalReport` 时发现 `recall_w` 字段存在但从未被填充；`main()` 里有赋值但 CLI 与 `evaluate()` 都未设置 |
| **严重度** | 🟠 中高（会导致把"召回不行"误读为"重排没用"） |
| **状态** | ⚠️ 部分修复（`ceilling_recall()` 已实现，但 CLI 与脚本两条路径仍未设置 `recall_w`，指标未出现在输出里） |
| **所属环节** | 评估 |

#### 🔍 现象

`evaluation.py` 已实现了 `ceilling_recall(w)`：

```python
def ceilling_recall(self, w: int) -> float:
    """``Recall@W``（W = 第一阶段候选深度）——**重排收益的硬上界**。

    若报告窗口 k 等于重排深度 W，则"前 k 名"恒等于重排输入集，
    Recall@k 对重排**在数学上完全不敏感**。因此必须另行报告 W 处的召回率，
    否则会把"第一阶段召回不行"误读成"重排没用"。
    """
```

但 `main()` 里虽然有 `report.recall_w = int(...fused_top...)`，
`cli.py` 的 `eval_cmd` 与 `run_eval.py` 路径**都没有设置它**，
因此这个上界指标**从未在任何评估输出里出现过**。

#### 🧪 证据

```python
# cli.py 的 eval_cmd（现状）
report = evaluate(settings, queries, top_k=top_k, use_rerank=not no_rerank)
render_report(report)          # ← recall_w 未设置，render 里 `if report.recall_w:` 为假，整段跳过

# evaluation.py::main（已设置）
report.recall_w = int(getattr(settings.retrieval, "fused_top", 0) or 0)
```

这是典型的「**能力已实现但未接线**」——代码、注释、渲染分支全都写好了，
只有最后一步赋值在两条路径之一上缺失。

#### 🎯 根因

**同一个能力有多个入口（CLI / 脚本 / 库），接线只做了一处。**

`evaluate()` 本身不设置 `recall_w`，而它**没有 `fused_top` 的信息**（那在 settings 里）。
于是每个调用方都要自己记得设置——只要有一个入口忘了，指标就静默消失。

**"静默消失"是这类问题的关键特征**：`render_report` 用 `if report.recall_w:` 保护，
缺值时只是不打印，**不报错、不告警**。

#### ✅ 解决方案

**建议方案（未实施，列为待办）**：把 `recall_w` 的填充**下沉到 `evaluate()` 内部**，
让调用方无需关心：

```python
def evaluate(settings, queries_path, top_k=10, use_rerank=True, retriever=None):
    ...
    report = EvalReport(rows=rows, top_k=top_k, use_rerank=use_rerank)
    # 【建议】在内部填充，避免每个调用方各自记得设置
    report.recall_w = int(getattr(settings.retrieval, "fused_top", 0) or 0)
    return report
```

并在 `render_report` 里，当 `recall_w == 0`（即未设置）时**显式告警**而不是静默跳过：

```python
if report.recall_w:
    console.print(...)
else:
    console.print("[yellow]⚠️ recall_w 未设置，Recall@W 上界不可用[/yellow]")
```

**为什么必须修**：`top_k=10` 而 `fused_top=20` 时，`Recall@10` 只覆盖了一半候选，
此时**任何对重排的评价都不能只看 Recall@10**。没有 `Recall@W` 就无法区分：

| 现象 | 真实原因 | 正确对策 |
| :--- | :--- | :--- |
| Recall@10 低 | 第一阶段召回的 20 里就没有 gold | 改进召回（编码/切分/融合） |
| Recall@10 低 | 20 里有 gold，但重排没排进前 10 | 改进重排（模型/prompt/阈值） |

**这两种情况的对策完全相反，而只看 Recall@10 无法区分。**

#### ✔️ 验证

```powershell
python eval\run_eval.py --queries eval\queries.jsonl --top-k 10
# 期望输出包含：回召率上限 Recall@20（第一阶段候选深度 W） = ...
```

#### 💡 学习要点

1. **同一能力的多个入口，接线必须收敛到一处。** 否则必然有一个入口忘记，
   而且症状是**静默的**（指标消失而非报错）。
2. **可选的输出分支要区分"未启用"与"未设置"。** `if report.recall_w:` 把
   "配置为 0" 和 "忘记设置" 混为一谈，前者是合法状态，后者是 bug。
3. **报告窗口 k 与候选深度 W 的关系必须显式呈现。** 当 `k ≥ W` 时，
   位置敏感指标对重排**在数学上完全不敏感**——这是必须在报告里说明的边界条件。

---

### P2-7 · 旧查询集硬编码 `qtype`，字段名与实现不符

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 写 `build_eval_pool.py` 时，按调研文档的示例字段名实现，随后核对真实查询集发现不符 |
| **严重度** | 🔵 低 |
| **状态** | ✅ 已修复（改为按 category 前缀判定，不硬编码类型清单） |
| **所属环节** | 评估 |

#### 🔍 现象

调研文档设计的评估集格式用 `qtype`（`fact` / `policy` / `calc` / `followup` / `nohit`），
并规定"`nohit` 表示库内无答案"。

但真实评估集 `eval/queries.jsonl` 用的是 **`category`**，且负例用**中文前缀「负例」**表示：

```json
{"id":"q064","query":"什么是量子纠缠","category":"负例-域外","gold_headings":[],"note":"完全域外，应低置信/拒答"}
```

而 `evaluation.py` 里的约定是：

```python
#: 不可答查询的类别前缀约定
UNANSWERABLE_PREFIX = "负例"
```

#### 🧪 证据

三种"类型"表达方式同时存在于设计文档中：

| 来源 | 字段/取值 |
| :--- | :--- |
| 调研文档（我写的设计） | `qtype`: `fact` / `policy` / `calc` / `followup` / `nohit` |
| 真实查询集 | `category`: `枚举` / `事实` / `定义` / `程序` / `数值` / `对比` / `表格` / `负例-*` |
| 代码约定 | `UNANSWERABLE_PREFIX = "负例"`（前缀判断） |

如果照调研文档实现硬编码的 `nohit` 判断，**真实评估集里的 7 条负例会被全部归入 positive** ——
因为它们的 `category` 是 `负例-域外` 而不是 `nohit`。

#### 🎯 根因

**设计文档凭空规定了字段名，没有先核对既有数据。**

这是"从零设计"思维的残留：调研阶段假设要新建评估集，于是自定义了格式；
后来发现已有评估集，但**没有回头校正已写下的字段约定**。

#### ✅ 解决方案

**统一到"前缀"约定，不硬编码类型清单**——这样任何语言、任何自定义分类都能表达负例：

```python
def classify_query(case) -> str:
    """按分级标注把查询归入 positive / partial / negative。"""
    if case.category.startswith(UNANSWERABLE_PREFIX):   # 复用 evaluation.py 的约定
        return "negative"
    grades = set(case.grades.values())
    if 2 in grades:
        return "positive"
    if 1 in grades:
        return "partial"
    return "negative"
```

**关键设计**：分类**优先看分级标注，而不是类型字段**。
`category` 只用于「负例判定」和「分组报告」，不承担「相关档位」的表达——
后者由 `grades` 决定。这样：

- 评估集里已有的、我没想到的 `category` 值（如 `对比`、`表格`）不会导致分类失败；
- 新增分类不需要改代码。

#### ✔️ 验证

`selftest_calibration.py` 覆盖：

```powershell
python eval\selftest_calibration.py
#   含 grade=2            → positive
#   仅 grade=1            → partial
#   全 0                  → negative
#   category="负例-荐股" 且含 grade=2 → negative（前缀优先）
#   grade=2 与 1 混合     → positive
```

第四、五条是**优先级断言**——确保"负例前缀"优先于"分级标注"。

#### 💡 学习要点

1. **设计格式前先看既有数据。** 凭空规定字段名，会在两个格式之间制造无谓的映射成本。
2. **用"约定前缀"而不是"枚举值清单"表达布尔性质。** `category.startswith("负例")` 能兼容
   任意细分的负例类型（`负例-域外` / `负例-语料外` / `负例-时效外`），而枚举清单每加一类就要改代码。
3. **让分类依据尽可能少地依赖"我设计的字段"。** 本例最终以 `grades` 为准，
   使新字段成为可选增强而不是必需项，兼容性大幅提升。

---
