"""In-memory pub/sub for Server-Sent Events, plus the pipeline log helper."""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from app.analytics import repo

log = logging.getLogger("pipeline")


class Bus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def attach(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def _fanout(self, msg: dict) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            if q.full():
                try:
                    q.get_nowait()  # drop the oldest message for slow clients
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(msg)

    def publish(self, type_: str, **data: Any) -> None:
        """Thread-safe: may be called from the worker thread."""
        if self._loop is None or self._loop.is_closed():
            return
        msg = {"type": type_, **data}
        try:
            self._loop.call_soon_threadsafe(self._fanout, msg)
        except RuntimeError:
            pass


bus = Bus()


def plog(stage: str, message: str, *, level: str = "info", camera_id: str | None = None,
         event_id: str | None = None, data: Any = None) -> None:
    """Pipeline log entry: stored in analytics.db, streamed to the UI and written to stdout."""
    getattr(log, level if level in ("info", "warning", "error") else "info")(
        "[%s] %s%s", stage, f"{event_id or camera_id}: " if (event_id or camera_id) else "", message)
    try:
        rec = repo.add_log(level, stage, message, camera_id, event_id, data)
    except Exception:  # logging must never break the pipeline
        log.exception("could not store pipeline log")
        return
    bus.publish("log", entry=rec)
