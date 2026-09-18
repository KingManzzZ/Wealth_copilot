import { useEffect, useRef, useState } from "react"
import type { KeyboardEvent } from "react"
import { Send } from "lucide-react"
import { Button } from "@/components/ui/button"

interface ChatInputProps {
  onSend: (text: string) => void
  disabled: boolean
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [value, setValue] = useState("")
  const ref = useRef<HTMLTextAreaElement>(null)

  const submit = () => {
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setValue("")
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  // 自适应高度
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [value])

  return (
    <div className="flex items-end gap-2 rounded-xl border border-border/60 bg-card p-1.5 shadow-soft transition-shadow duration-300 focus-within:shadow-lift focus-within:ring-2 focus-within:ring-ring/35">
      <textarea
        ref={ref}
        value={value}
        onChange={(e) => setValue(e.target.value)}
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
