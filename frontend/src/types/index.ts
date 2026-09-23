// ============================================================
// WealthPilot 前端类型定义
// 这些类型同时也是与后端 FastAPI 的数据契约
// ============================================================

// ===== 聊天与 Agent 循环 =====
export type ChatRole = "user" | "assistant" | "tool"

export interface ToolCall {
  id: string
  name: string
  arguments: Record<string, unknown>
  result?: unknown
  status: "running" | "success" | "error"
  startedAt: number
  endedAt?: number
}

export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  /** assistant 消息可能附带一轮工具调用 */
  toolCalls?: ToolCall[]
  createdAt: number
}

/**
 * 发给后端的历史消息：对齐 OpenAI Chat Completions 格式
 * - assistant 带 tool_calls 时，其后必须紧跟对应数量的 tool 消息
 * - tool_calls[].function.arguments 是 JSON 字符串
 */
export interface HistoryToolCall {
  id: string
  type: "function"
  function: { name: string; arguments: string }
}

export type HistoryMessage =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string; tool_calls?: HistoryToolCall[] }
  | { role: "tool"; content: string; tool_call_id: string }

/**
 * SSE 流式事件协议（与后端约定）
 * 每个事件以 `data: <JSON>\n\n` 形式下发
 */
export type ChatStreamEvent =
  | { type: "thinking"; content: string }
  | { type: "tool_call"; id: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_result"; id: string; result: unknown; status: "success" | "error" }
  | { type: "answer"; content: string }
  | { type: "round"; round: number }
  | { type: "done" }
  | { type: "error"; message: string }

// ===== 用户画像（按拆表后结构） =====

/**
 * 基本信息：对齐后端 basic_info 表。
 * 不再含 monthly_income/monthly_expense（从 monthly_cashflow 派生）；
 * 不再含 assets/debts/goals（拆为独立类型，由各自接口返回）。
 */
export interface BasicInfo {
  user_id: string
  name?: string
  age?: number
  /** 所在城市 */
  city?: string
  occupation?: string
  /** 所属行业 */
  industry?: string
  /** 教育程度 */
  education?: string
  family?: string
  risk_tolerance?: "conservative" | "balanced" | "aggressive"
  /** 投资年限（年） */
  investing_years?: number
  /** 流动性需求 */
  liquidity_needs?: "low" | "medium" | "high"
  /** 婚姻状况 */
  marital_status?: string
  /** 子女情况 */
  children?: string
  /** 赡养父母情况 */
  supporting_parents?: string
  last_updated?: string
}

/** 资产：对齐后端 assets 表，键为资产名，值为当前估值 */
export type Assets = Record<string, number>

/** 债务：对齐后端 debts 表，键为债务名，值含剩余本金和月还款额 */
export type Debts = Record<string, { remaining: number; monthly_payment: number }>

/** 月度现金流汇总：对齐后端 monthly_cashflow 表 */
export interface MonthlyCashflow {
  /** 'YYYY-MM' 格式 */
  month: string
  income: number
  expense: number
  /** 派生：income - expense */
  surplus: number
}

/** 单条交易流水：对齐后端 transactions 表 */
export interface Transaction {
  id: number
  transaction_type: "income" | "expense"
  amount: number
  category: string
  source?: string
  occurred_at: string
  note?: string
}

// ===== 仪表盘 =====
export interface FinancialKPIs {
  monthly_income: number
  monthly_expense: number
  monthly_surplus: number
  saving_rate: number // 0-1
}

export interface CashflowPoint {
  month: string
  income: number
  expense: number
  surplus: number
}

export interface FinancialGoal {
  id: string
  name: string
  target: number
  current: number
  deadline: string // ISO date
  priority: "high" | "medium" | "low"
}

export interface AssetLiabilityItem {
  name: string
  value: number
  kind: "asset" | "liability"
}

export interface HealthDimension {
  name: string
  score: number // 0-100
  advice?: string
}

export interface FinancialHealth {
  overall: number // 0-100
  grade: "A" | "B" | "C" | "D"
  dimensions: HealthDimension[]
}

export interface DashboardData {
  kpis: FinancialKPIs
  cashflow: CashflowPoint[]
  goals: FinancialGoal[]
  asset_liability: AssetLiabilityItem[]
  health: FinancialHealth
}

// ===== 后端状态 =====
export type BackendStatus = "online" | "offline" | "checking"

// ===== 布局 =====
/** 聊天面板停靠位置 */
export type ChatSide = "left" | "right"
