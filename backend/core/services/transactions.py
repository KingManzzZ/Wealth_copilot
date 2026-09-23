"""
交易流水 Service 层。

职责：管理 transactions 表，记录单条收支流水。
单用户模式下 user_id 固定为 OWNER_ID = 1。
"""

from core.database import get_connection
from core.services._common import OWNER_ID, _now


def list_transactions(month: str | None = None) -> list:
    """列出交易流水；month='YYYY-MM' 时按该月过滤，否则返回全部。

    返回 [{id, transaction_type, amount, category, source, occurred_at, note}, ...]
    按时间倒序（最新在前）。
    """
    if month is None:
        sql = """
            SELECT id, transaction_type, amount, category, source, occurred_at, note
            FROM transactions WHERE user_id = ?
            ORDER BY occurred_at DESC
        """
        params = (OWNER_ID,)
    else:
        # month 为 'YYYY-MM'，匹配 occurred_at 前缀
        sql = """
            SELECT id, transaction_type, amount, category, source, occurred_at, note
            FROM transactions
            WHERE user_id = ? AND strftime('%Y-%m', occurred_at) = ?
            ORDER BY occurred_at DESC
        """
        params = (OWNER_ID, month)

    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": row["id"],
            "transaction_type": row["transaction_type"],
            "amount": row["amount"],
            "category": row["category"],
            "source": row["source"] or "",
            "occurred_at": row["occurred_at"],
            "note": row["note"] or "",
        }
        for row in rows
    ]


def add_transaction(
    transaction_type: str,
    amount: float,
    category: str,
    occurred_at: str,
    source: str | None = None,
    note: str | None = None,
) -> dict:
    """新增单条交易流水。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO transactions
                (user_id, transaction_type, amount, category, source, occurred_at, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (OWNER_ID, transaction_type, amount, category, source, occurred_at, note),
        )
        conn.commit()
        new_id = cur.lastrowid
    finally:
        conn.close()
    return {
        "id": new_id,
        "transaction_type": transaction_type,
        "amount": amount,
        "category": category,
        "source": source or "",
        "occurred_at": occurred_at,
        "note": note or "",
    }


def delete_transaction(transaction_id: int) -> bool:
    """删除单条交易流水；返回是否实际删除。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM transactions WHERE user_id = ? AND id = ?",
            (OWNER_ID, transaction_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_transaction(transaction_id: int, **fields) -> dict | None:
    """更新单条交易流水；只允许更新 amount/category/source/occurred_at/note/transaction_type。

    返回更新后的流水 dict；记录不存在返回 None。
    """
    allowed = {
        "transaction_type", "amount", "category",
        "source", "occurred_at", "note",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        # 没有可更新字段，直接返回当前记录
        return _get_transaction(transaction_id)

    set_clause = ", ".join(f"{c} = ?" for c in updates)
    values = list(updates.values()) + [_now()]

    conn = get_connection()
    try:
        cur = conn.execute(
            f"""
            UPDATE transactions
            SET {set_clause}, updated_at = ?
            WHERE user_id = ? AND id = ?
            """,
            values + [OWNER_ID, transaction_id],
        )
        conn.commit()
        if cur.rowcount == 0:
            return None
        return _get_transaction(transaction_id)
    finally:
        conn.close()


def _get_transaction(transaction_id: int) -> dict | None:
    """读取单条交易流水（内部辅助）。"""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT id, transaction_type, amount, category, source, occurred_at, note
            FROM transactions WHERE user_id = ? AND id = ?
            """,
            (OWNER_ID, transaction_id),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "id": row["id"],
        "transaction_type": row["transaction_type"],
        "amount": row["amount"],
        "category": row["category"],
        "source": row["source"] or "",
        "occurred_at": row["occurred_at"],
        "note": row["note"] or "",
    }
