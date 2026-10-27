"""Structured outputs requested from the model (JSON schema → Ollama ``format``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FocusQuestion(BaseModel):
    id: str = Field(description="Q1, Q2, ...")
    question: str


class ConditionSpecOut(BaseModel):
    title: str = Field(description="At most 6 words")
    scope: Literal["frame", "event", "history"]
    scope_rationale: str
    focus_questions: list[FocusQuestion]
    decision_criteria: str


class FocusAnswer(BaseModel):
    id: str
    answer: Literal["yes", "no", "unknown"]
    evidence: str
    frame: int = Field(description="Frame number that supports the answer, -1 if none")


class ObservationOut(BaseModel):
    description: str
    focus_answers: list[FocusAnswer]
    best_frame: int


class FocusResult(BaseModel):
    id: str
    status: Literal["met", "not_met", "unknown"]
    evidence: str


class SingleDecisionOut(BaseModel):
    decision: Literal["YES", "NO", "UNCERTAIN"]
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    focus_results: list[FocusResult]
    key_frame: int


class HistoryDecisionOut(BaseModel):
    decision: Literal["YES", "PENDING", "NO"]
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    focus_results: list[FocusResult]
    key_event_no: int
    key_frame: int


class ClosingDecisionOut(BaseModel):
    decision: Literal["YES", "NO", "UNCERTAIN"]
    confidence: float = Field(ge=0, le=1)
    reasoning: str
    focus_results: list[FocusResult]
    key_event_no: int
    key_frame: int


class EpisodeSummaryOut(BaseModel):
    summary: str


def inline_schema(model: type[BaseModel]) -> dict:
    """JSON schema with ``$defs`` references inlined (simpler for grammar-constrained decoding)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].split("/")[-1]])
            # drop the schema annotation "title" (a string), keep a property called "title" (a dict)
            return {k: resolve(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)
