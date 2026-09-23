"""
交易流水工具集。

- record_income：记录一笔收入
- record_expense：记录一笔支出
- get_transactions：按时间/类型/类别/月份查询流水
- update_transaction：修改已有流水
- delete_transaction：删除流水
"""

from core.services.transactions import (
    list_transactions,
    add_transaction,
    update_transaction,
    delete_transaction,
)


def record_income(
    amount: float,
    category: str,
    occurred_at: str,
    source: str = "",
    note: str = "",
) -> dict:
    """记录一笔收入流水。

    Args:
        amount: 金额（元，必须 > 0）
        category: 收入分类（如"薪资"、"奖金"、"理财收益"）
        occurred_at: 发生时间，ISO 8601 格式（如 '2026-09-23T12:00:00'）
        source: 来源说明（可选，如"9月工资"）
        note: 备注（可选）

    Returns:
        新增的流水 dict
    """
    return add_transaction(
        transaction_type="income",
        amount=amount,
        category=category,
        occurred_at=occurred_at,
        source=source or None,
        note=note or None,
    )


def record_expense(
    amount: float,
    category: str,
    occurred_at: str,
    source: str = "",
    note: str = "",
) -> dict:
    """记录一笔支出流水。

    Args:
        amount: 金额（元，必须 > 0）
        category: 支出分类（如"餐饮"、"交通"、"房租"、"房贷"）
        occurred_at: 发生时间，ISO 8601 格式
        source: 用途说明（可选，如"午餐"）
        note: 备注（可选）

    Returns:
        新增的流水 dict
    """
    return add_transaction(
        transaction_type="expense",
        amount=amount,
        category=category,
        occurred_at=occurred_at,
        source=source or None,
        note=note or None,
    )


def get_transactions(month: str | None = None) -> list:
    """查询交易流水；month='YYYY-MM' 时按该月过滤。

    返回 [{id, transaction_type, amount, category, source, occurred_at, note}, ...]
    按时间倒序（最新在前）。
    """
    return list_transactions(month)


def update_transaction_tool(
    transaction_id: int,
    amount: float | None = None,
    category: str | None = None,
    source: str | None = None,
    occurred_at: str | None = None,
    note: str | None = None,
    transaction_type: str | None = None,
) -> dict | None:
    """修改已有流水；只传需要修改的字段即可。

    记录不存在返回 None。
    """
    fields = {}
    if amount is not None:
        fields["amount"] = amount
    if category is not None:
        fields["category"] = category
    if source is not None:
        fields["source"] = source
    if occurred_at is not None:
        fields["occurred_at"] = occurred_at
    if note is not None:
        fields["note"] = note
    if transaction_type is not None:
        fields["transaction_type"] = transaction_type
    return update_transaction(transaction_id, **fields)


def delete_transaction_tool(transaction_id: int) -> dict:
    """删除流水；返回 {ok: bool, deleted_id: int}。"""
    ok = delete_transaction(transaction_id)
    return {"ok": ok, "deleted_id": transaction_id}
