from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.schema import create_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create DB tables on startup (no-op if they already exist)
    create_tables()
    yield


app = FastAPI(
    title="Geospatial File Measurement API",
    description="API for processing geospatial files and calculating geometry measurements.",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router, prefix="/api")


@app.get("/health")
def health_check():
    return {"status": "ok"}