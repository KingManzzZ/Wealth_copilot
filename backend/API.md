# WealthPilot 后端接口文档

> 版本：v0.6 ｜ 更新日期：2026-09-24
> 适用范围：本文档描述后端 API 契约。v0.4 按"全表拆分"重构，原 `/api/profile` 聚合端点废弃，按子资源拆为 6 组 RESTful 端点；v0.6 启用 conversations/messages 两张表，新增 GPT 式多会话管理。
> 前端本地推导逻辑见 `frontend/src/lib/profile.ts`，前端类型定义见 `frontend/src/types/index.ts`。

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 接口前缀 | `/api`（开发环境 Vite dev proxy 转发至 `http://localhost:8000`） |
| 数据格式 | JSON，UTF-8；金额为数字类型（人民币元），非字符串 |
| 时间格式 | ISO 8601，秒级，如 `2026-09-22T10:30:00`；纯日期字段如 `2031-09-16`；月份字段 `YYYY-MM` |
| 单用户模式 | 用户归属固定 `OWNER_ID = 1`，前端无需传 `user_id`，多用户扩展时再引入 |
| 字段「已填写」判定 | 字符串：去空白后非空；数值：大于 0；对象/数组：长度大于 0；null/缺失：未填写 |

## 2. 用户数据模块（拆表后）

按关系建模拆为 6 张表，每张表对应一组 RESTful 端点。原 `/api/profile` 聚合端点已废弃。

### 2.1 接口列表

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/basic_info` | 取当前用户基本信息（未创建时返回默认值） |
| POST | `/api/basic_info` | UPSERT 基本信息（仅接受白名单字段，整体覆盖） |
| GET | `/api/assets` | 列出全部资产（`{name: value}` 字典） |
| POST | `/api/assets` | UPSERT 单条资产 body `{name, value, asset_type?}` |
| DELETE | `/api/assets/{name}` | 删除单条资产 |
| GET | `/api/debts` | 列出全部债务（`{name: {remaining, monthly_payment, annual_rate}}` 字典） |
| POST | `/api/debts` | UPSERT 单条债务 body `{name, remaining, monthly_payment, annual_rate?}` |
| DELETE | `/api/debts/{name}` | 删除单条债务 |
| GET | `/api/goals` | 列出全部目标（`FinancialGoal[]`） |
| POST | `/api/goals` | UPSERT 单条目标 body `FinancialGoal`（按 id 查重） |
| DELETE | `/api/goals/{goal_id}` | 删除单条目标 |
| GET | `/api/cashflow?months=6` | 取最近 N 个月的月度现金流汇总 |
| POST | `/api/cashflow` | UPSERT 某月汇总 body `{month, income, expense}` |
| GET | `/api/transactions?month=YYYY-MM` | 取交易流水（可按月过滤） |
| POST | `/api/transactions` | 新增单条交易流水 |
| DELETE | `/api/transactions/{id}` | 删除单条交易 |
| GET | `/api/dashboard` | 仪表盘聚合数据（KPI/现金流/目标/资产负债派生 + 5 维健康度） |
| GET | `/api/dashboard/health-detail` | 健康度详情：每维原始值/目标/状态（供前端抽屉） |
| GET | `/api/conversations` | 会话列表（历史记录，按 updated_at 倒序） |
| POST | `/api/conversations` | 创建会话（首条消息发出时惰性调用） |
| GET | `/api/conversations/{id}/messages` | 懒加载某会话全部消息 |
| POST | `/api/conversations/{id}/messages` | 批量追加消息（每轮对话一次写入） |
| DELETE | `/api/conversations/{id}` | 删除会话（messages 级联清除） |

### 2.2 基本信息（basic_info 表）

单用户模式下只维护一行（`id = user_id = OWNER_ID = 1`）。

| 字段 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `user_id` | string | 用户归属标识。服务端固定返回 `"user-1"` | `"user-1"` |
| `name` | string | 姓名 | `"学习者"` |
| `age` | number | 年龄（岁） | `29` |
| `city` | string | 所在城市 | `"上海"` |
| `occupation` | string | 职业 | `"后端工程师"` |
| `industry` | string | 所属行业 | `"互联网"` |
| `education` | string | 教育程度（自由文本） | `"硕士"` |
| `family` | string | 家庭状况（综合性描述） | `"单身"` |
| `risk_tolerance` | enum | 风险承受能力（算法估算，不可用户修改） | `"balanced"` |
| `investment_preference` | enum | 投资偏好（算法估算，不可用户修改） | `"balanced"` |
| `investing_years` | number | 投资年限（年） | `5` |
| `liquidity_needs` | enum | 活期现金需求（算法估算，不可用户修改） | `"medium"` |
| `marital_status` | string | 婚姻状况 | `"未婚"` |
| `children` | string | 子女情况 | `"暂无"` |
| `supporting_parents` | string | 赡养父母情况 | `"需赡养父母一方"` |
| `last_updated` | string | 服务端写入时间，客户端只读 | `"2026-09-23T10:30:00"` |

> **不再包含**：`monthly_income` / `monthly_expense`（已从 `transactions` 按月派生）；`assets` / `debts` / `goals`（拆为独立接口）。

### 2.3 资产（assets 表）

字典结构：键为资产名（中文允许），值为当前估值（元）。

- GET 响应：`{ "现金及活期": 80000, "基金": 30000 }`
- POST body：`{ "name": "现金", "value": 80000, "asset_type": "cash" }` —— UPSERT 语义，同名则更新
  - `asset_type` 可选：`cash` / `investment` / `property` / `other`；不传时保留原类型（新记录按名称推断）
- DELETE `/api/assets/{name}`：路径参数需 URL encode；不存在返回 404

### 2.4 债务（debts 表）

字典结构：键为债务名，值为对象 `{ remaining, monthly_payment, annual_rate }`。

- GET 响应：`{ "房贷": { "remaining": 800000, "monthly_payment": 6500, "annual_rate": 0.045 } }`
- POST body：`{ "name": "房贷", "remaining": 800000, "monthly_payment": 6500, "annual_rate": 0.045 }`
  - `annual_rate` 可选：年利率**小数**（`0.05` = 5%）；不传时保留原值
- DELETE `/api/debts/{name}`：路径参数需 URL encode

### 2.5 财富目标（goals 表）

列表结构：每个元素为一个 `FinancialGoal` 对象。

| 字段 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `id` | string | 前端用的字符串 id（如 "g1"）；后端按此查重 | `"g1"` |
| `name` | string | 目标名称 | `"二套房首付"` |
| `target` | number | 目标金额（元） | `500000` |
| `current` | number | 当前已积累金额（元） | `130000` |
| `deadline` | string | 截止日期 | `"2031-09-16"` |
| `priority` | enum | 优先级 `high` / `medium` / `low` | `"high"` |
| `created_at` | string | 创建时间（健康度算"按时间应达进度"用） | `"2026-04-01 10:00:00"` |

- POST body：完整 `FinancialGoal` 对象，按 `id` UPSERT
- DELETE `/api/goals/{goal_id}`

### 2.6 月度现金流（派生视图，**无独立表**）

| 字段 | 类型 | 说明 |
|---|---|---|
| `month` | string | `YYYY-MM` 格式，取自流水的 `occurred_at` |
| `income` | number | 当月收入流水合计（元） |
| `expense` | number | 当月支出流水合计（元） |
| `surplus` | number | 派生字段：`income - expense`，**不存储** |

- GET `/api/cashflow?months=6`：取最近 N 个**有流水**的月份（默认 6，上限 36），按月份升序返回
- **没有写接口**。月度汇总由 `transactions` 按月聚合派生（`core/services/cashflow.py`）；
  要改数字只能写逐笔流水（`POST /api/transactions`）。
  `POST /api/cashflow` 与 `set_monthly_cashflow` 工具已于 v0.5 下线——
  两套写入口会让"记了流水"与"月结余不变"同时成立（BK-27 / G-11）。

> dashboard 的 KPI（monthly_income / monthly_expense / monthly_surplus / saving_rate）
> 取**最近一个有流水的月份**聚合结果。

### 2.7 交易流水（transactions 表）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | number | 服务端自增 id |
| `transaction_type` | enum | `income` 或 `expense` |
| `amount` | number | 金额（元，必须 > 0） |
| `category` | string | 收支分类（薪资/餐饮/交通/房贷...） |
| `source` | string | 来源/去向（具体说明，可空） |
| `occurred_at` | string | 发生时间，ISO 8601 |
| `note` | string | 备注，可空 |

- GET `/api/transactions` 或 `/api/transactions?month=2026-09`：按月过滤，按时间倒序返回
- POST body：`{ "transaction_type": "expense", "amount": 200, "category": "餐饮", "source": "午餐", "occurred_at": "2026-09-23T12:00:00" }`
- DELETE `/api/transactions/{id}`

### 2.8 枚举值与量化标准

> **算法估算字段**：`risk_tolerance` / `investment_preference` / `liquidity_needs` 三个字段由系统算法自动估算，用户不可通过 tool 或前端修改。

**`risk_tolerance` 风险承受能力**：

| 取值 | 展示文案 | 量化标准 |
|---|---|---|
| `conservative` | 保守型 | 权益类资产占比 ≤ 20%，可承受最大回撤约 10% |
| `balanced` | 平衡型 | 权益类资产占比 40%~60%，可承受最大回撤约 20% |
| `aggressive` | 进取型 | 权益类资产占比 ≥ 70%，可承受最大回撤约 30% |

**`investment_preference` 投资偏好**：

| 取值 | 展示文案 | 量化标准 |
|---|---|---|
| `conservative` | 保守型 | 偏好固定收益类，权益类占比 ≤ 30% |
| `balanced` | 平衡型 | 均衡配置，权益类占比 30%~60% |
| `aggressive` | 进取型 | 偏好成长型，权益类占比 ≥ 60% |

**`liquidity_needs` 活期现金需求**：

| 取值 | 展示文案 | 需覆盖月数 |
|---|---|---|
| `low` | 较低 | 3 个月支出 |
| `medium` | 中等 | 6 个月支出 |
| `high` | 较高 | 12 个月支出 |

**`goals[].priority` 目标优先级**：`high` 高 ｜ `medium` 中 ｜ `low` 低（前端展示大写中文）。

### 2.9 字段填写要求分级

| 分级 | 字段 | 说明 |
|---|---|---|
| **必返** | `basic_info.user_id`、`basic_info.last_updated`、`assets`、`debts`、`goals` | 响应中始终存在；无数据时分别为 `"default"`、`""`、`{}`、`{}`、`[]` |
| **数值/字符串兜底** | basic_info 其余全部字段 | 未填写时返回 `0` / `""`（而非 `null`） |
| **完善度清单（10 项）** | 姓名、年龄、城市、职业、行业、教育程度、风险等级、投资年限、活期现金需求、婚姻状况、子女情况、赡养父母情况 | 资料完整度 = 已填写项数 ÷ 10 |

> v0.4 起 `monthly_income` / `monthly_expense` **不再属于完善度清单**，因为它们从 cashflow 派生而非用户填写字段。

### 2.10 Dashboard 聚合接口

`GET /api/dashboard` 返回复合结构，由后端聚合各子 service 派生：

```json
{
  "kpis": { "monthly_income": ..., "monthly_expense": ..., "monthly_surplus": ..., "saving_rate": 0.472 },
  "cashflow": [...],              // 最近 6 个月的 MonthlyCashflow
  "goals": [...],                 // 全部 FinancialGoal
  "asset_liability": [...],       // 资产+债务合并列表
  "health": {                     // 5 维财务健康度（实时计算，见 services/wealth_health.py）
    "overall": 69,
    "grade": "C",
    "dimensions": [
      { "name": "储蓄率", "score": 100, "advice": "..." },
      { "name": "应急储备", "score": 28, "advice": "..." },
      { "name": "负债健康", "score": 72, "advice": "..." },
      { "name": "目标进度", "score": 100, "advice": "..." },
      { "name": "资产配置", "score": 100, "advice": "..." }
    ],
    "data_quality": "full"        // full / partial / insufficient
  }
}
```

`health` 规则：储蓄率 25% + 应急 20% + 负债 25% + 目标 15% + 资产配置 15%；
缺失维度剔除后重新归一化；低于 40 分的维度每个扣 10 分（上限 30）；
分级 A≥85 / B≥70 / C≥55 / D<55。详见 `Investigation/财务健康度/01-指标与算法调研.md`。

**`GET /api/dashboard/health-detail`**：结构同 `health`，但每个维度多一个 `metrics`：

```json
{
  "overall": 69,
  "grade": "C",
  "data_quality": "full",
  "generated_at": "2026-10-28T22:25:15",
  "dimensions": [
    {
      "key": "emergency",
      "name": "应急储备",
      "score": 28,
      "advice": "...",
      "metrics": [
        {
          "label": "现金类资产可覆盖",
          "value": "1.7 个月",
          "target": "≥ 6 个月",
          "status": "bad",
          "note": "现金类 6.0 万 ÷ 月支出 3.6 万"
        }
      ]
    }
  ]
}
```

`status` 取值 `good` / `warn` / `bad`，只用于前端颜色；`/api/dashboard` 不返回 `metrics`。

KPI 派生规则：取**最近一个有流水的月份**，`income` / `expense` 为该月流水按类型求和，`surplus = income - expense`，`saving_rate = surplus / income`（income 为 0 时置 0）。没有任何流水时四项全为 0。

### 2.11 会话（conversations / messages 表）

GPT 式多会话：会话行**惰性创建**（发出首条消息时才建，空会话不落库）；前端用 localStorage 记住"当前停留在哪个会话 id"，聊天内容全部以后端为真相源。

**conversations**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | number | 自增 id |
| `title` | string \| null | 会话标题；为空时由首条 user 消息截取前 20 字自动补 |
| `updated_at` | string | 最近一次追加消息时间（列表排序依据） |
| `summary` | string \| null | 长会话压缩后的结构化摘要（见 `core/agent/compaction.py`） |
| `summary_until_message_id` | number \| null | 摘要覆盖到哪条消息（含）；`id <= 它的消息不再逐条发给模型` |
| `summary_anchor_message_id` | number \| null | 锚点（首条用户消息 id），永不参与摘要 |
| `status` | string | `active` / `archived` |

- GET `/api/conversations` → `[{id, title, updated_at}, ...]`
- POST `/api/conversations` body `{title?}` → `{id, title, updated_at}`

**messages**（嵌套存储：assistant 行的 `tool_calls` 列存完整 UI 结构 JSON，还原时 1:1 映射回前端 `ChatMessage`，不做规范化拆行）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | number | 自增 id（会话内按 id 升序即消息顺序） |
| `role` | enum | 仅 `user` / `assistant`（tool 中间态内嵌在 assistant.tool_calls） |
| `content` | string | 消息文本 |
| `tool_calls` | JSON \| null | 仅 assistant：`[{id, name, arguments, result?, status}]` |
| `interrupted` | boolean | 该轮被用户中止/断连。**UI 展示，但不进模型上下文**（回放跳过） |
| `stop_reason` | string \| null | `client`（点了停止）/ `disconnect`（掉线） |
| `turn_id` | string \| null | 一轮的 id（用户消息与助手消息共用），撤回按它定位 |
| `withdrawn` | boolean | 已撤回。撤回后**不出现在消息列表、不进模型上下文**，行保留供审计 |

- GET `/api/conversations/{id}/messages` → `[{id, role, content, tool_calls?, turn_id?, interrupted, stop_reason?}, ...]`；会话不存在 404
- POST `/api/conversations/{id}/messages` body `{messages: [{role, content, tool_calls?, turn_id?, interrupted?, stop_reason?}]}` → `{conversation_id, title, saved}`；每轮对话一次批量写入
- DELETE `/api/conversations/{id}` → 硬删，messages 由 `ON DELETE CASCADE` 级联清除
- POST `/api/conversations/{id}/withdraw` body `{turn_id, truncate?}` → **撤回一轮及其之后的所有轮次**（`truncate=false` 时只撤这一轮）：消息软删 + 这些轮次写过的数据还原，并清空会话摘要；返回 `{turn_id, withdrawn_messages, turns_withdrawn, user_text, rollback}`。数据被改过则 409

## 3. 其他接口

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/health` | 健康检查，前端据此判断是否降级 mock | 已实现 |
| POST | `/api/chat` | Agent SSE 流式对话（body 可带 `conversation_id` / `turn_id`） | 已实现，事件协议见 `frontend/API_CONTRACT.md` 第 4 节 |
| POST | `/api/chat/cancel` | 取消一轮生成 body `{turn_id}`；返回 `{cancelled, done, reason}`，幂等 | 已实现 |

## 4. 数据存储约定（后端内部）

v0.4 起按关系建模拆分，每张表都带 `user_id` 列（INTEGER NOT NULL DEFAULT 1）为多用户扩展铺路；v0.5 起删除 `monthly_cashflow` 汇总表（月度现金流改为由流水派生）：

| 表 | 用途 | 关键字段 |
|---|---|---|
| `basic_info` | 基本信息，单用户单行 | id, user_id, name, age, city, occupation, ..., risk_tolerance, investing_years, liquidity_needs |
| `assets` | 资产明细，多行 | id, user_id, name, value, asset_type (cash/investment/property/other) |
| `debts` | 债务明细，多行 | id, user_id, name, remaining, monthly_payment, annual_rate |
| `goals` | 财富目标，多行 | id, user_id, goal_id, name, target, current, deadline, priority, created_at |
| `transactions` | 交易流水明细（**收支唯一数据源**） | id, user_id, transaction_type, amount, category, source, occurred_at, note |
| `conversations` | 对话会话（v0.6 启用） | id, user_id, title, summary, summary_until_message_id, summary_anchor_message_id, status, created_at, updated_at |
| `messages` | 会话消息（v0.6 启用） | id, conversation_id (CASCADE), role, content, tool_calls, tool_name, turn_id, interrupted, stop_reason, withdrawn, withdrawn_at, created_at |
| `turns` | 一轮生成的生命周期（撤回用） | turn_id, conversation_id, status, created_at, finished_at |
| `turn_writes` | 一轮写入的差异日志（撤回时还原） | id, turn_id, conversation_id, table_name, pk_json, op, before_json, after_json, status |
| `compactions` | 上下文压缩审计 | id, conversation_id, covered_from/until_message_id, before/after_tokens, summary, prompt_version, created_at |
| `user_notes` | 跨会话非结构化记忆（L3） | id, user_id, category (preference/constraint/fact), content, source, status, expires_at, created_at, updated_at |

UNIQUE 约束：
- `basic_info`：`id`（单行模式固定为 OWNER_ID）
- `assets` / `debts`：`(user_id, name)`
- `goals`：`(user_id, goal_id)`

建表脚本见 `core/database/schema.py`（连接管理见 `core/database/connection.py`），各表读写逻辑在 `core/services/{basic_info,assets,debts,goals,cashflow,transactions}.py`。共用工具在 `core/services/_common.py`。

**上下文压缩**：`core/agent/compaction.py` 在轮次边界检查规模，超过 `CONTEXT_COMPACT_AT`（默认 75%）时把"锚点之后、最近 6 轮之前"的轮次摘要进 `conversations.summary`，并写 `compactions` 审计；`context.prepare_history(state=...)` 只发锚点 + 摘要 + 最近消息。关闭：`CONTEXT_COMPACTION=0`。

**跨会话记忆**：`core/services/memories.py` + `core/agent/l3.py`。每轮把 `user_notes` 中
`source=user_stated`、未过期的前 12 条注入当前 user 消息（日期之后、用户原话之前），
上限 1200 字符；`remember_note` / `forget_note` 工具负责写入与软删。

## 5. 变更记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-09-22 | v0.1 | 初始版本：Profile 模块字段要求（含新增 8 个资料字段、量化标准、派生指标规则、规划中接口） |
| 2026-09-22 | v0.2 | 按 TODO 对齐实现：路由接入 service 层（GET/POST/PATCH），service 补齐 8 个新字段与 12 项完善度清单，dashboard 改从 service 取数，前端契约示例同步 |
| 2026-09-22 | v0.3 | `financial_profiles` 表结构对齐契约：移除遗留列 `income_stability`，冗余列 `risk_preference` 更名 `risk_tolerance`（与 JSON 字段同名），DDL 补 NOT NULL 约束 |
| 2026-09-23 | v0.4 | **全表拆分重构**：废弃 `financial_profiles.profile_data` JSON 单列设计，按关系建模拆为 6 张独立表（basic_info / assets / debts / goals / monthly_cashflow / transactions）；废弃 `/api/profile` 聚合端点，按子资源拆为 6 组 RESTful 端点；`monthly_income`/`monthly_expense` 不再存为字段，由 `monthly_cashflow` 派生；每张表预埋 `user_id` 列；dashboard 改为从子 service 聚合数据，删除硬编码 CASHFLOW |
| 2026-09-23 | v0.5 | **算法字段隔离**：`risk_tolerance` 更名展示为"风险承受能力"；新增 `investment_preference`（投资偏好）列；`risk_tolerance`/`investment_preference`/`liquidity_needs` 三个字段标记为算法估算，从 `BASIC_INFO_FIELDS` 可写白名单和 `update_basic_info` tool 参数中移除；前端 ProfileCard 实现行内编辑（可编辑字段点击直接修改），三个算法字段只读展示 |
| 2026-09-24 | v0.6 | **会话持久化（GPT 式多会话）**：启用预留的 conversations/messages 两张表，新增 5 个 RESTful 端点（列表/创建/懒加载消息/批量追加/删除级联）；会话行惰性创建；消息按 UI 结构嵌套存储（assistant.tool_calls 存完整 JSON）；前端 useChat 升级为会话感知，localStorage 仅存当前会话 id，刷新自动恢复，历史下拉支持切换与删除；后端离线时 mock 对话不落库 |
| 2026-10-18 | v0.7 | **月度现金流改为派生（单一数据源）**：删除 `monthly_cashflow` 表与 `POST /api/cashflow`、下线 `set_monthly_cashflow` 工具；`GET /api/cashflow` 与 dashboard KPI 改为由 `transactions` 按月聚合，`surplus` 永远现算；修复"Agent 记了流水但月结余不变"（BK-27 / G-11）。前端移除从未被调用的 `getCashflow` / `upsertCashflow`。Agent 侧新增「财务数据写入」提示词规则并注入当前日期 |
| 2026-10-21 | v0.8 | **财务健康度实时计算**：删除 dashboard 里写死的 `HEALTH` 常量，改为 `services/wealth_health.py` 的 5 维评分（储蓄/应急/负债/目标/资产配置）；`assets` 新增 `asset_type`、`debts` 新增 `annual_rate`（幂等迁移）；goals 列表暴露 `created_at`；dashboard `health` 增加 `data_quality`（full/partial/insufficient） |
