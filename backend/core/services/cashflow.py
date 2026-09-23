"""
月度现金流 Service 层。

职责：管理 monthly_cashflow 表，替代 dashboard 原硬编码 CASHFLOW。
单用户模式下 user_id 固定为 OWNER_ID = 1。
monthly_income / monthly_expense 不再存为 basic_info 字段，由本 service 派生。
"""

from core.database import get_connection
from core.services._common import OWNER_ID, _now


def list_cashflow(months: int = 6) -> list:
    """取最近 N 个月的月度现金流汇总，按月份升序。

    返回 [{month, income, expense, surplus}, ...]，surplus 派生 = income - expense。
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT month, income, expense
            FROM monthly_cashflow
            WHERE user_id = ?
            ORDER BY month DESC
            LIMIT ?
            """,
            (OWNER_ID, months),
        ).fetchall()
    finally:
        conn.close()
    # 反转为升序（最近月在末），便于前端折线图横轴从左到右递增
    rows = list(reversed(rows))
    return [
        {
            "month": row["month"],
            "income": row["income"],
            "expense": row["expense"],
            "surplus": row["income"] - row["expense"],
        }
        for row in rows
    ]


def upsert_cashflow(month: str, income: float, expense: float) -> dict:
    """UPSERT 某月现金流汇总：month 格式 'YYYY-MM'。"""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO monthly_cashflow (user_id, month, income, expense, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, month) DO UPDATE SET
                income = excluded.income,
                expense = excluded.expense,
                updated_at = excluded.updated_at
            """,
            (OWNER_ID, month, income, expense, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "month": month,
        "income": income,
        "expense": expense,
        "surplus": income - expense,
    }


def get_latest_month() -> dict | None:
    """取最近一个月的现金流；无数据返回 None。

    dashboard KPI 派生用：monthly_income / monthly_expense 从此派生。
    """
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT month, income, expense
            FROM monthly_cashflow
            WHERE user_id = ?
            ORDER BY month DESC
            LIMIT 1
            """,
            (OWNER_ID,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "month": row["month"],
        "income": row["income"],
        "expense": row["expense"],
        "surplus": row["income"] - row["expense"],
    }


def delete_cashflow(month: str) -> bool:
    """删除某月现金流汇总；返回是否实际删除。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM monthly_cashflow WHERE user_id = ? AND month = ?",
            (OWNER_ID, month),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
