# RAG 检索与重排 · 问题与解决方案

> **模块定位**：从向量库召回候选、融合排序、精排、去重、截断。覆盖
> `backend/RAG_/src/wealth_rag/retrieval/`（hybrid / rerank）、`store/qdrant_store.py`、
> `sparse/bm25.py` 与相关 `config.yaml` 参数。
>
> **本文件是拆分自 [`问题与解决方案.md`](./问题与解决方案.md) 的模块文档**，
> 索引与跨模块经验见 [`README.md`](./README.md)。
>
> **这一模块的核心矛盾**：召回要"宽"（不能漏），精排要"准"（不能错），
> 而两者都用同一个 `score` 字段表达，于是产生了一系列"分数语义混乱"的问题。

## 问题索引

| 编号 | 问题 | 严重度 | 状态 | 子模块 |
| :--- | :--- | :--- | :--- | :--- |
| P1-3 | 同一标题重复占满 top-k 名额 | 🟡 中 | ✅ 已修复 | 去重 |
| P1-4 | `rerank_floor` 掩盖「无相关结果」 | 🟡 中 | ✅ 已修复 | 重排 |
| P2-10 | RRF 分按本次结果集最大值归一化，导致分数不可比 | 🔵 低 | 💡 建议未实施（应改为纯 rerank 定序，需评估集验证） | 排序 |
| P2-11 | 最终排序丢弃了原始 RRF 分 | 🔵 低 | 💡 建议未实施（应拆分 `score` 与 `final_score` 字段） | 排序 |
| P2-12 | 去重键粒度不一致 | 🔵 低 | 💡 建议未实施（去重键应统一到「节」级） | 去重 |
| P2-17 | Qdrant 本地库文件锁与多 worker / reload 冲突 | 🔵 低 | ⚠️ 待部署约束（须 `--workers 1` 且生产不带 `--reload`） | 存储 |
| P2-18 | `Retriever.search` 一次只能服务一个查询 | 🔵 低 | 📌 已知限制（接口不支持分路查询，本阶段不做） | 检索 |

---

### P1-3 · 同一标题重复占满 top-k 名额

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 系统审查（真实检索实测，按 `heading_path` 聚合命中分布） |
| **代码引入** | 2026-10-10（初始提交） |
| **严重度** | 🟡 中 |
| **状态** | ✅ 已修复 |
| **所属环节** | 检索 |
| **代码位置** | `src/wealth_rag/retrieval/hybrid.py:63` |

#### 🔍 现象

一条查询返回的多个结果来自同一个标题，挤掉了其它章节的内容。

#### 🧪 证据

- 「存款保险制度的限额是多少」top-8 中，**5 个块仅来自 2 个标题**（`金融知识普及读本 (第二版)` ×3、`存款保险条例` ×2）。
- 「黄金期货交易的特点」top-8 中，2 个来自 `(一) 上海黄金交易所黄金交易`。

#### 🎯 根因

**相关性（relevance）与多样性（diversity）是两个独立目标**，但当前排序只优化了前者。

一个"节"被切成多个子块后，这些子块在向量空间中彼此非常接近——它们会**成簇地**同时进入 top-k。如果只按分数截断，语义相近的块就会互相挤占名额，导致最终交给 LLM 的证据**覆盖面过窄**。

#### ✅ 解决方案

在 `hybrid.py:63` 返回前加**同父/同标题去重**。注意要保留足够的候选池——`fused_top` 是 20，`final_top` 是 8，去重后仍能填满：

```python
# hybrid.py
@staticmethod
def _dedupe_by_parent(results: list[SearchResult], final_k: int) -> list[SearchResult]:
    """按 parent_id（无则按 heading_path）去重，保证召回多样性。"""
    seen: set[str] = set()
    out: list[SearchResult] = []
    for r in results:
        key = r.parent_id or " > ".join(r.heading_path) or r.chunk_id
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= final_k:
            break
    return out
```

接入：

```python
if use_rerank and self.reranker is not None and results:
    ...
    self._fuse(results)

return self._dedupe_by_parent(results, final_k)
```

> **可选增强**：若希望"允许同节多块但限制上限"，可改为每个 `key` 最多保留 2 个；或引入 MMR（最大边际相关性）做冗余惩罚。建议先用简单去重，用评估集看效果再决定。

#### ✔️ 验证

`docs/audit/search_probe.py` 会打印 `[!] 同一 heading_path 重复命中`——改动后该提示应消失或显著减少。

#### 💡 学习要点

1. **top-k 截断只保证"最相关"，不保证"覆盖广"。** 需要多样性时，必须有显式的去重或惩罚机制。
2. **父子块结构天然导致"成簇召回"。** 同一节的子块语义高度接近，这是设计使然，不是 bug——但排序阶段要对此有意识。
3. **去重要留足候选池。** 去重前必须已经从更大集合（`fused_top=20`）里取到了足够候选，否则去重后会凑不满 `final_top`。

---

### P1-4 · `rerank_floor` 掩盖「无相关结果」

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 系统审查（真实检索实测，观察重排分分布） |
| **代码引入** | 2026-10-14（提交 `55c302c`），已存在约 1 天 |
| **严重度** | 🟡 中 |
| **状态** | ✅ 已修复 |
| **所属环节** | 检索 / 融合 |
| **代码位置** | `src/wealth_rag/retrieval/hybrid.py:74-79` |

#### 🔍 现象

知识库里根本没有答案时，系统仍然返回 8 条结果；且**丢弃了重排器给出的正确判断**。

#### 🧪 证据

查询「什么是再贴现」的实测结果：

```
[1] score=0.8333 rerank=0.0005   ← 重排分几乎为 0，即"无关"
[2] score=0.7500 rerank=0.0066
```

最高重排分仅 **0.0066**，远低于 `rerank_floor: 0.25`。按 `hybrid.py:74-79`，此时进入"保底"分支，丢弃重排结果、回退 RRF 排序：

```python
max_rerank = max((x.rerank_score or 0.0) for x in results)
if max_rerank >= r.rerank_floor:
    ...  # 加权融合
# else: 什么都不做，保留 RRF 排序
```

重排器其实**答对了**——库里确实没有「再贴现」的定义，返回的 `贴现` / `办理程序` 是近邻但不是答案。但系统把这个明确的否定信号当作噪声扔掉了。

#### 🎯 根因

**降级/保底逻辑把"明确的否定"变成了"沉默的肯定"。**

原设计的意图是好的：泛查询下重排分普遍很低，不应让重排全权推翻召回结果。但它把两种情况混为一谈：

| 情况 | 重排分特征 | 正确处置 |
| :--- | :--- | :--- |
| 泛查询（问题太宽泛） | 普遍偏低但不为 0 | 保留 RRF 保底 ✅ |
| **无答案**（库里确实没有） | **趋近于 0** | **明确报告"无相关结果"** |

用一个阈值区分"低"和"极低"，才能把这两种情况分开。

#### ✅ 解决方案

区分三种情况，而不是两分：

```python
# config.yaml
retrieval:
  rerank_weight: 0.6
  rerank_floor: 0.25          # ≥ 此值：重排可信，加权融合
  no_answer_threshold: 0.05   # < 此值：判定"库中无相关结果"，标记低置信
```

```python
# hybrid.py:_fuse
max_rerank = max((x.rerank_score or 0.0) for x in results)
if max_rerank >= r.rerank_floor:
    w = r.rerank_weight
    for x in results:
        x.score = (1 - w) * (x.score / max_rrf) + w * (x.rerank_score or 0.0)
elif max_rerank < r.no_answer_threshold:
    # 重排器明确判定"全都不相关" → 标记低置信，交由上层决定是否作答
    for x in results:
        x.low_confidence = True
results.sort(key=lambda x: x.score, reverse=True)
```

`SearchResult` 增加一个字段：

```python
# models.py
low_confidence: bool = False   # 重排分低于阈值，库中可能无相关证据
```

上层编排据此决定"没有找到可靠证据"，而不是拿低质片段强行生成答案。

> **设计原则**：**拒答能力是 RAG 系统质量的一部分。** 拿不相关的片段强行生成答案，比明确说"没有找到"危害更大。

#### ✔️ 验证

对一条知识库中确实不存在答案的查询，返回结果应带 `low_confidence=True`。

#### 💡 学习要点

1. **降级逻辑要保留上游的否定信号。** "回退到上一级排序"和"上游说没有"是两件不同的事，不能都走同一个 `else`。
2. **单个阈值只能二分，两种情况需要两个阈值。** 遇到"这个词有时该触发、有时不该"的情况，先想想是不是把两种语义混在了一起。
3. **RAG 系统需要"拒答"通道。** 没有拒答能力的检索系统，会把幻觉的责任推给生成阶段。

---

### P2-10 · RRF 分按本次结果集最大值归一化，导致分数不可比

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 审阅 `Retriever._fuse()` 的融合公式 |
| **严重度** | 🔵 低（对排序影响有限，但对"设阈值"和"跨轮比较"影响大） |
| **状态** | 💡 建议未实施（应改为纯 rerank 定序，需评估集验证） |
| **所属环节** | 检索 |

#### 🔍 现象

`_fuse()` 在"重排可信"档位做加权融合：

```python
max_rrf = max((x.score for x in results), default=0.0) or 1.0
max_rerank = max((x.rerank_score or 0.0) for x in results)
if max_rerank >= r.rerank_floor:
    w = r.rerank_weight          # 0.6
    for x in results:
        x.score = (1 - w) * (x.score / max_rrf) + w * (x.rerank_score or 0.0)
```

`max_rrf` 是**本次结果集里 RRF 分的最大值**。

#### 🧪 证据

归一化基准依赖于"本次候选集恰好包含哪些块"：

| 场景 | max_rrf | 同一个 (query, doc) 对的归一化分 |
| :--- | :--- | :--- |
| 候选集里有一个 RRF 极高的块 | 0.032 | `score / 0.032` → 偏小 |
| 候选集分布均匀 | 0.020 | `score / 0.020` → 偏大 |

**同一个 (query, doc) 对在两次调用里可能得到不同的最终分。**

而本文档 P1-4 引入的 `no_answer_threshold` 判定用的虽是 `rerank_score`（不受此影响），
但**融合后的 `score` 被当作对外排序分**——如果未来有人想基于 `score` 设阈值或做跨轮比较，
就会踩到这个不稳定性。

#### 🎯 根因

**用"本批次内的相对量"做归一化，使结果依赖于批次的组成。**

`max_rrf` 是批内统计量，不是全局常量。用它做分母，分数就失去了跨批次可比性。
（这是 min-max 归一化的固有缺陷，在推荐/检索里很常见。）

#### ✅ 解决方案

**建议改为纯 rerank 定序**，让 RRF 只承担"选谁进重排"的职责：

```python
# RRF 的职责 = 召回并粗排（决定 fused_top 进谁）
# rerank 的职责 = 精排（决定最终顺序）
# 两级排序的分工本来就不该通过加权求和混在一起
results.sort(key=lambda x: x.rerank_score or 0.0, reverse=True)
```

理由：加权融合的动机是"怕 rerank 不稳"，但**这个需求已由 `rerank_floor` 三档判定覆盖**
（rerank 不可信时整档退回 RRF）。两套机制做同一件事只会互相干扰。

**配置项保留**（`rerank_weight` 默认改为 `1.0`），这样想回退成融合只需改配置。

#### ✔️ 验证

```powershell
# 同一查询连跑两次，比较同一 chunk 的 score 是否稳定
python -c "
from wealth_rag.config import load_settings
from wealth_rag.retrieval.hybrid import Retriever
r = Retriever(load_settings('config.yaml'))
a = {x.chunk_id: x.score for x in r.search('什么是资产配置', top_k=5)}
b = {x.chunk_id: x.score for x in r.search('什么是资产配置', top_k=5)}
print(all(abs(a[k]-b[k]) < 1e-9 for k in a))
"
# 期望：True（若为 False，说明确实受批次组成影响）
```

#### 💡 学习要点

1. **批内归一化的量不可跨批次比较。** 想设阈值或跨轮对比，就必须用绝对量或固定基准。
2. **两套机制做同一件事会互相干扰。** `rerank_floor` 已处理"rerank 不可信"，
   加权融合再处理一遍只是引入了一个需要调参的额外自由度。
3. **职责分离比调参更可靠。** "RRF 负责召回、rerank 负责排序"不需要任何权重参数。

---

### P2-11 · 最终排序丢弃了原始 RRF 分

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 审阅 `_fuse()` 发现它**就地覆盖** `x.score` |
| **严重度** | 🔵 低（可观测性损失） |
| **状态** | 💡 建议未实施（应拆分 `score` 与 `final_score` 字段） |
| **所属环节** | 检索 |

#### 🔍 现象

```python
for x in results:
    x.score = (1 - w) * (x.score / max_rrf) + w * (x.rerank_score or 0.0)
```

`x.score` 原本是**召回阶段的 RRF 融合分**，被**覆盖**为最终融合分。
原始 RRF 分**永久丢失**。

#### 🧪 证据

`SearchResult` 只有一个 `score` 字段，却承担了两个语义：

| 语义 | 用途 | 是否保留 |
| :--- | :--- | :--- |
| 召回阶段分（RRF） | 诊断"这条是靠关键词命中还是靠语义命中" | ❌ 被覆盖 |
| 最终排序分 | 决定返回顺序 | ✅ |

后果：**无法事后复盘**"某条结果是因为 RRF 高还是因为 rerank 高而被选中的"。
在排查"为什么这个不相关的块排第二"时，这个信息是关键。

#### 🎯 根因

**一个字段承担两种职责**（与 P0-3「标题未进入证据文本」是同一类问题的不同表现）。

P0-3 是 `text` 与 `raw_text` 职责混用；本条是 `score` 的两种语义混用。
**都源于"没有在字段设计阶段明确每个字段服务哪个下游用途"。**

#### ✅ 解决方案

**建议拆分字段**：

```python
@dataclass
class SearchResult:
    score: float                    # 保留：召回阶段原始分（RRF）
    rerank_score: float | None      # 重排原始分
    final_score: float | None = None  # 新增：最终排序分
    evidence_quality: str = "high"  # 新增：high | medium | low
```

拆分后：
- `score` 归召回、`final_score` 归排序，语义不再重叠；
- `low_confidence` 布尔量可退化成 `evidence_quality == "low"` 的兼容别名（详见 P1-4 的后续演进）。

#### ✔️ 验证

```powershell
python -c "
from wealth_rag.config import load_settings
from wealth_rag.retrieval.hybrid import Retriever
r = Retriever(load_settings('config.yaml'))
for x in r.search('什么是再平衡策略', top_k=3):
    print(f'rrf={x.score:.4f} rerank={x.rerank_score}')
"
# 期望：两个分数都能看到且语义不同（改动前 x.score 已是融合值）
```

#### 💡 学习要点

1. **就地覆盖会销毁诊断信息。** 派生值应该新增字段，而不是覆盖原字段。
2. **字段语义重叠是"信息丢失"的常见来源**（与 P0-3 同源）。设计数据模型时要问：
   "这个字段会被几个下游以不同方式使用？"
3. **可观测性损失不会立刻显现**，但会在排查问题时集中爆发。改动成本极低（加两个字段），
   收益是未来所有排序问题的可诊断性。

---

### P2-12 · 去重键粒度不一致

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 审阅 `_dedupe_by_parent` 的去重键构造 |
| **严重度** | 🔵 低 |
| **状态** | 💡 建议未实施（去重键应统一到「节」级） |
| **所属环节** | 检索 |

#### 🔍 现象

```python
key = res.parent_id or " > ".join(res.heading_path) or res.chunk_id
```

三级回退，但**三级的粒度不同**：

| 回退级 | 粒度 | 效果 |
| :--- | :--- | :--- |
| `parent_id` | **节**级 | 同一节的多个子块被去重 ✅ |
| `heading_path` | **节**级 | 同上 ✅ |
| `chunk_id` | **块**级 | 同一节的多个子块**都会被保留** ❌ |

#### 🧪 证据

当某块**既没有 `parent_id` 也没有 `heading_path`** 时（例如文档级引言块），
去重粒度悄悄从"节"退化为"块"——`chunk_id` 每个都不同，去重**完全失效**。

这会静默降低召回多样性：本来应该"8 条来自 8 个不同节"，
可能变成"同一节的 3 个子块各占一个名额"。

#### 🎯 根因

**回退链的各级应当保持同一语义，但这里混入了不同粒度的键。**

`or` 链式回退很简洁，但**它假定各级是等价的**。当各级语义不同时，
回退会静默改变行为——这与 P1-4「降级逻辑要保留上游的否定信号」是同类问题的不同表现。

#### ✅ 解决方案

**统一到"节"级**，第三级改为与节相关的稳定键：

```python
def _dedupe_key(res) -> str:
    """去重键：统一到「节」粒度。

    第三级不能退回 chunk_id——那是块粒度，会让同一节的多个子块全部保留，
    去重静默失效。改用 doc + 页码作为"节"的近似。
    """
    if res.parent_id:
        return res.parent_id
    if res.heading_path:
        return " > ".join(res.heading_path)
    return f"{res.doc_id}#p{res.page or 0}"
```

同时**报告去重统计**，让"去重是否生效"可见：

```python
# 返回结果里附带
{"candidates_considered": 20, "dedup_dropped": 5, "returned": 8}
```

这样上层能区分「只有 3 条相关」与「本来有 8 条但被去重砍掉了」——
**这是当前完全无法区分的情况**（详见 02-rerank-output.md §2）。

#### ✔️ 验证

```powershell
python -c "
from wealth_rag.config import load_settings
from wealth_rag.retrieval.hybrid import Retriever
r = Retriever(load_settings('config.yaml'))
res = r.search('人民币防伪特征有哪些', top_k=8)
keys = [x.parent_id or ' > '.join(x.heading_path) or x.chunk_id for x in res]
print('返回', len(res), '条，不同节', len(set(keys)), '个')
"
# 期望：不同节数 = 返回条数（去重生效）
```

#### 💡 学习要点

1. **`or` 回退链要求各级语义等价。** 混入不同粒度的键会静默改变行为。
2. **去重必须可观测。** "砍掉了几条"是评估召回多样性的必要信息，当前完全不可见。
3. **这是 P1-4 同类问题的再现**：降级/回退路径必须保持与主路径相同的语义保证。
   说明"回退路径的语义一致性"值得作为一条通用检查项。

---

### P2-17 · Qdrant 本地库文件锁与多 worker / reload 冲突

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 阅读 `qdrant_store.py` 的既有注释，结合 `main.py` 的启动参数 |
| **严重度** | 🔵 低（有明确约束，但需写进部署文档） |
| **状态** | ⚠️ 待部署约束（须 `--workers 1` 且生产不带 `--reload`） |
| **所属环节** | 集成 / 部署 |

#### 🔍 现象

`qdrant_store.py:22-35` 的既有注释已记录关键约束：

> 本地模式通过文件锁独占数据库目录：**同一进程内多个 QdrantStore 必须共享同一个客户端**，
> 否则「先摄入、后检索」等组合用法会因锁冲突而报错。

而 `main.py:42` 现在是：

```python
uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
```

#### 🧪 证据

逐场景分析：

| 场景 | 风险 | 处理 |
| :--- | :--- | :--- |
| FastAPI 单进程 + 单例 Retriever | ✅ 安全 | — |
| `uvicorn --reload`（当前配置） | 重载重新 import 模块，旧进程释放锁；若新旧短暂重叠 → 锁冲突 | 开发期可接受；**生产必须关** |
| `uvicorn --workers N` | **直接撞锁**：多进程抢同一 Qdrant 目录 | **必须 `--workers 1`** |
| 同时跑 `rag ingest` CLI 与 API | 两个进程抢锁 | 摄入期间停服务，或改用服务模式 |

#### 🎯 根因

**本地嵌入式向量库的"零部署"优势，代价是"单写者"约束。**

Qdrant 本地模式用文件锁独占目录——这正是 P2-15 之外的另一处
"为了零部署而引入的隐含约束"。它不报错直到真的冲突，属于**部署期才暴露的问题**。

#### ✅ 解决方案

**写进部署文档 + 启动脚本**：

```powershell
# 生产启动（注意 --workers 1，且不带 --reload）
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
```

**需要并发时的正确做法**：把 Qdrant 切到**服务模式**（RAG 模块文档说明数据文件可原样迁移，
升级零代码改动）——而不是加 worker。

#### ✔️ 验证

```powershell
# 启动服务后，另开终端尝试摄入，应得到明确的锁冲突错误（而不是静默损坏）
rag ingest ./data
```

#### 💡 学习要点

1. **"零部署"的隐含代价是"单写者"。** 嵌入式数据库的便利性与并发能力是互斥的，
   选择时要明确知道自己放弃了什么。
2. **这类约束必须在部署文档里显式写出。** 它在开发环境（单进程 + reload）下不会暴露，
   上线加 worker 时才会炸。
3. **并发需求的正确解法是换架构层级（本地→服务模式），而不是绕过锁。**

---

### P2-18 · `Retriever.search` 一次只能服务一个查询

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 推演"多查询改写"（multi-query fanout）落地时的接口需求 |
| **严重度** | 🔵 低（已知限制，记录以备后续） |
| **状态** | 📌 已知限制（接口不支持分路查询，本阶段不做） |
| **所属环节** | 检索 |

#### 🔍 现象

`Retriever.search(query, ...)` 接受单个字符串。若要实现多查询改写
（生成 k 个改写 → 各检索一次 → RRF 融合），需要调用 k+1 次并自行融合。

#### 🧪 证据

调研中评估过的查询改写模式（见 `../Investigation/RAG触发策略和rerank/01-when-to-trigger.md`）：

| 模式 | 检索调用次数 | 当前接口是否支持 |
| :--- | :--- | :--- |
| 原生 | 1 | ✅ |
| HyDE | 1（仅稠密路换查询） | ⚠️ 需要分路控制，当前 `search` 不支持"只改稠密路的查询" |
| 多查询 fanout | k+1 | ⚠️ 需外层循环 + 自行 RRF |
| 追问改写 | 1（改写后） | ✅（在工具层改写即可） |

**HyDE 的"仅稠密路"要求是当前接口的结构性缺口**：调研材料明确指出
「HyDE for the dense arm only; keep the original query for the lexical arm」，
而 `search()` 内部把同一个 query 同时用于稠密编码与稀疏分词，无法分路。

#### 🎯 根因

**接口设计的是"单查询端到端"，而高级检索模式需要"分路控制查询"。**

这不是缺陷——首版不需要这些能力。但**记录下来很重要**，因为一旦要做 HyDE 或多查询，
会发现需要改接口签名（而非仅加参数），影响面比预想大。

#### ✅ 解决方案

**首版不做**（按 `Investigation/04` 的结论：改写模式"每个都要有评估集答案"，
且"链式堆叠所有改写模式"是明确的反模式）。

**若后续要做，建议的接口演进**：

```python
def search(
    query: str,
    top_k: int | None = None,
    use_rerank: bool | None = None,
    doc_id: str | None = None,
    # 【新增】分路查询覆盖：不传则两路都用 query
    dense_query: str | None = None,     # HyDE 用：只改稠密路的查询
    sparse_query: str | None = None,    # 关键词改写用
) -> list[SearchResult]:
```

多查询融合则建议放在**外层**（`Retriever` 之上），因为 RRF 融合逻辑已经存在于
`hybrid.py::_hybrid_local`，可提取复用。

#### ✔️ 验证

无需验证（未实施）。记录接口缺口即可。

#### 💡 学习要点

1. **记录"接口层面的缺口"和记录 bug 同样重要。** 前者不修不影响当下，
   但决定了后续功能的改动成本是"加参数"还是"改签名"。
2. **区分"不需要"与"做不到"。** 当前不做改写是**基于评估成本的决策**（不是做不到），
   而分路控制查询是**接口真的做不到**——两者要分开记录，否则将来会误判工作量。
3. **融合逻辑应提取为可复用函数**，避免多查询场景重复实现 RRF。

---
