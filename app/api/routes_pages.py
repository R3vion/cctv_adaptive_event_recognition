"""HTML pages (plain files; the JavaScript talks to the JSON API)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

router = APIRouter(include_in_schema=False)


def _page(name: str) -> FileResponse:
    path = WEB_DIR / f"{name}.html"
    if not path.exists():
        raise HTTPException(404, "page not found")
    return FileResponse(path, media_type="text/html", headers={"Cache-Control": "no-cache"})


@router.get("/")
def dashboard() -> FileResponse:
    return _page("dashboard")


@router.get("/import")
def import_page() -> FileResponse:
    return _page("import")


@router.get("/cameras")
def cameras_page() -> FileResponse:
    return _page("cameras")


@router.get("/cameras/{camera_id}")
def camera_page(camera_id: str) -> FileResponse:
    return _page("camera")


@router.get("/alerts")
def alerts_page() -> FileResponse:
    return _page("alerts")


@router.get("/alerts/{alert_id}")
def alert_page(alert_id: int) -> FileResponse:
    return _page("alert")


@router.get("/settings")
def settings_page() -> FileResponse:
    return _page("settings")
