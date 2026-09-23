import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { AnimatePresence, motion } from "framer-motion"
import { AlertCircle, BadgeCheck, ChevronDown, HelpCircle } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Progress } from "@/components/ui/progress"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { getBasicInfo, upsertBasicInfo } from "@/lib/api"
import {
  LIQUIDITY_LABELS,
  PREFERENCE_LABELS,
  RISK_LABELS,
  getInvestmentPreferenceHint,
  getLiquidityHint,
  getPendingConfirmations,
  getProfileCompletion,
  getRiskHint,
} from "@/lib/profile"
import { cn, formatPercent } from "@/lib/utils"
import type { BasicInfo } from "@/types"

/** 数值型字段：保存时需转 number */
const NUMERIC_FIELDS = new Set<string>(["age", "investing_years"])

/**
 * 顶栏右上角头像：点击弹出个人资料卡
 * - 基本信息 / 投资画像 / 家庭情况 三组字段
 * - 用户可点击字段值直接行内编辑（算法字段除外）
 * - 风险承受能力 / 投资偏好 / 活期现金需求 为算法估算字段，只读展示
 * - 底部展示资料完整度与待确认项
 */
export function UserMenu() {
  const [open, setOpen] = useState(false)
  const [basicInfo, setBasicInfo] = useState<BasicInfo | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    getBasicInfo()
      .then(setBasicInfo)
      .catch(() => {})
  }, [])

  // 行内编辑保存：调 upsertBasicInfo → 成功刷新 state，失败保持原值
  const handleSave = useCallback(async (field: keyof BasicInfo, value: string) => {
    const payload: Partial<BasicInfo> = {
      [field]: NUMERIC_FIELDS.has(field as string) ? Number(value) || 0 : value,
    } as Partial<BasicInfo>
    try {
      const updated = await upsertBasicInfo(payload)
      setBasicInfo(updated)
    } catch (e) {
      console.error("保存失败:", e)
    }
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

  const initial = basicInfo?.name?.trim().charAt(0) || "W"

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
            {basicInfo ? (
              <ProfileCard info={basicInfo} onSave={handleSave} />
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

function ProfileCard({
  info,
  onSave,
}: {
  info: BasicInfo
  onSave: (field: keyof BasicInfo, value: string) => void
}) {
  const { filled, total, ratio } = getProfileCompletion(info)
  const pending = getPendingConfirmations(info)

  const summary = [
    info.city,
    info.occupation,
    info.age ? `${info.age} 岁` : "",
  ]
    .filter(Boolean)
    .join(" · ")

  return (
    <div>
      {/* 头部：头像 + 姓名 + 概要 */}
      <div className="flex items-center gap-3 border-b border-border/60 px-4 py-3.5">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-primary text-lg font-medium text-primary-foreground shadow-soft">
          {info.name?.trim().charAt(0) || "W"}
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold">
              {info.name?.trim() || "未设置姓名"}
            </span>
            <Badge variant="secondary">{info.user_id}</Badge>
          </div>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {summary || "资料待完善"}
          </p>
        </div>
      </div>

      {/* 基本信息 */}
      <Section title="基本信息">
        <InfoRow label="姓名" display={info.name} rawValue={info.name} editable onSave={(v) => onSave("name", v)} />
        <InfoRow label="年龄" display={info.age ? `${info.age} 岁` : undefined} rawValue={info.age ? String(info.age) : ""} editable inputType="number" onSave={(v) => onSave("age", v)} />
        <InfoRow label="城市" display={info.city} rawValue={info.city} editable onSave={(v) => onSave("city", v)} />
        <InfoRow label="职业" display={info.occupation} rawValue={info.occupation} editable onSave={(v) => onSave("occupation", v)} />
        <InfoRow label="行业" display={info.industry} rawValue={info.industry} editable onSave={(v) => onSave("industry", v)} />
        <InfoRow label="教育程度" display={info.education} rawValue={info.education} editable onSave={(v) => onSave("education", v)} />
      </Section>

      {/* 投资画像 */}
      <Section title="投资画像">
        <InfoRow
          label="风险承受能力"
          display={info.risk_tolerance ? RISK_LABELS[info.risk_tolerance] : undefined}
          hint={getRiskHint(info)}
        />
        <InfoRow
          label="投资偏好"
          display={info.investment_preference ? PREFERENCE_LABELS[info.investment_preference] : undefined}
          hint={getInvestmentPreferenceHint(info)}
        />
        <InfoRow
          label="投资年限"
          display={info.investing_years ? `${info.investing_years} 年` : undefined}
          rawValue={info.investing_years ? String(info.investing_years) : ""}
          editable
          inputType="number"
          onSave={(v) => onSave("investing_years", v)}
        />
        <InfoRow
          label="活期现金需求"
          display={info.liquidity_needs ? LIQUIDITY_LABELS[info.liquidity_needs] : undefined}
          hint={getLiquidityHint(info)}
        />
      </Section>

      {/* 家庭情况 */}
      <Section title="家庭情况">
        <InfoRow label="婚姻" display={info.marital_status} rawValue={info.marital_status} editable onSave={(v) => onSave("marital_status", v)} />
        <InfoRow label="子女" display={info.children} rawValue={info.children} editable onSave={(v) => onSave("children", v)} />
        <InfoRow label="赡养父母" display={info.supporting_parents} rawValue={info.supporting_parents} editable onSave={(v) => onSave("supporting_parents", v)} />
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

/**
 * 信息行：支持只读和可编辑两种模式。
 * - editable + onSave：点击值区域进入行内编辑，Enter/blur 保存，Escape 取消
 * - 非 editable：纯展示（用于算法字段）
 */
function InfoRow({
  label,
  display,
  rawValue,
  hint,
  editable,
  inputType = "text",
  onSave,
}: {
  label: string
  display?: string
  rawValue?: string
  hint?: string
  editable?: boolean
  inputType?: "text" | "number"
  onSave?: (value: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(rawValue ?? "")

  // 非编辑态时同步外部值变更
  useEffect(() => {
    if (!editing) setDraft(rawValue ?? "")
  }, [rawValue, editing])

  const startEdit = () => {
    if (!editable) return
    setDraft(rawValue ?? "")
    setEditing(true)
  }

  const cancel = () => {
    setDraft(rawValue ?? "")
    setEditing(false)
  }

  const save = () => {
    setEditing(false)
    const trimmed = draft.trim()
    if (trimmed !== (rawValue ?? "").trim()) {
      onSave?.(trimmed)
    }
  }

  return (
    <div className="flex items-center justify-between gap-3 py-[3px]">
      <span className="inline-flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
        {label}
        {hint && <HintIcon content={hint} />}
      </span>
      {editing ? (
        <input
          autoFocus
          type={inputType}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save()
            if (e.key === "Escape") cancel()
          }}
          onBlur={save}
          className="w-32 rounded border border-border bg-background px-1.5 py-0.5 text-right text-xs outline-none focus:border-primary"
        />
      ) : display ? (
        <span
          className={cn(
            "truncate rounded px-1 text-xs font-medium transition-colors",
            editable && "-mx-1 cursor-pointer hover:bg-accent/40"
          )}
          onClick={editable ? startEdit : undefined}
        >
          {display}
        </span>
      ) : (
        <span
          className={cn(
            "rounded px-1 text-xs text-muted-foreground/60 transition-colors",
            editable && "-mx-1 cursor-pointer hover:bg-accent/40"
          )}
          onClick={editable ? startEdit : undefined}
        >
          {editable ? "点击编辑" : "待评估"}
        </span>
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
