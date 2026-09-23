"""
债务工具集。

- get_debts：列出全部负债
- record_debt：新增负债（UPSERT 语义）
- update_debt：更新负债余额或月还款（与 record_debt 等价）
"""

from core.services.debts import list_debts, upsert_debt


def get_debts() -> dict:
    """列出当前用户全部负债：{name: {remaining, monthly_payment}} 字典。"""
    return list_debts()


def record_debt(name: str, remaining: float, monthly_payment: float) -> dict:
    """新增负债：同名负债已存在则更新（UPSERT）。

    Args:
        name: 负债名称（如"房贷"、"车贷"、"信用卡"）
        remaining: 剩余本金（元）
        monthly_payment: 月还款额（元）

    Returns:
        {"name": ..., "remaining": ..., "monthly_payment": ...}
    """
    return upsert_debt(name, remaining, monthly_payment)


def update_debt(name: str, remaining: float, monthly_payment: float) -> dict:
    """更新负债余额或月还款；语义上与 record_debt 等价（底层都是 UPSERT）。"""
    return upsert_debt(name, remaining, monthly_payment)
