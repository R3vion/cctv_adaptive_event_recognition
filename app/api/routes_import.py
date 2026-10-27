"""Import page API (the simulated camera system)."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.camera_sim import store
from app.camera_sim.importer import CommitRequest, ImportRejected, manager
from app.config import get_settings
from app.selection import service as selection

router = APIRouter(prefix="/api/import", tags=["import"])


class SelectionUpdate(BaseModel):
    index: int
    threshold: float | None = None
    manual: bool | None = None
    trigger_s: float | None = None


def _session(sid: str):
    try:
        return manager.get(sid)
    except ImportRejected as exc:
        raise HTTPException(exc.status, str(exc)) from exc


def _full(session) -> dict:
    return {**session.summary(), "selection": selection.session_payload(session)}


@router.get("/cameras")
def cameras() -> dict:
    return {"cameras": store.list_cameras(), "episode_gap_s": get_settings().episode_gap_s}


@router.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    try:
        session = await run_in_threadpool(manager.create, file.filename or "", file.file)
    except ImportRejected as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    finally:
        await file.close()
    return _full(session)


@router.get("/sessions/{sid}")
def get_session(sid: str) -> dict:
    return _full(_session(sid))


@router.get("/sessions/{sid}/video")
def session_video(sid: str) -> FileResponse:
    return FileResponse(_session(sid).staged_path, media_type="video/mp4")


@router.post("/sessions/{sid}/selection")
async def update_selection(sid: str, body: SelectionUpdate) -> dict:
    session = _session(sid)
    if not 0 <= body.index < len(session.segments):
        raise HTTPException(400, "segment index out of range")

    def work():
        with session.lock:
            return selection.update_segment(session, body.index, threshold=body.threshold,
                                            manual=body.manual, trigger_s=body.trigger_s)

    return {"segments": await run_in_threadpool(work)}


@router.get("/sessions/{sid}/thumb/{grid_index}")
def thumb(sid: str, grid_index: int) -> Response:
    thumbs = _session(sid).extra["signals"].thumbs
    if not 0 <= grid_index < len(thumbs):
        raise HTTPException(404, "no such frame")
    return Response(thumbs[grid_index], media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.post("/sessions/{sid}/commit")
async def commit(sid: str, req: CommitRequest) -> dict:
    try:
        return await run_in_threadpool(manager.commit, sid, req)
    except ImportRejected as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.delete("/sessions/{sid}")
def discard(sid: str) -> dict:
    manager.discard(sid)
    return {"ok": True}
