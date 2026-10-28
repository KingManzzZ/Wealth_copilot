"""
财务健康度评分（L5 个维度）。

算法依据：``Investigation/财务健康度/01-指标与算法调研.md``。

## 五个维度

| 维度 | 权重 | 数据 |
| :--- | ---: | :--- |
| 储蓄率 | 25% | 近 3 个月 ``transactions`` 聚合 |
| 应急储备 | 20% | 现金类资产 / 月均支出 |
| 负债健康 | 25% | 月供收入比（DTI）+ 负债资产比 |
| 目标进度 | 15% | 目标缺口 vs 月结余能力 |
| 资产配置 | 15% | 高风险资产占比 vs 风险承受能力 |

## 两个现实约束（不新增 schema 的代价）

1. **``assets`` 没有"资产类型"字段**：这里按名称关键词分类
   （现金/活期/货币/存款 → 现金类；股票/基金/ETF… → 高风险；房/住宅 → 自住房）。
   关键词是启发式，后续应给 ``assets`` 加 ``asset_type`` 列。
2. **``goals`` 没有暴露 ``created_at``**：文档里的"按时间应达进度"无法算，
   MVP 改为"目标月缺口 vs 月结余能力"——同样能回答"这个目标现在可行吗"。

## 缺失数据

某个维度算不出来（无收入、无支出、无目标…）时**直接不返回该维度**，
总分按可用维度重新归一化；可用维度 < 2 时 ``overall=0``、``data_quality="insufficient"``。
刻意不用 0 填充：那会把"没数据"误判成"很差"。
"""

from __future__ import annotations

from datetime import date
from statistics import median

from core.services.assets import list_assets
from core.services.basic_info import get_basic_info
from core.services.cashflow import list_cashflow
from core.services.debts import list_debts
from core.services.goals import list_goals

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

WEIGHTS = {
    "saving": 0.25,
    "emergency": 0.20,
    "debt": 0.25,
    "goal": 0.15,
    "allocation": 0.15,
}

GRADE_THRESHOLDS = ((85, "A"), (70, "B"), (55, "C"), (0, "D"))

#: 风险承受能力 → 目标高风险资产占比
RISK_TARGETS = {
    "conservative": 0.20,
    "balanced": 0.40,
    "aggressive": 0.60,
}

#: 资产名称关键词（顺序很重要：先自住房，再现金，最后高风险）
_PROPERTY_KEYWORDS = ("房", "住宅", "商铺", "物业", "地产")
_CASH_KEYWORDS = ("现金", "活期", "货币", "余额宝", "零钱", "存款")
_RISKY_KEYWORDS = (
    "股票",
    "基金",
    "ETF",
    "指数",
    "偏股",
    "混合",
    "私募",
    "股权",
    "加密",
    "数字资产",
    "期货",
    "期权",
    "证券",
)

#: 低于该分数的维度触发短板惩罚
WEAK_DIMENSION = 40


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _num(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _linear(value: float, bad: float, good: float) -> float:
    """把 ``value`` 从 [bad, good] 线性映射到 0~100。"""
    if good == bad:
        return 0.0
    return 100.0 * _clamp01((value - bad) / (good - bad))


def _classify_asset(name: str) -> str:
    """按名称把资产分为 property / cash / risky / other。

    没有 ``asset_type`` 字段时的启发式；误判成本由"保守口径"兜底：
    现金类只认明确关键词，高风险只认明确关键词，其余归 other。
    """
    label = name or ""
    if any(keyword in label for keyword in _PROPERTY_KEYWORDS):
        return "property"
    if any(keyword in label for keyword in _CASH_KEYWORDS):
        return "cash"
    if any(keyword in label for keyword in _RISKY_KEYWORDS):
        return "risky"
    return "other"


def _median_positive(values) -> float | None:
    positives = [_num(v) for v in values if _num(v) > 0]
    return median(positives) if positives else None


def _parse_deadline(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _percent(value: float, digits: int = 0) -> str:
    return f"{value * 100:.{digits}f}%"


def _money(value: float) -> str:
    if abs(value) >= 10000:
        return f"{value / 10000:.1f} 万"
    return f"{value:,.0f} 元"


def _grade(overall: int) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if overall >= threshold:
            return grade
    return "D"


# ---------------------------------------------------------------------------
# 各维度
# ---------------------------------------------------------------------------


def _saving_dimension(rows) -> dict | None:
    rates = [
        (_num(row["income"]) - _num(row["expense"])) / _num(row["income"])
        for row in rows
        if _num(row["income"]) > 0
    ]
    if not rates:
        return None

    rate = sum(rates) / len(rates)
    score = int(round(_linear(rate, 0.05, 0.30)))
    if score >= 80:
        advice = f"储蓄率 {_percent(rate, 1)}，高于 20% 的健康线，保持。"
    elif score >= 50:
        advice = f"储蓄率 {_percent(rate, 1)}，接近 20% 目标，可再压一点可选支出。"
    else:
        advice = f"储蓄率 {_percent(rate, 1)}，偏低；建议每月先存收入的 20% 再消费。"
    return {"key": "saving", "name": "储蓄率", "score": score, "advice": advice}


def _emergency_dimension(assets: dict, rows) -> dict | None:
    monthly_expense = _median_positive([row["expense"] for row in rows])
    if monthly_expense is None:
        return None

    cash = sum(
        _num(value)
        for name, value in assets.items()
        if _classify_asset(name) == "cash"
    )
    months = cash / monthly_expense
    score = int(round(_linear(months, 0.0, 6.0)))

    if score >= 80:
        advice = f"应急资金可覆盖 {months:.1f} 个月支出，达到 6 个月目标。"
    elif score >= 50:
        advice = f"应急资金可覆盖 {months:.1f} 个月支出，建议补足到 6 个月。"
    else:
        advice = (
            f"应急资金仅覆盖 {months:.1f} 个月支出；"
            f"先补到 6 个月（约 {_money(max(0, monthly_expense * 6 - cash))}）。"
        )
    return {
        "key": "emergency",
        "name": "应急储备",
        "score": score,
        "advice": advice,
    }


def _debt_dimension(assets: dict, debts: dict, rows) -> dict | None:
    total_assets = sum(_num(value) for value in assets.values())
    total_debt = sum(_num(item.get("remaining")) for item in debts.values())
    monthly_payment = sum(
        _num(item.get("monthly_payment")) for item in debts.values()
    )
    monthly_income = _median_positive([row["income"] for row in rows])

    parts: list[tuple[float, float]] = []
    dti = None
    debt_ratio = None

    if monthly_income and monthly_income > 0:
        dti = monthly_payment / monthly_income
        parts.append((0.6, _linear(0.50 - dti, 0.0, 0.35)))
    if total_assets > 0:
        debt_ratio = total_debt / total_assets
        parts.append((0.4, _linear(0.70 - debt_ratio, 0.0, 0.50)))

    if not parts:
        return None

    score = int(
        round(sum(weight * value for weight, value in parts) / sum(w for w, _ in parts))
    )

    details = []
    if dti is not None:
        details.append(f"月供占收入 {_percent(dti, 1)}")
    if debt_ratio is not None:
        details.append(f"负债/资产 {_percent(debt_ratio, 1)}")
    detail_text = "，".join(details)

    if score >= 80:
        advice = f"{detail_text}，负债压力可控。"
    elif score >= 50:
        advice = f"{detail_text}，接近警戒线，优先偿还高息负债。"
    else:
        advice = f"{detail_text}，偏高；建议先降月供或增加收入。"
    return {"key": "debt", "name": "负债健康", "score": score, "advice": advice}


def _goal_dimension(goals: list, rows) -> dict | None:
    """目标可行性：月结余能力 / 目标所需月储蓄。

    文档里的"按时间应达进度"依赖 ``goals.created_at``，而 service 未暴露它；
    这里改用"缺口 vs 能力"，避免为了一个字段去改其他模块。
    """
    today = date.today()
    surpluses = [
        _num(row["income"]) - _num(row["expense"]) for row in rows
    ]
    capacity = max(0.0, median(surpluses)) if surpluses else 0.0

    weighted_score = 0.0
    total_weight = 0.0
    required_total = 0.0
    for goal in goals:
        if goal.get("status", "active") != "active":
            continue
        target = _num(goal.get("target"))
        current = _num(goal.get("current"))
        if target <= 0:
            continue

        deadline = _parse_deadline(goal.get("deadline"))
        if current >= target:
            score = 100.0
            required = 0.0
        elif deadline is None:
            continue  # 没有期限，无法判断可行性
        elif deadline < today:
            score = 20.0
            required = target - current
        else:
            months_left = max(1.0, (deadline - today).days / 30.44)
            required = max(0.0, (target - current) / months_left)
            coverage = capacity / required if required > 0 else 1.0
            score = _linear(coverage, 0.0, 1.0)

        weighted_score += score * target
        total_weight += target
        required_total += required

    if total_weight <= 0:
        return None

    score = int(round(weighted_score / total_weight))
    if score >= 80:
        advice = (
            f"月结余 {_money(capacity)}，可覆盖目标所需 {_money(required_total)}/月，在轨。"
        )
    elif score >= 50:
        advice = (
            f"月结余 {_money(capacity)}，目标需 {_money(required_total)}/月，"
            "略有缺口，可延长期限或提高储蓄。"
        )
    else:
        advice = (
            f"月结余 {_money(capacity)}，目标需 {_money(required_total)}/月，"
            "缺口较大；建议下调目标或延长截止时间。"
        )
    return {"key": "goal", "name": "目标进度", "score": score, "advice": advice}


def _allocation_dimension(assets: dict, info: dict) -> dict | None:
    total = sum(_num(value) for value in assets.values())
    property_value = sum(
        _num(value)
        for name, value in assets.items()
        if _classify_asset(name) == "property"
    )
    investable = total - property_value
    if investable <= 0:
        return None

    risk_tolerance = (info.get("risk_tolerance") or "").strip().lower()
    target = RISK_TARGETS.get(risk_tolerance)
    if target is None:
        return None

    risky = sum(
        _num(value)
        for name, value in assets.items()
        if _classify_asset(name) == "risky"
    )
    ratio = risky / investable
    score = int(round(100.0 * _clamp01(1 - abs(ratio - target) / 0.5)))

    label = {"conservative": "保守型", "balanced": "稳健型", "aggressive": "积极型"}[
        risk_tolerance
    ]
    if score >= 75:
        advice = f"高风险资产 {_percent(ratio)}，与{label}目标 {_percent(target)} 基本匹配。"
    else:
        direction = "偏高" if ratio > target else "偏低"
        advice = (
            f"高风险资产 {_percent(ratio)}，相对{label}目标 {_percent(target)} {direction}，"
            "可做再平衡。"
        )
    return {
        "key": "allocation",
        "name": "资产配置",
        "score": score,
        "advice": advice,
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def get_health() -> dict:
    """计算当前用户的财务健康度（供 ``/api/dashboard`` 使用）。

    返回契约与前端 ``FinancialHealth`` 一致：
    ``{overall, grade, dimensions: [{name, score, advice}]}``；
    额外带 ``data_quality``（full / partial / insufficient），前端可忽略。
    """
    assets = list_assets()
    debts = list_debts()
    goals = list_goals(status="active")
    rows = list_cashflow(months=3)
    info = get_basic_info()

    dimensions = [
        dimension
        for dimension in (
            _saving_dimension(rows),
            _emergency_dimension(assets, rows),
            _debt_dimension(assets, debts, rows),
            _goal_dimension(goals, rows),
            _allocation_dimension(assets, info),
        )
        if dimension is not None
    ]

    if not dimensions:
        return {
            "overall": 0,
            "grade": "D",
            "dimensions": [],
            "data_quality": "insufficient",
        }

    total_weight = sum(WEIGHTS[item["key"]] for item in dimensions)
    weighted = (
        sum(item["score"] * WEIGHTS[item["key"]] for item in dimensions)
        / total_weight
    )
    weak_count = sum(1 for item in dimensions if item["score"] < WEAK_DIMENSION)
    penalty = min(30, weak_count * 10)
    overall = int(round(max(0.0, min(100.0, weighted - penalty))))

    if len(dimensions) >= 5:
        data_quality = "full"
    elif len(dimensions) >= 2:
        data_quality = "partial"
    else:
        data_quality = "insufficient"

    return {
        "overall": overall,
        "grade": _grade(overall),
        "dimensions": dimensions,
        "data_quality": data_quality,
    }
