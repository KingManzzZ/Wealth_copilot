import { useEffect, useState } from "react"
import { getDashboard } from "@/lib/api"
import type { DashboardData } from "@/types"
import { KpiOverview } from "./kpi-overview"
import { CashflowChart } from "./cashflow-chart"
import { GoalsProgress } from "./goals-progress"
import { AssetLiability } from "./asset-liability"
import { HealthScore } from "./health-score"

export function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null)

  useEffect(() => {
    getDashboard().then(setData)
  }, [])

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
