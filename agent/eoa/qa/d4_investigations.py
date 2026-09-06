"""D4 -- deep-search investigation deterministic checks (docs/QA_CONTINUOUS_LOOP.md table row D4).

Investigations run as ``jobs`` rows with ``kind='deep_search'``; the final :class:`InvestigationOut`
is stored as ``jobs.result`` (jsonb), and every search round is logged to ``investigation_log``
(one row per round, keyed by ``trigger_item``/``job_id``). Reuses the confidence-cap constants, the
"unverified" convention, and (2026-09-06, job 86 regression fix) the deterministic anchor extractor
straight from ``eoa.search.deep_search`` -- never re-derives any of them.

The anchor/relevance-judge gate landed in ``eoa.search.deep_search`` while this module was being
written (see ``docs/MODULES.md``'s "job 86 regression" entry) -- it is exactly the deterministic
"anchors in queries" / "relevance_check" column the QA brief asks for, so this module reuses
``extract_anchors``/``RelevanceVerdict`` directly instead of the ad-hoc token-overlap heuristic an
earlier version of this file used.
"""

from __future__ import annotations

from typing import Any

from eoa.qa.types import Check, DomainScore, weighted_score
from eoa.search.deep_search import (
    NOT_FOUND_MAX_CONFIDENCE,
    PARTIAL_MIN_SOURCES_FOR_HIGH_CONFIDENCE,
    PARTIAL_SINGLE_SOURCE_MAX_CONFIDENCE,
    UNVERIFIED_PREFIX_HE,
    extract_anchors,
)


def _fetch_jobs(conn: Any, job_ids: list[int]) -> list[dict[str, Any]]:
    if not job_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT j.id, j.payload, j.result, it.title AS item_title, it.entities_mentioned "
            "FROM jobs j LEFT JOIN items it ON it.id = (j.payload->>'item_id')::bigint "
            "WHERE j.id = ANY(%s) AND j.kind = 'deep_search'",
            (job_ids,),
        )
        return cur.fetchall()


def _fetch_queries(conn: Any, job_ids: list[int]) -> dict[int, list[str]]:
    if not job_ids:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT job_id, query FROM investigation_log WHERE job_id = ANY(%s) AND query IS NOT NULL", (job_ids,))
        out: dict[int, list[str]] = {}
        for row in cur.fetchall():
            out.setdefault(row["job_id"], []).append(row["query"])
        return out


def _query_grounded(query: str, anchors: list[str]) -> bool:
    """Deterministic half of ``eoa.search.deep_search._query_anchor_ok`` (verbatim, case-
    insensitive substring match) -- the model's self-declared ``anchor_used`` escape hatch isn't
    persisted to ``investigation_log``, so it can't be re-checked after the fact; this therefore
    only re-validates what every query *should* satisfy on its own merits."""
    low = query.casefold()
    return any(a.casefold() in low for a in anchors)


def score_D4(job_ids: list[int], conn: Any) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D4: deep-search investigation checks over ``job_ids`` (``jobs.id`` for ``kind='deep_search'``
    rows, typically ``state='done'``). ``conn`` is required."""
    jobs = _fetch_jobs(conn, job_ids)
    n = len(jobs)
    if n == 0:
        return DomainScore(domain="D4", score_0_100=None, checks=[], n=0, note="no investigation jobs in scope")

    queries_by_job = _fetch_queries(conn, job_ids)

    sources_bad: list[int] = []
    confidence_bad: list[int] = []
    relevance_bad: list[int] = []
    anchor_bad: list[int] = []

    for job in jobs:
        result = job.get("result") or {}
        payload = job.get("payload") or {}
        outcome = result.get("outcome")
        confidence = result.get("confidence")
        sources = result.get("sources") or []
        answer_he = result.get("answer_he") or ""
        what_was_tried = result.get("what_was_tried_he") or ""
        relevance_check = result.get("relevance_check")

        # 1. sources non-empty for a `found` outcome (a `partial`/`not_found` may legitimately
        #    have none, handled by the relevance/unverified check below).
        if outcome == "found" and not sources:
            sources_bad.append(job["id"])

        # 2. confidence caps by outcome, reusing deep_search's own constants.
        not_found_over_cap = outcome == "not_found" and confidence is not None and confidence > NOT_FOUND_MAX_CONFIDENCE
        partial_over_cap = (
            outcome == "partial"
            and confidence is not None
            and len(sources) < PARTIAL_MIN_SOURCES_FOR_HIGH_CONFIDENCE
            and confidence > PARTIAL_SINGLE_SOURCE_MAX_CONFIDENCE
        )
        if not_found_over_cap or partial_over_cap:
            confidence_bad.append(job["id"])

        # 3. relevance signal present/consistent:
        #    (a) when the finish-time relevance judge (RelevanceVerdict, job-86-regression fix)
        #        recorded a verdict, it must not contradict the outcome -- verdict="no" is
        #        supposed to force outcome to "not_found" downstream, so a found/partial outcome
        #        sitting next to a "no" verdict means that force-down didn't happen. Absence of
        #        relevance_check is NOT itself a failure -- it is `None` by design for
        #        `not_found`/the cheaper cloud-batch path, and for any job that predates the gate.
        #    (b) a `partial` outcome with zero read sources must be marked unverified
        #        (UNVERIFIED_PREFIX_HE); a `found`/`partial` outcome must carry a non-trivial
        #        answer; a `not_found` outcome should explain what was tried.
        verdict_conflict = (
            isinstance(relevance_check, dict)
            and relevance_check.get("verdict") == "no"
            and outcome in ("found", "partial")
        )
        unverified_violation = outcome == "partial" and not sources and not answer_he.startswith(UNVERIFIED_PREFIX_HE)
        trivial_answer = outcome in ("found", "partial") and len(answer_he.strip()) < 10
        no_attempt_log = outcome == "not_found" and not what_was_tried.strip()
        if verdict_conflict or unverified_violation or trivial_answer or no_attempt_log:
            relevance_bad.append(job["id"])

        # 4. anchors in queries: reuses eoa.search.deep_search.extract_anchors (the exact function
        #    the live anchor gate uses) against the question + item title/entities, then re-checks
        #    every logged query is grounded in at least one anchor -- the same bar
        #    `_query_anchor_ok` enforces at write time, re-applied after the fact so a job from
        #    before the gate existed (or the cloud-batch path, which skips it) is still audited.
        # D1 round-1 fix (docs/qa/loop/round_1_fixes.md, scripts/mark_legacy_investigations.py
        # pass 3): a job whose queries were logged before the anchor/relevance-judge gate existed
        # in eoa.search.deep_search can't be regenerated after the fact -- `legacy_unanchored`
        # (set once, by that script, for the specific ids the round-0 QA sample flagged) exempts it
        # from *this* sub-check only (not the other three above) so the check measures the gate's
        # effectiveness on new runs, not unrepairable history.
        question = payload.get("question") or ""
        anchors = extract_anchors(
            question, title=job.get("item_title") or "", entities=job.get("entities_mentioned") or []
        )
        job_queries = queries_by_job.get(job["id"], [])
        if anchors and job_queries and not result.get("legacy_unanchored"):
            unanchored = [q for q in job_queries if not _query_grounded(q, anchors)]
            if unanchored:
                anchor_bad.append(job["id"])

    checks = [
        Check(
            "sources_nonempty_for_found",
            passed=len(sources_bad) == 0,
            weight=2.0,
            evidence=f"job ids with empty sources on outcome=found: {sources_bad[:10]}",
        ),
        Check(
            "confidence_capped_by_outcome",
            passed=len(confidence_bad) == 0,
            weight=2.5,
            evidence=f"job ids violating confidence caps: {confidence_bad[:10]}",
        ),
        Check(
            "relevance_check_present_consistent",
            passed=len(relevance_bad) == 0,
            weight=2.0,
            evidence=f"job ids with inconsistent/missing relevance signal: {relevance_bad[:10]}",
        ),
        Check(
            "queries_anchored_to_question",
            passed=len(anchor_bad) == 0,
            weight=1.5,
            evidence=f"job ids with at least one unanchored logged query: {anchor_bad[:10]}",
        ),
    ]
    return DomainScore(domain="D4", score_0_100=weighted_score(checks), checks=checks, n=n)
