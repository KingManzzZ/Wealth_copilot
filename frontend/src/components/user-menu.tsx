import { useEffect, useRef, useState, type ReactNode } from "react"
import { AnimatePresence, motion } from "framer-motion"
import { AlertCircle, BadgeCheck, ChevronDown, HelpCircle } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { getProfile } from "@/lib/api"
import {
  LIQUIDITY_LABELS,
  RISK_LABELS,
  getLiquidityHint,
  getPendingConfirmations,
  getProfileCompletion,
  getRiskHint,
} from "@/lib/profile"
import { cn, formatPercent } from "@/lib/utils"
import type { UserProfile } from "@/types"

/**
 * 顶栏右上角头像：点击弹出个人资料卡
 * - 基本信息 / 投资画像 / 家庭情况 三组字段
 * - 底部展示资料完整度与待确认项
 * 后端不在线时由 getProfile 自动降级到 mock 数据
 */
export function UserMenu() {
  const [open, setOpen] = useState(false)
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getProfile()
      .then(setProfile)
      .catch(() => {})
  }, [])

  // 展开时：点击外部或按 Esc 关闭
  useEffect(() => {
    if (!open) return
    const onPointerDown = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    document.addEventListener("pointerdown", onPointerDown)
    document.addEventListener("keydown", onKeyDown)
    return () => {
      document.removeEventListener("pointerdown", onPointerDown)
      document.removeEventListener("keydown", onKeyDown)
    }
  }, [open])

  const initial = profile?.name?.trim().charAt(0) || "W"

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label="查看个人资料"
        aria-expanded={open}
        className={cn(
          "flex items-center gap-1 rounded-full p-0.5 transition-colors hover:bg-accent/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50",
          open && "bg-accent/60"
        )}
      >
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-sm font-medium text-primary-foreground shadow-soft">
          {initial}
        </span>
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 text-muted-foreground transition-transform duration-200",
            open && "rotate-180"
          )}
        />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.16, ease: "easeOut" }}
            role="dialog"
            aria-label="个人资料"
            className="absolute right-0 top-[calc(100%+0.5rem)] z-50 w-[21rem] overflow-hidden rounded-xl border border-border/70 bg-popover/95 shadow-lift backdrop-blur-xl"
          >
            {profile ? (
              <ProfileCard profile={profile} />
            ) : (
              <div className="px-4 py-6 text-center text-xs text-muted-foreground">
                资料加载中…
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function ProfileCard({ profile }: { profile: UserProfile }) {
  const { filled, total, ratio } = getProfileCompletion(profile)
  const pending = getPendingConfirmations(profile)

  const summary = [
    profile.city,
    profile.occupation,
    profile.age ? `${profile.age} 岁` : "",
  ]
    .filter(Boolean)
    .join(" · ")

  return (
    <div>
      {/* 头部：头像 + 姓名 + 概要 */}
      <div className="flex items-center gap-3 border-b border-border/60 px-4 py-3.5">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-primary text-lg font-medium text-primary-foreground shadow-soft">
          {profile.name?.trim().charAt(0) || "W"}
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold">
              {profile.name?.trim() || "未设置姓名"}
            </span>
            <Badge variant="secondary">{profile.user_id}</Badge>
          </div>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {summary || "资料待完善"}
          </p>
        </div>
      </div>

      {/* 基本信息 */}
      <Section title="基本信息">
        <InfoRow label="年龄" value={profile.age ? `${profile.age} 岁` : undefined} />
        <InfoRow label="城市" value={profile.city} />
        <InfoRow label="职业" value={profile.occupation} />
        <InfoRow label="行业" value={profile.industry} />
        <InfoRow label="教育程度" value={profile.education} />
      </Section>

      {/* 投资画像 */}
      <Section title="投资画像">
        <InfoRow
          label="风险等级"
          value={profile.risk_tolerance ? RISK_LABELS[profile.risk_tolerance] : undefined}
          hint={getRiskHint(profile)}
        />
        <InfoRow
          label="投资年限"
          value={profile.investing_years ? `${profile.investing_years} 年` : undefined}
        />
        <InfoRow
          label="活期现金需求"
          value={profile.liquidity_needs ? LIQUIDITY_LABELS[profile.liquidity_needs] : undefined}
          hint={getLiquidityHint(profile)}
        />
      </Section>

      {/* 家庭情况 */}
      <Section title="家庭情况">
        <InfoRow label="婚姻" value={profile.marital_status} />
        <InfoRow label="子女" value={profile.children} />
        <InfoRow label="赡养父母" value={profile.supporting_parents} />
      </Section>

      {/* 底部：完善度 + 待确认项 */}
      <div className="bg-muted/30 px-4 py-3">
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">资料完整度</span>
          <span className="font-medium tabular-nums">
            {formatPercent(ratio, 0)}
            <span className="ml-1 font-normal text-muted-foreground">
              （{filled}/{total}）
            </span>
          </span>
        </div>
        <Progress value={ratio * 100} className="mt-1.5 h-1.5" />

        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {pending.length === 0 ? (
            <Badge variant="success">
              <BadgeCheck className="h-3 w-3" />
              资料已全部确认
            </Badge>
          ) : (
            pending.map((item) => (
              <Badge key={item} variant="warning">
                <AlertCircle className="h-3 w-3" />
                {item}
              </Badge>
            ))
          )}
        </div>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="border-b border-border/60 px-4 py-2.5">
      <h4 className="mb-0.5 text-[11px] font-medium text-muted-foreground/90">{title}</h4>
      {children}
    </div>
  )
}

function InfoRow({ label, value, hint }: { label: string; value?: string; hint?: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-[3px]">
      <span className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
        {label}
        {hint && <HintIcon content={hint} />}
      </span>
      {value ? (
        <span className="truncate text-xs font-medium">{value}</span>
      ) : (
        <span className="text-xs text-muted-foreground/60">待确认</span>
      )}
    </div>
  )
}

/** 「?」悬浮提示：展示字段的量化说明 */
function HintIcon({ content }: { content: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label="查看量化说明"
          className="inline-flex items-center justify-center rounded-full text-muted-foreground/60 transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        >
          <HelpCircle className="h-3 w-3" />
        </button>
      </TooltipTrigger>
      <TooltipContent side="top">{content}</TooltipContent>
    </Tooltip>
  )
}
