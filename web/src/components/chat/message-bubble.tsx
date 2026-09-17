import { motion } from "framer-motion"
import { cn } from "@/lib/utils"
import type { ChatMessage } from "@/types"
import { InlineMarkdown } from "./inline-markdown"
import { ToolCallCard } from "./tool-call-card"

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user"
  const isEmpty =
    !message.content && (!message.toolCalls || message.toolCalls.length === 0)

  return (
    <div
      className={cn(
        "flex flex-col gap-2",
        isUser ? "items-end" : "items-start"
      )}
    >
      {isEmpty && <TypingIndicator />}

      {message.content && (
        <div
          className={cn(
            "max-w-[85%] px-3.5 py-2.5 text-sm",
            isUser
              ? "rounded-2xl rounded-br-md bg-primary text-primary-foreground shadow-soft"
              : "rounded-2xl rounded-bl-md border border-border/60 bg-card text-card-foreground shadow-soft"
          )}
        >
          <InlineMarkdown content={message.content} />
        </div>
      )}

      {message.toolCalls?.map((tc) => (
        <ToolCallCard key={tc.id} toolCall={tc} />
      ))}
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
