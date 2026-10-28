"""
资产工具集。

- get_assets：列出全部资产
- record_asset：新增资产（UPSERT 语义，同名则更新）
- update_asset：更新资产估值（与 record_asset 等价，语义上区分新增/修改）
- calculate_net_worth：净资产 = 资产总额 - 负债总额
- calculate_emergency_fund：应急资金目标、当前覆盖月数、缺口
"""

from core.services.assets import list_assets, upsert_asset
from core.services.debts import list_debts
from core.services.basic_info import get_basic_info

#: 显式声明对外工具（`tools_route` 的自动发现会以此为准，见其模块文档）
__all__ = [
    "get_assets",
    "record_asset",
    "update_asset",
    "calculate_net_worth",
    "calculate_emergency_fund",
]


def get_assets() -> dict:
    """列出当前用户全部资产：{name: value} 字典。"""
    return list_assets()


def record_asset(name: str, value: float, asset_type: str = "") -> dict:
    """新增资产：同名资产已存在则更新估值（UPSERT）。

    Args:
        name: 资产名称（如"现金及活期"、"基金"、"股票"）
        value: 当前估值（元）
        asset_type: 可选，cash/investment/property/other；不传则按名称推断

    Returns:
        {"name": ..., "value": ..., "asset_type": ...}
    """
    return upsert_asset(name, value, asset_type=(asset_type or None))


def update_asset(name: str, value: float, asset_type: str = "") -> dict:
    """更新资产估值；语义上与 record_asset 等价（底层都是 UPSERT）。"""
    return upsert_asset(name, value, asset_type=(asset_type or None))


def calculate_net_worth() -> dict:
    """计算净资产 = 资产总额 - 负债总额（取剩余本金）。

    Returns:
        {"total_assets": ..., "total_debts": ..., "net_worth": ...}
    """
    assets = list_assets()
    debts = list_debts()
    total_assets = sum(assets.values())
    total_debts = sum(d["remaining"] for d in debts.values())
    return {
        "total_assets": total_assets,
        "total_debts": total_debts,
        "net_worth": total_assets - total_debts,
    }


def calculate_emergency_fund() -> dict:
    """计算应急资金目标与缺口。

    规则：
    - 目标月数由 basic_info.liquidity_needs 决定（low=3 / medium=6 / high=12，默认 6）
    - 必要月支出取最近月现金流 expense（cashflow 派生）；无现金流数据时返回提示
    - 现金资产 = assets 中名称含"现金"的资产之和（简化规则）

    Returns:
        {
            "liquidity_level": ...,
            "target_months": ...,
            "monthly_expense": ...,
            "target_amount": ...,
            "current_cash": ...,
            "shortfall": ...,
        }
    """
    LIQUIDITY_MONTHS = {"low": 3, "medium": 6, "high": 12}

    info = get_basic_info()
    level = info.get("liquidity_needs") or "medium"
    months = LIQUIDITY_MONTHS.get(level, 6)

    # 必要月支出：从最近月 cashflow 派生
    from core.services.cashflow import get_latest_month
    latest = get_latest_month()
    if latest is None:
        return {
            "liquidity_level": level,
            "target_months": months,
            "monthly_expense": 0,
            "target_amount": 0,
            "current_cash": 0,
            "shortfall": 0,
            "note": "无月度现金流数据，无法计算必要月支出",
        }
    expense = latest["expense"]
    target_amount = months * expense

    # 现金类资产：资产名包含"现金"或"活期"
    assets = list_assets()
    current_cash = sum(
        v for n, v in assets.items()
        if "现金" in n or "活期" in n
    )

    return {
        "liquidity_level": level,
        "target_months": months,
        "monthly_expense": expense,
        "target_amount": target_amount,
        "current_cash": current_cash,
        "shortfall": max(0, target_amount - current_cash),
    }
