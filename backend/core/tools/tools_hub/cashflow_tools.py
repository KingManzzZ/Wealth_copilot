"""
现金流工具集（**只读**）。

- get_monthly_cashflow：取最近月或最近 N 月的现金流汇总
- calculate_savings_rate：计算指定期间储蓄率

月度收入 / 支出 / 结余全部由 ``transactions`` 按月聚合派生（见
``core/services/cashflow.py``）。**这里没有写工具**：写入口只有逐笔流水
（``record_income`` / ``record_expense``）。原本的 ``set_monthly_cashflow``
（schema 自述"主要用于测试或数据补录"）已下线——它不是业务能力，
而且会和流水形成第二份"真相"（BK-27 / G-11）。
"""

from core.services.cashflow import get_latest_month, get_month_cashflow, list_cashflow

#: 显式声明对外工具（`tools_route` 的自动发现会以此为准）
__all__ = ["get_monthly_cashflow", "calculate_savings_rate"]


def get_monthly_cashflow(months: int = 1) -> dict | list:
    """获取月度现金流汇总（由流水按月聚合）。

    Args:
        months: 1（默认）= 只取最近一个有流水的月份，返回 dict；
                >1 = 取最近 N 个有流水的月份，返回 list 按月份升序。

    Returns:
        months=1: {"month": "2026-10", "income": ..., "expense": ..., "surplus": ...}
                  无流水时返回 {"month": "", "income": 0, "expense": 0, "surplus": 0}
        months>1: [{"month": ..., "income": ..., ...}, ...]

    注意 ``surplus`` 是现算的派生值（``income - expense``），不是存储字段；
    没有流水的月份不会出现在结果里。
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
        month: 指定月份 'YYYY-MM'；不传则取最近一个有流水的月份。

    Returns:
        {
            "month": ...,
            "income": ...,
            "expense": ...,
            "surplus": ...,      # 派生值
            "saving_rate": ...,  # 0~1 浮点，income=0 时为 0
        }
    """
    row = get_month_cashflow(month) if month else get_latest_month()
    if row is None:
        return {
            "month": month or "",
            "income": 0,
            "expense": 0,
            "surplus": 0,
            "saving_rate": 0,
            "note": f"{month} 无流水记录" if month else "无流水记录",
        }
    income = row["income"]
    surplus = row["surplus"]
    return {
        "month": row["month"],
        "income": income,
        "expense": row["expense"],
        "surplus": surplus,
        "saving_rate": round(surplus / income, 3) if income else 0,
    }
