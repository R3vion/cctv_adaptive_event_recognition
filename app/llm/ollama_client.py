"""Ollama chat client: structured JSON output, retries, repair request, call log.

Images are sent in original resolution when they fit; otherwise every image of the
call is scaled down by the same factor so that the images take at most
``IMAGE_CONTEXT_SHARE`` of ``num_ctx`` (measured: ~1 token per 1000 pixels).
"""

from __future__ import annotations

import base64
import json
import logging
import math
import re
import threading
import time
from dataclasses import dataclass
from typing import TypeVar
from urllib.parse import urlsplit

import cv2
import httpx
import numpy as np
from pydantic import BaseModel, ValidationError

from app.analytics import repo
from app.config import get_settings
from app.llm.schemas import inline_schema

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

PIXELS_PER_TOKEN = 1000.0
IMAGE_CONTEXT_SHARE = 0.75
CHARS_PER_TOKEN = 3.5
NUM_PREDICT = 8192


class LLMError(RuntimeError):
    def __init__(self, message: str, call_id: int | None = None):
        super().__init__(message)
        self.call_id = call_id


@dataclass
class ImageIn:
    label: str
    jpeg: bytes


def fit_images(images: list[ImageIn], text_chars: int, num_ctx: int, max_side: int) -> tuple[list[bytes], list[dict]]:
    """Resize images so they fit the context; returns JPEG bytes and metadata for the log."""
    decoded = [cv2.imdecode(np.frombuffer(im.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR) for im in images]
    sizes = [(d.shape[1], d.shape[0]) if d is not None else (0, 0) for d in decoded]
    scales = []
    for w, h in sizes:
        s = 1.0
        if max_side and max(w, h) > max_side:
            s = max_side / max(w, h)
        scales.append(s)
    budget = IMAGE_CONTEXT_SHARE * num_ctx - text_chars / CHARS_PER_TOKEN
    tokens = sum(w * s * h * s / PIXELS_PER_TOKEN for (w, h), s in zip(sizes, scales))
    fit = 1.0
    if images and budget > 0 and tokens > budget:
        fit = math.sqrt(budget / tokens)
    out, meta = [], []
    for im, img, (w, h), s in zip(images, decoded, sizes, scales):
        f = s * fit
        if img is not None and f < 0.999:
            nw, nh = max(1, round(w * f)), max(1, round(h * f))
            data = cv2.imencode(".jpg", cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA),
                                [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()
        else:
            nw, nh, data = w, h, im.jpeg
        out.append(data)
        meta.append({"label": im.label, "original": [w, h], "sent": [nw, nh], "bytes": len(data)})
    return out, meta


def redact(text: str) -> str:
    """Error texts reach the browser and the call log; the Ollama address stays on the server."""
    url = get_settings().ollama_url
    parts = urlsplit(url)
    for secret in (url, parts.netloc, parts.hostname):
        if secret:
            text = text.replace(secret, "<ollama>")
    return text


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start >= 0 and end > start else text


class OllamaClient:
    def __init__(self) -> None:
        self._status_cache: tuple[float, dict] | None = None
        self._lock = threading.Lock()

    # -- status -------------------------------------------------------------------
    def status(self, force: bool = False) -> dict:
        """Reachability as the browser sees it: no address, only what the backend found."""
        with self._lock:
            if not force and self._status_cache and time.time() - self._status_cache[0] < 10:
                return self._status_cache[1]
        s = get_settings()
        info = {"model": s.ollama_model, "ok": False, "model_available": False, "version": None, "error": None}
        try:
            with httpx.Client(timeout=5) as client:
                info["version"] = client.get(f"{s.ollama_url}/api/version").json().get("version")
                tags = client.get(f"{s.ollama_url}/api/tags").json().get("models", [])
            info["ok"] = True
            info["model_available"] = any(m.get("name") == s.ollama_model or m.get("model") == s.ollama_model
                                          for m in tags)
        except Exception as exc:  # noqa: BLE001 - any failure means "offline"
            info["error"] = redact(str(exc))[:200]
        with self._lock:
            self._status_cache = (time.time(), info)
        return info

    # -- chat ---------------------------------------------------------------------
    def chat(self, purpose: str, system: str, user: str, schema: type[T], *, think: bool,
             images: list[ImageIn] | None = None) -> tuple[T, int]:
        """One structured call. Returns the parsed object and the llm_calls row id."""
        s = get_settings()
        images = images or []
        jpegs, meta = fit_images(images, len(system) + len(user), int(s.ollama_num_ctx), int(s.vlm_max_image_side))
        options = {"temperature": 0.0, "num_ctx": int(s.ollama_num_ctx), "num_predict": NUM_PREDICT}
        message = {"role": "user", "content": user}
        if jpegs:
            message["images"] = [base64.b64encode(j).decode("ascii") for j in jpegs]
        body = {
            "model": s.ollama_model,
            "messages": [{"role": "system", "content": system}, message],
            "stream": False,
            "think": bool(think),
            "format": inline_schema(schema),
            "options": options,
            "keep_alive": s.ollama_keep_alive,
        }
        record = {"purpose": purpose, "model": s.ollama_model, "think": think, "system": system, "prompt": user,
                  "images": meta, "options": {**options, "keep_alive": s.ollama_keep_alive}}

        started = time.time()
        data, attempts, error = None, 0, None
        retries = int(s.llm_retries)
        while attempts <= retries:
            attempts += 1
            try:
                with httpx.Client(timeout=httpx.Timeout(float(s.ollama_timeout_s), connect=10)) as client:
                    resp = client.post(f"{s.ollama_url}/api/chat", json=body)
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {resp.status_code}: {resp.text[:300]}", request=resp.request,
                                                response=resp)
                data = resp.json()
                if resp.status_code >= 400 or data.get("error"):
                    error = redact(f"HTTP {resp.status_code}: {data.get('error') or resp.text[:300]}")
                    data = None
                    break  # client errors are not retried
                error = None
                break
            except (httpx.HTTPError, ValueError) as exc:
                error = redact(f"{type(exc).__name__}: {exc}")[:500]
                if attempts <= retries:
                    time.sleep(min(2 ** (attempts - 1), 8))
        record.update(attempts=attempts, duration_ms=int((time.time() - started) * 1000))

        if data is None:
            record["error"] = error
            call_id = repo.insert_llm_call(record)
            raise LLMError(f"Ollama call failed after {attempts} attempt(s): {error}", call_id)

        msg = data.get("message") or {}
        content = msg.get("content") or ""
        record.update(response=content, thinking=msg.get("thinking"),
                      prompt_tokens=data.get("prompt_eval_count"), output_tokens=data.get("eval_count"))
        try:
            parsed = schema.model_validate_json(_extract_json(content))
        except (ValidationError, ValueError) as exc:
            record["error"] = f"invalid JSON: {str(exc)[:300]}"
            call_id = repo.insert_llm_call(record)
            if purpose == "repair":
                raise LLMError("the model did not return valid JSON (repair failed too)", call_id) from exc
            parsed, _ = self._repair(content, str(exc), schema)
            return parsed, call_id
        record["parsed"] = parsed.model_dump()
        return parsed, repo.insert_llm_call(record)

    def _repair(self, content: str, problem: str, schema: type[T]) -> tuple[T, int]:
        system = "You fix malformed JSON. Return only a JSON object that matches the required schema."
        user = (f"This model output does not match the required JSON schema.\nProblem: {problem[:600]}\n\n"
                f"Output:\n{content[:6000]}\n\nReturn the corrected JSON object only.")
        return self.chat("repair", system, user, schema, think=False)


client = OllamaClient()


def to_prompt_list(lines: list[str]) -> str:
    return "\n".join(lines) if lines else "(none)"


def compact(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
