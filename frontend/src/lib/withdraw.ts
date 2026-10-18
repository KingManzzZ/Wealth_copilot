import type { ChatMessage } from "@/types"

/** `request_withdraw` 提案的前端形态（来自 tool_result） */
export interface WithdrawProposal {
  turnId: string
  summary: string
  affectedTurns: number
  affectedWrites: number
  userText: string
}

/**
 * 从一条 assistant 消息里找撤回提案。
 *
 * 提案是**只读工具** `request_withdraw` 的返回；找到后渲染成确认卡片，
 * 用户点「确认撤回」才真正调用 `POST /api/conversations/{id}/withdraw`。
 */
export function findWithdrawProposal(
  message: ChatMessage
): WithdrawProposal | null {
  for (const call of message.toolCalls ?? []) {
    const result = call.result as Record<string, unknown> | undefined
    if (!result || result.proposal !== "withdraw_turn") continue
    const turnId = typeof result.turn_id === "string" ? result.turn_id : ""
    if (!turnId) continue
    return {
      turnId,
      summary:
        typeof result.summary === "string" ? result.summary : "撤回这一轮对话",
      affectedTurns: Number(result.affected_turns) || 1,
      affectedWrites: Number(result.affected_writes) || 0,
      userText: typeof result.user_text === "string" ? result.user_text : "",
    }
  }
  return null
}
