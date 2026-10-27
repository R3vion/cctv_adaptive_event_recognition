"""Small helpers around the standard ``sqlite3`` module."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


def connect(path: Path, *, readonly: bool = False, timeout: float = 15.0) -> sqlite3.Connection:
    """Open a connection. ``readonly`` uses SQLite's ``mode=ro`` so writes fail."""
    if readonly:
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True,
                               timeout=timeout, check_same_thread=False)
    else:
        conn = sqlite3.connect(path, timeout=timeout, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(path: Path, *, readonly: bool = False) -> Iterator[sqlite3.Connection]:
    """Connection that commits on success, rolls back on error and always closes."""
    conn = connect(path, readonly=readonly)
    try:
        yield conn
        if not readonly:
            conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def apply_schema(path: Path, schema_sql: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    """Wall-clock time, local, second precision."""
    return datetime.now().isoformat(timespec="seconds")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


def row_dict(row: sqlite3.Row | None, json_fields: tuple[str, ...] = ()) -> dict | None:
    if row is None:
        return None
    out = dict(row)
    for field in json_fields:
        if field in out:
            out[field] = loads(out[field], default=None)
    return out
