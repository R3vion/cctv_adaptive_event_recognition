"""Prompt → ConditionSpec (scope, focus questions, decision criteria)."""

from __future__ import annotations

from app.config import get_settings
from app.llm import prompts
from app.llm.ollama_client import client
from app.llm.schemas import ConditionSpecOut

SCOPES = ("frame", "event", "history")


class SpecError(ValueError):
    pass


def analyze(prompt: str, *, think: bool | None = None) -> dict:
    """Ask the LLM for a proposal (not saved)."""
    prompt = prompt.strip()
    if not prompt:
        raise SpecError("The prompt is empty.")
    system, user = prompts.render("condition_analysis", prompt=prompt)
    think = get_settings().think_condition if think is None else think
    out, call_id = client.chat("condition", system, user, ConditionSpecOut, think=think)
    spec = {
        "prompt": prompt,
        "title": out.title.strip(),
        "scope": out.scope,
        "scope_rationale": out.scope_rationale.strip(),
        "focus_questions": [{"id": f"Q{i + 1}", "question": q.question.strip()} for i, q in enumerate(out.focus_questions)],
        "decision_criteria": out.decision_criteria.strip(),
        "llm_call_id": call_id,
    }
    spec["suggestion"] = {k: spec[k] for k in ("title", "scope", "scope_rationale", "focus_questions", "decision_criteria")}
    return spec


def validate(spec: dict) -> dict:
    """Normalise a (possibly user-edited) spec before saving."""
    prompt = str(spec.get("prompt", "")).strip()
    scope = str(spec.get("scope", "")).strip()
    questions = [str(q.get("question", "") if isinstance(q, dict) else q).strip() for q in spec.get("focus_questions", [])]
    questions = [q for q in questions if q]
    criteria = str(spec.get("decision_criteria", "")).strip()
    if not prompt:
        raise SpecError("The prompt is empty.")
    if scope not in SCOPES:
        raise SpecError(f"Scope must be one of {', '.join(SCOPES)}.")
    if not questions:
        raise SpecError("At least one focus question is needed.")
    if not criteria:
        raise SpecError("Decision criteria are needed.")
    suggestion = spec.get("suggestion") or None
    overridden = bool(suggestion) and suggestion.get("scope") != scope
    return {
        "prompt": prompt,
        "title": (str(spec.get("title") or "").strip() or prompt[:60]),
        "scope": scope,
        "focus_questions": [{"id": f"Q{i + 1}", "question": q} for i, q in enumerate(questions)],
        "decision_criteria": criteria,
        "scope_overridden": overridden or bool(spec.get("scope_overridden")),
        "suggestion": suggestion,
        "llm_call_id": spec.get("llm_call_id"),
    }
