# WealthPilot Tool 生态清单

> 版本：MVP 规划参考

## 说明

本清单围绕个人财富规划 Agent 的业务流程整理。优先实现核心数据读写、财务计算、目标管理与计划制定，再逐步扩展日历、复盘、情景模拟和外部信息能力。

**优先级说明：**

| 标记 | 含义 |
|------|------|
| P0 | 核心必备，优先开发 |
| P1 | 增强体验，核心闭环稳定后开发 |
| P2 | 后续扩展，按需求开发 |

**状态标记说明：**

| 标记 | 含义 |
|------|------|
| 已完成 | 已实现并通过 `route()` 调用验证 |
| 暂未实现 | 尚未开发，待后续迭代 |

---

## 一、用户财务数据与记忆工具（P0）

| # | Tool 名称 | 说明 | 状态 |
|---|-----------|------|------|
| 1 | `get_financial_profile` | 获取用户当前财务画像，例如收入概况、现金、投资资产、负债、收入稳定性等。`basic_info_tools.py` | 已完成 |
| 2 | `update_financial_profile` | 更新长期财务属性，例如收入变化、收入稳定性、风险偏好等。**注：已拆分为** `update_basic_info`（基本信息）+ `record_asset`/`update_asset`（资产）+ `record_debt`/`update_debt`（负债）+ `create_goal`/`update_goal`（目标）等独立工具，原 `update_financial_profile` 不再作为单一工具存在。 | 已完成 |
| 3 | `record_income` | 记录一笔收入，包含金额、来源、发生日期、备注等。`transaction_tools.py` | 已完成 |
| 4 | `record_expense` | 记录一笔支出，包含金额、类别、发生日期、是否固定支出等。`transaction_tools.py` | 已完成 |
| 5 | `get_transactions` | 按时间、类型、类别等条件查询收入和支出流水。`transaction_tools.py` | 已完成 |
| 6 | `update_transaction` | 修改已有流水，例如修正金额、日期或分类。**注：实现名为** `update_transaction_tool`（避免与 service 层函数同名冲突）。`transaction_tools.py` | 已完成 |
| 7 | `delete_transaction` | 删除用户确认要删除的错误流水。**注：实现名为** `delete_transaction_tool`（避免与 service 层函数同名冲突）。`transaction_tools.py` | 已完成 |
| 8 | `get_monthly_cashflow` | 统计指定月份的收入、支出、结余及储蓄率（由流水按月聚合派生）。`cashflow_tools.py` | 已完成 |

**设计要点：**
- 收入和支出流水应与用户当前财务画像分开存储。
- 月度现金流等确定性结果由 Python/SQL 计算，不依赖 LLM 心算。
- **月度现金流是派生值，没有写工具**：`transactions` 是收支唯一数据源，结余 = 收入合计 − 支出合计，
  由 `core/services/cashflow.py` 现算。原 `set_monthly_cashflow`（自述"主要用于测试或数据补录"）
  已下线——它与流水构成第二份"真相"，会造成"记了账但月结余不变"。
- 对金额、日期、分类存在歧义的输入，应先追问或确认。
- 写入前使用 Pydantic 校验参数，并由 Service 层执行业务规则。

---

## 二、资产、负债与财务健康工具（P0/P1）

| # | Tool 名称 | 说明 | 状态 |
|---|-----------|------|------|
| 1 | `get_assets` | 查询现金、存款、投资资产及其他资产明细。`asset_tools.py` | 已完成 |
| 2 | `record_asset` | 新增资产或记录资产估值。`asset_tools.py` | 已完成 |
| 3 | `update_asset` | 更新资产余额、估值、名称或类型。底层与 `record_asset` 共用 UPSERT 语义。`asset_tools.py` | 已完成 |
| 4 | `get_debts` | 查询贷款、信用卡欠款及其他负债。`debt_tools.py` | 已完成 |
| 5 | `record_debt` | 新增负债，记录余额、利率、还款日等。`debt_tools.py` | 已完成 |
| 6 | `update_debt` | 更新负债余额、利率、还款状态等。底层与 `record_debt` 共用 UPSERT 语义。`debt_tools.py` | 已完成 |
| 7 | `calculate_net_worth` | 计算净资产：资产总额减去负债总额。`asset_tools.py` | 已完成 |
| 8 | `calculate_emergency_fund` | 根据必要月支出和目标保障月数，计算应急资金目标、现有覆盖程度及缺口。`asset_tools.py` | 已完成 |
| 9 | `calculate_savings_rate` | 计算指定期间的储蓄率，并返回计算口径和所用数据。`cashflow_tools.py` | 已完成 |

---

## 三、财富目标管理工具（P0）

| # | Tool 名称 | 说明 | 状态 |
|---|-----------|------|------|
| 1 | `create_goal` | 创建目标，记录名称、目标金额、截止日期、优先级等。`goal_tools.py` | 已完成 |
| 2 | `get_goals` | 查询用户目标，支持按状态、优先级筛选。`goal_tools.py` | 已完成 |
| 3 | `update_goal` | 修改目标金额、截止日期、优先级或其他属性。`goal_tools.py` | 已完成 |
| 4 | `complete_goal` | 将已达成目标标记为完成，并记录完成情况。**注：实现名为** `complete_goal_tool`（避免与 service 层函数同名冲突）。`goal_tools.py` | 已完成 |
| 5 | `calculate_goal_contribution` | 根据目标金额、当前已储备金额和剩余期限，计算所需定期储蓄额。`goal_tools.py` | 已完成 |
| 6 | `check_goal_feasibility` | 结合现金流、已有目标和必要支出，评估目标在给定假设下的可行性。 | 暂未实现 |
| 7 | `prioritize_goals` | 根据用户设定的优先级、期限、必要性及资金约束，整理目标间的资金分配方案。 | 暂未实现 |

---

## 四、财富计划与预算工具（P0）

| # | Tool 名称 | 说明 | 状态 |
|---|-----------|------|------|
| 1 | `generate_monthly_plan` | 根据财务画像、目标、必要支出和现金流生成月度资金分配方案。 | 暂未实现 |
| 2 | `get_current_wealth_plan` | 查询当前生效的财富计划。 | 暂未实现 |
| 3 | `update_wealth_plan` | 根据用户确认或财务状态变化修改现有计划。 | 暂未实现 |
| 4 | `get_budget` | 查询指定月份或类别的预算额度及使用情况。 | 暂未实现 |
| 5 | `set_budget` | 创建或调整总预算、分类预算。 | 暂未实现 |
| 6 | `check_budget_status` | 对比实际支出与预算，返回剩余额度、超支情况和使用比例。 | 暂未实现 |
| 7 | `allocate_monthly_surplus` | 按已确认规则，将可分配结余分配到应急资金、短期目标和其他目标。 | 暂未实现 |

**计划操作原则：**
- `generate_monthly_plan` 生成建议，不应自动视为用户已确认的计划。
- 由用户确认后，再调用 `update_wealth_plan` 等工具保存正式计划。

---

## 五、财务日历与执行工具（P1）

| # | Tool 名称 | 说明 | 状态 |
|---|-----------|------|------|
| 1 | `get_upcoming_financial_actions` | 查询未来一段时间的财务行动，例如房租、还款、储蓄任务和月度复盘。 | 暂未实现 |
| 2 | `create_financial_action` | 创建行动，记录名称、日期、金额、关联目标及状态。 | 暂未实现 |
| 3 | `update_financial_action` | 修改行动日期、金额、备注或执行状态。 | 暂未实现 |
| 4 | `complete_financial_action` | 标记行动完成，并记录完成时间和实际金额。 | 暂未实现 |
| 5 | `get_calendar_events` | 查询指定日期范围内的财务日历事件。 | 暂未实现 |
| 6 | `reschedule_financial_action` | 调整未完成行动的日期，并保留原计划信息。 | 暂未实现 |

**日历事件应区分：**
- **计划事件**：预计发生的事项。
- **实际事件**：已经发生的交易或行动。
- **提醒事项**：到时间需要用户处理的行动。

不能将计划事件自动记成已发生的交易。

---

## 六、月度复盘与计划调整工具（P1）

| # | Tool 名称 | 说明 | 状态 |
|---|-----------|------|------|
| 1 | `generate_monthly_review` | 汇总指定月份的计划与实际情况，生成复盘数据。 | 暂未实现 |
| 2 | `get_monthly_review` | 查询历史复盘记录。 | 暂未实现 |
| 3 | `record_review_feedback` | 记录用户对复盘结论、目标完成情况或计划调整的反馈。 | 暂未实现 |
| 4 | `analyze_budget_deviation` | 分析实际支出与预算差异，识别主要偏差类别。 | 暂未实现 |
| 5 | `suggest_plan_adjustments` | 根据现金流变化、目标进度和复盘结果提出可选调整方案。 | 暂未实现 |
| 6 | `apply_plan_adjustment` | 用户确认后，将选定调整应用到财富计划。 | 暂未实现 |

---

## 七、情景模拟与财务测算工具（P1）

| # | Tool 名称 | 说明 | 状态 |
|---|-----------|------|------|
| 1 | `simulate_savings_scenario` | 模拟不同收入、支出或储蓄金额下的资金积累过程。 | 暂未实现 |
| 2 | `simulate_goal_timeline` | 模拟不同储蓄方案下达到目标的时间。 | 暂未实现 |
| 3 | `simulate_income_change` | 分析收入上升、下降或中断对现金流和目标的影响。 | 暂未实现 |
| 4 | `simulate_expense_change` | 分析房租、生活成本或其他支出变化对计划的影响。 | 暂未实现 |
| 5 | `simulate_market_downturn` | 在用户提供的市场下跌假设下，测算投资资产变化及其对整体财务状况的影响。 | 暂未实现 |
| 6 | `compare_wealth_scenarios` | 对比多个情景的结余、目标进度、现金储备等指标。 | 暂未实现 |

> 情景模拟必须注明假设，特别是投资收益率、波动或下跌幅度；结果不是收益承诺或确定性预测。

---

## 八、财务知识与外部信息工具（P2）

| # | Tool 名称 | 说明 | 状态 |
|---|-----------|------|------|
| 1 | `search_financial_knowledge` | 从经过筛选的知识库检索预算、储蓄、债务管理等内容。 | 暂未实现 |
| 2 | `get_financial_term_explanation` | 获取财务术语解释，帮助用户理解专业概念。 | 暂未实现 |
| 3 | `search_public_financial_information` | 检索公开金融信息，并返回来源和时间信息。 | 暂未实现 |
| 4 | `get_market_data` | 获取所接入数据源提供的市场行情或历史数据。 | 暂未实现 |
| 5 | `get_inflation_data` | 获取指定地区和期间的通胀数据，用于长期购买力情景分析。 | 暂未实现 |

> 外部数据需注明来源、更新时间、适用地区和授权范围。没有真实数据源时，不得将模型生成内容冒充实时行情或官方统计。

---

## 九、建议开发顺序

### 阶段 1：数据读写闭环 — 已完成

| Tool 名称 | 状态 |
|-----------|------|
| `record_income` | 已完成 |
| `record_expense` | 已完成 |
| `get_transactions` | 已完成 |
| `get_financial_profile` | 已完成 |
| `update_financial_profile` | 已完成（拆分） |
| `get_monthly_cashflow` | 已完成 |

> 验收目标：用户通过自然语言记账，之后可以准确查询流水和现金流。

### 阶段 2：财富目标与计划 — 部分完成

| Tool 名称 | 状态 |
|-----------|------|
| `create_goal` | 已完成 |
| `get_goals` | 已完成 |
| `update_goal` | 已完成 |
| `check_goal_feasibility` | 暂未实现 |
| `calculate_goal_contribution` | 已完成 |
| `generate_monthly_plan` | 暂未实现 |
| `get_current_wealth_plan` | 暂未实现 |
| `update_wealth_plan` | 暂未实现 |

> 验收目标：用户可以创建目标、评估可行性，并获得可保存和调整的月度计划。

### 阶段 3：资产负债与财务健康 — 已完成

| Tool 名称 | 状态 |
|-----------|------|
| `get_assets` | 已完成 |
| `get_debts` | 已完成 |
| `calculate_net_worth` | 已完成 |
| `calculate_emergency_fund` | 已完成 |
| `calculate_savings_rate` | 已完成 |

> 验收目标：基于资产、负债和现金流分析财务状况。

### 阶段 4：日历、执行与复盘 — 暂未实现

| Tool 名称 | 状态 |
|-----------|------|
| `get_calendar_events` | 暂未实现 |
| `create_financial_action` | 暂未实现 |
| `complete_financial_action` | 暂未实现 |
| `generate_monthly_review` | 暂未实现 |
| `analyze_budget_deviation` | 暂未实现 |
| `suggest_plan_adjustments` | 暂未实现 |

> 验收目标：形成目标、计划、执行、复盘、调整的循环。

### 阶段 5：情景模拟与知识扩展 — 暂未实现

| Tool 名称 | 状态 |
|-----------|------|
| `simulate_savings_scenario` | 暂未实现 |
| `simulate_goal_timeline` | 暂未实现 |
| `compare_wealth_scenarios` | 暂未实现 |
| 知识检索及外部数据工具 | 暂未实现 |

> 验收目标：用户可以比较不同方案的影响，并理解每种方案所依赖的假设。

---

## 十、Tool 设计原则

1. 一个 Tool 尽量只承担一个清晰业务职责，避免把大量操作混成一个万能工具。
2. 区分查询、计算和写入工具，便于测试、权限控制和故障排查。
3. 所有财务数据操作都要进行参数校验和业务规则校验。
4. 修改、删除和正式计划变更应有明确授权策略。
5. Tool 返回结构化结果，例如 `success`、`data`、`error`，供 Agent 解释。
6. LLM 负责理解用户意图和解释结果；Tool 负责执行；Service 负责业务逻辑；数据库负责持久化。
7. 一个 Agent 配合统一 Tool Registry 和多个职责清晰的工具模块，足以支撑当前阶段。

> 建议：不要为了工具数量而堆砌功能。优先把数据读写、目标、计划做成端到端闭环，再扩展日历、复盘和模拟能力。

---

## 附：当前实现统计

**已实现工具：25 个**（分布在 8 个 `tools_hub` 模块中；不含 RAG 的 `search_knowledge`）

| 模块 | 工具数 | 工具列表 |
|------|--------|----------|
| `basic_info_tools.py` | 2 | `get_financial_profile`, `update_basic_info` |
| `asset_tools.py` | 5 | `get_assets`, `record_asset`, `update_asset`, `calculate_net_worth`, `calculate_emergency_fund` |
| `debt_tools.py` | 3 | `get_debts`, `record_debt`, `update_debt` |
| `goal_tools.py` | 5 | `create_goal`, `get_goals`, `update_goal`, `complete_goal_tool`, `calculate_goal_contribution` |
| `transaction_tools.py` | 5 | `record_income`, `record_expense`, `get_transactions`, `update_transaction_tool`, `delete_transaction_tool` |
| `cashflow_tools.py` | 2 | `get_monthly_cashflow`, `calculate_savings_rate` |
| `withdraw_tools.py` | 1 | `request_withdraw`（**只读提案**：生成撤回确认卡片，不执行删除） |
| `memory_tools.py` | 2 | `remember_note`, `forget_note`（跨会话偏好/约束/事实；非结构化部分） |

**暂未实现工具：34 个**

| 分类 | 数量 |
|------|------|
| 财富计划与预算 | 7 |
| 财务日历与执行 | 6 |
| 月度复盘与计划调整 | 6 |
| 情景模拟与财务测算 | 6 |
| 财务知识与外部信息 | 5 |
| 目标可行性分析 | 2 |
| budget 相关 | 2 |
