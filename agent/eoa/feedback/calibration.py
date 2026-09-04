"""FR-3.3 / FR-11.3: turn `triage_feedback` into calibration `lessons`.

`calibrate()` reads the last `days` days of `triage_feedback`, computes the
mean ordinal delta (`user_level - agent_level`) per `items.domain` and per
`sources.kind` ("source_kind"), and flags a *systematic bias* wherever
`|mean delta| >= BIAS_THRESHOLD` with at least `MIN_SAMPLES` feedback rows
backing it. Each detected bias is written/kept as an active
`lessons(kind='calibration')` row, deduplicated by a stable `source_ref`
(``calib:domain:<domain>`` / ``calib:source_kind:<kind>``): re-running
`calibrate()` updates the text in place if the bias direction/magnitude
changed, leaves it untouched if not, and deactivates (never deletes) any
previously-flagged bias that no longer holds.

`triage.py`'s `_lessons_text()` already reads `lessons(kind='calibration')`
(most-recent-first, capped at 12) and feeds them straight into the next
triage prompt -- this module is the writer side of that loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from eoa.db import connection
from eoa.memory.relational import add_lesson, get_lessons

log = structlog.get_logger(__name__)

# Ordinal encoding of `items.level` / `triage_feedback.user_level|agent_level`
# (see `agent/eoa/api/routes/items.py::FEEDBACK_LEVELS`), low to high importance.
LEVEL_ORDER: dict[str, int] = {"archive": 0, "yellow": 1, "orange": 2, "red": 3}

DEFAULT_PERIOD_DAYS = 30
MIN_SAMPLES = 3
BIAS_THRESHOLD = 0.5


@dataclass
class DomainBias:
    """One detected systematic bias (either a `domain` or a `source_kind` group)."""

    scope: str  # "domain" | "source_kind"
    key: str
    n: int
    mean_delta: float

    @property
    def source_ref(self) -> str:
        return f"calib:{self.scope}:{self.key}"


@dataclass
class CalibrationSummary:
    period_days: int
    feedback_n: int
    domain_deltas: dict[str, float] = field(default_factory=dict)
    source_kind_deltas: dict[str, float] = field(default_factory=dict)
    biases: list[DomainBias] = field(default_factory=list)
    lessons_created: int = 0
    lessons_updated: int = 0
    lessons_deactivated: int = 0


# --------------------------------------------------------------------------
# DB boundary (kept as small, separately-monkeypatchable functions so unit
# tests can exercise the pure grouping/threshold/text logic without a DB).
# --------------------------------------------------------------------------


def _feedback_rows(days: int) -> list[dict[str, Any]]:
    """`triage_feedback` rows from the last `days` days, joined to their item's `domain` and
    source `kind` ("source_kind"). Rows without a resolvable `user_level`/`agent_level` pair
    are still returned; `calibrate()` skips them."""
    query = """
        SELECT tf.id, tf.item_id, tf.user_level, tf.agent_level, tf.created_at,
               i.domain AS domain, s.kind AS source_kind
        FROM triage_feedback tf
        JOIN items i ON i.id = tf.item_id
        LEFT JOIN sources s ON s.id = i.source_id
        WHERE tf.created_at >= now() - (%(days)s || ' days')::interval
        ORDER BY tf.created_at DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, {"days": days})
        return cur.fetchall()


def _active_calibration_lessons() -> dict[str, dict[str, Any]]:
    """Active `lessons(kind='calibration')` rows keyed by `source_ref` (rows without one are ignored --
    they cannot participate in this module's dedupe/deactivate cycle)."""
    return {ls["source_ref"]: ls for ls in get_lessons("calibration") if ls.get("source_ref")}


def _update_lesson_text(lesson_id: int, text: str) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE lessons SET text = %s WHERE id = %s", (text, lesson_id))


def _deactivate_lessons(lesson_ids: list[int]) -> int:
    if not lesson_ids:
        return 0
    with connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE lessons SET active = false WHERE id = ANY(%s)", (lesson_ids,))
    return len(lesson_ids)


# --------------------------------------------------------------------------
# pure logic
# --------------------------------------------------------------------------


def _fmt_magnitude(x: float) -> str:
    """Render a bias magnitude the way the Hebrew lesson text wants it: whole numbers bare
    (``1`` not ``1.0``), everything else to one decimal place."""
    rounded = round(x, 1)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.1f}"


def _bias_text(bias: DomainBias) -> str:
    magnitude = _fmt_magnitude(abs(bias.mean_delta))
    subject = f"בתחום {bias.key}" if bias.scope == "domain" else f"במקורות מסוג {bias.key}"
    if bias.mean_delta < 0:
        return f"{subject} המשתמש מוריד דירוג בממוצע ב-{magnitude} רמה — היה שמרני יותר"
    return f"{subject} המשתמש מעלה דירוג בממוצע ב-{magnitude} רמה — היה פחות שמרני"


def _group_deltas(rows: list[dict[str, Any]]) -> tuple[dict[str, list[float]], dict[str, list[float]], int]:
    """Group ordinal deltas by `domain` and by `source_kind`; returns `(by_domain, by_source_kind, n_counted)`.

    Rows whose `user_level`/`agent_level` don't both map through `LEVEL_ORDER`
    are skipped entirely (not counted) rather than guessed at.
    """
    by_domain: dict[str, list[float]] = {}
    by_source_kind: dict[str, list[float]] = {}
    counted = 0
    for row in rows:
        u = LEVEL_ORDER.get(row.get("user_level"))
        a = LEVEL_ORDER.get(row.get("agent_level"))
        if u is None or a is None:
            continue
        delta = float(u - a)
        counted += 1
        domain = row.get("domain")
        if domain:
            by_domain.setdefault(domain, []).append(delta)
        source_kind = row.get("source_kind")
        if source_kind:
            by_source_kind.setdefault(source_kind, []).append(delta)
    return by_domain, by_source_kind, counted


def _detect_biases(
    by_domain: dict[str, list[float]], by_source_kind: dict[str, list[float]]
) -> list[DomainBias]:
    biases: list[DomainBias] = []
    for scope, grouped in (("domain", by_domain), ("source_kind", by_source_kind)):
        for key, deltas in grouped.items():
            n = len(deltas)
            if n < MIN_SAMPLES:
                continue
            mean_delta = sum(deltas) / n
            if abs(mean_delta) < BIAS_THRESHOLD:
                continue
            biases.append(DomainBias(scope=scope, key=key, n=n, mean_delta=mean_delta))
    return biases


def _mean_per_key(grouped: dict[str, list[float]]) -> dict[str, float]:
    return {key: sum(deltas) / len(deltas) for key, deltas in grouped.items()}


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def calibrate(days: int = DEFAULT_PERIOD_DAYS) -> CalibrationSummary:
    """Recompute domain/source_kind calibration bias from recent feedback and sync it into
    `lessons(kind='calibration')` (create new, update changed text, deactivate stale)."""
    rows = _feedback_rows(days)
    by_domain, by_source_kind, counted = _group_deltas(rows)
    biases = _detect_biases(by_domain, by_source_kind)

    existing = _active_calibration_lessons()
    current_texts: dict[str, str] = {b.source_ref: _bias_text(b) for b in biases}

    created = updated = 0
    for source_ref, text in current_texts.items():
        prior = existing.get(source_ref)
        if prior is None:
            add_lesson("calibration", text, source_ref=source_ref)
            created += 1
        elif prior.get("text") != text:
            _update_lesson_text(prior["id"], text)
            updated += 1

    stale_ids = [ls["id"] for ref, ls in existing.items() if ref not in current_texts]
    deactivated = _deactivate_lessons(stale_ids)

    summary = CalibrationSummary(
        period_days=days,
        feedback_n=counted,
        domain_deltas=_mean_per_key(by_domain),
        source_kind_deltas=_mean_per_key(by_source_kind),
        biases=biases,
        lessons_created=created,
        lessons_updated=updated,
        lessons_deactivated=deactivated,
    )
    log.info(
        "feedback.calibrated",
        period_days=days,
        feedback_n=counted,
        biases=len(biases),
        created=created,
        updated=updated,
        deactivated=deactivated,
    )
    return summary
