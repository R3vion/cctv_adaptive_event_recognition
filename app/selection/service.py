"""Frame selection for import sessions (with the chart data) and for single clips."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.config import get_settings
from app.media import mjpeg
from app.media.ffmpeg import decode_frames, scaled_size
from app.selection.selector import SelectionParams, SelectionResult, auto_threshold, combined_motion, select
from app.selection.signals import Features, extract

THUMB_SIDE = 360  # chart tooltips, filmstrip and the looping preview on the import page


def params_from_settings(threshold: float, manual: bool = False) -> SelectionParams:
    s = get_settings()
    return SelectionParams(
        threshold=float(threshold), w_pixel=s.selection_w_pixel, w_hist=s.selection_w_hist,
        pixel_delta=s.pixel_delta, frames_min=s.frames_min, frames_max=s.frames_max, manual=manual,
    )


def default_threshold(features: Features) -> tuple[float, str]:
    """(threshold, source): the fixed setting, or the noise-adaptive value when it is 0."""
    s = get_settings()
    if float(s.selection_threshold) > 0:
        return float(s.selection_threshold), "fixed"
    motion = combined_motion(features, params_from_settings(0.0))
    return auto_threshold(motion, float(s.selection_auto_k)), "auto"


@dataclass
class SegmentState:
    index: int
    start: int            # first grid frame of the segment
    end: int              # exclusive
    duration_s: float
    threshold: float
    trigger_s: float
    manual: bool = False
    result: SelectionResult | None = None


@dataclass
class SessionSignals:
    grid_fps: float
    mode: str
    features: Features
    thumbs: list[bytes]
    default_threshold: float
    threshold_source: str  # auto | fixed
    segments: list[SegmentState] = field(default_factory=list)


def _thumb(frame: np.ndarray) -> bytes:
    h, w = frame.shape[:2]
    scale = THUMB_SIDE / max(h, w)
    if scale < 1:
        frame = cv2.resize(frame, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])[1].tobytes()


def analyse_session(session) -> None:
    """Import hook: decode the upload on the signal grid and select frames per segment."""
    s = get_settings()
    info = session.info
    grid_fps = min(float(s.signal_fps), info.fps)
    size = scaled_size(info.width, info.height, int(s.signal_side))
    thumbs: list[bytes] = []
    features = extract(decode_frames(session.staged_path, grid_fps, size), grid_fps,
                       mode=s.selection_mode, pixel_delta=s.pixel_delta,
                       on_frame=lambda i, fr: thumbs.append(_thumb(fr)))
    threshold, source = default_threshold(features)
    n = len(features)
    segments = []
    for seg in session.segments:
        start = min(n - 1, int(round(seg.start_s * grid_fps)))
        end = max(start + 1, min(n, int(round((seg.start_s + seg.duration_s) * grid_fps))))
        segments.append(SegmentState(seg.index, start, end, seg.duration_s, threshold, seg.duration_s / 2))
    session.extra["signals"] = SessionSignals(grid_fps, s.selection_mode, features, thumbs, threshold, source,
                                              segments)
    recompute(session, 0)


def recompute(session, from_index: int) -> None:
    """Re-run the selection from ``from_index`` on (the keyframe chain continues)."""
    sig: SessionSignals = session.extra["signals"]
    init_ref = None
    if from_index > 0 and sig.segments[from_index - 1].result is not None:
        init_ref = sig.segments[from_index - 1].result.end_ref
    for seg in sig.segments[from_index:]:
        feats = sig.features.slice(seg.start, seg.end)
        trigger_idx = int(round(seg.trigger_s * sig.grid_fps))
        seg.result = select(feats, trigger_idx, params_from_settings(seg.threshold, seg.manual), init_ref)
        init_ref = seg.result.end_ref


def update_segment(session, index: int, *, threshold: float | None = None, manual: bool | None = None,
                   trigger_s: float | None = None) -> list[dict]:
    """Apply a change from the import page; returns this and every following segment."""
    sig: SessionSignals = session.extra["signals"]
    seg = sig.segments[index]
    if manual is not None:
        seg.manual = bool(manual)
        if not seg.manual:
            seg.threshold = sig.default_threshold
    if threshold is not None:
        seg.threshold = max(0.0, float(threshold))
    if trigger_s is not None:
        seg.trigger_s = min(max(0.0, float(trigger_s)), seg.duration_s)
    recompute(session, index)
    return [segment_payload(sig, s) for s in sig.segments[index:]]


def segment_payload(sig: SessionSignals, seg: SegmentState) -> dict:
    res = seg.result
    times = sig.features.times[seg.start:seg.end] - sig.features.times[seg.start]
    return {
        "index": seg.index,
        "grid_offset": seg.start,
        "grid_fps": sig.grid_fps,
        "mode": sig.mode,
        "threshold": round(seg.threshold, 2),
        "default_threshold": sig.default_threshold,
        "threshold_source": "manual" if seg.manual else sig.threshold_source,
        "manual": seg.manual,
        "trigger_s": round(seg.trigger_s, 3),
        "frames": [p.as_dict() for p in res.frames],
        "candidates": res.candidates,
        "selected": len(res.frames),
        "chart": res.chart(times),
    }


def session_payload(session) -> list[dict]:
    sig: SessionSignals = session.extra["signals"]
    return [segment_payload(sig, s) for s in sig.segments]


def stored_selection(session, segment_index: int) -> dict:
    """What analytics.db keeps for an imported segment."""
    sig: SessionSignals = session.extra["signals"]
    payload = segment_payload(sig, sig.segments[segment_index])
    return {"origin": "import", "mode": sig.mode, "threshold": payload["threshold"],
            "threshold_source": payload["threshold_source"], "manual": payload["manual"],
            "frames": payload["frames"], "signals": payload["chart"],
            "stats": {"candidates": payload["candidates"], "selected": payload["selected"],
                      "grid_fps": sig.grid_fps}}


def select_for_clip(clip: Path, fps: float, trigger_offset_s: float) -> dict:
    """Fallback for events without a stored selection: compute it from the MJPEG clip."""
    s = get_settings()
    count = mjpeg.frame_count(clip)
    grid_fps = min(float(s.signal_fps), fps)
    step = fps / grid_fps
    wanted = sorted({min(count - 1, int(round(k * step))) for k in range(int(count / step) + 1)})

    def frames():
        for jpeg in mjpeg.read_frames(clip, wanted):
            img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            h, w = img.shape[:2]
            yield cv2.resize(img, scaled_size(w, h, int(s.signal_side)), interpolation=cv2.INTER_AREA)

    feats = extract(frames(), grid_fps, mode=s.selection_mode, pixel_delta=s.pixel_delta)
    feats.times = np.asarray([i / fps for i in wanted])
    threshold, source = default_threshold(feats)
    trigger_idx = int(np.argmin(np.abs(feats.times - trigger_offset_s)))
    res = select(feats, trigger_idx, params_from_settings(threshold))
    return {"origin": "auto", "mode": s.selection_mode, "threshold": threshold, "threshold_source": source,
            "manual": False, "frames": [p.as_dict() for p in res.frames], "signals": res.chart(feats.times),
            "stats": {"candidates": res.candidates, "selected": len(res.frames), "grid_fps": grid_fps}}
