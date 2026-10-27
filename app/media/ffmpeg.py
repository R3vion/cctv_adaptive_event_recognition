"""Thin wrappers around the ffmpeg / ffprobe command line tools."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

import numpy as np

from app.config import get_settings

SOI_BOUNDARY = b"\xff\xd9\xff\xd8"  # end of one JPEG immediately followed by the next


class FFmpegError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoInfo:
    duration_s: float
    fps: float
    width: int
    height: int
    codec: str = ""

    @property
    def frame_count(self) -> int:
        return max(1, round(self.duration_s * self.fps))


def _ffmpeg() -> str:
    return get_settings().ffmpeg_bin


def _run(args: list[str], *, input_bytes: bytes | None = None, timeout: float = 600) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(args, input=input_bytes, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise FFmpegError(f"binary not found: {args[0]}") from exc
    if proc.returncode != 0:
        raise FFmpegError(proc.stderr.decode("utf-8", "replace")[-2000:])
    return proc


def _parse_rate(rate: str) -> float:
    if not rate or rate == "0/0":
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        return float(num) / float(den) if float(den) else 0.0
    return float(rate)


def probe(path: Path) -> VideoInfo:
    """Duration, fps and size of the first video stream."""
    settings = get_settings()
    try:
        proc = _run([
            settings.ffprobe_bin, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,duration",
            "-show_entries", "format=duration", "-of", "json", str(path),
        ], timeout=60)
    except FFmpegError as exc:
        if "binary not found" not in str(exc):
            raise
        return _probe_with_ffmpeg(path)
    data = json.loads(proc.stdout or b"{}")
    streams = data.get("streams") or []
    if not streams:
        raise FFmpegError("no video stream")
    st = streams[0]
    fps = _parse_rate(st.get("avg_frame_rate", "")) or _parse_rate(st.get("r_frame_rate", ""))
    duration = float(data.get("format", {}).get("duration") or st.get("duration") or 0)
    if duration <= 0 or fps <= 0:
        raise FFmpegError("could not determine duration / fps")
    return VideoInfo(duration, fps, int(st["width"]), int(st["height"]), st.get("codec_name", ""))


def _probe_with_ffmpeg(path: Path) -> VideoInfo:
    """Fallback when ffprobe is not installed: parse ``ffmpeg -i`` output."""
    try:
        proc = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, timeout=60)
    except FileNotFoundError as exc:
        raise FFmpegError(f"binary not found: {_ffmpeg()}") from exc
    text = proc.stderr.decode("utf-8", "replace")
    dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    video = re.search(r"Stream #\S+.*?Video:\s*(\w+).*?(\d{2,5})x(\d{2,5})", text)
    fps = re.search(r"([\d.]+)\s*fps", text) or re.search(r"([\d.]+)\s*tbr", text)
    if not (dur and video and fps):
        raise FFmpegError("could not parse video information")
    h, m, s = dur.groups()
    duration = int(h) * 3600 + int(m) * 60 + float(s)
    return VideoInfo(duration, float(fps.group(1)), int(video.group(2)), int(video.group(3)), video.group(1))


def scaled_size(width: int, height: int, long_side: int) -> tuple[int, int]:
    """Size with the long side limited to ``long_side`` (even numbers, aspect kept)."""
    if max(width, height) <= long_side:
        w, h = width, height
    elif width >= height:
        w, h = long_side, round(height * long_side / width)
    else:
        w, h = round(width * long_side / height), long_side
    return max(2, w - w % 2), max(2, h - h % 2)


def decode_frames(path: Path, fps: float, size: tuple[int, int]) -> Iterator[np.ndarray]:
    """Yield BGR frames resampled to ``fps`` and scaled to ``size`` (w, h)."""
    w, h = size
    args = [_ffmpeg(), "-v", "error", "-i", str(path), "-an",
            "-vf", f"fps={fps:.6f},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    frame_bytes = w * h * 3
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=frame_bytes * 4)
    except FileNotFoundError as exc:
        raise FFmpegError(f"binary not found: {args[0]}") from exc
    assert proc.stdout is not None
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        stderr = proc.stderr.read() if proc.stderr else b""
        proc.wait()
    if proc.returncode not in (0, None) and proc.returncode != -13:
        raise FFmpegError(stderr.decode("utf-8", "replace")[-2000:])


def transcode_mjpeg_chunks(
    path: Path,
    fps: float,
    frames_per_chunk: list[int],
    open_chunk: Callable[[int], BinaryIO],
    quality: int = 3,
) -> list[int]:
    """Re-encode ``path`` as MJPEG at constant ``fps`` and split it into chunk files.

    ``frames_per_chunk[i]`` frames go into the file returned by ``open_chunk(i)``.
    Returns the number of frames actually written per chunk.
    """
    args = [_ffmpeg(), "-v", "error", "-i", str(path), "-an", "-vf", f"fps={fps:.6f}",
            "-c:v", "mjpeg", "-q:v", str(quality), "-f", "mjpeg", "-"]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        raise FFmpegError(f"binary not found: {args[0]}") from exc
    assert proc.stdout is not None
    written = [0] * len(frames_per_chunk)
    chunk_idx, current = 0, open_chunk(0) if frames_per_chunk else None

    def emit(jpeg: bytes) -> None:
        nonlocal chunk_idx, current
        while chunk_idx < len(frames_per_chunk) and written[chunk_idx] >= frames_per_chunk[chunk_idx]:
            current.close()
            chunk_idx += 1
            current = open_chunk(chunk_idx) if chunk_idx < len(frames_per_chunk) else None
        if current is None:
            return  # more frames than planned: drop the surplus
        current.write(jpeg)
        written[chunk_idx] += 1

    buf = b""
    try:
        while True:
            data = proc.stdout.read(1 << 20)
            if not data:
                break
            buf += data
            while True:
                cut = buf.find(SOI_BOUNDARY)
                if cut < 0:
                    break
                emit(buf[: cut + 2])
                buf = buf[cut + 2:]
        if buf:
            emit(buf)
    finally:
        if current is not None:
            current.close()
        proc.stdout.close()
        stderr = proc.stderr.read() if proc.stderr else b""
        proc.wait()
    if proc.returncode != 0:
        raise FFmpegError(stderr.decode("utf-8", "replace")[-2000:])
    return written


def mjpeg_to_mp4(src: Path, dst: Path, fps: float) -> None:
    """H.264 preview of an MJPEG clip (browser playable)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".tmp.mp4")
    _run([_ffmpeg(), "-v", "error", "-y", "-f", "mjpeg", "-framerate", f"{fps:.6f}", "-i", str(src),
          "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-c:v", "libx264", "-preset", "veryfast",
          "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp)])
    tmp.replace(dst)
