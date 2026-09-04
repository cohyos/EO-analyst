"""FR-11.4: the weekly "what changed because of your feedback" transparency summary.

Purely templated (no LLM, per FR-11.4's design): reads `lessons` created in
the last `period_days` and turns them into a short Hebrew paragraph grouped
by kind. Calibration deltas are included implicitly -- `feedback.calibration`
already writes them as `lessons(kind='calibration')` rows whose text spells
out the bias in Hebrew, so this module doesn't need its own separate query
into `triage_feedback` to describe them again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from eoa.db import connection
from eoa.notify import ntfy

log = structlog.get_logger(__name__)

DEFAULT_PERIOD_DAYS = 7
_MAX_LINES_PER_KIND = 5

_KIND_LABELS: dict[str, str] = {
    "calibration": "כיול דירוג חשיבות",
    "watchlist": "עדכון רשימת מעקב",
    "style": "התאמת סגנון/אורך דוחות",
    "decision": "הכרעות משתמש",
    "meta": "סיכומי מטא",
}


@dataclass
class MetaSummary:
    period_days: int
    lesson_count: int
    by_kind: dict[str, int] = field(default_factory=dict)
    text_he: str = ""


def _lessons_in_period(days: int) -> list[dict[str, Any]]:
    query = """
        SELECT kind, text, source_ref, created_at
        FROM lessons
        WHERE created_at >= now() - (%(days)s || ' days')::interval
        ORDER BY created_at DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"days": days})
        return cur.fetchall()


def _render_text(period_days: int, lessons: list[dict[str, Any]]) -> str:
    if not lessons:
        return f"בשבעת הימים האחרונים ({period_days} ימים) לא נוצרו לקחים חדשים מהמשוב — אין שינויים לדווח."

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for ls in lessons:
        by_kind.setdefault(ls.get("kind") or "other", []).append(ls)

    parts = [f"מה השתנה בעקבות המשוב ({period_days} ימים אחרונים, {len(lessons)} לקחים חדשים):"]
    for kind, kind_lessons in by_kind.items():
        label = _KIND_LABELS.get(kind, kind)
        parts.append(f"\n{label}:")
        for ls in kind_lessons[:_MAX_LINES_PER_KIND]:
            parts.append(f"- {ls['text']}")
        overflow = len(kind_lessons) - _MAX_LINES_PER_KIND
        if overflow > 0:
            parts.append(f"- ...ועוד {overflow} לקחים נוספים")
    return "\n".join(parts)


def weekly_meta_summary(period_days: int = DEFAULT_PERIOD_DAYS) -> MetaSummary:
    """Build the FR-11.4 weekly transparency summary from lessons created in the last `period_days`."""
    lessons = _lessons_in_period(period_days)
    by_kind_counts: dict[str, int] = {}
    for ls in lessons:
        kind = ls.get("kind") or "other"
        by_kind_counts[kind] = by_kind_counts.get(kind, 0) + 1
    return MetaSummary(
        period_days=period_days,
        lesson_count=len(lessons),
        by_kind=by_kind_counts,
        text_he=_render_text(period_days, lessons),
    )


def post_weekly_meta(period_days: int = DEFAULT_PERIOD_DAYS) -> MetaSummary:
    """Build the weekly summary and push it out via `ntfy.status()` (FR-11.4)."""
    summary = weekly_meta_summary(period_days)
    ntfy.status(summary.text_he)
    log.info("feedback.weekly_meta_posted", period_days=period_days, lesson_count=summary.lesson_count)
    return summary
