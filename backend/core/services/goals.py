"""
财富目标 Service 层。

职责：管理 goals 表，对齐前端 FinancialGoal[] 契约。
goal_id 为前端用的字符串 id（如 "g1"），id 为物理主键。
"""

from core.database import get_connection
from core.services._common import OWNER_ID, _now


def list_goals(status: str | None = None) -> list:
    """列出当前用户全部目标：[{id, name, target, current, deadline, priority, status}, ...]。

    status 可选 'active' / 'completed' / 'paused' 用于筛选；不传则返回全部。
    """
    if status is None:
        sql = """
            SELECT goal_id, name, target, current, deadline, priority, status,
                   created_at
            FROM goals WHERE user_id = ? ORDER BY id
        """
        params = (OWNER_ID,)
    else:
        sql = """
            SELECT goal_id, name, target, current, deadline, priority, status,
                   created_at
            FROM goals WHERE user_id = ? AND status = ? ORDER BY id
        """
        params = (OWNER_ID, status)

    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": row["goal_id"],
            "name": row["name"],
            "target": row["target"],
            "current": row["current"],
            "deadline": row["deadline"] or "",
            "priority": row["priority"],
            "status": row["status"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def upsert_goal(goal: dict) -> dict:
    """UPSERT 单条目标：按 goal_id 查重，存在则更新。

    goal 必须包含 id（即 goal_id）、name、target、current、deadline、priority。
    """
    goal_id = goal.get("id") or ""
    name = goal.get("name") or ""
    target = goal.get("target") or 0
    current = goal.get("current") or 0
    deadline = goal.get("deadline") or None
    priority = goal.get("priority") or "medium"

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO goals (user_id, goal_id, name, target, current, deadline, priority, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, goal_id) DO UPDATE SET
                name = excluded.name,
                target = excluded.target,
                current = excluded.current,
                deadline = excluded.deadline,
                priority = excluded.priority,
                updated_at = excluded.updated_at
            """,
            (OWNER_ID, goal_id, name, target, current, deadline, priority, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "id": goal_id,
        "name": name,
        "target": target,
        "current": current,
        "deadline": deadline or "",
        "priority": priority,
    }


def delete_goal(goal_id: str) -> bool:
    """删除单条目标；返回是否实际删除了记录。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM goals WHERE user_id = ? AND goal_id = ?",
            (OWNER_ID, goal_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _set_goal_status(goal_id: str, status: str) -> dict | None:
    """内部辅助：更新目标状态；返回更新后的目标或 None。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            UPDATE goals SET status = ?, updated_at = ?
            WHERE user_id = ? AND goal_id = ?
            """,
            (status, _now(), OWNER_ID, goal_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
    finally:
        conn.close()
    # 回读
    goals = list_goals()
    for g in goals:
        if g["id"] == goal_id:
            return g
    return None


def complete_goal(goal_id: str) -> dict | None:
    """将目标标记为已完成；记录不存在返回 None。"""
    return _set_goal_status(goal_id, "completed")


def pause_goal(goal_id: str) -> dict | None:
    """将目标标记为已暂停；记录不存在返回 None。"""
    return _set_goal_status(goal_id, "paused")


def resume_goal(goal_id: str) -> dict | None:
    """将目标从暂停状态恢复为 active；记录不存在返回 None。"""
    return _set_goal_status(goal_id, "active")
