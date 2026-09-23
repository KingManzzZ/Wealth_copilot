"""
基本信息接口（路由层）。

- GET   /api/basic_info：获取当前用户基本信息（未创建时返回默认值）
- POST  /api/basic_info：UPSERT 基本信息（仅更新传入字段，未传字段保留）

处理函数使用同步 def：SQLite 访问为阻塞 I/O，交给 FastAPI 线程池执行。
"""

from fastapi import APIRouter, Body

from core.services.basic_info import get_basic_info, upsert_basic_info

router = APIRouter()


@router.get("/basic_info")
def read_basic_info():
    """获取当前用户基本信息。"""
    return get_basic_info()


@router.post("/basic_info")
def upsert_basic_info_endpoint(data: dict = Body(...)):
    """UPSERT 基本信息：仅接受白名单字段，整体覆盖。"""
    return upsert_basic_info(data)
