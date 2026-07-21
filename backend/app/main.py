"""Fortuner Buying Agent — FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.auth import require_user
from app.api.routes import router as api_router
from app.api.web import router as web_router
from app.config import get_settings
from app.db.session import init_db
from app.workers.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    Path(settings.raw_snapshot_dir).mkdir(parents=True, exist_ok=True)
    Path("./data").mkdir(parents=True, exist_ok=True)
    init_db()
    logger.info("Database initialised (%s)", settings.database_url.split("://")[0])
    scheduler = start_scheduler()
    yield
    stop_scheduler()
    if scheduler:
        logger.info("Scheduler stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.app_env != "production" else None,
    )
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(api_router)
    app.include_router(web_router)

    @app.get("/api/public/health")
    def public_health():
        return {"status": "ok", "app": settings.app_name}

    return app


app = create_app()
