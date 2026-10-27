"""Read-only access to the camera system's events.db."""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.db.connection import transaction


def _db():
    return transaction(get_settings().events_db_path, readonly=True)


def available() -> bool:
    return get_settings().events_db_path.exists()


def events_after(created_after: str | None, known: set[str] | None = None, limit: int = 500) -> list[dict]:
    """Events created at/after ``created_after`` (wall clock), oldest first."""
    if not available():
        return []
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM camera_events WHERE created_at >= ? ORDER BY created_at, camera_id, event_no LIMIT ?",
            (created_after or "", limit),
        ).fetchall()
    out = [dict(r) for r in rows]
    return [e for e in out if not known or e["event_id"] not in known]


def all_event_ids() -> list[str]:
    if not available():
        return []
    with _db() as conn:
        return [r[0] for r in conn.execute("SELECT event_id FROM camera_events")]


def get_event(event_id: str) -> dict | None:
    if not available():
        return None
    with _db() as conn:
        row = conn.execute("SELECT * FROM camera_events WHERE event_id = ?", (event_id,)).fetchone()
    return dict(row) if row else None


def camera_events(camera_id: str) -> list[dict]:
    if not available():
        return []
    with _db() as conn:
        rows = conn.execute("SELECT * FROM camera_events WHERE camera_id = ? ORDER BY event_no", (camera_id,)).fetchall()
    return [dict(r) for r in rows]


def events_by_ids(event_ids: list[str]) -> dict[str, dict]:
    if not event_ids or not available():
        return {}
    with _db() as conn:
        marks = ",".join("?" * len(event_ids))
        rows = conn.execute(f"SELECT * FROM camera_events WHERE event_id IN ({marks})", event_ids).fetchall()
    return {r["event_id"]: dict(r) for r in rows}


def cameras() -> list[dict]:
    if not available():
        return []
    with _db() as conn:
        rows = conn.execute(
            """SELECT camera_id, COUNT(*) AS events, MIN(timestamp_iso) AS first_iso, MAX(timestamp_iso) AS last_iso,
                      (SELECT COUNT(*) FROM sources s WHERE s.camera_id = e.camera_id) AS sources
               FROM camera_events e GROUP BY camera_id ORDER BY camera_id"""
        ).fetchall()
    return [dict(r) for r in rows]


def sources(camera_id: str) -> list[dict]:
    if not available():
        return []
    with _db() as conn:
        rows = conn.execute("SELECT * FROM sources WHERE camera_id = ? ORDER BY id", (camera_id,)).fetchall()
    return [dict(r) for r in rows]


def abs_path(relative: str) -> Path:
    return get_settings().data_path / relative
