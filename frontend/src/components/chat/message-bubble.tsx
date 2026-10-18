import { useCallback, useMemo } from "react"
import { motion } from "framer-motion"
import { cn } from "@/lib/utils"
import type { ChatMessage } from "@/types"
import { evidenceIndexes, knowledgeState } from "@/lib/evidence"
import { KnowledgeNotice } from "./knowledge-notice"
import { Markdown } from "./markdown"
import { ToolCallGroup } from "./tool-call-card"

/**
 * 单条消息。
 *
 * 回答正文走 markdown 渲染（`[n]` 变成可点 chip），
 * 用户消息保持纯文本 —— 用户输入不是 markdown，没必要解析，也不该让 `*` 变成斜体。
 */
export function MessageBubble({
  message,
  onCitation,
}: {
  message: ChatMessage
  onCitation: (index: number) => void
}) {
  const isUser = message.role === "user"
  const isEmpty =
    !message.content && (!message.toolCalls || message.toolCalls.length === 0)

  // index 集合决定哪些 `[n]` 值得渲染成 chip：没有对应证据的就保持原文
  const indexes = useMemo(() => evidenceIndexes(message), [message])
  const hasCitation = useCallback(
    (index: number) => indexes.has(index),
    [indexes]
  )
  const state = useMemo(() => knowledgeState(message), [message])

  return (
    <div
      className={cn(
        "flex flex-col gap-2",
        isUser ? "items-end" : "items-start"
      )}
    >
      {isEmpty && <TypingIndicator />}

      {!isUser && <KnowledgeNotice state={state} />}

      {message.content && (
        <div
          className={cn(
            "max-w-[85%] px-3.5 py-2.5 text-sm",
            isUser
              ? "rounded-2xl rounded-br-md bg-primary text-primary-foreground shadow-soft"
              : "rounded-2xl rounded-bl-md border border-border/60 bg-card text-card-foreground shadow-soft"
          )}
        >
          {isUser ? (
            <div className="whitespace-pre-wrap leading-relaxed">
              {message.content}
            </div>
          ) : (
            <Markdown
              content={message.content}
              hasCitation={hasCitation}
              onCitation={onCitation}
            />
          )}
          {message.stopped && (
            <p
              className="mt-1.5 text-[11px] text-muted-foreground"
              data-message-state="stopped"
            >
              已停止生成
            </p>
          )}
        </div>
      )}

      {message.toolCalls && message.toolCalls.length > 0 && (
        <ToolCallGroup toolCalls={message.toolCalls} />
      )}
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex items-center gap-1.5 rounded-2xl rounded-bl-md border border-border/60 bg-card px-3.5 py-3 shadow-soft">
      {[0, 1, 2].map((i) => (
        <motion.span
          key={i}
          className="h-1.5 w-1.5 rounded-full bg-muted-foreground"
          animate={{ opacity: [0.3, 1, 0.3] }}
          transition={{
            duration: 1.2,
            repeat: Infinity,
            delay: i * 0.2,
          }}
        />
      ))}
    </div>
  )
}
