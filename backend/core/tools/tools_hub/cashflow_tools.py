"""
现金流工具集。

- get_monthly_cashflow：取最近月或指定月的现金流汇总
- calculate_savings_rate：计算指定期间储蓄率
"""

from core.services.cashflow import list_cashflow, get_latest_month, upsert_cashflow

#: 显式声明对外工具（`tools_route` 的自动发现会以此为准）
__all__ = ["get_monthly_cashflow", "calculate_savings_rate", "set_monthly_cashflow"]


def get_monthly_cashflow(months: int = 1) -> dict | list:
    """获取月度现金流汇总。

    Args:
        months: 1（默认）= 只取最近一个月，返回 dict；
                >1 = 取最近 N 个月，返回 list 按月份升序。

    Returns:
        months=1: {"month": "2026-09", "income": ..., "expense": ..., "surplus": ...}
                 无数据时返回 {"month": "", "income": 0, "expense": 0, "surplus": 0}
        months>1: [{"month": ..., "income": ..., ...}, ...]
    """
    if months == 1:
        latest = get_latest_month()
        if latest is None:
            return {"month": "", "income": 0, "expense": 0, "surplus": 0}
        return latest
    return list_cashflow(months=months)


def calculate_savings_rate(month: str | None = None) -> dict:
    """计算储蓄率 = (income - expense) / income。

    Args:
        month: 指定月份 'YYYY-MM'；不传则取最近一个月。

    Returns:
        {
            "month": ...,
            "income": ...,
            "expense": ...,
            "surplus": ...,
            "saving_rate": ...,  # 0~1 浮点，income=0 时为 0
        }
    """
    if month is None:
        latest = get_latest_month()
        if latest is None:
            return {
                "month": "",
                "income": 0,
                "expense": 0,
                "surplus": 0,
                "saving_rate": 0,
                "note": "无现金流数据",
            }
        income = latest["income"]
        expense = latest["expense"]
        surplus = income - expense
        return {
            "month": latest["month"],
            "income": income,
            "expense": expense,
            "surplus": surplus,
            "saving_rate": round(surplus / income, 3) if income else 0,
        }

    # 指定月份：从 list_cashflow 中找
    rows = list_cashflow(months=36)
    target = next((r for r in rows if r["month"] == month), None)
    if target is None:
        return {
            "month": month,
            "income": 0,
            "expense": 0,
            "surplus": 0,
            "saving_rate": 0,
            "note": f"{month} 无现金流记录",
        }
    income = target["income"]
    expense = target["expense"]
    surplus = income - expense
    return {
        "month": month,
        "income": income,
        "expense": expense,
        "surplus": surplus,
        "saving_rate": round(surplus / income, 3) if income else 0,
    }


# 保留 upsert_cashflow 的工具入口，方便 Agent 写入月度汇总（测试用）
def set_monthly_cashflow(month: str, income: float, expense: float) -> dict:
    """UPSERT 某月现金流汇总。"""
    return upsert_cashflow(month, income, expense)
