from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.routes.health import router as health_router
from api.routes.dashboard import router as dashboard_router
from api.routes.profile import router as profile_router
from api.routes.chat import router as chat_router
from core.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时显式建表（幂等）；get_connection 首次连接另有懒加载兜底
    init_db()
    yield


app = FastAPI(title="Agent Study API", lifespan=lifespan)


app.include_router(health_router, prefix="/api", tags=["health"])
app.include_router(dashboard_router, prefix="/api", tags=["dashboard"])
app.include_router(profile_router, prefix="/api", tags=["profile"])
app.include_router(chat_router, prefix="/api", tags=["chat"])



if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
