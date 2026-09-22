import { useCallback, useEffect, useState } from "react"
import { Activity, Circle, RefreshCw } from "lucide-react"
import { ThemeToggle } from "@/components/theme-toggle"
import { Badge } from "@/components/ui/badge"
import { detectBackendStatus } from "@/lib/api"
import type { BackendStatus } from "@/types"
import { cn } from "@/lib/utils"

// 离线时每 15 秒探测一次，后端恢复后自动切回 online
const OFFLINE_POLL_INTERVAL = 15000

export function TopBar() {
  const [status, setStatus] = useState<BackendStatus>("checking")
  const [refreshing, setRefreshing] = useState(false)

  const refresh = useCallback(async () => {
    setRefreshing(true)
    try {
      // 强制重新探测：detectBackendStatus 只缓存 online，offline 会重新探测
      const next = await detectBackendStatus()
      setStatus(next)
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // 离线/检测中定时重试，后端上线后自动恢复
  useEffect(() => {
    if (status === "online") return
    const timer = setInterval(refresh, OFFLINE_POLL_INTERVAL)
    return () => clearInterval(timer)
  }, [status, refresh])

  return (
    <header className="flex h-16 shrink-0 items-center justify-between border-b border-border/50 bg-background/60 px-6 backdrop-blur-xl">
      <div className="flex items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground shadow-soft">
          <Activity className="h-4 w-4" />
        </div>
        <div className="flex items-baseline gap-2.5">
          <span className="text-[17px] font-semibold leading-none tracking-tight">
            WealthPilot
          </span>
          <span className="hidden text-xs text-muted-foreground sm:inline">
            个人财富规划 Agent
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <BackendIndicator status={status} refreshing={refreshing} onRetry={refresh} />
        <ThemeToggle />
      </div>
    </header>
  )
}

function BackendIndicator({
  status,
  refreshing,
  onRetry,
}: {
  status: BackendStatus
  refreshing: boolean
  onRetry: () => void
}) {
  const config = {
    checking: { label: "检测中", variant: "secondary" as const, pulse: true },
    online: { label: "后端在线", variant: "success" as const, pulse: false },
    offline: { label: "Mock 模式", variant: "outline" as const, pulse: false },
  }
  const c = config[status]
  return (
    <button
      type="button"
      onClick={onRetry}
      disabled={refreshing}
      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium transition-colors hover:bg-accent/50 disabled:opacity-60"
      title="点击重新检测后端状态"
      aria-label="重新检测后端状态"
    >
      <Badge variant={c.variant}>
        <Circle className={cn("h-1.5 w-1.5 fill-current", c.pulse && "animate-pulse")} />
        {c.label}
      </Badge>
      <RefreshCw
        className={cn("h-3 w-3 text-muted-foreground", refreshing && "animate-spin")}
      />
    </button>
  )
}
