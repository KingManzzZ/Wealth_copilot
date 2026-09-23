"""
基本信息工具集。

- get_financial_profile：聚合 basic_info + assets + debts + goals，作为用户当前财务画像
- update_basic_info：更新基本信息字段（按 v0.4 拆表后的语义，原 update_financial_profile 改名）
"""

from core.services.basic_info import get_basic_info, upsert_basic_info
from core.services.assets import list_assets
from core.services.debts import list_debts
from core.services.goals import list_goals


def get_financial_profile() -> dict:
    """获取当前用户财务画像：基本信息 + 资产 + 债务 + 目标聚合视图。

    返回示例：
      {
        "basic_info": {...},
        "assets": {"现金": 80000, "基金": 30000},
        "debts": {"房贷": {"remaining": 800000, "monthly_payment": 6500}},
        "goals": [{"id": "g1", "name": "买房", "target": 500000, "current": 130000, ...}],
      }
    """
    return {
        "basic_info": get_basic_info(),
        "assets": list_assets(),
        "debts": list_debts(),
        "goals": list_goals(),
    }


def update_basic_info(**fields) -> dict:
    """更新基本信息字段（仅 basic_info 表的用户可编辑字段；assets/debts/goals 走各自工具）。

    可接受字段：name / age / city / occupation / industry / education / family /
    marital_status / children / supporting_parents / investing_years。
    其他字段会被白名单过滤掉。
    risk_tolerance / investment_preference / liquidity_needs 为算法估算字段，
    不可通过此工具修改。
    """
    return upsert_basic_info(fields)
