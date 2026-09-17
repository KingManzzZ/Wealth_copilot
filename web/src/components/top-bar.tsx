import { useEffect, useState } from "react"
import { Activity, Circle } from "lucide-react"
import { ThemeToggle } from "@/components/theme-toggle"
import { Badge } from "@/components/ui/badge"
import { detectBackendStatus } from "@/lib/api"
import type { BackendStatus } from "@/types"
import { cn } from "@/lib/utils"

export function TopBar() {
  const [status, setStatus] = useState<BackendStatus>("checking")

  useEffect(() => {
    detectBackendStatus().then(setStatus)
  }, [])

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
        <BackendIndicator status={status} />
        <ThemeToggle />
      </div>
    </header>
  )
}

function BackendIndicator({ status }: { status: BackendStatus }) {
  const config = {
    checking: { label: "检测中", variant: "secondary" as const, pulse: true },
    online: { label: "后端在线", variant: "success" as const, pulse: false },
    offline: { label: "Mock 模式", variant: "outline" as const, pulse: false },
  }
  const c = config[status]
  return (
    <Badge variant={c.variant}>
      <Circle className={cn("h-1.5 w-1.5 fill-current", c.pulse && "animate-pulse")} />
      {c.label}
    </Badge>
  )
}
