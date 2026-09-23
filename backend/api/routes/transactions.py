"""
交易流水接口（路由层）。

- GET    /api/transactions?month=YYYY-MM：取某月交易明细（不传则全部）
- POST   /api/transactions：新增单条交易流水
- DELETE /api/transactions/{id}：删除单条交易
"""

from fastapi import APIRouter, Body, HTTPException

from core.services.transactions import (
    list_transactions,
    add_transaction,
    delete_transaction,
)

router = APIRouter()


@router.get("/transactions")
def read_transactions(month: str | None = None):
    """列出交易流水；month='YYYY-MM' 时按该月过滤。"""
    return list_transactions(month)


@router.post("/transactions")
def add_transaction_endpoint(data: dict = Body(...)):
    """新增单条交易流水。"""
    transaction_type = data.get("transaction_type")
    amount = data.get("amount")
    category = data.get("category")
    occurred_at = data.get("occurred_at")
    if not transaction_type or amount is None or not category or not occurred_at:
        raise HTTPException(
            status_code=400,
            detail="transaction_type、amount、category、occurred_at 必填",
        )
    if transaction_type not in ("income", "expense"):
        raise HTTPException(
            status_code=400,
            detail="transaction_type 必须为 'income' 或 'expense'",
        )
    return add_transaction(
        transaction_type=transaction_type,
        amount=float(amount),
        category=category,
        occurred_at=occurred_at,
        source=data.get("source"),
        note=data.get("note"),
    )


@router.delete("/transactions/{transaction_id}")
def delete_transaction_endpoint(transaction_id: int):
    """删除单条交易流水；不存在时返回 404。"""
    if not delete_transaction(transaction_id):
        raise HTTPException(
            status_code=404,
            detail=f"交易 {transaction_id} 不存在",
        )
    return {"ok": True}
