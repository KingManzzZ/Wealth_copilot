import { AlertTriangle, SearchX } from "lucide-react"
import { cn } from "@/lib/utils"
import type { KnowledgeState } from "@/lib/evidence"

/**
 * 「这轮没查到依据」的独立提示态。
 *
 * 为什么不能只靠回答正文表达：SYSTEM_PROMPT 要求模型在证据不足时如实说明，
 * 但那句话会淹没在整段回答里；用户扫一眼很难判断"这段结论到底有没有出处"。
 * 把状态提到正文之外单独成块，等于给这轮回答盖一个戳。
 *
 * 两档的语义不同，视觉也要不同：
 * - `abstain`（directive）→ 回答**不该**包含事实性结论，用警示色；
 * - `low`（evidence_quality）→ 有资料但相关性弱，用提醒色。
 */
export function KnowledgeNotice({ state }: { state: KnowledgeState }) {
  if (state.kind === "none" || state.kind === "ok") return null

  const abstain = state.kind === "abstain"
  const Icon = abstain ? SearchX : AlertTriangle

  return (
    <div
      className={cn(
        "flex max-w-[85%] items-start gap-2 rounded-xl border px-3 py-2 text-xs shadow-soft",
        abstain
          ? "border-destructive/25 bg-destructive/5"
          : "border-chart-5/35 bg-chart-5/10"
      )}
      role="note"
      data-knowledge-state={state.kind}
    >
      <Icon
        className={cn(
          "mt-0.5 h-3.5 w-3.5 shrink-0",
          abstain ? "text-destructive" : "text-chart-5"
        )}
      />
      <div className="min-w-0 space-y-0.5">
        <p
          className={cn(
            "font-medium",
            abstain ? "text-destructive" : "text-foreground"
          )}
        >
          {abstain ? "知识库中未找到依据" : "证据有限"}
        </p>
        <p className="leading-relaxed text-muted-foreground">
          {state.notice ??
            (abstain
              ? "这轮回答没有引用知识库资料。涉及政策条款、税率与额度时请以官方文件为准。"
              : "检索到的资料相关性较低，以下内容仅供参考，不要当作确定条款引用。")}
        </p>
      </div>
    </div>
  )
}
