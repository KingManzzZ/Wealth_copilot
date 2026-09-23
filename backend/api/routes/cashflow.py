"""
月度现金流接口（路由层）。

- GET   /api/cashflow?months=6：取最近 N 个月的月度现金流汇总
- POST  /api/cashflow：UPSERT 某月汇总 body {month, income, expense}
"""

from fastapi import APIRouter, Body, HTTPException

from core.services.cashflow import list_cashflow, upsert_cashflow

router = APIRouter()


@router.get("/cashflow")
def read_cashflow(months: int = 6):
    """取最近 N 个月的月度现金流汇总（默认 6 个月，按月份升序）。"""
    if months < 1 or months > 36:
        raise HTTPException(status_code=400, detail="months 须在 1~36 范围")
    return list_cashflow(months)


@router.post("/cashflow")
def upsert_cashflow_endpoint(data: dict = Body(...)):
    """UPSERT 某月现金流汇总：month 格式 'YYYY-MM'。"""
    month = data.get("month")
    income = data.get("income")
    expense = data.get("expense")
    if not month or income is None or expense is None:
        raise HTTPException(
            status_code=400,
            detail="month、income、expense 必填",
        )
    return upsert_cashflow(month, float(income), float(expense))
