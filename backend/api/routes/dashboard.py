"""
Dashboard 接口（路由层）。

聚合各子 service 的数据返回前端仪表盘所需的复合结构。
KPI 与现金流图全部由 transactions 按月聚合派生（不再有 monthly_cashflow 汇总表），
health 由 ``core/services/wealth_health.py`` 实时计算（5 维评分，不落库）。
"""

from fastapi import APIRouter

from core.services.assets import list_assets
from core.services.cashflow import list_cashflow, get_latest_month
from core.services.debts import list_debts
from core.services.goals import list_goals
from core.services.wealth_health import get_health

router = APIRouter()


def _build_kpis(latest: dict | None) -> dict:
    """从最近月现金流派生 KPI；无数据时全置 0。"""
    income = latest["income"] if latest else 0
    expense = latest["expense"] if latest else 0
    surplus = income - expense
    return {
        "monthly_income": income,
        "monthly_expense": expense,
        "monthly_surplus": surplus,
        # 画像未填收入时储蓄率置 0，避免除零
        "saving_rate": round(surplus / income, 3) if income else 0,
    }


def _build_asset_liability(assets: dict, debts: dict) -> list:
    """将 assets 和 debts 合并为资产负债列表。"""
    items = [{"name": n, "value": v, "kind": "asset"} for n, v in assets.items()]
    items += [
        {"name": n, "value": d["remaining"], "kind": "liability"}
        for n, d in debts.items()
    ]
    return items


@router.get("/dashboard")
def dashboard():
    """聚合各 service 数据返回仪表盘所需结构。"""
    latest = get_latest_month()
    return {
        "kpis": _build_kpis(latest),
        "cashflow": list_cashflow(months=6),
        "goals": list_goals(),
        "asset_liability": _build_asset_liability(list_assets(), list_debts()),
        "health": get_health(),
    }
