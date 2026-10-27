"""Per-frame change features.

Frames arrive already downscaled (``signal_side``).  For every frame we keep a blurred
grey image and an HSV colour histogram; the frame-to-frame curves are computed here,
the "change vs. last keyframe" curve depends on the threshold and is computed by the
selector.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np

HIST_BINS = (30, 32)  # hue, saturation
FLOW_MIN_PX = 1.0     # optical-flow magnitude that counts as movement


@dataclass
class Features:
    times: np.ndarray         # (n,) seconds from the start of the sequence
    gray: np.ndarray          # (n, h, w) uint8, blurred
    hist: np.ndarray          # (n, bins) float32, normalised
    motion_pixel: np.ndarray  # (n,) % changed pixels vs previous frame (or moving pixels)
    motion_hist: np.ndarray   # (n,) histogram distance vs previous frame, 0-100

    def __len__(self) -> int:
        return len(self.times)

    def slice(self, start: int, end: int) -> "Features":
        return Features(self.times[start:end] - self.times[start], self.gray[start:end], self.hist[start:end],
                        self.motion_pixel[start:end], self.motion_hist[start:end])


def prepare(frame_bgr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    gray = cv2.GaussianBlur(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, list(HIST_BINS), [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return gray, hist.flatten().astype(np.float32)


def pixel_change(a: np.ndarray, b: np.ndarray, delta: int) -> float:
    """Percentage of pixels whose grey value differs by more than ``delta``."""
    return float(np.count_nonzero(cv2.absdiff(a, b) > delta)) * 100.0 / a.size


def hist_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Bhattacharyya distance of two histograms scaled to 0-100."""
    return float(cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA)) * 100.0


def moving_pixels(prev: np.ndarray, cur: np.ndarray) -> float:
    flow = cv2.calcOpticalFlowFarneback(prev, cur, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    mag = np.linalg.norm(flow, axis=2)
    return float(np.count_nonzero(mag > FLOW_MIN_PX)) * 100.0 / mag.size


def extract(frames: Iterable[np.ndarray], fps: float, *, mode: str = "pixel_hist", pixel_delta: int = 25,
            on_frame=None) -> Features:
    """Features of a frame sequence sampled at ``fps``.

    ``on_frame(index, frame)`` is called for every frame (used to build thumbnails).
    """
    grays, hists, mp, mh = [], [], [], []
    for i, frame in enumerate(frames):
        gray, hist = prepare(frame)
        if grays:
            if mode == "optical_flow":
                mp.append(moving_pixels(grays[-1], gray))
            else:
                mp.append(pixel_change(gray, grays[-1], pixel_delta))
            mh.append(hist_distance(hist, hists[-1]))
        else:
            mp.append(0.0)
            mh.append(0.0)
        grays.append(gray)
        hists.append(hist)
        if on_frame is not None:
            on_frame(i, frame)
    if not grays:
        raise ValueError("no frames decoded")
    n = len(grays)
    return Features(
        times=np.arange(n, dtype=np.float64) / fps,
        gray=np.stack(grays),
        hist=np.stack(hists),
        motion_pixel=np.asarray(mp, dtype=np.float64),
        motion_hist=np.asarray(mh, dtype=np.float64),
    )
