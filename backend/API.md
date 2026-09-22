# WealthPilot 后端接口文档

> 版本：v0.1 ｜ 更新日期：2026-09-22
> 适用范围：本文档描述后端 API 契约。当前优先完成 **Profile（财务画像）模块** 的字段要求，其余模块后续补充。
> 前端本地推导逻辑见 `frontend/src/lib/profile.ts`，前端类型定义见 `frontend/src/types/index.ts`。

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 接口前缀 | `/api`（开发环境 Vite dev proxy 转发至 `http://localhost:8000`） |
| 数据格式 | JSON，UTF-8；金额为数字类型（人民币元），非字符串 |
| 时间格式 | ISO 8601，秒级，如 `2026-09-22T10:30:00`；纯日期字段如 `2031-09-16` |
| 单用户模式 | 用户归属固定 `OWNER_ID = 1`，前端无需传 `user_id`，多用户扩展时再引入 |

## 2. Profile 模块（财务画像）

### 2.1 接口列表

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/profile` | 获取当前用户财务画像 | 路由仍为静态 mock，待接入 service 层 |
| POST | `/api/profile` | 创建初始画像（已存在则整体覆盖） | 规划中，`service.create_profile` 已就绪 |
| PATCH | `/api/profile` | 部分更新画像字段 | 规划中，`service.update_profile` 已就绪 |

### 2.2 字段定义（UserProfile）

#### 基本资料

| 字段 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `user_id` | string | 用户归属标识。单用户模式默认 `"default"`，创建后为 `"user-1"` | `"user-1"` |
| `name` | string | 姓名 | `"学习者"` |
| `age` | number | 年龄（岁） | `29` |
| `city` | string | 所在城市 | `"上海"` |
| `occupation` | string | 职业 | `"后端工程师"` |
| `industry` | string | 所属行业 | `"互联网"` |
| `education` | string | 教育程度（自由文本，如 高中/大专/本科/硕士/博士） | `"硕士"` |

#### 财务状况

| 字段 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `monthly_income` | number | 月收入（元） | `18000` |
| `monthly_expense` | number | 月支出（元） | `9500` |
| `assets` | object | 资产映射：**键**为资产名称，**值**为当前估值（元） | `{ "现金及活期": 80000 }` |
| `debts` | object | 负债映射：**键**为负债名称，**值**为对象 `{ remaining, monthly_payment }`（剩余本金、月还款额） | `{ "房贷": { "remaining": 800000, "monthly_payment": 6500 } }` |
| `goals` | array | 财富目标列表，元素见下表 | — |

`goals[]` 元素：

| 字段 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `id` | string | 目标唯一标识 | `"g1"` |
| `name` | string | 目标名称 | `"二套房首付"` |
| `target` | number | 目标金额（元） | `500000` |
| `current` | number | 当前已积累金额（元） | `130000` |
| `deadline` | string | 截止日期 | `"2031-09-16"` |
| `priority` | enum | 优先级，见 2.3 | `"high"` |

#### 投资偏好

| 字段 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `risk_tolerance` | enum | 风险等级，取值与量化标准见 2.3 | `"balanced"` |
| `investing_years` | number | 投资年限（年） | `5` |
| `liquidity_needs` | enum | 活期现金需求，取值与量化标准见 2.3 | `"medium"` |

#### 家庭情况

| 字段 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `family` | string | 家庭状况（历史字段，综合性描述） | `"单身"` |
| `marital_status` | string | 婚姻状况 | `"未婚"` |
| `children` | string | 子女情况 | `"暂无"` |
| `supporting_parents` | string | 赡养父母情况 | `"需赡养父母一方"` |

#### 系统字段

| 字段 | 类型 | 说明 | 示例 |
|---|---|---|---|
| `last_updated` | string | 画像最近更新时间（服务端写入，客户端只读） | `"2026-09-22T10:30:00"` |

### 2.3 枚举值与量化标准

**`risk_tolerance` 风险等级**（量化标准用于前端「?」悬浮说明）：

| 取值 | 展示文案 | 量化标准 |
|---|---|---|
| `conservative` | 保守型 | 权益类资产占比 ≤ 20%，可承受最大回撤约 10% |
| `balanced` | 平衡型 | 权益类资产占比 40%~60%，可承受最大回撤约 20% |
| `aggressive` | 进取型 | 权益类资产占比 ≥ 70%，可承受最大回撤约 30% |

**`liquidity_needs` 活期现金需求**：

| 取值 | 展示文案 | 需覆盖月数 | 建议金额估算 |
|---|---|---|---|
| `low` | 较低 | 3 个月支出 | 月支出 × 3 |
| `medium` | 中等 | 6 个月支出 | 月支出 × 6 |
| `high` | 较高 | 12 个月支出 | 月支出 × 12 |

> 估算示例：月支出 ¥9,500，`medium` → 建议保留活期现金约 ¥57,000。

**`goals[].priority` 目标优先级**：`high` 高 ｜ `medium` 中 ｜ `low` 低（前端展示大写中文）。

### 2.4 字段填写要求分级

| 分级 | 字段 | 说明 |
|---|---|---|
| **必返** | `user_id`、`assets`、`debts`、`goals`、`last_updated` | 响应中始终存在；无数据时分别为 `"default"`、`{}`、`{}`、`[]`、`""` |
| **数值/字符串兜底** | 其余全部字段 | 未填写时建议返回 `0` / `""`（而非 `null`），前端将空值判定为「待确认」 |
| **完善度清单（12 项）** | 姓名、年龄、城市、职业、行业、教育程度、风险等级、投资年限、活期现金需求、婚姻状况、子女情况、赡养父母情况 | 资料完整度 = 已填写项数 ÷ 12 |
| **首次设置必填（5 项）** | `name`、`age`、`occupation`、`monthly_income`、`monthly_expense` | 全部有效才算完成 onboarding |

**字段「已填写」判定规则**（前后端需保持一致）：

- 字符串：去除首尾空白后非空
- 数值：大于 0
- 对象 / 数组：长度大于 0
- `null` / 缺失：未填写

### 2.5 派生指标（计算规则）

| 指标 | 规则 | 当前实现位置 |
|---|---|---|
| 资料完整度 | 完善度清单 12 项中「已填写」项占比（0.0 ~ 1.0） | 后端 `get_profile_completion()`；前端 `getProfileCompletion()` 本地推导 |
| 待确认项 | 清单中未填写字段 → 文案「XX待确认」 | 前端 `getPendingConfirmations()` 本地推导 |
| 首次设置完成 | 5 项必填字段全部「已填写」 | 后端 `is_onboarding_completed()` |

> 说明：以上指标当前**不通过接口返回**，由前端按上述规则本地推导（结果与后端 service 对齐）。若后续需要服务端计算返回，可扩展响应字段（规划中）。

### 2.6 `GET /api/profile`

获取当前用户财务画像；尚未创建时返回默认值（数值 `0`、集合为空、`user_id = "default"`）。

- 请求：无参数
- 响应 200：

```json
{
  "user_id": "user-1",
  "name": "学习者",
  "age": 29,
  "city": "上海",
  "occupation": "后端工程师",
  "industry": "互联网",
  "education": "硕士",
  "family": "单身",
  "monthly_income": 18000,
  "monthly_expense": 9500,
  "assets": { "现金及活期": 80000, "基金": 30000, "股票": 20000 },
  "debts": { "房贷": { "remaining": 800000, "monthly_payment": 6500 } },
  "goals": [
    {
      "id": "g1",
      "name": "二套房首付",
      "target": 500000,
      "current": 130000,
      "deadline": "2031-09-16",
      "priority": "high"
    }
  ],
  "risk_tolerance": "balanced",
  "investing_years": 5,
  "liquidity_needs": "medium",
  "marital_status": "未婚",
  "last_updated": "2026-09-22T10:30:00"
}
```

> 示例中 `children`、`supporting_parents` 未提供 —— 前端资料卡将显示「子女情况待确认」「赡养父母情况待确认」，完整度为 10/12 ≈ 83%。

### 2.7 规划中接口（`POST` / `PATCH /api/profile`）

| 项 | POST（创建） | PATCH（更新） |
|---|---|---|
| 语义 | 默认值叠加传入字段，**整体覆盖**已存在画像 | **浅合并**：仅覆盖传入字段，未传字段保留 |
| `assets`/`debts`/`goals` | 整体替换 | 整体替换（不做键级合并） |
| 非法字段 | 忽略（仅接受 2.2 契约内字段，剔除 `null`） | 同左 |
| 响应 | 写入后的完整画像（含服务端生成的 `last_updated`） | 同左 |

> 对应 `core/services/profile.py` 的 `create_profile()` / `update_profile()`，service 层已实现，HTTP 路由待添加。

## 3. 其他接口（占位，待补充）

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | `/api/health` | 健康检查，前端据此判断是否降级 mock | 已实现 |
| GET | `/api/dashboard` | 仪表盘聚合数据 | 已实现（静态数据） |
| POST | `/api/chat` | Agent SSE 流式对话 | 已实现，事件协议见 `frontend/API_CONTRACT.md` 第 4 节 |
| — | 资产模块 | `core/services/asset.py` | service 占位，接口待定义 |
| — | 交易流水模块 | `core/services/transaction.py` | service 占位，接口待定义 |

## 4. 数据存储约定（后端内部）

- `financial_profiles` 表单行记录（`id = OWNER_ID = 1`），完整画像 JSON 存于 `profile_data` 字段；
- `monthly_income`、`risk_preference` 冗余到独立列，便于将来直接 SQL 统计；
- 建表语句见 `core/database/_first.py`，读写逻辑见 `core/services/profile.py`。

## 5. 待对齐事项（TODO）

1. `api/routes/profile.py` 移除静态 `PROFILE`，接入 `core.services.profile.get_profile()`；
2. `service` 层 `CORE_FIELDS` 与 `_default_profile()` 扩展 8 个新字段（`city`、`industry`、`education`、`investing_years`、`liquidity_needs`、`marital_status`、`children`、`supporting_parents`），完善度清单由 10 项对齐为 12 项；
3. （可选）添加 `POST` / `PATCH /api/profile` 路由；
4. 前端 `frontend/API_CONTRACT.md` 的 profile 示例同步本文字段。

## 6. 变更记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-09-22 | v0.1 | 初始版本：Profile 模块字段要求（含新增 8 个资料字段、量化标准、派生指标规则、规划中接口） |
