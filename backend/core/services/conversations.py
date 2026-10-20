"""
会话 Service 层。

职责：管理 conversations + messages 两张表，支撑 GPT 式多会话：
- 会话行惰性创建（用户发出首条消息时才建，空会话不落库）
- 消息按前端 UI 结构嵌套存储：assistant 行 tool_calls 列存完整 JSON 数组
  （含 id / name / arguments / result / status），还原时 1:1 映射回 ChatMessage
- summary / status 字段：status 预留（删除为硬删 + ON DELETE CASCADE）；
  summary 及两个压缩位置字段由 ``core/agent/compaction.py`` 写入（长会话压缩），
  本模块只负责存储与读取
"""

import json

from core.database import get_connection
from core.services._common import OWNER_ID, _now

# 历史记录标题最大长度（由首条用户消息截取）
TITLE_MAX_LEN = 20


def list_conversations() -> list:
    """列出全部会话（按 updated_at 倒序），供前端历史记录。

    返回 [{id, title, updated_at}, ...]
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, title, updated_at
            FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (OWNER_ID,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"id": row["id"], "title": row["title"], "updated_at": row["updated_at"]}
        for row in rows
    ]


def create_conversation(title: str | None = None) -> dict:
    """创建新会话，返回 {id, title, updated_at}。"""
    now = _now()
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO conversations (user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (OWNER_ID, title, now, now),
        )
        conn.commit()
        conversation_id = cursor.lastrowid
    finally:
        conn.close()
    return {"id": conversation_id, "title": title, "updated_at": now}


def get_conversation_messages(conversation_id: int) -> list | None:
    """取某会话全部消息（按 id 升序）；会话不存在返回 None。

    返回 [{id, role, content, tool_calls?: list}, ...]
    """
    conn = get_connection()
    try:
        conv = conn.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, OWNER_ID),
        ).fetchone()
        if conv is None:
            return None
        rows = conn.execute(
            """
            SELECT id, role, content, tool_calls, turn_id,
                   interrupted, stop_reason
            FROM messages
            WHERE conversation_id = ? AND withdrawn = 0
            ORDER BY id
            """,
            (conversation_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_message(row) for row in rows]


def _row_to_message(row) -> dict:
    """将 messages 表行转为契约 dict；tool_calls JSON 解析失败时降级为空数组。

    ``turn_id`` 一并返回：前端要靠它发起"撤回这一轮"。
    ``interrupted`` 让历史里被中止的轮次也能标成"已停止生成"。
    """
    message = {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "interrupted": bool(row["interrupted"]),
    }
    if row["turn_id"]:
        message["turn_id"] = row["turn_id"]
    if row["stop_reason"]:
        message["stop_reason"] = row["stop_reason"]
    if row["tool_calls"]:
        try:
            message["tool_calls"] = json.loads(row["tool_calls"])
        except (TypeError, ValueError):
            message["tool_calls"] = []
    return message


def append_messages(conversation_id: int, items: list) -> dict | None:
    """单事务批量追加消息；会话不存在返回 None。

    items 元素（路由层已用 Pydantic 校验）：``{role, content, tool_calls?,
    interrupted?, stop_reason?}``。仅接受 user / assistant 两种角色——
    tool 中间态已内嵌在 assistant.tool_calls。

    ``interrupted`` 只对 assistant 有意义：标记"这一轮是被中止的"，
    回放时据此把整轮排除出模型上下文（UI 仍然展示）。
    顺带：刷新会话 updated_at；title 为空且含 user 消息时截前 20 字补标题。
    """
    cleaned = []
    for item in items:
        entry = {"role": item["role"], "content": item["content"]}
        if item["role"] == "assistant":
            if item.get("tool_calls"):
                entry["tool_calls"] = item["tool_calls"]
            if item.get("interrupted"):
                entry["interrupted"] = True
                entry["stop_reason"] = item.get("stop_reason") or "client"
        if item.get("turn_id"):
            entry["turn_id"] = item["turn_id"]
        cleaned.append(entry)

    now = _now()
    conn = get_connection()
    try:
        conv = conn.execute(
            "SELECT id, title FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, OWNER_ID),
        ).fetchone()
        if conv is None:
            return None

        if cleaned:
            for entry in cleaned:
                conn.execute(
                    """
                    INSERT INTO messages
                        (conversation_id, role, content, tool_calls,
                         turn_id, interrupted, stop_reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        entry["role"],
                        entry["content"],
                        json.dumps(entry["tool_calls"], ensure_ascii=False)
                        if "tool_calls" in entry
                        else None,
                        entry.get("turn_id"),
                        1 if entry.get("interrupted") else 0,
                        entry.get("stop_reason"),
                    ),
                )

        # 首条 user 消息生成标题（历史列表展示用）
        title = conv["title"]
        if not title:
            first_user = next(
                (e["content"].strip() for e in cleaned if e["role"] == "user"), ""
            )
            if first_user:
                title = first_user[:TITLE_MAX_LEN]

        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, conversation_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "conversation_id": conversation_id,
        "title": title,
        "saved": len(cleaned),
    }


def delete_conversation(conversation_id: int) -> bool:
    """删除会话；messages 行由 ON DELETE CASCADE 级联清除。"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, OWNER_ID),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()
