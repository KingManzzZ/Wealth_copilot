import type {
  BackendStatus,
  ChatStreamEvent,
  DashboardData,
  HistoryMessage,
  UserProfile,
} from "@/types"
import { mockDashboard, mockProfile, mockStreamChat } from "./mock-data"

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

export async function getProfile(): Promise<UserProfile> {
  const online = await detectBackendStatus()
  if (online === "offline") return mockProfile
  const res = await fetch(`${API_BASE}/profile`)
  if (!res.ok) return mockProfile
  return res.json()
}

export async function getDashboard(): Promise<DashboardData> {
  const online = await detectBackendStatus()
  if (online === "offline") return mockDashboard
  const res = await fetch(`${API_BASE}/dashboard`)
  if (!res.ok) return mockDashboard
  return res.json()
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
