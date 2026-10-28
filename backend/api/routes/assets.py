"""
资产接口（路由层）。

- GET    /api/assets：列出全部资产（返回 {name: value} 字典）
- POST   /api/assets：UPSERT 单条资产 body {name, value}
- DELETE /api/assets/{name}：删除单条资产
"""

from fastapi import APIRouter, Body, HTTPException

from core.services.assets import list_assets, upsert_asset, delete_asset

router = APIRouter()


@router.get("/assets")
def read_assets():
    """列出当前用户全部资产。"""
    return list_assets()


@router.post("/assets")
def upsert_asset_endpoint(data: dict = Body(...)):
    """UPSERT 单条资产：同名则更新 value（可选 asset_type）。"""
    name = data.get("name")
    value = data.get("value")
    if not name or value is None:
        raise HTTPException(status_code=400, detail="name 和 value 必填")
    return upsert_asset(name, float(value), data.get("asset_type") or None)


@router.delete("/assets/{name}")
def delete_asset_endpoint(name: str):
    """删除单条资产；不存在时返回 404。"""
    if not delete_asset(name):
        raise HTTPException(status_code=404, detail=f"资产 '{name}' 不存在")
    return {"ok": True}
