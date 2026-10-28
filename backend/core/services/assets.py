"""
资产 Service 层。

职责：管理 assets 表，对齐前端 ``Record<string, number>`` 契约。

v0.7 起新增 ``asset_type``（cash / investment / property / other）：
财务健康度需要区分"现金类、自住房、高风险资产"，只靠名称关键词太脆。
旧数据没有类型时按名称推断（``infer_asset_type``），不阻塞使用。
"""

from __future__ import annotations

from core.database import get_connection
from core.services._common import OWNER_ID, _now

#: 资产类型白名单
ASSET_TYPES = ("cash", "investment", "property", "other")

#: 名称 → 类型的关键词（顺序：先自住房，再现金，最后高风险）
_PROPERTY_KEYWORDS = ("房", "住宅", "商铺", "物业", "地产")
_CASH_KEYWORDS = ("现金", "活期", "货币", "余额宝", "零钱", "存款")
_RISKY_KEYWORDS = (
    "股票",
    "基金",
    "ETF",
    "指数",
    "偏股",
    "混合",
    "私募",
    "股权",
    "加密",
    "数字资产",
    "期货",
    "期权",
    "证券",
)


def infer_asset_type(name: str) -> str:
    """按名称推断资产类型（旧数据/未填类型时的兜底）。"""
    label = name or ""
    if any(keyword in label for keyword in _PROPERTY_KEYWORDS):
        return "property"
    if any(keyword in label for keyword in _CASH_KEYWORDS):
        return "cash"
    if any(keyword in label for keyword in _RISKY_KEYWORDS):
        return "investment"
    return "other"


def _effective_type(name: str, stored: str | None) -> str:
    """显式类型优先；``other``/空值回落为名称推断。"""
    if stored in ("cash", "investment", "property"):
        return stored
    return infer_asset_type(name)


def list_assets() -> dict:
    """列出当前用户全部资产：``{name: value}`` 字典（保持旧契约不变）。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name, value FROM assets WHERE user_id = ? ORDER BY id",
            (OWNER_ID,),
        ).fetchall()
    finally:
        conn.close()
    return {row["name"]: row["value"] for row in rows}


def list_asset_details() -> dict:
    """列出资产明细：``{name: {value, asset_type}}``，供健康度使用。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT name, value, asset_type FROM assets WHERE user_id = ? ORDER BY id",
            (OWNER_ID,),
        ).fetchall()
    finally:
        conn.close()
    return {
        row["name"]: {
            "value": row["value"],
            "asset_type": _effective_type(row["name"], row["asset_type"]),
        }
        for row in rows
    }


def upsert_asset(name: str, value: float, asset_type: str | None = None) -> dict:
    """UPSERT 单条资产：同名则更新 value（可选更新类型）。

    Args:
        name: 资产名称
        value: 当前估值（元）
        asset_type: ``cash`` / ``investment`` / ``property`` / ``other``；
            不传时保留已有类型（新记录按名称推断），避免"只改估值"把类型冲掉。
    """
    if asset_type is not None and asset_type not in ASSET_TYPES:
        raise ValueError(f"asset_type 只能是 {' / '.join(ASSET_TYPES)}")

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT asset_type FROM assets WHERE user_id = ? AND name = ?",
            (OWNER_ID, name),
        ).fetchone()

        if asset_type is None:
            effective = (
                _effective_type(name, existing["asset_type"])
                if existing is not None
                else infer_asset_type(name)
            )
            conn.execute(
                """
                INSERT INTO assets (user_id, name, value, asset_type, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, name) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (OWNER_ID, name, value, effective, _now()),
            )
        else:
            effective = asset_type
            conn.execute(
                """
                INSERT INTO assets (user_id, name, value, asset_type, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, name) DO UPDATE SET
                    value = excluded.value,
                    asset_type = excluded.asset_type,
                    updated_at = excluded.updated_at
                """,
                (OWNER_ID, name, value, effective, _now()),
            )
        conn.commit()
    finally:
        conn.close()
    return {"name": name, "value": value, "asset_type": effective}


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
