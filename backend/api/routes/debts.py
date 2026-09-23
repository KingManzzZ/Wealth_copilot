"""
债务接口（路由层）。

- GET    /api/debts：列出全部债务
- POST   /api/debts：UPSERT 单条债务 body {name, remaining, monthly_payment}
- DELETE /api/debts/{name}：删除单条债务
"""

from fastapi import APIRouter, Body, HTTPException

from core.services.debts import list_debts, upsert_debt, delete_debt

router = APIRouter()


@router.get("/debts")
def read_debts():
    """列出当前用户全部债务。"""
    return list_debts()


@router.post("/debts")
def upsert_debt_endpoint(data: dict = Body(...)):
    """UPSERT 单条债务：同名则更新。"""
    name = data.get("name")
    remaining = data.get("remaining")
    monthly_payment = data.get("monthly_payment")
    if not name or remaining is None or monthly_payment is None:
        raise HTTPException(
            status_code=400,
            detail="name、remaining、monthly_payment 必填",
        )
    return upsert_debt(name, float(remaining), float(monthly_payment))


@router.delete("/debts/{name}")
def delete_debt_endpoint(name: str):
    """删除单条债务；不存在时返回 404。"""
    if not delete_debt(name):
        raise HTTPException(status_code=404, detail=f"债务 '{name}' 不存在")
    return {"ok": True}
