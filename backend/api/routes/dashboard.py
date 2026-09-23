"""
Dashboard 接口（路由层）。

聚合各子 service 的数据返回前端仪表盘所需的复合结构。
不再硬编码 CASHFLOW，改为读 monthly_cashflow 表；
KPI 不再从 profile 派生，改为从 cashflow 派生（取最近一个月）。
health 仍用静态（动态计算待后续迭代）。
"""

from fastapi import APIRouter

from core.services.assets import list_assets
from core.services.cashflow import list_cashflow, get_latest_month
from core.services.debts import list_debts
from core.services.goals import list_goals

router = APIRouter()

# 健康评分：规则化输出（将来可换成真实算法/LLM 评估）
HEALTH = {
    "overall": 0,
    "grade": "B",
    "dimensions": [
        {"name": "储蓄率", "score": 78, "advice": "储蓄率 47%，高于 30% 的健康线，保持。"},
        {"name": "应急充足度", "score": 65, "advice": "应急资金覆盖 4.4 个月支出，建议补足到 6 个月。"},
        {"name": "负债比", "score": 70, "advice": "负债/资产 86%，房贷为主，注意利率上行风险。"},
        {"name": "目标进度", "score": 75, "advice": "测试111"},
    ],
}


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
        "health": HEALTH,
    }
