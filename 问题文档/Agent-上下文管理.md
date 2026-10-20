# Agent 上下文管理 · 问题与解决方案

> **模块定位**：会话历史的**形态**与**权威归属**——历史怎么存、怎么回放给模型、
> 谁负责写、请求前怎么裁剪。覆盖：
>
> - `backend/api/routes/chat.py`（入口契约与服务端流程）
> - `backend/core/services/replay.py`（历史回放/展开）
> - `backend/core/agent/context.py`（预算、观察掩码、滑动窗口）
> - `backend/core/agent/compaction.py`（长会话压缩：摘要 + 锚点 + 保留最近 N 轮）
> - `backend/core/agent/l1.py`（单次调用：稳定前缀、工具结果截断、请求度量）
> - `frontend/src/hooks/useChat.ts`、`frontend/src/lib/api.ts`（发送与写权）
>
> **与另两条问题线的关系**：
>
> | 文档 | 主题 |
> | :--- | :--- |
> | 本文 | 会话历史的形态、权威归属、请求前裁剪 |
> | [Agent-工具调用可靠性.md](./Agent-工具调用可靠性.md) | 工具名/参数幻觉与熔断（`T` 编号） |
> | [backend-core架构审查.md](./backend-core架构审查.md) | `backend/core` 架构审查（只列问题） |
>
> **编号体系**：用 **`C`（Context）前缀**。`C1-x` = 直接导致协议错误或数据损坏的；
> `C2-x` = 架构与边界问题。
>
> **调研依据**：[`../Investigation/上下文机制/`](../Investigation/上下文机制/)——
> 尤其 `06-前置件详解.md`（回放展开器与 `conversation_id` 的完整设计）。

---

## 一句话结论

> **历史的形态（怎么喂给模型）和历史的权威（谁说了算）是两件事，必须分别解决。**
> 前者靠一个"配对由构造保证"的展开器；后者靠"服务端读库 + 前端停写"。

### 本轮最值得记住的一个发现

实施 B（服务端接管历史）时才发现：**只把写权交给后端是不够的，还必须同时让前端停写**。
否则每轮会写两份（前端 `POST /messages` + 后端 `append_messages`），
表现为"会话里每条消息都出现两次"。

这个坑不在原计划里，是在对照 `useChat.ts` 的 `finally` 块时发现的——见 **C2-5**。

---

## 问题索引

| 编号 | 问题 | 严重度 | 状态 | 位置 |
| :--- | :--- | :--- | :--- | :--- |
| C1-1 | 前端历史组装有两个配对缺陷（缺 `result` / `name` 过滤不对称） | 🔴 高 | ⚠️ 已绕开（关键路径移交服务端；前端旧实现仍在） | `useChat.ts` `buildHistory` / `replay.py` |
| C1-2 | 断流把 `running` 状态落库，会话永久无法续聊 | 🔴 高 | ✅ 已修复（代码就位，e2e 未实测） | `chat.py` `_AssistantTurn` |
| C2-1 | `/api/chat` 无 `conversation_id`，历史权威在客户端 | 🟡 中 | ✅ 已实现（代码就位，e2e 未实测） | `chat.py` / `replay.py` |
| C2-2 | `history` 客户端可控，可伪造 `tool` 结果绕过工具安全边界 | 🔴 高 | ⚠️ 部分修复（服务端路径已关闭；兼容期仍在） | `chat.py` `_legacy_response` |
| C2-3 | 请求前无上下文预算，无法裁剪 | 🟡 中 | ✅ 已实现（**19 条用例实测通过**） | `context.py` |
| C2-4 | 同会话并发写入会让 `tool_calls` 与 `tool` 错配 | 🔵 低 | ⚠️ 部分修复（进程内锁；多 worker 不覆盖） | `chat.py` `_acquire` |
| C2-5 | 写权移交期前后端双写，消息重复 | 🔴 高 | ✅ 已修复（tsc 通过） | `useChat.ts` `finally` |
| C2-6 | 长会话没有压缩，token 随轮次单调上升 | 🟡 中 | ✅ 已实现（**8 条压缩用例实测通过**） | `compaction.py` |
| C2-7 | L1 单次调用：system 带日期、工具结果无上限、无请求度量 | 🟡 中 | ✅ 已实现（**8 条 L1 用例实测通过**） | `l1.py` / `agent.py` |

**合计 9 条**：6 条已修复/已实现，1 条已绕开，2 条部分修复。

> **状态口径**（沿用本项目 README 的约定）：
> "✅ 已修复（有实测证据）"指**跑过**；
> "代码就位，未实测"指静态审阅与类型检查通过但**未执行**——
> 本轮的 e2e 层因环境缺 `fastapi` 且数据库只读而无法执行，**必须如实标注**。

---

## 改动前后对照

| 场景 | 改动前 | 改动后 |
| :--- | :--- | :--- |
| 历史由谁提供 | 前端拼好 `history` 传给后端 | 后端按 `conversation_id` 从库读 |
| 工具结果的形态 | 内嵌在 `assistant.tool_calls` | 服务端展开成 `assistant` + 逐条 `tool` |
| 缺 `result` 的调用 | 缺 `tool` 消息 → 下次续聊 400 | 服务端合成 `interrupted` 提示，会话可续 |
| 谁写 `messages` 表 | 前端（整轮结束一次写） | **后端**（用户消息请求开始写、助手消息流结束写） |
| 断流时 | 用户输入 + 半截助手一起丢；状态坏在库里 | 用户输入已落库；助手按 `interrupted` 收敛 |
| 请求前裁剪 | 无 | 观察掩码（保留最近 8 条工具输出）+ 预算裁剪（8000 单位）+ 长会话压缩（75% 触发，保留最近 6 轮） |
| 单条工具结果 | 原样塞入 | 超过 4000 字符截断（保留头尾），回放历史时同样截断 |
| system 里的动态内容 | 末尾拼"当前日期"→ 缓存每天失效 | 日期移到当前 user 消息；system + tools 会话内稳定 |
| 请求规模观测 | 无 | 每轮首次请求打印 system/tools/历史/工具往返分段 |
| 并发 | 无保护 | 同会话进程内互斥，第二个请求 409 |

---

## C1-1 · 前端历史组装有两个配对缺陷

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 阅读 `useChat.ts` 的 `buildHistory`，对照 OpenAI 的 `tool_calls` 配对约束 |
| **严重度** | 🔴 高（症状是"某会话再也发不出消息"，且提示与真实原因无关） |
| **状态** | ⚠️ **已绕开**——服务端接管后该路径不再关键；前端旧实现未改（见末注） |
| **所属环节** | 历史回放 |

### 🔍 现象

两个同源缺陷，都违反"每个 `tool_calls[i].id` 必须有一条对应 `tool` 消息"的硬约束：

**缺陷 A · 缺 `result` 时省略 `tool` 消息**

```ts
for (const tc of calls) {
  if (tc.result !== undefined) {            // ← 没有 result 就整条跳过
    history.push({ role: "tool", content: ..., tool_call_id: tc.id })
  }
}
```

而 `tool_calls` 元素是**无条件**加入 assistant 的。于是
`assistant.tool_calls` 里有一个 id，后面却没有对应的 `tool` 消息 → **400**。

**缺陷 B · `name` 过滤不对称**

```ts
const tool_calls = calls.filter((tc) => tc.name).map(...)   // 过滤了 name
for (const tc of calls) { ... }                             // 却遍历未过滤的 calls
```

`name` 为空但 `result` 存在时：`tool_calls` 里没有它、`tool` 消息里却有它 →
**孤儿 tool 消息**（`tool_call_id` 不在前一条 assistant 的 `tool_calls` 里）。

### 🧪 证据

`result === undefined` 不是假想状态，有一条明确的产生路径：

1. `tool_call` 事件到达 → 建 `status:"running"`、无 `result` 的 tc；
2. 只有 `tool_result` 事件才写入 `result`；
3. 若流在两者之间中断（关页面 / 断网 / 后端异常），`result` **永远 `undefined`**；
4. 更糟的是这个状态被**持久化**（`finally` 里 `toInput(finalAssistant)` 落库）。

于是该会话**之后每一次续聊都会 400**，而用户看到的是"请求失败"。

### 🎯 根因

**配对关系没有被"构造"保证，而是靠两侧各自过滤。**
只要两条路径的判断条件不完全一致（`result !== undefined` vs 无条件、`filter(name)` vs 全量），
配对就会断。这类缺陷无法靠"记得两边都改"解决——必须让**一处数据同时驱动两侧**。

### ✅ 解决方案

服务端新增 `backend/core/services/replay.py`，把四条不变量做成实现约束：

| 不变量 | 要求 |
| :--- | :--- |
| **I1** 配对完整 | 每个 `tool_calls[i].id` 恰好一条 `tool` 消息 |
| **I2** 无孤儿 | 每条 `tool` 的 `tool_call_id` 必须在前一条 assistant 的 `tool_calls` 里 |
| **I3** 类型正确 | `arguments` 必须是 JSON 字符串 |
| **I4** 缺失必补 | 无 `result` 时**合成** `interrupted` 提示，不能省略 |

关键实现（`replay.py:63`）：**只过滤一次，两侧共用同一列表**。

```python
usable = [c for c in calls if c.get("id") and c.get("name")]
# assistant 的 tool_calls 与随后的 tool 消息都从 usable 派生
```

并让服务端接管组装：`chat.py` 带 `conversation_id` 时**忽略客户端 `history`**，
走 `replay.expand_to_model_messages`。这样这两个前端缺陷在权威路径上被**绕开**。

### ✔️ 验证

`tests/rag/test_context.py` 覆盖了四条不变量（19 条用例全通过），其中两条是反向用例：

- `check_expand_synthesizes_missing_result` —— 缺 `result` 时仍补一条 tool 消息（I4）；
- `check_expand_drops_nameless_call_in_pairs` —— 无 `name` 的调用**成对丢弃**，不产生孤儿（I1/I2）。

每个用例末尾都调 `assert_pairing()` 做结构性自检，比逐字段断言更可靠。

```powershell
cd backend
python tests\rag\run_tests.py --only test_context.py
# → 结果：通过 19 / 失败 0 / 错误 0 / 跳过 0
```

### 💡 学习要点

**配对约束必须由"单一数据源"保证，不能靠两侧各自判断。**
判断条件一旦有两份实现，就一定会漂移——而漂移的症状是"某种历史组合下才 400"，
极难复现。

> **末注**：这两个缺陷在 `frontend/src/hooks/useChat.ts` 的 `buildHistory` 里**仍然存在**。
> 之所以标"已绕开"而非"已修复"，是因为服务端权威路径不再使用它。
> 但它仍服务**兼容路径**（无 `conversation_id` 时），建议后续一并修掉，
> 或直接删掉兼容路径。

---

## C1-2 · 断流把 `running` 状态落库，会话永久无法续聊

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 推演"工具执行中关掉页面"的完整链路 |
| **严重度** | 🔴 高（一次误操作让整条会话报废） |
| **状态** | ✅ 已修复（代码就位；e2e 因环境限制未实测） |
| **所属环节** | 落库时机 |

### 🔍 现象

工具执行需要 1~3s（知识库检索）。若用户在这个窗口里关页面 / 断网 / 后端异常，
助手消息会停在"有 `tool_call`、无 `tool_result`"的半截状态。

### 🧪 证据

旧实现在 `finally` 里把当前消息整条落库，**不区分状态**：

```ts
if (finalAssistant) items.push(toInput(finalAssistant))
await appendConversationMessages(convId, items)
```

`toInput` 原样拷贝 `toolCalls`，于是 `status:"running"`、无 `result` 的记录进了库。
下次续聊时 `buildHistory` 按 C1-1 的缺陷 A 处理 → 缺 `tool` 消息 → 400。

同时 `applyEvent` 的 `error` 分支**不改消息结构**（注释写明"thinking / round / done / error 不改消息结构"），
所以后端发 `error` 事件**不会**把在跑的 tc 标成失败。

### 🎯 根因

**"未完成"这个状态没有被表达出来。**
系统只有 `running / success / error` 三态，而"流中断"没有被收敛到终态就被持久化了。

### ✅ 解决方案

把收敛放在**落库前**，且用"缺 `result`"这个事实本身来表达"未完成"（不新增字段）：

```python
# chat.py `_AssistantTurn.to_message()`
for call in calls:
    if call["status"] == "running" or not call.get("result"):
        call["status"] = "error"
        call["result"] = INTERRUPTED_PAYLOAD   # error_kind="interrupted", retryable=True
        call["endedAt"] = now
```

**为什么复用 `retryable=True` 的错误契约**：模型已经学会"`retryable` 为真就重试"
（见 [Agent-工具调用可靠性.md](./Agent-工具调用可靠性.md) 的 T1-1/T2-3）。
用同一套语义，模型看到 `interrupted` 会自然地重新调用工具。

### ✔️ 验证

- `replay.py` 侧：`check_expand_synthesizes_missing_result` 断言缺 `result` 时合成的载荷
  含 `error_kind="interrupted"`、`retryable=True`（已实测通过）；
- `chat.py` 侧：`test_api_e2e.py::check_conversation_id_persists_and_replays` 断言落库的
  `tool_calls` 状态**不能是 `running`**——但该用例在本环境被 Skip（缺 `fastapi` + 库只读）。

> **未实测部分**：真实断流（关页面）路径没有执行验证。建议手工验一次：
> 发一个会调 `search_knowledge` 的消息，在工具卡片显示"运行中"时关掉标签页，
> 重开该会话再发消息——期望不报 400，且模型知道上次工具没跑完。

### 💡 学习要点

**"未完成"必须被显式收敛，不能靠调用方自觉。**
只要存在"状态已产生但没走完"的窗口，就必须在**写入口**做终态收敛——
放在读取侧（回放时兜底）也对，但那只是第二道防线。

---

## C2-1 · `/api/chat` 无 `conversation_id`，历史权威在客户端

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 阅读 `chat.py` 契约；推演 L2（单会话上下文）裁剪需要什么 |
| **严重度** | 🟡 中（不报错，但让所有裁剪策略无处落脚） |
| **状态** | ✅ 已实现（代码就位；e2e 未实测） |
| **所属环节** | 接口契约 |

### 🔍 现象

改动前：

```python
class ChatRequest(BaseModel):
    message: str
    history: list          # ← 历史由客户端全权提供
```

后端完全无状态：不知道这段历史属于哪个会话、是否完整、总共有多少 token。

### 🧪 证据

由此产生三个直接后果：

| 后果 | 说明 |
| :--- | :--- |
| **裁剪无作用对象** | 观察掩码要知道"最近 N 轮"的边界、预算要知道总量——都依赖会话 |
| **摘要无处挂** | `conversations.summary` 字段已建好（`schema.py:150`），但没有服务端流程会去读它 |
| **无法在请求前决策** | `usage` 是**响应之后**才有的，而裁剪必须在**请求之前**决定 |

### 🎯 根因

**把"历史"当成了"上一次请求的参数"，而不是"服务端的一份持久状态"。**
于是持久化（`/conversations/{id}/messages`，前端写）和上下文组装（`/chat`，前端传）
成了两条互不相干的链路：历史**存了**，但**从不参与**发给模型的历史组装。

### ✅ 解决方案

`ChatRequest` 增加 `conversation_id`，服务端接管读历史：

```python
class ChatRequest(BaseModel):
    message: str
    conversation_id: int | None = None   # 新前端必传
    history: list | None = None          # 已废弃，仅兼容旧前端
```

三种分支（`chat.py:196`）：

| 请求 | 路径 |
| :--- | :--- |
| 有 `conversation_id` | 服务端读库 → 展开 → 裁剪 → 流式 → 落库 |
| 无 `conversation_id` 有 `history` | 兼容路径：归一化后直接用，**不读库不落库**，并打印告警 |
| 两者都无 | 新建会话，走服务端路径 |

**关键顺序**（`chat.py:220-226`）：先读历史（此时不含本轮用户消息，`chat_stream` 自己会追加），
再落库用户消息——这样断流也不会丢用户输入。

### ✔️ 验证

- 静态：`tsc --noEmit` 0 错误（前端 `streamChat` 与 `useChat` 都已传 `conversation_id`）；
- 动态：`check_conversation_id_persists_and_replays` 断言"库里留下 user+assistant 两条"
  且"回放能产出配对完整的消息"——该用例在本环境 **Skip**（缺 `fastapi`）。

> **未实测部分**：服务端读库→落库的完整链路没跑过。
> 环境限制：`fastapi` 未安装；`backend/db/` 在沙箱里不可写（SQLite 无法建 journal，
> 报 `attempt to write a readonly database`），临时目录同样不可写。

### 💡 学习要点

**"持久化"与"上下文组装"不是同一件事，但必须共用同一份历史。**
两者分离时系统看起来正常（历史列表能看、对话能继续），
但所有依赖"整体视角"的能力（预算、裁剪、摘要）都无法实现——而**不会报错**。

---

## C2-2 · `history` 客户端可控，可伪造 `tool` 结果绕过工具安全边界

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 对照"工具调用兜底"的假设链，检查有哪条旁路 |
| **严重度** | 🔴 高（完整性缺陷：服务端的安全边界可被绕过） |
| **状态** | ⚠️ **部分修复**（服务端权威路径已关闭；兼容路径仍在） |
| **所属环节** | 安全 / 完整性 |

### 🔍 现象

`history: list` 没有任何结构校验，客户端可以发**任意** messages。最危险的一种：

```json
{"role": "tool", "tool_call_id": "fake_1", "content": "{\"assets\": [{\"value\": 9999999}]}"}
```

### 🧪 证据

这条伪造的 `tool` 消息**根本不经过** `tools_route.route()`：

- 不触发 `validate_arguments`（缺必填 / 幻觉参数校验被跳过）；
- 不进 `seen_calls`（同参去重无效）；
- 不参与未知工具熔断。

也就是说，`Investigation/tool幻觉兜底机制/` 与本项目 T 系列问题里做的所有工具侧防护，
都建立在"工具结果来自 `route()`"这个**隐含前提**上；而 `history` 是一条不经过它的旁路。

同类风险还有两条：伪造 `assistant` 消息（让模型把某句话当成"我已经说过"）、
删改历史（结论偏差且后端无法察觉）。

### 🎯 根因

**把"客户端提交的历史"当成了可信历史。**
在单机个人项目里，威胁模型很薄（"攻击者"只有用户自己或已失陷的前端），
所以风险等级实际不高；但**从完整性角度看，权威源放错了地方**。

### ✅ 解决方案

服务端权威路径下**完全忽略客户端 `history`**：历史只从 `messages` 表读。
于是伪造的 `tool` 消息无法进入模型上下文。这是"关闭"而不是"过滤"——不试图识别伪造，
而是不给它进入的通道。

### ✔️ 验证

- 代码层面：`chat.py` 带 `conversation_id` 的分支不读取 `request.history`；
- `check_unknown_conversation_returns_404` 确认"不存在的会话"会 404，**而不是**静默新建
  （本环境 Skip）。

> **仍存在的口子**：兼容路径（无 `conversation_id`、只传 `history`）依然采信客户端历史。
> 这是有意的过渡安排，但**必须设定退出条件**——建议统计该分支的命中量，降到可忽略后删除。

### 💡 学习要点

**"校验"不能修补"错误的信任边界"。**
伪造的历史可以完全合法（结构、类型、配对都对），任何 schema 校验都拦不住它；
唯一有效的办法是**换权威源**。

---

## C2-3 · 请求前无上下文预算，无法裁剪

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 阅读 `agent.chat_stream`，确认 `messages` 只增不减 |
| **严重度** | 🟡 中（成本随轮次线性上升；长会话质量下降） |
| **状态** | ✅ 已实现（**19 条用例实测通过**） |
| **所属环节** | 上下文裁剪 |

### 🔍 现象

`chat_stream` 每轮把完整历史重发，**没有任何裁剪**。工具输出又恰好是体积大头
（JetBrains 实测约占一个 turn 的 **84%**），所以 token 成本随轮次单调上升。

### 🧪 证据

参考 [`../Investigation/上下文机制/02-L2-单会话上下文.md`](../Investigation/上下文机制/02-L2-单会话上下文.md)：

- 裸奔（无管理）的成本是"有管理"的两倍以上；
- **观察掩码**（把旧工具输出换成占位符）可降 **50%~57%** 成本，solve rate 持平或略升；
- 而 **LLM 摘要有反例**：Gemini 2.5 Flash（thinking）上 solve rate 从 40.4% 掉到 **31.4%**。

所以第一步不该是摘要，而是**掩码 + 窗口**。

### 🎯 根因

**上下文被当成"无限资源"，没有预算概念。**
此外还有一个技术性原因：`usage` 只有响应后才知道，而裁剪必须在请求前决定，
所以必须引入**估算**——很多人卡在"估算不准所以不做"，其实是把完美当成了前提。

### ✅ 解决方案

新增 `backend/core/agent/context.py`，三件事：

| 组件 | 作用 |
| :--- | :--- |
| `estimate_tokens` | 按字符数估算（刻意**高估**，宁可提前裁剪） |
| `mask_old_tool_results` | 保留最近 8 条工具输出原文，更早的换占位符。**只清 `content`，不删消息**（保住 `tool_call_id` 配对） |
| `trim_to_budget` | 超预算时从**最旧的非保留单元**丢弃；**单元 = assistant + 其后所有 tool**，绝不拆开配对；`keep_first=1` 保护开头的用户原始目标 |

两个可调参数走环境变量（非法值降级为默认，配置不该让服务起不来）：
`CONTEXT_BUDGET_TOKENS`（默认 8000）、`KEEP_RECENT_TOOL_RESULTS`（默认 8）。

### ✔️ 验证

`tests/rag/test_context.py` 19 条用例全通过，其中三条守住裁剪的安全性：

- `check_mask_keeps_pairing` —— 掩码后配对仍完整；
- `check_trim_respects_units` —— 裁剪不拆 assistant/tool；
- `check_trim_keeps_first_unit` —— 第一个单元（会话目标）永不被丢。

```powershell
cd backend
python tests\rag\run_tests.py --only test_context.py
# → 通过 19 / 失败 0 / 错误 0
```

### 💡 学习要点

**裁剪的难点不是"压多少"，而是"不能拆散配对"和"不能丢开头的目标"。**
前者是协议约束（会 400），后者是质量约束（会答非所问，且**不报错**）。

---

## C2-4 · 同会话并发写入会让 `tool_calls` 与 `tool` 错配

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16 |
| **发现方式** | 推演"两个标签页同时在同一会话发消息" |
| **严重度** | 🔵 低（单用户下罕见；但发生后数据不可读） |
| **状态** | ⚠️ 部分修复（进程内锁；多 worker 不覆盖） |
| **所属环节** | 并发控制 |

### 🔍 现象

服务端接管写权后，两个请求会同时往同一个 `conversation_id` 追加消息，
落库顺序可能变成：

```
user(A) → user(B) → assistant(B) → assistant(A)
```

回放时 `assistant(A).tool_calls` 后面跟的是 `assistant(B)` 的 tool 消息 → 配对错乱。

### 🎯 根因

**没有"会话级"的互斥概念。** 之前的历史由前端各自持有，不存在共享写入；
接管后引入了服务端共享状态，却没有同步引入互斥。

### ✅ 解决方案

进程内会话锁 + 明确的 409：

```python
_ACTIVE: set = set()
_ACTIVE_LOCK = threading.Lock()

if not _acquire(conversation_id):
    raise HTTPException(409, detail=f"会话 {conversation_id} 正在生成中，请等待上一轮完成")
```

锁在流的 `finally` 里释放，覆盖正常结束、异常与断流三种路径。

### ✔️ 验证

**未实测。** 该路径需要并发请求，本环境缺 `fastapi`（e2e 层全部 Skip）。

> **已知局限**：进程内锁只在**单进程**有效。本项目单用户、单 worker 部署足够；
> 若将来上多 worker，必须换成数据库行锁（如 `UPDATE conversations SET locked_at=... WHERE ...`）
> 或 Redis 锁，否则锁形同虚设。

### 💡 学习要点

**引入共享状态时，必须同时引入它的并发约束。**
否则问题不会立刻出现——只会在某次"两个标签页"时让数据变成不可读的形态。

---

## C2-5 · 写权移交期前后端双写，消息重复

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-16（**实施 B 的过程中发现**） |
| **发现方式** | 对照 `useChat.ts` 的 `finally` 块，确认"谁在写" |
| **严重度** | 🔴 高（每轮消息翻倍，会话结构被破坏） |
| **状态** | ✅ 已修复（tsc 通过） |
| **所属环节** | 写权归属 |

### 🔍 现象

后端开始落库后，前端**仍然**在 `finally` 里调用 `appendConversationMessages`：

```ts
if (convId !== null) {
  const items: ConversationMessageInput[] = []
  if (currentUser) items.push(toInput(currentUser))
  if (finalAssistant) items.push(toInput(finalAssistant))
  if (items.length > 0) await appendConversationMessages(convId, items)   // ← 与后端重复
}
```

结果：每轮对话在 `messages` 表里出现**两份** user 和两份 assistant。

### 🧪 证据

两条写路径并存：

| 写方 | 位置 | 触发 |
| :--- | :--- | :--- |
| 前端 | `useChat.ts` `finally` → `POST /conversations/{id}/messages` | 每轮结束 |
| 后端 | `chat.py` → `append_messages` | 用户消息：请求开始；助手消息：流结束 |

### 🎯 根因

**把"新增一个写方"当成了加法，没意识到它是"替换"。**
这也是我在 `05-落地方案` 里标注的风险点（"第 3 步是风险点：写权转移期间如果两边都在写，会重复"），
但实际实施时如果不主动核对前端，很容易漏掉。

### ✅ 解决方案

前端**停止写历史**，只保留刷新会话列表：

```ts
// 本轮消息落库已移交后端：POST /api/chat 在服务端读历史、裁剪，
// 并在流结束时把 user + assistant 写入 messages 表。
// 前端若再写一次就会产生重复消息（双写），所以这里只刷新会话列表。
if (convId !== null) {
  void refreshConversations()
}
```

连带清理：删除 `toInput` 函数、`appendConversationMessages` 与 `ConversationMessageInput` 的导入、
以及只服务于落库的 `userMsgId` / `assistantMsgId` 变量
（`tsconfig` 开了 `noUnusedLocals`，残留会直接报错——这一点反而帮我们确认清理干净了）。

### ✔️ 验证

```powershell
cd frontend
node node_modules/typescript/bin/tsc --noEmit -p tsconfig.app.json   # exit 0
node node_modules/oxlint/bin/oxlint src/hooks/useChat.ts src/lib/api.ts  # 0 errors
```

> **未实测**：没有跑一次真实对话确认"只写一份"。
> 建议手工验：发一条带工具调用的消息，再拉 `/api/conversations/{id}/messages`，
> 确认 roles 恰为 `["user", "assistant"]` 而不是四条。

### 💡 学习要点

**"转移写权"是一次替换，必须显式下线旧写方。**
这类问题的可怕之处是：功能看起来完全正常（对话能继续、历史能看），
只有去数一下库里的行数才能发现。

---

## C2-6 · 长会话没有压缩，token 随轮次单调上升

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-19 |
| **发现方式** | 接入掩码/窗口后复查：窗口丢弃的轮次模型就永远看不到了 |
| **严重度** | 🟡 中（长会话质量下降；窗口丢的是"结论"而不是"体积"） |
| **状态** | ✅ 已实现（**8 条压缩用例实测通过**） |
| **所属环节** | 上下文压缩 |

### 🔍 现象

掩码只能清掉旧工具输出，窗口只能把最旧的单元**丢掉**。一旦会话长到超预算，
早期已经得出的结论、决策、失败路径会被静默丢弃——模型只能重新查，或者直接答偏。
而且丢什么完全由"位置"决定，不是由"重要性"决定。

### 🎯 根因

**裁剪 ≠ 压缩。** 裁剪是"少给"（信息消失），压缩是"改写后仍然给"（保留结论）。
前者在预算压力下必然丢语义，后者才能让长会话保持连续。

### ✅ 解决方案

新增 `backend/core/agent/compaction.py`，在**轮次边界**（`chat.py` 组装本轮历史时）
做一次"摘要 + 保留最近 N 轮"：

```
prepare_history(rows, state)  →  info.pre_tokens
        │
        ├─ pre_tokens < 75% × 预算 → 不压缩，直接掩码 + 窗口
        │
        └─ 达到阈值 → maybe_compact():
             ① 锚点 = 会话第一条用户消息（原文保留，**不参与摘要**）
             ② 边界 = 倒数第 6 条用户消息（最近 6 轮保留原文）
             ③ 范围 = 锚点之后、边界之前、且未被上次摘要覆盖
             ④ transcript = 已有摘要 + 新增对话（工具结果截断到 400 字）
             ⑤ 调摘要模型 → 结构化摘要（recall 优先：目标/数值/决策/未决/失败路径）
             ⑥ 写 conversations.summary + summary_until_message_id + summary_anchor_message_id
             ⑦ 写 compactions 审计行（前后 token、覆盖区间、prompt 版本）
        │
        └─ 重新 prepare_history：锚点原文 + 摘要 system + 未覆盖的最近消息
              → 掩码 → 窗口（keep_first=2：锚点和摘要都不许裁）
```

**一次压缩的效果**（示意）：

| | 消息数 | 估算规模 |
|---|---|---|
| 压缩前（20 轮，含工具输出） | 约 60 条 | 约 12000 |
| 压缩后 | 锚点 1 + 摘要 1 + 最近 6 轮约 18 条 | 约 3500 |

**为什么锚点独立、为什么不把最近 N 轮也摘要**：

- 锚点是"原始目标与硬约束"，多层摘要后最容易漂移，必须原文保留；
- 最近 6 轮是"当前正在做的事"，摘要会丢掉刚刚查到的细节。

### ⚙️ 配置

| 环境变量 | 默认 | 作用 |
| :--- | :--- | :--- |
| `CONTEXT_COMPACTION` | `1` | `0` 关闭压缩（回退到掩码 + 窗口） |
| `CONTEXT_COMPACT_AT` | `0.75` | 触发阈值（预留摘要本身的 token） |
| `CONTEXT_KEEP_RECENT_TURNS` | `6` | 压缩后保留最近多少轮原文 |

失败不影响聊天：摘要模型报错/返回空时只记日志，本轮按未压缩继续。

### ✔️ 验证

```powershell
cd backend
python tests\rag\run_tests.py --only test_context.py
# → 结果：通过 27 / 失败 0 / 错误 0 / 跳过 0
```

其中 8 条专测压缩：

- `plan_scopes_old_turns` —— 划界正确（锚点之后、保留边界之前）；
- `plan_needs_enough_turns` —— 轮数不足不压缩；
- `transcript_includes_tool_results` —— 工具结果进摘要输入；
- `prepare_history_injects_summary` —— 锚点 + 摘要 + 最近消息的组装顺序；
- `maybe_compact_persists_summary_and_audit` —— 摘要/覆盖位置/锚点/审计行落库；
- `maybe_compact_respects_threshold` —— 未过阈值不动作；
- `maybe_compact_can_be_disabled` —— 开关生效；
- `second_compaction_needs_new_messages` —— 没有新消息不重复压缩。

### ⚠️ 已知取舍

| 取舍 | 说明 |
| :--- | :--- |
| **增量摘要的递归漂移** | 这次是"已有摘要 + 新增对话"，不是每次从原文重算。缓解：锚点独立 + `compactions` 留档（可回溯/重算） |
| **压缩延迟** | 首次压缩在请求前同步调用摘要模型（已丢线程池），可能多 1~3s；之后的请求不再压缩 |
| **仍需 A/B** | 摘要有反例（C2-3 证据：Gemini thinking −22.3%），所以默认阈值 75%、保留 6 轮，且可一键关闭 |
| **摘要质量依赖提示词** | 提示词版本记录在 `compactions.prompt_version`，改动会影响行为 |
| **撤回与压缩的交互** | 撤回会清空会话摘要（`turn_state.withdraw` 里置 NULL）：摘要可能包含被撤内容，不清空会经由摘要"复活"；代价是下次请求可能重新压缩 |

### 💡 学习要点

**压缩的关键不是"压多少"，而是"留什么"：锚点、最近 N 轮、结构化结论。**
以及一个容易被忽略的工程约束：**压缩本身也要花 token**，所以触发阈值不能设在窗口边缘。

---

## C2-7 · L1 单次调用：没有"稳定前缀"和"工具结果上限"

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-19 |
| **发现方式** | 对照 `Investigation/上下文机制/01-L1-单次调用上下文.md` 做 token 审计 |
| **严重度** | 🟡 中（缓存每天失效一次；一次大工具返回就能吃掉小半个窗口） |
| **状态** | ✅ 已实现（**8 条 L1 用例实测通过**） |
| **所属环节** | 单次请求组装 |

### 🔍 现象（审计数字）

同一轮请求的固定成本：

| 段 | 规模 | 说明 |
| :--- | :--- | :--- |
| system prompt | 约 **3170** 字符 | 模块常量，但**末尾拼了"当前日期"** |
| tools schema（24 个） | 约 **9231** 字符 | 每轮重复支付 |
| 历史 + 工具往返 | 随会话增长 | 本轮工具结果原样塞入，无上限 |

三个具体问题：

1. **system 里带日期**：提示词缓存按前缀命中，system 一变，后面的 tools + history
   全部失效——等于**每天**全量 miss 一次（不是每轮，但足以吃掉大部分收益）；
2. **工具结果无上限**：`search_knowledge(include_parent=True)` 一次可能上千 token，
   原样进 `messages` 后，这一轮后续全在挤窗口；
3. **没有请求级度量**：没人知道"这一轮到底花了多少、工具占了多少、缓存命中没有"。

### 🎯 根因

**把 L1 当成"把 messages 拼起来"，没有把它当预算分配。**
L2 的裁剪解决"历史太长"，但解决不了"这次请求的 system/tools/单条工具结果本身就很胖"。

### ✅ 解决方案

新增 `backend/core/agent/l1.py`，改三处：

**① 稳定前缀（缓存友好）**

```
system  = SYSTEM_PROMPT                # 纯常量，不含日期/用户名
user    = "[当前日期：2026-10-19（周日）]\n" + 用户原话
```

- 日期从 system 末尾移到**当前 user 消息开头**：它永远在最末，不在缓存前缀里；
- system + tools 在整个会话内**字节级稳定**，前缀缓存才有机会命中；
- 同时修正了 prompt 里"见系统消息末尾"的过期说法。

**② 工具结果截断（L1 与 L2 互补）**

| | 时机 | 做法 |
| :--- | :--- | :--- |
| **L1 截断** | 刚拿到工具结果时 + 回放历史时 | 单条超过 **4000** 字符 → 保留头 70% + 尾 30%，中间插入"已截断"标记 |
| **L2 掩码** | 结果变成历史之后 | 最近 8 条保留，更早的换成占位符 |

两处都改：`agent.py` 回填本轮 tool 消息时截断；`context.prepare_history` 对回放出的
最近 tool 消息也截断（否则历史里的大结果每次都会重新进上下文）。

**③ 度量**

- 每轮第一次请求打印一行：
  `[l1] 请求规模：system 3170 + tools 9231（24 个）+ 历史 24 + 工具往返 0 = 12425 字符`
- 可选 usage：`L1_INCLUDE_USAGE=1` 时请求 `stream_options.include_usage`，日志输出
  `prompt / completion / cached / 命中率`。**默认关闭**：中转层不一定支持，开了可能 400；
  代码里做了"失败一次自动回退"。

### ⚙️ 配置

| 环境变量 | 默认 | 作用 |
| :--- | :--- | :--- |
| `L1_TOOL_RESULT_MAX_CHARS` | `4000` | 单条工具结果上限（<=0 关闭截断） |
| `L1_INCLUDE_USAGE` | `0` | 是否请求 usage（验证中转支持后再开） |

### ✔️ 验证

```powershell
cd backend
python tests\rag\run_tests.py --only test_l1.py test_agent_events.py test_context.py
# → 结果：通过 50 / 失败 0 / 错误 0 / 跳过 1
```

8 条 L1 用例：

- `system_prompt_is_stable` —— system 是常量、不含日期、多次调用一致；
- `user_message_carries_date` —— 日期只在当前 user 消息里；
- `short_tool_content_untouched` / `long_tool_content_keeps_head_and_tail` —— 截断边界；
- `truncate_only_touches_tool_messages` —— 不动 assistant / tool_call_id；
- `prepare_history_truncates_recent_tools` —— 回放路径也截断；
- `request_stats_segments` / `usage_parsing` —— 度量口径。

### ⚠️ 已知取舍

| 取舍 | 说明 |
| :--- | :--- |
| **日期在 user 消息里** | 模型可能把日期当成用户原话的一部分；影响可接受，换来稳定前缀 |
| **截断有损** | 被截掉的中间段模型看不到；但它知道"被截断"，可以重新调工具 |
| **usage 默认关闭** | 中转是否透传 `stream_options` / 缓存字段无法离线验证，需真实链路确认 |
| **tools 仍是全量 24 个** | 9231 字符固定成本；24 个仍在"全量枚举"舒适区，暂不引入 tool RAG |
| **`prompt_cache_key` 未设置** | 中转是否支持未知；先靠"顺序稳定 + 去动态内容"拿到自动前缀缓存 |

### 💡 学习要点

**L1 的优化顺序是：先让前缀稳定（几乎零成本），再给单条结果设上限，最后才谈度量。**
顺序反过来（先上缓存字段、先上工具检索）通常是在为一个还没稳定的前缀做昂贵优化。

---

## 通用经验

| # | 原则 | 对应问题 |
| :--- | :--- | :--- |
| 1 | **配对约束必须由单一数据源保证，不能两侧各自过滤** | C1-1 |
| 2 | **"未完成"必须在写入口收敛成终态**，不能靠调用方自觉 | C1-2 |
| 3 | **持久化与上下文组装不是同一件事，但必须共用同一份历史** | C2-1 |
| 4 | **校验不能修补错误的信任边界**——伪造的历史在结构上完全合法 | C2-2 |
| 5 | **裁剪有两个约束：不拆配对（协议）、不丢开头目标（质量）** | C2-3 |
| 6 | **引入共享状态必须同时引入并发约束** | C2-4 |
| 7 | **转移写权是替换而非加法，旧写方必须显式下线** | C2-5 |
| 8 | **估算不完美不是不做的理由**——`usage` 请求后才有，裁剪必须在请求前决定 | C2-3 |
| 9 | **压缩要留三样东西：锚点、最近 N 轮、结构化结论**；压缩本身也要吃 token | C2-6 |
| 10 | **L1 先让前缀稳定，再给单条结果设上限，最后才谈度量** | C2-7 |

> 第 8 条值得单独强调：很多团队卡在"token 估算不准所以不敢裁剪"，
> 但裁剪的价值来自数量级差异（工具输出占 84%），估算误差 ±30% 根本不影响结论。

---

## 未做与后续

| 项 | 说明 | 触发条件 |
| :--- | :--- | :--- |
| **前端 `buildHistory` 修复或删除** | C1-1 的缺陷仍在兼容路径生效 | 兼容路径下线时一起删；或单独修 |
| **压缩的 A/B 评测** | C2-6 已实现，但摘要有反例（Gemini thinking −22.3%），需要真实长会话样本对比"掩码+窗口"与"+压缩" | 有 20 轮以上的真实会话样本 |
| **`client_msg_id` 幂等** | 客户端重发会让用户消息重复入库。**刻意没加**——不在本次范围，且"声明未实现"比"不声明"更糟 | 出现真实重发场景 |
| **多 worker 的会话锁** | 现在是进程内锁 | 部署改为多 worker |
| **兼容路径下线** | 无 `conversation_id` 的 `history` 分支仍在 | 统计命中量降到可忽略 |

---

## 验证汇总

```powershell
cd backend
python tests\rag\run_tests.py --only test_l1.py test_context.py test_api_e2e.py test_agent_events.py test_tool_contract.py
# → 结果：通过 77 / 失败 0 / 错误 0 / 跳过 11
```

**全量套件**仍会因既有的环境问题报错（与本次改动无关）：
`yaml` 未安装（`ModuleNotFoundError: No module named 'yaml'`）+ 临时目录不可写
（`PermissionError: ...\Temp\dsh-...\config.yaml`）。集中在 RAG 的
`test_evidence` / `test_engine` / `test_calibration_eval` 三个文件。

**前端**

```powershell
cd frontend
node node_modules/typescript/bin/tsc --noEmit -p tsconfig.app.json   # exit 0
node node_modules/oxlint/bin/oxlint src/hooks/useChat.ts src/lib/api.ts  # 0 errors
```

### 本环境无法验证的三件事（必须如实记录）

| 项 | 阻塞原因 |
| :--- | :--- |
| e2e：`/api/chat` 服务端读库→落库 | `fastapi` 未安装 → `TestClient` 不可用 |
| e2e：落库后回放配对 | 同上；且 `backend/db/` 不可写（`attempt to write a readonly database`） |
| 前端：真实断流路径 | 需要浏览器操作 |

这三项建议在具备 fastapi 与本机可写数据库的环境里补跑，命令已在上面列出。

---

## 附：文档修订记录

| 日期 | 版本 | 变更 |
| :--- | :--- | :--- |
| 2026-10-16 | v1.0 | 首次建立本模块：7 条问题（C1-1、C1-2、C2-1 ~ C2-5），含服务端 `replay.py` / `context.py`、`chat.py` 契约改造与前端写权移交 |
