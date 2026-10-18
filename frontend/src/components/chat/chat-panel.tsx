import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { AnimatePresence, motion } from "framer-motion"
import { History, PanelLeft, PanelRight, Sparkles, SquarePen, Trash2 } from "lucide-react"
import { useChat } from "@/hooks/useChat"
import { Button } from "@/components/ui/button"
import { collectEvidence, type EvidenceGroup } from "@/lib/evidence"
import { findWithdrawProposal, type WithdrawProposal } from "@/lib/withdraw"
import type { ChatMessage, ChatSide, ConversationMeta } from "@/types"
import { cn } from "@/lib/utils"
import { EvidenceDrawer } from "./evidence-drawer"
import { MessageBubble } from "./message-bubble"
import { ChatInput } from "./chat-input"

interface ChatPanelProps {
  side: ChatSide
  onToggleSide: () => void
}

/** 证据抽屉的打开状态：哪条消息、哪条被点中 */
interface EvidenceView {
  groups: EvidenceGroup[]
  activeIndex: number
}

export function ChatPanel({ side, onToggleSide }: ChatPanelProps) {
  const {
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
  } = useChat()
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const [inputValue, setInputValue] = useState("")
  const [evidence, setEvidence] = useState<EvidenceView | null>(null)
  const [withdrawError, setWithdrawError] = useState<string | null>(null)
  const [proposalPending, setProposalPending] = useState(false)
  /** 被用户点过「取消」的提案（按消息 id 记，避免它一直弹） */
  const [dismissedProposal, setDismissedProposal] = useState<string | null>(null)

  const currentTitle =
    conversations.find((c) => c.id === conversationId)?.title ?? null

  // 撤回按钮挂在每条**用户消息**下面：含义是"回到发送这条之前"，
  // 所以这一轮和它之后的对话会一起撤掉（否则上下文矛盾）。
  const canWithdraw = !isStreaming && !isStopping

  /**
   * 模型通过 `request_withdraw` 提出的撤回提案。
   *
   * 只认**最后一条**提案；而且目标轮次必须还在消息里（撤回成功后它就不在了，
   * 卡片要自动消失）。用户点过取消的也不再显示。
   */
  const proposalView = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index]
      if (message.role !== "assistant") continue
      const proposal = findWithdrawProposal(message)
      if (!proposal) continue
      if (message.id === dismissedProposal) return null
      const stillExists = messages.some((m) => m.turnId === proposal.turnId)
      if (!stillExists) return null
      return { messageId: message.id, proposal }
    }
    return null
  }, [messages, dismissedProposal])

  // 新消息到达时滚到底部
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  // 点正文里的 [n]：只在这条消息真的有证据时才开抽屉，否则 chip 根本不会渲染
  const openEvidence = useCallback((message: ChatMessage, index: number) => {
    const groups = collectEvidence(message)
    if (!groups.some((g) => g.items.some((i) => i.index === index))) return
    setEvidence({ groups, activeIndex: index })
  }, [])

  /**
   * 撤回从某条用户消息开始的所有轮次：对话消失 + 数据还原。
   * 二次确认后不可恢复——文案必须说清"不可恢复"和"会撤掉几轮"。
   */
  const handleWithdraw = useCallback(
    async (turnId: string, turnCount: number) => {
      if (!canWithdraw) return
      const ok = window.confirm(
        `撤回后不可恢复。\n\n将从这条消息起，移除它及之后的 ${turnCount} 轮对话，` +
          "并还原这些轮次写入账本的数据。"
      )
      if (!ok) return
      setWithdrawError(null)
      try {
        const result = await withdrawTurn(turnId)
        setInputValue(result.user_text)
        requestAnimationFrame(() => {
          const el = inputRef.current
          if (!el) return
          el.focus()
          el.setSelectionRange(el.value.length, el.value.length)
        })
      } catch (e) {
        setWithdrawError((e as Error).message || "撤回失败")
      }
    },
    [canWithdraw, withdrawTurn]
  )

  /**
   * 确认模型提案：卡片上的「确认撤回」就是二次确认，不再额外弹窗。
   * 成功后用服务端消息覆盖本地，卡片随目标轮次一起消失。
   */
  const confirmProposal = useCallback(
    async (proposal: WithdrawProposal) => {
      setProposalPending(true)
      setWithdrawError(null)
      try {
        // 模型提案是"只撤这一轮"：当前这轮（提出请求的这轮）要保留
        const result = await withdrawTurn(proposal.turnId, false)
        setInputValue(result.user_text)
        requestAnimationFrame(() => {
          const el = inputRef.current
          if (!el) return
          el.focus()
          el.setSelectionRange(el.value.length, el.value.length)
        })
      } catch (e) {
        setWithdrawError((e as Error).message || "撤回失败")
      } finally {
        setProposalPending(false)
      }
    },
    [withdrawTurn]
  )

  const dismissProposal = useCallback((messageId: string) => {
    setDismissedProposal(messageId)
  }, [])

  // 点击建议词：只填入输入框并聚焦，由用户手动发送
  const handlePick = (text: string) => {
    setInputValue(text)
    requestAnimationFrame(() => {
      const el = inputRef.current
      if (!el) return
      el.focus()
      const len = el.value.length
      el.setSelectionRange(len, len)
    })
  }

  const handleSend = () => {
    const trimmed = inputValue.trim()
    if (!trimmed) return
    sendMessage(trimmed)
    setInputValue("")
  }

  const moveTo = side === "left" ? "右侧" : "左侧"

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border/50 px-3">
        <div className="flex min-w-0 flex-1 items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 shrink-0"
            onClick={startNewConversation}
            disabled={isStreaming}
            title="开始新对话"
            aria-label="开始新对话"
          >
            <SquarePen className="h-4 w-4" />
          </Button>
          <span className="min-w-0 truncate text-xs font-medium tracking-wide text-muted-foreground">
            {currentTitle ?? "新对话"}
          </span>
        </div>
        <div className="flex shrink-0 items-center">
          <HistoryMenu
            conversations={conversations}
            currentId={conversationId}
            disabled={isStreaming}
            onSelect={selectConversation}
            onDelete={removeConversation}
          />
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onToggleSide}
            title={`把对话栏移到${moveTo}`}
            aria-label={`把对话栏移到${moveTo}`}
          >
            {side === "left" ? (
              <PanelRight className="h-4 w-4" />
            ) : (
              <PanelLeft className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-5">
        <div className="flex flex-col gap-5">
          {messages.length === 0 && <EmptyState onPick={handlePick} />}
          {messages.map((m, index) => {
            // 从这条起还剩几轮（含自己）——二次确认里要告诉用户会撤掉多少
            const turnCount = messages
              .slice(index)
              .filter((item) => item.role === "user").length
            const showWithdraw =
              canWithdraw && m.role === "user" && Boolean(m.turnId)
            const card =
              proposalView && proposalView.messageId === m.id
                ? proposalView.proposal
                : null
            return (
              <motion.div
                key={m.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
              >
                <MessageBubble
                  message={m}
                  onCitation={(citation) => openEvidence(m, citation)}
                  onWithdraw={
                    showWithdraw
                      ? () => handleWithdraw(m.turnId as string, turnCount)
                      : undefined
                  }
                  proposal={card ?? undefined}
                  proposalPending={proposalPending}
                  onConfirmProposal={
                    card ? () => confirmProposal(card) : undefined
                  }
                  onDismissProposal={
                    card ? () => dismissProposal(m.id) : undefined
                  }
                />
              </motion.div>
            )
          })}
        </div>
      </div>

      {withdrawError && (
        <p className="shrink-0 px-5 pb-2 text-[11px] text-destructive">
          {withdrawError}
        </p>
      )}

      <div className="shrink-0 border-t border-border/50 p-3.5">
        <ChatInput
          value={inputValue}
          onChange={setInputValue}
          onSend={handleSend}
          onStop={stopStreaming}
          streaming={isStreaming}
          stopping={isStopping}
          disabled={isStreaming || isStopping}
          textareaRef={inputRef}
        />
      </div>

      <EvidenceDrawer
        open={evidence !== null}
        groups={evidence?.groups ?? []}
        activeIndex={evidence?.activeIndex ?? null}
        onClose={() => setEvidence(null)}
      />
    </div>
  )
}

/** 历史会话下拉：标题 + 相对时间 + 悬浮删除；流式进行中禁用切换避免串台 */
function HistoryMenu({
  conversations,
  currentId,
  disabled,
  onSelect,
  onDelete,
}: {
  conversations: ConversationMeta[]
  currentId: number | null
  disabled: boolean
  onSelect: (id: number) => void
  onDelete: (id: number) => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  // 点击面板外部时收起
  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener("pointerdown", onPointerDown)
    return () => document.removeEventListener("pointerdown", onPointerDown)
  }, [open])

  return (
    <div ref={rootRef} className="relative">
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        title="历史会话"
        aria-label="历史会话"
      >
        <History className="h-4 w-4" />
      </Button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
            className="absolute right-0 top-9 z-50 w-64 overflow-hidden rounded-xl border border-border/60 bg-card shadow-soft"
          >
            <div className="max-h-72 overflow-y-auto p-1.5">
              {conversations.length === 0 ? (
                <p className="px-2.5 py-5 text-center text-xs text-muted-foreground">
                  暂无历史会话
                </p>
              ) : (
                conversations.map((c) => (
                  <div
                    key={c.id}
                    className={cn(
                      "group flex items-center rounded-lg transition-colors hover:bg-accent/50",
                      c.id === currentId && "bg-accent/40"
                    )}
                  >
                    <button
                      type="button"
                      className="min-w-0 flex-1 px-2.5 py-2 text-left"
                      onClick={() => {
                        setOpen(false)
                        onSelect(c.id)
                      }}
                    >
                      <span className="block truncate text-xs text-foreground">
                        {c.title || "未命名会话"}
                      </span>
                      <span className="block text-[10px] text-muted-foreground/70">
                        {formatRelative(c.updated_at)}
                      </span>
                    </button>
                    <button
                      type="button"
                      className="mr-1 rounded p-1.5 text-muted-foreground/50 opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                      title="删除会话"
                      aria-label="删除会话"
                      onClick={() => onDelete(c.id)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** updated_at → 相对时间文案 */
function formatRelative(iso: string): string {
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ""
  const diffMin = Math.floor((Date.now() - t) / 60000)
  if (diffMin < 1) return "刚刚"
  if (diffMin < 60) return `${diffMin} 分钟前`
  const h = Math.floor(diffMin / 60)
  if (h < 24) return `${h} 小时前`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d} 天前`
  return new Date(iso).toLocaleDateString()
}

function EmptyState({ onPick }: { onPick: (text: string) => void }) {
  // 第一条是写入引导：Agent 能记账这件事本身（CX-7）比"能查询"更需要被看见
  const suggestions = [
    "帮我记一笔今天的午餐 35 元",
    "我的月度现金流怎么样？",
    "5 年内能攒够 50 万首付吗？",
    "应急资金够吗？",
  ]
  return (
    <div className="flex flex-col items-center gap-4 py-14 text-center">
      <span className="flex h-11 w-11 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Sparkles className="h-5 w-5" />
      </span>
      <div className="space-y-1">
        <p className="text-sm font-medium">和 WealthPilot 开始对话</p>
        <p className="text-xs leading-relaxed text-muted-foreground">
          聊聊你的收入、支出与目标
        </p>
      </div>
      <div className="mt-1 flex flex-col items-center gap-2">
        {suggestions.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onPick(s)}
            className="rounded-full border border-border/60 bg-card/70 px-3.5 py-1.5 text-xs text-muted-foreground shadow-soft transition-colors hover:border-primary/40 hover:bg-accent/50 hover:text-foreground"
          >
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}
