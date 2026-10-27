"""Alerts: list, detail, review, key frame."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.analytics import events_source, repo
from app.analytics.alerts import key_frame_file
from app.analytics.bus import bus, plog

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class Review(BaseModel):
    action: Literal["confirm", "dismiss"]
    note: str | None = None


@router.get("")
def list_alerts(status: str | None = None, camera_id: str | None = None, condition_id: int | None = None,
                include_superseded: bool = False) -> dict:
    return {"alerts": repo.list_alerts(status=status, camera_id=camera_id, condition_id=condition_id,
                                       include_superseded=include_superseded)}


@router.get("/{alert_id}")
def alert_detail(alert_id: int) -> dict:
    alert = repo.get_alert(alert_id)
    if alert is None:
        raise HTTPException(404, "alert not found")
    events = events_source.events_by_ids(alert["event_ids"])
    evaluation = None
    with repo.db() as conn:
        row = conn.execute("SELECT * FROM evaluations WHERE id = ?", (alert["evaluation_id"],)).fetchone()
        evaluation = dict(row) if row else None
    return {"alert": alert, "events": [events[i] for i in alert["event_ids"] if i in events],
            "episode": repo.get_episode(alert["episode_id"]), "evaluation": evaluation}


@router.post("/{alert_id}/review")
def review(alert_id: int, body: Review) -> dict:
    alert = repo.get_alert(alert_id)
    if alert is None:
        raise HTTPException(404, "alert not found")
    if alert["status"] not in ("ACTIVE", "NEEDS_REVIEW", "CONFIRMED", "DISMISSED"):
        raise HTTPException(400, f"a {alert['status']} alert cannot be reviewed")
    status = "CONFIRMED" if body.action == "confirm" else "DISMISSED"
    repo.update_alert(alert_id, status=status, review_note=body.note)
    plog("alert", f"alert #{alert_id} {status.lower()} by the operator", camera_id=alert["camera_id"])
    bus.publish("alert", alert_id=alert_id, status=status, camera_id=alert["camera_id"])
    return repo.get_alert(alert_id)


@router.get("/{alert_id}/key_frame.jpg")
def key_frame(alert_id: int) -> FileResponse:
    alert = repo.get_alert(alert_id)
    if alert is None or not alert.get("key_frame_path"):
        raise HTTPException(404, "no key frame")
    return FileResponse(key_frame_file(alert["key_frame_path"]), media_type="image/jpeg")
