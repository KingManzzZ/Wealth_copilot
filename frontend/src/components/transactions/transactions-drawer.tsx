import { useCallback, useEffect, useMemo, useState } from "react"
import * as Dialog from "@radix-ui/react-dialog"
import { AnimatePresence, motion } from "framer-motion"
import {
  ArrowDownLeft,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Receipt,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { useChatTurnCompleted } from "@/hooks/useChatTurnCompleted"
import {
  deleteTransaction,
  detectBackendStatus,
  getTransactions,
} from "@/lib/api"
import { cn, formatCurrency } from "@/lib/utils"
import type { Transaction } from "@/types"

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"]

/**
 * 交易流水抽屉（CX-3）。
 *
 * Agent 的 record_income / record_expense 只写 transactions 表，而仪表盘的 KPI 派生自
 * monthly_cashflow —— 也就是说这些写入以前**没有任何界面能看到**。这个抽屉是它们唯一的
 * 可见出口，所以它必须：
 * 1. 打开即拉当前月（GET /api/transactions?month=YYYY-MM）；
 * 2. 一轮对话结束后自动重拉（chat-turn-completed），账目"记完就出现"；
 * 3. 空月份给出写入引导，而不是一片空白。
 *
 * 三个刻意的选择：
 * - **非模态**（modal={false}、无遮罩、点外部不关）：它是账本侧栏，不是对话框。
 *   若做成模态，打开时聊天输入被挡住，"一边聊一边看账目滚动"的闭环就断了。
 * - **开在聊天栏的另一侧**：聊天栏宽 400、抽屉宽 448，同侧会整个盖住对话。
 * - 用 Radix Dialog 而不是手搓 fixed 面板：aria 接线、Esc 关闭、Portal 都免费，
 *   且 Portal 挂到 body，不受顶栏 backdrop-filter 包含块影响被裁切。
 */
export function TransactionsDrawer({
  open,
  onClose,
  side,
}: {
  open: boolean
  onClose: () => void
  /** 抽屉停靠边：由调用方选「聊天栏的另一侧」 */
  side: "left" | "right"
}) {
  const [month, setMonth] = useState(currentMonth)
  const [items, setItems] = useState<Transaction[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isMock, setIsMock] = useState(false)
  // 待确认删除的行；带上月份，切月后自动失效（无需在 effect 里重置）
  const [pendingDelete, setPendingDelete] = useState<{
    month: string
    id: number
  } | null>(null)

  const nowMonth = currentMonth()
  const pendingId = pendingDelete?.month === month ? pendingDelete.id : null

  /** keep=true 时保留旧列表，避免事件触发的重拉把抽屉闪成骨架 */
  const load = useCallback(async (target: string, keep = false) => {
    if (!keep) {
      setItems(null)
      setError(null)
    }
    try {
      const [list, status] = await Promise.all([
        getTransactions(target),
        detectBackendStatus(),
      ])
      setItems(list)
      setIsMock(status === "offline")
      setError(null)
    } catch (e) {
      setError((e as Error).message || "流水加载失败")
      if (!keep) setItems([])
    }
  }, [])

  // 打开 / 切月：重新拉取
  useEffect(() => {
    if (!open) return
    void load(month)
  }, [open, month, load])

  const handleClose = useCallback(() => {
    setPendingDelete(null)
    onClose()
  }, [onClose])

  // Agent 记了账：抽屉开着就整月重拉（关闭时不浪费请求）
  useChatTurnCompleted(() => {
    if (!open) return
    void load(month, true)
  })

  const handleDelete = async (id: number) => {
    setPendingDelete(null)
    try {
      await deleteTransaction(id)
    } catch {
      // 删除失败不弹错：下面复用重拉把视图拉回服务端真相
    }
    await load(month, true)
  }

  const { income, expense } = useMemo(() => {
    let inc = 0
    let exp = 0
    for (const t of items ?? []) {
      if (t.transaction_type === "income") inc += t.amount
      else exp += t.amount
    }
    return { income: inc, expense: exp }
  }, [items])

  const groups = useMemo(() => groupByDate(items ?? []), [items])

  return (
    <Dialog.Root
      open={open}
      modal={false}
      onOpenChange={(next) => {
        if (!next) handleClose()
      }}
    >
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Content
              asChild
              forceMount
              // 点对话区/仪表盘不关抽屉：侧栏要能边聊边看
              onInteractOutside={(event) => event.preventDefault()}
            >
              <motion.div
                initial={{ x: side === "left" ? "-100%" : "100%" }}
                animate={{ x: 0 }}
                exit={{ x: side === "left" ? "-100%" : "100%" }}
                transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
                className={cn(
                  "fixed top-0 z-50 flex h-full w-[min(26rem,92vw)] flex-col bg-card shadow-lift focus:outline-none",
                  side === "left"
                    ? "left-0 border-r border-border/60"
                    : "right-0 border-l border-border/60"
                )}
              >
                {/* 头部 */}
                <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-border/50 px-4">
                  <div className="flex min-w-0 items-center gap-2">
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
                      <Receipt className="h-3.5 w-3.5" />
                    </span>
                    <Dialog.Title className="truncate text-sm font-semibold">
                      交易流水
                    </Dialog.Title>
                    {isMock && <Badge variant="outline">Mock</Badge>}
                  </div>
                  <Dialog.Close asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label="关闭流水"
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </Dialog.Close>
                </div>

                {/* 月份导航 */}
                <div className="flex shrink-0 items-center justify-between gap-2 px-4 pt-3">
                  <div className="flex items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={() => setMonth((m) => shiftMonth(m, -1))}
                      aria-label="上个月"
                    >
                      <ChevronLeft className="h-3.5 w-3.5" />
                    </Button>
                    <span className="min-w-[5.5rem] text-center text-xs font-medium tabular-nums">
                      {monthLabel(month)}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6"
                      onClick={() => setMonth((m) => shiftMonth(m, 1))}
                      aria-label="下个月"
                    >
                      <ChevronRight className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                  {month !== nowMonth && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 px-2 text-[11px]"
                      onClick={() => setMonth(nowMonth)}
                    >
                      回到本月
                    </Button>
                  )}
                </div>

                {/* 汇总：与 KPI 卡同色系（收入青绿 / 支出陶土橙 / 结余鼠尾草绿） */}
                <div className="mx-4 mt-3 grid shrink-0 grid-cols-3 gap-2 rounded-lg border border-border/50 bg-muted/30 p-2.5">
                  <SummaryCell label="收入" value={income} tone="text-chart-1" />
                  <SummaryCell label="支出" value={expense} tone="text-chart-3" />
                  <SummaryCell
                    label="结余"
                    value={income - expense}
                    tone="text-chart-2"
                  />
                </div>

                {/* 列表 */}
                <div className="min-h-0 flex-1">
                  {items === null ? (
                    <ListSkeleton />
                  ) : error ? (
                    <ListError
                      message={error}
                      onRetry={() => void load(month)}
                    />
                  ) : groups.length === 0 ? (
                    <ListEmpty month={month} />
                  ) : (
                    <ScrollArea className="h-full">
                      <div className="flex flex-col gap-3 px-2 py-3">
                        {groups.map(([date, rows]) => (
                          <div key={date}>
                            <h4 className="px-2.5 pb-1 text-[11px] font-medium text-muted-foreground/80">
                              {dateLabel(date)}
                            </h4>
                            <div className="flex flex-col">
                              {rows.map((t) => (
                                <TransactionRow
                                  key={t.id}
                                  item={t}
                                  pending={pendingId === t.id}
                                  onAskDelete={() =>
                                    setPendingDelete({ month, id: t.id })
                                  }
                                  onCancelDelete={() => setPendingDelete(null)}
                                  onConfirmDelete={() => void handleDelete(t.id)}
                                />
                              ))}
                            </div>
                          </div>
                        ))}
                      </div>
                    </ScrollArea>
                  )}
                </div>

                <div className="shrink-0 border-t border-border/50 px-4 py-2.5">
                  <p className="text-[11px] leading-relaxed text-muted-foreground">
                    {"在对话里说「帮我记一笔今天的午餐 35 元」，Agent 记完这里就会出现。"}
                  </p>
                </div>

                <Dialog.Description className="sr-only">
                  Agent 通过对话记录的收入与支出明细，可按月份查看与删除。
                </Dialog.Description>
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  )
}

function SummaryCell({
  label,
  value,
  tone,
}: {
  label: string
  value: number
  tone: string
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span
        className={cn(
          "truncate text-sm font-semibold tabular-nums",
          tone
        )}
      >
        {formatCurrency(value)}
      </span>
    </div>
  )
}

function TransactionRow({
  item,
  pending,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete,
}: {
  item: Transaction
  pending: boolean
  onAskDelete: () => void
  onCancelDelete: () => void
  onConfirmDelete: () => void
}) {
  const isIncome = item.transaction_type === "income"
  const meta = [timeLabel(item.occurred_at), item.note].filter(Boolean).join(" · ")

  return (
    <div className="group flex items-center gap-2.5 rounded-lg px-2.5 py-2 transition-colors hover:bg-accent/40">
      <span
        className={cn(
          "flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
          isIncome ? "bg-chart-1/10 text-chart-1" : "bg-chart-3/12 text-chart-3"
        )}
      >
        {isIncome ? (
          <ArrowDownLeft className="h-3.5 w-3.5" />
        ) : (
          <ArrowUpRight className="h-3.5 w-3.5" />
        )}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-1.5">
          <span className="truncate text-xs font-medium text-foreground">
            {item.category}
          </span>
          {item.source && (
            <span className="truncate text-[11px] text-muted-foreground">
              {item.source}
            </span>
          )}
        </div>
        {meta && (
          <p className="truncate text-[11px] text-muted-foreground/70">
            {meta}
          </p>
        )}
      </div>

      <span
        className={cn(
          "shrink-0 text-xs font-medium tabular-nums",
          isIncome ? "text-chart-1" : "text-foreground"
        )}
      >
        {isIncome ? "+" : "−"}
        {formatCurrency(item.amount)}
      </span>

      {pending ? (
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            className="rounded px-1.5 py-0.5 text-[11px] text-destructive transition-colors hover:bg-destructive/10"
            onClick={onConfirmDelete}
          >
            删除
          </button>
          <button
            type="button"
            className="rounded px-1.5 py-0.5 text-[11px] text-muted-foreground transition-colors hover:bg-accent"
            onClick={onCancelDelete}
          >
            取消
          </button>
        </div>
      ) : (
        <button
          type="button"
          className="shrink-0 rounded p-1 text-muted-foreground/40 opacity-0 transition-opacity hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
          onClick={onAskDelete}
          title="删除这条流水"
          aria-label="删除这条流水"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  )
}

function ListSkeleton() {
  return (
    <div className="flex flex-col gap-2 px-4 py-3">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-11 animate-pulse rounded-lg border border-border/40 bg-muted/50"
        />
      ))}
    </div>
  )
}

function ListEmpty({ month }: { month: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
      <span className="flex h-11 w-11 items-center justify-center rounded-full bg-primary/10 text-primary">
        <Receipt className="h-5 w-5" />
      </span>
      <div className="space-y-1">
        <p className="text-sm font-medium">{monthLabel(month)}没有流水</p>
        <p className="text-xs leading-relaxed text-muted-foreground">
          试试在对话里说「帮我记一笔今天的午餐 35 元」，Agent 记完这里就会出现。
        </p>
      </div>
    </div>
  )
}

function ListError({
  message,
  onRetry,
}: {
  message: string
  onRetry: () => void
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
      <p className="text-xs text-muted-foreground">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCw className="h-3.5 w-3.5" />
        重试
      </Button>
    </div>
  )
}

/** 本地当前月 YYYY-MM */
function currentMonth(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`
}

/** YYYY-MM 位移 delta 个月，自动处理跨年 */
function shiftMonth(month: string, delta: number): string {
  const [year, mon] = month.split("-").map(Number)
  const d = new Date(year, mon - 1 + delta, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`
}

/** 2026-10 → "2026 年 10 月" */
function monthLabel(month: string): string {
  const [year, mon] = month.split("-")
  return `${year} 年 ${Number(mon)} 月`
}

/** 2026-10-18 → "10 月 18 日 · 周日"（按本地日历算，不做时区换算） */
function dateLabel(date: string): string {
  const [year, mon, day] = date.split("-").map(Number)
  if (!year || !mon || !day) return date
  const weekday = WEEKDAYS[new Date(year, mon - 1, day).getDay()]
  return `${mon} 月 ${day} 日 · ${weekday}`
}

/** "2026-10-18T12:30:00" → "12:30"；没有时间部分返回空串 */
function timeLabel(iso: string): string {
  return iso.slice(11, 16)
}

/** 按 occurred_at 的日期分组；后端已按时间倒序，Map 保留插入顺序即最新在前 */
function groupByDate(list: Transaction[]): [string, Transaction[]][] {
  const groups = new Map<string, Transaction[]>()
  for (const t of list) {
    const key = t.occurred_at.slice(0, 10) || "未知日期"
    const bucket = groups.get(key)
    if (bucket) bucket.push(t)
    else groups.set(key, [t])
  }
  return [...groups.entries()]
}
