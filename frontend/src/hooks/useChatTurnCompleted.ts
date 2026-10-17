import { useEffect, useRef } from "react"
import { onChatTurnCompleted, type ChatTurnDetail } from "@/lib/events"

/**
 * 订阅「一轮对话结束」事件。
 *
 * handler 用 ref 持有最新闭包，effect 的 deps 为空 —— 订阅只建立一次，
 * 不会因为 handler 里引用了会变的状态（open / month 等）而反复解绑重绑。
 */
export function useChatTurnCompleted(
  handler: (detail: ChatTurnDetail) => void
): void {
  const handlerRef = useRef(handler)

  // 每次渲染后同步最新闭包
  useEffect(() => {
    handlerRef.current = handler
  })

  useEffect(() => onChatTurnCompleted((detail) => handlerRef.current(detail)), [])
}
