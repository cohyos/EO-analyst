"""Pydantic response models documenting the shapes in ``docs/API.md``.

These are intentionally not wired into route decorators as ``response_model``:
several routes (items, entities, graph, investigations) merge DB rows with
best-effort/adapter data whose optional fields vary by what has been
implemented so far, and strict response-model validation would either
silently drop fields or reject legitimate partial data. They exist here as
the documented, importable contract for the API surface and for anything
(tests, other modules) that wants a typed view of it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel


class ItemCard(BaseModel):
    id: int
    title: str | None = None
    url: str | None = None
    source_name: str | None = None
    published_at: dt.datetime | None = None
    lang: str | None = None
    domain: str | None = None
    subdomain: str | None = None
    report_kind: str | None = None
    trl: str | None = None
    geography: str | None = None
    score: int | None = None
    level: str | None = None
    triage_reason: str | None = None
    summary_he: str | None = None
    so_what_he: str | None = None
    entities_mentioned: list[str] = []
    tags: list[str] = []
    security_status: str | None = None
    dedup_of: int | None = None
    key_facts: list[str] = []
    uncertainty_he: str | None = None


class ItemDetail(ItemCard):
    clean_text: str | None = None
    events: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    investigations: list[dict[str, Any]] = []


class Report(BaseModel):
    id: int
    kind: str | None = None
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    path_docx: str | None = None
    path_md: str | None = None
    path_html: str | None = None
    qa_passed: bool | None = None
    created_at: dt.datetime | None = None
    headline_count: int = 0


class ReportDetail(Report):
    html: str | None = None
    open_points: list[dict[str, Any]] = []
    items_included: list[int] = []


class Entity(BaseModel):
    id: int
    name: str
    kind: str
    country: str | None = None
    aliases: list[str] = []
    focus: list[str] = []
    item_count: int = 0
    last_seen: dt.datetime | None = None


class EntityDetail(Entity):
    timeline: list[dict[str, Any]] = []
    neighbors: list[dict[str, Any]] = []


class GraphNode(BaseModel):
    id: int
    name: str | None = None
    kind: str | None = None
    country: str | None = None


class GraphEdge(BaseModel):
    src: int
    dst: int
    label: str
    item_id: int | None = None
    evidence: str | None = None


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class Investigation(BaseModel):
    job_id: int
    item_id: int | None = None
    question: str | None = None
    state: str
    rounds: int = 0
    queries: int = 0
    pages_read: int = 0
    outcome: str | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None


class InvestigationDetail(Investigation):
    log: list[dict[str, Any]] = []
    answer: dict[str, Any] | None = None


class Clarification(BaseModel):
    id: int
    kind: str | None = None
    question: str | None = None
    options: list[str] | None = None
    answer: str | None = None
    asked_at: dt.datetime | None = None
    timeout_at: dt.datetime | None = None
    assumed: bool = False


class SurveyQuestion(BaseModel):
    id: str
    type: Literal["choice", "scale", "open"]
    text_he: str
    options: list[str] | None = None


class Survey(BaseModel):
    id: int
    report_id: int | None = None
    questions: list[SurveyQuestion]
    answers: dict[str, Any] = {}


class Lesson(BaseModel):
    id: int
    kind: str | None = None
    text: str | None = None
    active: bool = True
    created_at: dt.datetime | None = None


class Job(BaseModel):
    id: int
    kind: str
    payload: dict[str, Any] | None = None
    state: str
    priority: int = 5
    attempts: int = 0
    not_before: dt.datetime | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None


class ErrorBody(BaseModel):
    code: str
    message_he: str
    detail: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody
