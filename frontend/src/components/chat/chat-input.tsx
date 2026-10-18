import { useEffect } from "react"
import type { KeyboardEvent, RefObject } from "react"
import { Send, Square } from "lucide-react"
import { Button } from "@/components/ui/button"

interface ChatInputProps {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  /** 中止当前流（保留已生成内容） */
  onStop: () => void
  /** 是否正在流式生成 —— 决定右下角是「发送」还是「停止」 */
  streaming: boolean
  /** 不能发送（正在生成 / 空输入） */
  disabled: boolean
  /** 外部传入的 textarea ref，供父组件聚焦/操作光标 */
  textareaRef: RefObject<HTMLTextAreaElement | null>
}

export function ChatInput({
  value,
  onChange,
  onSend,
  onStop,
  streaming,
  disabled,
  textareaRef,
}: ChatInputProps) {
  const submit = () => {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSend()
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  // 自适应高度
  useEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [value, textareaRef])

  return (
    <div className="flex items-end gap-2 rounded-xl border border-border/60 bg-card p-1.5 shadow-soft transition-shadow duration-300 focus-within:shadow-lift focus-within:ring-2 focus-within:ring-ring/35">
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        rows={1}
        placeholder="问 WealthPilot…（Enter 发送，Shift+Enter 换行）"
        className="max-h-40 flex-1 resize-none bg-transparent px-2.5 py-2 text-sm leading-relaxed placeholder:text-muted-foreground focus:outline-none"
      />
      {streaming ? (
        // 生成中：主操作变成「停止」。不把发送按钮留在旁边，避免滚轮误触发出第二条消息
        <Button
          variant="outline"
          size="icon"
          onClick={onStop}
          title="停止生成（保留已生成内容）"
          aria-label="停止生成"
          className="shrink-0"
        >
          <Square className="h-3.5 w-3.5 fill-current" />
        </Button>
      ) : (
        <Button
          size="icon"
          onClick={submit}
          disabled={disabled || !value.trim()}
          aria-label="发送"
          className="shrink-0"
        >
          <Send className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}
