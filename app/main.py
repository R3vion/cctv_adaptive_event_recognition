"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.analytics import repo as analytics_repo
from app.analytics.bus import bus
from app.analytics.import_hook import store_import_selections
from app.analytics.worker import worker
from app.api import routes_alerts, routes_cameras, routes_import, routes_pages, routes_system
from app.api.routes_pages import WEB_DIR
from app.camera_sim import store as camera_store
from app.camera_sim.importer import manager as import_manager
from app.config import get_settings
from app.selection import service as selection_service

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, str(settings.log_level).upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings().data_path.mkdir(parents=True, exist_ok=True)
    camera_store.init()  # the simulated camera system owns events.db
    analytics_repo.init()
    bus.attach(asyncio.get_running_loop())
    worker.start()
    yield
    await worker.stop()


import_manager.on_create.append(selection_service.analyse_session)
import_manager.on_commit.append(store_import_selections)

app = FastAPI(title="CCTV adaptive event recognition", lifespan=lifespan)
app.include_router(routes_import.router)
app.include_router(routes_system.router)
app.include_router(routes_cameras.router)
app.include_router(routes_alerts.router)
app.include_router(routes_pages.router)
app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")


@app.middleware("http")
async def revalidate_static(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"  # always revalidate (ETag) – no stale UI after updates
    return response


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}
