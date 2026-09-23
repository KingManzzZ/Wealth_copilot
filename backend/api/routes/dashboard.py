from fastapi import APIRouter

from core.services.profile import get_profile

router = APIRouter()

# 近 6 个月现金流历史（profile 里没有的时间序列数据，dashboard 自己存）
CASHFLOW = [
    {"month": "4月", "income": 18000, "expense": 9200, "surplus": 8800},
    {"month": "5月", "income": 18000, "expense": 10500, "surplus": 7500},
    {"month": "6月", "income": 18000, "expense": 8900, "surplus": 9100},
    {"month": "7月", "income": 18500, "expense": 9500, "surplus": 9000},
    {"month": "8月", "income": 18000, "expense": 8700, "surplus": 9300},
    {"month": "9月", "income": 18000, "expense": 9500, "surplus": 8500},
]

# 健康评分：规则化输出（将来可换成真实算法/LLM 评估）
HEALTH = {
    "overall": 0 ,
    "grade": "B",
    "dimensions": [
        {"name": "储蓄率", "score": 78, "advice": "储蓄率 47%，高于 30% 的健康线，保持。"},
        {"name": "应急充足度", "score": 65, "advice": "应急资金覆盖 4.4 个月支出，建议补足到 6 个月。"},
        {"name": "负债比", "score": 70, "advice": "负债/资产 86%，房贷为主，注意利率上行风险。"},
        {"name": "目标进度", "score": 75, "advice": "测试111"},
    ],
}

def _build_kpis(p: dict) -> dict:
    income = p["monthly_income"]
    expense = p["monthly_expense"]
    surplus = income - expense
    return {
        "monthly_income": income,
        "monthly_expense": expense,
        "monthly_surplus": surplus,
        # 画像未填收入时储蓄率置 0，避免除零
        "saving_rate": round(surplus / income, 3) if income else 0,
    }

def _build_asset_liability(p: dict) -> list:
    items = [{"name": n, "value": v, "kind": "asset"} for n, v in p["assets"].items()]
    items += [{"name": n, "value": d["remaining"], "kind": "liability"} for n, d in p["debts"].items()]
    return items

@router.get("/dashboard")
def dashboard():
    p = get_profile()
    return {
        "kpis": _build_kpis(p),
        "cashflow": CASHFLOW,
        "goals": p["goals"],
        "asset_liability": _build_asset_liability(p),
        "health": HEALTH,
    }
