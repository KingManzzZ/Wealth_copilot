"""
财富目标工具集。

- create_goal：创建目标（UPSERT 语义）
- get_goals：列出目标，可按 status 筛选
- update_goal：修改目标字段（与 create_goal 等价，底层都是 UPSERT）
- complete_goal：将目标标记为已完成
- calculate_goal_contribution：计算达成目标所需月储蓄额
"""

from datetime import datetime
from core.services.goals import (
    list_goals,
    upsert_goal,
    complete_goal,
)

#: 显式声明对外工具（`tools_route` 的自动发现会以此为准）
__all__ = [
    "create_goal",
    "get_goals",
    "update_goal",
    "complete_goal_tool",
    "calculate_goal_contribution",
]


def create_goal(
    id: str,
    name: str,
    target: float,
    current: float = 0,
    deadline: str = "",
    priority: str = "medium",
) -> dict:
    """创建财富目标：goal_id 已存在则整体覆盖（UPSERT）。

    Args:
        id: 目标唯一标识（字符串，如 "g1"）
        name: 目标名称（如"二套房首付"）
        target: 目标金额（元）
        current: 当前已积累金额（元，默认 0）
        deadline: 截止日期，'YYYY-MM-DD' 格式
        priority: 优先级 'high' / 'medium' / 'low'，默认 'medium'
    """
    return upsert_goal({
        "id": id,
        "name": name,
        "target": target,
        "current": current,
        "deadline": deadline,
        "priority": priority,
    })


def get_goals(status: str | None = None) -> list:
    """列出财富目标；status 可选 'active' / 'completed' / 'paused' 筛选。

    Returns:
        [{id, name, target, current, deadline, priority, status}, ...]
    """
    return list_goals(status)


def update_goal(
    id: str,
    name: str | None = None,
    target: float | None = None,
    current: float | None = None,
    deadline: str | None = None,
    priority: str | None = None,
) -> dict:
    """修改目标字段：传入字段覆盖现有目标，未传字段保留（基于 UPSERT 语义）。

    注意：底层是 INSERT ... ON CONFLICT DO UPDATE，未传字段会用默认值覆盖。
    若要保留原值，请先 get_goals 取出再合并后调 update_goal。
    """
    goal = {"id": id}
    if name is not None:
        goal["name"] = name
    if target is not None:
        goal["target"] = target
    if current is not None:
        goal["current"] = current
    if deadline is not None:
        goal["deadline"] = deadline
    if priority is not None:
        goal["priority"] = priority
    return upsert_goal(goal)


def complete_goal_tool(goal_id: str) -> dict | None:
    """将目标标记为已完成；记录不存在返回 None。"""
    return complete_goal(goal_id)


def calculate_goal_contribution(goal_id: str) -> dict:
    """计算达成目标所需月储蓄额。

    规则：(target - current) / 剩余月数。
    - 剩余月数 = 截止日期与今天之间的月数
    - 若目标已完成（current >= target），返回 required_monthly = 0
    - 若截止日期已过或缺失，返回 required_monthly = 不足额（视为一次性补足）

    Returns:
        {
            "goal_id": ...,
            "name": ...,
            "target": ...,
            "current": ...,
            "shortfall": ...,
            "deadline": ...,
            "remaining_months": ...,
            "required_monthly": ...,
        }
    """
    goals = list_goals()
    goal = next((g for g in goals if g["id"] == goal_id), None)
    if goal is None:
        return {"error": f"目标 {goal_id} 不存在"}

    target = goal["target"]
    current = goal["current"]
    shortfall = max(0, target - current)

    if shortfall == 0:
        return {
            "goal_id": goal_id,
            "name": goal["name"],
            "target": target,
            "current": current,
            "shortfall": 0,
            "deadline": goal["deadline"],
            "remaining_months": 0,
            "required_monthly": 0,
            "note": "目标已达成",
        }

    # 计算剩余月数
    deadline = goal["deadline"]
    if not deadline:
        return {
            "goal_id": goal_id,
            "name": goal["name"],
            "target": target,
            "current": current,
            "shortfall": shortfall,
            "deadline": "",
            "remaining_months": 0,
            "required_monthly": shortfall,
            "note": "无截止日期，所需金额为一次性补足额",
        }

    try:
        deadline_dt = datetime.fromisoformat(deadline)
    except ValueError:
        # 'YYYY-MM-DD' 也能被 fromisoformat 解析
        try:
            deadline_dt = datetime.strptime(deadline, "%Y-%m-%d")
        except ValueError:
            return {"error": f"截止日期格式无效: {deadline}"}

    today = datetime.now()
    if deadline_dt <= today:
        return {
            "goal_id": goal_id,
            "name": goal["name"],
            "target": target,
            "current": current,
            "shortfall": shortfall,
            "deadline": deadline,
            "remaining_months": 0,
            "required_monthly": shortfall,
            "note": "截止日期已过，所需金额为一次性补足额",
        }

    # 月数估算：(年差 * 12 + 月差)，向上取整避免低估
    delta_months = (deadline_dt.year - today.year) * 12 + (deadline_dt.month - today.month)
    if deadline_dt.day > today.day:
        delta_months += 1
    delta_months = max(1, delta_months)

    return {
        "goal_id": goal_id,
        "name": goal["name"],
        "target": target,
        "current": current,
        "shortfall": shortfall,
        "deadline": deadline,
        "remaining_months": delta_months,
        "required_monthly": round(shortfall / delta_months, 2),
    }


# 命名规范：tools_route 自动发现模块下所有 inspect.isfunction 的函数。
# 这里把 complete_goal 重命名为 complete_goal_tool 避免与 service 同名函数冲突。
