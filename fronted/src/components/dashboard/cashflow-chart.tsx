import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { formatCurrency } from "@/lib/utils"
import type { CashflowPoint } from "@/types"

interface TooltipProps {
  active?: boolean
  payload?: { value: number; name: string; color: string }[]
  label?: string
}

function ChartTooltip({ active, payload, label }: TooltipProps) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-xl border border-border/60 bg-popover px-3.5 py-2.5 text-xs shadow-lift">
      <p className="mb-2 font-medium">{label}</p>
      <div className="space-y-1">
        {payload.map((p) => (
          <div key={p.name} className="flex items-center gap-2">
            <span
              className="h-2 w-2 rounded-full"
              style={{ backgroundColor: p.color }}
            />
            <span className="text-muted-foreground">{p.name}</span>
            <span className="ml-auto pl-3 font-medium tabular-nums">
              {formatCurrency(p.value)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function CashflowChart({ data }: { data: CashflowPoint[] }) {
  return (
    <Card className="flex h-full flex-col">
      <CardHeader>
        <CardTitle>月度现金流</CardTitle>
        <CardDescription>近 6 个月收入、支出与结余对比</CardDescription>
      </CardHeader>
      <CardContent className="min-h-0 flex-1">
        <div className="h-full min-h-[240px] w-full">
          {/*
            barSize 为具体数值时，recharts 会把整组居中，并忽略 barCategoryGap。
            组间距由 (每月槽位 - 组宽) 决定，因此这里靠收窄柱身换取分组可辨识度：
            18px × 3 + 2px × 2 = 58px 组宽，槽位约 73px，组间距约 15px。
          */}
          <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} barGap={2}>
            <CartesianGrid
              strokeDasharray="4 6"
              vertical={false}
              stroke="var(--color-border)"
              strokeOpacity={0.7}
            />
            <XAxis
              dataKey="month"
              tickLine={false}
              axisLine={false}
              dy={6}
              tick={{ fontSize: 11, fill: "var(--color-muted-foreground)" }}
            />
            <YAxis
              tickLine={false}
              axisLine={false}
              dx={-6}
              tick={{ fontSize: 11, fill: "var(--color-muted-foreground)" }}
              tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`}
            />
            <Tooltip
              content={<ChartTooltip />}
              cursor={{ fill: "var(--color-muted)", opacity: 0.35, radius: 8 }}
            />
            <Legend
              wrapperStyle={{ fontSize: 11, paddingTop: 12 }}
              iconType="circle"
              iconSize={7}
            />
            <Bar
              dataKey="income"
              name="收入"
              fill="var(--color-chart-1)"
              radius={[6, 6, 0, 0]}
              barSize={18}
            />
            <Bar
              dataKey="expense"
              name="支出"
              fill="var(--color-chart-3)"
              radius={[6, 6, 0, 0]}
              barSize={18}
            />
            <Bar
              dataKey="surplus"
              name="结余"
              fill="var(--color-chart-2)"
              radius={[6, 6, 0, 0]}
              barSize={18}
            />
          </BarChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  )
}
