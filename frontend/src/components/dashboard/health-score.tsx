import { motion } from "framer-motion"
import { ChevronRight } from "lucide-react"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Progress } from "@/components/ui/progress"
import type { FinancialHealth } from "@/types"

const gradeColor: Record<FinancialHealth["grade"], string> = {
  A: "text-chart-2",
  B: "text-chart-1",
  C: "text-chart-3",
  D: "text-destructive",
}

export function HealthScore({
  health,
  onOpenDetail,
}: {
  health: FinancialHealth
  /** 打开详情抽屉；不传则卡片只读 */
  onOpenDetail?: () => void
}) {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>财务健康度</CardTitle>
        <CardDescription>基于储蓄、应急、负债、目标、配置五维评估</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-6 flex items-center gap-6">
          <ScoreRing score={health.overall} />
          <div>
            <p
              className={`text-xs font-medium tracking-wide ${gradeColor[health.grade]}`}
            >
              Grade {health.grade}
            </p>
            <p className="mt-1 text-[34px] font-semibold leading-none tracking-tight tabular-nums">
              {health.overall}
              <span className="ml-0.5 text-base font-normal text-muted-foreground">
                /100
              </span>
            </p>
            <p className="mt-2 text-xs text-muted-foreground">综合评分</p>
          </div>
        </div>
        <div className="space-y-4">
          {health.dimensions.map((d) => (
            <div key={d.name} className="space-y-1.5">
              <div className="flex items-baseline justify-between text-xs">
                <span className="text-foreground/80">{d.name}</span>
                <span className="font-semibold tabular-nums">
                  {d.score}
                </span>
              </div>
              <Progress value={d.score} indicatorClassName="bg-chart-5" />
              {d.advice && (
                <p className="pt-0.5 text-[11px] leading-relaxed text-muted-foreground">
                  {d.advice}
                </p>
              )}
            </div>
          ))}
        </div>

        {onOpenDetail && (
          <Button
            variant="ghost"
            size="sm"
            className="mt-4 w-full justify-between px-2 text-[11px] text-muted-foreground"
            onClick={onOpenDetail}
          >
            查看各项原始数据
            <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

function ScoreRing({ score }: { score: number }) {
  const radius = 38
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (score / 100) * circumference

  return (
    <div className="relative h-24 w-24 shrink-0">
      <svg width="96" height="96" viewBox="0 0 96 96" className="-rotate-90">
        <circle
          cx="48"
          cy="48"
          r={radius}
          fill="none"
          stroke="var(--color-muted)"
          strokeWidth="7"
        />
        <motion.circle
          cx="48"
          cy="48"
          r={radius}
          fill="none"
          stroke="var(--color-chart-5)"
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={circumference}
          initial={{ strokeDashoffset: circumference }}
          animate={{ strokeDashoffset: offset }}
          transition={{ duration: 1.3, ease: [0.16, 1, 0.3, 1] }}
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className="text-2xl font-semibold tracking-tight tabular-nums">
          {score}
        </span>
      </div>
    </div>
  )
}
