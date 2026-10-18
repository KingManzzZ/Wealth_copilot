import { useEffect } from "react"
import * as Dialog from "@radix-ui/react-dialog"
import { motion } from "framer-motion"
import { BookOpen, X } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { EvidenceGroup, EvidenceQuality } from "@/lib/evidence"

const QUALITY_STYLE: Record<
  EvidenceQuality,
  { label: string; className: string }
> = {
  high: { label: "证据充分", className: "border-chart-2/40 bg-chart-2/12 text-chart-2" },
  medium: { label: "证据一般", className: "border-chart-5/40 bg-chart-5/12 text-chart-5" },
  low: { label: "证据不足", className: "border-chart-3/45 bg-chart-3/12 text-chart-3" },
}

/**
 * 引用抽屉：点正文里的 `[n]` 打开，右侧滑出，展示这次检索拿到的证据原文。
 *
 * 三个刻意的决定：
 * - **非模态**：它是"查一下出处"，不是对话框。打开时仍能和 Agent 继续对话，
 *   否则「读证据 → 追问」这个动作每轮都要关一次抽屉。
 * - **按检索分节**：一轮里可能检索多次，`index` 每次从 1 重新编号；
 *   合并展示会造出"编号全局唯一"的假象，分节才是事实。
 * - **选中项自动滚入视野**：点 `[3]` 时要直接看到第 3 条，而不是从头找。
 */
export function EvidenceDrawer({
  open,
  groups,
  activeIndex,
  onClose,
}: {
  open: boolean
  groups: EvidenceGroup[]
  activeIndex: number | null
  onClose: () => void
}) {
  const total = groups.reduce((sum, group) => sum + group.items.length, 0)

  useEffect(() => {
    if (!open || activeIndex == null) return
    const el = document.querySelector(`[data-evidence-index="${activeIndex}"]`)
    if (el instanceof HTMLElement) {
      el.scrollIntoView({ block: "center", behavior: "smooth" })
    }
  }, [open, activeIndex, groups])

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !next && onClose()} modal={false}>
      <Dialog.Portal>
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed inset-y-0 right-0 z-50 w-[min(26rem,92vw)] focus:outline-none"
        >
          <motion.div
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
            className="flex h-full flex-col border-l border-border/60 bg-card shadow-lift"
          >
            <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border/50 px-4">
              <div className="flex min-w-0 items-center gap-2">
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                  <BookOpen className="h-3.5 w-3.5" />
                </span>
                <Dialog.Title className="truncate text-sm font-semibold">
                  知识库依据
                </Dialog.Title>
                {total > 0 && (
                  <Badge variant="outline" className="shrink-0">
                    {total} 条
                  </Badge>
                )}
              </div>
              <Dialog.Close asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  aria-label="关闭依据"
                >
                  <X className="h-4 w-4" />
                </Button>
              </Dialog.Close>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
              {total === 0 ? (
                <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
                  <span className="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
                    <BookOpen className="h-4 w-4" />
                  </span>
                  <p className="text-xs leading-relaxed text-muted-foreground">
                    这轮回答没有引用知识库依据。
                    <br />
                    检索未命中时，Agent 应当如实说明，而不是补出条款。
                  </p>
                </div>
              ) : (
                <div className="space-y-5">
                  {groups.map((group, gi) => (
                    <EvidenceSection
                      key={group.toolCallId}
                      group={group}
                      order={gi}
                      totalGroups={groups.length}
                      activeIndex={activeIndex}
                    />
                  ))}
                </div>
              )}
            </div>
          </motion.div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

function EvidenceSection({
  group,
  order,
  totalGroups,
  activeIndex,
}: {
  group: EvidenceGroup
  order: number
  totalGroups: number
  activeIndex: number | null
}) {
  const style = group.quality ? QUALITY_STYLE[group.quality] : null
  const abstain = group.directive === "abstain"

  return (
    <section className="space-y-2.5">
      <header className="space-y-1.5">
        {totalGroups > 1 && (
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            第 {order + 1} 次检索
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {abstain ? (
            <span className="rounded-full border border-destructive/25 bg-destructive/5 px-2 py-0.5 text-[11px] text-destructive">
              未找到依据
            </span>
          ) : (
            style && (
              <span
                className={cn(
                  "rounded-full border px-2 py-0.5 text-[11px]",
                  style.className
                )}
              >
                {style.label}
              </span>
            )
          )}
        </div>
        {group.query && (
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            <span className="text-muted-foreground/70">检索式：</span>
            {group.query}
          </p>
        )}
      </header>

      {group.items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-border/70 px-3 py-3 text-[11px] text-muted-foreground">
          这次检索没有返回证据片段。
        </p>
      ) : (
        <ol className="space-y-2.5">
          {group.items.map((item) => (
            <li
              key={`${group.toolCallId}-${item.index}`}
              data-evidence-index={item.index}
              className={cn(
                "rounded-xl border p-3 transition-colors",
                item.index === activeIndex
                  ? "border-primary/45 bg-primary/5"
                  : "border-border/60 bg-background/40"
              )}
            >
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    "flex h-5 min-w-5 items-center justify-center rounded-full px-1.5 text-[11px] font-semibold tabular-nums",
                    item.index === activeIndex
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted text-muted-foreground"
                  )}
                >
                  {item.index}
                </span>
                <span className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground">
                  {item.citation}
                </span>
              </div>
              <p className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-foreground/90">
                {item.text}
              </p>
            </li>
          ))}
        </ol>
      )}

      {group.notice && (
        <p className="rounded-lg bg-muted/50 px-2.5 py-2 text-[11px] leading-relaxed text-muted-foreground">
          {group.notice}
        </p>
      )}
    </section>
  )
}
