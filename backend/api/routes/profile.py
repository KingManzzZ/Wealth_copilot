"""
财务画像接口（路由层）。

- GET   /api/profile：获取当前用户财务画像（未创建时返回默认值）
- POST  /api/profile：创建初始画像（默认值叠加传入字段，已存在则整体覆盖）
- PATCH /api/profile：部分更新（浅合并，assets/debts/goals 整体替换）

处理函数使用同步 def：SQLite 访问为阻塞 I/O，交给 FastAPI 线程池执行，
避免占用事件循环；字段白名单与合并规则均在 service 层完成。
"""

from fastapi import APIRouter, Body

from core.services.profile import create_profile, get_profile, update_profile

router = APIRouter()


@router.get("/profile")
def read_profile():
    """获取当前用户财务画像。"""
    return get_profile()


@router.post("/profile")
def create_user_profile(data: dict = Body(...)):
    """创建初始画像：已存在则整体覆盖。"""
    return create_profile(data)


@router.patch("/profile")
def update_user_profile(data: dict = Body(...)):
    """部分更新画像：仅覆盖传入字段。"""
    return update_profile(data)
