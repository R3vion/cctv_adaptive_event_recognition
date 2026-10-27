"""Alerts are derived from the current evaluations of a prompt in an episode.

``reconcile`` is called after every new evaluation and when an episode closes.  It
computes the alert the episode should have and creates, keeps, updates or supersedes
alerts accordingly – so re-runs never produce duplicates and review decisions
(confirmed / dismissed) survive as long as the underlying evaluation does.
"""

from __future__ import annotations

from app.analytics import events_source, repo
from app.analytics.bus import bus, plog
from app.config import get_settings
from app.media import mjpeg


def key_frame_file(rel: str):
    """``analytics/alerts/<id>/key_frame.jpg`` (relative to the data dir) → file path."""
    return get_settings().analytics_files_path / rel.split("/", 1)[1]


def desired_alert(evals: list[dict], scope: str, episode_closed: bool) -> tuple[str, dict] | None:
    """(kind, evaluation) the episode should be alerted with, or None.

    - first YES (event evaluations in time order, then the closing decision) → DETECTED
    - closed episode, history scope: closing decision UNCERTAIN, or still PENDING → NEEDS_REVIEW
    - closed episode, frame / event scope: any UNCERTAIN → NEEDS_REVIEW
    """
    ordered = sorted(evals, key=lambda e: (e["is_closing"], e["event_no"], e["id"]))
    for e in ordered:
        if e["decision"] == "YES":
            return "DETECTED", e
    if not episode_closed:
        return None
    if scope == "history":
        closing = [e for e in ordered if e["is_closing"]]
        if closing:
            last = closing[-1]
            return ("NEEDS_REVIEW", last) if last["decision"] in ("UNCERTAIN", "PENDING") else None
        decided = [e for e in ordered if e["decision"] in ("PENDING", "UNCERTAIN", "NO")]
        if decided and decided[-1]["decision"] in ("PENDING", "UNCERTAIN"):
            return "NEEDS_REVIEW", decided[-1]
        return None
    uncertain = [e for e in ordered if e["decision"] == "UNCERTAIN"]
    if uncertain:
        return "NEEDS_REVIEW", max(uncertain, key=lambda e: e.get("confidence") or 0)
    return None


def _key_frame(ev: dict, camera_id: str) -> tuple[str | None, float | None, bytes | None]:
    """(key event id, time in the clip, JPEG) of the evaluation's key frame."""
    key_no = ev.get("key_event_no") or ev["event_no"]
    key_event_id = f"{camera_id}#{key_no:03d}"
    event = events_source.get_event(key_event_id) or events_source.get_event(ev["event_id"])
    if event is None:
        return None, None, None
    obs = None
    if event["event_id"] == ev["event_id"] and ev.get("observation_id"):
        obs = repo.get_observation(ev["observation_id"])
    if obs is None:
        for cand in repo.current_evaluations(ev["condition_id"], ev["episode_id"]):
            if cand["event_id"] == event["event_id"] and cand.get("observation_id"):
                obs = repo.get_observation(cand["observation_id"])
                break
    if obs is None:
        found = repo.observations_for_event(event["event_id"])
        obs = found[0] if found else None
    t = event["trigger_offset_s"]
    if obs and obs["frames"]:
        n = ev.get("key_frame")  # frame number within the key event's observation
        n = n if n and 1 <= n <= len(obs["frames"]) else obs.get("best_frame")
        if n and 1 <= n <= len(obs["frames"]):
            t = obs["frames"][n - 1]["t"]
    clip = events_source.abs_path(event["clip_path"])
    jpeg = mjpeg.read_frame(clip, mjpeg.index_at(t, event["fps"], mjpeg.frame_count(clip)))
    return event["event_id"], t, jpeg


def reconcile(condition_id: int, episode_id: int) -> dict | None:
    episode = repo.get_episode(episode_id)
    evals = [e for e in repo.current_evaluations(condition_id, episode_id) if e["decision"] not in ("SKIPPED",)]
    existing = repo.current_alert(condition_id, episode_id)
    scope = repo.get_version(evals[-1]["condition_version_id"])["scope"] if evals else None
    desired = desired_alert(evals, scope, episode["status"] == "CLOSED") if evals else None

    if desired is None:
        if existing:
            repo.update_alert(existing["id"], status="SUPERSEDED")
            plog("alert", f"alert #{existing['id']} superseded (no longer supported by the evaluations)",
                 camera_id=episode["camera_id"])
            bus.publish("alert", alert_id=existing["id"], status="SUPERSEDED", camera_id=episode["camera_id"])
        return None

    kind, ev = desired
    episode_fields = {"episode_start_iso": episode["start_iso"], "episode_end_iso": episode["end_iso"],
                      "episode_summary": episode.get("summary"),
                      "event_ids": repo.episode_event_ids(episode_id)}
    if existing and existing["kind"] == kind and existing["evaluation_id"] == ev["id"]:
        repo.update_alert(existing["id"], **episode_fields)
        return repo.get_alert(existing["id"])
    if existing:
        repo.update_alert(existing["id"], status="SUPERSEDED")
        bus.publish("alert", alert_id=existing["id"], status="SUPERSEDED", camera_id=episode["camera_id"])

    version = repo.get_version(ev["condition_version_id"])
    alert_id = repo.insert_alert({
        "kind": kind, "status": "ACTIVE" if kind == "DETECTED" else "NEEDS_REVIEW",
        "condition_id": condition_id, "condition_version_id": ev["condition_version_id"],
        "camera_id": episode["camera_id"], "episode_id": episode_id, "run_id": ev["run_id"],
        "evaluation_id": ev["id"], "prompt": version["prompt"], "title": version["title"],
        "scope": version["scope"], "decision": ev["decision"], "confidence": ev.get("confidence"),
        "reasoning": ev.get("reasoning"), "focus_results": ev.get("focus_results") or [], **episode_fields,
    })
    key_event_id, key_t, jpeg = _key_frame(ev, episode["camera_id"])
    if jpeg:
        rel = f"analytics/alerts/{alert_id}/key_frame.jpg"
        path = key_frame_file(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(jpeg)
        repo.update_alert(alert_id, key_event_id=key_event_id, key_frame_t=key_t, key_frame_path=rel)
    label = "ALERT" if kind == "DETECTED" else "NEEDS REVIEW"
    plog("alert", f"{label} #{alert_id}: \"{version['title']}\" (confidence {ev.get('confidence') or 0:.2f})",
         level="warning", camera_id=episode["camera_id"], event_id=key_event_id,
         data={"alert_id": alert_id, "kind": kind})
    bus.publish("alert", alert_id=alert_id, status="ACTIVE" if kind == "DETECTED" else "NEEDS_REVIEW",
                camera_id=episode["camera_id"], title=version["title"])
    return repo.get_alert(alert_id)
