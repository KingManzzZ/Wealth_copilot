"""
跨会话记忆（L3）：非结构化偏好 / 约束 / 事实。

## 为什么只有这一小块

财务事实（资产、负债、目标、流水）已经在结构化表里，**抽取只会引入误差**；
真正缺的是"不接受股票""偏好稳健""每月 10 号发工资"这类表里放不下的内容。
所以这里不做 Mem0 式抽取管线，只做一个带类型白名单、时效、可删除的轻量笔记表。

## 三条硬约束（来自 ``Investigation/上下文机制/03-L3-跨会话记忆.md``）

1. ``category`` 白名单：``preference`` / ``constraint`` / ``fact``，不让模型自由发挥；
2. ``source = "model_inferred"`` 的条目不进 prompt——防止一次推测变成永久偏见；
3. ``expires_at`` 支持时效；检索时**先按状态/时效过滤，再取**。

另外：记忆是**数据不是指令**，检索结果必须带 id 与时间，模型才能判断可信度与取舍。
"""

from __future__ import annotations

from datetime import date

from core.database import get_connection
from core.services._common import OWNER_ID, _now

#: 类型白名单（写入与检索共用）
CATEGORIES = ("preference", "constraint", "fact")

#: 单条内容长度上限（超过说明用户说的不是"一条偏好"，而是叙事）
MAX_CONTENT_CHARS = 500

#: 单用户活跃条目上限，超过时拒绝新写入（防止检索噪声无限上升）
MAX_ACTIVE_NOTES = 200


def _row_to_note(row) -> dict:
    return {
        "id": row["id"],
        "category": row["category"],
        "content": row["content"],
        "source": row["source"],
        "status": row["status"],
        "expires_at": row["expires_at"],
        "source_conversation_id": row["source_conversation_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def add_note(
    category: str,
    content: str,
    source: str = "user_stated",
    expires_at: str | None = None,
    conversation_id: int | None = None,
) -> dict:
    """新增一条记忆；``category`` 不在白名单时抛 ``ValueError``。"""
    category = (category or "").strip()
    content = (content or "").strip()
    if category not in CATEGORIES:
        raise ValueError(f"category 只能是 {', '.join(CATEGORIES)}")
    if not content:
        raise ValueError("content 不能为空")
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS]
    if source not in ("user_stated", "model_inferred"):
        raise ValueError("source 只能是 user_stated / model_inferred")
    if expires_at:
        try:
            date.fromisoformat(expires_at[:10])
        except ValueError as exc:
            raise ValueError("expires_at 必须是 YYYY-MM-DD") from exc

    now = _now()
    conn = get_connection()
    try:
        existing = conn.execute(
            """
            SELECT * FROM user_notes
            WHERE user_id = ? AND category = ? AND content = ?
              AND status = 'active'
            LIMIT 1
            """,
            (OWNER_ID, category, content),
        ).fetchone()
        if existing is not None:
            note = _row_to_note(existing)
            note["duplicate"] = True
            return note

        active = conn.execute(
            "SELECT COUNT(*) FROM user_notes WHERE user_id = ? AND status = 'active'",
            (OWNER_ID,),
        ).fetchone()[0]
        if active >= MAX_ACTIVE_NOTES:
            raise ValueError(f"记忆条目已达上限 {MAX_ACTIVE_NOTES}，请先删除旧条目")

        cursor = conn.execute(
            """
            INSERT INTO user_notes
                (user_id, category, content, source, source_conversation_id,
                 status, expires_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                OWNER_ID,
                category,
                content,
                source,
                conversation_id,
                expires_at,
                now,
                now,
            ),
        )
        conn.commit()
        note_id = cursor.lastrowid
    finally:
        conn.close()

    note = {
        "id": note_id,
        "category": category,
        "content": content,
        "source": source,
        "status": "active",
        "expires_at": expires_at,
        "source_conversation_id": conversation_id,
        "created_at": now,
        "updated_at": now,
        "duplicate": False,
    }
    return note


def list_notes(include_inferred: bool = False, include_expired: bool = False) -> list:
    """列出活跃记忆；默认只含 ``user_stated`` 且未过期。"""
    clauses = ["user_id = ?", "status = 'active'"]
    params: list = [OWNER_ID]
    if not include_inferred:
        clauses.append("source = 'user_stated'")
    if not include_expired:
        clauses.append(
            "(expires_at IS NULL OR substr(expires_at, 1, 10) >= date('now','localtime'))"
        )
    sql = (
        "SELECT * FROM user_notes WHERE "
        + " AND ".join(clauses)
        + " ORDER BY CASE category WHEN 'constraint' THEN 0 "
        "WHEN 'preference' THEN 1 ELSE 2 END, updated_at DESC, id DESC"
    )
    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_row_to_note(row) for row in rows]


def list_for_prompt(limit: int = 12) -> list:
    """给 prompt 用的检索：只取可信、未过期、按类别优先级排序的前 N 条。"""
    return list_notes(include_inferred=False, include_expired=False)[:limit]


def forget_note(note_id: int) -> bool:
    """软删除一条记忆（保留审计）。不存在/已删除返回 False。"""
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE user_notes
            SET status = 'deleted', updated_at = ?
            WHERE id = ? AND user_id = ? AND status = 'active'
            """,
            (_now(), note_id, OWNER_ID),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def count_active() -> int:
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM user_notes WHERE user_id = ? AND status = 'active'",
            (OWNER_ID,),
        ).fetchone()[0]
    finally:
        conn.close()
