import { useState } from "react"
import { AnimatePresence, motion } from "framer-motion"
import { Check, ChevronDown, Loader2, Terminal, X } from "lucide-react"
import { cn } from "@/lib/utils"
import type { ToolCall } from "@/types"

const statusConfig = {
  running: {
    icon: Loader2,
    className: "animate-spin text-muted-foreground",
    label: "执行中",
  },
  success: { icon: Check, className: "text-chart-2", label: "完成" },
  error: { icon: X, className: "text-destructive", label: "失败" },
} as const

export function ToolCallCard({ toolCall }: { toolCall: ToolCall }) {
  const [expanded, setExpanded] = useState(false)
  const c = statusConfig[toolCall.status]
  const Icon = c.icon

  return (
    <div className="w-full max-w-[85%] overflow-hidden rounded-xl border border-border/60 bg-muted/40 text-xs shadow-soft">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left transition-colors hover:bg-accent/50"
      >
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-chart-1/10 text-chart-1">
          <Terminal className="h-3 w-3" />
        </span>
        <span className="font-mono text-[11px] text-foreground">
          {toolCall.name}
        </span>
        <Icon className={cn("h-3.5 w-3.5 shrink-0", c.className)} />
        <span className="text-muted-foreground">{c.label}</span>
        <ChevronDown
          className={cn(
            "ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-300",
            expanded && "rotate-180"
          )}
        />
      </button>
      <AnimatePresence initial={false}>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden border-t border-border/50"
          >
            <div className="space-y-3 p-3.5">
              <div>
                <p className="mb-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                  参数
                </p>
                <pre className="overflow-x-auto rounded-lg bg-background/70 p-2.5 font-mono text-[11px] leading-relaxed shadow-sink">
                  {JSON.stringify(toolCall.arguments, null, 2)}
                </pre>
              </div>
              {toolCall.result !== undefined && (
                <div>
                  <p className="mb-1.5 text-[10px] uppercase tracking-wider text-muted-foreground">
                    返回
                  </p>
                  <pre className="overflow-x-auto rounded-lg bg-background/70 p-2.5 font-mono text-[11px] leading-relaxed shadow-sink">
                    {typeof toolCall.result === "string"
                      ? toolCall.result
                      : JSON.stringify(toolCall.result, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
