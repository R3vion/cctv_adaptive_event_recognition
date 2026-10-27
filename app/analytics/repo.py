"""Data access for analytics.db (plain sqlite3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from app.config import get_settings
from app.db.connection import apply_schema, dumps, loads, now_iso, row_dict, transaction

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")

VERSION_JSON = ("focus_questions", "suggestion")
EVAL_JSON = ("focus_results",)
ALERT_JSON = ("focus_results", "event_ids")
OBS_JSON = ("covered_versions", "frames", "focus_answers")


def path() -> Path:
    return get_settings().analytics_db_path


def init() -> None:
    apply_schema(path(), SCHEMA_SQL)


def db():
    return transaction(path())


def _rows(rows: Iterable[sqlite3.Row], json_fields: tuple[str, ...] = ()) -> list[dict]:
    return [row_dict(r, json_fields) for r in rows]


# --- settings ---------------------------------------------------------------------
def settings_overrides() -> dict[str, Any]:
    with db() as conn:
        return {r["key"]: loads(r["value"]) for r in conn.execute("SELECT key, value FROM settings")}


def set_setting(key: str, value: Any) -> None:
    with db() as conn:
        conn.execute("INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                     (key, dumps(value), now_iso()))


def delete_setting(key: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


# --- conditions -------------------------------------------------------------------
_CONDITION_SELECT = """
    SELECT c.id, c.camera_id, c.enabled, c.deleted, c.created_at, c.updated_at, c.current_version_id,
           v.version, v.prompt, v.title, v.scope, v.focus_questions, v.decision_criteria, v.scope_overridden,
           v.suggestion, v.llm_call_id
    FROM conditions c JOIN condition_versions v ON v.id = c.current_version_id
"""


def create_condition(camera_id: str, spec: dict) -> dict:
    now = now_iso()
    with db() as conn:
        cid = conn.execute("INSERT INTO conditions (camera_id, enabled, created_at, updated_at) VALUES (?, 1, ?, ?)",
                           (camera_id, now, now)).lastrowid
        vid = _insert_version(conn, cid, 1, spec)
        conn.execute("UPDATE conditions SET current_version_id = ? WHERE id = ?", (vid, cid))
    return get_condition(cid)


def _insert_version(conn: sqlite3.Connection, condition_id: int, version: int, spec: dict) -> int:
    return conn.execute(
        """INSERT INTO condition_versions (condition_id, version, prompt, title, scope, focus_questions,
               decision_criteria, scope_overridden, suggestion, llm_call_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (condition_id, version, spec["prompt"], spec["title"], spec["scope"], dumps(spec["focus_questions"]),
         spec["decision_criteria"], int(bool(spec.get("scope_overridden"))), dumps(spec.get("suggestion")),
         spec.get("llm_call_id"), now_iso()),
    ).lastrowid


def new_version(condition_id: int, spec: dict) -> dict:
    with db() as conn:
        last = conn.execute("SELECT MAX(version) FROM condition_versions WHERE condition_id = ?",
                            (condition_id,)).fetchone()[0] or 0
        vid = _insert_version(conn, condition_id, last + 1, spec)
        conn.execute("UPDATE conditions SET current_version_id = ?, updated_at = ? WHERE id = ?",
                     (vid, now_iso(), condition_id))
    return get_condition(condition_id)


def set_condition_flags(condition_id: int, *, enabled: bool | None = None, deleted: bool | None = None) -> None:
    with db() as conn:
        if enabled is not None:
            conn.execute("UPDATE conditions SET enabled = ?, updated_at = ? WHERE id = ?",
                         (int(enabled), now_iso(), condition_id))
        if deleted is not None:
            conn.execute("UPDATE conditions SET deleted = ?, enabled = 0, updated_at = ? WHERE id = ?",
                         (int(deleted), now_iso(), condition_id))


def get_condition(condition_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute(_CONDITION_SELECT + " WHERE c.id = ?", (condition_id,)).fetchone()
    return row_dict(row, VERSION_JSON)


def list_conditions(camera_id: str | None = None, *, active_only: bool = False) -> list[dict]:
    sql = _CONDITION_SELECT + " WHERE c.deleted = 0"
    args: list[Any] = []
    if camera_id is not None:
        sql += " AND c.camera_id = ?"
        args.append(camera_id)
    if active_only:
        sql += " AND c.enabled = 1"
    with db() as conn:
        return _rows(conn.execute(sql + " ORDER BY c.camera_id, c.id", args), VERSION_JSON)


def condition_versions(condition_id: int) -> list[dict]:
    with db() as conn:
        return _rows(conn.execute("SELECT * FROM condition_versions WHERE condition_id = ? ORDER BY version DESC",
                                  (condition_id,)), VERSION_JSON)


def get_version(version_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM condition_versions WHERE id = ?", (version_id,)).fetchone()
    return row_dict(row, VERSION_JSON)


# --- frame selections ----------------------------------------------------------------
def save_selection(event_id: str, camera_id: str, event_no: int, sel: dict) -> None:
    with db() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO frame_selections (event_id, camera_id, event_no, origin, mode, threshold,
                   threshold_source, manual, frames, signals, stats, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, camera_id, event_no, sel["origin"], sel["mode"], sel["threshold"], sel["threshold_source"],
             int(bool(sel["manual"])), dumps(sel["frames"]), dumps(sel["signals"]), dumps(sel["stats"]), now_iso()),
        )


def get_selection(event_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM frame_selections WHERE event_id = ?", (event_id,)).fetchone()
    return row_dict(row, ("frames", "signals", "stats"))


# --- event queue ------------------------------------------------------------------------
def queued_ids() -> set[str]:
    with db() as conn:
        return {r[0] for r in conn.execute("SELECT event_id FROM event_queue")}


def enqueue_events(events: list[dict], status: str) -> None:
    now = now_iso()
    with db() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO event_queue (event_id, camera_id, event_no, timestamp_iso, status, discovered_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(e["event_id"], e["camera_id"], e["event_no"], e["timestamp_iso"], status, now, now) for e in events],
        )


def next_queued() -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM event_queue WHERE status = 'QUEUED' "
                           "ORDER BY discovered_at, camera_id, timestamp_iso, event_no LIMIT 1").fetchone()
    return row_dict(row)


def set_queue_status(event_id: str, status: str, *, error: str | None = None, episode_id: int | None = None) -> None:
    with db() as conn:
        if episode_id is not None:
            conn.execute("UPDATE event_queue SET status = ?, error = ?, episode_id = ?, updated_at = ? WHERE event_id = ?",
                         (status, error, episode_id, now_iso(), event_id))
        else:
            conn.execute("UPDATE event_queue SET status = ?, error = ?, updated_at = ? WHERE event_id = ?",
                         (status, error, now_iso(), event_id))


def release_waiting(camera_id: str | None = None) -> int:
    with db() as conn:
        if camera_id:
            cur = conn.execute("UPDATE event_queue SET status = 'QUEUED', updated_at = ? "
                               "WHERE status IN ('WAITING', 'ERROR') AND camera_id = ?", (now_iso(), camera_id))
        else:
            cur = conn.execute("UPDATE event_queue SET status = 'QUEUED', updated_at = ? "
                               "WHERE status IN ('WAITING', 'ERROR')", (now_iso(),))
        return cur.rowcount


def reset_processing() -> None:
    """After a restart nothing is processing any more."""
    with db() as conn:
        conn.execute("UPDATE event_queue SET status = 'QUEUED' WHERE status = 'PROCESSING'")
        conn.execute("UPDATE jobs SET status = 'QUEUED' WHERE status = 'RUNNING'")


def queue_counts() -> dict[str, int]:
    with db() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM event_queue GROUP BY status")}


def queue_for_camera(camera_id: str) -> dict[str, dict]:
    with db() as conn:
        return {r["event_id"]: dict(r) for r in conn.execute("SELECT * FROM event_queue WHERE camera_id = ?", (camera_id,))}


def pending_for_camera(camera_id: str) -> int:
    with db() as conn:
        return conn.execute("SELECT COUNT(*) FROM event_queue WHERE camera_id = ? AND status IN ('QUEUED', 'PROCESSING')",
                            (camera_id,)).fetchone()[0]


# --- episodes -------------------------------------------------------------------------
def latest_episode(camera_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM episodes WHERE camera_id = ? ORDER BY start_iso DESC, id DESC LIMIT 1",
                           (camera_id,)).fetchone()
    return row_dict(row)


def get_episode(episode_id: int) -> dict | None:
    with db() as conn:
        return row_dict(conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone())


def create_episode(event: dict) -> dict:
    now = now_iso()
    with db() as conn:
        eid = conn.execute(
            """INSERT INTO episodes (camera_id, status, first_event_no, last_event_no, start_iso, last_event_iso,
                   end_iso, event_count, last_activity_at)
               VALUES (?, 'OPEN', ?, ?, ?, ?, ?, 1, ?)""",
            (event["camera_id"], event["event_no"], event["event_no"], event["timestamp_iso"], event["timestamp_iso"],
             event["end_iso"], now),
        ).lastrowid
    return get_episode(eid)


def extend_episode(episode_id: int, event: dict) -> dict:
    with db() as conn:
        conn.execute(
            """UPDATE episodes SET last_event_no = MAX(last_event_no, ?), last_event_iso = MAX(last_event_iso, ?),
                   end_iso = MAX(end_iso, ?), event_count = event_count + 1, last_activity_at = ?, status = 'OPEN',
                   closed_at = NULL, close_reason = NULL
               WHERE id = ?""",
            (event["event_no"], event["timestamp_iso"], event["end_iso"], now_iso(), episode_id),
        )
    return get_episode(episode_id)


def touch_episode(episode_id: int) -> None:
    with db() as conn:
        conn.execute("UPDATE episodes SET last_activity_at = ? WHERE id = ?", (now_iso(), episode_id))


def close_episode(episode_id: int, reason: str) -> None:
    with db() as conn:
        conn.execute("UPDATE episodes SET status = 'CLOSED', closed_at = ?, close_reason = ? WHERE id = ?",
                     (now_iso(), reason, episode_id))


def set_episode_summary(episode_id: int, summary: str, llm_call_id: int | None) -> None:
    with db() as conn:
        conn.execute("UPDATE episodes SET summary = ?, summary_llm_call_id = ? WHERE id = ?",
                     (summary, llm_call_id, episode_id))


def open_episodes() -> list[dict]:
    with db() as conn:
        return _rows(conn.execute("SELECT * FROM episodes WHERE status = 'OPEN' ORDER BY last_activity_at"))


def camera_episodes(camera_id: str) -> list[dict]:
    with db() as conn:
        return _rows(conn.execute("SELECT * FROM episodes WHERE camera_id = ? ORDER BY start_iso DESC, id DESC",
                                  (camera_id,)))


def memory_episodes(camera_id: str, before_iso: str, since_iso: str) -> list[dict]:
    """Closed episodes of the camera that ended before ``before_iso`` and after ``since_iso``."""
    with db() as conn:
        return _rows(conn.execute(
            "SELECT * FROM episodes WHERE camera_id = ? AND status = 'CLOSED' AND start_iso < ? AND end_iso >= ? "
            "ORDER BY start_iso", (camera_id, before_iso, since_iso)))


def episode_event_ids(episode_id: int) -> list[str]:
    with db() as conn:
        return [r[0] for r in conn.execute(
            "SELECT event_id FROM event_queue WHERE episode_id = ? ORDER BY timestamp_iso, event_no", (episode_id,))]


# --- LLM calls --------------------------------------------------------------------------
def insert_llm_call(rec: dict) -> int:
    with db() as conn:
        return conn.execute(
            """INSERT INTO llm_calls (purpose, model, think, system, prompt, images, options, response, thinking, parsed,
                   error, attempts, prompt_tokens, output_tokens, duration_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rec["purpose"], rec["model"], int(rec["think"]), rec["system"], rec["prompt"], dumps(rec["images"]),
             dumps(rec["options"]), rec.get("response"), rec.get("thinking"), dumps(rec.get("parsed")),
             rec.get("error"), rec.get("attempts", 1), rec.get("prompt_tokens"), rec.get("output_tokens"),
             rec.get("duration_ms"), now_iso()),
        ).lastrowid


def get_llm_call(call_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM llm_calls WHERE id = ?", (call_id,)).fetchone()
    return row_dict(row, ("images", "options", "parsed"))


# --- observations ------------------------------------------------------------------------
def insert_observation(rec: dict) -> int:
    with db() as conn:
        return conn.execute(
            """INSERT INTO observations (event_id, camera_id, event_no, frames_hash, focus_hash, covered_versions, frames,
                   description, focus_answers, best_frame, llm_call_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rec["event_id"], rec["camera_id"], rec["event_no"], rec["frames_hash"], rec["focus_hash"],
             dumps(rec["covered_versions"]), dumps(rec["frames"]), rec["description"], dumps(rec["focus_answers"]),
             rec.get("best_frame"), rec.get("llm_call_id"), now_iso()),
        ).lastrowid


def find_observation(event_id: str, frames_hash: str, version_ids: list[int]) -> dict | None:
    """Latest observation of these frames that already answers every given condition version."""
    with db() as conn:
        rows = conn.execute("SELECT * FROM observations WHERE event_id = ? AND frames_hash = ? ORDER BY id DESC",
                            (event_id, frames_hash)).fetchall()
    for row in rows:
        obs = row_dict(row, OBS_JSON)
        if set(version_ids) <= set(obs["covered_versions"]):
            return obs
    return None


def get_observation(obs_id: int) -> dict | None:
    with db() as conn:
        return row_dict(conn.execute("SELECT * FROM observations WHERE id = ?", (obs_id,)).fetchone(), OBS_JSON)


def observations_for_event(event_id: str) -> list[dict]:
    with db() as conn:
        return _rows(conn.execute("SELECT * FROM observations WHERE event_id = ? ORDER BY id DESC", (event_id,)), OBS_JSON)


# --- runs & evaluations ---------------------------------------------------------------------
def create_run(kind: str, condition: dict, episode_id: int) -> int:
    with db() as conn:
        return conn.execute(
            "INSERT INTO runs (kind, condition_id, condition_version_id, camera_id, episode_id, status, started_at)"
            " VALUES (?, ?, ?, ?, ?, 'RUNNING', ?)",
            (kind, condition["id"], condition["current_version_id"], condition["camera_id"], episode_id, now_iso()),
        ).lastrowid


def live_run(condition: dict, episode_id: int) -> int:
    """The open live run of this condition version in the episode (created on demand)."""
    with db() as conn:
        row = conn.execute("SELECT id FROM runs WHERE kind = 'live' AND condition_version_id = ? AND episode_id = ? "
                           "ORDER BY id DESC LIMIT 1", (condition["current_version_id"], episode_id)).fetchone()
    return row[0] if row else create_run("live", condition, episode_id)


def finish_live_runs(episode_id: int) -> None:
    with db() as conn:
        conn.execute("UPDATE runs SET status = 'DONE', finished_at = ? WHERE kind = 'live' AND episode_id = ? "
                     "AND status = 'RUNNING'", (now_iso(), episode_id))


def finish_run(run_id: int, status: str = "DONE", error: str | None = None) -> None:
    with db() as conn:
        conn.execute("UPDATE runs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                     (status, error, now_iso(), run_id))


def insert_evaluation(rec: dict) -> int:
    with db() as conn:
        return conn.execute(
            """INSERT INTO evaluations (run_id, condition_id, condition_version_id, camera_id, episode_id, event_id,
                   event_no, is_closing, decision, confidence, reasoning, focus_results, key_event_no, key_frame,
                   observation_id, llm_call_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rec["run_id"], rec["condition_id"], rec["condition_version_id"], rec["camera_id"], rec["episode_id"],
             rec["event_id"], rec["event_no"], int(rec.get("is_closing", False)), rec["decision"], rec.get("confidence"),
             rec.get("reasoning"), dumps(rec.get("focus_results")), rec.get("key_event_no"), rec.get("key_frame"),
             rec.get("observation_id"), rec.get("llm_call_id"), now_iso()),
        ).lastrowid


def supersede_evaluations(condition_id: int, episode_id: int, *, event_id: str | None = None,
                          closing_only: bool = False) -> None:
    sql = "UPDATE evaluations SET superseded = 1 WHERE condition_id = ? AND episode_id = ? AND superseded = 0"
    args: list[Any] = [condition_id, episode_id]
    if event_id is not None:
        sql += " AND event_id = ? AND is_closing = 0"
        args.append(event_id)
    if closing_only:
        sql += " AND is_closing = 1"
    with db() as conn:
        conn.execute(sql, args)


def current_evaluations(condition_id: int, episode_id: int) -> list[dict]:
    with db() as conn:
        return _rows(conn.execute(
            "SELECT * FROM evaluations WHERE condition_id = ? AND episode_id = ? AND superseded = 0 "
            "ORDER BY is_closing, event_no, id", (condition_id, episode_id)), EVAL_JSON)


def evaluations_for_event(event_id: str) -> list[dict]:
    with db() as conn:
        return _rows(conn.execute("SELECT * FROM evaluations WHERE event_id = ? ORDER BY id DESC", (event_id,)), EVAL_JSON)


def evaluations_for_camera(camera_id: str) -> list[dict]:
    with db() as conn:
        return _rows(conn.execute("SELECT * FROM evaluations WHERE camera_id = ? ORDER BY id DESC", (camera_id,)),
                     EVAL_JSON)


def conditions_in_episode(episode_id: int) -> list[int]:
    with db() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT condition_id FROM evaluations WHERE episode_id = ?",
                                           (episode_id,))]


# --- alerts --------------------------------------------------------------------------------
def current_alert(condition_id: int, episode_id: int) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM alerts WHERE condition_id = ? AND episode_id = ? AND status != 'SUPERSEDED' "
                           "ORDER BY id DESC LIMIT 1", (condition_id, episode_id)).fetchone()
    return row_dict(row, ALERT_JSON)


def insert_alert(rec: dict) -> int:
    now = now_iso()
    cols = ["kind", "status", "condition_id", "condition_version_id", "camera_id", "episode_id", "run_id",
            "evaluation_id", "prompt", "title", "scope", "decision", "confidence", "reasoning", "focus_results",
            "event_ids", "key_event_id", "key_frame_t", "key_frame_path", "episode_start_iso", "episode_end_iso",
            "episode_summary"]
    values = [dumps(rec[c]) if c in ALERT_JSON else rec.get(c) for c in cols]
    with db() as conn:
        return conn.execute(
            f"INSERT INTO alerts ({', '.join(cols)}, created_at, updated_at) VALUES ({', '.join('?' * len(cols))}, ?, ?)",
            values + [now, now]).lastrowid


def update_alert(alert_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    values = [dumps(v) if k in ALERT_JSON else v for k, v in fields.items()]
    with db() as conn:
        conn.execute(f"UPDATE alerts SET {sets}, updated_at = ? WHERE id = ?", values + [now_iso(), alert_id])


def get_alert(alert_id: int) -> dict | None:
    with db() as conn:
        return row_dict(conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone(), ALERT_JSON)


def list_alerts(*, status: str | None = None, camera_id: str | None = None, condition_id: int | None = None,
                include_superseded: bool = False, limit: int = 200) -> list[dict]:
    sql, args = "SELECT * FROM alerts WHERE 1 = 1", []
    if status:
        sql += " AND status = ?"
        args.append(status)
    elif not include_superseded:
        sql += " AND status != 'SUPERSEDED'"
    if camera_id:
        sql += " AND camera_id = ?"
        args.append(camera_id)
    if condition_id:
        sql += " AND condition_id = ?"
        args.append(condition_id)
    with db() as conn:
        return _rows(conn.execute(sql + " ORDER BY id DESC LIMIT ?", args + [limit]), ALERT_JSON)


def alert_counts() -> dict[str, int]:
    with db() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM alerts GROUP BY status")}


def alerts_for_episode(episode_id: int) -> list[dict]:
    with db() as conn:
        return _rows(conn.execute("SELECT * FROM alerts WHERE episode_id = ? AND status != 'SUPERSEDED'",
                                  (episode_id,)), ALERT_JSON)


# --- jobs -----------------------------------------------------------------------------------
def enqueue_job(kind: str, payload: dict) -> int:
    with db() as conn:
        return conn.execute("INSERT INTO jobs (kind, payload, status, created_at) VALUES (?, ?, 'QUEUED', ?)",
                            (kind, dumps(payload), now_iso())).lastrowid


def next_job() -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE status = 'QUEUED' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            return None
        conn.execute("UPDATE jobs SET status = 'RUNNING', started_at = ? WHERE id = ?", (now_iso(), row["id"]))
    return row_dict(row, ("payload",))


def finish_job(job_id: int, error: str | None = None) -> None:
    with db() as conn:
        conn.execute("UPDATE jobs SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                     ("ERROR" if error else "DONE", error, now_iso(), job_id))


def job_counts() -> dict[str, int]:
    with db() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")}


# --- camera deletion ---------------------------------------------------------------------------
def camera_busy(camera_id: str) -> bool:
    """True while the worker is processing an event or running a job of this camera."""
    with db() as conn:
        return conn.execute(
            "SELECT 1 FROM event_queue WHERE camera_id = ? AND status = 'PROCESSING' UNION ALL "
            "SELECT 1 FROM jobs WHERE status = 'RUNNING' AND json_extract(payload, '$.camera_id') = ?",
            (camera_id, camera_id)).fetchone() is not None


def delete_camera_data(camera_id: str) -> dict:
    """Delete everything the analytics stored about a camera. Returns the deleted alert ids and counts."""
    with db() as conn:
        cid = (camera_id,)
        alert_ids = [r[0] for r in conn.execute("SELECT id FROM alerts WHERE camera_id = ?", cid)]
        call_ids = [r[0] for r in conn.execute(
            "SELECT llm_call_id FROM observations WHERE camera_id = ? UNION "
            "SELECT llm_call_id FROM evaluations WHERE camera_id = ? UNION "
            "SELECT summary_llm_call_id FROM episodes WHERE camera_id = ? UNION "
            "SELECT v.llm_call_id FROM condition_versions v JOIN conditions c ON c.id = v.condition_id "
            "WHERE c.camera_id = ?", cid * 4) if r[0] is not None]
        conn.execute("DELETE FROM condition_versions WHERE condition_id IN "
                     "(SELECT id FROM conditions WHERE camera_id = ?)", cid)
        counts = {table: conn.execute(f"DELETE FROM {table} WHERE camera_id = ?", cid).rowcount
                  for table in ("alerts", "evaluations", "runs", "observations", "event_queue", "episodes",
                                "frame_selections", "conditions", "pipeline_log")}
        conn.execute("DELETE FROM jobs WHERE status = 'QUEUED' AND json_extract(payload, '$.camera_id') = ?", cid)
        conn.executemany("DELETE FROM llm_calls WHERE id = ?", [(i,) for i in call_ids])
    return {"alert_ids": alert_ids, "counts": counts}


# --- pipeline log -----------------------------------------------------------------------------
def add_log(level: str, stage: str, message: str, camera_id: str | None = None, event_id: str | None = None,
            data: Any = None) -> dict:
    rec = {"ts": now_iso(), "level": level, "stage": stage, "camera_id": camera_id, "event_id": event_id,
           "message": message, "data": data}
    with db() as conn:
        rec["id"] = conn.execute(
            "INSERT INTO pipeline_log (ts, level, stage, camera_id, event_id, message, data) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rec["ts"], level, stage, camera_id, event_id, message, dumps(data) if data is not None else None),
        ).lastrowid
        conn.execute("DELETE FROM pipeline_log WHERE id <= ?", (rec["id"] - 5000,))
    return rec


def recent_logs(limit: int = 200, camera_id: str | None = None) -> list[dict]:
    with db() as conn:
        if camera_id:
            rows = conn.execute("SELECT * FROM pipeline_log WHERE camera_id = ? ORDER BY id DESC LIMIT ?",
                                (camera_id, limit))
        else:
            rows = conn.execute("SELECT * FROM pipeline_log ORDER BY id DESC LIMIT ?", (limit,))
        return _rows(rows, ("data",))
