import { useCallback, useEffect, useState } from "react"
import { AlertTriangle, RefreshCw } from "lucide-react"
import { getDashboard } from "@/lib/api"
import type { DashboardData } from "@/types"
import { KpiOverview } from "./kpi-overview"
import { CashflowChart } from "./cashflow-chart"
import { GoalsProgress } from "./goals-progress"
import { AssetLiability } from "./asset-liability"
import { HealthScore } from "./health-score"
import { Button } from "@/components/ui/button"

export function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    getDashboard()
      .then(setData)
      .catch((e) => {
        setError((e as Error).message)
      })
  }, [])

  useEffect(() => {
    load()
  }, [load])

  if (error) return <DashboardError message={error} onRetry={load} />
  if (!data) return <DashboardSkeleton />

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-6xl flex-col gap-5 p-7">
        <KpiOverview kpis={data.kpis} />

        {/* 非对称 12 栏节奏，避免均匀 2x2 的表格感 */}
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
          <div className="lg:col-span-7">
            <CashflowChart data={data.cashflow} />
          </div>
          <div className="lg:col-span-5">
            <HealthScore health={data.health} />
          </div>
          <div className="lg:col-span-5">
            <GoalsProgress goals={data.goals} />
          </div>
          <div className="lg:col-span-7">
            <AssetLiability items={data.asset_liability} />
          </div>
        </div>
      </div>
    </div>
  )
}

function DashboardError({
  message,
  onRetry,
}: {
  message: string
  onRetry: () => void
}) {
  return (
    <div className="flex flex-1 items-center justify-center p-7">
      <div className="flex max-w-sm flex-col items-center gap-4 rounded-xl border border-destructive/30 bg-destructive/5 p-8 text-center">
        <span className="flex h-12 w-12 items-center justify-center rounded-full bg-destructive/10 text-destructive">
          <AlertTriangle className="h-6 w-6" />
        </span>
        <div className="space-y-1">
          <p className="text-sm font-medium">仪表盘数据加载失败</p>
          <p className="text-xs text-muted-foreground">{message}</p>
        </div>
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5" />
          重试
        </Button>
      </div>
    </div>
  )
}

function DashboardSkeleton() {
  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-6xl flex-col gap-5 p-7">
        <div className="h-[124px] animate-pulse rounded-xl border border-border/60 bg-card shadow-soft" />
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-12">
          <div className="h-88 animate-pulse rounded-xl border border-border/60 bg-card shadow-soft lg:col-span-7" />
          <div className="h-88 animate-pulse rounded-xl border border-border/60 bg-card shadow-soft lg:col-span-5" />
          <div className="h-88 animate-pulse rounded-xl border border-border/60 bg-card shadow-soft lg:col-span-5" />
          <div className="h-88 animate-pulse rounded-xl border border-border/60 bg-card shadow-soft lg:col-span-7" />
        </div>
      </div>
    </div>
  )
}
