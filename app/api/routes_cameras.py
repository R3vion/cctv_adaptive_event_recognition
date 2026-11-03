"""Cameras, prompts (conditions), events, re-runs and media."""

from __future__ import annotations

import re
import shutil

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.analytics import condition_analyzer as ca
from app.analytics import events_source, repo
from app.analytics.bus import bus, plog
from app.camera_sim import store
from app.config import get_settings
from app.llm.ollama_client import LLMError
from app.media import mjpeg
from app.media.ffmpeg import FFmpegError, mjpeg_to_mp4

router = APIRouter(prefix="/api", tags=["cameras"])

CAMERA_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class AnalyzeRequest(BaseModel):
    prompt: str


class ConditionBody(BaseModel):
    prompt: str
    title: str | None = None
    scope: str
    focus_questions: list
    decision_criteria: str
    suggestion: dict | None = None
    llm_call_id: int | None = None
    backfill: bool = False


class ConditionPatch(BaseModel):
    enabled: bool


class RerunBody(BaseModel):
    condition_id: int
    force_observe: bool = False


class BackfillBody(BaseModel):
    force_observe: bool = False


# --- cameras -----------------------------------------------------------------------
@router.get("/cameras")
def cameras() -> dict:
    cams = {c["camera_id"]: {**c, "prompts": 0, "alerts": 0, "needs_review": 0} for c in events_source.cameras()}
    for cond in repo.list_conditions():
        cams.setdefault(cond["camera_id"], {"camera_id": cond["camera_id"], "events": 0, "sources": 0,
                                            "first_iso": None, "last_iso": None, "prompts": 0, "alerts": 0,
                                            "needs_review": 0})
        cams[cond["camera_id"]]["prompts"] += 1
    for alert in repo.list_alerts(limit=10000):
        if alert["camera_id"] in cams:
            key = "needs_review" if alert["status"] == "NEEDS_REVIEW" else "alerts"
            cams[alert["camera_id"]][key] += 1
    return {"cameras": sorted(cams.values(), key=lambda c: c["camera_id"].lower())}


@router.get("/cameras/{camera_id}")
def camera(camera_id: str) -> dict:
    events = events_source.camera_events(camera_id)
    conditions = repo.list_conditions(camera_id)
    if not events and not conditions:
        raise HTTPException(404, "unknown camera")
    queue = repo.queue_for_camera(camera_id)
    evals = repo.evaluations_for_camera(camera_id)
    current: dict[tuple[str, int], dict] = {}
    closing: dict[tuple[int, int], dict] = {}
    history_count: dict[tuple[str, int], int] = {}
    for e in evals:  # newest first
        key = (e["event_id"], e["condition_id"])
        if e["is_closing"]:
            if not e["superseded"]:
                closing.setdefault((e["episode_id"], e["condition_id"]), _eval_brief(e))
            continue
        history_count[key] = history_count.get(key, 0) + 1
        if not e["superseded"]:
            current.setdefault(key, _eval_brief(e))
    episodes = {ep["id"]: {**ep, "events": [], "alerts": [], "closing": {}} for ep in repo.camera_episodes(camera_id)}
    for (ep_id, cid), brief in closing.items():
        if ep_id in episodes:
            episodes[ep_id]["closing"][cid] = brief
    for alert in repo.list_alerts(camera_id=camera_id, limit=1000):
        if alert["episode_id"] in episodes:
            episodes[alert["episode_id"]]["alerts"].append(
                {k: alert[k] for k in ("id", "status", "kind", "title", "condition_id", "confidence")})
    unassigned = []
    for ev in events:
        q = queue.get(ev["event_id"], {})
        row = {**ev, "queue_status": q.get("status", "NEW"), "error": q.get("error"),
               "results": {c["id"]: current.get((ev["event_id"], c["id"])) for c in conditions},
               "runs": {c["id"]: history_count.get((ev["event_id"], c["id"]), 0) for c in conditions}}
        if q.get("episode_id") in episodes:
            episodes[q["episode_id"]]["events"].append(row)
        else:
            unassigned.append(row)
    return {
        "camera_id": camera_id,
        "sources": events_source.sources(camera_id),
        "conditions": conditions,
        "episodes": sorted(episodes.values(), key=lambda e: e["start_iso"], reverse=True),
        "unassigned": unassigned,
        "analysis_mode": get_settings().analysis_mode,
    }


@router.delete("/cameras/{camera_id}")
def delete_camera(camera_id: str) -> dict:
    """Remove a camera everywhere: from the (simulated) camera system and from the analytics.

    The camera system part stands in for deleting the camera in the real backend; the
    analytics never writes events.db on its own.
    """
    if not CAMERA_RE.match(camera_id):
        raise HTTPException(400, "invalid camera id")
    if not events_source.camera_events(camera_id) and not repo.list_conditions(camera_id):
        raise HTTPException(404, "unknown camera")
    if repo.camera_busy(camera_id):
        raise HTTPException(409, "The analysis is working on this camera right now – try again in a moment.")
    removed = store.delete_camera(camera_id)  # first, so the worker cannot rediscover the events
    analytics = repo.delete_camera_data(camera_id)
    files = get_settings().analytics_files_path
    shutil.rmtree(files / "previews" / camera_id, ignore_errors=True)
    for alert_id in analytics["alert_ids"]:
        shutil.rmtree(files / "alerts" / str(alert_id), ignore_errors=True)
    plog("camera", f"camera deleted: {removed['events']} event(s), {removed['sources']} video(s), "
                   f"{len(analytics['alert_ids'])} alert(s)", camera_id=camera_id)
    bus.publish("conditions", camera_id=camera_id)
    bus.publish("alert", camera_id=camera_id)
    return {"ok": True, **removed, "alerts": len(analytics["alert_ids"])}


def _enqueue_job(kind: str, payload: dict) -> int:
    """Queue a re-run / backfill job and tell the browsers right away (the Queue counters)."""
    job = repo.enqueue_job(kind, payload)
    bus.publish("job", job_id=job, camera_id=payload.get("camera_id"), status="QUEUED")
    return job


def _eval_brief(e: dict) -> dict:
    return {k: e[k] for k in ("id", "decision", "confidence", "is_closing", "run_id", "condition_version_id")}


@router.post("/cameras/{camera_id}/analyze")
def analyze_camera(camera_id: str) -> dict:
    n = repo.release_waiting(camera_id)
    plog("queue", f"{n} waiting / failed event(s) queued for analysis", camera_id=camera_id)
    bus.publish("queue", camera_id=camera_id, status="QUEUED")
    return {"queued": n}


# --- conditions ------------------------------------------------------------------------
@router.post("/conditions/analyze")
async def analyze_prompt(body: AnalyzeRequest) -> dict:
    try:
        return await run_in_threadpool(ca.analyze, body.prompt)
    except ca.SpecError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, f"Condition analysis failed: {exc}") from exc


@router.post("/cameras/{camera_id}/conditions")
def create_condition(camera_id: str, body: ConditionBody) -> dict:
    if not CAMERA_RE.match(camera_id):
        raise HTTPException(400, "invalid camera id")
    try:
        spec = ca.validate(body.model_dump())
    except ca.SpecError as exc:
        raise HTTPException(400, str(exc)) from exc
    cond = repo.create_condition(camera_id, spec)
    plog("prompt", f"prompt added: \"{cond['title']}\" ({cond['scope']})", camera_id=camera_id)
    if body.backfill:
        _enqueue_job("backfill", {"condition_id": cond["id"], "camera_id": camera_id})
    bus.publish("conditions", camera_id=camera_id)
    return cond


@router.put("/conditions/{condition_id}")
def update_condition(condition_id: int, body: ConditionBody) -> dict:
    cond = repo.get_condition(condition_id)
    if cond is None:
        raise HTTPException(404, "prompt not found")
    try:
        spec = ca.validate(body.model_dump())
    except ca.SpecError as exc:
        raise HTTPException(400, str(exc)) from exc
    updated = repo.new_version(condition_id, spec)
    plog("prompt", f"prompt edited → version {updated['version']}: \"{updated['title']}\"", camera_id=cond["camera_id"])
    if body.backfill:
        _enqueue_job("backfill", {"condition_id": condition_id, "camera_id": cond["camera_id"]})
    bus.publish("conditions", camera_id=cond["camera_id"])
    return updated


@router.patch("/conditions/{condition_id}")
def patch_condition(condition_id: int, body: ConditionPatch) -> dict:
    cond = repo.get_condition(condition_id)
    if cond is None:
        raise HTTPException(404, "prompt not found")
    repo.set_condition_flags(condition_id, enabled=body.enabled)
    bus.publish("conditions", camera_id=cond["camera_id"])
    return repo.get_condition(condition_id)


@router.delete("/conditions/{condition_id}")
def delete_condition(condition_id: int) -> dict:
    cond = repo.get_condition(condition_id)
    if cond is None:
        raise HTTPException(404, "prompt not found")
    repo.set_condition_flags(condition_id, deleted=True)
    plog("prompt", f"prompt deleted: \"{cond['title']}\"", camera_id=cond["camera_id"])
    bus.publish("conditions", camera_id=cond["camera_id"])
    return {"ok": True}


@router.get("/conditions/{condition_id}/versions")
def versions(condition_id: int) -> dict:
    return {"versions": repo.condition_versions(condition_id)}


@router.post("/conditions/{condition_id}/backfill")
def backfill(condition_id: int, body: BackfillBody) -> dict:
    cond = repo.get_condition(condition_id)
    if cond is None:
        raise HTTPException(404, "prompt not found")
    job = _enqueue_job("backfill", {"condition_id": condition_id, "camera_id": cond["camera_id"],
                                        "force_observe": body.force_observe})
    plog("job", f"\"{cond['title']}\" queued to run on existing events (job #{job})", camera_id=cond["camera_id"])
    return {"job_id": job}


# --- events ----------------------------------------------------------------------------
def _event(event_id: str) -> dict:
    ev = events_source.get_event(event_id)
    if ev is None:
        raise HTTPException(404, "event not found")
    return ev


@router.get("/events/{event_id}")
def event_detail(event_id: str) -> dict:
    ev = _event(event_id)
    queue = repo.queue_for_camera(ev["camera_id"]).get(event_id, {})
    evals = repo.evaluations_for_event(event_id)
    runs: dict[int, dict] = {}
    with repo.db() as conn:
        for r in conn.execute(f"SELECT id, kind, started_at FROM runs WHERE id IN "
                              f"({','.join(str(e['run_id']) for e in evals) or '0'})"):
            runs[r["id"]] = dict(r)
    for e in evals:
        e["run"] = runs.get(e["run_id"])
    return {"event": ev, "queue": queue, "selection": repo.get_selection(event_id),
            "observations": repo.observations_for_event(event_id), "evaluations": evals}


@router.post("/events/{event_id}/rerun")
def rerun(event_id: str, body: RerunBody) -> dict:
    ev = _event(event_id)
    cond = repo.get_condition(body.condition_id)
    if cond is None:
        raise HTTPException(404, "prompt not found")
    job = _enqueue_job("rerun", {"event_id": event_id, "condition_id": body.condition_id,
                                     "force_observe": body.force_observe, "camera_id": ev["camera_id"]})
    what = "the episode is replayed" if cond["scope"] == "history" else "this event only"
    plog("job", f"re-run of \"{cond['title']}\" queued (job #{job}; {what})", camera_id=ev["camera_id"],
         event_id=event_id)
    return {"job_id": job}


@router.get("/events/{event_id}/trigger.jpg")
def trigger_frame(event_id: str) -> FileResponse:
    return FileResponse(events_source.abs_path(_event(event_id)["trigger_frame_path"]), media_type="image/jpeg")


@router.get("/events/{event_id}/frame")
def frame(event_id: str, t: float = 0.0, w: int = 0) -> Response:
    ev = _event(event_id)
    clip = events_source.abs_path(ev["clip_path"])
    jpeg = mjpeg.read_frame(clip, mjpeg.index_at(t, ev["fps"], mjpeg.frame_count(clip)))
    if w > 0:
        jpeg = mjpeg.resize_jpeg(jpeg, w, 80)
    return Response(jpeg, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/events/{event_id}/preview.mp4")
async def preview(event_id: str) -> FileResponse:
    ev = _event(event_id)
    out = get_settings().analytics_files_path / "previews" / ev["camera_id"] / f"chunk_{ev['event_no']:03d}.mp4"
    if not out.exists():
        try:
            await run_in_threadpool(mjpeg_to_mp4, events_source.abs_path(ev["clip_path"]), out, ev["fps"])
        except FFmpegError as exc:
            raise HTTPException(500, f"preview failed: {exc}") from exc
    return FileResponse(out, media_type="video/mp4")


@router.get("/observations/{obs_id}")
def observation(obs_id: int) -> dict:
    obs = repo.get_observation(obs_id)
    if obs is None:
        raise HTTPException(404, "observation not found")
    return obs
