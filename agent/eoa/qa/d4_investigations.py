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

import re
from pathlib import Path
from typing import Any

from eoa.qa.types import Check, DomainScore, weighted_score
from eoa.search.deep_search import (
    NOT_FOUND_MAX_CONFIDENCE,
    PARTIAL_MIN_SOURCES_FOR_HIGH_CONFIDENCE,
    PARTIAL_SINGLE_SOURCE_MAX_CONFIDENCE,
    UNVERIFIED_PREFIX_HE,
    extract_anchors,
)

# ---------------------------------------------------------------------------------------------
# Round 5 (2026-09-06, docs/REPORT_TEMPLATE_BENCHMARK.md sec 2.6 "DS3" / sec 4 item 7): the
# "blocked" outcome distinct from "not_found" is landing in other engineers' file scopes
# (``eoa.search.deep_search``'s result schema, the report renderer) this same evening. These two
# checks are the deterministic, read-only-of-the-rendered-report QA gates for that work -- they
# read the "חקירות עומק" bullet list in the already-rendered daily/weekly Markdown (the exact
# ``- **<question>** — <outcome_label>: <answer>`` shape ``eoa.report.daily``/``weekly`` emit),
# never the DB directly, since the actual regression this catches (docs/REPORT_TEMPLATE_BENCHMARK.md
# evidence: "לא נמצא: התשובה נחסמה בבדיקת אבטחה...") is in what gets *rendered*, not the job row.
# Optional (``report_path=None`` skips both, added to the check list only when the file and its
# investigations section actually exist) -- there is nothing to audit when no report was produced.
# ---------------------------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)
_INVESTIGATIONS_HEADING_HE = "חקירות עומק"
_ENTRY_RE = re.compile(r"^-\s+\*\*(.+?)\*\*\s+—\s+([^:]+):\s*(.*)$", re.MULTILINE)
_NOT_FOUND_LABEL_HE = "לא נמצא"
_BLOCKED_SIGNAL_WORDS_HE = ("נחסם", "הוסתרה", "בדיקת אבטחה")


def _sections(md_text: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(md_text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        out.append((m.group(2).strip(), md_text[start:end].strip()))
    return out


def _investigation_entries(report_text: str) -> list[tuple[str, str, str]]:
    """``[(question, outcome_label_he, rest_of_line)]`` for every rendered investigation bullet in
    the "חקירות עומק" section -- returns ``[]`` when the report has no such section (never raises)."""
    body = ""
    for h, b in _sections(report_text):
        if _INVESTIGATIONS_HEADING_HE in h:
            body = b
            break
    if not body:
        return []
    return [(m.group(1), m.group(2).strip(), m.group(3)) for m in _ENTRY_RE.finditer(body)]


def _blocked_distinct_from_not_found_check(entries: list[tuple[str, str, str]]) -> Check:
    """A rendered entry labeled "לא נמצא" (not_found) whose own text betrays a security-gate
    block (docs/REPORT_TEMPLATE_BENCHMARK.md DS3 evidence: "לא נמצא: התשובה נחסמה בבדיקת
    אבטחה...") is indistinguishable from a genuine "searched thoroughly, nothing there" result --
    it should read as blocked, not not-found."""
    bad = [
        q[:60]
        for q, label, rest in entries
        if _NOT_FOUND_LABEL_HE in label and any(w in rest for w in _BLOCKED_SIGNAL_WORDS_HE)
    ]
    return Check(
        "blocked_distinct_from_not_found",
        len(bad) == 0,
        weight=1.5,
        evidence=f"{len(bad)} entr(y/ies) rendered 'לא נמצא' despite a security-block marker: {bad}",
    )


def _normalize_question(q: str) -> str:
    return re.sub(r"\s+", " ", q).strip().casefold()


def _no_contradictory_reruns_check(entries: list[tuple[str, str, str]]) -> Check:
    """One entry per normalised question in the rendered investigations section -- a question
    logged twice (a rerun) risks showing two different/contradictory outcomes side by side."""
    seen: set[str] = set()
    dups: list[str] = []
    for q, _label, _rest in entries:
        norm = _normalize_question(q)
        if not norm:
            continue
        if norm in seen:
            dups.append(q[:60])
        else:
            seen.add(norm)
    return Check(
        "no_contradictory_reruns_in_report",
        len(dups) == 0,
        weight=1.0,
        evidence=f"{len(dups)} question(s) logged more than once in the report: {dups}",
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


def score_D4(job_ids: list[int], conn: Any, *, report_path: Path | None = None) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D4: deep-search investigation checks over ``job_ids`` (``jobs.id`` for ``kind='deep_search'``
    rows, typically ``state='done'``). ``conn`` is required.

    ``report_path`` (round 5, optional): the latest daily/weekly report Markdown, if one exists
    this round -- feeds the two report-rendering checks (:func:`_blocked_distinct_from_not_found_check`,
    :func:`_no_contradictory_reruns_check`). Omitted from the check list when no report file (or no
    "חקירות עומק" section within it) is available -- nothing to audit yet.
    """
    jobs = _fetch_jobs(conn, job_ids)
    n = len(jobs)

    # Round 5: the two report-rendering checks are independent of whether any deep-search job was
    # sampled this round (they read the already-rendered report file, not the DB) -- computed
    # before the "no jobs" short-circuit below so a round with zero sampled jobs but a real report
    # still gets scored on them, instead of the whole domain silently going "manual only".
    report_checks: list[Check] = []
    report_entries_n = 0
    if report_path is not None and report_path.exists():
        entries = _investigation_entries(report_path.read_text(encoding="utf-8"))
        if entries:
            report_checks = [
                _blocked_distinct_from_not_found_check(entries),
                _no_contradictory_reruns_check(entries),
            ]
            report_entries_n = len(entries)

    if n == 0 and not report_checks:
        return DomainScore(domain="D4", score_0_100=None, checks=[], n=0, note="no investigation jobs in scope")
    if n == 0:
        return DomainScore(
            domain="D4",
            score_0_100=weighted_score(report_checks),
            checks=report_checks,
            n=report_entries_n,
            note="no investigation jobs in scope this round -- report-rendering checks only",
        )

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
    # Round 5: append the report-rendering checks computed above (present whenever a report file
    # with a "חקירות עומק" section was found) rather than discarding them now that jobs also exist
    # in scope this round -- see the report_checks computation earlier in this function.
    checks.extend(report_checks)
    return DomainScore(domain="D4", score_0_100=weighted_score(checks), checks=checks, n=n + report_entries_n)
