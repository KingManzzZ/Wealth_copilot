import { useState, useRef, useCallback, useEffect } from "react"
import { TopBar } from "@/components/top-bar"
import { ChatPanel } from "@/components/chat/chat-panel"
import { Dashboard } from "@/components/dashboard/dashboard"
import { TransactionsDrawer } from "@/components/transactions/transactions-drawer"
import { useChatTurnCompleted } from "@/hooks/useChatTurnCompleted"
import { hasTransactionWrite } from "@/lib/events"
import { cn } from "@/lib/utils"
import type { ChatSide } from "@/types"

const MIN_WIDTH = 280
const MAX_WIDTH = 600
const DEFAULT_WIDTH = 400

/**
 * 应用主壳：顶栏 + 聊天常驻栏 + 仪表盘
 * 外壳保持透明，让 body 的暖调氛围层透出来
 *
 * 聊天栏位置通过 flex-row-reverse 翻转（DOM 顺序不变），
 * 由聊天面板头部右上角的按钮切换。
 */
export function AppShell() {
  const [chatSide, setChatSide] = useState<ChatSide>("right")
  const [chatWidth, setChatWidth] = useState(DEFAULT_WIDTH)
  const [transactionsOpen, setTransactionsOpen] = useState(false)
  const [hasNewTransactions, setHasNewTransactions] = useState(false)
  const isDragging = useRef(false)
  const startX = useRef(0)
  const startWidth = useRef(0)

  // Agent 记了账 → 顶栏「流水」按钮点亮提示点；打开抽屉即熄灭。
  // 抽屉已经开着时不点亮（正在看，无需提示）。
  useChatTurnCompleted((detail) => {
    if (transactionsOpen) return
    if (hasTransactionWrite(detail.toolNames)) setHasNewTransactions(true)
  })

  const toggleTransactions = useCallback(() => {
    setTransactionsOpen((v) => !v)
    setHasNewTransactions(false)
  }, [])

  const toggleSide = () =>
    setChatSide((s) => (s === "left" ? "right" : "left"))

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      isDragging.current = true
      startX.current = e.clientX
      startWidth.current = chatWidth
      document.body.style.cursor = "col-resize"
      document.body.style.userSelect = "none"
    },
    [chatWidth]
  )

  const handleMouseMove = useCallback(
    (e: MouseEvent) => {
      if (!isDragging.current) return

      const delta = e.clientX - startX.current
      // 聊天栏在左侧时，向右拖增加宽度；在右侧时（flex-row-reverse），向左拖增加宽度
      const direction = chatSide === "left" ? 1 : -1
      const newWidth = startWidth.current + delta * direction

      setChatWidth(Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, newWidth)))
    },
    [chatSide]
  )

  const handleMouseUp = useCallback(() => {
    isDragging.current = false
    document.body.style.cursor = ""
    document.body.style.userSelect = ""
  }, [])

  useEffect(() => {
    document.addEventListener("mousemove", handleMouseMove)
    document.addEventListener("mouseup", handleMouseUp)
    return () => {
      document.removeEventListener("mousemove", handleMouseMove)
      document.removeEventListener("mouseup", handleMouseUp)
    }
  }, [handleMouseMove, handleMouseUp])

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar
        onToggleTransactions={toggleTransactions}
        hasNewTransactions={hasNewTransactions}
      />
      <div
        className={cn(
          "flex min-h-0 flex-1",
          chatSide === "right" && "flex-row-reverse"
        )}
      >
        {/* 聊天常驻栏：半透明纸面，透出背景氛围 */}
        <aside
          className="flex shrink-0 flex-col bg-card/45 backdrop-blur-[2px]"
          style={{ width: chatWidth }}
        >
          <ChatPanel side={chatSide} onToggleSide={toggleSide} />
        </aside>

        {/* 拖拽手柄 + 分隔线 */}
        <div
          className="group relative w-2 cursor-col-resize flex items-center justify-center hover:bg-primary/5 transition-colors"
          onMouseDown={handleMouseDown}
        >
          <div className="h-full w-px bg-border/50 group-hover:bg-primary/40 group-active:bg-primary/60 transition-colors" />
        </div>
        {/* 仪表盘主区 */}
        <main className="flex min-w-0 flex-1 flex-col">
          <Dashboard />
        </main>
      </div>

      {/* 流水抽屉：开在聊天栏的另一侧，避免盖住对话。
          挂根层而非 header 内 —— 顶栏的 backdrop-filter 会成为 fixed 后代的包含块 */}
      <TransactionsDrawer
        open={transactionsOpen}
        onClose={() => setTransactionsOpen(false)}
        side={chatSide === "right" ? "left" : "right"}
      />
    </div>
  )
}
