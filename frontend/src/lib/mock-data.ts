import type {
  Assets,
  BasicInfo,
  ChatStreamEvent,
  DashboardData,
  Debts,
  FinancialGoal,
  MonthlyCashflow,
  Transaction,
} from "@/types"

// ============================================================
// Mock 数据：基于 WealthPilot 典型用户画像
// 月入 1.8 万，房贷 6500/月，目标 5 年攒 50 万首付
// 后端不在线时，前端用这份数据独立跑通展示
// 拆分后的 mock：对应 6 个独立接口
// ============================================================

export const mockBasicInfo: BasicInfo = {
  user_id: "demo-001",
  name: "学习者",
  age: 29,
  city: "上海",
  occupation: "后端工程师",
  industry: "互联网",
  education: "硕士",
  family: "单身",
  risk_tolerance: "balanced",
  investing_years: 5,
  liquidity_needs: "medium",
  marital_status: "未婚",
  // 子女 / 赡养父母情况未提供：用于演示「待确认项」
  last_updated: "2026-09-16",
}

export const mockAssets: Assets = {
  现金及活期: 80000,
  基金: 30000,
  股票: 20000,
}

export const mockDebts: Debts = {
  房贷: { remaining: 800000, monthly_payment: 6500 },
}

export const mockGoals: FinancialGoal[] = [
  {
    id: "g1",
    name: "二套房首付",
    target: 500000,
    current: 130000,
    deadline: "2031-09-16",
    priority: "high",
  },
  {
    id: "g2",
    name: "应急资金（6 个月支出）",
    target: 57000,
    current: 42000,
    deadline: "2027-03-16",
    priority: "medium",
  },
  {
    id: "g3",
    name: "年度旅行基金",
    target: 20000,
    current: 6000,
    deadline: "2027-06-16",
    priority: "low",
  },
]

export const mockCashflow: MonthlyCashflow[] = [
  { month: "2026-04", income: 18000, expense: 9200, surplus: 8800 },
  { month: "2026-05", income: 18000, expense: 10500, surplus: 7500 },
  { month: "2026-06", income: 18000, expense: 8900, surplus: 9100 },
  { month: "2026-07", income: 18500, expense: 9500, surplus: 9000 },
  { month: "2026-08", income: 18000, expense: 8700, surplus: 9300 },
  { month: "2026-09", income: 18000, expense: 9500, surplus: 8500 },
]

export const mockTransactions: Transaction[] = [
  {
    id: 1,
    transaction_type: "income",
    amount: 18000,
    category: "薪资",
    source: "9月工资",
    occurred_at: "2026-09-10T09:00:00",
  },
  {
    id: 2,
    transaction_type: "expense",
    amount: 6500,
    category: "房贷",
    source: "9月房贷",
    occurred_at: "2026-09-05T12:00:00",
  },
  {
    id: 3,
    transaction_type: "expense",
    amount: 200,
    category: "餐饮",
    source: "午餐",
    occurred_at: "2026-09-20T12:30:00",
  },
]

export const mockDashboard: DashboardData = {
  kpis: {
    monthly_income: 18000,
    monthly_expense: 9500,
    monthly_surplus: 8500,
    saving_rate: 0.472,
  },
  cashflow: mockCashflow,
  goals: mockGoals,
  asset_liability: [
    { name: "现金及活期", value: 80000, kind: "asset" },
    { name: "基金", value: 30000, kind: "asset" },
    { name: "股票", value: 20000, kind: "asset" },
    { name: "房贷", value: 800000, kind: "liability" },
  ],
  health: {
    overall: 72,
    grade: "B",
    dimensions: [
      { name: "储蓄率", score: 78, advice: "储蓄率 47%，高于 30% 的健康线，保持。" },
      { name: "应急充足度", score: 65, advice: "应急资金覆盖 4.4 个月支出，建议补足到 6 个月。" },
      { name: "负债比", score: 70, advice: "负债/资产 86%，房贷为主，注意利率上行风险。" },
      { name: "目标进度", score: 75, advice: "首付目标进度 26%，按当前节奏可如期达成。" },
    ],
  },
}

// 模拟 Agent 流式响应：用户问月度现金流 → 触发工具 → 给出回答
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export async function* mockStreamChat(message: string): AsyncGenerator<ChatStreamEvent> {
  const lower = message.toLowerCase()

  if (lower.includes("现金流") || lower.includes("结余") || lower.includes("储蓄")) {
    yield { type: "round", round: 1 }
    await sleep(300)
    yield { type: "thinking", content: "用户在问月度现金流，我需要调用计算工具得到准确数字。" }
    await sleep(400)
    yield {
      type: "tool_call",
      id: "call_mock_1",
      name: "calculate_monthly_cashflow",
      arguments: { monthly_income: 18000, monthly_expense: 9500 },
    }
    await sleep(700)
    yield {
      type: "tool_result",
      id: "call_mock_1",
      result: { monthly_income: 18000, monthly_expense: 9500, surplus: 8500, saving_rate: 0.472 },
      status: "success",
    }
    await sleep(400)
    yield { type: "answer", content: "根据你最近的数据：" }
    await sleep(250)
    yield { type: "answer", content: "月收入 ¥18,000，月支出 ¥9,500，月度结余 **¥8,500**，储蓄率 47.2%。\n\n" }
    await sleep(300)
    yield { type: "answer", content: "这个储蓄率高于 30% 的健康线，状态不错。按当前节奏，一年可攒下约 ¥102,000，对 50 万首付目标贡献可观。\n\n建议：把结余先补足 6 个月应急资金（还差 ¥15,000），再优先投放到首付目标账户。" }
    yield { type: "done" }
    return
  }

  // 默认回复
  yield { type: "answer", content: `已收到：「${message}」。\n\n（这是 mock 模式的回复，后端 FastAPI 接入后将由 WealthPilot Agent 真实作答。你可以试试问「我的月度现金流怎么样」触发工具调用演示。）` }
  yield { type: "done" }
}
