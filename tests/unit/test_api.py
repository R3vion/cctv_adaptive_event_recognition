"""API smoke tests (no model involved)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.analytics import events_source, repo
from app.camera_sim import store
from app.config import get_settings
from app.main import app


def test_pages_settings_and_prompt_crud(data_dir):
    with TestClient(app) as client:
        for page in ("/", "/import", "/cameras", "/alerts", "/settings", "/cameras/Gate", "/alerts/1"):
            assert client.get(page).status_code == 200, page

        settings = client.get("/api/settings").json()["settings"]
        assert {s["key"] for s in settings} >= {"ollama_base_url", "frames_min", "frames_max", "analysis_mode"}
        bad = client.put("/api/settings", json={"values": {"analysis_mode": "sometimes"}})
        assert bad.status_code == 400
        ok = client.put("/api/settings", json={"values": {"analysis_mode": "manual"}}).json()["settings"]
        mode = next(s for s in ok if s["key"] == "analysis_mode")
        assert mode["value"] == "manual" and mode["overridden"]
        reset = client.delete("/api/settings/analysis_mode").json()["settings"]
        assert not next(s for s in reset if s["key"] == "analysis_mode")["overridden"]

        spec = {"prompt": "Alert me if you see smoke.", "title": "Smoke", "scope": "frame",
                "focus_questions": ["Is smoke visible?"], "decision_criteria": "Q1 yes.",
                "suggestion": {"scope": "event"}}
        created = client.post("/api/cameras/Gate/conditions", json=spec).json()
        assert created["scope"] == "frame" and created["scope_overridden"]
        edited = client.put(f"/api/conditions/{created['id']}", json={**spec, "scope": "event"}).json()
        assert edited["version"] == 2
        assert client.patch(f"/api/conditions/{created['id']}", json={"enabled": False}).json()["enabled"] == 0
        assert len(client.get(f"/api/conditions/{created['id']}/versions").json()["versions"]) == 2
        cams = client.get("/api/cameras").json()["cameras"]
        assert any(c["camera_id"] == "Gate" and c["prompts"] == 1 for c in cams)
        assert client.post("/api/cameras/bad id!/conditions", json=spec).status_code in (400, 404)

        assert client.get("/api/alerts").json() == {"alerts": []}
        assert client.get("/api/events/Nope%23001").status_code == 404
        assert client.get("/api/system/status").json()["worker"]["running"]
        assert "templates" in client.get("/api/prompts").json()


def _fake_camera(data_dir, camera_id: str) -> None:
    """One source video and one event in events.db, with its files on disk (no ffmpeg needed)."""
    files = [f"source/{camera_id}.mp4", f"processed/{camera_id}/chunk_001/chunk_001.mjpeg",
             f"processed/{camera_id}/chunk_001/trigger_frame.jpg"]
    for rel in files:
        (data_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (data_dir / rel).write_bytes(b"x")
    with store.write_connection() as conn:
        src = conn.execute("INSERT INTO sources (filename, camera_id, source_path, duration_s, fps, width, height, "
                           "uploaded_at) VALUES (?, ?, ?, 10, 10, 160, 120, '2026-10-30T10:00:00')",
                           (f"{camera_id}.mp4", camera_id, files[0])).lastrowid
        conn.execute("INSERT INTO camera_events (event_id, camera_id, event_no, source_id, source_offset_s, timestamp, "
                     "timestamp_iso, duration_s, fps, frame_count, width, height, clip_path, trigger_frame_path, "
                     "trigger_offset_s, created_at) VALUES (?, ?, 1, ?, 0, '2026_10_30-10_00_00', "
                     "'2026-10-30T10:00:00', 10, 10, 100, 160, 120, ?, ?, 5, '2026-10-30T10:00:00')",
                     (f"{camera_id}#001", camera_id, src, files[1], files[2]))


def test_delete_camera_removes_events_files_and_analytics(data_dir):
    repo.init()
    _fake_camera(data_dir, "Porch")
    _fake_camera(data_dir, "Gate")
    client = TestClient(app)  # no lifespan: the worker stays off
    spec = {"prompt": "Alert me if you see smoke.", "title": "Smoke", "scope": "frame",
            "focus_questions": ["Is smoke visible?"], "decision_criteria": "Q1 yes."}
    cond = client.post("/api/cameras/Porch/conditions", json=spec).json()
    event = events_source.get_event("Porch#001") | {"end_iso": "2026-10-30T10:00:10"}
    repo.enqueue_events([event], "DONE")
    episode = repo.create_episode(event)
    alert_id = repo.insert_alert({"kind": "DETECTED", "status": "ACTIVE", "condition_id": cond["id"],
                                  "condition_version_id": cond["current_version_id"], "camera_id": "Porch",
                                  "episode_id": episode["id"], "evaluation_id": 1, "prompt": spec["prompt"],
                                  "title": "Smoke", "scope": "frame", "decision": "YES", "focus_results": [],
                                  "event_ids": ["Porch#001"]})
    key_frame = get_settings().analytics_files_path / "alerts" / str(alert_id) / "key_frame.jpg"
    key_frame.parent.mkdir(parents=True)
    key_frame.write_bytes(b"x")

    res = client.delete("/api/cameras/Porch")
    assert res.status_code == 200 and res.json()["events"] == 1 and res.json()["alerts"] == 1
    assert [c["camera_id"] for c in client.get("/api/cameras").json()["cameras"]] == ["Gate"]
    assert not (data_dir / "processed" / "Porch").exists() and not (data_dir / "source" / "Porch.mp4").exists()
    assert not key_frame.parent.exists()
    assert repo.list_conditions("Porch") == [] and repo.camera_episodes("Porch") == []
    assert client.get("/api/alerts").json() == {"alerts": []}
    assert (data_dir / "processed" / "Gate").exists()
    assert client.delete("/api/cameras/Porch").status_code == 404
