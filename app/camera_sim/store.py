"""Read/write access to events.db for the simulated camera system."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from app.config import get_settings
from app.db.connection import apply_schema, connect, transaction

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def db_path() -> Path:
    return get_settings().events_db_path


def init() -> None:
    apply_schema(db_path(), SCHEMA_SQL)


def list_cameras() -> list[dict]:
    with transaction(db_path(), readonly=True) as conn:
        rows = conn.execute(
            """
            SELECT camera_id, COUNT(*) AS events, MAX(event_no) AS last_event_no
            FROM camera_events GROUP BY camera_id ORDER BY camera_id
            """
        ).fetchall()
        out = []
        for row in rows:
            last = last_event(conn, row["camera_id"])
            out.append({**dict(row), "last_timestamp_iso": last["timestamp_iso"],
                        "last_timestamp": last["timestamp"], "last_duration_s": last["duration_s"]})
        return out


def last_event(conn: sqlite3.Connection, camera_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM camera_events WHERE camera_id = ? ORDER BY event_no DESC LIMIT 1", (camera_id,)
    ).fetchone()
    return dict(row) if row else None


def source_exists(filename: str) -> bool:
    with transaction(db_path(), readonly=True) as conn:
        return conn.execute("SELECT 1 FROM sources WHERE filename = ?", (filename,)).fetchone() is not None


def write_connection() -> sqlite3.Connection:
    return connect(db_path())


def delete_camera(camera_id: str) -> dict:
    """Remove a camera from the camera system: its events, source videos, clips and trigger frames."""
    data_dir = get_settings().data_path
    with transaction(db_path()) as conn:
        sources = [r["source_path"] for r in
                   conn.execute("SELECT source_path FROM sources WHERE camera_id = ?", (camera_id,))]
        events = conn.execute("DELETE FROM camera_events WHERE camera_id = ?", (camera_id,)).rowcount
        conn.execute("DELETE FROM sources WHERE camera_id = ?", (camera_id,))
    for rel in sources:
        (data_dir / rel).unlink(missing_ok=True)
    shutil.rmtree(data_dir / "processed" / camera_id, ignore_errors=True)
    return {"events": events, "sources": len(sources)}
