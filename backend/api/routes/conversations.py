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


class AppendMessagesRequest(BaseModel):
    messages: list[MessageIn]


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
