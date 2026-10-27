"""Stores the frame selection made on the import page in analytics.db."""

from __future__ import annotations

from app.analytics import repo
from app.selection import service


def store_import_selections(session, result: dict, choices: dict) -> None:
    for ev in result["events"]:
        idx = ev["segment_index"]
        choice = choices.get(idx)
        if choice is not None and choice.trigger_offset_s is not None:
            # the saved trigger frame is mandatory in the selection; make sure it is the final one
            service.update_segment(session, idx, trigger_s=ev["trigger_offset_s"])
        repo.save_selection(ev["event_id"], ev["camera_id"], ev["event_no"], service.stored_selection(session, idx))
