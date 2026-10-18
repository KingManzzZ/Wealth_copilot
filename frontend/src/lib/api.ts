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
  StoredMessage,
  Transaction,
} from "@/types"
import {
  mockAssets,
  mockBasicInfo,
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

/**
 * 严格重拉：给「事件驱动的刷新」用（chat-turn-completed）。
 *
 * 与 getBasicInfo 的唯一区别是**不回退 mock**：把 mockBasicInfo 顶上真实资料，
 * 比不刷新更糟——用户会看到姓名/城市凭空变成演示值。失败一律返回 null，
 * 由调用方保持旧值。
 */
export async function refetchBasicInfo(): Promise<BasicInfo | null> {
  const online = await detectBackendStatus()
  if (online === "offline") return null
  try {
    const res = await fetch(`${API_BASE}/basic_info`)
    if (!res.ok) return null
    return (await res.json()) as BasicInfo
  } catch {
    return null
  }
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
// 月度现金流是**派生数据**：后端由 transactions 按月聚合得出，
// 因此没有独立的读写客户端。要改数字请写流水（addTransaction），
// 要读汇总请用 getDashboard().cashflow / .kpis。
// （原 getCashflow / upsertCashflow 是死代码：从未被任何组件调用，
//   upsertCashflow 对应的 POST /api/cashflow 也已在后端下线。）
// ============================================================

// ============================================================
// Transactions
// ============================================================
export async function getTransactions(month?: string): Promise<Transaction[]> {
  const online = await detectBackendStatus()
  if (online === "offline") return filterMockTransactions(month)
  const url = month
    ? `${API_BASE}/transactions?month=${encodeURIComponent(month)}`
    : `${API_BASE}/transactions`
  const res = await fetch(url)
  if (!res.ok) return filterMockTransactions(month)
  return res.json()
}

/**
 * mock 流水也按月份过滤：否则抽屉切到任何月份都显示同一批数据，
 * 会造成"切月份没用"的假象（mock 模式本来就该看起来像真的）。
 */
function filterMockTransactions(month?: string): Transaction[] {
  if (!month) return mockTransactions
  return mockTransactions.filter((t) => t.occurred_at.slice(0, 7) === month)
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

/** 严格重拉 dashboard；失败/离线返回 null（不回退 mock，理由见 refetchBasicInfo） */
export async function refetchDashboard(): Promise<DashboardData | null> {
  const online = await detectBackendStatus()
  if (online === "offline") return null
  try {
    const res = await fetch(`${API_BASE}/dashboard`)
    if (!res.ok) return null
    return (await res.json()) as DashboardData
  } catch {
    return null
  }
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
 *
 * `conversationId` 给了就走**服务端权威路径**：后端自己从库里读历史、做裁剪并落库，
 * 此时 `history` 会被忽略（仍然发过去，是为了兼容尚未升级的后端——它只认 history）。
 *
 * `signal` 用于「停止生成」：中止后 fetch 的响应体会 reject，
 * 调用方（useChat）据此把消息标记为已截断，而不是当作错误。
 *
 * `turnId` 由前端生成本轮 id：停止按钮要拿它去 `POST /api/chat/cancel`。
 * 由前端生成而不是等服务端下发，是为了让"刚发出去就点停止"这个窗口里也有 id 可用。
 */
export async function* streamChat(
  message: string,
  history: HistoryMessage[],
  conversationId?: number | null,
  signal?: AbortSignal,
  turnId?: string
): AsyncGenerator<ChatStreamEvent> {
  const online = await detectBackendStatus()

  if (online === "offline") {
    // mock 分支也要响应停止：否则点了停止仍在逐条吐字，看起来像按钮失效
    for await (const event of mockStreamChat(message)) {
      if (signal?.aborted) throw abortStream()
      yield event
    }
    return
  }

  const res = await fetch(`${API_BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      history,
      ...(conversationId != null ? { conversation_id: conversationId } : {}),
      ...(turnId ? { turn_id: turnId } : {}),
    }),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(`chat failed: ${res.status}`)

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  try {
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
  } finally {
    // 主动中止时 reader 仍处于读取中，取消它避免连接悬挂；
    // 已经读完的情况下 cancel 是无害的
    void reader.cancel().catch(() => {})
  }
}

/** 与浏览器 fetch 中止时同名的错误，便于上层用 name === "AbortError" 统一判断 */
function abortStream(): DOMException {
  return new DOMException("已停止生成", "AbortError")
}

/**
 * 取消一轮生成（幂等）。
 *
 * 刻意**不做** `detectBackendStatus()` 前置探测：停止是用户显式动作，
 * 应该在同一个 tick 里把请求发出去，抢在本地 `abort()` 之前到达服务端，
 * 这样服务端记录的 `stop_reason` 才是 `client` 而不是 `disconnect`。
 *
 * `keepalive` 让请求在页面卸载（关标签页）时也能发完。
 */
export async function cancelChatTurn(turnId: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/chat/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ turn_id: turnId }),
      keepalive: true,
    })
    return res.ok
  } catch {
    // 离线 / 请求失败都只是"没取消成"：本地 abort 已经把 UI 停了
    return false
  }
}
