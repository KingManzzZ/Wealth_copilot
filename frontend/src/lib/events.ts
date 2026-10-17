/**
 * 跨组件失效广播（最小事件总线）。
 *
 * 场景：Agent 在一轮对话里可能通过工具写库（record_asset / record_expense /
 * update_basic_info…），但这些写入发生在后端，前端各视图（Dashboard、UserMenu、
 * 流水抽屉）并不知道自己的数据已经过期。
 *
 * 设计取舍：
 * - 只有**一个**事件："一轮对话结束"（chat-turn-completed）。不细分"谁写了什么"，
 *   因为写入口会随工具集增长，判断"这轮有没有写"一旦判错就是静默不刷新；
 *   消费者只是重拉 1~2 个聚合 GET，代价远小于漏刷。
 * - 不做全局状态库：window.dispatchEvent 足够，且不引入 Provider 层级。
 * - 事件名只在这里定义，任何组件不得写字面量，避免生产端/消费端漂移。
 */

/** 一轮对话结束事件名 */
export const CHAT_TURN_COMPLETED = "chat-turn-completed"

/** 事件载荷 */
export interface ChatTurnDetail {
  /** 本轮所属会话；未落库的轮次（后端不可用/建会话失败）为 null */
  conversationId: number | null
  /** 本轮调用过的工具名，按调用顺序；用于排查与"流水有新数据"红点 */
  toolNames: string[]
}

/** 会写 transactions 表的工具（红点与提示用） */
export const TRANSACTION_WRITE_TOOLS = ["record_income", "record_expense"]

/** 本轮是否写过流水 */
export function hasTransactionWrite(toolNames: string[]): boolean {
  return toolNames.some((name) => TRANSACTION_WRITE_TOOLS.includes(name))
}

/** 广播：一轮对话结束（由 useChat 的 finally 调用） */
export function emitChatTurnCompleted(detail: ChatTurnDetail): void {
  window.dispatchEvent(
    new CustomEvent<ChatTurnDetail>(CHAT_TURN_COMPLETED, { detail })
  )
}

/** 订阅：返回解绑函数，直接作为 useEffect 的清理函数 */
export function onChatTurnCompleted(
  handler: (detail: ChatTurnDetail) => void
): () => void {
  const listener = (event: Event) => {
    handler((event as CustomEvent<ChatTurnDetail>).detail)
  }
  window.addEventListener(CHAT_TURN_COMPLETED, listener)
  return () => window.removeEventListener(CHAT_TURN_COMPLETED, listener)
}
