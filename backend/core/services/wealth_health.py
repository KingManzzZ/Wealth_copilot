"""
财务健康度评分（5 个维度）。

算法依据：``Investigation/财务健康度/01-指标与算法调研.md``。

## 五个维度

| 维度 | 权重 | 数据 |
| :--- | ---: | :--- |
| 储蓄率 | 25% | 近 3 个月 ``transactions`` 聚合 |
| 应急储备 | 20% | 现金类资产 / 月均支出 |
| 负债健康 | 25% | 月供收入比（DTI）+ 负债资产比 + 高息负债占比 |
| 目标进度 | 15% | 实际进度 / 按时间应达进度（无 created_at 时退回"缺口 vs 月结余"） |
| 资产配置 | 15% | 高风险资产占比 vs 风险承受能力 |

## 数据来源

- ``assets.asset_type``（cash / investment / property / other）——旧数据为 other 时按名称推断；
- ``debts.annual_rate``（小数）——识别高息负债，未填按"无高息"处理；
- ``goals.created_at`` + ``deadline``——计算"按时间应达进度"；
- 其余全部由 ``transactions`` 派生，不落库。

## 缺失数据

某个维度算不出来（无收入、无支出、无目标…）时**直接不返回该维度**，
总分按可用维度重新归一化；可用维度 < 2 时 ``overall=0``、``data_quality="insufficient"``。
刻意不用 0 填充：那会把"没数据"误判成"很差"。
"""

from __future__ import annotations

from datetime import date, datetime
from statistics import median

from core.services.assets import list_asset_details
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

#: 年利率 ≥ 8% 视为高息负债
HIGH_INTEREST_RATE = 0.08

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


def _median_positive(values) -> float | None:
    positives = [_num(v) for v in values if _num(v) > 0]
    return median(positives) if positives else None


def _parse_date(raw: str | None) -> date | None:
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


def _status(value: float, good: float, warn: float, higher_is_better: bool = True) -> str:
    """把原始值映射成 good/warn/bad（用于详情页的颜色）。"""
    if higher_is_better:
        if value >= good:
            return "good"
        return "warn" if value >= warn else "bad"
    if value <= good:
        return "good"
    return "warn" if value <= warn else "bad"


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
    return {
        "key": "saving",
        "name": "储蓄率",
        "score": score,
        "advice": advice,
        "metrics": [
            {
                "label": "近 3 个月平均储蓄率",
                "value": _percent(rate, 1),
                "target": "≥ 20%",
                "status": _status(rate, 0.20, 0.10),
            }
        ],
    }


def _emergency_dimension(assets: dict, rows) -> dict | None:
    monthly_expense = _median_positive([row["expense"] for row in rows])
    if monthly_expense is None:
        return None

    cash = sum(
        _num(item.get("value"))
        for item in assets.values()
        if item.get("asset_type") == "cash"
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
        "metrics": [
            {
                "label": "现金类资产可覆盖",
                "value": f"{months:.1f} 个月",
                "target": "≥ 6 个月",
                "status": _status(months, 6.0, 3.0),
                "note": f"现金类 {_money(cash)} ÷ 月支出 {_money(monthly_expense)}",
            }
        ],
    }


def _debt_dimension(assets: dict, debts: dict, rows) -> dict | None:
    total_assets = sum(_num(item.get("value")) for item in assets.values())
    total_debt = sum(_num(item.get("remaining")) for item in debts.values())
    monthly_payment = sum(
        _num(item.get("monthly_payment")) for item in debts.values()
    )
    monthly_income = _median_positive([row["income"] for row in rows])

    parts: list[tuple[float, float]] = []
    dti = None
    debt_ratio = None
    high_interest_ratio = 0.0

    if monthly_income and monthly_income > 0:
        dti = monthly_payment / monthly_income
        parts.append((0.6, _linear(0.50 - dti, 0.0, 0.35)))
    if total_assets > 0:
        debt_ratio = total_debt / total_assets
        parts.append((0.4, _linear(0.70 - debt_ratio, 0.0, 0.50)))

    # 有年利率数据时，把"高息负债占比"作为第三个子项（10% 以下满分，50% 以上 0 分）
    has_rate = any(item.get("annual_rate") is not None for item in debts.values())
    if total_debt > 0 and has_rate and parts:
        high_interest = sum(
            _num(item.get("remaining"))
            for item in debts.values()
            if item.get("annual_rate") is not None
            and _num(item.get("annual_rate")) >= HIGH_INTEREST_RATE
        )
        high_interest_ratio = high_interest / total_debt
        parts = [(0.5, score) for _, score in parts] + [
            (0.2, _linear(0.50 - high_interest_ratio, 0.0, 0.40))
        ]

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
    if high_interest_ratio > 0:
        details.append(f"高息负债占 {_percent(high_interest_ratio, 1)}")
    detail_text = "，".join(details)

    if score >= 80:
        advice = f"{detail_text}，负债压力可控。"
    elif score >= 50:
        advice = f"{detail_text}，接近警戒线，优先偿还高息负债。"
    else:
        advice = f"{detail_text}，偏高；建议先降月供或增加收入。"

    metrics = []
    if dti is not None:
        metrics.append(
            {
                "label": "月供 / 月收入（DTI）",
                "value": _percent(dti, 1),
                "target": "≤ 28%（≤ 36% 可接受）",
                "status": _status(dti, 0.28, 0.36, higher_is_better=False),
            }
        )
    if debt_ratio is not None:
        metrics.append(
            {
                "label": "总负债 / 总资产",
                "value": _percent(debt_ratio, 1),
                "target": "≤ 50%",
                "status": _status(debt_ratio, 0.50, 0.70, higher_is_better=False),
            }
        )
    if total_debt > 0 and has_rate:
        metrics.append(
            {
                "label": "高息负债（年利率 ≥ 8%）占比",
                "value": _percent(high_interest_ratio, 1),
                "target": "≤ 10%",
                "status": _status(
                    high_interest_ratio, 0.10, 0.30, higher_is_better=False
                ),
            }
        )
    return {
        "key": "debt",
        "name": "负债健康",
        "score": score,
        "advice": advice,
        "metrics": metrics,
    }


def _goal_dimension(goals: list, rows) -> dict | None:
    """目标进度。

    优先用文档的"按时间应达进度"（需要 ``created_at``）；
    老数据没有 created_at 时退回"月结余能力 / 目标所需月储蓄"。
    """
    today = date.today()
    surpluses = [_num(row["income"]) - _num(row["expense"]) for row in rows]
    capacity = max(0.0, median(surpluses)) if surpluses else 0.0

    weighted_score = 0.0
    total_weight = 0.0
    time_notes: list[tuple[str, float, float, float, float]] = []
    required_total = 0.0

    for goal in goals:
        if goal.get("status", "active") != "active":
            continue
        target = _num(goal.get("target"))
        current = _num(goal.get("current"))
        if target <= 0:
            continue

        deadline = _parse_date(goal.get("deadline"))
        created = _parse_date(goal.get("created_at"))
        actual = _clamp01(current / target)

        if deadline is not None and created is not None and deadline > created:
            elapsed = _clamp01((today - created).days / (deadline - created).days)
            gap = actual - elapsed
            score = 100.0 * _clamp01(0.5 + 2 * gap)
            if deadline < today and current < target:
                score = 20.0
            time_notes.append(
                (goal.get("name") or "目标", actual, elapsed, current, target)
            )
        elif deadline is not None and deadline < today and current < target:
            score = 20.0
            time_notes.append(
                (goal.get("name") or "目标", actual, 1.0, current, target)
            )
        else:
            # 没有 created_at：退回"缺口 vs 月结余"
            if deadline is None:
                continue
            months_left = max(1.0, (deadline - today).days / 30.44)
            required = max(0.0, (target - current) / months_left)
            required_total += required
            coverage = capacity / required if required > 0 else 1.0
            score = _linear(coverage, 0.0, 1.0)

        weighted_score += score * target
        total_weight += target

    if total_weight <= 0:
        return None

    score = int(round(weighted_score / total_weight))

    if time_notes:
        worst_name, worst_actual, worst_elapsed, worst_current, worst_target = min(
            time_notes, key=lambda item: item[1] - item[2]
        )
        gap = worst_actual - worst_elapsed
        if gap >= 0.05:
            advice = (
                f"{len(time_notes)} 个目标均在轨；最落后的是"
                f"「{worst_name}」（实际 {_percent(worst_actual)}，"
                f"应达 {_percent(worst_elapsed)}）。"
            )
        else:
            advice = (
                f"「{worst_name}」实际进度 {_percent(worst_actual)}，"
                f"按时间应达 {_percent(worst_elapsed)}，落后 {abs(gap) * 100:.0f} 个百分点；"
                "可提高月储蓄或延长期限。"
            )
        metrics = [
            {
                "label": f"「{worst_name}」实际进度",
                "value": _percent(worst_actual),
                "target": f"≥ {_percent(worst_elapsed)}（按时间应达）",
                "status": _status(gap, 0.0, -0.10),
                "note": f"已存 {_money(worst_current)} / 目标 {_money(worst_target)}",
            }
        ]
    elif score >= 80:
        advice = f"目标所需月储蓄可由月结余 {_money(capacity)} 覆盖，在轨。"
        metrics = [
            {
                "label": "月结余能力",
                "value": f"{_money(capacity)}/月",
                "target": f"需 {_money(required_total)}/月",
                "status": "good",
            }
        ]
    elif score >= 50:
        advice = "目标略有缺口，可延长期限或提高储蓄。"
        metrics = [
            {
                "label": "月结余能力",
                "value": f"{_money(capacity)}/月",
                "target": f"需 {_money(required_total)}/月",
                "status": "warn",
            }
        ]
    else:
        advice = "目标缺口较大；建议下调目标或延长截止时间。"
        metrics = [
            {
                "label": "月结余能力",
                "value": f"{_money(capacity)}/月",
                "target": f"需 {_money(required_total)}/月",
                "status": "bad",
            }
        ]

    return {
        "key": "goal",
        "name": "目标进度",
        "score": score,
        "advice": advice,
        "metrics": metrics,
    }


def _allocation_dimension(assets: dict, info: dict) -> dict | None:
    total = sum(_num(item.get("value")) for item in assets.values())
    property_value = sum(
        _num(item.get("value"))
        for item in assets.values()
        if item.get("asset_type") == "property"
    )
    investable = total - property_value
    if investable <= 0:
        return None

    risk_tolerance = (info.get("risk_tolerance") or "").strip().lower()
    target = RISK_TARGETS.get(risk_tolerance)
    if target is None:
        return None

    risky = sum(
        _num(item.get("value"))
        for item in assets.values()
        if item.get("asset_type") == "investment"
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
        "metrics": [
            {
                "label": "高风险资产 / 可投资资产",
                "value": _percent(ratio),
                "target": f"{label}目标 {_percent(target)}",
                "status": _status(score / 100, 0.75, 0.50),
                "note": f"高风险 {_money(risky)} / 可投资 {_money(investable)}",
            }
        ],
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def get_health(include_metrics: bool = False) -> dict:
    """计算当前用户的财务健康度（供 ``/api/dashboard`` 使用）。

    返回契约与前端 ``FinancialHealth`` 一致：
    ``{overall, grade, dimensions: [{name, score, advice}]}``；
    额外带 ``data_quality``（full / partial / insufficient），前端可忽略。

    ``include_metrics=True`` 时每个维度额外带 ``metrics``（原始值 / 目标 / 状态），
    供详情抽屉使用；``/api/dashboard`` 默认不返回，避免把卡片接口撑大。
    """
    assets = list_asset_details()
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

    if include_metrics:
        public_dimensions = dimensions
    else:
        public_dimensions = [
            {key: value for key, value in item.items() if key != "metrics"}
            for item in dimensions
        ]

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
        "dimensions": public_dimensions,
        "data_quality": data_quality,
    }


def get_health_detail() -> dict:
    """健康度详情：``get_health(include_metrics=True)`` + ``generated_at``。"""
    result = get_health(include_metrics=True)
    result["generated_at"] = datetime.now().isoformat(timespec="seconds")
    return result
