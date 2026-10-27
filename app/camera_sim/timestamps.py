"""Event timestamp helpers (display format ``YYYY_MM_DD-hh_mm_ss``)."""

from __future__ import annotations

from datetime import datetime, timedelta

DISPLAY_FORMAT = "%Y_%m_%d-%H_%M_%S"
SEGMENT_STEP_S = 10


def parse_iso(value: str) -> datetime:
    """Parse a naive local ISO timestamp (``YYYY-MM-DDTHH:MM[:SS]``)."""
    dt = datetime.fromisoformat(value.strip())
    return dt.replace(tzinfo=None, microsecond=0)


def to_display(dt: datetime) -> str:
    return dt.strftime(DISPLAY_FORMAT)


def to_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def segment_starts(start: datetime, count: int, step_s: int = SEGMENT_STEP_S) -> list[datetime]:
    """Timestamps of consecutive segments: +10 s each."""
    return [start + timedelta(seconds=step_s * i) for i in range(count)]
