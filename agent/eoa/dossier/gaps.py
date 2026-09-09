"""Gap tracking across dossier runs (PD-datasheet, 2026-09-09, LESSONS-1 item 4):
``docs/qa/content_review/LESSONS-fable-dossier.md`` finding 8 -- a hand-written second-round review
opens with "מעקב פערי המידע מסבב א'" (follow-up on round A's information gaps) and closes several of
them; the automated pipeline never re-visited what a previous run of the same product failed to
find.

Pure/data-in-data-out, like ``eoa.dossier.datasheet``/``programs`` (no import of
``eoa.dossier.corpus``/``plan`` -- ``plan`` imports this module). Operates directly on
``CorpusResult.previous`` (the raw ``product_dossiers`` DB row of the same ``product_key``,
already available with no extra query) rather than importing
``eoa.llm.schemas.product_dossier.ProductDossierOut`` (the schema file this task's brief excludes
from this lane's ownership) -- ``previous["data"]`` is that schema's own persisted JSON shape, read
here as a plain ``dict`` by field name only, so a schema change on the parallel LESSONS-2 lane can
only ever make a field silently absent (handled -- ``.get()`` throughout), never break an import.

Scope note (see ``docs/qa/content_review/LESSONS-1.md``): this module can only ever determine
CLOSED/OPEN status for a gap that already existed in the previous run (this run's own follow-up
topic either found something or didn't) -- classifying a gap as NEW requires this run's own final
``risks_and_gaps_he``, which only exists after ``eoa.dossier.extract.build_dossier`` runs, outside
this research-stage lane's file ownership. :func:`diff_new_gaps` is provided ready for the
LESSONS-2 lane to call once that final list exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Hard cap on how many previously-open gaps get their own follow-up research topic this run --
#: each one is a real ``investigate()`` call (network + LLM cost), so bounded the same way
#: ``eoa.dossier.plan``'s own per-topic budgets are.
MAX_GAP_FOLLOWUP_TOPICS = 6

#: Confidence floor for treating a follow-up topic's own investigation as having actually closed
#: the gap it was aimed at -- below this, "found something" is too weak to call the gap resolved.
_CLOSED_CONFIDENCE_FLOOR = 0.5


@dataclass(frozen=True)
class GapFollowupTopic:
    """A dynamically-generated extra research topic -- ``eoa.dossier.plan.run_plan`` wraps this
    into its own ``Topic`` dataclass (kept independent here so this module never needs to import
    ``eoa.dossier.plan``, which imports this module)."""

    key: str
    title_he: str
    question_he: str
    gap: str


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        try:
            parsed = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def extract_gaps_from_previous(previous: dict[str, Any] | None) -> list[str]:
    """Every open gap the previous dossier of the same product left behind, deduped in this
    priority order: (1) an explicit ``data.meta.gaps`` list (once persisted by the LESSONS-2 lane
    via :func:`gap_status`-shaped rows -- forward-compatible: absent today, simply contributes
    nothing until that write is wired up), (2) ``data.risks_and_gaps_he`` (the schema's own existing
    free-text gap list), (3) a ``data.specifications`` row whose ``value`` is null/empty -- an
    unfilled required spec is itself an information gap worth a targeted follow-up question, per
    LESSONS-fable-dossier's own emphasis on the specification table."""
    if not previous:
        return []
    data = _as_dict(previous.get("data"))
    if not data:
        return []
    gaps: list[str] = []
    seen: set[str] = set()

    def _add(text: str | None) -> None:
        t = (text or "").strip()
        if t and t not in seen:
            seen.add(t)
            gaps.append(t)

    meta = _as_dict(data.get("meta"))
    for entry in meta.get("gaps") or []:
        if isinstance(entry, dict):
            _add(entry.get("gap"))
        else:
            _add(str(entry) if entry else None)

    for gap in data.get("risks_and_gaps_he") or []:
        # risks_and_gaps_he is list[Sentence] (agent/eoa/llm/schemas/analysis.py) -- persisted as
        # {"text_he": ..., "cites": [...]} dicts, never plain strings. A live run against a real
        # previous dossier (docs/qa/content_review/LESSONS-1.md) caught this: stringifying the
        # whole dict produced an unreadable Python-repr gap text ("{'cites': [3, 4], 'text_he':
        # ...}") instead of the actual Hebrew sentence. Same extraction eoa.dossier.report's own
        # _previous_gap_texts uses for the identical field, kept independent rather than imported
        # per this module's own docstring (that function reads risks_and_gaps_he for a narrower
        # "what already counts as known" purpose, this one for "what to research next").
        if isinstance(gap, dict):
            _add(gap.get("text_he"))
        else:
            _add(str(gap) if gap else None)

    for row in data.get("specifications") or []:
        if not isinstance(row, dict):
            continue
        value = row.get("value")
        if value is None or not str(value).strip():
            label = row.get("parameter_he") or row.get("key") or ""
            if label:
                _add(f"מפרט חסר: {label}")

    return gaps


def gap_followup_question_he(product_name: str, vendor: str | None, gap: str) -> str:
    return (
        f"בהמשך לסקירה קודמת של {product_name} ({vendor or 'היצרן'}): מה ניתן למצוא כעת לגבי הפער "
        f"הבא שזוהה ולא נסגר בסבב הקודם? הפער: {gap}"
    )


def build_gap_followup_topics(
    gaps: list[str],
    *,
    product_name: str,
    vendor: str | None,
    max_gaps: int = MAX_GAP_FOLLOWUP_TOPICS,
) -> list[GapFollowupTopic]:
    """One :class:`GapFollowupTopic` per open gap, capped at ``max_gaps`` -- the extra "gap
    follow-up" topics the task brief asks for, ready for ``eoa.dossier.plan.run_plan`` to run
    exactly like any other topic (search + read + synthesize)."""
    out: list[GapFollowupTopic] = []
    for i, gap in enumerate(gaps[:max_gaps]):
        out.append(
            GapFollowupTopic(
                key=f"gap_followup_{i + 1}",
                title_he="מעקב פערים מסבב קודם",
                question_he=gap_followup_question_he(product_name, vendor, gap),
                gap=gap,
            )
        )
    return out


def gap_status(
    followup_topics: list[GapFollowupTopic],
    outcomes: dict[str, tuple[str, float, list[int]]],
) -> list[dict[str, Any]]:
    """``[{"gap", "status", "cites"}, ...]`` -- ``status`` is ``"closed"`` when the matching
    follow-up topic's own investigation outcome was ``"found"``/``"partial"`` at confidence >=
    :data:`_CLOSED_CONFIDENCE_FLOOR`, else ``"open"``. ``outcomes`` maps a followup topic's own
    ``key`` -> ``(outcome, confidence, cite_ns)`` -- the caller (``eoa.dossier.plan.run_plan``)
    already has exactly this from each topic's own ``TopicFinding``/``Investigation.result``."""
    rows: list[dict[str, Any]] = []
    for topic in followup_topics:
        outcome, confidence, cites = outcomes.get(topic.key, ("not_found", 0.0, []))
        closed = outcome in {"found", "partial"} and confidence >= _CLOSED_CONFIDENCE_FLOOR
        rows.append(
            {
                "gap": topic.gap,
                "status": "closed" if closed else "open",
                "cites": cites,
            }
        )
    return rows


def diff_new_gaps(previous_gaps: list[str], current_gaps: list[str]) -> list[dict[str, Any]]:
    """``[{"gap", "status": "new", "cites": []}, ...]`` for every entry in ``current_gaps`` that
    wasn't already in ``previous_gaps``. Provided for the LESSONS-2 lane (``eoa.dossier.report``/
    ``extract``, which computes this run's own final ``risks_and_gaps_he`` -- the "current_gaps"
    this needs) to merge alongside :func:`gap_status`'s closed/open rows into one
    ``CorpusResult.gap_status``-shaped list before persisting ``data.meta.gaps`` for the next run."""
    previous_set = {g.strip() for g in previous_gaps if g and g.strip()}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for gap in current_gaps:
        g = (gap or "").strip()
        if not g or g in previous_set or g in seen:
            continue
        seen.add(g)
        out.append({"gap": g, "status": "new", "cites": []})
    return out


__all__ = [
    "MAX_GAP_FOLLOWUP_TOPICS",
    "GapFollowupTopic",
    "build_gap_followup_topics",
    "diff_new_gaps",
    "extract_gaps_from_previous",
    "gap_followup_question_he",
    "gap_status",
]
