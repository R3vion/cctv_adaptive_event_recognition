from __future__ import annotations

import numpy as np

from app.camera_sim import importer as imp
from app.selection import service
from app.selection.selector import SelectionParams, auto_threshold, frame_scope_subset, select
from app.selection.signals import extract

FPS = 15.0


def frames_from(levels: list[float], spot: dict[int, tuple[int, int]] | None = None):
    """BGR frames with a uniform grey level; ``spot`` paints a white square at (x, y) on frame i."""
    out = []
    for i, level in enumerate(levels):
        img = np.full((90, 160, 3), int(level), dtype=np.uint8)
        if spot and i in spot:
            x, y = spot[i]
            img[y:y + 30, x:x + 30] = 255
        out.append(img)
    return out


def params(**kw) -> SelectionParams:
    base = dict(threshold=5.0, frames_min=5, frames_max=30)
    base.update(kw)
    return SelectionParams(**base)


def kinds(result) -> dict[int, list[str]]:
    return {p.index: p.kinds for p in result.frames}


def test_static_segment_gets_mandatory_frames_and_uniform_fill():
    feats = extract(frames_from([80] * 150), FPS)
    res = select(feats, 75, params())
    k = kinds(res)
    assert "first" in k[0] and "last" in k[149] and "trigger" in k[75]
    assert len(res.frames) == 5 and res.candidates == 0
    gaps = np.diff([p.index for p in res.frames])
    assert gaps.max() - gaps.min() <= 2  # a static stretch degrades to uniform sampling


def test_short_sharp_event_is_caught_as_peak():
    # an object visible for only two frames (~0.13 s) – what uniform sampling would miss
    feats = extract(frames_from([80] * 150, spot={61: (40, 30), 62: (70, 30)}), FPS)
    res = select(feats, 75, params())
    picked = [p.index for p in res.frames if "peak" in p.kinds or "keyframe" in p.kinds]
    assert any(60 <= i <= 63 for i in picked)


def test_slow_drift_triggers_keyframes_although_frame_to_frame_change_is_small():
    levels = list(np.linspace(60, 160, 150))  # gradual darkening/brightening like creeping smoke
    feats = extract(frames_from(levels), FPS, pixel_delta=25)
    p = params(threshold=20.0)
    res = select(feats, 75, p)
    assert res.motion.max() < p.threshold      # no single step crosses the line...
    assert any("keyframe" in pk.kinds for pk in res.frames)  # ...but the accumulated change does


def test_max_limit_and_manual_override():
    rng = np.random.default_rng(0)
    levels = list(rng.integers(0, 255, 150))  # every frame changes a lot
    feats = extract(frames_from(levels), FPS)
    capped = select(feats, 75, params(threshold=1.0))
    assert len(capped.frames) <= 30
    manual = select(feats, 75, params(threshold=1.0, manual=True))
    assert len(manual.frames) > 30
    assert {0, 75, 149} <= {p.index for p in manual.frames}


def test_auto_threshold_follows_noise_level():
    quiet = np.r_[0, np.full(100, 0.6) + np.linspace(0, 0.2, 100)]
    busy = np.r_[0, np.full(100, 5.0) + np.linspace(0, 4.0, 100)]
    assert auto_threshold(quiet, 6) < auto_threshold(busy, 6)
    assert auto_threshold(np.zeros(50), 6) == 0.5  # floor


def test_frame_scope_subset_keeps_trigger_and_best_scores():
    frames = [{"t": 0.0, "score": 1, "kinds": ["first"]}, {"t": 2.0, "score": 9, "kinds": ["peak"]},
              {"t": 5.0, "score": 0, "kinds": ["trigger"]}, {"t": 7.0, "score": 4, "kinds": ["keyframe"]},
              {"t": 9.9, "score": 2, "kinds": ["last"]}]
    sub = frame_scope_subset(frames, 3)
    assert [f["t"] for f in sub] == [2.0, 5.0, 7.0]


def test_import_session_selection_and_clip_fallback(data_dir, video_factory):
    video = video_factory("Moving.mp4", 21, fps=15, source="testsrc2")
    imp.manager.on_create.append(service.analyse_session)
    try:
        with video.open("rb") as fh:
            session = imp.manager.create(video.name, fh)
    finally:
        imp.manager.on_create.remove(service.analyse_session)
    payload = service.session_payload(session)
    assert [p["index"] for p in payload] == [0, 1, 2]
    assert all(p["threshold_source"] == "auto" for p in payload)
    assert all(3 <= p["selected"] for p in payload)

    updated = service.update_segment(session, 1, threshold=0.5, manual=True)
    assert [p["index"] for p in updated] == [1, 2]  # following segments are recomputed too
    assert updated[0]["threshold_source"] == "manual"

    result = imp.manager.commit(session.id, imp.CommitRequest(camera_id="Moving", start="2026-10-30T08:00:00"))
    clip = data_dir / result["events"][0]["clip_path"]
    auto = service.select_for_clip(clip, 15.0, 5.0)
    assert auto["origin"] == "auto" and len(auto["frames"]) >= 3
    assert any("trigger" in f["kinds"] for f in auto["frames"])
