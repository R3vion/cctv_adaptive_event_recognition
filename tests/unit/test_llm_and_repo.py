from __future__ import annotations

import re
import sqlite3

import cv2
import numpy as np
import pytest

from app.analytics import condition_analyzer as ca
from app.analytics import events_source, repo
from app.config import get_settings
from app.db.connection import connect
from app.llm import prompts
from app.llm.ollama_client import ImageIn, _extract_json, fit_images
from app.llm.schemas import ConditionSpecOut, HistoryDecisionOut, inline_schema


def jpeg(w: int, h: int) -> bytes:
    return cv2.imencode(".jpg", np.zeros((h, w, 3), dtype=np.uint8))[1].tobytes()


def test_images_keep_original_size_when_they_fit():
    _, meta = fit_images([ImageIn("a", jpeg(1412, 1080))] * 5, 2000, 32768, 0)
    assert all(m["sent"] == [1412, 1080] for m in meta)


def test_images_are_scaled_uniformly_to_fit_the_context():
    _, meta = fit_images([ImageIn("a", jpeg(1412, 1080))] * 30, 4000, 32768, 0)
    w, h = meta[0]["sent"]
    assert w < 1412 and all(m["sent"] == [w, h] for m in meta)
    tokens = sum(m["sent"][0] * m["sent"][1] / 1000 for m in meta)
    assert tokens <= 0.75 * 32768


def test_max_image_side_setting_is_respected():
    _, meta = fit_images([ImageIn("a", jpeg(1920, 1080))], 100, 32768, 800)
    assert max(meta[0]["sent"]) == 800


def test_inline_schema_keeps_title_property_and_has_no_refs():
    schema = inline_schema(ConditionSpecOut)
    assert "title" in schema["properties"]
    assert "$ref" not in str(schema)
    assert "items" in inline_schema(HistoryDecisionOut)["properties"]["focus_results"]


def test_extract_json_from_fenced_answer():
    assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_every_prompt_template_has_system_and_user_part():
    for t in prompts.all_templates():
        assert t["system"] and t["user"], t["name"]
    _, user = prompts.render("decision_single", prompt="p", criteria="c", questions="q", event_id="e",
                             timestamp="t", observation="o")
    assert not re.search(r"\{\{\w+\}\}", user)


def test_condition_versions(data_dir):
    repo.init()
    spec = ca.validate({"prompt": "Alert me if you see smoke.", "scope": "frame",
                        "focus_questions": [{"question": "Is smoke visible?"}], "decision_criteria": "Q1 yes.",
                        "suggestion": {"scope": "frame"}})
    cond = repo.create_condition("Cam", spec)
    assert cond["version"] == 1 and cond["scope"] == "frame" and not cond["scope_overridden"]
    edited = ca.validate({**spec, "scope": "event", "suggestion": {"scope": "frame"}})
    cond2 = repo.new_version(cond["id"], edited)
    assert cond2["version"] == 2 and cond2["scope_overridden"]
    assert [v["version"] for v in repo.condition_versions(cond["id"])] == [2, 1]
    assert repo.get_version(cond["current_version_id"])["scope"] == "frame"  # old version is immutable


def test_spec_validation_rejects_bad_input():
    with pytest.raises(ca.SpecError):
        ca.validate({"prompt": "x", "scope": "sometimes", "focus_questions": ["q"], "decision_criteria": "c"})
    with pytest.raises(ca.SpecError):
        ca.validate({"prompt": "x", "scope": "frame", "focus_questions": [], "decision_criteria": "c"})


def test_analytics_cannot_write_events_db(data_dir):
    assert events_source.available()
    conn = connect(get_settings().events_db_path, readonly=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM camera_events")
    conn.close()
