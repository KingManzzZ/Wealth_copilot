import { useCallback, useEffect, useRef, useState } from "react"
import {
  createConversation,
  deleteConversationApi,
  fetchConversationMessages,
  listConversations,
  streamChat,
} from "@/lib/api"
import { emitChatTurnCompleted } from "@/lib/events"
import type {
  ChatMessage,
  ChatStreamEvent,
  ConversationMeta,
  HistoryMessage,
  StoredMessage,
  ToolCall,
} from "@/types"

const uid = () => Math.random().toString(36).slice(2, 11)

// 刷新即新会话：不保留任何本地锚点，历史会话通过下拉进入

/**
 * 把前端的 ChatMessage 列表还原成后端需要的 OpenAI 格式历史消息：
 * - user → { role: "user", content }
 * - assistant → { role: "assistant", content, tool_calls? }
 *   每个有 result 的 toolCall 紧跟一条 { role: "tool", content, tool_call_id }
 */
function buildHistory(messages: ChatMessage[]): HistoryMessage[] {
  const history: HistoryMessage[] = []
  for (const m of messages) {
    if (m.role === "user") {
      history.push({ role: "user", content: m.content })
    } else if (m.role === "assistant") {
      const calls = m.toolCalls ?? []
      const tool_calls = calls
        .filter((tc) => tc.name)
        .map((tc) => ({
          id: tc.id,
          type: "function" as const,
          function: {
            name: tc.name,
            arguments: JSON.stringify(tc.arguments ?? {}),
          },
        }))
      const assistantMsg: HistoryMessage = {
        role: "assistant",
        content: m.content,
      }
      if (tool_calls.length) {
        assistantMsg.tool_calls = tool_calls
      }
      history.push(assistantMsg)

      for (const tc of calls) {
        if (tc.result !== undefined) {
          history.push({
            role: "tool",
            content:
              typeof tc.result === "string"
                ? tc.result
                : JSON.stringify(tc.result),
            tool_call_id: tc.id,
          })
        }
      }
    }
  }
  return history
}

/** 后端存储消息 → 前端 ChatMessage（DB 行 id 加前缀转字符串，避免与 uid 冲突） */
function toChatMessage(m: StoredMessage): ChatMessage {
  return {
    id: `db-${m.id}`,
    role: m.role,
    content: m.content,
    toolCalls: m.tool_calls ?? [],
    createdAt: Date.now(),
  }
}

/**
 * 聊天状态管理 hook（GPT 式多会话）
 * - 会话行惰性创建：新会话纯前端状态，发出首条消息时才建行
 * - 刷新即新会话：挂载时只拉历史列表，不恢复上次停留的会话
 * - 每轮流结束后把 user + assistant 消息批量落库（一次写入，不逐事件写）
 */
export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [conversations, setConversations] = useState<ConversationMeta[]>([])
  const [conversationId, setConversationId] = useState<number | null>(null)
  const assistantIdRef = useRef<string | null>(null)
  const conversationIdRef = useRef<number | null>(null)
  const messagesRef = useRef<ChatMessage[]>([])

  // 用 ref 同步最新 messages，避免闭包捕获旧值
  const syncRef = (next: ChatMessage[]) => {
    messagesRef.current = next
    setMessages(next)
  }

  const refreshConversations = useCallback(async () => {
    setConversations(await listConversations())
  }, [])

  // 登记当前会话 id（state + ref）
  const adoptConversation = useCallback((id: number) => {
    conversationIdRef.current = id
    setConversationId(id)
  }, [])

  // 挂载：只拉历史列表供下拉显示；刷新即新会话，不恢复上次停留的会话
  useEffect(() => {
    void refreshConversations()
  }, [refreshConversations])

  /** 开始新会话：纯前端重置，不发请求（发了首条消息才真正建行） */
  const startNewConversation = useCallback(() => {
    conversationIdRef.current = null
    setConversationId(null)
    syncRef([])
  }, [])

  /** 进入历史会话：懒加载该会话消息并还原 */
  const selectConversation = useCallback(
    async (id: number) => {
      if (isStreaming || id === conversationIdRef.current) return
      const storedMessages = await fetchConversationMessages(id)
      if (storedMessages === null) {
        // 会话可能已被删除，刷新列表兜底
        void refreshConversations()
        return
      }
      adoptConversation(id)
      syncRef(storedMessages.map(toChatMessage))
    },
    [adoptConversation, isStreaming, refreshConversations]
  )

  /** 删除会话；若删除的是当前会话则重置为新会话 */
  const removeConversation = useCallback(
    async (id: number) => {
      const ok = await deleteConversationApi(id)
      if (!ok) return
      setConversations((list) => list.filter((c) => c.id !== id))
      if (conversationIdRef.current === id) startNewConversation()
    },
    [startNewConversation]
  )

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim()
      if (!trimmed || isStreaming) return

      // 首条消息发出时才真正创建会话；后端不可用（mock 模式）则 convId 保持 null，本轮不落库
      let convId = conversationIdRef.current
      if (convId === null) {
        const conv = await createConversation()
        if (conv) {
          adoptConversation(conv.id)
          convId = conv.id
        }
      }

      const userMsg: ChatMessage = {
        id: uid(),
        role: "user",
        content: trimmed,
        createdAt: Date.now(),
      }
      const assistantMsg: ChatMessage = {
        id: uid(),
        role: "assistant",
        content: "",
        toolCalls: [],
        createdAt: Date.now(),
      }
      assistantIdRef.current = assistantMsg.id

      const history = buildHistory(messagesRef.current)

      syncRef([...messagesRef.current, userMsg, assistantMsg])
      setIsStreaming(true)

      try {
        for await (const evt of streamChat(trimmed, history, convId)) {
          const current = messagesRef.current
          syncRef(
            current.map((m) =>
              m.id === assistantIdRef.current ? applyEvent(m, evt) : m
            )
          )
        }
      } catch (e) {
        const current = messagesRef.current
        syncRef(
          current.map((m) =>
            m.id === assistantIdRef.current
              ? {
                  ...m,
                  content: m.content + `\n\n[错误] ${(e as Error).message}`,
                }
              : m
          )
        )
      } finally {
        // 若流在产出任何事件前就失败，assistant 消息既无内容也无工具调用，
        // 只可能残留一个空壳（错误已在 catch 追加到 content）。
        // 如果连错误都没留下（极端情况），移除这条空消息避免 typing 指示器常驻。
        const current = messagesRef.current
        const assistant = current.find((m) => m.id === assistantIdRef.current)
        if (
          assistant &&
          !assistant.content &&
          (!assistant.toolCalls || assistant.toolCalls.length === 0)
        ) {
          syncRef(current.filter((m) => m.id !== assistantIdRef.current))
        }
        setIsStreaming(false)
        assistantIdRef.current = null

        // 本轮消息落库已**移交后端**：POST /api/chat 在服务端读历史、裁剪，
        // 并在流结束时把 user + assistant 写入 messages 表。
        // 前端若再写一次就会产生重复消息（双写），所以这里只刷新会话列表（标题/排序会变）。
        if (convId !== null) {
          void refreshConversations()
        }

        // 失效广播：这一轮的工具调用可能改过库（资产 / 流水 / 基本资料），
        // 通知 Dashboard、UserMenu、流水抽屉自行重拉。
        // 无条件广播——不判断"这轮有没有写"：写入口随工具集增长，判错一次就是静默不刷新，
        // 而消费者只是多拉 1~2 个聚合 GET。详见 lib/events.ts。
        emitChatTurnCompleted({
          conversationId: convId,
          toolNames: assistant?.toolCalls?.map((tc) => tc.name) ?? [],
        })
      }
    },
    [adoptConversation, isStreaming, refreshConversations]
  )

  return {
    messages,
    isStreaming,
    sendMessage,
    conversations,
    conversationId,
    selectConversation,
    startNewConversation,
    removeConversation,
  }
}

function applyEvent(msg: ChatMessage, evt: ChatStreamEvent): ChatMessage {
  switch (evt.type) {
    case "answer":
      return { ...msg, content: msg.content + evt.content }
    case "tool_call": {
      const tc: ToolCall = {
        id: evt.id,
        name: evt.name,
        arguments: evt.arguments,
        status: "running",
        startedAt: Date.now(),
      }
      return { ...msg, toolCalls: [...(msg.toolCalls ?? []), tc] }
    }
    case "tool_result":
      return {
        ...msg,
        toolCalls: (msg.toolCalls ?? []).map((tc) =>
          tc.id === evt.id
            ? {
                ...tc,
                result: evt.result,
                status: evt.status,
                endedAt: Date.now(),
              }
            : tc
        ),
      }
    // thinking / round / done / error 不改消息结构
    default:
      return msg
  }
}
