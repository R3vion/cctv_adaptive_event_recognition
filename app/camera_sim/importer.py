"""Import of an uploaded MP4 as camera events.

Flow: ``create`` stages the upload and plans the 10 s segments (the import page then
shows them), ``commit`` writes the source video, the MJPEG chunks, the trigger frames
and the ``events.db`` rows.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable

from pydantic import BaseModel

from app.camera_sim import store
from app.camera_sim.timestamps import parse_iso, segment_starts, to_display, to_iso
from app.config import get_settings
from app.db.connection import now_iso
from app.media import mjpeg
from app.media.ffmpeg import FFmpegError, VideoInfo, probe, transcode_mjpeg_chunks

log = logging.getLogger(__name__)

CAMERA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SEGMENT_SECONDS = 10.0
SESSION_TTL_S = 3 * 3600


class ImportRejected(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Segment:
    index: int
    start_s: float
    duration_s: float
    first_frame: int  # frame index in the source at its own fps
    frame_count: int


@dataclass
class ImportSession:
    id: str
    filename: str
    staged_path: Path
    info: VideoInfo
    segments: list[Segment]
    created_at: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. change signals (app.selection)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "suggested_camera_id": suggest_camera_id(self.filename),
            "video": {"duration_s": round(self.info.duration_s, 3), "fps": round(self.info.fps, 3),
                      "width": self.info.width, "height": self.info.height},
            "segments": [
                {"index": s.index, "start_s": round(s.start_s, 3), "duration_s": round(s.duration_s, 3),
                 "default_trigger_s": round(s.duration_s / 2, 3)}
                for s in self.segments
            ],
        }


class SegmentChoice(BaseModel):
    index: int
    trigger_offset_s: float | None = None


class CommitRequest(BaseModel):
    camera_id: str
    start: str  # naive local ISO timestamp of the first segment
    segments: list[SegmentChoice] = []


def suggest_camera_id(filename: str) -> str:
    cid = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(filename).stem).strip("_")[:64]
    return cid or "camera"


def plan_segments(info: VideoInfo, seconds: float = SEGMENT_SECONDS) -> list[Segment]:
    """10 s segments; the remainder is always kept as a shorter last segment."""
    per = max(1, round(seconds * info.fps))
    total = info.frame_count
    segments, start = [], 0
    while start < total:
        n = min(per, total - start)
        segments.append(Segment(len(segments), start / info.fps, n / info.fps, start, n))
        start += n
    return segments


class ImportManager:
    def __init__(self) -> None:
        self._sessions: dict[str, ImportSession] = {}
        self._lock = threading.Lock()
        self.on_create: list[Callable[[ImportSession], None]] = []
        self.on_commit: list[Callable[[ImportSession, dict, dict[int, SegmentChoice]], None]] = []

    @staticmethod
    def staging_root() -> Path:
        return get_settings().data_path / "staging"

    # -- sessions -----------------------------------------------------------
    def create(self, filename: str, fileobj: BinaryIO) -> ImportSession:
        self.expire_old()
        name = Path(filename or "").name
        if not name.lower().endswith(".mp4"):
            raise ImportRejected("Only .mp4 files can be imported.")
        if store.source_exists(name):
            raise ImportRejected(f"'{name}' has already been imported.", 409)
        sid = uuid.uuid4().hex
        folder = self.staging_root() / sid
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        with path.open("wb") as out:
            shutil.copyfileobj(fileobj, out, length=4 << 20)
        try:
            info = probe(path)
        except FFmpegError as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise ImportRejected(f"Cannot read the video: {exc}") from exc
        session = ImportSession(sid, name, path, info, plan_segments(info))
        try:
            for hook in self.on_create:
                hook(session)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        with self._lock:
            self._sessions[sid] = session
        return session

    def get(self, sid: str) -> ImportSession:
        with self._lock:
            session = self._sessions.get(sid)
        if session is None:
            raise ImportRejected("Import session not found or expired.", 404)
        return session

    def discard(self, sid: str) -> None:
        with self._lock:
            session = self._sessions.pop(sid, None)
        if session is not None:
            shutil.rmtree(session.staged_path.parent, ignore_errors=True)

    def expire_old(self) -> None:
        now = time.time()
        with self._lock:
            old = [sid for sid, s in self._sessions.items() if now - s.created_at > SESSION_TTL_S]
        for sid in old:
            self.discard(sid)

    # -- commit -------------------------------------------------------------
    def commit(self, sid: str, req: CommitRequest) -> dict:
        session = self.get(sid)
        with session.lock:
            result = self._commit(session, req)
        choices = {c.index: c for c in req.segments}
        for hook in self.on_commit:
            try:
                hook(session, result, choices)
            except Exception:  # the import itself is done; hooks are best effort
                log.exception("import commit hook failed")
        self.discard(sid)
        return result

    def _commit(self, session: ImportSession, req: CommitRequest) -> dict:
        camera_id = req.camera_id.strip()
        if not CAMERA_ID_RE.match(camera_id):
            raise ImportRejected("Camera id may contain letters, digits, '_' and '-' (max 64).")
        try:
            start = parse_iso(req.start)
        except ValueError as exc:
            raise ImportRejected("Invalid start timestamp.") from exc

        settings = get_settings()
        data_dir = settings.data_path
        info = session.info
        choices = {c.index: c for c in req.segments}
        created: list[Path] = []
        src_rel = Path("source") / session.filename
        src_abs = data_dir / src_rel

        conn = store.write_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")  # serialises numbering between imports
            if conn.execute("SELECT 1 FROM sources WHERE filename = ?", (session.filename,)).fetchone():
                raise ImportRejected(f"'{session.filename}' has already been imported.", 409)
            last = store.last_event(conn, camera_id)
            if last and start <= parse_iso(last["timestamp_iso"]):
                raise ImportRejected(
                    f"The start must be later than the camera's last event ({last['timestamp']}).")
            first_no = last["event_no"] + 1 if last else 1

            src_abs.parent.mkdir(parents=True, exist_ok=True)
            if src_abs.exists():
                raise ImportRejected(f"{src_rel.as_posix()} already exists.", 409)
            shutil.copy2(session.staged_path, src_abs)
            created.append(src_abs)

            cam_dir = data_dir / "processed" / camera_id
            chunk_paths: list[Path] = []

            def open_chunk(i: int) -> BinaryIO:
                name = f"chunk_{first_no + i:03d}"
                folder = cam_dir / name
                folder.mkdir(parents=True, exist_ok=False)
                created.append(folder)
                path = folder / f"{name}.mjpeg"
                chunk_paths.append(path)
                return path.open("wb")

            written = transcode_mjpeg_chunks(src_abs, info.fps, [s.frame_count for s in session.segments],
                                             open_chunk)

            uploaded_at = now_iso()
            cur = conn.execute(
                "INSERT INTO sources (filename, camera_id, source_path, duration_s, fps, width, height, uploaded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (session.filename, camera_id, src_rel.as_posix(), info.duration_s, info.fps,
                 info.width, info.height, uploaded_at),
            )
            source_id = cur.lastrowid
            stamps = segment_starts(start, len(session.segments))
            events = []
            for seg, n_frames, clip in zip(session.segments, written, chunk_paths):
                if n_frames == 0:
                    continue
                event_no = first_no + seg.index
                duration = n_frames / info.fps
                choice = choices.get(seg.index)
                wanted = choice.trigger_offset_s if choice and choice.trigger_offset_s is not None else duration / 2
                idx = mjpeg.index_at(min(max(wanted, 0.0), duration), info.fps, n_frames)
                trigger_path = clip.parent / "trigger_frame.jpg"
                trigger_path.write_bytes(mjpeg.read_frame(clip, idx))
                ev = {
                    "event_id": f"{camera_id}#{event_no:03d}",
                    "camera_id": camera_id,
                    "event_no": event_no,
                    "segment_index": seg.index,
                    "source_offset_s": round(seg.start_s, 3),
                    "timestamp": to_display(stamps[seg.index]),
                    "timestamp_iso": to_iso(stamps[seg.index]),
                    "duration_s": round(duration, 3),
                    "fps": info.fps,
                    "frame_count": n_frames,
                    "clip_path": clip.relative_to(data_dir).as_posix(),
                    "trigger_frame_path": trigger_path.relative_to(data_dir).as_posix(),
                    "trigger_offset_s": round(idx / info.fps, 3),
                }
                conn.execute(
                    """INSERT INTO camera_events (event_id, camera_id, event_no, source_id, source_offset_s,
                           timestamp, timestamp_iso, duration_s, fps, frame_count, width, height, clip_path,
                           trigger_frame_path, trigger_offset_s, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ev["event_id"], camera_id, event_no, source_id, ev["source_offset_s"], ev["timestamp"],
                     ev["timestamp_iso"], ev["duration_s"], info.fps, n_frames, info.width, info.height,
                     ev["clip_path"], ev["trigger_frame_path"], ev["trigger_offset_s"], uploaded_at),
                )
                events.append(ev)
            if not events:
                raise ImportRejected("No frames could be decoded from the video.")
            conn.commit()
        except BaseException:
            conn.rollback()
            for path in reversed(created):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            raise
        finally:
            conn.close()
        log.info("imported %s as %s (%d events)", session.filename, camera_id, len(events))
        return {"camera_id": camera_id, "source_id": source_id, "source_path": src_rel.as_posix(),
                "events": events}


manager = ImportManager()
