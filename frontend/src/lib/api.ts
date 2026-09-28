import type {
  Assets,
  BasicInfo,
  BackendStatus,
  ChatStreamEvent,
  ConversationMessageInput,
  ConversationMeta,
  DashboardData,
  Debts,
  FinancialGoal,
  HistoryMessage,
  MonthlyCashflow,
  StoredMessage,
  Transaction,
} from "@/types"
import {
  mockAssets,
  mockBasicInfo,
  mockCashflow,
  mockDashboard,
  mockDebts,
  mockGoals,
  mockStreamChat,
  mockTransactions,
} from "./mock-data"

// ============================================================
// API client
// 约定：后端 FastAPI 暴露在 /api/* （Vite dev proxy 已配）
// 后端不在线时自动降级到 mock，保证前端可独立演示
// ============================================================

const API_BASE = "/api"
const HEALTH_TIMEOUT = 5000

let cachedBackendStatus: BackendStatus | null = null

export async function pingBackend(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/health`, {
      signal: AbortSignal.timeout(HEALTH_TIMEOUT),
    })
    return res.ok
  } catch {
    return false
  }
}

export async function detectBackendStatus(): Promise<BackendStatus> {
  // 只缓存 online；offline 不缓存，下次调用会重新探测，避免首次超时后永远走 mock
  if (cachedBackendStatus === "online") return cachedBackendStatus
  const ok = await pingBackend()
  cachedBackendStatus = ok ? "online" : "offline"
  return cachedBackendStatus
}

// ============================================================
// Basic Info
// ============================================================
export async function getBasicInfo(): Promise<BasicInfo> {
  const online = await detectBackendStatus()
  if (online === "offline") return mockBasicInfo
  const res = await fetch(`${API_BASE}/basic_info`)
  if (!res.ok) return mockBasicInfo
  return res.json()
}

export async function upsertBasicInfo(data: Partial<BasicInfo>): Promise<BasicInfo> {
  const res = await fetch(`${API_BASE}/basic_info`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(`upsert basic_info failed: ${res.status}`)
  return res.json()
}

// ============================================================
// Assets
// ============================================================
export async function getAssets(): Promise<Assets> {
  const online = await detectBackendStatus()
  if (online === "offline") return mockAssets
  const res = await fetch(`${API_BASE}/assets`)
  if (!res.ok) return mockAssets
  return res.json()
}

export async function upsertAsset(name: string, value: number): Promise<{ name: string; value: number }> {
  const res = await fetch(`${API_BASE}/assets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, value }),
  })
  if (!res.ok) throw new Error(`upsert asset failed: ${res.status}`)
  return res.json()
}

export async function deleteAsset(name: string): Promise<void> {
  const res = await fetch(`${API_BASE}/assets/${encodeURIComponent(name)}`, {
    method: "DELETE",
  })
  if (!res.ok) throw new Error(`delete asset failed: ${res.status}`)
}

// ============================================================
// Debts
// ============================================================
export async function getDebts(): Promise<Debts> {
  const online = await detectBackendStatus()
  if (online === "offline") return mockDebts
  const res = await fetch(`${API_BASE}/debts`)
  if (!res.ok) return mockDebts
  return res.json()
}

export async function upsertDebt(
  name: string,
  remaining: number,
  monthlyPayment: number,
): Promise<{ name: string; remaining: number; monthly_payment: number }> {
  const res = await fetch(`${API_BASE}/debts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, remaining, monthly_payment: monthlyPayment }),
  })
  if (!res.ok) throw new Error(`upsert debt failed: ${res.status}`)
  return res.json()
}

export async function deleteDebt(name: string): Promise<void> {
  const res = await fetch(`${API_BASE}/debts/${encodeURIComponent(name)}`, {
    method: "DELETE",
  })
  if (!res.ok) throw new Error(`delete debt failed: ${res.status}`)
}

// ============================================================
// Goals
// ============================================================
export async function getGoals(): Promise<FinancialGoal[]> {
  const online = await detectBackendStatus()
  if (online === "offline") return mockGoals
  const res = await fetch(`${API_BASE}/goals`)
  if (!res.ok) return mockGoals
  return res.json()
}

export async function upsertGoal(goal: FinancialGoal): Promise<FinancialGoal> {
  const res = await fetch(`${API_BASE}/goals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(goal),
  })
  if (!res.ok) throw new Error(`upsert goal failed: ${res.status}`)
  return res.json()
}

export async function deleteGoal(goalId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/goals/${encodeURIComponent(goalId)}`, {
    method: "DELETE",
  })
  if (!res.ok) throw new Error(`delete goal failed: ${res.status}`)
}

// ============================================================
// Cashflow
// ============================================================
export async function getCashflow(months = 6): Promise<MonthlyCashflow[]> {
  const online = await detectBackendStatus()
  if (online === "offline") return mockCashflow
  const res = await fetch(`${API_BASE}/cashflow?months=${months}`)
  if (!res.ok) return mockCashflow
  return res.json()
}

export async function upsertCashflow(
  month: string,
  income: number,
  expense: number,
): Promise<MonthlyCashflow> {
  const res = await fetch(`${API_BASE}/cashflow`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ month, income, expense }),
  })
  if (!res.ok) throw new Error(`upsert cashflow failed: ${res.status}`)
  return res.json()
}

// ============================================================
// Transactions
// ============================================================
export async function getTransactions(month?: string): Promise<Transaction[]> {
  const online = await detectBackendStatus()
  if (online === "offline") return mockTransactions
  const url = month
    ? `${API_BASE}/transactions?month=${encodeURIComponent(month)}`
    : `${API_BASE}/transactions`
  const res = await fetch(url)
  if (!res.ok) return mockTransactions
  return res.json()
}

export async function addTransaction(
  data: Omit<Transaction, "id">,
): Promise<Transaction> {
  const res = await fetch(`${API_BASE}/transactions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(`add transaction failed: ${res.status}`)
  return res.json()
}

export async function deleteTransaction(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/transactions/${id}`, {
    method: "DELETE",
  })
  if (!res.ok) throw new Error(`delete transaction failed: ${res.status}`)
}

// ============================================================
// Dashboard (聚合接口，单独保留)
// ============================================================
export async function getDashboard(): Promise<DashboardData> {
  const online = await detectBackendStatus()
  if (online === "offline") return mockDashboard
  const res = await fetch(`${API_BASE}/dashboard`)
  if (!res.ok) return mockDashboard
  return res.json()
}

// ============================================================
// Conversations（会话持久化）
// 后端为唯一真相源：离线时列表为空、读取返回 null、写入静默跳过
// （mock 对话不落库，刷新即清）
// ============================================================
export async function listConversations(): Promise<ConversationMeta[]> {
  const online = await detectBackendStatus()
  if (online === "offline") return []
  try {
    const res = await fetch(`${API_BASE}/conversations`)
    if (!res.ok) return []
    return res.json()
  } catch {
    return []
  }
}

export async function createConversation(
  title?: string,
): Promise<ConversationMeta | null> {
  const online = await detectBackendStatus()
  if (online === "offline") return null
  try {
    const res = await fetch(`${API_BASE}/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: title ?? null }),
    })
    if (!res.ok) return null
    return res.json()
  } catch {
    return null
  }
}

export async function fetchConversationMessages(
  id: number,
): Promise<StoredMessage[] | null> {
  const online = await detectBackendStatus()
  if (online === "offline") return null
  try {
    const res = await fetch(`${API_BASE}/conversations/${id}/messages`)
    if (!res.ok) return null
    return res.json()
  } catch {
    return null
  }
}

export async function appendConversationMessages(
  id: number,
  messages: ConversationMessageInput[],
): Promise<boolean> {
  const online = await detectBackendStatus()
  if (online === "offline") return false
  try {
    const res = await fetch(`${API_BASE}/conversations/${id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages }),
    })
    return res.ok
  } catch {
    return false
  }
}

export async function deleteConversationApi(id: number): Promise<boolean> {
  const online = await detectBackendStatus()
  if (online === "offline") return false
  try {
    const res = await fetch(`${API_BASE}/conversations/${id}`, {
      method: "DELETE",
    })
    return res.ok
  } catch {
    return false
  }
}

/**
 * 流式聊天：返回事件异步迭代器
 * 后端协议：SSE，每行 `data: <JSON>\n`，结尾 `data: [DONE]\n\n`
 * 后端不在线时降级到 mockStreamChat 模拟 Agent 循环
 */
export async function* streamChat(
  message: string,
  history: HistoryMessage[]
): AsyncGenerator<ChatStreamEvent> {
  const online = await detectBackendStatus()

  if (online === "offline") {
    yield* mockStreamChat(message)
    return
  }

  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history }),
  })
  if (!res.ok || !res.body) throw new Error(`chat failed: ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split("\n")
    buffer = lines.pop() ?? ""
    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed.startsWith("data:")) continue
      const payload = trimmed.slice(5).trim()
      if (payload === "[DONE]") return
      try {
        yield JSON.parse(payload) as ChatStreamEvent
      } catch {
        // 忽略无法解析的行
      }
    }
  }
}
