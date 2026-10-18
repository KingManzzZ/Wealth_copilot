"""
会话接口（路由层）。

- GET    /api/conversations                  会话列表（历史记录）
- POST   /api/conversations                  创建会话（首条消息发出时惰性调用）
- GET    /api/conversations/{id}/messages    懒加载某会话全部消息
- POST   /api/conversations/{id}/messages    批量追加消息（每轮对话一次写入）
- DELETE /api/conversations/{id}             删除会话（messages 级联清除）

处理函数使用同步 def：SQLite 访问为阻塞 I/O，交给 FastAPI 线程池执行。
"""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.services import turn_state
from core.services.conversations import (
    append_messages,
    create_conversation,
    delete_conversation,
    get_conversation_messages,
    list_conversations,
)

router = APIRouter()


class ConversationCreateRequest(BaseModel):
    title: str | None = None


class MessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    tool_calls: list | None = None
    turn_id: str | None = None


class AppendMessagesRequest(BaseModel):
    messages: list[MessageIn]


class WithdrawRequest(BaseModel):
    turn_id: str


@router.get("/conversations")
def list_conversations_endpoint():
    """会话列表（按 updated_at 倒序）。"""
    return list_conversations()


@router.post("/conversations")
def create_conversation_endpoint(data: ConversationCreateRequest | None = None):
    """创建会话；title 可空（追加首条 user 消息时自动补）。"""
    return create_conversation(data.title if data else None)


@router.get("/conversations/{conversation_id}/messages")
def read_conversation_messages(conversation_id: int):
    """懒加载某会话全部消息；不存在返回 404。"""
    messages = get_conversation_messages(conversation_id)
    if messages is None:
        raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")
    return messages


@router.post("/conversations/{conversation_id}/messages")
def append_conversation_messages(conversation_id: int, body: AppendMessagesRequest):
    """批量追加消息；会话不存在返回 404。"""
    result = append_messages(
        conversation_id,
        [m.model_dump(exclude_none=True) for m in body.messages],
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")
    return result


@router.delete("/conversations/{conversation_id}")
def delete_conversation_endpoint(conversation_id: int):
    """删除会话；messages 行由 ON DELETE CASCADE 级联清除。"""
    if not delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")
    return {"ok": True}


@router.post("/conversations/{conversation_id}/withdraw")
def withdraw_turn(conversation_id: int, body: WithdrawRequest):
    """撤回一轮**及其之后的所有轮次**：消息消失 + 这些轮次写过的数据还原。

    - 从这一轮起（含）后面的对话一起撤，保证上下文不前后矛盾；
    - 如果这一轮还在生成，先停下来再撤；
    - 数据在这些轮次之后被改过 → 整体放弃并返回 409（不覆盖用户的修改）。
    """
    if get_conversation_messages(conversation_id) is None:
        raise HTTPException(status_code=404, detail=f"会话 {conversation_id} 不存在")

    # 延迟导入：chat 路由与 conversations 路由互不依赖，但 stop_turn 在 chat 里
    from api.routes.chat import stop_turn

    if not stop_turn(conversation_id, "withdraw"):
        raise HTTPException(
            status_code=409,
            detail="这一轮仍在停止中，请稍后重试撤回",
        )

    try:
        return turn_state.withdraw(conversation_id, body.turn_id)
    except turn_state.TurnNotFound:
        raise HTTPException(status_code=404, detail="这一轮不存在或已撤回")
    except turn_state.NoWritesRecorded:
        raise HTTPException(status_code=409, detail="这一轮尚未收尾，暂时不能撤回")
    except turn_state.DataConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "这些轮次写过的数据之后被修改过，已放弃撤回（不会覆盖你的修改）",
                "conflicts": [
                    {
                        "table": item["write"]["table_name"],
                        "op": item["write"]["op"],
                        "reason": item["reason"],
                    }
                    for item in exc.items
                ],
            },
        )
