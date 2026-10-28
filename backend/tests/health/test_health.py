"""财务健康度 5 维算法：边界、权重、数据缺失、字段回退。"""

from __future__ import annotations

from datetime import date, timedelta

import _support
from run_tests import close, eq, in_, not_in, true

from core.services.assets import list_asset_details, upsert_asset
from core.services.debts import list_debts, upsert_debt
from core.services.goals import upsert_goal
from core.services.transactions import add_transaction
from core.services.wealth_health import _grade, get_health, get_health_detail


def _dims(result: dict) -> dict:
    return {item["name"]: item for item in result["dimensions"]}


def check_empty_database():
    with _support.temp_database():
        result = get_health()
        eq(result["overall"], 0)
        eq(result["dimensions"], [])
        eq(result["data_quality"], "insufficient")


def check_full_dataset_has_five_dimensions():
    with _support.temp_database():
        _support.set_risk_tolerance("balanced")
        upsert_asset("现金", 60000, "cash")
        upsert_asset("股票基金", 40000, "investment")
        upsert_asset("自住房", 800000, "property")
        upsert_debt("房贷", 500000, 5000, 0.045)
        _support.add_month(20000, 12000)
        _support.add_goal(
            "首付",
            300000,
            120000,
            date.today() + timedelta(days=365),
            created_at=date.today() - timedelta(days=180),
        )

        result = get_health()
        eq(len(result["dimensions"]), 5)
        eq(result["data_quality"], "full")
        for item in result["dimensions"]:
            true(0 <= item["score"] <= 100, f"{item['name']} 分数越界")
            true(bool(item["advice"]), f"{item['name']} 缺 advice")
        true(0 <= result["overall"] <= 100)
        in_(result["grade"], ("A", "B", "C", "D"))


def check_saving_rate_extremes():
    with _support.temp_database():
        _support.add_month(10000, 6000)  # 40%
        saving = _dims(get_health())["储蓄率"]
        eq(saving["score"], 100)

    with _support.temp_database():
        _support.add_month(10000, 10000)  # 0%
        saving = _dims(get_health())["储蓄率"]
        eq(saving["score"], 0)


def check_emergency_months():
    with _support.temp_database():
        _support.add_month(10000, 10000)
        upsert_asset("现金", 60000, "cash")  # 6 个月
        eq(_dims(get_health())["应急储备"]["score"], 100)

    with _support.temp_database():
        _support.add_month(10000, 10000)
        upsert_asset("现金", 0, "cash")
        eq(_dims(get_health())["应急储备"]["score"], 0)


def check_debt_dti_lowers_score():
    with _support.temp_database():
        _support.add_month(10000, 5000)
        upsert_asset("资产", 100000, "investment")
        upsert_debt("低月供", 20000, 1000, 0.04)
        low = _dims(get_health())["负债健康"]["score"]

    with _support.temp_database():
        _support.add_month(10000, 5000)
        upsert_asset("资产", 100000, "investment")
        upsert_debt("高月供", 60000, 5000, 0.04)
        high = _dims(get_health())["负债健康"]["score"]

    true(high < low, f"高 DTI 分数应更低（high={high}, low={low}）")


def check_high_interest_lowers_score():
    with _support.temp_database():
        _support.add_month(10000, 5000)
        upsert_asset("资产", 100000, "investment")
        upsert_debt("低息", 30000, 2000, 0.03)
        low = _dims(get_health())["负债健康"]["score"]

    with _support.temp_database():
        _support.add_month(10000, 5000)
        upsert_asset("资产", 100000, "investment")
        upsert_debt("高息", 30000, 2000, 0.24)
        high = _dims(get_health())["负债健康"]["score"]

    true(high < low, f"高息负债分数应更低（high={high}, low={low}）")


def check_goal_time_progress():
    with _support.temp_database():
        _support.add_goal(
            "首付",
            100000,
            50000,
            date.today() + timedelta(days=180),
            created_at=date.today() - timedelta(days=180),
        )
        score = _dims(get_health())["目标进度"]["score"]
        close(score, 50, 5, "时间过半、进度过半 → 约 50 分")

    with _support.temp_database():
        _support.add_goal(
            "首付",
            100000,
            90000,
            date.today() + timedelta(days=180),
            created_at=date.today() - timedelta(days=180),
        )
        true(_dims(get_health())["目标进度"]["score"] >= 90, "大幅超前应高分")

    with _support.temp_database():
        _support.add_goal(
            "过期目标",
            100000,
            50000,
            date.today() - timedelta(days=10),
            created_at=date.today() - timedelta(days=200),
        )
        eq(_dims(get_health())["目标进度"]["score"], 20)


def check_allocation_matches_tolerance():
    with _support.temp_database():
        _support.set_risk_tolerance("balanced")
        upsert_asset("现金", 60000, "cash")
        upsert_asset("股票", 40000, "investment")
        eq(_dims(get_health())["资产配置"]["score"], 100)

    with _support.temp_database():
        _support.set_risk_tolerance("aggressive")
        upsert_asset("现金", 60000, "cash")
        upsert_asset("股票", 40000, "investment")
        eq(_dims(get_health())["资产配置"]["score"], 60)


def check_missing_risk_tolerance_omits_allocation():
    with _support.temp_database():
        upsert_asset("现金", 10000, "cash")
        _support.add_month(10000, 5000)
        names = _dims(get_health())
        true("资产配置" not in names, "没有风险测评时不应给资产配置分")
        eq(get_health()["data_quality"], "partial")


def check_asset_type_inference_and_override():
    with _support.temp_database():
        upsert_asset("货币基金", 1000)
        upsert_asset("股票", 1000)
        upsert_asset("自住房", 1000)
        details = list_asset_details()
        eq(details["货币基金"]["asset_type"], "cash")
        eq(details["股票"]["asset_type"], "investment")
        eq(details["自住房"]["asset_type"], "property")

        upsert_asset("股票账户", 1000, "cash")
        eq(list_asset_details()["股票账户"]["asset_type"], "cash", "显式类型应覆盖推断")


def check_annual_rate_preserved_on_update():
    with _support.temp_database():
        upsert_debt("信用卡", 10000, 1000, 0.18)
        upsert_debt("信用卡", 9000, 900)  # 不传利率
        eq(list_debts()["信用卡"]["annual_rate"], 0.18, "不传利率时应保留原值")


def check_detail_has_metrics():
    """详情接口：每维带原始值/目标/状态；主接口不带 metrics。"""
    with _support.temp_database():
        _support.set_risk_tolerance("balanced")
        upsert_asset("现金", 60000, "cash")
        upsert_asset("股票", 40000, "investment")
        _support.add_month(20000, 12000)
        _support.add_goal(
            "首付",
            300000,
            120000,
            date.today() + timedelta(days=365),
            created_at=date.today() - timedelta(days=180),
        )

        detail = get_health_detail()
        true("generated_at" in detail)
        eq(len(detail["dimensions"]), 5)
        for item in detail["dimensions"]:
            true(bool(item.get("metrics")), f"{item['name']} 缺 metrics")
            for metric in item["metrics"]:
                for key in ("label", "value", "target", "status"):
                    in_(key, metric, f"{item['name']} metric 缺 {key}")
                in_(metric["status"], ("good", "warn", "bad"))

        plain = get_health()
        for item in plain["dimensions"]:
            not_in("metrics", item, "主接口不应带 metrics")


def check_grade_boundaries():
    eq(_grade(100), "A")
    eq(_grade(85), "A")
    eq(_grade(84), "B")
    eq(_grade(70), "B")
    eq(_grade(69), "C")
    eq(_grade(55), "C")
    eq(_grade(54), "D")
    eq(_grade(0), "D")
