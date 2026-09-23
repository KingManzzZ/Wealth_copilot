"""
债务 Service 层。

职责：管理 debts 表，对齐前端 Record<string, { remaining, monthly_payment }> 契约。
原 schema 的 liabilities 表已改名为 debts。
"""

from core.database import get_connection
from core.services._common import OWNER_ID, _now


def list_debts() -> dict:
    """列出当前用户全部债务：{name: {remaining, monthly_payment}} 字典。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name, remaining, monthly_payment FROM debts WHERE user_id = ? ORDER BY id",
            (OWNER_ID,),
        ).fetchall()
    finally:
        conn.close()
    return {
        row["name"]: {
            "remaining": row["remaining"],
            "monthly_payment": row["monthly_payment"],
        }
        for row in rows
    }


def upsert_debt(name: str, remaining: float, monthly_payment: float) -> dict:
    """UPSERT 单条债务：同名则更新 remaining 和 monthly_payment。"""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO debts (user_id, name, remaining, monthly_payment, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, name) DO UPDATE SET
                remaining = excluded.remaining,
                monthly_payment = excluded.monthly_payment,
                updated_at = excluded.updated_at
            """,
            (OWNER_ID, name, remaining, monthly_payment, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "name": name,
        "remaining": remaining,
        "monthly_payment": monthly_payment,
    }


def delete_debt(name: str) -> bool:
    """删除单条债务；返回是否实际删除了记录。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM debts WHERE user_id = ? AND name = ?",
            (OWNER_ID, name),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
