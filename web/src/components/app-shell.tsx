import { useState } from "react"
import { TopBar } from "@/components/top-bar"
import { ChatPanel } from "@/components/chat/chat-panel"
import { Dashboard } from "@/components/dashboard/dashboard"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/lib/utils"
import type { ChatSide } from "@/types"

/**
 * 应用主壳：顶栏 + 聊天常驻栏 + 仪表盘
 * 外壳保持透明，让 body 的暖调氛围层透出来
 *
 * 聊天栏位置通过 flex-row-reverse 翻转（DOM 顺序不变），
 * 由聊天面板头部右上角的按钮切换。
 */
export function AppShell() {
  const [chatSide, setChatSide] = useState<ChatSide>("left")

  const toggleSide = () =>
    setChatSide((s) => (s === "left" ? "right" : "left"))

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <TopBar />
      <div
        className={cn(
          "flex min-h-0 flex-1",
          chatSide === "right" && "flex-row-reverse"
        )}
      >
        {/* 聊天常驻栏：半透明纸面，透出背景氛围 */}
        <aside className="flex w-[400px] shrink-0 flex-col bg-card/45 backdrop-blur-[2px]">
          <ChatPanel side={chatSide} onToggleSide={toggleSide} />
        </aside>
        <Separator orientation="vertical" className="bg-border/50" />
        {/* 仪表盘主区 */}
        <main className="flex min-w-0 flex-1 flex-col">
          <Dashboard />
        </main>
      </div>
    </div>
  )
}
