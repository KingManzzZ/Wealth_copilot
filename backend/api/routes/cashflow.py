"""
月度现金流接口（路由层，**只读**）。

月度收入 / 支出 / 结余由 ``transactions`` 按月聚合派生（见
``core/services/cashflow.py``），因此这里没有写接口：
**录入口只有逐笔流水**（``POST /api/transactions`` 或 Agent 的
``record_income`` / ``record_expense``），不存在"手工填一个汇总"的路径。

> 旧版本曾有 ``POST /api/cashflow`` 与 ``set_monthly_cashflow`` 工具，
> 与流水并存导致"记了账但月结余不变"（BK-27 / G-11）。两类写入入口
> 已在 v0.5 一并下线，避免派生值与手工值互相覆盖。
"""

from fastapi import APIRouter, HTTPException

from core.services.cashflow import list_cashflow

router = APIRouter()


@router.get("/cashflow")
def read_cashflow(months: int = 6):
    """取最近 N 个有流水的月份汇总（默认 6，按月份升序）。"""
    if months < 1 or months > 36:
        raise HTTPException(status_code=400, detail="months 须在 1~36 范围")
    return list_cashflow(months)
