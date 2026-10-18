"""
会话撤回提案工具（**只读**）。

## 它不是"撤回工具"

用户说"把刚才那笔记账撤回"时，模型不该直接删数据——撤回会永久移除对话并还原账本，
必须由人确认。所以这个工具只做一件事：**生成一份提案**，返回给前端渲染确认卡片；
真正执行的是 ``POST /api/conversations/{id}/withdraw``，且只在用户点「确认撤回」时调用。

## 目标怎么确定

工具签名里没有 ``conversation_id``，也不需要模型记住轮次 id：

- 不传 ``turn_id`` → 取"最近一个**已完成**的轮次"，并排除正在生成的这一轮
  （用户说"撤回刚才那轮"就是它）；
- 传了 ``turn_id`` → 校验它属于当前会话、未撤回、不在生成中。

这些信息来自 ``core/agent/turn_context.py`` 的线程本地上下文（worker 线程绑定）。
"""

from __future__ import annotations

from core.agent import turn_context
from core.services import turn_state

__all__ = ["request_withdraw"]


def request_withdraw(turn_id: str = "", reason: str = "") -> dict:
    """生成"撤回某一轮"的确认提案（**不会执行撤回**）。

    适用：用户说「撤回 / 撤销 / 回退 / 刚才那轮不要了 / 删掉上一轮」。
    不适用：删除某一条流水（那是 delete_transaction_tool）。

    Args:
        turn_id: 要撤回的轮次 id；**留空表示最近一轮已完成的对话**，不确定时不要传。
        reason: 用户的原始说法（可选，仅用于卡片展示）。

    Returns:
        提案载荷，含 ``proposal="withdraw_turn"``、``turn_id``、``summary`` 等；
        需要用户在界面上点「确认撤回」才真正执行。
    """
    context = turn_context.snapshot()
    if context is None:
        return {
            "error": "当前不在对话轮次内，无法生成撤回提案",
            "error_kind": "no_turn_context",
            "retryable": False,
        }

    conversation_id, current_turn_id = context
    try:
        preview = turn_state.preview_withdraw(
            conversation_id,
            turn_id=(turn_id or "").strip() or None,
            exclude_turn_id=current_turn_id,
        )
    except turn_state.NoWithdrawableTurn as exc:
        return {
            "error": str(exc),
            "error_kind": "no_withdrawable_turn",
            "retryable": False,
        }
    except turn_state.TurnNotFound:
        return {
            "error": f"轮次 {turn_id} 不存在或已撤回",
            "error_kind": "turn_not_found",
            "retryable": False,
        }
    except turn_state.NoWritesRecorded:
        return {
            "error": "这一轮还在收尾，暂时不能撤回",
            "error_kind": "turn_running",
            "retryable": False,
        }

    head = preview["user_text"][:20]
    if preview["affected_turns"] > 1:
        summary = f"将撤回从「{head}」开始的 {preview['affected_turns']} 轮对话"
    else:
        summary = f"将撤回「{head}」这一轮对话"
    if preview["affected_writes"]:
        summary += f"，并还原 {preview['affected_writes']} 处数据变更"

    return {
        "proposal": "withdraw_turn",
        "requires_confirmation": True,
        "conversation_id": conversation_id,
        "turn_id": preview["turn_id"],
        "affected_turns": preview["affected_turns"],
        "affected_messages": preview["affected_messages"],
        "affected_writes": preview["affected_writes"],
        "user_text": preview["user_text"],
        "summary": summary,
        "reason": (reason or "").strip()[:200],
        "truncate": False,
        "confirm_endpoint": f"/api/conversations/{conversation_id}/withdraw",
        "notice": "不要直接说已撤回：请让用户点界面上的「确认撤回」。",
    }
