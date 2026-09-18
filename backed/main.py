from fastapi import FastAPI

from api.routes.health import router as health_router

app = FastAPI(title="Agent Study API")

app.include_router(health_router, prefix="/api", tags=["health"])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
