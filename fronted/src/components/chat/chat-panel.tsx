import { useEffect, useRef } from "react"
import { motion } from "framer-motion"
import { PanelLeft, PanelRight, Sparkles } from "lucide-react"
import { useChat } from "@/hooks/useChat"
import { Button } from "@/components/ui/button"
import type { ChatSide } from "@/types"
import { MessageBubble } from "./message-bubble"
import { ChatInput } from "./chat-input"

interface ChatPanelProps {
  side: ChatSide
  onToggleSide: () => void
}

export function ChatPanel({ side, onToggleSide }: ChatPanelProps) {
  const { messages, isStreaming, sendMessage } = useChat()
  const scrollRef = useRef<HTMLDivElement>(null)

  // 新消息到达时滚到底部
  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  const moveTo = side === "left" ? "右侧" : "左侧"

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 shrink-0 items-center justify-between border-b border-border/50 px-5">
        <span className="text-xs font-medium tracking-wide text-muted-foreground">
          对话
        </span>
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

      <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-5">
        <div className="flex flex-col gap-5">
          {messages.length === 0 && <EmptyState />}
          {messages.map((m) => (
            <motion.div
              key={m.id}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            >
              <MessageBubble message={m} />
            </motion.div>
          ))}
        </div>
      </div>

      <div className="shrink-0 border-t border-border/50 p-3.5">
        <ChatInput onSend={sendMessage} disabled={isStreaming} />
      </div>
    </div>
  )
}

function EmptyState() {
  const suggestions = [
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
          <span
            key={s}
            className="rounded-full border border-border/60 bg-card/70 px-3.5 py-1.5 text-xs text-muted-foreground shadow-soft"
          >
            {s}
          </span>
        ))}
      </div>
    </div>
  )
}
