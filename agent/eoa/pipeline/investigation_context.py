"""Item context for deep-search jobs -- built once, at enqueue time, from the item's own row.

Round-7 finding (docs/qa/loop/round_7_fixes.md, R7-investigations): golden jobs 47 (item 10,
entities ``AeroVironment / US Army``) and 70 (item 81, Anduril) ran with an empty ``context_he``
because the UI/API enqueue paths never filled it, and the triage path filled it *before* the
analyze stage had extracted ``entities_mentioned``, so the entities line was "—". The anchors
`eoa.search.deep_search.extract_anchors` parses from the "כותרת הפריט: / ישויות: / תקציר:" lines
therefore never reached the investigation. Every enqueue site now calls
:func:`item_context_he_from_db`, and the worker calls :func:`ensure_context_he` right before
``investigate()`` so a job queued with a stale/blank context is refreshed from the current row.
"""

from __future__ import annotations

import re
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_SEED_LINE_RE = re.compile(r"^זרע חיפוש באנגלית מוצע:.*$", re.M)
_ENTITIES_LINE_RE = re.compile(r"^ישויות:\s*(.*)$", re.M)
_EMPTY_ENTITIES = {"", "—", "-", "–"}
_ROW_COLUMNS = ("title", "entities_mentioned", "summary_he")


def format_item_context_he(
    title: str | None,
    entities: list[str] | None,
    summary_he: str | None,
    seed_en: str | None = None,
) -> str:
    """The canonical context block -- exactly the line shapes ``extract_anchors`` parses."""
    entities_str = ", ".join(e for e in (entities or []) if e) or "—"
    lines = [
        f"כותרת הפריט: {(title or '').strip()}",
        f"ישויות: {entities_str}",
        f"תקציר: {(summary_he or '').strip()}",
    ]
    if seed_en:
        lines.append(f"זרע חיפוש באנגלית מוצע: {seed_en.strip()}")
    return "\n".join(lines)


def _row_get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[_ROW_COLUMNS.index(key)]


def item_context_he_from_db(item_id: int | None, *, seed_en: str | None = None) -> str:
    """``format_item_context_he`` from the live ``items`` row; ``""`` when the item is missing or
    the DB is unavailable (never raises -- context is a convenience, not a precondition)."""
    if item_id is None:
        return ""
    try:
        from eoa.db import connection

        with connection(timeout=5) as conn:
            row = conn.execute(
                "SELECT title, entities_mentioned, summary_he FROM items WHERE id = %s", (item_id,)
            ).fetchone()
    except Exception as exc:
        log.debug("item_context_lookup_failed", item_id=item_id, error=str(exc)[:160])
        return ""
    if not row:
        return ""
    title, entities, summary = (_row_get(row, k) for k in _ROW_COLUMNS)
    if not (title or entities or summary):
        return ""
    return format_item_context_he(title, list(entities or []), summary, seed_en)


def context_needs_refresh(context_he: str | None) -> bool:
    """Blank, or carrying an empty entities line (built before analysis extracted entities)."""
    if not (context_he or "").strip():
        return True
    m = _ENTITIES_LINE_RE.search(context_he or "")
    return m is None or m.group(1).strip() in _EMPTY_ENTITIES


def ensure_context_he(payload: dict[str, Any]) -> str:
    """The ``context_he`` a worker should hand to ``investigate()``: the payload's own when it is
    complete, else a fresh block from the item row (keeping the payload's English seed line)."""
    current = (payload or {}).get("context_he") or ""
    if not context_needs_refresh(current):
        return current
    seed = None
    m = _SEED_LINE_RE.search(current)
    if m:
        seed = m.group(0).split(":", 1)[1].strip() or None
    fresh = item_context_he_from_db((payload or {}).get("item_id"), seed_en=seed)
    if fresh and context_needs_refresh(fresh) and current.strip():
        # the row still has no entities -- keep whatever the enqueuer wrote rather than downgrading
        return current
    if fresh:
        log.info("deep_search_context_refreshed", item_id=(payload or {}).get("item_id"))
        return fresh
    return current
