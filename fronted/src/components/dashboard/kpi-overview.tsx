import { useEffect, useState } from "react"
import { ArrowDownRight, ArrowUpRight, PiggyBank, Wallet } from "lucide-react"
import { cn, formatCurrency, formatPercent } from "@/lib/utils"
import type { FinancialKPIs } from "@/types"

interface KpiItem {
  label: string
  value: number
  format: (v: number) => string
  icon: typeof Wallet
  tint: string
}

/**
 * 分隔线策略：
 * 移动端 2 列 → 右侧格加左边框、首行格加下边框
 * lg 桌面 4 列 → 仅第 2~4 格加左边框
 */
const cellBorders = [
  "border-b lg:border-b-0",
  "border-b border-l lg:border-b-0",
  "lg:border-l",
  "border-l",
]

export function KpiOverview({ kpis }: { kpis: FinancialKPIs }) {
  const items: KpiItem[] = [
    {
      label: "月收入",
      value: kpis.monthly_income,
      format: (v) => formatCurrency(v),
      icon: Wallet,
      tint: "bg-chart-1/10 text-chart-1",
    },
    {
      label: "月支出",
      value: kpis.monthly_expense,
      format: (v) => formatCurrency(v),
      icon: ArrowDownRight,
      tint: "bg-chart-3/12 text-chart-3",
    },
    {
      label: "月结余",
      value: kpis.monthly_surplus,
      format: (v) => formatCurrency(v),
      icon: ArrowUpRight,
      tint: "bg-chart-2/12 text-chart-2",
    },
    {
      label: "储蓄率",
      value: kpis.saving_rate,
      format: (v) => formatPercent(v),
      icon: PiggyBank,
      tint: "bg-chart-5/14 text-chart-5",
    },
  ]

  return (
    <section className="overflow-hidden rounded-xl border border-border/60 bg-card shadow-soft">
      <div className="grid grid-cols-2 lg:grid-cols-4">
        {items.map((it, i) => (
          <div
            key={it.label}
            className={cn(
              "group flex flex-col gap-3 border-border/50 p-6 transition-colors duration-300 hover:bg-accent/40",
              cellBorders[i]
            )}
          >
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium tracking-wide text-muted-foreground">
                {it.label}
              </span>
              <span
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-full transition-transform duration-300 group-hover:scale-105",
                  it.tint
                )}
              >
                <it.icon className="h-3.5 w-3.5" />
              </span>
            </div>
            <CountUp value={it.value} format={it.format} delay={i * 0.08} />
          </div>
        ))}
      </div>
    </section>
  )
}

/**
 * 数字 count-up 动画：easeOutCubic 缓动
 */
function CountUp({
  value,
  format,
  delay = 0,
}: {
  value: number
  format: (v: number) => string
  delay?: number
}) {
  const [display, setDisplay] = useState(() => format(0))

  useEffect(() => {
    let raf: number
    const start = performance.now() + delay * 1000
    const duration = 900

    const tick = (now: number) => {
      if (now < start) {
        raf = requestAnimationFrame(tick)
        return
      }
      const t = Math.min(1, (now - start) / duration)
      const eased = 1 - Math.pow(1 - t, 3)
      setDisplay(format(value * eased))
      if (t < 1) raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [value, delay, format])

  return (
    <span className="text-[26px] font-semibold leading-none tracking-tight tabular-nums">
      {display}
    </span>
  )
}
