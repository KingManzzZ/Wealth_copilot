"""
Service 层共用工具。

职责：所有子 service（basic_info / assets / debts / goals / cashflow / transactions）
共用的常量与辅助函数。从原 profile.py 抽取，避免重复实现。
"""

from datetime import datetime

# 单用户归属标识，未来扩展多用户时替换为真实 user_id
OWNER_ID: int = 1


def _now() -> str:
    """当前时间戳，ISO 8601 秒级，用于 last_updated / updated_at。"""
    return datetime.now().isoformat(timespec="seconds")


def _sanitize(data: dict, allowed_fields: tuple) -> dict:
    """只保留 allowed_fields 内的字段并剔除 None，防止非法字段入库。"""
    cleaned = {}
    for field in allowed_fields:
        value = data.get(field)
        if value is not None:
            cleaned[field] = value
    return cleaned


def _is_filled(value) -> bool:
    """判断字段是否有效（与前端 _is_filled 规则对齐）：
    - 字符串：去空白后非空
    - 数值：大于 0
    - 对象/数组：长度大于 0
    - None/缺失：未填写
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (dict, list)):
        return len(value) > 0
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return value > 0
    return True
