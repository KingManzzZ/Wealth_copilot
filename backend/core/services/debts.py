"""
债务 Service 层。

职责：管理 debts 表，对齐前端 ``Record<string, { remaining, monthly_payment }>`` 契约。

v0.7 起新增 ``annual_rate``（年利率，小数：0.05 = 5%）：
健康度用它识别"高息负债"，优先级高于普通房贷。
"""

from __future__ import annotations

from core.database import get_connection
from core.services._common import OWNER_ID, _now


def list_debts() -> dict:
    """列出当前用户全部债务：``{name: {remaining, monthly_payment, annual_rate}}``。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT name, remaining, monthly_payment, annual_rate
            FROM debts WHERE user_id = ? ORDER BY id
            """,
            (OWNER_ID,),
        ).fetchall()
    finally:
        conn.close()
    return {
        row["name"]: {
            "remaining": row["remaining"],
            "monthly_payment": row["monthly_payment"],
            "annual_rate": row["annual_rate"],
        }
        for row in rows
    }


def upsert_debt(
    name: str,
    remaining: float,
    monthly_payment: float,
    annual_rate: float | None = None,
) -> dict:
    """UPSERT 单条债务：同名则更新 remaining / monthly_payment（可选更新利率）。

    Args:
        annual_rate: 年利率，小数（``0.05`` = 5%）；不传时保留已有值。
    """
    if annual_rate is not None and not (0 <= float(annual_rate) <= 1):
        raise ValueError("annual_rate 必须是 0~1 的小数（如 0.05 = 5%）")

    conn = get_connection()
    try:
        if annual_rate is None:
            conn.execute(
                """
                INSERT INTO debts
                    (user_id, name, remaining, monthly_payment, annual_rate, updated_at)
                VALUES (?, ?, ?, ?, NULL, ?)
                ON CONFLICT(user_id, name) DO UPDATE SET
                    remaining = excluded.remaining,
                    monthly_payment = excluded.monthly_payment,
                    updated_at = excluded.updated_at
                """,
                (OWNER_ID, name, remaining, monthly_payment, _now()),
            )
        else:
            conn.execute(
                """
                INSERT INTO debts
                    (user_id, name, remaining, monthly_payment, annual_rate, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, name) DO UPDATE SET
                    remaining = excluded.remaining,
                    monthly_payment = excluded.monthly_payment,
                    annual_rate = excluded.annual_rate,
                    updated_at = excluded.updated_at
                """,
                (OWNER_ID, name, remaining, monthly_payment, float(annual_rate), _now()),
            )
        conn.commit()

        row = conn.execute(
            """
            SELECT remaining, monthly_payment, annual_rate
            FROM debts WHERE user_id = ? AND name = ?
            """,
            (OWNER_ID, name),
        ).fetchone()
    finally:
        conn.close()

    return {
        "name": name,
        "remaining": row["remaining"] if row else remaining,
        "monthly_payment": row["monthly_payment"] if row else monthly_payment,
        "annual_rate": row["annual_rate"] if row else annual_rate,
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
