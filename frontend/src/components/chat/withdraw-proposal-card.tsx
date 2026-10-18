import { AlertTriangle } from "lucide-react"
import { Button } from "@/components/ui/button"
import type { WithdrawProposal } from "@/lib/withdraw"

/**
 * 撤回确认卡片。
 *
 * 为什么由前端渲染而不是模型直接执行：撤回会删对话 + 还原数据，**不可恢复**；
 * 模型只能"提案"，执行必须经过用户点击。卡片本身就是二次确认。
 */
export function WithdrawProposalCard({
  proposal,
  pending,
  onConfirm,
  onDismiss,
}: {
  proposal: WithdrawProposal
  pending: boolean
  onConfirm: () => void
  onDismiss: () => void
}) {
  return (
    <div className="w-full max-w-[85%] rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-xs shadow-soft">
      <div className="flex items-center gap-1.5 font-medium text-destructive">
        <AlertTriangle className="h-3.5 w-3.5" />
        撤回确认
      </div>
      <p className="mt-1.5 leading-relaxed text-muted-foreground">
        {proposal.summary}
      </p>
      <p className="mt-1 text-[11px] text-muted-foreground/80">
        确认后不可恢复：这条及之后的对话会被移除。
      </p>
      <div className="mt-2.5 flex gap-2">
        <Button
          size="sm"
          variant="destructive"
          onClick={onConfirm}
          disabled={pending}
        >
          {pending ? "撤回中…" : "确认撤回"}
        </Button>
        <Button size="sm" variant="outline" onClick={onDismiss} disabled={pending}>
          取消
        </Button>
      </div>
    </div>
  )
}
