# 04 · 待验证问题与验收方法

> 对应问题：**调研结论里哪些是「有依据的判断」、哪些是「必须实测才能定的」？怎么验收？**

前两篇给了设计方向，但有三类结论**现在无法确定，必须用本项目自己的语料和查询实测**：

1. 两个阈值（`no_answer_threshold`=0.05、`rerank_floor`=0.25）是经验值，未校准；
2. rerank 相对不 rerank 的真实增益，现有评估集测不出来；
3. 端到端延迟与触发准确率，从未在真实对话上测过。

---

## 1. 评估现状：已有 chunk 级框架，缺的是「分数采集」与「分级标注」

**先纠正调研初期的一个误判。** `../README.md` 与 `TECH_SUMMARY.md` 里写的是「评估为文档级，
对切分质量不敏感」——但**在线代码已经重写过**：`src/wealth_rag/evaluation.py` 现在是完整的
chunk 级框架，能力比文档描述的强得多：

| 已有能力 | 实现位置 |
| --- | --- |
| chunk 级匹配（标签锚定**标题路径**，重新切分后仍可用） | `evaluation.py::is_gold` |
| 可答 / 不可答查询分开报告，含**拒答准确率**与**误拒率** | `EvalReport.answerable` / `unanswerable` |
| `must_not` 负例断言（目录页、页眉不该出现） | `evaluation.py::is_forbidden` |
| Hit@k / Recall@k / Precision@k / MRR / nDCG@k / MAP@k | `EvalReport.summary` |
| **bootstrap 95% 置信区间** | `bootstrap_ci` |
| **配对 bootstrap 检验 + p 值**（消融归因） | `paired_bootstrap_pvalue` |
| 分级 nDCG（gain = 2^grade − 1） | `_graded_ndcg`（本次新增） |
| 父子块诊断（回查成功率、同父冗余度） | `parent_coverage` / `parent_redundancy` |
| 重排收益的**硬上界** Recall@W | `ceilling_recall` |

**所以本节的主张要收窄为：框架齐了，但缺两样东西——**

1. **rerank 分数没有被采集**。原实现只算命中排名，不记录 `rerank_score`，因此握有
   阈值参数却无法校准它们。**本次已补**：`QueryOutcome` 新增 `top_rerank_score` /
   `top_rank` / `max_relevant_rerank` / `max_irrelevant_rerank` / `max_grade2_rerank` /
   `max_grade1_rerank` / `margin`。
2. **没有分级标注（0/1/2）**。原格式只有「命中 / 不命中」，而阈值校准需要区分
   「直接回答问题」与「相关但不完整」——这正是 `no_answer_threshold`（拒答）与
   `rerank_floor`（信不信重排）之间那一档的来源。**本次已补**：`grades` 字段
   + 完整的三步标注流程工具。

因此 Q1（阈值校准）现在**有工具可以直接做**，不再是纯手工活。

---

## 2. 必须实测的问题清单

### Q1（P0）· rerank 分数分布与阈值校准

| 项 | 内容 |
| --- | --- |
| **问题** | `no_answer_threshold`=0.05 与 `rerank_floor`=0.25 在本项目语料上是否合理？ |
| **方法** | **复用已有评估集，不必从零标注**。三步：<br>① `python eval/build_eval_pool.py --queries eval/queries.jsonl --out eval/pool --pool-depth 10` 生成候选池（**关闭重排**取纯 RRF 排序，避免标注被既有排序限制；`system_gold` 由已有 `gold_headings` 映射而来）<br>② `python eval/apply_grades.py --pool eval/pool --labels eval/pool/labels.template.json --out eval/queries.graded.jsonl --seed-from-gold`：把 `gold_headings` 命中的候选派生为 grade=2、其余为 0，**立刻得到可用的 positive/negative 划分**<br>③ `python eval/tune_rerank_threshold.py --queries eval/queries.graded.jsonl --ab --json runs/calib.json` 出分布、推荐值与 rerank A/B<br>⚠️ **`--seed-from-gold` 区分不出 grade=1（相关但不完整）**，所以 `rerank_floor` 仍需要人工把「相关但单独不能回答问题」的候选改成 1。无法派生。<br>⚠️ 负例只有 7 条，先把 [`eval/queries.calibration.jsonl`](../backend/RAG_/eval/queries.calibration.jsonl) 里补的 8 条负例合并进来（凑到 15 条），否则 `negative < 8` 会告警且拒答率估计不稳 |
| **推荐规则** | `no_answer_threshold` = positive 顶分的 `1 − min_cover` 分位（默认 min_cover=0.95）；`rerank_floor` = partial 顶分的 p25，且强制高于前者（否则三档退化成两档） |
| **判据** | ① 相关块（grade≥1）的 p5 是否 ≥ 无关块（grade=0）的 p95？<br>—— 是 → 分数可分，单一阈值有效；<br>—— 否 → **分布重叠**，单一阈值不可能同时不误拒与不漏答，此时应优先「不编造」（阈值取保守值）并在提示词里强制声明不确定性 |
| **产出** | `--json` 落盘的分布表 + 推荐值 + 判定对比，供 git 追踪 |
| **现有线索** | ① RAG 模块 TECH_SUMMARY 实测相关块 0.79 / 0.89、无关块 0.003~0.008，区分度看着很好，**但那是 6 条样例查询**。<br>② **本项目已有一份 70 条的真实评估集**（`eval/queries.jsonl`：63 条可答 + 7 条负例），基线 `eval/runs/baseline.json` 记录的指标见下方「基线诊断」——那里已经能看出**当前 0.05 阈值明显偏低**。 |

### 2.1 基线诊断：现有数据已经说明 0.05 不够用

`eval/runs/baseline.json`（rerank 开启，top_k=10）记录了这份 70 条评估集上的实测结果：

| 指标 | 值 | 解读 |
| --- | --- | --- |
| Hit@10 | 1.000 | 饱和，**没有区分度**（k=10 = `fused_top` 的一半，且 63 条可答全都命中） |
| MRR@10 | 0.930 | 真正的信号；说明重排把正确答案排得很靠前 |
| nDCG@10 | 0.947 | 同上 |
| 误拒率（可答） | **1.59%**（1/63） | 当前 0.05 阈值下，只有 1 条有答案的查询被误判为「无证据」 |
| 拒答准确率（不可答） | **71.4%**（5/7） | 7 条负例里**有 2 条没被拒答** —— 即会硬答库里没有的内容 |

**这张表已经给出了一个可行动的结论：当前的 0.05 阈值偏向「多答、少拒」——误拒率很低（1.6%），
但漏拒率高达 28.6%。** 在财富规划领域这个偏向是反的（编造法规的代价 > 一次误拒），
所以阈值应当上调。上调的空间来自那 1.59%：允许误拒率从 1.6% 升到 5%（`min_cover=0.95`）
可以换回一部分漏拒率。

**但要诚实说明这个推断的边界**：
- 7 条负例太少（`< 8` 会触发告警），无法稳定估计漏拒率——**28.6% 这个数字本身置信区间很宽**；
- 只有 1 条误拒样本，「1.59%」实际上约等于「1 条」，同样不可靠；
- 基线是在**改动前**的代码上跑的，不含 rerank 分数采集，因此**看不到分数分布**，
  无法判断该上调到多少。

所以下一步不是"调参"，而是**补齐负例 + 重跑以采集分数**——这正是本节的 Q1。

### Q2（P0）· rerank 的真实增益（块级 A/B）

| 项 | 内容 |
| --- | --- |
| **问题** | rerank 到底带来多少块级排序提升？值不值那 1~3s 延迟？ |
| **方法** | 同一评估集，跑 `use_rerank=True` vs `use_rerank=False` 两组，对比 **Recall@k（k=1,3,5）**、**MRR**、**nDCG@10** |
| **判据** | MRR 相对提升 > 10% 才值得默认开启；若 < 5%，考虑默认关闭 rerank 或改用更小的 `fused_top` |
| **注意** | 不要只看均值。按查询类型分组报告（事实型 / 政策型 / 组合型），**很可能是「政策型增益大、组合型增益小」** |

### Q3（P0）· 端到端延迟

| 项 | 内容 |
| --- | --- |
| **问题** | 加 RAG 后用户感知的等待时间是多少？ |
| **方法** | 分环节打点：模型预热后的查询编码 / Qdrant 召回+RRF / rerank / 父块回查 / LLM 首 token。分别记录 **冷启动** 与 **热查询** 的 P50 / P95 |
| **重点** | `fused_top`(20) 是 rerank 延迟的直接乘数。测 `fused_top` ∈ {10, 20} 两档的延迟与 Q2 的指标，找平衡点 |
| **判据** | 热查询端到端 P95 < 8s（含 LLM）可接受；单次检索 > 3s 需要优化 |

### Q4（P1）· 触发准确率

| 项 | 内容 |
| --- | --- |
| **问题** | 模型该查的时候查了吗？不该查的时候查了吗？ |
| **方法** | 造 40 条对话样本：20 条**应该查知识库**（政策条款、金融概念、产品条款）、20 条**不应该查**（纯用户数据、纯计算、寒暄） |
| **指标** | `漏召率`（该查没查）、`误召率`（不该查却查了）、`平均检索次数/轮` |
| **判据** | 漏召率是**更严重的那个指标**（漏召 → 可能编造法规）。目标漏召率 < 10%；误召率可容忍到 20%（代价只是多花 1~3s） |

### Q5（P1）· 追问改写的有效性

| 项 | 内容 |
| --- | --- |
| **问题** | 追问（带指代）的检索质量是否崩塌？工具侧的「要求模型补全」够不够？ |
| **方法** | 构造 10 组多轮对话，第二轮全部用指代（「它」「这个」「那上面说的」）。对比：<br>① 直接用追问原句检索<br>② 人工补全后的查询检索<br>③ 让模型自己补全后检索（A1 方案） |
| **指标** | Recall@5（以补全后的人工结果为上界）、`query_used` 里是否出现了正确的实体名 |
| **判据** | 若 ③ 相对 ② 的 Recall@5 差距 > 20% → A1 不够，必须上 A2（自动改写兜底） |

### Q6（P1）· 拒答行为

| 项 | 内容 |
| --- | --- |
| **问题** | `evidence_quality='low'` 时，模型真的会拒答吗？还是会硬答/编造？ |
| **方法** | 20 条**库内必然没有答案**的问题（如「XX 基金的近三年收益率」），检查最终回答：<br>• 是否如实说「未找到依据」<br>• 是否出现了具体数字/条款（若出现且不在证据里 → **编造**） |
| **指标** | 拒答率、编造率 |
| **判据** | **编造率必须为 0**。这是财富规划领域的红线 —— 编造一条监管规定可能导致用户真实的资金损失 |

### Q7（P2）· 上下文组织方式的对比

| 项 | 内容 |
| --- | --- |
| **问题** | 父块扩展有没有用？去重有没有必要？`final_top` 取多少合适？ |
| **方法** | 消融：`include_parent` ∈ {true, false}、`dedupe_by_parent` ∈ {true, false}、`final_top` ∈ {3, 5, 8} |
| **指标** | 答案正确率（人工或 LLM-as-judge）+ 上下文 token 数 + 延迟 |
| **预期** | 父块扩展在「问题需要跨段综合」时有用，在「答案就在一个子块里」时是纯噪声（参考 CRAG 的知识精炼结论：直接给原文不如精炼后给） |

---

## 3. 评估集与标注流程（工具已落地）

### 3.0 先复用已有的 70 条评估集

`eval/queries.jsonl` **已经是** chunk 级评估集：70 条查询，每条带 `category`、
`gold_headings`（锚定标题路径）、部分带 `must_not`；其中 63 条可答、7 条以「负例」开头。
`eval/runs/` 下还有 `baseline.json` 与 `no_rerank.json` 两次历史运行，可作消融基线。

**所以不要新建评估集从零标注**，直接在这份上补：

```bash
# 1) 补足负例：把 queries.calibration.jsonl 的 8 条负例并入（7 → 15 条）
# 2) 生成候选池（关闭重排取纯 RRF 排序，system_gold 由 gold_headings 映射）
python eval/build_eval_pool.py --queries eval/queries.jsonl --out eval/pool --pool-depth 10

# 3) 用 gold 派生初值，立刻得到 positive / negative 划分（区分不出 grade=1）
python eval/apply_grades.py --pool eval/pool --labels eval/pool/labels.template.json \
    --out eval/queries.graded.jsonl --seed-from-gold

# 4) 出分布、阈值推荐、rerank A/B
python eval/tune_rerank_threshold.py --queries eval/queries.graded.jsonl --ab --json runs/calib.json
```

**`--seed-from-gold` 的能与不能**（重要）：

| 能做 | 不能做 |
| --- | --- |
| 把 `gold_headings` 命中的候选记为 grade=2、其余记为 0 | 区分「直接回答」与「相关但不完整」 |
| 由此得到可靠的 **positive / negative 划分** | 因此 **`rerank_floor` 仍需真实人工 grade=1 标注** |
| 足以校准 `no_answer_threshold`（它只看 positive vs negative 的顶分分离） | — |

### 3.1 两级文件：查询集 → 候选池 → 分级标签 → 分级查询集

```
eval/queries.calibration.jsonl      手写：查询 + category（+ 可选 must_not / gold_headings）
        │  python eval/build_eval_pool.py
        ▼
eval/pool/pool.jsonl                生成：每条查询的候选块全文（关闭重排，纯 RRF 排序）
eval/pool/labels.template.json      生成：待填的 0/1/2 模板（含 reviewed 列表）
        │  人工标注 → labels.json
        │  python eval/apply_grades.py
        ▼
eval/queries.graded.jsonl           生成：带 grades + gold_headings，可直接喂给评估器
        │  python eval/tune_rerank_threshold.py
        ▼
分数分布 + 阈值推荐 + 判定对比
```

**为什么拆成两级文件**：候选池（`pool.jsonl`，含正文）供人阅读；标签（`labels.json`，只含
分数）供机器消费。两者分离后，重新生成候选池不会冲掉已完成的标注，标注也可以独立评审。

### 3.2 查询集字段

```json
{"id":"c001","query":"什么是资产配置？为什么要做资产配置？","category":"concept","note":"doc 01 应有直接定义"}
{"id":"负例-001","query":"请推荐一只明年涨幅最大的股票。","category":"负例-荐股","note":"知识库不含个股推荐"}
```

| 字段 | 说明 |
| --- | --- |
| `id` | **稳定 ID**，标签文件按它索引。改查询文案可以，但不要改 ID |
| `category` | 自由文本，评估报告按它分组。**以「负例」开头 → 该查询被判为不可答**（`UNANSWERABLE_PREFIX`），这是负例的唯一约定 |
| `query` | 检索用的查询串。追问型查询请把指代补全（检索侧看不到 history） |
| `must_not` | 可选。断言「这类块不该出现」（如目录页、页眉），命中即计入违反率 |
| `gold_headings` | 可选。已有的标题级 gold 标注；`build_eval_pool` 会把它映射成模板里的 `system_gold` 供参考，**但标注者可以推翻** |

> ⚠️ **追问型（followup）查询目前无法直接测**：`evaluation.py` 的查询集不读 `history`，
> 检索也只会收到单条 `query`。要测追问改写（Q5），必须先把 `history` 经改写后拼成完整
> 查询再写入 `query` 字段——或者按 §2 Q5 的方法单独跑三组对照实验。这是当前的已知空白。

### 3.3 标注口径

| 分值 | 含义 |
| --- | --- |
| **2** | 直接回答问题——答案所需信息**完整**出现在该块内 |
| **1** | 相关但不完整——同主题、提供背景，但单独不能回答问题 |
| **0** | 无关 |

关键要求：**必须把看过的候选填进 `reviewed` 列表**。因为「未标注（默认 0）」与
「标注为 0（确认无关）」在文件里长得一样；不区分的话，一个「只标了 3 条、其余全是默认 0」
的标签文件会被当成高精度评估集，得出完全错误的阈值。`apply_grades.py` 会据 `reviewed`
计算真实覆盖率，低于 30% 直接报错，低于 60% 告警。

### 3.4 指标实现：不要重写评估器

`src/wealth_rag/evaluation.py` 已经实现了 chunk 级指标 + bootstrap 置信区间 +
配对 bootstrap 检验。`apply_grades.py` 的工作只是把分级标注**翻译**成该评估器认识的形式
（`grades` + 由它派生的 `gold_headings`），指标口径因此只有一处实现。

已新增的分级能力：给定 `grades` 时 nDCG 改用**分级增益** `gain = 2^grade − 1`
（grade 2 得 3 分、grade 1 得 1 分）。二值 nDCG 会把「直接回答」和「相关但不完整」
当成一样好——而那正是 rerank 要拉开的差距，用二值 nDCG 衡量 rerank 等于自废武功。

### 3.5 阈值推荐规则

```bash
python eval/tune_rerank_threshold.py --queries eval/queries.graded.jsonl --ab --json runs/calib.json
```

工具给出的规则（不是"取中位数"这种拍脑袋）：

| 参数 | 规则 | 理由 |
| --- | --- | --- |
| `no_answer_threshold` | positive 顶分的 `1 − min_cover` 分位（默认 min_cover=0.95） | 语义直接：允许至多 5% 的有答案查询被误拒 |
| 同上（保守备选） | 覆盖率约束内的**最大**阈值 | 分布不可分时偏向拒答——财富规划领域编造法规的代价高于一次误拒 |
| `rerank_floor` | partial 顶分的 p25，且强制 ≥ `no_answer_threshold` + 0.05 | 保证 `low < floor`，否则三档判定退化成两档，`_fuse` 的中间档永远走不到 |

**为什么不用「最大 F1」当决策规则**：扫阈最大化「positive 保留率 × negative 拒答率」的
调和平均，其中 `positive 保留率` 只统计「顶分 ≥ 阈值」，**不检查顶分是否真的来自相关块**。
一个库里没答案、却因噪声块拿到高分的 positive 查询同样被计成"保留成功"，于是 F1 被噪声
抬高、选出偏低阈值（偏向硬答）。因此 F1 只作报告项打印出来供参考。

**必须输出并归档**：三类（positive / partial / negative）与四组块级（grade=2 / grade=1 /
grade≥1 / grade=0）的分数分布，加上选定阈值与在该阈值下三类的判对率。

---

## 4. 统计功效提醒（避免过度解读）

样本量 40~60 条时：

| 结论类型 | 是否可信 |
| --- | --- |
| 「rerank 让 MRR 从 0.45 提升到 0.72」 | ✅ 可信（差异很大） |
| 「rerank 让 MRR 提升了 2%」 | ❌ 不可信，落在噪声范围内 |
| 「阈值 0.05 与 0.08 哪个更好」 | ❌ 需要几百条标注才能区分 |
| 「某类查询（政策型）增益明显大于另一类」 | ⚠️ 需谨慎，每类只有 8~12 条时分组结论很脆弱 |

**务实做法**：只追求**粗粒度结论**（「rerank 值不值」「阈值该在 0.2 还是 0.05 量级」「追问改写是否必要」），不要追求精细调参。精细调参的收益远小于「拒答不编造」这类行为层面的收益。

---

## 5. 验收清单（Checklist）

设计与实现完成后，逐项打勾：

### 功能

- [ ] `search_knowledge` 工具能被模型正确调用（`tool_call` 事件出现）
- [ ] 工具返回值是合法 JSON，无 UUID / 绝对路径 / 重复文本 / 内部分数
- [ ] 证据块带 `[1]` `[2]` 编号，回答中的引用能对上（**引用可回溯是硬要求**）
- [ ] `evidence_quality='low'` 时回答为拒答话术，**编造率 = 0**
- [ ] 追问场景下 `query_used` 里出现了正确的实体名（指代被补全）
- [ ] 涉及用户数值的问题，`query_used` 里含具体数值（如 `年收入 30 万`），不是「我的收入」

### 性能

- [ ] 服务启动预热成功，首个 RAG 请求无 15s 冷启动
- [ ] 热查询检索 P95 < 3s（GPU）
- [ ] `uvicorn` 以 `--workers 1` 运行，无 Qdrant 锁冲突
- [ ] 并发 3 个请求时无 OOM、无交叉污染（线程锁生效）

### 健壮性

- [ ] 知识库目录缺失 / 模型未下载时，服务仍能启动，RAG 工具返回明确 error
- [ ] RAG 不可用时，Agent 能降级回答（不进入死循环）
- [ ] 知识库文档里的「指令型文本」不会被执行（注入防御）
- [ ] 空知识库检索不抛异常，返回 `evidence_quality='low'`

### 评估（有数据支撑）

- [ ] 评估集 ≥ 40 条，含 positive / partial / negative 三类，**negative 与 positive 各 ≥ 8 条**
- [ ] 候选池用 `build_eval_pool.py` 生成（关闭重排取纯 RRF 排序，避免确认偏误）
- [ ] 标注覆盖率 ≥ 60%（`apply_grades.py` 会校验并打印），并把看过的候选列入 `reviewed`
- [ ] 已归档四组块级分数分布（grade=2 / grade=1 / grade≥1 / grade=0）与三类顶分分布
- [ ] 已判定「相关块 p5 ≥ 无关块 p95」是否成立；不成立时已按"偏向不编造"选保守阈值
- [ ] `no_answer_threshold` / `rerank_floor` 由 `tune_rerank_threshold.py` 选定，
      `--json` 结果已落盘并在 `config.yaml` 注释里写明依据与日期
- [ ] 已跑 `--ab` 记录 rerank 开/关的块级 MRR / nDCG@10 对比（含配对 bootstrap p 值）
- [ ] 已记录 `fused_top` ∈ {10, 20} 的延迟与指标对比
- [ ] 已记录触发准确率（漏召率 / 误召率）
- [ ] 已知空白：追问型（followup）尚未纳入自动评估——查询集不读 `history`，
      需按 Q5 单独做三组对照实验，或先把改写后的完整查询写入 `query` 字段

---

## 6. 尚存的设计分歧（需要在实现前拍板）

| # | 问题 | 选项 | 倾向 |
| --- | --- | --- | --- |
| 1 | 排序是否保留加权融合 | ① 纯 rerank ② 加权融合（调权重） | ①（见 [02 §1](RAG触发策略和rerank/02-rerank-output.md#1-最终排序应该以-rerank-分为准不是加权混合)）；配置项保留以便回退 |
| 2 | `low_confidence` 是「拒答」还是「谨慎作答」 | ① 强制拒答 ② 作答但声明不确定 | ①（财富规划领域的编造代价不对称）；但要先用 Q1 确认阈值可靠，否则会导致大面积误拒 |
| 3 | `final_top` 默认值 | 3 / 5 / 8 | 5（在「少而精」与「覆盖多源」之间） |
| 4 | 父块是否默认给 | ① 默认 false ② 默认 true | ①；模型需要时再要 |
| 5 | 追问改写放工具侧还是 Agent 侧 | ① Agent 侧（工具描述引导） ② 工具侧（自动改写） | ① 首版，② 作为 Q5 验证后的兜底 |
| 6 | 是否需要 `doc_type` 过滤 | ① 首版不做 ② 摄入时打标 + 过滤 | ①（需要重新摄入，收益不确定） |
