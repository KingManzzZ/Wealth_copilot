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

/** 展开后单条工具调用的详情子卡：再次点击头部可折叠参数与返回 */
function ToolCallItem({ toolCall }: { toolCall: ToolCall }) {
  const [expanded, setExpanded] = useState(false)
  const c = statusConfig[toolCall.status]
  const Icon = c.icon
  const duration =
    toolCall.endedAt !== undefined
      ? `${((toolCall.endedAt - toolCall.startedAt) / 1000).toFixed(1)}s`
      : null

  return (
    <div className="overflow-hidden rounded-lg border border-border/40 bg-background/50">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left transition-colors hover:bg-accent/50"
      >
        <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded bg-chart-1/10 text-chart-1">
          <Terminal className="h-2.5 w-2.5" />
        </span>
        <span className="font-mono text-[11px] text-foreground">
          {toolCall.name}
        </span>
        <Icon className={cn("h-3 w-3 shrink-0", c.className)} />
        <span className="text-muted-foreground">{c.label}</span>
        {duration && (
          <span className="text-[10px] text-muted-foreground/70">
            {duration}
          </span>
        )}
        <ChevronDown
          className={cn(
            "ml-auto h-3 w-3 shrink-0 text-muted-foreground transition-transform duration-300",
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
            className="overflow-hidden border-t border-border/40"
          >
            <div className="space-y-2.5 p-2.5">
              <Section
                title="参数"
                content={JSON.stringify(toolCall.arguments, null, 2)}
              />
              {toolCall.result !== undefined && (
                <Section
                  title="返回"
                  content={
                    typeof toolCall.result === "string"
                      ? toolCall.result
                      : JSON.stringify(toolCall.result, null, 2)
                  }
                />
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function Section({ title, content }: { title: string; content: string }) {
  return (
    <div>
      <p className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">
        {title}
      </p>
      <pre className="max-h-48 overflow-auto rounded-lg bg-muted/50 p-2 font-mono text-[11px] leading-relaxed">
        {content}
      </pre>
    </div>
  )
}

/**
 * 一条 assistant 消息内全部工具调用的折叠展示。
 * 收起时仅一行摘要（执行中实时显示当前调用的工具名），
 * 展开后逐项显示参数与返回。
 */
export function ToolCallGroup({ toolCalls }: { toolCalls: ToolCall[] }) {
  const [expanded, setExpanded] = useState(false)

  const running = toolCalls.find((tc) => tc.status === "running")
  const failedCount = toolCalls.filter((tc) => tc.status === "error").length

  const summary = running
    ? `正在调用 ${running.name}…`
    : toolCalls.length === 1
      ? `${toolCalls[0].name} · ${failedCount ? "失败" : "完成"}`
      : failedCount
        ? `${toolCalls.length} 次工具调用 · ${failedCount} 项失败`
        : `${toolCalls.length} 次工具调用 · 全部完成`

  return (
    <div className="w-full max-w-[85%] overflow-hidden rounded-xl border border-border/60 bg-muted/40 text-xs shadow-soft">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-accent/50"
      >
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md bg-chart-1/10 text-chart-1">
          <Terminal className="h-3 w-3" />
        </span>
        <span className="truncate text-muted-foreground">{summary}</span>
        {running ? (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-foreground" />
        ) : failedCount ? (
          <X className="h-3.5 w-3.5 shrink-0 text-destructive" />
        ) : (
          <Check className="h-3.5 w-3.5 shrink-0 text-chart-2" />
        )}
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
            <div className="space-y-2 p-2.5">
              {toolCalls.map((tc) => (
                <ToolCallItem key={tc.id} toolCall={tc} />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
