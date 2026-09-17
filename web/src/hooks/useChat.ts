import { useCallback, useRef, useState } from "react"
import { streamChat } from "@/lib/api"
import type { ChatMessage, ChatStreamEvent, ToolCall } from "@/types"

const uid = () => Math.random().toString(36).slice(2, 11)

/**
 * 聊天状态管理 hook
 * 维护消息列表 + 流式接收 Agent 事件（thinking/tool_call/tool_result/answer）
 */
export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const assistantIdRef = useRef<string | null>(null)
  const messagesRef = useRef<ChatMessage[]>([])

  // 用 ref 同步最新 messages，避免闭包捕获旧值
  const syncRef = (next: ChatMessage[]) => {
    messagesRef.current = next
    setMessages(next)
  }

  const sendMessage = useCallback(async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || isStreaming) return

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

    const history = messagesRef.current.map((m) => ({
      role: m.role,
      content: m.content,
    }))

    syncRef([...messagesRef.current, userMsg, assistantMsg])
    setIsStreaming(true)

    try {
      for await (const evt of streamChat(trimmed, history)) {
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
                content:
                  m.content + `\n\n[错误] ${(e as Error).message}`,
              }
            : m
        )
      )
    } finally {
      setIsStreaming(false)
      assistantIdRef.current = null
    }
  }, [isStreaming])

  return { messages, isStreaming, sendMessage }
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
