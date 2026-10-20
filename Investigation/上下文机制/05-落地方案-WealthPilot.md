# 落地方案 · WealthPilot

> 把 [README](README.md) 的四层结论映射到本仓库的实际代码，给出分阶段实施路径。
> **核心判断：先补 L1 的两个前置件，再做 L2 的掩码，最后才考虑摘要。**

---

## 1. 现状复盘：三个前置缺口

在写任何裁剪算法之前，必须承认三个结构性事实：

### 缺口 ① `/api/chat` 无状态，没有 `conversation_id`

[`chat.py:11-13`](../../backend/api/routes/chat.py#L11-L13)：

```python
class ChatRequest(BaseModel):
    message: str
    history: list          # ← 历史由前端传
```

后端不知道：

- 这段历史属于哪个会话 → 无法按会话隔离策略；
- 这段历史是否完整（前端可能已截断）→ 无法判断“丢了多少”；
- 会话总 token 量 → 无法做预算。

**后果：只要保持这个形态，L2 就做不完整。**

### 缺口 ② 历史存储是 UI 形态，不能直接回放给模型

[`conversations.py:109`](../../backend/core/services/conversations.py#L109)：

> 仅接受 user / assistant 两种角色——tool 中间态已内嵌在 assistant.tool_calls。

直接映射成 `messages` 会**缺 `role="tool"` 消息** → OpenAI 协议 400。
**必须有一层「回放展开器」。**

### 缺口 ③ `summary` 字段预留未用

`conversations.summary`（[`schema.py:150`](../../backend/core/database/schema.py#L150)）
已经建好，但 [`conversations.py:8`](../../backend/core/services/conversations.py#L8) 明确写着
“现阶段预留，不做归档/摘要”。

好消息：**表结构不用改**，将来落摘要时直接写这个字段。

---

## 2. 分阶段路线

| 阶段 | 内容 | 依赖 | 工作量 | 风险 |
| :--- | :--- | :--- | :--- | :--- |
| **P0** | L1 加固：历史回放展开器 + 工具结果字段裁剪 + 缓存验证 | 无 | 1 天 | 低 |
| **P1** | L2 观察掩码 + 锚点保留 | P0 | 1 天 | 低 |
| **P2** | 服务端接管历史（chat 收 `conversation_id`） | 前后端联调 | 2 天 | 中 |
| **P3** | L2 compaction（阈值触发 + 摘要） | P1、P2 + A/B | 2 天 | 中高 |
| **P4** | L3 偏好笔记（轻量） | P0 | 1 天 | 低 |

> **P0 与 P2 的细节见 [06-前置件详解.md](06-前置件详解.md)**——这两件不是"顺手加个函数/字段"，
> 各自都有需要先想清楚的边界情况。

**注意 P3 的依赖**：摘要必须基于**服务端可信历史**（P2）才能做对。
如果历史仍由前端传，你无法知道摘要该覆盖哪一段。

---

## 3. P0 · L1 加固（两个前置件）

### 3.1 历史回放展开器（**最高优先级**）

这是 L2 的地基，也和已有的工具协议约束同源。

**完整设计、代码骨架与测试计划见 [06-前置件详解.md](06-前置件详解.md) 第一部分**，
这里只留结论。

四条必须同时满足的不变量（缺一个就 400 或语义错乱）：

| 不变量 | 要求 |
| :--- | :--- |
| **I1** 配对完整 | 每个 `tool_calls[i].id` 恰好对应一条 `tool` 消息 |
| **I2** 无孤儿 | 每条 `tool` 消息的 `tool_call_id` 必须能在前一条 assistant 的 `tool_calls` 里找到 |
| **I3** 类型正确 | `arguments` 必须是 **JSON 字符串**（UI/DB 里存的是对象） |
| **I4** 缺失必补 | 没有 `result` 时**合成**一条"未完成"的 tool 消息，不能省略 |

> ⚠️ I1/I2 必须由**同一个过滤后的列表**驱动。两侧分别过滤就会产生孤儿 tool 消息——
> 这正是前端 `buildHistory` 现有实现的一个真实漏洞（详见 06 §1.4 的漏洞 B）。
>
> 同样地，缺 `result` 时不能输出 `"null"`，而要输出可读的“未完成”提示（漏洞 A）。

**为什么 P0 就做、不等 P2 一起**：它是纯函数，可独立测试、接线前零行为变更，
而 P2 的服务端组装**必须**调用它。

**验收**：拿一个含工具调用的真实会话，`expand_to_model_messages` 的结果直接喂给
`client.chat.completions.create` 不报 400。

### 3.2 工具结果字段裁剪

`search_knowledge` 的 `include_parent=True` 是唯一可能爆量的口子
（schema 描述里写了“体积较大”，但**没有代码层面的硬上限**）。

```python
# backend/core/tools/tools_hub/rag_tools/impl.py

#: 单条证据 parent 全文的硬上限（字符）。超了就截断并留标记。
_MAX_PARENT_CHARS = 2000

def _clip_parent(text: str) -> str:
    if len(text) <= _MAX_PARENT_CHARS:
        return text
    return text[:_MAX_PARENT_CHARS] + f"\n…（已截断，原文 {len(text)} 字符）"
```

**为什么放 L1**：它不需要任何跨轮状态，改完立刻生效，风险最低。

### 3.3 验证缓存是否真的生效

本项目走 OpenAI 兼容中转（`base_url=os.getenv("LLM_URL")`），
**中转很可能静默丢弃缓存字段**（见 [01-L1 §2.3 陷阱 5](01-L1-单次调用上下文.md)）。

```python
# 一次性诊断：连发两次相同前缀的请求，看 cached_tokens
resp = client.chat.completions.create(model="deepseek-flash", messages=messages)
details = getattr(resp.usage, "prompt_tokens_details", None)
print("cached_tokens =", getattr(details, "cached_tokens", None))
```

**判读**：

- `> 0` → 中转支持，可以继续优化前缀顺序；
- `= 0` 且无报错 → **被中转吞了**，这条优化暂时放弃，把精力放到截断上。

---

## 4. P1 · L2 观察掩码（建议最先做的 L2 策略）

### 4.1 为什么先做掩码，不做摘要

依据 [02-L2 §4](02-L2-单会话上下文.md) 的实测：

| 策略 | 成本 | solve rate |
| :--- | :--- | :--- |
| 观察掩码 | **−50% ~ −57%** | 持平或略升 |
| LLM 摘要 | −41% ~ −55% | **可能大幅下降**（Gemini thinking 上 −22.3%） |

掩码**不需要额外模型调用**、不引入摘要失真这个新故障源，
而工具输出又恰好是体积大头（JetBrains 实测约 84%）。
**这是唯一"几乎不会更差"的策略。**

### 4.2 实现骨架

```python
# backend/core/agent/context.py（新增）

#: 保留最近多少轮的完整工具输出，更早的替换成占位符。
#: 取 6 的理由：本项目一次财务咨询通常 2~4 轮工具调用，
#: 6 轮足以覆盖"当前正在处理的任务"，更早的细节已进摘要或可重新查询。
KEEP_RECENT_TURNS = 6

_MASKED = "[较早的工具输出已省略，如需重新获取请再次调用该工具]"


def mask_old_tool_results(messages: list[dict], keep: int = KEEP_RECENT_TURNS) -> list[dict]:
    """把较早的 tool 消息内容替换成占位符（观察掩码）。

    只掩码**输出内容**，保留 assistant 的 tool_calls（动作）：
    模型仍然知道"我在第 3 步用 query=X 查过知识库"，只是看不到那 15k 原文。
    这样既省 token，又不破坏"我做过什么"的连续性。

    注意：**必须保留 role 与 tool_call_id**——只清空 content。
    直接删掉 tool 消息会让 tool_calls 失去配对，触发 API 400。
    """
    # 从后往前数 tool 消息，保留最近的 keep*2 条（一轮可能有并行调用）
    tool_idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
    if len(tool_idx) <= keep:
        return messages

    masked = [dict(m) for m in messages]
    for i in tool_idx[:-keep]:
        masked[i]["content"] = _MASKED
    return masked
```

**三个必须守住的点**：

1. **只清 content，不删消息**——`tool_call_id` 必须还在；
2. **不掩码最近 N 轮**——当前任务正在用的结果不能动；
3. **占位符要可读**——告诉模型“这里原本有内容、能重新取”，而不是留个空字符串。

### 4.3 锚点保留

纯掩码/截断会丢早期目标。用一个独立字段锚定：

```python
#: 会话目标的锚定文本。从第 1 轮抽取，永不参与滚动/摘要。
ANCHOR_PREFIX = "【会话目标（锚定，原文）】"


def build_anchor(first_user_message: str) -> dict:
    """把用户最初的目标与约束固定成一条 system-ish 消息，永久保留在历史最前。"""
    return {
        "role": "user",
        "content": f"{ANCHOR_PREFIX}{first_user_message}",
    }
```

组装顺序：

```
system
anchor（原始目标，永不滚动）
… 掩码后的历史 …
最近 N 轮原文
当前 user 输入
```

**为什么不放 system**：system 应当是稳定不变的（缓存友好）；锚点每个会话不同，
放 user 角色既不影响缓存前缀，也不会被模型当成新指令。

---

## 5. P2 · 服务端接管历史

这是让 L2 从“能做一点”变成“能做对”的转折点。

### 5.1 接口改动

```python
class ChatRequest(BaseModel):
    conversation_id: int | None = None   # 新会话为 None，后端负责创建
    message: str
    # history 保留但标记 deprecated，兼容旧前端
```

### 5.2 服务端流程

```
POST /api/chat
  → 若 conversation_id 为空：创建会话
  → 读 messages 表 → expand_to_model_messages()   ← P0 的展开器
  → mask_old_tool_results() + 插入 anchor          ← P1 的掩码
  → 预算检查（token 超限 → 触发 P3 压缩）
  → chat_stream(messages)
  → 每轮结束 append_messages()（仍是 UI 形态，供前端折叠）
```

**关键**：存储保持 UI 形态（前端要折叠），**回放时才展开**。
两种形态各自最优，不必二选一。

### 5.3 验收

| 检查项 | 方法 |
| :--- | :--- |
| 同一会话第 2 轮的 input tokens 不应比第 1 轮翻倍 | 读 `usage` |
| 回放历史后 API 不报 400 | 真实会话跑通 |
| 前端折叠显示不受影响 | 手工验证 |

---

## 6. P3 · Compaction（**必须 A/B 后才上**）

> **状态（2026-10-19）**：已实现，见 `backend/core/agent/compaction.py` 与
> [`../../问题文档/Agent-上下文管理.md`](../../问题文档/Agent-上下文管理.md) 的 C2-6。
> A/B 评测仍是待办（见该文「未做与后续」）。

只有在 P1+P2 之后 token 仍在单调上升才做。

### 6.1 触发条件

```python
#: 触发压缩的窗口占用比。取 0.75 而非 0.95：
#: 压缩本身要调一次摘要模型，也要吃 token；且要留余量给模型作答。
COMPACT_AT = 0.75

#: 压缩后保留最近多少轮原文（JetBrains 实验用 M=10，本项目取小一些）
KEEP_RECENT_AFTER_COMPACT = 6
```

### 6.2 两条硬规则

1. **只在轮次边界压缩**——不要在工具循环中间压缩，否则会抹掉“刚查到什么”；
2. **锚点不参与摘要**——原始目标与硬约束永久独立保留（[02-L2 §6.3](02-L2-单会话上下文.md)）。

### 6.3 摘要提示词（按“先 recall 后 precision”）

```text
请把以下对话压缩为结构化摘要，供后续推理使用。必须保留：

1. 用户的原始目标与所有约束（逐条列出，不要概括、不要合并）
2. 已确认的事实与数值（保持精确，不要取整、不要换算）
3. 做过的决策及其理由
4. 未解决的问题 / 待办
5. 试过但失败的路径（各一句话，说明为什么失败）

可以丢弃：冗长的工具返回原文、重复的寒暄、已经得出结论的中间讨论。

输出用 Markdown 分节。
```

### 6.4 必须记录的元数据

| 字段 | 用途 |
| :--- | :--- |
| 压缩发生的轮次 | 定位“从哪一轮开始变笨” |
| 压缩前 token 数 | 判断是否为窗口压力所致 |
| 压缩前后完整内容 | 可回放、可对比 |
| 提示词版本 | 提示词一改行为就变 |

落一张 `compactions` 表即可。**没有这些记录，线上问题无法归因。**

---

## 7. P4 · L3 轻量偏好笔记

采用 [03-L3 §8](03-L3-跨会话记忆.md) 的结论：**主体走已有结构化表，只补“非结构化偏好”**。

```python
# backend/core/database/schema.py 新增

CREATE TABLE IF NOT EXISTS user_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1,
    category    TEXT    NOT NULL,          -- preference / constraint / fact
    content     TEXT    NOT NULL,
    source      TEXT,                      -- user_stated / model_inferred
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    expires_at  TEXT                       -- NULL = 永久
);
```

**三条设计约束**：

1. `category` 用**白名单**（不让模型自由发挥），且只有 `preference` / `constraint` / `fact` 三类；
2. `source = "model_inferred"` 的条目**不进 prompt**——防止模型推测变成长期偏见；
3. `expires_at` 必须支持——有些偏好是有时效的。

**不做的事**：不引入 Mem0 式抽取管线。你们的财务事实是用户填的，抽取只会引入误差。

---

## 8. 度量与验收

### 9.1 必须埋的点

| 指标 | 位置 | 期望 |
| :--- | :--- | :--- |
| 每轮 input tokens | `agent.py` 的 `usage` | **趋稳**，不单调上升 |
| 掩码触发数 / 掩码掉的字符数 | `mask_old_tool_results` | 与工具调用量成正比 |
| 压缩次数 / 会话 | 压缩入口 | 与轮次比值，过高说明裁剪太轻 |
| 缓存命中率 | `usage.prompt_tokens_details` | > 0.8（若中转支持） |
| 成本 / 会话 | 累计 token × 单价 | 下降 |

### 9.2 遗忘探针（**最该先建的基础设施**）

没有它，你无法知道裁剪有没有伤到记忆。

固定 5~10 条长会话回归集，每条结构：

```
第 1 轮：埋一个关键约束（"不接受股票类产品"）
第 2~20 轮：正常财务咨询 + 工具调用
第 21 轮：问一个只有记住该约束才能答对的问题
         （"这个方案里配 30% 股票基金行吗？"）
期望：拒绝，并引用最初的约束
```

**每条会话都要在第 1、10、20、21 轮各跑一次**，观察“早期信息保留率”随轮次的衰减曲线。
这条曲线就是 L2 的验收指标。

---

## 9. 明确不做的事（有依据）

| 不做 | 依据 |
| :--- | :--- |
| **一上来就上 LLM 摘要** | JetBrains 实测摘要有反例（Gemini thinking −22.3%），而掩码几乎免费；顺序应该是掩码 → 窗口 → 摘要 |
| **引入 Mem0 式记忆抽取** | 你们的财务事实是**用户结构化填写**的，抽取只会引入误差（[03-L3 §2.2](03-L3-跨会话记忆.md)） |
| **上历史 RAG（把历史向量化）** | 引入召回失败这一新错误源，且对“全局摘要类”问题不友好；L2 早期不需要 |
| **子代理架构** | 你们的任务是单用户单对话，无法并行分解，收益抵不上复杂度 |
| **追求"大窗口就够"** | Context Rot 实测：**focused ~300 tokens 显著优于 full 113k tokens** |

---

## 10. 一句话总结

> **先做「回放展开器」和「工具结果掩码」这两件小事——它们成本最低、风险最小、
> 覆盖的收益最大。摘要、记忆都是后面的事。**

顺序一旦反过来（先上摘要、先上记忆），就会在**没有可信历史**的地基上
叠最复杂的机制，返工成本极高。
