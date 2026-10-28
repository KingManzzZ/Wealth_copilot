import { useCallback, useEffect, useState } from "react"
import * as Dialog from "@radix-ui/react-dialog"
import { AnimatePresence, motion } from "framer-motion"
import { Activity, RefreshCw, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { getHealthDetail } from "@/lib/api"
import { cn } from "@/lib/utils"
import type { HealthDetail, HealthMetric } from "@/types"

const QUALITY_LABEL: Record<string, string> = {
  full: "数据完整",
  partial: "部分数据缺失",
  insufficient: "数据不足",
}

const METRIC_TONE: Record<HealthMetric["status"], string> = {
  good: "text-chart-2",
  warn: "text-chart-3",
  bad: "text-destructive",
}

const METRIC_DOT: Record<HealthMetric["status"], string> = {
  good: "bg-chart-2",
  warn: "bg-chart-3",
  bad: "bg-destructive",
}

function scoreTone(score: number): string {
  if (score >= 80) return "text-chart-2"
  if (score >= 50) return "text-chart-3"
  return "text-destructive"
}

/**
 * 财务健康度详情抽屉。
 *
 * 卡片只给分数；抽屉回答"这个分是怎么来的"：每个维度的原始值、目标线、
 * 差距和一句可执行的建议。数据来自 `GET /api/dashboard/health-detail`，
 * 不进 dashboard 主请求，避免把卡片接口撑大。
 */
export function HealthDetailDrawer({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const [detail, setDetail] = useState<HealthDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getHealthDetail()
      if (!data) throw new Error("后端不可用，暂时看不到明细")
      setDetail(data)
    } catch (e) {
      setError((e as Error).message || "健康度明细加载失败")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild forceMount>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="fixed inset-0 z-40 bg-black/30 backdrop-blur-[1px]"
              />
            </Dialog.Overlay>
            <Dialog.Content asChild forceMount>
              <motion.div
                initial={{ x: "100%" }}
                animate={{ x: 0 }}
                exit={{ x: "100%" }}
                transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
                className="fixed right-0 top-0 z-50 flex h-full w-[min(30rem,94vw)] flex-col border-l border-border/60 bg-card shadow-lift focus:outline-none"
              >
                <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border/50 px-4">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                      <Activity className="h-3.5 w-3.5" />
                    </span>
                    <div className="min-w-0">
                      <Dialog.Title className="truncate text-sm font-semibold">
                        财务健康度详情
                      </Dialog.Title>
                      <Dialog.Description className="truncate text-[11px] text-muted-foreground">
                        每个维度的原始值、目标线与差距
                      </Dialog.Description>
                    </div>
                  </div>
                  <Dialog.Close asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label="关闭健康度详情"
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </Dialog.Close>
                </div>

                <div className="flex-1 overflow-y-auto px-5 py-4">
                  {loading && <LoadingState />}

                  {!loading && error && (
                    <div className="flex flex-col items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-6 text-center">
                      <p className="text-sm font-medium">明细加载失败</p>
                      <p className="text-xs text-muted-foreground">{error}</p>
                      <Button variant="outline" size="sm" onClick={load}>
                        <RefreshCw className="h-3.5 w-3.5" />
                        重试
                      </Button>
                    </div>
                  )}

                  {!loading && !error && detail && (
                    <div className="space-y-4">
                      <Summary detail={detail} />

                      {detail.dimensions.length === 0 && (
                        <div className="rounded-xl border border-border/60 bg-card p-5 text-center text-xs text-muted-foreground">
                          暂无足够数据。先记几笔流水、补上资产/负债/目标，健康度才有意义。
                        </div>
                      )}

                      {detail.dimensions.map((dimension) => (
                        <section
                          key={dimension.key || dimension.name}
                          className="rounded-xl border border-border/60 bg-card p-4 shadow-soft"
                        >
                          <div className="flex items-baseline justify-between gap-2">
                            <h3 className="text-sm font-semibold">
                              {dimension.name}
                            </h3>
                            <span
                              className={cn(
                                "text-lg font-semibold tabular-nums",
                                scoreTone(dimension.score)
                              )}
                            >
                              {dimension.score}
                              <span className="ml-0.5 text-[11px] font-normal text-muted-foreground">
                                /100
                              </span>
                            </span>
                          </div>

                          {dimension.advice && (
                            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                              {dimension.advice}
                            </p>
                          )}

                          <div className="mt-3 space-y-2">
                            {dimension.metrics.map((metric) => (
                              <MetricRow
                                key={`${metric.label}-${metric.value}`}
                                metric={metric}
                              />
                            ))}
                          </div>
                        </section>
                      ))}
                    </div>
                  )}
                </div>
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  )
}

function Summary({ detail }: { detail: HealthDetail }) {
  const quality = detail.data_quality
  return (
    <div className="rounded-xl border border-border/60 bg-card p-4 shadow-soft">
      <div className="flex items-end justify-between gap-3">
        <div>
          <p className="text-[11px] font-medium tracking-wide text-muted-foreground">
            Grade {detail.grade}
          </p>
          <p className="mt-1 text-3xl font-semibold leading-none tracking-tight tabular-nums">
            {detail.overall}
            <span className="ml-1 text-sm font-normal text-muted-foreground">
              /100
            </span>
          </p>
        </div>
        {quality && (
          <span className="rounded-full border border-border/60 px-2 py-0.5 text-[10px] text-muted-foreground">
            {QUALITY_LABEL[quality] ?? quality}
          </span>
        )}
      </div>
      {detail.generated_at && (
        <p className="mt-2 text-[10px] text-muted-foreground/70">
          计算时间：{detail.generated_at.replace("T", " ")}
        </p>
      )}
    </div>
  )
}

function MetricRow({ metric }: { metric: HealthMetric }) {
  return (
    <div className="rounded-lg bg-muted/40 px-2.5 py-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 flex-1 text-[11px] text-foreground/80">
          {metric.label}
        </span>
        <span
          className={cn(
            "flex shrink-0 items-center gap-1 text-xs font-semibold tabular-nums",
            METRIC_TONE[metric.status]
          )}
        >
          <span
            className={cn("h-1.5 w-1.5 rounded-full", METRIC_DOT[metric.status])}
          />
          {metric.value}
        </span>
      </div>
      <div className="mt-0.5 flex items-baseline justify-between gap-2">
        <span className="text-[10px] text-muted-foreground">
          目标 {metric.target}
        </span>
        {metric.note && (
          <span className="text-right text-[10px] text-muted-foreground/70">
            {metric.note}
          </span>
        )}
      </div>
    </div>
  )
}

function LoadingState() {
  return (
    <div className="space-y-3">
      <div className="h-20 animate-pulse rounded-xl border border-border/60 bg-card" />
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-28 animate-pulse rounded-xl border border-border/60 bg-card"
        />
      ))}
    </div>
  )
}
