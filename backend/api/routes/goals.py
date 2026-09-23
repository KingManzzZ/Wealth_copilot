"""
财富目标接口（路由层）。

- GET    /api/goals：列出全部目标
- POST   /api/goals：UPSERT 单条目标 body FinancialGoal
- DELETE /api/goals/{goal_id}：删除单条目标
"""

from fastapi import APIRouter, Body, HTTPException

from core.services.goals import list_goals, upsert_goal, delete_goal

router = APIRouter()


@router.get("/goals")
def read_goals():
    """列出当前用户全部目标。"""
    return list_goals()


@router.post("/goals")
def upsert_goal_endpoint(data: dict = Body(...)):
    """UPSERT 单条目标：按 goal_id 查重，存在则更新。"""
    goal_id = data.get("id")
    if not goal_id:
        raise HTTPException(status_code=400, detail="id（goal_id）必填")
    return upsert_goal(data)


@router.delete("/goals/{goal_id}")
def delete_goal_endpoint(goal_id: str):
    """删除单条目标；不存在时返回 404。"""
    if not delete_goal(goal_id):
        raise HTTPException(status_code=404, detail=f"目标 '{goal_id}' 不存在")
    return {"ok": True}
