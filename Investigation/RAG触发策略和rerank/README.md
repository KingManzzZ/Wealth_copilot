# RAG 融合调研：触发策略与 rerank 输出

> 调研对象：`backend/RAG_/`（wealth-rag 检索模块）融入 `../../backend`（WealthPilot 个人财富规划 Agent）
> 调研问题：① **什么时候触发 RAG**；② **rerank 结果怎么输出**给上层编排
> 调研日期：2026-10-15
> 状态：设计调研（只调研与选型，不含实现）

---

## 文档索引

| 文档 | 回答的问题 | 关键结论 |
| --- | --- | --- |
| [01-when-to-trigger.md](01-when-to-trigger.md) | 什么时候触发检索？谁来决策？失败怎么办？ | **首版让 LLM 显式调用 `search_knowledge` 工具**（顺应当前 ReAct 架构）；配「否定触发器 + 追问改写」两个硬缺口；v1.5 再加规则/分类器兜底 |
| [02-rerank-output.md](02-rerank-output.md) | rerank 之后怎么排序、怎么截断、怎么组织成给 LLM 的证据？ | 最终排序**以 rerank 分为准**（RRF 降级为元数据）；`low_confidence` 只作信号不作闸门；去重必须先于截断；证据块带 `[En]` 编号 + citation |
| [03-integration-notes.md](03-integration-notes.md) | 落到本项目要改哪些文件、有哪些工程坑？ | 单例 Retriever + 启动预热；工具返回值必须瘦身（否则 token 爆炸）；同步阻塞调用不要放进 async 事件循环；补 `month`/`category` 上下文注入 |
| [04-open-questions-and-eval.md](./04-open-questions-and-eval.md) | 哪些结论需要实测校准？怎么验收？ | **校准工具链已落地**（候选池 → 人工标注 → 分数分布 → 阈值推荐 + rerank A/B）；必须实测：两个阈值、rerank 增益、端到端延迟、触发准确率 |

### 本次同步落地的代码产出

| 文件 | 作用 |
| --- | --- |
| [`src/wealth_rag/calibration.py`](../backend/RAG_/src/wealth_rag/calibration.py) | 阈值校准实现（分布统计 + 推荐规则 + 判定对比 + rerank A/B） |
| [`eval/build_eval_pool.py`](../backend/RAG_/eval/build_eval_pool.py) | 生成候选池与人工标注模板（关闭重排取候选，避免确认偏误） |
| [`eval/apply_grades.py`](../backend/RAG_/eval/apply_grades.py) | 合并 0/1/2 标注 → 分级查询集；**校验标注覆盖率**；支持 `--seed-from-gold` 由已有 gold 派生初值 |
| [`eval/tune_rerank_threshold.py`](../backend/RAG_/eval/tune_rerank_threshold.py) | 校准命令行入口（薄封装） |
| [`eval/selftest_calibration.py`](../backend/RAG_/eval/selftest_calibration.py) | 校准逻辑自检（**不加载模型**，可进 CI） |
| [`eval/queries.calibration.jsonl`](../backend/RAG_/eval/queries.calibration.jsonl) | 补充负例（7 → 15 条）+ 完整用法说明 |
| `rag calibrate` | CLI 入口（等价于上面的脚本） |
| [`src/wealth_rag/evaluation.py`](../backend/RAG_/src/wealth_rag/evaluation.py) | 新增 rerank 分数采集 + `grades` 分级标注 + 分级 nDCG |

### 一个重要发现：评估集比预想的完整得多

`eval/` 目录下**已经有一份 70 条的 chunk 级评估集**（`queries.jsonl`，63 条可答 + 7 条负例）
和两次历史运行结果（`runs/baseline.json` / `runs/no_rerank.json`）。这意味着：

- **不需要从零标注** —— 用 `--seed-from-gold` 就能由已有 `gold_headings` 派生出
  positive / negative 划分，直接校准 `no_answer_threshold`；
- 只有 `rerank_floor` 依赖的 grade=1（相关但不完整）无法派生，需要人工补；
- 基线数据（误拒率 1.59%、拒答准确率 71.4%）**已经显示当前 0.05 阈值偏向「多答少拒」**，
  与财富规划领域应偏保守的要求相反。详见 [04 §2.1](./04-open-questions-and-eval.md)。

---

## 一句话结论

**触发**：不要在 RAG 模块内部做「要不要检索」的决策，把决策权留在 Agent 循环里作为一次显式工具调用；但必须补两个 RAG 模块自己解决不了的硬缺口——**追问改写**（多轮指代）和**确定性兜底触发**（关键词/合规类问题强制检索），否则「模型自己决定」在真实对话里会漏召。

**输出**：`Retriever.search()` 现在返回的 `list[SearchResult]` **不适合直接 JSON 序列化丢给 LLM**（含 `text` 面包屑重复、`parent_text` 体积不可控、分数语义混乱）。需要一个薄适配层，把它转成「带编号的紧凑证据块 + 明确的引用格式 + 拒答信号」，并控制上下文预算。

---

## 现状摘要（调研基线）

### 宿主项目 `backend/`（WealthPilot）

- **架构**：FastAPI + OpenAI SDK（DeepSeek 兼容端点），`backend/core/agent/agent.py` 是一个手写 ReAct 循环，`MAX_ROUNDS=5`。
- **工具层**：`tools_schema.py` 声明 OpenAI function-calling 契约（6 组、约 24 个工具）；`tools_route.py` 自动发现 `tools_hub/*.py` 并注册。**新增 RAG 工具只需加一个 `tools_hub/rag_tools.py` + 一段 schema，路由层零改动。**
- **当前无任何检索能力**：全部 24 个工具都是「用户自身财务数据 CRUD + 计算」，没有任何外部知识来源。
- **流式协议**：SSE，事件类型 `round` / `thinking` / `answer` / `tool_call` / `tool_result` / `error` / `done`（`backend/api/routes/chat.py`）。

### RAG 模块 `backend/RAG_/`（wealth-rag）

- **在线链路**：`Retriever.search(query, top_k, use_rerank, doc_id)`
  → Qwen3-Embedding 编码查询 → Qdrant `prefetch` 双路召回（dense 30 + sparse 30）→ RRF 融合 top-20
  → Qwen3-Reranker-0.6B 逐条 yes/no logit 打分 → 融合排序 → 按 parent 去重 → 截断 top-8（`final_top`）
  → 按 `parent_id` 回查父块全文。
- **已内建的信号**：`SearchResult.low_confidence`（最高重排分 < `no_answer_threshold`=0.05 时为真）、`citation` 属性、`parent_text` 上下文扩展。
- **接口形态**：Python 库（可直接 import）+ Typer CLI，无 HTTP 服务。**与宿主同进程集成的路径是通的。**
- **评估能力**：`evaluation.py` 已是 **chunk 级**框架，含 bootstrap 置信区间、配对 bootstrap 检验、
  拒答准确率 / 误拒率、父子块诊断，以及分级 nDCG（`gain = 2^grade − 1`）。
  ⚠️ 模块自身的 `README.md` / `TECH_SUMMARY.md` 仍写「评估为文档级」，**已过时**——以代码为准。

---

## 调研方法

- 一手来源：Self-RAG、Adaptive-RAG、CRAG、Lost-in-the-Middle 等论文原文/摘要页；
- 工程实践来源：RAG 工程课程中关于 query rewriting / routing 的章节、知识库产品的阈值配置讨论；
- 代码核对：`backend/RAG_/src/wealth_rag/` 全部在线链路代码 + `backend/core/` 全部 Agent/工具代码，逐行确认；
- **工具落地**：为「触发与阈值」类结论补上可执行的实测工具链（见上方代码产出表），
  因为这类结论靠读代码和读论文都定不下来。

> ⚠️ 所有引用来源均在正文中以链接标注。**外部来源仅作参考，本项目结论以自身代码与实测为准**——
> 尤其两个阈值（0.05 / 0.25）目前仍是开发期的经验取值，**尚未用本项目语料校准**。
> 校准工具已就绪但**还没跑过**（需要人工标注），因此本文所有涉及阈值的判断都属待验证，
> 见 [04](./04-open-questions-and-eval.md)。

---

## 术语约定

| 术语 | 含义 |
| --- | --- |
| **触发（trigger）** | 决定「这一轮要不要去知识库检索」的动作 |
| **召回（recall）** | 双路检索拿到候选集（dense 30 + sparse 30） |
| **精排（rerank）** | 用 cross-encoder 式模型对候选逐条打相关性分并重排 |
| **证据块（evidence chunk）** | rerank 后真正进入 LLM 上下文的那几段文本 |
| **拒答（abstention）** | 证据不足时明确说「没找到可靠依据」而不是硬答 |
| **改写（rewrite）** | 把带指代/省略的追问补全成可独立检索的查询 |
