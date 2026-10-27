from __future__ import annotations

from datetime import datetime

import cv2
import numpy as np
import pytest

from app.camera_sim import importer as imp
from app.camera_sim import store
from app.camera_sim.timestamps import segment_starts, to_display
from app.db.connection import transaction
from app.media import mjpeg
from app.media.ffmpeg import VideoInfo


def test_plan_segments_keeps_short_remainder():
    segs = imp.plan_segments(VideoInfo(34.0, 10.0, 160, 120))
    assert [round(s.duration_s, 1) for s in segs] == [10.0, 10.0, 10.0, 4.0]
    assert [s.first_frame for s in segs] == [0, 100, 200, 300]


def test_plan_segments_short_video_is_one_segment():
    segs = imp.plan_segments(VideoInfo(7.1, 30.0, 160, 120))
    assert len(segs) == 1 and segs[0].frame_count == 213


def test_suggest_camera_id():
    assert imp.suggest_camera_id("Front Door (1).mp4") == "Front_Door_1"
    assert imp.suggest_camera_id("foodDelivery.mp4") == "foodDelivery"


def test_timestamps_shift_by_ten_seconds():
    stamps = segment_starts(datetime(2026, 10, 29, 14, 32, 5), 3)
    assert [to_display(s) for s in stamps] == [
        "2026_10_29-14_32_05", "2026_10_29-14_32_15", "2026_10_29-14_32_25"]


def test_mjpeg_frame_offsets_roundtrip():
    frames = []
    for value in (10, 120, 240):
        img = np.full((24, 32, 3), value, dtype=np.uint8)
        frames.append(cv2.imencode(".jpg", img)[1].tobytes())
    offsets = mjpeg.frame_offsets(b"".join(frames))
    assert len(offsets) == 3
    assert [e - s for s, e in offsets] == [len(f) for f in frames]


def _import(video, camera_id: str, start: str, triggers: dict[int, float] | None = None) -> dict:
    with video.open("rb") as fh:
        session = imp.manager.create(video.name, fh)
    choices = [imp.SegmentChoice(index=i, trigger_offset_s=t) for i, t in (triggers or {}).items()]
    return imp.manager.commit(session.id, imp.CommitRequest(camera_id=camera_id, start=start, segments=choices))


def test_import_creates_events_files_and_rows(data_dir, video_factory):
    video = video_factory("Gate.mp4", 23)
    result = _import(video, "Gate", "2026-10-29T14:32:05", {0: 2.0})
    events = result["events"]
    assert [e["event_id"] for e in events] == ["Gate#001", "Gate#002", "Gate#003"]
    assert [e["timestamp"] for e in events] == [
        "2026_10_29-14_32_05", "2026_10_29-14_32_15", "2026_10_29-14_32_25"]
    assert [round(e["duration_s"]) for e in events] == [10, 10, 3]
    assert events[0]["trigger_offset_s"] == pytest.approx(2.0)
    assert events[1]["trigger_offset_s"] == pytest.approx(5.0)  # default: middle

    assert (data_dir / "source" / "Gate.mp4").exists()
    for e in events:
        chunk = data_dir / e["clip_path"]
        assert chunk.name == f"chunk_{e['event_no']:03d}.mjpeg"
        assert chunk.parent.name == f"chunk_{e['event_no']:03d}"
        assert (data_dir / e["trigger_frame_path"]).name == "trigger_frame.jpg"
        assert mjpeg.frame_count(chunk) == e["frame_count"]

    with transaction(store.db_path(), readonly=True) as conn:
        n = conn.execute("SELECT COUNT(*) FROM camera_events WHERE camera_id='Gate'").fetchone()[0]
    assert n == 3


def test_second_video_continues_numbering_and_duplicate_is_rejected(data_dir, video_factory):
    _import(video_factory("Porch1.mp4", 12), "Porch", "2026-10-29T10:00:00")
    second = _import(video_factory("Porch2.mp4", 5), "Porch", "2026-10-29T10:05:00")
    assert [e["event_id"] for e in second["events"]] == ["Porch#003"]
    assert (data_dir / "processed" / "Porch" / "chunk_003" / "chunk_003.mjpeg").exists()

    with pytest.raises(imp.ImportRejected) as err:
        with (data_dir.parent / "videos" / "Porch1.mp4").open("rb") as fh:
            imp.manager.create("Porch1.mp4", fh)
    assert err.value.status == 409


def test_start_must_follow_last_event(data_dir, video_factory):
    _import(video_factory("A.mp4", 5), "Cam", "2026-10-29T10:00:00")
    with pytest.raises(imp.ImportRejected):
        _import(video_factory("B.mp4", 5), "Cam", "2026-10-29T09:00:00")
    # nothing of the rejected import is left behind
    assert not (data_dir / "source" / "B.mp4").exists()
    assert not (data_dir / "processed" / "Cam" / "chunk_002").exists()


def test_invalid_camera_id_rejected(data_dir, video_factory):
    with pytest.raises(imp.ImportRejected):
        _import(video_factory("C.mp4", 3), "bad id!", "2026-10-29T10:00:00")
