import { useEffect } from "react"
import type { KeyboardEvent, RefObject } from "react"
import { Send } from "lucide-react"
import { Button } from "@/components/ui/button"

interface ChatInputProps {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  disabled: boolean
  /** 外部传入的 textarea ref，供父组件聚焦/操作光标 */
  textareaRef: RefObject<HTMLTextAreaElement | null>
}

export function ChatInput({
  value,
  onChange,
  onSend,
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
      <Button
        size="icon"
        onClick={submit}
        disabled={disabled || !value.trim()}
        aria-label="发送"
      >
        <Send className="h-4 w-4" />
      </Button>
    </div>
  )
}
