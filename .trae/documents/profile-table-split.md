# Profile 全表拆分重构计划（v3）

## Context（为什么做）

当前财务画像完整 JSON 存于 `financial_profiles.profile_data` 单列，且 dashboard 里硬编码 6 个月现金流（[dashboard.py:8-15](file:///d:/Python_Projects/Agent_study/backend/api/routes/dashboard.py#L8-L15)）。用户决定按完整关系建模重构：

- 拆 6 张独立表（基本信息 + 资产 + 债务 + 目标 + 月度现金流汇总 + 交易流水）
- API 与表 1:1 对应，废弃 `/api/profile` 聚合端点
- `monthly_income`/`monthly_expense` 不再存为字段，从月度现金流汇总表派生（取最近月）
- 每张表保留 `user_id` 列为多用户扩展铺路
- 前端同步改造，按需独立拉数据

## 关键决策（已与用户确认）

| 项 | 决策 |
|---|---|
| monthly_income/monthly_expense 归属 | 从月度现金流汇总表派生，不存为基本信息表字段 |
| `/api/profile` 聚合端点 | 完全废弃，前端改分别调子端点 |
| 单月收支详情表 | 复用现有 `transactions` 表（已定义未用），加 `user_id` 列 |
| `user_id` 类型 | INTEGER NOT NULL DEFAULT 1，未来关联 `users.id` |

## 数据库 Schema 设计

### 1. `basic_info` 表（原 financial_profiles 改名 + 精简）

```
CREATE TABLE IF NOT EXISTS basic_info (
    id                   INTEGER PRIMARY KEY,
    user_id              INTEGER NOT NULL DEFAULT 1,
    name                 TEXT    NOT NULL DEFAULT '',
    age                  NUMERIC NOT NULL DEFAULT 0,
    city                 TEXT    NOT NULL DEFAULT '',
    occupation           TEXT    NOT NULL DEFAULT '',
    industry             TEXT    NOT NULL DEFAULT '',
    education            TEXT    NOT NULL DEFAULT '',
    family               TEXT    NOT NULL DEFAULT '',
    marital_status       TEXT    NOT NULL DEFAULT '',
    children             TEXT    NOT NULL DEFAULT '',
    supporting_parents   TEXT    NOT NULL DEFAULT '',
    risk_tolerance       TEXT    NOT NULL DEFAULT '',
    investing_years      NUMERIC NOT NULL DEFAULT 0,
    liquidity_needs      TEXT    NOT NULL DEFAULT '',
    created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

移除：`profile_data`、`monthly_income`、`monthly_expense`、`risk_preference`（旧名）。`id = user_id = 1` 单用户模式。

### 2. `assets` 表（对齐前端 `Record<string, number>`）

```
CREATE TABLE IF NOT EXISTS assets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1,
    name        TEXT    NOT NULL,
    value       NUMERIC NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, name)
);
CREATE INDEX IF NOT EXISTS ix_assets_user ON assets (user_id);
```

### 3. `debts` 表（原 liabilities 改名为 debts，对齐前端命名）

```
CREATE TABLE IF NOT EXISTS debts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL DEFAULT 1,
    name             TEXT    NOT NULL,
    remaining        NUMERIC NOT NULL DEFAULT 0,
    monthly_payment  NUMERIC NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, name)
);
CREATE INDEX IF NOT EXISTS ix_debts_user ON debts (user_id);
```

### 4. `goals` 表（对齐前端 `FinancialGoal[]`）

```
CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1,
    goal_id     TEXT    NOT NULL,            -- 前端用的字符串 id（如 "g1"）
    name        TEXT    NOT NULL,
    target      NUMERIC NOT NULL,
    current     NUMERIC NOT NULL DEFAULT 0,
    deadline    TEXT,
    priority    TEXT    NOT NULL DEFAULT 'medium'
                        CHECK (priority IN ('high', 'medium', 'low')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, goal_id)
);
CREATE INDEX IF NOT EXISTS ix_goals_user ON goals (user_id);
```

### 5. `monthly_cashflow` 表（月度现金流汇总，新表）

```
CREATE TABLE IF NOT EXISTS monthly_cashflow (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL DEFAULT 1,
    month       TEXT    NOT NULL,            -- 'YYYY-MM' 格式，如 '2026-09'
    income      NUMERIC NOT NULL DEFAULT 0,
    expense     NUMERIC NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, month)
);
CREATE INDEX IF NOT EXISTS ix_cashflow_user_month ON monthly_cashflow (user_id, month);
```

替代 [dashboard.py:8-15](file:///d:/Python_Projects/Agent_study/backend/api/routes/dashboard.py#L8-L15) 的硬编码 CASHFLOW。

### 6. `transactions` 表（复用并精简原 schema，加 user_id）

```
CREATE TABLE IF NOT EXISTS transactions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL DEFAULT 1,
    transaction_type  TEXT    NOT NULL CHECK (transaction_type IN ('income', 'expense')),
    amount            NUMERIC NOT NULL CHECK (amount > 0),
    category          TEXT    NOT NULL,           -- 收支分类（薪资/餐饮/交通...）
    source            TEXT,                        -- 来源/去向（具体说明）
    occurred_at       TEXT    NOT NULL,           -- 发生时间
    note              TEXT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_transactions_user_occurred ON transactions (user_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_transactions_user_type_category ON transactions (user_id, transaction_type, category);
```

复用原 schema 的字段（transaction_type/amount/category/source/occurred_at），加 `user_id`，移除原 `note`（保留作为可选）。

## API 端点设计

### 废弃端点

- `GET/POST/PATCH /api/profile` 全部废弃（[profile.py 路由](file:///d:/Python_Projects/Agent_study/backend/api/routes/profile.py)）

### 新端点列表

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/basic_info` | 取当前用户基本信息 |
| POST/PATCH | `/api/basic_info` | UPSERT 基本信息（单条记录） |
| GET | `/api/assets` | 列出全部资产（返回 `{name: value}` 字典） |
| POST | `/api/assets` | UPSERT 单条资产 body `{name, value}` |
| DELETE | `/api/assets/{name}` | 删除单条资产 |
| GET | `/api/debts` | 列出全部债务 |
| POST | `/api/debts` | UPSERT 单条债务 body `{name, remaining, monthly_payment}` |
| DELETE | `/api/debts/{name}` | 删除单条债务 |
| GET | `/api/goals` | 列出全部目标 |
| POST | `/api/goals` | UPSERT 单条目标 body `FinancialGoal` |
| DELETE | `/api/goals/{goal_id}` | 删除单条目标 |
| GET | `/api/cashflow?months=6` | 取最近 N 个月的月度现金流汇总 |
| POST | `/api/cashflow` | UPSERT 某月汇总 body `{month, income, expense}` |
| GET | `/api/transactions?month=YYYY-MM` | 取某月交易明细 |
| POST | `/api/transactions` | 新增单条交易流水 |
| DELETE | `/api/transactions/{id}` | 删除单条交易 |

**Dashboard 路由改造**（[dashboard.py](file:///d:/Python_Projects/Agent_study/backend/api/routes/dashboard.py)）：

- 不再从 `get_profile()` 派生 KPI；改读 `monthly_cashflow` 取最近月 income/expense
- `_build_asset_liability` 改为调 `list_assets()` + `list_debts()`
- `goals` 字段改为调 `list_goals()`
- `cashflow` 字段改为读 `monthly_cashflow` 表（替代硬编码 CASHFLOW）
- `health` 保留静态（用户说先用静态）

## Service 层改造

### 新建独立 service（[core/services/](file:///d:/Python_Projects/Agent_study/backend/core/services)）

按表拆分 service，每个 service 一个文件，职责单一：

1. **`basic_info.py`** —— 新建。`get_basic_info()` / `upsert_basic_info(data)` / `_default_basic_info()`
2. **`assets.py`** —— 已存在占位文件 [asset.py](file:///d:/Python_Projects/Agent_study/backend/core/services/asset.py)，改名 + 实现。`list_assets()` / `upsert_asset(name, value)` / `delete_asset(name)`
3. **`debts.py`** —— 新建。`list_debts()` / `upsert_debt(name, remaining, monthly_payment)` / `delete_debt(name)`
4. **`goals.py`** —— 新建。`list_goals()` / `upsert_goal(goal)` / `delete_goal(goal_id)`
5. **`cashflow.py`** —— 新建。`list_cashflow(months=6)` / `upsert_cashflow(month, income, expense)` / `get_latest_month()`（dashboard KPI 派生用）
6. **`transactions.py`** —— 已存在占位文件 [transaction.py](file:///d:/Python_Projects/Agent_study/backend/core/services/transaction.py)，改名 + 实现。`list_transactions(month=None)` / `add_transaction(...)` / `delete_transaction(id)`
7. **`profile.py`** —— 废弃（含 `_load_profile` / `_save_profile` / `get_profile` 等全部方法）

每个 service 共用模式：
- 模块级 `OWNER_ID = 1` 常量
- 每个函数自建 `get_connection()` 单连接事务，finally close
- 写操作走 `INSERT ... ON CONFLICT(user_id, ...) DO UPDATE` UPSERT
- 列表查询返回前端契约形态（dict 或 list，不做 ORM 包装）

### 共用工具抽到 `core/services/_common.py`

- `OWNER_ID: int = 1` 常量
- `_now() -> str` 时间戳
- `_sanitize(data, allowed_fields)` 字段白名单
- `_is_filled(value) -> bool` 兜底判定

从原 [profile.py](file:///d:/Python_Projects/Agent_study/backend/core/services/profile.py) 抽出来共用。

## 前端改造（同步进行）

### 类型层 [types/index.ts](file:///d:/Python_Projects/Agent_study/frontend/src/types/index.ts)

废弃 `UserProfile` 聚合类型，拆为：

```typescript
export interface BasicInfo {
  user_id: string
  name?: string
  age?: number
  city?: string
  occupation?: string
  industry?: string
  education?: string
  family?: string
  marital_status?: string
  children?: string
  supporting_parents?: string
  risk_tolerance?: "conservative" | "balanced" | "aggressive"
  investing_years?: number
  liquidity_needs?: "low" | "medium" | "high"
  last_updated?: string
}

// 保留 FinancialGoal 不变
export type Assets = Record<string, number>
export type Debts = Record<string, { remaining: number; monthly_payment: number }>

export interface MonthlyCashflow {
  month: string  // 'YYYY-MM'
  income: number
  expense: number
  surplus: number  // 派生：income - expense
}

export interface Transaction {
  id: number
  transaction_type: "income" | "expense"
  amount: number
  category: string
  source?: string
  occurred_at: string
  note?: string
}
```

### API client [api.ts](file:///d:/Python_Projects/Agent_study/frontend/src/lib/api.ts)

废弃 `getProfile()`，新增 6 个客户端方法：

```typescript
export async function getBasicInfo(): Promise<BasicInfo>
export async function getAssets(): Promise<Assets>
export async function getDebts(): Promise<Debts>
export async function getGoals(): Promise<FinancialGoal[]>
export async function getCashflow(months = 6): Promise<MonthlyCashflow[]>
export async function getTransactions(month?: string): Promise<Transaction[]>
```

每个都保留 mock 兜底（offline 时降级）。

### 组件改造

- **[user-menu.tsx](file:///d:/Python_Projects/Agent_study/frontend/src/components/user-menu.tsx)** — `ProfileCard` 改为读 `BasicInfo`（不再依赖 assets/debts/goals）。`getProfileCompletion` 和 `getPendingConfirmations` 改为只看 BasicInfo 字段。
- **[dashboard.tsx](file:///d:/Python_Projects/Agent_study/frontend/src/components/...dashboard)** — 改为分别调 `getCashflow` + `getAssets` + `getDebts` + `getGoals`，用 `Promise.all` 并发拉取。
- **[lib/profile.ts](file:///d:/Python_Projects/Agent_study/frontend/src/lib/profile.ts)** — `PROFILE_CHECKLIST` 移除 `monthly_income`/`monthly_expense`（不再属于 BasicInfo）；完善度计算改为只看 BasicInfo 字段。
- **[lib/mock-data.ts](file:///d:/Python_Projects/Agent_study/frontend/src/lib/mock-data.ts)** — mock profile 拆为 `mockBasicInfo` / `mockAssets` / `mockDebts` / `mockGoals` / `mockCashflow` / `mockTransactions`。

## 文件改动清单

### 后端

1. [schema.py](file:///d:/Python_Projects/Agent_study/backend/core/database/schema.py) — 重写 `SCHEMA_SQL`，6 张表按上述 DDL
2. [services/basic_info.py](file:///d:/Python_Projects/Agent_study/backend/core/services/basic_info.py) — 新建
3. [services/asset.py](file:///d:/Python_Projects/Agent_study/backend/core/services/asset.py) → 重命名为 `assets.py`，实现
4. [services/debts.py](file:///d:/Python_Projects/Agent_study/backend/core/services/debts.py) — 新建
5. [services/goals.py](file:///d:/Python_Projects/Agent_study/backend/core/services/goals.py) — 新建
6. [services/cashflow.py](file:///d:/Python_Projects/Agent_study/backend/core/services/cashflow.py) — 新建
7. [services/transaction.py](file:///d:/Python_Projects/Agent_study/backend/core/services/transaction.py) → 重命名为 `transactions.py`，实现
8. [services/profile.py](file:///d:/Python_Projects/Agent_study/backend/core/services/profile.py) — 删除（或保留 `_default_profile` 暂时兼容，最终删）
9. [services/_common.py](file:///d:/Python_Projects/Agent_study/backend/core/services/_common.py) — 新建共用工具
10. [api/routes/profile.py](file:///d:/Python_Projects/Agent_study/backend/api/routes/profile.py) — 重写为拆分的 6 个子路由组（或拆成 basic_info.py / assets.py / debts.py / goals.py / cashflow.py / transactions.py 多个文件）
11. [api/routes/dashboard.py](file:///d:/Python_Projects/Agent_study/backend/api/routes/dashboard.py) — 改为从子 service 拉数据，删除硬编码 CASHFLOW
12. [main.py](file:///d:/Python_Projects/Agent_study/backend/main.py) — 注册新路由
13. [API.md](file:///d:/Python_Projects/Agent_study/backend/API.md) — 重写第 2 节"Profile 模块"，第 4 节"数据存储约定"，加 v0.4 变更记录

### 前端

14. [types/index.ts](file:///d:/Python_Projects/Agent_study/frontend/src/types/index.ts) — 废弃 `UserProfile`，新增 `BasicInfo`/`Assets`/`Debts`/`MonthlyCashflow`/`Transaction`
15. [lib/api.ts](file:///d:/Python_Projects/Agent_study/frontend/src/lib/api.ts) — 废弃 `getProfile`，新增 6 个独立 fetch
16. [lib/profile.ts](file:///d:/Python_Projects/Agent_study/frontend/src/lib/profile.ts) — 完善度计算改为基于 BasicInfo
17. [lib/mock-data.ts](file:///d:/Python_Projects/Agent_study/frontend/src/lib/mock-data.ts) — mock 数据拆分
18. [components/user-menu.tsx](file:///d:/Python_Projects/Agent_study/frontend/src/components/user-menu.tsx) — ProfileCard 改为读 BasicInfo
19. dashboard 相关组件（具体路径执行时再 grep 确认） — 改为多 API 并发拉取

## 不在本次范围

- `connection.py` 的 `_schema_checked` 简化（与本重构独立）
- 数据迁移脚本（删 db/data.db 重建）
- 健康分动态计算（仍用 dashboard 静态 HEALTH）
- 多用户实现（仅预埋 user_id 列，service 层硬编码 OWNER_ID = 1）

## 验证

1. **删 db**：删除 `backend/db/data.db`（及 wal/shm）
2. **启动后端**：`cd backend && python main.py`，确认 lifespan 建表成功
3. **空数据兜底**：
   - `curl http://localhost:8000/api/basic_info` → 默认值（数值 0、字符串 ""）
   - `curl http://localhost:8000/api/assets` → `{}`
   - `curl http://localhost:8000/api/goals` → `[]`
   - `curl http://localhost:8000/api/cashflow` → `[]`
4. **写入数据**：
   - `POST /api/basic_info` body `{name:"测试", age:29}`
   - `POST /api/assets` body `{name:"现金", value:80000}`
   - `POST /api/debts` body `{name:"房贷", remaining:800000, monthly_payment:6500}`
   - `POST /api/goals` body `{id:"g1", name:"买房", target:500000, current:130000, deadline:"2031-09-16", priority:"high"}`
   - `POST /api/cashflow` body `{month:"2026-09", income:18000, expense:9500}`
   - `POST /api/transactions` body `{transaction_type:"expense", amount:200, category:"餐饮", source:"午餐", occurred_at:"2026-09-23T12:00:00"}`
5. **回读校验**：每个 GET 端点都返回写入的数据
6. **UPSERT 验证**：再 POST 同名 asset 不同 value，确认更新而非新增
7. **DELETE 验证**：`DELETE /api/assets/现金` 后再 GET 确认已删
8. **Dashboard**：`curl http://localhost:8000/api/dashboard` —— KPI 从 cashflow 派生、asset_liability 从 assets+debts 派生、goals 从 goals 表读
9. **前端启动**：`cd frontend && npm run dev`，访问页面，确认 UserMenu 资料卡正常显示、Dashboard 数据正常加载
10. **直接查表**：`sqlite3 backend/db/data.db` 执行 `SELECT * FROM basic_info; SELECT * FROM assets; ...` 确认数据真的拆到 6 张表
