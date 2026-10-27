"""Hybrid frame selection.

1. mandatory frames: first, trigger, last
2. candidates: keyframes (change vs. last keyframe reaches the threshold) and peaks
   (local maxima of the frame-to-frame change at or above the threshold)
3. too many → best scores with temporal non-maximum suppression (``frames_max``)
4. too few  → fill along the cumulative change (``frames_min``)

A manual threshold disables steps 3 and 4.  The keyframe reference can be carried over
from the previous segment, so slow change across segments is still noticed; mandatory
frames do not reset the reference.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.selection.signals import Features, hist_distance, pixel_change


@dataclass(frozen=True)
class SelectionParams:
    threshold: float
    w_pixel: float = 0.7
    w_hist: float = 0.3
    pixel_delta: int = 25
    frames_min: int = 5
    frames_max: int = 30
    manual: bool = False


@dataclass
class Picked:
    index: int
    t: float
    score: float
    kinds: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"index": self.index, "t": round(self.t, 3), "score": round(self.score, 2), "kinds": self.kinds}


@dataclass
class SelectionResult:
    frames: list[Picked]
    motion: np.ndarray
    ref: np.ndarray
    candidates: int
    end_ref: tuple[np.ndarray, np.ndarray]  # reference (grey, hist) to continue with

    def chart(self, times: np.ndarray) -> dict:
        return {"t": [round(float(x), 3) for x in times],
                "motion": [round(float(x), 2) for x in self.motion],
                "ref": [round(float(x), 2) for x in self.ref]}


def combined_motion(f: Features, p: SelectionParams) -> np.ndarray:
    return p.w_pixel * f.motion_pixel + p.w_hist * f.motion_hist


def auto_threshold(motion: np.ndarray, k: float, floor: float = 0.5) -> float:
    """Noise-adaptive threshold: median + k·1.4826·MAD of the frame-to-frame change."""
    values = np.asarray(motion[1:] if len(motion) > 1 else motion, dtype=np.float64)
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med)))
    return round(max(floor, med + k * 1.4826 * mad), 2)


def keyframe_chain(f: Features, p: SelectionParams, init_ref=None):
    """Change vs. the last keyframe; a frame reaching the threshold becomes the new reference."""
    ref_gray, ref_hist = init_ref if init_ref is not None else (f.gray[0], f.hist[0])
    ref = np.zeros(len(f))
    keys: list[tuple[int, float]] = []
    for i in range(len(f)):
        s = p.w_pixel * pixel_change(f.gray[i], ref_gray, p.pixel_delta) + p.w_hist * hist_distance(f.hist[i], ref_hist)
        ref[i] = s
        if s >= p.threshold:
            keys.append((i, s))
            ref_gray, ref_hist = f.gray[i], f.hist[i]
    return ref, keys, (ref_gray, ref_hist)


def find_peaks(values: np.ndarray, threshold: float) -> list[tuple[int, float]]:
    peaks = []
    n = len(values)
    for i in range(n):
        v = values[i]
        if v < threshold:
            continue
        left = values[i - 1] if i > 0 else -np.inf
        right = values[i + 1] if i < n - 1 else -np.inf
        if v > left and v >= right:
            peaks.append((i, float(v)))
    return peaks


def _fill(selected: dict[int, Picked], motion: np.ndarray, times: np.ndarray, target: int) -> None:
    """Insert frames where the most change accumulated between already selected frames."""
    eps = 0.05 * float(motion.mean()) + 1e-3  # keeps static stretches from being ignored
    cum = np.concatenate([[0.0], np.cumsum(motion + eps)])
    while len(selected) < target:
        idx = sorted(selected)
        best = None
        for a, b in zip(idx, idx[1:]):
            if b - a < 2:
                continue
            weight = cum[b] - cum[a]
            if best is None or weight > best[0]:
                best = (weight, a, b)
        if best is None:
            return
        _, a, b = best
        mid = (cum[a] + cum[b]) / 2
        m = int(np.clip(np.searchsorted(cum, mid) - 1, a + 1, b - 1))
        selected[m] = Picked(m, float(times[m]), float(motion[m]), ["fill"])


def select(f: Features, trigger_index: int, p: SelectionParams, init_ref=None) -> SelectionResult:
    n = len(f)
    motion = combined_motion(f, p)
    ref, keys, end_ref = keyframe_chain(f, p, init_ref)

    candidates: dict[int, Picked] = {}
    for i, score in keys:
        candidates[i] = Picked(i, float(f.times[i]), score, ["keyframe"])
    for i, score in find_peaks(motion, p.threshold):
        if i in candidates:
            candidates[i].kinds.append("peak")
            candidates[i].score = max(candidates[i].score, score)
        else:
            candidates[i] = Picked(i, float(f.times[i]), score, ["peak"])

    selected: dict[int, Picked] = {}
    trigger_index = min(max(trigger_index, 0), n - 1)
    for i, kind in ((0, "first"), (trigger_index, "trigger"), (n - 1, "last")):
        score = max(float(ref[i]), float(motion[i]))
        if i in selected:
            selected[i].kinds.append(kind)
        else:
            selected[i] = Picked(i, float(f.times[i]), score, [kind])

    if p.manual:
        for i, c in candidates.items():
            if i in selected:
                selected[i].kinds.extend(c.kinds)
            else:
                selected[i] = c
    else:
        duration = float(f.times[-1] - f.times[0]) if n > 1 else 0.0
        fps = (n - 1) / duration if duration > 0 else 1.0
        gap = max(1, round(0.5 * duration / max(p.frames_max, 1) * fps))
        for c in sorted(candidates.values(), key=lambda c: c.score, reverse=True):
            if len(selected) >= p.frames_max:
                break
            if c.index in selected:
                selected[c.index].kinds.extend(c.kinds)
                continue
            if all(abs(c.index - j) >= gap for j in selected):
                selected[c.index] = c
        _fill(selected, motion, f.times, min(p.frames_min, n))

    frames = [selected[i] for i in sorted(selected)]
    return SelectionResult(frames, motion, ref, len(candidates), end_ref)


def frame_scope_subset(frames: list[dict], k: int) -> list[dict]:
    """Trigger frame + the best scoring other frames, ``k`` in total, in time order."""
    trigger = [fr for fr in frames if "trigger" in fr["kinds"]]
    others = sorted((fr for fr in frames if "trigger" not in fr["kinds"]), key=lambda fr: fr["score"], reverse=True)
    chosen = (trigger + others)[: max(k, 1)]
    return sorted(chosen, key=lambda fr: fr["t"])
