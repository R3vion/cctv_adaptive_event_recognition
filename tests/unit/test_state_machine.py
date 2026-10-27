from __future__ import annotations

import pytest

from app.analytics import alerts, engine, repo
from app.analytics import condition_analyzer as ca
from app.camera_sim import importer as imp
from app.config import set_test_overrides


def ev(id_, event_no, decision, *, closing=False, confidence=0.8):
    return {"id": id_, "event_no": event_no, "decision": decision, "is_closing": int(closing),
            "confidence": confidence}


# --- desired alert (pure) -------------------------------------------------------------
def test_first_yes_wins_and_alerts_while_episode_is_open():
    kind, chosen = alerts.desired_alert([ev(1, 1, "NO"), ev(3, 3, "YES"), ev(2, 2, "YES")], "event", False)
    assert kind == "DETECTED" and chosen["id"] == 2


def test_uncertain_waits_for_episode_end_then_needs_review():
    evals = [ev(1, 1, "NO"), ev(2, 2, "UNCERTAIN", confidence=0.4)]
    assert alerts.desired_alert(evals, "frame", False) is None
    kind, chosen = alerts.desired_alert(evals, "frame", True)
    assert kind == "NEEDS_REVIEW" and chosen["id"] == 2


def test_negative_episode_has_no_alert():
    assert alerts.desired_alert([ev(1, 1, "NO"), ev(2, 2, "NO")], "event", True) is None


def test_history_uses_the_closing_decision():
    pending = [ev(1, 1, "PENDING"), ev(2, 2, "PENDING")]
    assert alerts.desired_alert(pending, "history", False) is None
    kind, _ = alerts.desired_alert(pending + [ev(3, 2, "UNCERTAIN", closing=True)], "history", True)
    assert kind == "NEEDS_REVIEW"
    assert alerts.desired_alert(pending + [ev(3, 2, "NO", closing=True)], "history", True) is None
    kind, chosen = alerts.desired_alert(pending + [ev(3, 2, "YES", closing=True)], "history", True)
    assert kind == "DETECTED" and chosen["id"] == 3


def test_history_left_pending_without_closing_needs_review():
    kind, _ = alerts.desired_alert([ev(1, 1, "PENDING")], "history", True)
    assert kind == "NEEDS_REVIEW"


# --- episodes (database, no model) -------------------------------------------------------
def event(no: int, ts: str, camera: str = "Cam") -> dict:
    return {"event_id": f"{camera}#{no:03d}", "camera_id": camera, "event_no": no, "timestamp_iso": ts,
            "timestamp": ts, "duration_s": 10.0}


@pytest.fixture
def analytics(data_dir):
    repo.init()
    set_test_overrides(data_dir=str(data_dir), episode_gap_s=15)
    return data_dir


def _queue(*events):
    repo.enqueue_events(list(events), "QUEUED")


def test_events_within_the_gap_form_one_episode(analytics):
    a, b = event(1, "2026-10-30T10:00:00"), event(2, "2026-10-30T10:00:10")
    _queue(a, b)
    ep1 = engine.assign_episode(a)
    ep2 = engine.assign_episode(b)
    assert ep1["id"] == ep2["id"] and ep2["event_count"] == 2 and ep2["status"] == "OPEN"


def test_a_larger_gap_closes_the_episode_and_opens_a_new_one(analytics):
    a, b = event(1, "2026-10-30T10:00:00"), event(2, "2026-10-30T10:05:00")
    _queue(a, b)
    ep1 = engine.assign_episode(a)
    ep2 = engine.assign_episode(b)
    assert ep1["id"] != ep2["id"]
    assert repo.get_episode(ep1["id"])["status"] == "CLOSED"


def test_closed_episode_is_reopened_by_an_event_within_the_gap(analytics):
    a, b = event(1, "2026-10-30T10:00:00"), event(2, "2026-10-30T10:00:10")
    _queue(a, b)
    ep = engine.assign_episode(a)
    repo.close_episode(ep["id"], "idle")
    again = engine.assign_episode(b)
    assert again["id"] == ep["id"] and again["status"] == "OPEN"


# --- reconcile (database + a real clip for the key frame) -----------------------------------
def test_reconcile_creates_keeps_and_supersedes_alerts(analytics, video_factory):
    video = video_factory("Cam.mp4", 12)
    with video.open("rb") as fh:
        session = imp.manager.create("Cam.mp4", fh)
    imp.manager.commit(session.id, imp.CommitRequest(camera_id="Cam", start="2026-10-30T10:00:00"))
    cond = repo.create_condition("Cam", ca.validate({
        "prompt": "Alert me if you see smoke.", "scope": "frame", "focus_questions": ["Is smoke visible?"],
        "decision_criteria": "Q1 yes."}))
    e1 = event(1, "2026-10-30T10:00:00")
    _queue(e1)
    ep = engine.assign_episode(e1)
    run = repo.create_run("live", cond, ep["id"])
    base = {"run_id": run, "condition_id": cond["id"], "condition_version_id": cond["current_version_id"],
            "camera_id": "Cam", "episode_id": ep["id"], "event_id": e1["event_id"], "event_no": 1,
            "confidence": 0.9, "reasoning": "r", "focus_results": [], "key_frame": 1}
    first = repo.insert_evaluation({**base, "decision": "YES"})

    alert = alerts.reconcile(cond["id"], ep["id"])
    assert alert["kind"] == "DETECTED" and alert["status"] == "ACTIVE" and alert["evaluation_id"] == first
    assert (analytics / alert["key_frame_path"]).exists()

    repo.update_alert(alert["id"], status="CONFIRMED")
    kept = alerts.reconcile(cond["id"], ep["id"])
    assert kept["id"] == alert["id"] and kept["status"] == "CONFIRMED"  # review survives a no-op reconcile

    repo.supersede_evaluations(cond["id"], ep["id"], event_id=e1["event_id"])
    repo.insert_evaluation({**base, "decision": "NO"})
    assert alerts.reconcile(cond["id"], ep["id"]) is None
    assert repo.get_alert(alert["id"])["status"] == "SUPERSEDED"


def test_stop_after_first_yes(analytics):
    cond = repo.create_condition("Cam", ca.validate({
        "prompt": "p", "scope": "event", "focus_questions": ["q"], "decision_criteria": "c"}))
    e1 = event(1, "2026-10-30T10:00:00")
    _queue(e1)
    ep = engine.assign_episode(e1)
    assert not engine.is_stopped(cond["id"], ep["id"])
    repo.insert_evaluation({"run_id": repo.create_run("live", cond, ep["id"]), "condition_id": cond["id"],
                            "condition_version_id": cond["current_version_id"], "camera_id": "Cam",
                            "episode_id": ep["id"], "event_id": e1["event_id"], "event_no": 1, "decision": "YES"})
    assert engine.is_stopped(cond["id"], ep["id"])
