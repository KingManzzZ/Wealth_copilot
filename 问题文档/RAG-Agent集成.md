# RAG ↔ Agent 集成 · 问题与解决方案

> **模块定位**：把 RAG 检索能力作为工具接进 WealthPilot Agent。覆盖
> `backend/core/tools/tools_hub/rag_tools/`（tools / impl / evidence 三层）、
> `backend/core/tools/tools_route.py`、`tools_schema.py`、`core/agent/` 与 `main.py`。
>
> **本文件是拆分自 [`问题与解决方案.md`](./问题与解决方案.md) 的模块文档**，
> 索引与跨模块经验见 [`README.md`](./README.md)。
>
> **集成层的三类职责**：挡住模型乱传的参数、管住昂贵且独占的资源（模型单例 / GPU 串行 / 上下文预算）、
> 把内部数据变成 LLM 能安全消费的形态。

## 问题索引

| 编号 | 问题 | 严重度 | 状态 | 子模块 |
| :--- | :--- | :--- | :--- | :--- |
| P1-10 | 工具注册表会连带注册辅助函数 | 🟡 中 | ✅ 已修复（改为以 schema 为准建注册表，已消除该类问题） | 工具层 |
| P2-13 | `parent_text` 体积无上限 | 🔵 低 | ✅ 已修复（代码，未实测——上下文预算已实现为硬约束） | 上下文预算 |
| P2-14 | `SearchResult` 不可直接序列化给 LLM | 🔵 低 | ✅ 已修复（代码，未实测——适配层已落地） | 适配层 |
| P2-15 | 模型冷启动 15s，且可能每次调用重复加载 | 🔵 低 | ✅ 已修复（代码，未实测——单例 + 预热已实现） | 模型单例 |
| P2-16 | 同步阻塞的 GPU 推理缺少并发保护 | 🔵 低 | ✅ 已修复（代码，未实测——线程锁已实现） | 并发控制 |

---

### P1-10 · 工具注册表会连带注册辅助函数

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 阅读 `tools_route.py` 的自动发现逻辑，推演 RAG 工具落地时的行为 |
| **严重度** | 🟡 中（不致命，但会让工具表变脏、且可能暴露内部函数） |
| **状态** | ✅ 已修复（改为以 schema 为准建注册表，已消除该类问题） |
| **所属环节** | 集成 |

#### 🔍 现象

`tools_route.py` 的自动发现规则是：

```python
for name, func in inspect.getmembers(module, inspect.isfunction):
    # 只注册在本模块内 def 的函数，跳过 import 来的 service 函数
    if func.__module__ != module.__name__:
        continue
    TOOL_REGISTRY[name] = func
```

判据只有两条：**是函数** + **在本模块内定义**。
**没有排除下划线开头的私有函数。**

于是如果 `rag_tools.py` 里写了：

```python
def _get_retriever(): ...        # ← 会被注册进 TOOL_REGISTRY
def _format_evidence(...): ...   # ← 会被注册
def search_knowledge(...): ...   # ← 这才是真工具
```

这三个函数**都会进注册表**。

#### 🧪 证据

模块文件级的下划线过滤是存在的（`tools_route.py:12`）：

```python
for entry in os.listdir(_hub_dir):
    if entry.startswith("_") or not entry.endswith(".py"):
        continue        # ← 跳过 _ 开头的**文件**
```

所以设计意图是"用下划线表示私有"，但**这个约定只落实在文件级，没有落实到函数级**。

**实际危害评估**（不过度渲染）：

| 危害 | 是否成立 |
| :--- | :--- |
| 模型会调用到 `_get_retriever` | ❌ 不会——`TOOLS_SCHEMA` 里没有它的 schema，模型无从知道它存在 |
| 手动 `route("_get_retriever", "{}")` 能调通 | ✅ 成立，但需要有人主动构造 |
| `[tools_route] 自动发现并注册的工具: [...]` 日志被污染 | ✅ 成立，调试时噪声增加 |
| 未来若改为"从注册表反向生成 schema"，私有函数会被意外暴露 | ✅ 隐患 |

#### 🎯 根因

**"私有性"的约定在两层实现中不一致**：文件层用下划线前缀过滤，函数层没有对应规则。

这类"约定只落实了一半"的问题很常见，特征是**文档/示例里是这么用的，
但代码没强制**——于是依赖使用者的自觉。

#### ✅ 解决方案

**实现时规避（推荐，不改现有代码）**：把辅助逻辑放进**以下划线开头的模块**，
或用一个类来承载状态：

```python
# backend/core/tools/tools_hub/rag_tools.py
_state = {"retriever": None}      # 模块级变量，不是函数 → 不会被注册
_lock = threading.Lock()          # 同上

def _build_retriever(): ...       # 仍会被注册，但无害（见上表）

def search_knowledge(query, top_k=5):   # 唯一的真工具
    ...
```

或者更干净——**把内部实现放进单独的私有模块**：

```
tools_hub/rag_tools.py          # 只放 search_knowledge，import 自 _rag_impl
tools_hub/_rag_impl.py          # 下划线开头 → 整个文件被跳过
```

**建议的根治（可选，需改 `tools_route.py`）**：

```python
if func.__module__ != module.__name__ or name.startswith("_"):
    continue        # 跳过本模块内的私有函数
```

#### ✔️ 验证

```powershell
# 启动后端，看注册日志
# 期望：只出现 search_knowledge，不出现 _build_retriever 等
python -c "from core.tools import tools_route; print(sorted(tools_route.TOOL_REGISTRY))"
```

#### 💡 学习要点

1. **"私有"的约定要在所有层级一致落实**，否则等于没有约定。
2. **评估危害时要分清"实际危害"与"理论危害"。** 本例中模型不会被污染（因为没有 schema），
   所以定级为 🟡 而非 🔴——**不过度渲染问题本身也是一种准确性**。
3. **自动发现机制的注册表是"能力清单"，会被调试与后续功能消费。** 它的干净程度会影响
   后续所有基于它的功能，所以值得在第一次接入时就处理干净。

---

### P2-13 · `parent_text` 体积无上限

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 推演 RAG 工具返回值的 token 体积 |
| **严重度** | 🔵 低（危害明确但尚未发生，因为融合还没做） |
| **状态** | ✅ 已修复（代码，未实测——上下文预算已实现为硬约束） |
| **所属环节** | 集成 |

#### 🔍 现象

`_fill_parent_text()` 在**最终截断之后**回查父块全文并挂到 `res.parent_text`。
这部分体积**完全不受 `final_top` 控制**：

| 项 | 上限 |
| :--- | :--- |
| 子块 | 512 token（`chunking.max_tokens`） |
| **父块** | **无上限**（所属「节」的全文） |

#### 🧪 证据

推演最坏情况：

```
8 条证据 × (子块 512 token + 父块 3000 token)
  = 8 × 3512
  ≈ 28,000 token
```

**一次工具调用就能把 LLM 上下文打爆。** 而且这是"静默"的——
没有任何地方会告警"你刚刚塞进去 28K token"。

#### 🎯 根因

**扩展上下文的机制缺少预算约束。**

`parent_text` 的设计目标（P2-2 之后改为引用式回查）是"提供更完整的上下文供 LLM 使用"，
但**没有定义"多少算够"**。在单机检索场景里这不是问题（CLI 展示而已），
一旦作为工具返回值进入 LLM 上下文，就必须有预算。

#### ✅ 解决方案

**建议三层约束**：

1. **工具参数控制**：

```python
def search_knowledge(query: str, top_k: int = 5,
                     include_parent: bool = False,
                     context_budget_tokens: int = 2000) -> dict:
```

默认 **不给父块**（`include_parent=False`）。模型需要更大上下文时会自己再要一次——
**这比默默塞 28K token 好**，而且让模型显式表达"我需要更多上下文"是有价值的信号。

2. **预算内降级**：预算优先放子块 `raw_text`，剩余预算才用于父块扩展；
   超预算时从尾部**整节**丢弃（不切碎单节）。

3. **父块按需截断**：若给父块，只给"子块位置附近的前后 N 个字符"。

> ⚠️ **实现前提**：截断父块需要知道**子块在父块中的偏移**，
> 而当前 payload 里没有这个信息。需要摄入侧补 `parent_offset` 字段，
> 或先退化为"父块前 1500 字符"这种粗糙做法。

#### ✔️ 验证

```powershell
# 检查单次工具返回的 token 量
python -c "
import json, tiktoken
enc = tiktoken.get_encoding('cl100k_base')
# 假设 payload 已由适配层生成
t = open('tool_payload_sample.json', encoding='utf-8').read()
print('tokens:', len(enc.encode(t)))
"
# 期望：< 2500
```

#### 💡 学习要点

1. **上下文扩展机制必须有预算。** 任何"给更多上下文"的功能都要回答"多少算够"。
2. **默认值要选保守的那一侧。** `include_parent=False` 是更好的默认——
   模型的"再要一次"比"静默塞爆"更容易调试。
3. **未做预算约束时，问题不会显现直到接入 LLM**。这是"集成阶段才暴露的设计缺口"，
   说明**设计时应按最终的消费方（LLM 上下文）来估算体积，而不是按当前消费方（CLI 表格）**。

---

### P2-14 · `SearchResult` 不可直接序列化给 LLM

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 推演 `tools_route.route()` 对返回值的 `json.dumps` 行为 |
| **严重度** | 🔵 低（有明确解法，但必须做） |
| **状态** | ✅ 已修复（代码，未实测——适配层已落地） |
| **所属环节** | 集成 |

#### 🔍 现象

`tools_route.route()` 会对工具返回值做 `json.dumps(result, ensure_ascii=False)`。
而 `Retriever.search()` 返回的是 `list[SearchResult]`，`SearchResult` 是 **dataclass**：

```python
json.dumps(SearchResult(...))   # → TypeError: Object of type SearchResult is not JSON serializable
```

即使加上 `dataclasses.asdict()` 能序列化，**内容也不适合给 LLM**。

#### 🧪 证据

逐字段审查 `SearchResult` 作为 LLM 输入的问题：

| 字段 | 问题 | 处置 |
| :--- | :--- | :--- |
| `text` | 含面包屑前缀，与 `raw_text` 内容重叠；512 token 级 | ❌ 丢弃 |
| `raw_text` | 已含叶子标题，是给 LLM 的正确文本 | ✅ 保留 |
| `parent_text` | 无上限（见 P2-13） | ⚠️ 按预算给 |
| `source_path` | `E:\...\data\19-080249-金融知识普及读本.pdf` 本地绝对路径，对用户无意义且泄露目录结构 | ❌ 丢弃 |
| `heading_path` | 数组，已包含在 `citation` 里 | ❌ 丢弃 |
| `chunk_id` / `parent_id` | 36 字符 UUID ×2，对 LLM **零信息量** | ❌ 丢弃 |
| `score` / `rerank_score` | 模型会拿分数当"可信度"复述（"该证据相关度 0.89"），污染输出风格 | ❌ 不给模型 |
| `page` / `doc_title` | 短且有用 | ✅ 折进 `citation` |

**实测估算**：单条证据从 ~1200+ token 降到 ~500 token 以内；
8 条从可能 28K token 降到 4K 以内。

#### 🎯 根因

**`SearchResult` 是"内部数据模型"，被直接当成了"对外接口契约"。**

内部模型为了完整性和可调试性会携带全部字段；对外接口需要的是**最小充分信息**。
两者目标相反，**不存在一个数据结构能同时最优地服务两者**。

这与 P0-3（`text` vs `raw_text` 职责混用）同源：
都是"一个数据结构承担了多个消费方的需求"。

#### ✅ 解决方案

**加一层瘦身适配**（这是集成时必须做的第一件事）：

```python
def _format_evidence(query: str, results: list[SearchResult]) -> dict:
    return {
        "evidence_quality": ...,          # high | medium | low
        "top_rerank_score": ...,          # 数值给前端/日志用
        "query_used": query,              # 回显实际检索用的查询串（调试追问改写极重要）
        "result_count": len(results),
        "results": [
            {
                "index": i,                                   # 编号 = 引用锚点
                "citation": r.citation,                       # 文档 | 页码 | 标题路径
                "text": r.raw_text,                           # 证据正文
                "chunk_type": r.chunk_type,
                "parent_text": r.parent_text if include_parent else None,
            }
            for i, r in enumerate(results, start=1)
        ],
        "notice": ...,                    # 拒答/降级指令放在数据里，遵循度更高
    }
```

**`index` 编号是关键设计**：SYSTEM_PROMPT 里规定"每处结论标注来源编号如 `[1]`"，
编号**必须由后端给**，不能让模型自己数。

#### ✔️ 验证

```powershell
python -c "
import json
from wealth_rag.config import load_settings
from wealth_rag.retrieval.hybrid import Retriever
# 粗验：适配层输出可序列化且不含内部字段
payload = _format_evidence('测试', Retriever(load_settings('config.yaml')).search('测试', top_k=3))
s = json.dumps(payload, ensure_ascii=False)
assert 'chunk_id' not in s and 'source_path' not in s
assert 'E:\\\\' not in s
print('OK, tokens ≈', len(__import__('tiktoken').get_encoding('cl100k_base').encode(s)))
"
```

#### 💡 学习要点

1. **内部模型 ≠ 对外契约。** 直接暴露 dataclass 既会序列化失败，也会泄露内部字段。
2. **给 LLM 的内容要按"最小充分"裁剪**，而不是"全给省事"。
   UUID、绝对路径、内部分数都是纯 token 浪费，其中内部分数还会**主动误导**模型（被当可信度复述）。
3. **编号要后端给。** 引用机制的锚点不能依赖模型自己计数——那是最容易出错的地方。
4. **这是 P0-3 的同类问题在集成层的再现**（一个结构服务多个消费方）。
   说明"数据结构服务几个下游"是个需要反复检查的维度。

---

### P2-15 · 模型冷启动 15s，且可能每次调用重复加载

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 阅读 `Retriever.__init__` 与编码器/重排器的懒加载实现 |
| **严重度** | 🔵 低（有明确解法，但影响体验） |
| **状态** | ✅ 已修复（代码，未实测——单例 + 预热已实现） |
| **所属环节** | 集成 |

#### 🔍 现象

`Retriever.__init__`（`retrieval/hybrid.py:18-25`）会构造
`Qwen3Embedder` / `Qwen3Reranker` / `SparseEncoder` / `ChineseBM25` / `QdrantStore`。

其中编码器与重排器是**懒加载**的（`load()` 在首次 `encode_query` / `score` 时才真正
`from_pretrained`）。如果每个工具调用都 `Retriever(settings)`，就会**反复加载两个 1.15GB 的模型**。

#### 🧪 证据

| 风险 | 后果 |
| :--- | :--- |
| 每次工具调用 `new Retriever()` | 每次 `from_pretrained` 重载 2×1.15GB 权重 → 首次十几秒，之后每次数百 ms～数秒 |
| 显存累积 | 可能 OOM |
| 首个用户请求承担冷启动 | RAG 模块文档实测「检索含冷启动 ~15s，热查询秒级」 |

#### 🎯 根因

**懒加载是正确的设计（避免不用时占显存），但缺少"只加载一次"的保证。**

懒加载解决的是"什么时候加载"，没解决"加载几次"。而后者需要**单例作用域**——
这是两个正交的问题。

#### ✅ 解决方案

**模块级单例 + 启动预热 + 吞异常降级**（与 `QdrantStore` 的 `_CLIENTS` 缓存同思路）：

```python
# backend/core/tools/tools_hub/rag_tools.py
_state = {"retriever": None}

def _get_retriever():
    if _state["retriever"] is None:
        from wealth_rag.config import load_settings
        from wealth_rag.retrieval.hybrid import Retriever
        _state["retriever"] = Retriever(load_settings(RAG_CONFIG_PATH))
    return _state["retriever"]
```

启动预热放在 FastAPI 的 `lifespan`（`backend/main.py`）：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from core.tools.tools_hub import rag_tools
    rag_tools.prewarm()      # 加载模型 + 建集合 + 一次占位检索
    yield
```

> ⚠️ **`prewarm()` 必须吞掉异常并降级**：如果 RAG 依赖缺失（没装 torch / Qdrant 库不存在 /
> 模型没下载），服务仍应能启动，只是 RAG 工具不可用。
> 否则一个**可选能力**的故障会让**整个 Agent** 起不来。

#### ✔️ 验证

```powershell
# 连跑两次检索，第二次应无加载日志
python -c "
from wealth_rag.config import load_settings
from wealth_rag.retrieval.hybrid import Retriever
import time
r = Retriever(load_settings('config.yaml'))
t=time.time(); r.search('测试一', top_k=3); print('首次', round(time.time()-t,2), 's')
t=time.time(); r.search('测试二', top_k=3); print('第二次', round(time.time()-t,2), 's')
"
# 期望：首次含冷启动（十几秒），第二次秒级
```

#### 💡 学习要点

1. **懒加载解决"何时加载"，不解决"加载几次"。** 后者需要单例作用域，两者正交。
2. **启动预热要把失败挪到启动阶段。** 让"依赖缺失"在启动时暴露并降级，而不是在用户第一次提问时炸。
3. **可选能力的故障不能导致核心服务不可用。** 预热必须吞异常——这是"可选"这个词的实际含义。

---

### P2-16 · 同步阻塞的 GPU 推理缺少并发保护

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 阅读 `agent.py` 与 `chat.py` 的调用链，确认当前是同步生成器 |
| **严重度** | 🔵 低（当前安全，但改动容易踩坑） |
| **状态** | ✅ 已修复（代码，未实测——线程锁已实现） |
| **所属环节** | 集成 |

#### 🔍 现象

`Retriever.search()` 是**纯同步**的（GPU 推理 + 本地磁盘 IO，一次 1~3s，CPU 上可能 10s+）。
而 `chat.py` 里的调用方式需要确认是否阻塞事件循环。

#### 🧪 证据

```python
# chat.py
async def chat(request: ChatRequest):
    def event_stream():                      # 同步生成器
        for event in chat_stream(...):       # ← 同步阻塞
            yield sse_(event)
    return StreamingResponse(event_stream(), ...)
```

**好在此处是安全的**：FastAPI 对**同步生成器**会放到线程池执行（`iterate_in_threadpool`），
不会直接阻塞事件循环。**这一点是幸运，不是设计**。

**两个真实风险**：

| 风险 | 说明 |
| :--- | :--- |
| 有人把 `chat_stream` 改成 `async def` | 必须改用 `run_in_threadpool` / `asyncio.to_thread` 包裹检索，否则单次检索冻结整个服务 |
| 并发请求同时触发 RAG | 多个线程同时往同一张 GPU 灌推理任务 → 显存峰值叠加、延迟互相拖累 |

#### 🎯 根因

**"当前不阻塞"是一个偶然的、脆弱的前提，没有被显式保护或注明。**

同步生成器恰好被 FastAPI 丢进线程池——但代码里没有任何注释说明这个依赖，
后来者把函数改成 `async` 是很自然的改动，**改完就会静默引入全服务冻结**。

#### ✅ 解决方案

**(a) 加注释注明前提**（最低成本、最必要）：

```python
def event_stream():
    # ⚠️ 必须保持同步生成器：FastAPI 会把它放进线程池执行（iterate_in_threadpool）。
    # 若改成 async def，Retriever.search() 会在事件循环里同步阻塞，冻结整个服务。
    for event in chat_stream(...):
        yield sse_(event)
```

**(b) 用线程锁串行化模型调用**（8GB 显存下的必要保护）：

```python
_lock = threading.Lock()

def search_knowledge(query: str, top_k: int = 5) -> dict:
    with _lock:                       # 串行化 GPU 调用，避免显存峰值叠加
        results = _get_retriever().search(query, top_k=top_k)
    return _format_evidence(query, results)
```

**(c) 补工具执行进度事件**：现有 SSE 事件流里，工具执行期间**没有任何进度事件**，
只有执行完才 `yield tool_result`。检索要 1~3s，用户会看到长时间静默。

建议补 `tool_start` 事件，让前端能显示「正在检索知识库…」：

```python
yield {"type": "tool_call", "id": ..., "name": ..., "arguments": arguments}
yield {"type": "tool_start", "id": tool["id"], "name": tool["name"]}   # 【新增】
origin_result = tools_route.route(tool["name"], tool["arguments"])
yield {"type": "tool_result", ...}
```

#### ✔️ 验证

```powershell
# 并发 3 个请求，观察是否有 OOM 或延迟叠加异常
# 前端观察：检索期间是否出现「正在检索知识库…」
```

#### 💡 学习要点

1. **依赖框架的隐式行为时，必须写注释。** "同步生成器会被丢进线程池"是个不易察觉的前提，
   不注明就会被后来者无意破坏。
2. **GPU 是独占资源，并发调用必须串行化或限流。** 8GB 显存下并发不会提升吞吐，只会互相拖累。
3. **耗时操作要有进度反馈。** 1~3s 的静默在流式接口里体验上等同于卡死；
   `tool_start` 事件的成本极低，收益是体验的质变。

---
