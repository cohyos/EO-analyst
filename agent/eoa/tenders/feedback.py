"""W2b (docs/REVIEW_2026-09-06_evening.md, user requirement 2026-09-06 18:55, verbatim: "be open --
and through the relevance feedback given to each tender, the system tunes itself"): the operator
feedback loop over tenders.intake/relevance_score.

Three responsibilities, mirroring the pre-existing triage lessons/calibration design
(``agent/eoa/pipeline/triage.py``'s ``_lessons_text``, ``agent/eoa/feedback/calibration.py``) but
scoped entirely to ``tenders``/``tender_feedback`` -- deliberately its own small module rather than
folded into the generic ``lessons``/``recent_feedback`` tables, since a tender's relevance feedback
means something different (👍/👎 on a stored notice, not a triage-level red/orange re-rating) and
drives two tender-specific learned values (the relevance threshold, per-source priority) those
generic tables have no notion of:

1. **Recording feedback** (:func:`record_feedback`): one append-only ``tender_feedback`` row per
   👍/👎, snapshotting the tender's ``source``/``country``/``matched_terms`` at feedback time (so a
   later correction to the tender row itself never rewrites feedback history), and immediately
   updates the tender's own ``intake`` (👍 -> ``'accepted'``, 👎 -> ``'rejected-by-user'``, hidden by
   default per ``eoa.api.services.list_tenders``).
2. **Self-tuning the relevance threshold** (:func:`recompute_relevance_threshold`): a simple 1-D
   threshold search over recent feedback (``tenders.relevance_score`` vs. ``tender_feedback.verdict``)
   -- the threshold that best separates 👍 from 👎, clamped to ``[THRESHOLD_MIN, THRESHOLD_MAX]``, only
   recomputed once at least ``MIN_SAMPLES_FOR_THRESHOLD`` feedback rows exist (an earlier/smaller
   sample is too noisy to trust over the seeded 0.6 default).
3. **Self-tuning source priority** (:func:`recompute_source_priority`): a source whose last 20
   stored notices drew some feedback but never a single 👍 gets a persisted priority decrement so
   ``eoa.tenders.scan.scan_tenders`` queries it later in the pass -- never disabled outright, purely
   a scan-ordering hint.

Also the "lessons" side of the loop (:func:`tender_lessons_text`): up to 8 recent feedback examples
(4 👍 + 4 👎, title + one-line reason) formatted for injection into the ``tender_extract`` prompt's
``{lessons}`` placeholder, exactly the same "recent examples as calibrated lessons" shape
``pipeline.triage._lessons_text`` feeds the triage prompt.

Every function here degrades gracefully on a DB hiccup (never raises out of a scan/report path,
per docs/CONVENTIONS.md rule 9) except :func:`record_feedback` itself, whose whole point is a
user-initiated write -- a failure there must surface to the caller (the API route), not be silently
swallowed.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any, Literal

import structlog

from eoa.db import connection

log = structlog.get_logger(__name__)

VerdictLiteral = Literal["relevant", "irrelevant"]

# Seeded in db/migrations/versions/0021_tender_feedback.py's tender_relevance_state row -- kept
# here too as the in-process fallback whenever that row is somehow missing/unreadable (a fresh
# scan must never crash for lack of a threshold; it just uses the same default the migration seeds).
DEFAULT_RELEVANCE_THRESHOLD = 0.6
THRESHOLD_MIN = 0.3
THRESHOLD_MAX = 0.8
MIN_SAMPLES_FOR_THRESHOLD = 10

# How many of a source's most-recently-stored tenders to look at when deciding whether it has
# earned a scan-priority decrement (user requirement: "last 20 stored notices").
SOURCE_PRIORITY_LOOKBACK = 20
SOURCE_PRIORITY_DECREMENT = -1  # a flat, non-escalating decrement -- see recompute_source_priority

# Up to this many of each verdict feed the tender_extract prompt's "lessons" block (user
# requirement: "up to 8 recent feedback examples (4 positive, 4 negative)").
LESSONS_PER_VERDICT = 4


# --------------------------------------------------------------------------
# recording feedback
# --------------------------------------------------------------------------


def _tender_snapshot(tender_id: int) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT source, country, matched_terms FROM tenders WHERE id = %(id)s", {"id": tender_id})
        return cur.fetchone()


def record_feedback(
    tender_id: int, verdict: VerdictLiteral, reason: str | None = None
) -> dict[str, Any] | None:
    """Insert one ``tender_feedback`` row and update the tender's own ``intake`` at once (👍 ->
    ``'accepted'``, 👎 -> ``'rejected-by-user'``). Returns the inserted feedback row (``None`` if
    ``tender_id`` doesn't exist). Recomputing the learned threshold/source priority is best-effort
    (never lets a transient DB hiccup in the *self-tuning* step fail the feedback write itself,
    which is the part the operator is actually waiting on) -- but the feedback INSERT and the
    ``intake`` UPDATE themselves are allowed to raise, same as any other user-initiated write."""
    snapshot = _tender_snapshot(tender_id)
    if snapshot is None:
        return None

    intake = "accepted" if verdict == "relevant" else "rejected-by-user"
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tender_feedback (tender_id, verdict, reason, source, territory, matched_terms)
            VALUES (%(tender_id)s, %(verdict)s, %(reason)s, %(source)s, %(territory)s, %(matched_terms)s)
            RETURNING id, tender_id, verdict, reason, source, territory, matched_terms, created_at
            """,
            {
                "tender_id": tender_id,
                "verdict": verdict,
                "reason": reason,
                "source": snapshot.get("source"),
                "territory": snapshot.get("country"),
                "matched_terms": snapshot.get("matched_terms") or None,
            },
        )
        row = cur.fetchone()
        cur.execute(
            "UPDATE tenders SET intake = %(intake)s WHERE id = %(id)s", {"intake": intake, "id": tender_id}
        )
    log.info("tender_feedback_recorded", tender_id=tender_id, verdict=verdict, intake=intake)

    try:
        recompute_relevance_threshold()
    except Exception as exc:
        log.warning("tender_threshold_recompute_failed", error=str(exc)[:200])
    if snapshot.get("source"):
        try:
            recompute_source_priority(snapshot["source"])
        except Exception as exc:
            log.warning(
                "tender_source_priority_recompute_failed", source=snapshot.get("source"), error=str(exc)[:200]
            )

    return row


def list_feedback_for_tender(tender_id: int) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, tender_id, verdict, reason, source, territory, matched_terms, created_at "
            "FROM tender_feedback WHERE tender_id = %(id)s ORDER BY created_at DESC",
            {"id": tender_id},
        )
        return cur.fetchall()


# --------------------------------------------------------------------------
# self-tuning relevance threshold
# --------------------------------------------------------------------------


def get_relevance_threshold() -> float:
    """Current learned threshold (``tenders.relevance_score >= this`` -> ``intake='accepted'`` at
    insert time) -- falls back to :data:`DEFAULT_RELEVANCE_THRESHOLD` if the singleton row is
    somehow missing (a fresh DB before migration 0021's seed row landed, or a transient read
    failure) so a scan never blocks on this."""
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT relevance_threshold FROM tender_relevance_state WHERE id = 1")
            row = cur.fetchone()
            if row is not None and row.get("relevance_threshold") is not None:
                return float(row["relevance_threshold"])
    except Exception as exc:
        log.debug("tender_relevance_threshold_read_failed", error=str(exc)[:200])
    return DEFAULT_RELEVANCE_THRESHOLD


def _best_separating_threshold(samples: list[tuple[float, bool]]) -> float:
    """Simple 1-D threshold search: try every midpoint between consecutive distinct scores (plus
    the two extremes) and keep whichever correctly classifies (``score >= t`` == ``is_positive``)
    the most samples. Ties keep the first (lowest) best threshold found -- deterministic, and a
    lower threshold is the more permissive/"open" choice when several separate the data equally
    well, in keeping with this whole feature's "be open" mandate."""
    scores = sorted({s for s, _ in samples})
    candidates = [0.0, *[(a + b) / 2 for a, b in pairwise(scores)], 1.0]
    best_t = DEFAULT_RELEVANCE_THRESHOLD
    best_correct = -1
    for t in candidates:
        correct = sum(1 for score, is_pos in samples if (score >= t) == is_pos)
        if correct > best_correct:
            best_correct = correct
            best_t = t
    return best_t


def recompute_relevance_threshold(min_samples: int = MIN_SAMPLES_FOR_THRESHOLD) -> float | None:
    """Recompute + persist the learned threshold from recent feedback. A no-op (returns ``None``,
    leaves the stored threshold untouched) until at least ``min_samples`` feedback rows exist --
    below that, a threshold search is just noise. Only feedback whose tender actually has a
    ``relevance_score`` is used (always true post-migration-0021, but defensive against a stray
    NULL). Clamped to ``[THRESHOLD_MIN, THRESHOLD_MAX]`` per the user's own requirement, so a
    pathological feedback batch (e.g. all 👎) can never push the threshold to a degenerate 0 or 1."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.relevance_score AS score, tf.verdict AS verdict
            FROM tender_feedback tf
            JOIN tenders t ON t.id = tf.tender_id
            WHERE t.relevance_score IS NOT NULL
            ORDER BY tf.created_at DESC
            """
        )
        rows = cur.fetchall()
    if len(rows) < min_samples:
        return None

    samples = [(float(r["score"]), r["verdict"] == "relevant") for r in rows]
    threshold = max(THRESHOLD_MIN, min(THRESHOLD_MAX, _best_separating_threshold(samples)))

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tender_relevance_state (id, relevance_threshold)
            VALUES (1, %(threshold)s)
            ON CONFLICT (id) DO UPDATE SET relevance_threshold = EXCLUDED.relevance_threshold
            """,
            {"threshold": threshold},
        )
    log.info("tender_relevance_threshold_recomputed", threshold=threshold, samples=len(samples))
    return threshold


# --------------------------------------------------------------------------
# self-tuning source priority
# --------------------------------------------------------------------------


def get_source_priorities() -> dict[str, int]:
    """``{source_id: priority_decrement}`` for every source that has earned one (missing key ==
    the baseline 0, never scanned any differently than before). Never raises -- returns ``{}`` on
    any DB hiccup so a scan's source ordering just falls back to config order."""
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT source_id, priority_decrement FROM tender_source_priority")
            return {r["source_id"]: r["priority_decrement"] for r in cur.fetchall()}
    except Exception as exc:
        log.debug("tender_source_priorities_read_failed", error=str(exc)[:200])
        return {}


def recompute_source_priority(source_id: str, *, lookback: int = SOURCE_PRIORITY_LOOKBACK) -> int:
    """A source earns :data:`SOURCE_PRIORITY_DECREMENT` (never lower -- this is a flat flag, not an
    escalating penalty, and never disables the source: ``eoa.tenders.scan`` still scans it, just
    later in the pass) once its last ``lookback`` stored tenders have drawn *some* feedback but
    *never* a single 👍. A source with zero feedback at all on its recent notices is left at the
    baseline (0) -- no evidence either way yet, per this feature's "never guess" spirit. Returns the
    resulting decrement (0 or :data:`SOURCE_PRIORITY_DECREMENT`)."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM tenders WHERE source = %(source)s ORDER BY created_at DESC LIMIT %(n)s",
            {"source": source_id, "n": lookback},
        )
        recent_ids = [r["id"] for r in cur.fetchall()]
        if not recent_ids:
            return 0
        cur.execute(
            "SELECT verdict, count(*) AS n FROM tender_feedback WHERE tender_id = ANY(%(ids)s) GROUP BY verdict",
            {"ids": recent_ids},
        )
        counts = {r["verdict"]: r["n"] for r in cur.fetchall()}

    has_any_feedback = bool(counts)
    has_positive = counts.get("relevant", 0) > 0
    decrement = SOURCE_PRIORITY_DECREMENT if (has_any_feedback and not has_positive) else 0

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO tender_source_priority (source_id, priority_decrement)
            VALUES (%(source_id)s, %(decrement)s)
            ON CONFLICT (source_id) DO UPDATE SET priority_decrement = EXCLUDED.priority_decrement
            """,
            {"source_id": source_id, "decrement": decrement},
        )
    if decrement:
        log.info("tender_source_priority_decremented", source=source_id, decrement=decrement)
    return decrement


# --------------------------------------------------------------------------
# lessons (fed into the tender_extract LLM prompt, mirrors pipeline.triage._lessons_text)
# --------------------------------------------------------------------------


def recent_feedback_examples(limit_per_verdict: int = LESSONS_PER_VERDICT) -> list[dict[str, Any]]:
    """Up to ``limit_per_verdict`` most-recent 👍 examples + up to ``limit_per_verdict`` most-recent
    👎 examples (title + reason), most-recent first within each verdict. Never raises -- returns
    ``[]`` on any DB hiccup (this only ever feeds prompt text, never a persistence decision)."""
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT tf.verdict AS verdict, tf.reason AS reason, tf.created_at AS created_at,
                       t.title AS title
                FROM tender_feedback tf
                JOIN tenders t ON t.id = tf.tender_id
                WHERE tf.verdict = %(verdict)s
                ORDER BY tf.created_at DESC
                LIMIT %(limit)s
                """,
                {"verdict": "relevant", "limit": limit_per_verdict},
            )
            positives = cur.fetchall()
            cur.execute(
                """
                SELECT tf.verdict AS verdict, tf.reason AS reason, tf.created_at AS created_at,
                       t.title AS title
                FROM tender_feedback tf
                JOIN tenders t ON t.id = tf.tender_id
                WHERE tf.verdict = %(verdict)s
                ORDER BY tf.created_at DESC
                LIMIT %(limit)s
                """,
                {"verdict": "irrelevant", "limit": limit_per_verdict},
            )
            negatives = cur.fetchall()
        return [*positives, *negatives]
    except Exception as exc:
        log.debug("tender_feedback_examples_unavailable", error=str(exc)[:200])
        return []


def tender_lessons_text() -> str:
    """Formats :func:`recent_feedback_examples` as a bullet list for the ``tender_extract``
    prompt's ``{lessons}`` placeholder -- exactly the "recent calibrated examples" shape
    ``pipeline.triage._lessons_text`` feeds the triage prompt, scoped to tenders. Never raises."""
    examples = recent_feedback_examples()
    if not examples:
        return "אין עדיין משוב רלוונטיות קודם מהמשתמש."
    lines: list[str] = []
    for ex in examples:
        mark = "👍 רלוונטי" if ex.get("verdict") == "relevant" else "👎 לא רלוונטי"
        title = (ex.get("title") or "").strip() or "(ללא כותרת)"
        reason = (ex.get("reason") or "").strip()
        reason_part = f" -- {reason}" if reason else ""
        lines.append(f"- [{mark}] {title}{reason_part}")
    return "\n".join(lines)
