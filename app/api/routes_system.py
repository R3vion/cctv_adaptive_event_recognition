"""System status, live stream, logs, dashboard, settings and prompt templates."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.analytics import events_source, repo
from app.analytics.bus import bus, plog
from app.analytics.worker import worker
from app.config import SPECS, env_defaults, get_settings, reload_settings, validate_override
from app.llm import prompts
from app.llm.ollama_client import client

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/system/status")
async def status() -> dict:
    ollama = await run_in_threadpool(client.status)
    return {"ollama": ollama, "worker": worker.status()}


@router.get("/stream")
async def stream(request: Request) -> StreamingResponse:
    queue = bus.subscribe()

    async def events():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/logs")
def logs(limit: int = 200, camera_id: str | None = None) -> dict:
    return {"entries": repo.recent_logs(min(max(limit, 1), 1000), camera_id)}


@router.get("/dashboard")
def dashboard() -> dict:
    cams = events_source.cameras()
    return {
        "cameras": len(cams),
        "events": sum(c["events"] for c in cams),
        "prompts": len(repo.list_conditions(active_only=True)),
        "queue": repo.queue_counts(),
        "alerts": repo.alert_counts(),
        "recent_alerts": repo.list_alerts(limit=6),
        "worker": worker.status(),
    }


@router.get("/llm-calls/{call_id}")
def llm_call(call_id: int) -> dict:
    call = repo.get_llm_call(call_id)
    if call is None:
        raise HTTPException(404, "LLM call not found")
    return call


# --- settings ------------------------------------------------------------------------
class SettingsUpdate(BaseModel):
    values: dict[str, object]


@router.get("/settings")
def get_settings_list() -> dict:
    current = get_settings()
    env = env_defaults()
    items = []
    for spec in SPECS:
        items.append({
            "key": spec.key, "label": spec.label, "group": spec.group, "help": spec.help,
            "type": spec.type.__name__, "choices": list(spec.choices) if spec.choices else None,
            "min": spec.minimum, "max": spec.maximum, "editable": spec.editable,
            "value": getattr(current, spec.key), "env_value": env[spec.key],
            "overridden": spec.key in current.overridden,
        })
    return {"settings": items}


@router.put("/settings")
def update_settings(body: SettingsUpdate) -> dict:
    clean = {}
    for key, raw in body.values.items():
        try:
            clean[key] = validate_override(key, raw)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"{key}: {exc}") from exc
    for key, value in clean.items():
        repo.set_setting(key, value)
    reload_settings()
    if clean:
        plog("settings", "updated: " + ", ".join(f"{k}={v}" for k, v in clean.items()))
    return get_settings_list()


@router.delete("/settings/{key}")
def reset_setting(key: str) -> dict:
    repo.delete_setting(key)
    reload_settings()
    plog("settings", f"{key} reset to the .env value")
    return get_settings_list()


@router.post("/settings/test-ollama")
async def test_ollama() -> dict:
    return await run_in_threadpool(client.status, True)


@router.get("/prompts")
def prompt_templates() -> dict:
    return {"templates": prompts.all_templates()}
