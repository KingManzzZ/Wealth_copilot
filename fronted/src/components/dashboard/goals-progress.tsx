import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import { formatCurrency } from "@/lib/utils"
import type { FinancialGoal } from "@/types"

const priorityConfig = {
  high: { label: "高", variant: "warning" as const },
  medium: { label: "中", variant: "default" as const },
  low: { label: "低", variant: "secondary" as const },
}

export function GoalsProgress({ goals }: { goals: FinancialGoal[] }) {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>财务目标进度</CardTitle>
        <CardDescription>按优先级排列，进度条显示当前与目标占比</CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        {goals.map((g) => {
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
      </CardContent>
    </Card>
  )
}
