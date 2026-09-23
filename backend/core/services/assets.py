"""
资产 Service 层。

职责：管理 assets 表，对齐前端 Record<string, number> 契约。
单用户模式下 user_id 固定为 OWNER_ID = 1。
"""

from core.database import get_connection
from core.services._common import OWNER_ID, _now


def list_assets() -> dict:
    """列出当前用户全部资产：{name: value} 字典。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name, value FROM assets WHERE user_id = ? ORDER BY id",
            (OWNER_ID,),
        ).fetchall()
    finally:
        conn.close()
    return {row["name"]: row["value"] for row in rows}


def upsert_asset(name: str, value: float) -> dict:
    """UPSERT 单条资产：同名则更新 value。"""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO assets (user_id, name, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, name) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (OWNER_ID, name, value, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"name": name, "value": value}


def delete_asset(name: str) -> bool:
    """删除单条资产；返回是否实际删除了记录。"""
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM assets WHERE user_id = ? AND name = ?",
            (OWNER_ID, name),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
