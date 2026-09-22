import { useEffect, useState } from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import { formatCurrency } from "@/lib/utils"
import type { FinancialGoal } from "@/types"

// 每页固定展示的目标数量：少于则背景槽位补齐，多于则分页
const PAGE_SIZE = 3

const priorityConfig = {
  high: { label: "高", variant: "warning" as const },
  medium: { label: "中", variant: "default" as const },
  low: { label: "低", variant: "secondary" as const },
}

export function GoalsProgress({ goals }: { goals: FinancialGoal[] }) {
  const totalPages = Math.max(1, Math.ceil(goals.length / PAGE_SIZE))
  const [page, setPage] = useState(0)

  // goals 数量变化（如换数据源）时收敛越界页码
  useEffect(() => {
    if (page >= totalPages) setPage(0)
  }, [page, totalPages])

  const visible = goals.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE)
  const placeholders = PAGE_SIZE - visible.length

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>财务目标进度</CardTitle>
        <CardDescription>按优先级排列，进度条显示当前与目标占比</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        {visible.map((g) => {
          const pct = Math.min(100, (g.current / g.target) * 100)
          const daysLeft = Math.ceil(
            (new Date(g.deadline).getTime() - Date.now()) / 86_400_000
          )
          const cfg = priorityConfig[g.priority]
          return (
            <div key={g.id} className="space-y-2">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{g.name}</span>
                  <Badge variant={cfg.variant}>{cfg.label}优先</Badge>
                </div>
                <span className="text-xs tabular-nums text-muted-foreground">
                  {formatCurrency(g.current, false)} /{" "}
                  {formatCurrency(g.target, false)}
                </span>
              </div>
              <Progress value={pct} />
              <div className="flex justify-between text-xs text-muted-foreground">
                <span className="font-semibold tabular-nums">
                  {pct.toFixed(0)}%
                </span>
                <span>{daysLeft > 0 ? `剩余 ${daysLeft} 天` : "已逾期"}</span>
              </div>
            </div>
          )
        })}

        {/* 不足 3 条时用背景槽位补齐，保持卡片高度稳定 */}
        {Array.from({ length: placeholders }).map((_, i) => (
          <GoalPlaceholder key={`placeholder-${i}`} />
        ))}

        {/* 超过 3 条时底部分页：左/右箭头 + 页码 */}
        {totalPages > 1 && (
          <div className="flex items-center justify-end gap-2 pt-1">
            <span className="mr-auto text-xs tabular-nums text-muted-foreground">
              {page + 1} / {totalPages}
            </span>
            <Button
              variant="outline"
              size="icon"
              className="h-7 w-7"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              aria-label="上一页"
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="h-7 w-7"
              disabled={page === totalPages - 1}
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              aria-label="下一页"
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * 空槽位：结构与真实目标块一致（标题行 + 进度条 + 信息行），
 * 全部用 muted 背景色填充，保证不同数据量下卡片高度不跳动
 */
function GoalPlaceholder() {
  return (
    <div className="space-y-2 opacity-60" aria-hidden>
      <div className="flex items-center justify-between gap-2">
        <div className="h-5 w-28 rounded bg-muted" />
        <div className="h-4 w-20 rounded bg-muted" />
      </div>
      <div className="h-2.5 w-full rounded-full bg-muted" />
      <div className="flex items-center justify-between">
        <div className="h-3.5 w-8 rounded bg-muted" />
        <div className="h-3.5 w-16 rounded bg-muted" />
      </div>
    </div>
  )
}
