from fastapi import FastAPI

from api.routes.health import router as health_router
from api.routes.dashboard import router as dashboard_router
from api.routes.profile import router as profile_router

app = FastAPI(title="Agent Study API")

app.include_router(health_router, prefix="/api", tags=["health"])
app.include_router(dashboard_router, prefix="/api", tags=["dashboard"])
app.include_router(profile_router, prefix="/api", tags=["profile"])



if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
