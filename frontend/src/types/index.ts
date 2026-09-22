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

// ===== 用户画像 =====
export interface UserProfile {
  user_id: string
  name?: string
  age?: number
  occupation?: string
  family?: string
  monthly_income?: number
  monthly_expense?: number
  assets: Record<string, number>
  debts: Record<string, { remaining: number; monthly_payment: number }>
  goals: FinancialGoal[]
  risk_tolerance?: "conservative" | "balanced" | "aggressive"
  last_updated?: string
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
