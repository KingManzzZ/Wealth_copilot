# WealthPilot 后端接口文档

> 版本：v0.4 ｜ 更新日期：2026-09-23
> 适用范围：本文档描述后端 API 契约。v0.4 按"全表拆分"重构，原 `/api/profile` 聚合端点废弃，按子资源拆为 6 组 RESTful 端点。
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
| POST | `/api/assets` | UPSERT 单条资产 body `{name, value}` |
| DELETE | `/api/assets/{name}` | 删除单条资产 |
| GET | `/api/debts` | 列出全部债务（`{name: {remaining, monthly_payment}}` 字典） |
| POST | `/api/debts` | UPSERT 单条债务 body `{name, remaining, monthly_payment}` |
| DELETE | `/api/debts/{name}` | 删除单条债务 |
| GET | `/api/goals` | 列出全部目标（`FinancialGoal[]`） |
| POST | `/api/goals` | UPSERT 单条目标 body `FinancialGoal`（按 id 查重） |
| DELETE | `/api/goals/{goal_id}` | 删除单条目标 |
| GET | `/api/cashflow?months=6` | 取最近 N 个月的月度现金流汇总 |
| POST | `/api/cashflow` | UPSERT 某月汇总 body `{month, income, expense}` |
| GET | `/api/transactions?month=YYYY-MM` | 取交易流水（可按月过滤） |
| POST | `/api/transactions` | 新增单条交易流水 |
| DELETE | `/api/transactions/{id}` | 删除单条交易 |
| GET | `/api/dashboard` | 仪表盘聚合数据（KPI/现金流/目标/资产负债派生） |

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

> **不再包含**：`monthly_income` / `monthly_expense`（已从 `monthly_cashflow` 派生）；`assets` / `debts` / `goals`（拆为独立接口）。

### 2.3 资产（assets 表）

字典结构：键为资产名（中文允许），值为当前估值（元）。

- GET 响应：`{ "现金及活期": 80000, "基金": 30000 }`
- POST body：`{ "name": "现金", "value": 80000 }` —— UPSERT 语义，同名则更新
- DELETE `/api/assets/{name}`：路径参数需 URL encode；不存在返回 404

### 2.4 债务（debts 表）

字典结构：键为债务名，值为对象 `{ remaining, monthly_payment }`。

- GET 响应：`{ "房贷": { "remaining": 800000, "monthly_payment": 6500 } }`
- POST body：`{ "name": "房贷", "remaining": 800000, "monthly_payment": 6500 }`
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

- POST body：完整 `FinancialGoal` 对象，按 `id` UPSERT
- DELETE `/api/goals/{goal_id}`

### 2.6 月度现金流（monthly_cashflow 表）

| 字段 | 类型 | 说明 |
|---|---|---|
| `month` | string | `YYYY-MM` 格式，如 `2026-09` |
| `income` | number | 当月收入合计（元） |
| `expense` | number | 当月支出合计（元） |
| `surplus` | number | 派生字段：income - expense |

- GET `/api/cashflow?months=6`：取最近 N 个月（默认 6，上限 36），按月份升序返回
- POST body：`{ "month": "2026-09", "income": 18000, "expense": 9500 }` —— UPSERT，按 (user_id, month) 唯一

> dashboard 的 KPI（monthly_income / monthly_expense / saving_rate）从最近一个月的 monthly_cashflow 派生。

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
  "health": { ... }               // 当前为静态数据，后续接入动态算法
}
```

KPI 派生规则：取 `monthly_cashflow` 表最近一月的 `income` / `expense`，`surplus = income - expense`，`saving_rate = surplus / income`（income 为 0 时置 0）。

## 3. 其他接口

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/health` | 健康检查，前端据此判断是否降级 mock | 已实现 |
| POST | `/api/chat` | Agent SSE 流式对话 | 已实现，事件协议见 `frontend/API_CONTRACT.md` 第 4 节 |

## 4. 数据存储约定（后端内部）

v0.4 起按关系建模拆为 6 张表，每张表都带 `user_id` 列（INTEGER NOT NULL DEFAULT 1）为多用户扩展铺路：

| 表 | 用途 | 关键字段 |
|---|---|---|
| `basic_info` | 基本信息，单用户单行 | id, user_id, name, age, city, occupation, ..., risk_tolerance, investing_years, liquidity_needs |
| `assets` | 资产明细，多行 | id, user_id, name, value |
| `debts` | 债务明细，多行 | id, user_id, name, remaining, monthly_payment |
| `goals` | 财富目标，多行 | id, user_id, goal_id, name, target, current, deadline, priority |
| `monthly_cashflow` | 月度现金流汇总 | id, user_id, month, income, expense |
| `transactions` | 交易流水明细 | id, user_id, transaction_type, amount, category, source, occurred_at, note |

UNIQUE 约束：
- `basic_info`：`id`（单行模式固定为 OWNER_ID）
- `assets` / `debts`：`(user_id, name)`
- `goals`：`(user_id, goal_id)`
- `monthly_cashflow`：`(user_id, month)`

建表脚本见 `core/database/schema.py`（连接管理见 `core/database/connection.py`），各表读写逻辑在 `core/services/{basic_info,assets,debts,goals,cashflow,transactions}.py`。共用工具在 `core/services/_common.py`。

## 5. 变更记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-09-22 | v0.1 | 初始版本：Profile 模块字段要求（含新增 8 个资料字段、量化标准、派生指标规则、规划中接口） |
| 2026-09-22 | v0.2 | 按 TODO 对齐实现：路由接入 service 层（GET/POST/PATCH），service 补齐 8 个新字段与 12 项完善度清单，dashboard 改从 service 取数，前端契约示例同步 |
| 2026-09-22 | v0.3 | `financial_profiles` 表结构对齐契约：移除遗留列 `income_stability`，冗余列 `risk_preference` 更名 `risk_tolerance`（与 JSON 字段同名），DDL 补 NOT NULL 约束 |
| 2026-09-23 | v0.4 | **全表拆分重构**：废弃 `financial_profiles.profile_data` JSON 单列设计，按关系建模拆为 6 张独立表（basic_info / assets / debts / goals / monthly_cashflow / transactions）；废弃 `/api/profile` 聚合端点，按子资源拆为 6 组 RESTful 端点；`monthly_income`/`monthly_expense` 不再存为字段，由 `monthly_cashflow` 派生；每张表预埋 `user_id` 列；dashboard 改为从子 service 聚合数据，删除硬编码 CASHFLOW |
| 2026-09-23 | v0.5 | **算法字段隔离**：`risk_tolerance` 更名展示为"风险承受能力"；新增 `investment_preference`（投资偏好）列；`risk_tolerance`/`investment_preference`/`liquidity_needs` 三个字段标记为算法估算，从 `BASIC_INFO_FIELDS` 可写白名单和 `update_basic_info` tool 参数中移除；前端 ProfileCard 实现行内编辑（可编辑字段点击直接修改），三个算法字段只读展示 |
