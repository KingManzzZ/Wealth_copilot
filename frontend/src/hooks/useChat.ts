import { useCallback, useEffect, useRef, useState } from "react"
import {
  cancelChatTurn,
  createConversation,
  deleteConversationApi,
  fetchConversationMessages,
  listConversations,
  streamChat,
  withdrawConversationTurn,
} from "@/lib/api"
import { emitChatTurnCompleted } from "@/lib/events"
import type {
  ChatMessage,
  ChatStreamEvent,
  ConversationMeta,
  HistoryMessage,
  StoredMessage,
  ToolCall,
  WithdrawResult,
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
    // 历史里的中断轮次也要显示"已停止生成"，否则刷新后看起来像正常说完的
    stopped: m.interrupted === true,
    turnId: m.turn_id,
    createdAt: Date.now(),
  }
}

/** 本轮 id：优先 crypto.randomUUID（localhost/https 可用），否则退化为时间戳随机串 */
function newTurnId(): string {
  const webCrypto = globalThis.crypto
  if (webCrypto && typeof webCrypto.randomUUID === "function") {
    return webCrypto.randomUUID()
  }
  return `t-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
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
  /** 已请求取消、正在等服务端收尾（落库 + 释放会话锁）。此间禁止再发 */
  const [isStopping, setIsStopping] = useState(false)
  const [conversations, setConversations] = useState<ConversationMeta[]>([])
  const [conversationId, setConversationId] = useState<number | null>(null)
  const assistantIdRef = useRef<string | null>(null)
  const conversationIdRef = useRef<number | null>(null)
  const messagesRef = useRef<ChatMessage[]>([])
  /** 当前流的取消句柄；null 表示没有正在进行的生成 */
  const abortRef = useRef<AbortController | null>(null)
  /** 当前轮 id（取消接口按它定位）；null 表示没有进行中的轮次 */
  const turnIdRef = useRef<string | null>(null)

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
      if (!trimmed || isStreaming || isStopping) return

      // 首条消息发出时才真正创建会话；后端不可用（mock 模式）则 convId 保持 null，本轮不落库
      let convId = conversationIdRef.current
      if (convId === null) {
        const conv = await createConversation()
        if (conv) {
          adoptConversation(conv.id)
          convId = conv.id
        }
      }

      // 本轮 id 由前端生成：这样"刚发出就点停止"也有 id 可用，不必等服务端首帧。
      // 同时把它挂在 user / assistant 两条消息上——撤回时按 turnId 找到整轮。
      const turnId = newTurnId()
      turnIdRef.current = turnId

      const userMsg: ChatMessage = {
        id: uid(),
        role: "user",
        content: trimmed,
        turnId,
        createdAt: Date.now(),
      }
      const assistantMsg: ChatMessage = {
        id: uid(),
        role: "assistant",
        content: "",
        toolCalls: [],
        turnId,
        createdAt: Date.now(),
      }
      assistantIdRef.current = assistantMsg.id

      const history = buildHistory(messagesRef.current)

      syncRef([...messagesRef.current, userMsg, assistantMsg])
      setIsStreaming(true)

      // 每轮一个 AbortController：停止按钮调 abort()，fetch 的 body 流会直接 reject。
      // 已经收到的 token 留在界面上（不撤回），只是不再追加。
      const controller = new AbortController()
      abortRef.current = controller

      try {
        for await (const evt of streamChat(
          trimmed,
          history,
          convId,
          controller.signal,
          turnId
        )) {
          const current = messagesRef.current
          syncRef(
            current.map((m) =>
              m.id === assistantIdRef.current ? applyEvent(m, evt) : m
            )
          )
        }
      } catch (e) {
        // 主动中止不是错误：不清空、不追加"[错误]"，只把这条回答标记为被截断
        const aborted =
          (e as Error | undefined)?.name === "AbortError" ||
          controller.signal.aborted
        const current = messagesRef.current
        syncRef(
          current.map((m) =>
            m.id === assistantIdRef.current
              ? aborted
                ? { ...m, stopped: true }
                : {
                    ...m,
                    content: m.content + `\n\n[错误] ${(e as Error).message}`,
                  }
              : m
          )
        )
      } finally {
        if (abortRef.current === controller) abortRef.current = null
        if (turnIdRef.current === turnId) turnIdRef.current = null
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
    [adoptConversation, isStreaming, isStopping, refreshConversations]
  )

  /**
   * 停止当前生成（本地立即停 + 通知服务端取消）。
   *
   * 顺序很重要：
   * 1. **先**把取消请求发出去（同一个 tick，不做状态探测），
   * 2. **再** abort 本地流。反过来会让服务端先看到断连，
   *    落库的 `stop_reason` 变成 `disconnect`，事后分不清"用户停的"还是"掉线"。
   * 3. 等服务端收尾（落库 + 释放会话锁）再放开输入，
   *    否则立刻重发会撞上 409（上一轮仍在生成中）。
   */
  const stopStreaming = useCallback(async () => {
    const turnId = turnIdRef.current
    const pending = turnId ? cancelChatTurn(turnId) : null
    abortRef.current?.abort()
    if (!pending) return
    setIsStopping(true)
    try {
      await pending
    } finally {
      setIsStopping(false)
    }
  }, [])

  /**
   * 撤回一轮：服务端把这一轮的对话标为撤回、还原这一轮写过的数据。
   *
   * 成功后用**服务端消息**覆盖本地（撤回是服务端行为，不能只改 UI），
   * 并广播一次数据失效让仪表盘重拉；返回结果供调用方把用户原话回填输入框。
   */
  const withdrawTurn = useCallback(
    async (turnId: string): Promise<WithdrawResult> => {
      const cid = conversationIdRef.current
      if (cid === null) throw new Error("当前没有会话")
      const result = await withdrawConversationTurn(cid, turnId)
      const stored = await fetchConversationMessages(cid)
      if (stored !== null) syncRef(stored.map(toChatMessage))
      void refreshConversations()
      emitChatTurnCompleted({ conversationId: cid, toolNames: [] })
      return result
    },
    [refreshConversations]
  )

  return {
    messages,
    isStreaming,
    isStopping,
    sendMessage,
    stopStreaming,
    withdrawTurn,
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
    // 服务端确认中止：即使本地没来得及 abort（例如别的标签页触发），这里也能收敛
    case "cancelled":
      return { ...msg, stopped: true }
    // thinking / round / turn / done / error 不改消息结构
    default:
      return msg
  }
}
