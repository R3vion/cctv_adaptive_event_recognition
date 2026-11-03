"""Runtime configuration.

Every setting has a default that can be overridden by an environment variable
(``.env``) with the same name in upper case.  Editable settings can also be
overridden from the Settings page; those overrides live in ``analytics.db``
(table ``settings``) and win over the environment.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")  # the project's .env, wherever the process was starter


@dataclass(frozen=True)
class SettingSpec:
    key: str
    type: type
    default: Any
    group: str
    label: str
    help: str = ""
    choices: tuple[str, ...] | None = None
    editable: bool = True
    minimum: float | None = None
    maximum: float | None = None
    hidden: bool = False  # the value never leaves the server (settings API sends None)

    @property
    def env_name(self) -> str:
        return self.key.upper()


SPECS: list[SettingSpec] = [
    # --- Ollama -----------------------------------------------------------
    SettingSpec("ollama_base_url", str, "http://localhost:11434", "Ollama", "Base URL",
                "Set in .env only. Every Ollama call is made by the backend, so the browser "
                "never needs – and never gets – this address.", editable=False, hidden=True),
    SettingSpec("ollama_model", str, "qwen3.6:27b", "Ollama", "Model"),
    SettingSpec("ollama_num_ctx", int, 32768, "Ollama", "Context window (num_ctx)", minimum=2048),
    SettingSpec("ollama_timeout_s", int, 300, "Ollama", "HTTP timeout per call (s)", minimum=10),
    SettingSpec("ollama_keep_alive", str, "30m", "Ollama", "keep_alive",
                "How long the model stays loaded after a call, e.g. 30m."),
    SettingSpec("think_condition", bool, True, "Ollama", "Thinking: condition analysis"),
    SettingSpec("think_observation", bool, False, "Ollama", "Thinking: visual observation"),
    SettingSpec("think_decision", bool, True, "Ollama", "Thinking: decisions"),
    SettingSpec("llm_retries", int, 3, "Ollama", "Retries per call", minimum=0, maximum=10),
    SettingSpec("vlm_max_image_side", int, 0, "Ollama", "Max image side sent to the VLM (px)",
                "0 = original resolution.", minimum=0),
    # --- Analysis ---------------------------------------------------------
    SettingSpec("analysis_mode", str, "auto", "Analysis", "Analysis start",
                "auto: the worker analyses new events immediately; manual: press Analyze.",
                choices=("auto", "manual")),
    SettingSpec("episode_gap_s", float, 15.0, "Analysis", "Episode gap (s)",
                "Two events of a camera belong to the same episode if their timestamps "
                "are at most this far apart.", minimum=1),
    SettingSpec("episode_idle_close_s", float, 30.0, "Analysis", "Idle close (s, wall clock)",
                "An open episode is closed when no new event arrived for this long.", minimum=1),
    SettingSpec("camera_memory_hours", float, 24.0, "Analysis", "Camera memory window (h)",
                "Summaries of earlier closed episodes of the same camera within this window "
                "are given to history decisions.", minimum=0),
    # --- Frame selection --------------------------------------------------
    SettingSpec("selection_mode", str, "pixel_hist", "Frame selection", "Change signal",
                "pixel_hist: changed-pixel ratio + colour histogram distance; "
                "optical_flow: Farneback flow for the frame-to-frame curve.",
                choices=("pixel_hist", "optical_flow")),
    SettingSpec("selection_threshold", float, 0.0, "Frame selection", "Default threshold (%)",
                "0 = auto: per video, median + k·1.4826·MAD of the frame-to-frame change "
                "(the video's own noise level plus a margin). Any other value is a fixed threshold.",
                minimum=0, maximum=100),
    SettingSpec("selection_auto_k", float, 6.0, "Frame selection", "Auto threshold sensitivity (k)",
                "Lower = more candidate frames.", minimum=0.5, maximum=50),
    SettingSpec("selection_w_pixel", float, 0.7, "Frame selection", "Weight: pixel change",
                minimum=0, maximum=1),
    SettingSpec("selection_w_hist", float, 0.3, "Frame selection", "Weight: histogram distance",
                minimum=0, maximum=1),
    SettingSpec("frames_min", int, 5, "Frame selection", "Min frames per segment", minimum=3),
    SettingSpec("frames_max", int, 30, "Frame selection", "Max frames per segment", minimum=3),
    SettingSpec("frame_scope_frames", int, 3, "Frame selection", "Frames for frame-scope prompts",
                "Trigger frame + the best scoring frames.", minimum=1),
    SettingSpec("signal_fps", float, 15.0, "Frame selection", "Signal sampling rate (fps)",
                "Candidate frames are evaluated on this grid (capped at the video fps).",
                minimum=1, maximum=60),
    SettingSpec("signal_side", int, 320, "Frame selection", "Signal resolution (px, long side)",
                minimum=64, maximum=1280),
    SettingSpec("pixel_delta", int, 25, "Frame selection", "Pixel change threshold (0-255)",
                "A pixel counts as changed when its blurred grey value differs by more than this.",
                minimum=1, maximum=255),
    # --- System (environment only) -----------------------------------------
    SettingSpec("data_dir", str, "./data", "System", "Data directory", editable=False),
    SettingSpec("ffmpeg_bin", str, "ffmpeg", "System", "ffmpeg binary", editable=False),
    SettingSpec("ffprobe_bin", str, "ffprobe", "System", "ffprobe binary", editable=False),
    SettingSpec("log_level", str, "INFO", "System", "Log level", editable=False),
]

SPEC_BY_KEY = {s.key: s for s in SPECS}


def _coerce(spec: SettingSpec, raw: Any) -> Any:
    if spec.type is bool:
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    value = spec.type(raw)
    if spec.choices and value not in spec.choices:
        raise ValueError(f"{spec.key} must be one of {spec.choices}")
    if spec.minimum is not None and value < spec.minimum:
        raise ValueError(f"{spec.key} must be >= {spec.minimum}")
    if spec.maximum is not None and value > spec.maximum:
        raise ValueError(f"{spec.key} must be <= {spec.maximum}")
    return value


def env_defaults() -> dict[str, Any]:
    values = {}
    for spec in SPECS:
        raw = os.environ.get(spec.env_name)
        values[spec.key] = spec.default if raw in (None, "") else _coerce(spec, raw)
    return values


class Settings:
    """Attribute access to the merged settings (env + overrides)."""

    def __init__(self, values: dict[str, Any], overridden: set[str]):
        self._values = values
        self.overridden = overridden

    def __getattr__(self, item: str) -> Any:
        try:
            return self._values[item]
        except KeyError as exc:
            raise AttributeError(item) from exc

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    @property
    def data_path(self) -> Path:
        return Path(self._values["data_dir"]).resolve()

    @property
    def events_db_path(self) -> Path:
        return self.data_path / "events.db"

    @property
    def analytics_db_path(self) -> Path:
        # tests may point the analytics side somewhere else while reading the real events.db
        override = self._values.get("analytics_db")
        return Path(override).resolve() if override else self.data_path / "analytics.db"

    @property
    def analytics_files_path(self) -> Path:
        override = self._values.get("analytics_files_dir")
        return Path(override).resolve() if override else self.data_path / "analytics"

    @property
    def ollama_url(self) -> str:
        return self._values["ollama_base_url"].rstrip("/")


_lock = threading.Lock()
_cached: Settings | None = None
_test_overrides: dict[str, Any] = {}


def _read_db_overrides(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {}
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    out = {}
    for key, value in rows:
        spec = SPEC_BY_KEY.get(key)
        if spec and spec.editable:
            try:
                out[key] = _coerce(spec, json.loads(value))
            except (ValueError, TypeError):
                continue
    return out


def get_settings() -> Settings:
    global _cached
    with _lock:
        if _cached is None:
            values = env_defaults()
            values.update(_test_overrides)
            db_over = _read_db_overrides(Path(values["data_dir"]).resolve() / "analytics.db")
            values.update(db_over)
            values.update(_test_overrides)
            _cached = Settings(values, set(db_over))
        return _cached


def reload_settings() -> Settings:
    global _cached
    with _lock:
        _cached = None
    return get_settings()


def set_test_overrides(**values: Any) -> None:
    """Force values regardless of env/DB (used by tests)."""
    _test_overrides.clear()
    _test_overrides.update(values)
    reload_settings()


def validate_override(key: str, raw: Any) -> Any:
    spec = SPEC_BY_KEY.get(key)
    if spec is None or not spec.editable:
        raise ValueError(f"{key} is not an editable setting")
    return _coerce(spec, raw)
