import { motion } from "framer-motion"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { cn, formatCurrency } from "@/lib/utils"
import type { AssetLiabilityItem } from "@/types"

export function AssetLiability({ items }: { items: AssetLiabilityItem[] }) {
  const assets = items.filter((i) => i.kind === "asset")
  const liabilities = items.filter((i) => i.kind === "liability")
  const totalAsset = assets.reduce((s, i) => s + i.value, 0)
  const totalLiab = liabilities.reduce((s, i) => s + i.value, 0)
  const netWorth = totalAsset - totalLiab
  const max = Math.max(totalAsset, totalLiab, 1)

  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle>资产负债概览</CardTitle>
        <CardDescription>净资产 = 总资产 − 总负债</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-6 grid grid-cols-3 gap-3">
          <Stat label="总资产" value={totalAsset} className="text-chart-2" />
          <Stat label="总负债" value={totalLiab} className="text-chart-4" />
          <Stat label="净资产" value={netWorth} className="text-chart-1" />
        </div>
        <div className="space-y-4">
          {items.map((i) => (
            <Row key={i.name} item={i} max={max} />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

function Stat({
  label,
  value,
  className,
}: {
  label: string
  value: number
  className?: string
}) {
  return (
    <div className="space-y-1">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p
        className={cn(
          "text-lg font-semibold leading-none tracking-tight tabular-nums",
          className
        )}
      >
        {formatCurrency(value)}
      </p>
    </div>
  )
}

function Row({ item, max }: { item: AssetLiabilityItem; max: number }) {
  const isAsset = item.kind === "asset"
  const pct = max > 0 ? (item.value / max) * 100 : 0
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-xs">
        <span className="text-foreground/80">{item.name}</span>
        <span className="tabular-nums text-muted-foreground">
          {formatCurrency(item.value)}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
          className={cn(
            "h-full rounded-full",
            isAsset ? "bg-chart-2" : "bg-chart-4"
          )}
        />
      </div>
    </div>
  )
}
