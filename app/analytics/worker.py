"""Background worker: discovers new camera events and processes the queue and jobs.

One worker, one item at a time: events are processed in time order per camera and
re-runs never race with live processing.
"""

from __future__ import annotations

import asyncio
import logging

from app.analytics import engine, events_source, repo
from app.analytics.bus import bus, plog
from app.config import get_settings

log = logging.getLogger(__name__)

IDLE_SLEEP_S = 1.0


class Worker:
    def __init__(self) -> None:
        self.current: str | None = None
        self.running = False
        self._task: asyncio.Task | None = None

    # -- status --------------------------------------------------------------------
    def status(self) -> dict:
        return {"running": self.running, "busy": self.current is not None, "current": self.current,
                "mode": get_settings().analysis_mode, "queue": repo.queue_counts(), "jobs": repo.job_counts()}

    def _set_current(self, value: str | None) -> None:
        self.current = value
        bus.publish("worker", busy=value is not None, current=value)

    # -- loop ------------------------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            repo.reset_processing()
            self.running = True
            self._task = asyncio.create_task(self._run(), name="analytics-worker")

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while self.running:
            try:
                did_work = await asyncio.to_thread(self.step)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep the worker alive
                log.exception("worker step failed")
                plog("worker", f"unexpected error: {exc}", level="error")
                self._set_current(None)
                did_work = False
            if not did_work:
                await asyncio.sleep(IDLE_SLEEP_S)

    def step(self) -> bool:
        """One unit of work. Returns True when something was done."""
        self.discover()
        job = repo.next_job()
        if job:
            self.run_job(job)
            return True
        item = repo.next_queued()
        if item:
            self._set_current(item["event_id"])
            try:
                engine.process_event(item["event_id"])
            except Exception as exc:
                repo.set_queue_status(item["event_id"], "ERROR", error=str(exc)[:500])
                plog("worker", f"processing failed: {exc}", level="error", camera_id=item["camera_id"],
                     event_id=item["event_id"])
                log.exception("processing %s failed", item["event_id"])
            finally:
                self._set_current(None)
            return True
        return engine.close_idle_episodes()

    def discover(self) -> int:
        new_ids = set(events_source.all_event_ids()) - repo.queued_ids()
        if not new_ids:
            return 0
        events = sorted(events_source.events_by_ids(sorted(new_ids)).values(),
                        key=lambda e: (e["camera_id"], e["timestamp_iso"], e["event_no"]))
        status = "QUEUED" if get_settings().analysis_mode == "auto" else "WAITING"
        repo.enqueue_events(events, status)
        for cam in sorted({e["camera_id"] for e in events}):
            n = sum(1 for e in events if e["camera_id"] == cam)
            plog("discover", f"{n} new event(s) → {status}", camera_id=cam)
        bus.publish("queue", camera_id=None, status=status)
        return len(events)

    def run_job(self, job: dict) -> None:
        payload = job["payload"]
        label = f"job #{job['id']} {job['kind']}"
        self._set_current(label)
        try:
            if job["kind"] == "rerun":
                engine.rerun_event(payload["event_id"], int(payload["condition_id"]),
                                   force_observe=bool(payload.get("force_observe")))
            elif job["kind"] == "backfill":
                engine.backfill(int(payload["condition_id"]), force_observe=bool(payload.get("force_observe")))
            else:
                raise ValueError(f"unknown job kind {job['kind']}")
            repo.finish_job(job["id"])
            plog("job", f"{label} done", camera_id=payload.get("camera_id"))
        except Exception as exc:
            repo.finish_job(job["id"], str(exc)[:500])
            plog("job", f"{label} failed: {exc}", level="error", camera_id=payload.get("camera_id"))
            log.exception("%s failed", label)
        finally:
            self._set_current(None)
            bus.publish("job", job_id=job["id"])


worker = Worker()
