"""
月度现金流（**派生视图**）Service 层。

## 结余不是字段

``surplus = income - expense``，由本模块在查询时现算。库里既没有 ``surplus`` 列，
也没有 ``monthly_cashflow`` 汇总表（v0.5 起删除）。

## 唯一数据源是 transactions

记账只能逐笔写 ``transactions``；月度收入 / 支出 / 结余全部由它按月聚合得出。
这样"Agent 记了一笔支出"与"仪表盘月结余"在结构上不可能再不一致。

旧设计的病根是**同一事实存两份且无同步**：``record_expense`` 只写流水，
而 dashboard KPI 读 ``monthly_cashflow`` 汇总表 → 用户记了账，月结余纹丝不动，
会认为"Agent 没记住"（BK-27 / G-11）。改成派生后该问题从结构上消失，
也顺带不再需要一个"主要用于测试"的 ``set_monthly_cashflow`` 写入工具。

## 月份的来源

月份取自流水的 ``occurred_at``（``'YYYY-MM-DDTHH:MM:SS'``）前 7 位。
``strftime('%Y-%m', occurred_at)`` 对 ISO 8601 字符串有效，且能吃到
``ix_transactions_user_occurred`` 之外的分组代价（数据量级为个人账本，可忽略）。
"""

from core.database import get_connection
from core.services._common import OWNER_ID

#: 月份表达式（与 transactions.occurred_at 的 ISO 8601 格式对齐）
_MONTH_EXPR = "strftime('%Y-%m', occurred_at)"

#: 按月聚合的基础查询：收入、支出分别求和；无该类型流水时记 0
_AGG_SQL = f"""
    SELECT {_MONTH_EXPR} AS month,
           SUM(CASE WHEN transaction_type = 'income'  THEN amount ELSE 0 END) AS income,
           SUM(CASE WHEN transaction_type = 'expense' THEN amount ELSE 0 END) AS expense
    FROM transactions
    WHERE user_id = ?
"""


def _to_row(row) -> dict:
    """聚合行 → 对外 dict；``surplus`` 永远现算，不来自存储。"""
    income = row["income"] or 0
    expense = row["expense"] or 0
    return {
        "month": row["month"],
        "income": income,
        "expense": expense,
        "surplus": income - expense,
    }


def list_cashflow(months: int = 6) -> list:
    """取最近 N 个**有流水**的月份汇总，按月份升序。

    返回 ``[{month, income, expense, surplus}, ...]``；没有流水的月份不会出现
    （不做零填充：零填充会让"没有数据"和"收入支出都为 0"看起来一样）。
    """
    months = max(1, int(months))
    conn = get_connection()
    try:
        rows = conn.execute(
            _AGG_SQL + " GROUP BY month ORDER BY month DESC LIMIT ?",
            (OWNER_ID, months),
        ).fetchall()
    finally:
        conn.close()
    # 反转为升序（最近月在末），便于前端折线图横轴从左到右递增
    return [_to_row(row) for row in reversed(rows)]


def get_latest_month() -> dict | None:
    """取最近一个有流水的月份汇总；无任何流水返回 None。

    dashboard KPI（monthly_income / monthly_expense / monthly_surplus / saving_rate）
    与应急资金测算都从这里取"最近一个月"。
    """
    conn = get_connection()
    try:
        row = conn.execute(
            _AGG_SQL + " GROUP BY month ORDER BY month DESC LIMIT 1",
            (OWNER_ID,),
        ).fetchone()
    finally:
        conn.close()
    return _to_row(row) if row is not None else None


def get_month_cashflow(month: str) -> dict | None:
    """取指定月份（``'YYYY-MM'``）汇总；该月无流水返回 None。

    ``calculate_savings_rate`` 用它替代原先"拉 36 个月再线性查找"的做法——
    后者在流水跨度超过 36 个月时会静默给不出结果。
    """
    if not month:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            _AGG_SQL + f" AND {_MONTH_EXPR} = ? GROUP BY month",
            (OWNER_ID, month),
        ).fetchone()
    finally:
        conn.close()
    return _to_row(row) if row is not None else None
