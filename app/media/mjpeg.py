"""Reading frames out of raw MJPEG clips (a plain concatenation of JPEG images)."""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import cv2
import numpy as np

SOI = b"\xff\xd8\xff"

_cache: OrderedDict[tuple[str, float], list[tuple[int, int]]] = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_SIZE = 64


def frame_offsets(data: bytes) -> list[tuple[int, int]]:
    """(start, end) byte ranges of the JPEG frames inside an MJPEG stream."""
    starts = []
    pos = data.find(SOI)
    while pos >= 0:
        # a new image starts right after the previous one's EOI marker (or at 0)
        if pos == 0 or data[pos - 2:pos] == b"\xff\xd9":
            starts.append(pos)
        pos = data.find(SOI, pos + 3)
    return [(s, starts[i + 1] if i + 1 < len(starts) else len(data)) for i, s in enumerate(starts)]


def _offsets_for(path: Path) -> tuple[bytes, list[tuple[int, int]]]:
    data = path.read_bytes()
    key = (str(path), path.stat().st_mtime)
    with _cache_lock:
        offsets = _cache.get(key)
        if offsets is not None:
            _cache.move_to_end(key)
            return data, offsets
    offsets = frame_offsets(data)
    with _cache_lock:
        _cache[key] = offsets
        while len(_cache) > _CACHE_SIZE:
            _cache.popitem(last=False)
    return data, offsets


def frame_count(path: Path) -> int:
    return len(_offsets_for(path)[1])


def read_frame(path: Path, index: int) -> bytes:
    """JPEG bytes of frame ``index`` (clamped to the clip)."""
    data, offsets = _offsets_for(path)
    if not offsets:
        raise ValueError(f"no frames in {path}")
    s, e = offsets[min(max(index, 0), len(offsets) - 1)]
    return data[s:e]


def read_frames(path: Path, indices: list[int]) -> list[bytes]:
    data, offsets = _offsets_for(path)
    last = len(offsets) - 1
    return [data[slice(*offsets[min(max(i, 0), last)])] for i in indices]


def index_at(t: float, fps: float, count: int) -> int:
    return min(max(round(t * fps), 0), max(count - 1, 0))


def iter_decoded(path: Path):
    """Yield decoded BGR frames of a clip."""
    data, offsets = _offsets_for(path)
    for s, e in offsets:
        img = cv2.imdecode(np.frombuffer(data[s:e], dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            yield img


def resize_jpeg(jpeg: bytes, max_side: int, quality: int = 90) -> bytes:
    """Downscale so the long side is at most ``max_side`` (0 = keep as is)."""
    if max_side <= 0:
        return jpeg
    img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return jpeg
    h, w = img.shape[:2]
    if max(h, w) <= max_side:
        return jpeg
    scale = max_side / max(h, w)
    img = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))), interpolation=cv2.INTER_AREA)
    ok, out = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return out.tobytes() if ok else jpeg
