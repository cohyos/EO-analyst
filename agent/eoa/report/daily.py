"""Stage: report/daily — collect analyzed items, draft the daily report, QA-gate citations, render.

Pipeline: ``collect_items`` (+ ``collect_events`` / ``collect_deep_search`` / ``collect_open_clarifications``)
-> ``draft_report`` (resident model) -> ``qa_citations.check`` -> on failure, one corrective LLM retry,
then (if still failing) replace the narrative with a deterministic, cited substitute synthesis built
straight from the data (round 3, 2026-09-06 -- see ``_deterministic_fallback_draft``; superseded the
original goal-1 behaviour of dropping the narrative to tables-only + an apology sentence) -> render
docx/md/html -> persist a ``reports`` row.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from psycopg.types.json import Json

from eoa.config import REPO_ROOT, settings
from eoa.db import connection
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import DailyReportDraft, Sentence
from eoa.report.docx_builder import (
    build_docx,
    fmt_amount,
    fmt_date,
    hebrew_date_str,
    render_html,
    render_markdown,
    save_docx,
    validate_docx,
)
from eoa.report.qa_citations import QAResult, check
from eoa.report.textnorm import normalize_draft

log = structlog.get_logger(__name__)

JERUSALEM = ZoneInfo("Asia/Jerusalem")

_LEVELS_PRIMARY = ("red", "orange")
_LEVELS_FALLBACK = ("red", "orange", "yellow")
_MIN_ITEMS_BEFORE_FALLBACK = 3


@dataclass
class ReportPaths:
    docx: Path
    md: Path
    html: Path
    report_id: int
    qa: QAResult


def _today_jerusalem() -> dt.date:
    return dt.datetime.now(JERUSALEM).date()


def _period(
    period_start: dt.date | None, period_end: dt.date | None
) -> tuple[dt.datetime, dt.datetime, dt.date]:
    """Resolve the collection window as tz-aware UTC timestamps, plus the Jerusalem calendar date
    used to label/persist the report.

    F3: both ``None`` (the default — a scheduled night run) means the 24 hours ending *now*
    (Asia/Jerusalem), expressed as timestamps rather than a bare date — the previous
    date-only default meant a 01:00 run only ever saw items published since local midnight (~1
    hour), not a real trailing day. The report is still labeled with the run's own Jerusalem
    calendar date. Either given (manual/CLI rebuild of a specific date/range) keeps the original,
    reproducible semantics: the whole Jerusalem day(s) from ``period_start`` 00:00:00 to
    ``period_end`` 23:59:59.999999 inclusive.
    """
    if period_start is None and period_end is None:
        end_ts = dt.datetime.now(JERUSALEM)
        start_ts = end_ts - dt.timedelta(hours=24)
        return start_ts.astimezone(dt.UTC), end_ts.astimezone(dt.UTC), end_ts.date()
    end_date = period_end or period_start
    start_date = period_start or end_date
    assert end_date is not None and start_date is not None  # for type-checkers; unreachable otherwise
    start_ts = dt.datetime.combine(start_date, dt.time.min, tzinfo=JERUSALEM)
    end_ts = dt.datetime.combine(end_date, dt.time.max, tzinfo=JERUSALEM)
    return start_ts.astimezone(dt.UTC), end_ts.astimezone(dt.UTC), end_date


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------


def collect_items(
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Analyzed items in the period, ``level`` red/orange (falling back to also include yellow if
    fewer than 3 rows come back), ordered by score desc, each carrying a stable 1-based ``n``.

    NOTE: ``items`` has no ``key_facts``/``uncertainty_he`` columns in the current schema (only
    ``summary_he``/``so_what_he`` are persisted from the analyze stage), so ``key_facts`` here is
    always ``[]`` until that gap is closed upstream; the report prompt degrades gracefully.
    """
    start, end, _label = _period(period_start, period_end)
    cap = max_items or settings().triage.daily_report_max_items

    def _query(levels: tuple[str, ...]) -> list[dict[str, Any]]:
        sql = """
            SELECT i.id, i.url, i.title, i.domain, i.subdomain, i.published_at, i.level, i.score,
                   i.summary_he, i.so_what_he, i.report_kind, i.geography, i.trl,
                   COALESCE(src.name, i.url) AS source_name
            FROM items i
            LEFT JOIN sources src ON src.id = i.source_id
            WHERE i.security_status = 'clean'
              AND i.dedup_of IS NULL
              AND i.level = ANY(%(levels)s)
              AND COALESCE(i.published_at, i.fetched_at, i.created_at) BETWEEN %(start)s AND %(end)s
              -- F20: a tender-derived item (e.g. a 2015 notice with no published_at, picked up by
              -- fetched_at/created_at falling in the window) is already rendered in the tenders
              -- board/forecast table (eoa.tenders.report_section) -- showing it again here as a
              -- news headline is a duplicate, and for an old notice a misleading one.
              AND NOT EXISTS (SELECT 1 FROM tenders t WHERE t.item_id = i.id)
              -- F20: an undated, source-less row is a search/deep-search-derived page scrape
              -- (no RSS/HTML source, no article publish date) -- not a dated news item, so it
              -- does not belong in the dated news sections either.
              AND NOT (i.published_at IS NULL AND i.source_id IS NULL)
            ORDER BY i.score DESC NULLS LAST, i.published_at DESC NULLS LAST
            LIMIT %(limit)s
        """
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, {"levels": list(levels), "start": start, "end": end, "limit": cap})
            return cur.fetchall()

    rows = _query(_LEVELS_PRIMARY)
    if len(rows) < _MIN_ITEMS_BEFORE_FALLBACK:
        rows = _query(_LEVELS_FALLBACK)
    for row in rows:
        row.setdefault("key_facts", [])
    for idx, row in enumerate(rows, start=1):
        row["n"] = idx
    log.info("report_items_collected", count=len(rows), start=str(start), end=str(end))
    return rows


# W5 (round 4, docs/qa/loop/round_4_fixes.md): the analyze-stage classifier over-applies
# kind='test' to events that carry no trial/demonstration/live-fire vocabulary at all (a contract
# award, a funding note, an academic paper, a production-line milestone, ...) -- e.g. the review's
# own example, an event labeled "ניסוי" for what was actually a funding allocation. This is a
# deterministic, conservative guard: it only ever *downgrades* 'test' to 'other' when none of the
# vocabulary below appears in the event/item text; it never invents a kind, and it never touches
# any kind other than 'test'.
_TEST_VOCAB_RE = re.compile(
    r"ניסוי|ניסויים|\btest\b|\btests\b|\btrial\b|\btrials\b|\bdemonstration\b|הדגמה|\bfiring\b|ירי",
    re.IGNORECASE,
)

#: W5: for kind in ('test', 'other') specifically, a "concrete anchor" is parties/customer/amount/
#: date -- deliberately narrower than :func:`_event_has_signal`'s general ``program``-inclusive
#: check, since an unanchored "test"/"other" row is exactly the class of near-empty, unclear-kind
#: row the review flagged.
_ANCHOR_KINDS_STRICT = ("test", "other")


def _looks_like_test(ev: dict[str, Any]) -> bool:
    text = " ".join(str(x) for x in (ev.get("title"), ev.get("summary_he"), ev.get("item_title")) if x)
    return bool(_TEST_VOCAB_RE.search(text))


def _sanitize_event_kind(ev: dict[str, Any]) -> dict[str, Any]:
    """W5: an event tagged ``kind='test'`` whose own title/summary carries none of the trial/test
    vocabulary is rewritten to ``'other'`` (logged) -- makes the upstream classifier's drift visible
    instead of silently mislabeling the report's events table."""
    if ev.get("kind") != "test" or _looks_like_test(ev):
        return ev
    log.info("event_kind_test_reclassified_other", event_id=ev.get("id"), item_id=ev.get("item_id"))
    ev = dict(ev)
    ev["kind"] = "other"
    return ev


def _has_concrete_anchor(ev: dict[str, Any]) -> bool:
    return bool(ev.get("parties") or ev.get("customer") or ev.get("amount_usd") is not None or ev.get("date"))


def _normalize_event_key(ev: dict[str, Any]) -> tuple[str, str, str, str]:
    """A dedup key for a business event: same kind + same normalised parties/customer/program is
    almost always the same underlying fact re-extracted from more than one covering article, or
    emitted more than once for a single item by the analyze stage (F9/F16)."""
    parties = ev.get("parties") or []
    parties_norm = tuple(sorted(p.strip().casefold() for p in parties if p and p.strip()))
    customer_norm = (ev.get("customer") or "").strip().casefold()
    program_norm = (ev.get("program") or "").strip().casefold()
    return (ev.get("kind") or "", "|".join(parties_norm), customer_norm, program_norm)


def _event_richness(ev: dict[str, Any]) -> int:
    score = sum(1 for k in ("date", "amount_usd", "currency", "customer", "program") if ev.get(k))
    return score + len(ev.get("parties") or [])


def _dedup_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse events sharing :func:`_normalize_event_key` across the whole report window,
    keeping the richest (most fields populated) row per group (F9/F16)."""
    best: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for ev in rows:
        key = _normalize_event_key(ev)
        cur = best.get(key)
        if cur is None or _event_richness(ev) > _event_richness(cur):
            best[key] = ev
    return list(best.values())


def _event_has_signal(ev: dict[str, Any]) -> bool:
    """An event with neither parties, a customer/program, nor an amount carries no information for
    the reader — drop it rather than render an almost-empty row (F9/F16). W5 (round 4): kind
    'test'/'other' rows are held to the stricter :func:`_has_concrete_anchor` test on top of this
    -- an unclear-kind row also needs a genuinely concrete anchor (parties/customer/amount/date),
    not just a bare ``program`` string, to be worth showing."""
    base = bool(
        ev.get("parties") or ev.get("customer") or ev.get("program") or ev.get("amount_usd") is not None
    )
    if not base:
        return False
    return not (ev.get("kind") in _ANCHOR_KINDS_STRICT and not _has_concrete_anchor(ev))


def _event_sort_key(ev: dict[str, Any]) -> tuple[dt.date, float]:
    date = ev.get("date") or dt.date.min
    amount = ev.get("amount_usd")
    try:
        amount_val = float(amount) if amount is not None else 0.0
    except (TypeError, ValueError):
        amount_val = 0.0
    return (date, amount_val)


#: W5 (round 4): a small grace window on the *start* side of the default (both-None) daily 24h
#: window only -- ``events.date`` is a DATE column with no time-of-day, so comparing it against a
#: sharp 24h-ago timestamp cutoff would clip an event dated "today" that happened to land a few
#: hours before the report actually ran. Applied only to the auto (both-None) daily case; an
#: explicit period_start/period_end (manual rebuild, or weekly.py's own 7-day range) is already a
#: whole-day range and gets no extra grace.
_EVENT_DAILY_GRACE = dt.timedelta(hours=6)


def collect_events(
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Business events (contract awards, M&A, ...) in the period, deduplicated, stripped of
    information-free rows, sorted date desc then amount desc (F9/F16), most recent first.
    ``limit`` (used by the weekly report to cap its table at 40 rows) is applied last, after
    dedup/filter/sort.

    W5 (round 4): the window filter is anchored on ``e.date`` -- or, only when that's ``NULL``, the
    source item's own ``published_at`` -- and nothing further. The previous version additionally
    fell back to ``i.fetched_at``/``i.created_at`` (ingestion time) whenever *both* were ``NULL``,
    which is how a business event derived from an undated, search-sourced item could show up in a
    "yesterday's events" table regardless of how old the underlying fact actually was; an event
    with no real anchor date now simply falls outside every window instead (``COALESCE(...)  IS
    NULL`` never satisfies a ``BETWEEN``). The default (both-None) daily window also gets a small
    grace period on its start side -- see :data:`_EVENT_DAILY_GRACE`.
    """
    start_ts, end_ts, _label = _period(period_start, period_end)
    if period_start is None and period_end is None:
        start_ts = start_ts - _EVENT_DAILY_GRACE
    start_date, end_date = start_ts.astimezone(JERUSALEM).date(), end_ts.astimezone(JERUSALEM).date()
    sql = """
        SELECT e.id, e.item_id, e.kind, e.title, e.date, e.amount_usd, e.currency, e.parties,
               e.customer, e.program, e.summary_he, e.confidence,
               i.url AS item_url, i.title AS item_title, i.published_at,
               COALESCE(src.name, i.url) AS source_name
        FROM events e
        JOIN items i ON i.id = e.item_id
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE COALESCE(e.date, i.published_at::date) BETWEEN %(start)s AND %(end)s
        ORDER BY e.date DESC NULLS LAST, e.id DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"start": start_date, "end": end_date})
        rows = cur.fetchall()
    rows = [_sanitize_event_kind(ev) for ev in rows]
    rows = _dedup_events(rows)
    rows = [ev for ev in rows if _event_has_signal(ev)]
    rows.sort(key=_event_sort_key, reverse=True)
    if limit is not None:
        rows = rows[:limit]
    return rows


def collect_deep_search(
    period_start: dt.date | None = None, period_end: dt.date | None = None
) -> list[dict[str, Any]]:
    """Deep-search jobs (``kind='deep_search'``) finished in the period, with the investigation's
    final result (``jobs.result``, an ``InvestigationOut``-shaped payload) when present."""
    start, end, _label = _period(period_start, period_end)
    sql = """
        SELECT j.id AS job_id, j.payload, j.result, j.state, j.finished_at,
               i.id AS trigger_item_id, i.title AS trigger_title, i.url AS trigger_url
        FROM jobs j
        LEFT JOIN items i ON i.id = NULLIF(j.payload->>'item_id', '')::bigint
        WHERE j.kind = 'deep_search'
          AND j.state IN ('done', 'partial')
          AND j.finished_at IS NOT NULL
          AND j.finished_at BETWEEN %(start)s AND %(end)s
        ORDER BY j.finished_at DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"start": start, "end": end})
        rows = cur.fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        payload = row.get("payload") or {}
        result = row.get("result") or {}
        out.append(
            {
                "job_id": row["job_id"],
                "trigger_item_id": row.get("trigger_item_id"),
                "trigger_title": row.get("trigger_title"),
                "trigger_url": row.get("trigger_url"),
                "question": payload.get("question"),
                "outcome": result.get("outcome") or row.get("state"),
                "answer_he": result.get("answer_he", ""),
                "confidence": result.get("confidence"),
                "sources": result.get("sources", []),
                "key_facts": result.get("key_facts", []),
                "contradictions_he": result.get("contradictions_he", ""),
            }
        )
    return reconcile_deep_search_reruns(out)


#: Outcome rank for :func:`reconcile_deep_search_reruns` -- a "found" answer beats a later
#: "not_found" for the same question (the later run usually failed on budget/search outage).
_OUTCOME_RANK = {"found": 4, "partial": 3, "off_topic": 1, "not_found": 0}


def reconcile_deep_search_reruns(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Round-3 judge (weekly 2026-09-06): the same investigation question appeared several times
    in one report with contradictory outcomes ("found" with an answer vs "not_found" ×3). Group
    entries by normalised question (and trigger item), keep the best-outcome run (ties → newest,
    i.e. first in the ``finished_at DESC`` order), and record ``rerun_count`` plus a Hebrew note
    so the reader sees one reconciled answer, not a contradiction."""
    groups: dict[tuple[Any, str], list[dict[str, Any]]] = {}
    order: list[tuple[Any, str]] = []
    for e in entries:
        q = " ".join((e.get("question") or "").split()).casefold()
        key = (e.get("trigger_item_id"), q)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(e)
    out: list[dict[str, Any]] = []
    for key in order:
        runs = groups[key]
        best = max(runs, key=lambda r: (_OUTCOME_RANK.get(r.get("outcome") or "", 0), -runs.index(r)))
        if len(runs) > 1:
            best = dict(best)
            best["rerun_count"] = len(runs)
            others = [r.get("outcome") for r in runs if r is not best]
            best["rerun_note_he"] = (
                f"השאלה נחקרה {len(runs)} פעמים השבוע; מוצגת הריצה עם התוצאה הטובה ביותר "
                f"(ריצות נוספות: {', '.join(str(o) for o in others)})."
            )
        out.append(best)
    return out


def _filter_deep_search_to_items_included(
    deep_search: list[dict[str, Any]], items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Q3-15 (docs/qa/findings_Q3_r1.md): drop a deep-search entry whose ``trigger_item_id`` is
    not one of this report's own ``items`` -- the "חקירות עומק" (deep investigations) section
    must never show an open question the reader has no way to cross-check because its item isn't
    in the report at all (an "orphaned" open question, per the finding). An entry with no
    ``trigger_item_id`` (a general question, not about any single item) is always kept -- it
    isn't orphaned relative to anything."""
    included_item_ids = {it["id"] for it in items if it.get("id") is not None}
    return [
        d
        for d in deep_search
        if d.get("trigger_item_id") is None or d.get("trigger_item_id") in included_item_ids
    ]


def collect_open_clarifications() -> list[dict[str, Any]]:
    """Clarifications still awaiting a user answer."""
    sql = """
        SELECT id, kind, question, options, asked_at, timeout_at
        FROM clarifications
        WHERE answered_at IS NULL
        ORDER BY asked_at ASC NULLS LAST, id ASC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


# --------------------------------------------------------------------------
# drafting
# --------------------------------------------------------------------------


#: Q3-15 (docs/qa/findings_Q3_r1.md): a section domain that doesn't resolve to any taxonomy key
#: (even via a fuzzy match) renders under this Hebrew label instead of the raw, meaningless slug
#: (e.g. a daily report once rendered the literal heading "naval_eo_ir").
_UNKNOWN_DOMAIN_LABEL_HE = "תחומים נוספים"
#: difflib.get_close_matches cutoff for resolving a near-miss domain slug (e.g. the model writing
#: "naval_eo_ir" for the real key "naval_surveillance") -- conservative enough that an unrelated
#: domain never gets fuzzy-matched to the wrong one.
_DOMAIN_FUZZY_CUTOFF = 0.5


def _domain_similarity(a: str, b: str) -> float:
    """difflib's ``SequenceMatcher.ratio()`` is not actually symmetric in practice (its greedy
    matching-block algorithm can find a different total match length depending on which string is
    ``a`` vs ``b``, e.g. "naval_eo_ir"/"naval_surveillance" score 0.55 one way and 0.48 the other)
    -- take the more generous of the two orderings so a real near-miss slug isn't missed on
    account of comparison order alone."""
    import difflib

    return max(
        difflib.SequenceMatcher(None, a, b).ratio(),
        difflib.SequenceMatcher(None, b, a).ratio(),
    )


def _resolve_domain_key(domain: str | None) -> str | None:
    """Q3-15: map ``domain`` (as returned by the report-drafting LLM, or an item's own already-
    validated `domain` column) to a real ``config/taxonomy.yaml`` domain key. An exact match wins
    outright; otherwise the closest key by string similarity, if any clears
    :data:`_DOMAIN_FUZZY_CUTOFF`. Returns ``None`` when nothing reasonable matches -- the caller
    (:func:`_domain_label`) is what decides the fallback label, this function never itself
    invents or guesses a taxonomy key it isn't reasonably confident about."""
    if not domain:
        return None
    domains = list(settings().taxonomy.get("domains", {}).keys())
    if domain in domains:
        return domain
    best_key, best_score = None, 0.0
    for key in domains:
        score = _domain_similarity(domain, key)
        if score > best_score:
            best_key, best_score = key, score
    return best_key if best_score >= _DOMAIN_FUZZY_CUTOFF else None


def _domain_label(domain: str | None) -> str:
    """Hebrew label for ``domain`` -- never the raw slug itself when it isn't (or doesn't fuzzy-
    match) a real taxonomy key (Q3-15)."""
    if not domain:
        return "כללי"
    domains = settings().taxonomy.get("domains", {})
    resolved = _resolve_domain_key(domain)
    if resolved is None:
        return _UNKNOWN_DOMAIN_LABEL_HE
    entry = domains.get(resolved, {})
    label = entry.get("label")
    return label if isinstance(label, str) and label else _UNKNOWN_DOMAIN_LABEL_HE


def _level_label(level: str | None) -> str:
    levels = settings().taxonomy.get("triage_levels", {})
    entry = levels.get(level or "", {})
    emoji = entry.get("emoji", "")
    label = entry.get("label", level or "")
    return f"{emoji} {label}".strip()


def _group_by_domain(items: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    order = list(settings().taxonomy.get("domains", {}).keys())
    buckets: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        buckets.setdefault(it.get("domain") or "secondary", []).append(it)
    ordered = [d for d in order if d in buckets] + [d for d in buckets if d not in order]
    return [(d, buckets[d]) for d in ordered]


def _format_items_block(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for domain, group in _group_by_domain(items):
        lines.append(f"### {_domain_label(domain)}")
        for it in group:
            key_facts = it.get("key_facts") or []
            kf = "; ".join(key_facts) if key_facts else "—"
            lines.append(
                f"[{it['n']}] כותרת: {it.get('title') or '—'} | מקור: "
                f"{it.get('source_name') or it.get('url') or '—'} | תאריך: {fmt_date(it.get('published_at'))} "
                f"| רמה: {_level_label(it.get('level'))}"
            )
            lines.append(f"תקציר: {it.get('summary_he') or '—'}")
            lines.append(f"מה זה אומר: {it.get('so_what_he') or '—'}")
            lines.append(f"עובדות מפתח: {kf}")
            lines.append("")
    return "\n".join(lines)


def _normalize_section_titles(draft: DailyReportDraft) -> DailyReportDraft:
    """Force every section's ``title_he`` to the authoritative taxonomy label for its ``domain``
    (F7): the report prompt asks the model to copy the domain heading verbatim, but a heading like
    'נגד כטב"מים (C-UAS)' contains an embedded literal ``"`` that a small local model's JSON output
    sometimes fails to escape correctly, truncating the string at that quote ('נגד כטב'). Rather
    than depend on the model's JSON-escaping fidelity for a value we already know deterministically
    ("config, not code"), the section's own ``domain`` key (validated separately) is used to look
    the label up again here — a domain not found in the taxonomy keeps whatever the model wrote."""
    new_sections = [
        section.model_copy(update={"title_he": _domain_label(section.domain)}) if section.domain else section
        for section in draft.sections
    ]
    return draft.model_copy(update={"sections": new_sections})


def _no_items_draft() -> DailyReportDraft:
    return DailyReportDraft(
        exec_summary=[],
        sections=[],
        system_note_he=(
            "לא זוהו בתקופה זו פריטים חדשים ברמת חשיבות red/orange (ואף לא ברמת yellow כחלופה). "
            "אין ממצאים לדיווח היום."
        ),
        outlook=[],
        open_points_he=[],
    )


@dataclass
class TableCounts:
    """Q3-14 (docs/qa/findings_Q3_r1.md): counts of report content that is rendered as a table
    rather than drafted by the LLM (events, open tenders, new tender forecasts, completed deep
    searches) -- ``draft_report`` needs these to know the exec summary must never claim "no
    findings" while these tables are non-empty, even when the LLM-facing ``items`` list is empty
    or thin (A9's own ``collect_items`` filter is unrelated and stays untouched)."""

    events: int = 0
    open_tenders: int = 0
    new_forecasts: int = 0
    deep_search: int = 0

    @property
    def total(self) -> int:
        return self.events + self.open_tenders + self.new_forecasts + self.deep_search

    def context_he(self) -> str:
        if not self.total:
            return "אין (כל הטבלאות ריקות בתקופה זו)."
        parts = []
        if self.events:
            parts.append(f"{self.events} אירועים עסקיים/מבצעיים")
        if self.open_tenders:
            parts.append(f"{self.open_tenders} מכרזים פתוחים")
        if self.new_forecasts:
            parts.append(f"{self.new_forecasts} תחזיות מכרזים חדשות/מעודכנות")
        if self.deep_search:
            parts.append(f"{self.deep_search} חקירות עומק שהושלמו")
        return "; ".join(parts) + "."


def _tables_only_draft(counts: TableCounts) -> DailyReportDraft:
    """Q3-14: used when ``items`` is empty but at least one table (events/tenders/forecasts/deep
    search) is not -- a short, honest, deterministic summary of what the report *does* contain,
    instead of ``_no_items_draft``'s blanket "no findings" (which used to run unconditionally
    whenever the LLM-facing items list was empty, even with full tables right below it)."""
    return DailyReportDraft(
        exec_summary=[],
        sections=[],
        system_note_he=(
            "לא זוהו בתקופה זו פריטי חדשות חדשים ברמת חשיבות red/orange/yellow, אך קיים תוכן "
            f"רלוונטי בטבלאות הדוח: {counts.context_he()} פירוט מלא בטבלאות בהמשך הדוח."
        ),
        outlook=[],
        open_points_he=[],
    )


def _qa_failed_twice_draft() -> DailyReportDraft:
    """Kept only for a report with *zero* qualifying items when the LLM path itself was somehow
    still exercised (should not happen in practice -- ``draft_report``/``build_daily`` never call
    the two-failure path with an empty ``items``, see the ``if not qa.passed and items`` guards).
    The normal two-failure fallback since round 3 (2026-09-06) is
    :func:`_deterministic_fallback_draft`, which -- unlike this function -- has real per-item data
    to build a cited substitute summary from."""
    return DailyReportDraft(
        exec_summary=[],
        sections=[],
        system_note_he=(
            "הטיוטה הטקסטואלית של הדוח לא עברה את בדיקת האזכורים גם לאחר ניסיון תיקון, ולכן הושמטה "
            "במלואה מדוח זה כדי לא להציג ניסוח חלקי או לא מאומת. הטבלאות הדטרמיניסטיות (אירועים, "
            "מכרזים, מעקב טכנולוגי) ונספח המקורות שלהלן אינם מושפעים ומוצגים במלואם."
        ),
        outlook=[],
        open_points_he=[],
    )


# --------------------------------------------------------------------------
# round-3 (2026-09-06, D6 judge finding 1): deterministic substitute synthesis for a draft that
# fails citation QA twice -- a cited, honestly-labelled executive summary built straight from
# already-numbered report data (no LLM), rather than dropping the narrative entirely.
# --------------------------------------------------------------------------

_FALLBACK_TOP_ITEMS = 6
_FALLBACK_TOP_EVENTS = 5
_FALLBACK_TOP_ISRAEL_ITEMS = 5
_FALLBACK_TEXT_TRUNC_CHARS = 220

#: Small local copy of ``eoa.report.docx_builder._EVENT_KIND_LABELS_HE`` (same convention already
#: used throughout this package -- see e.g. ``eoa.report.weekly``'s module docstring -- of a
#: report-building module keeping its own copy of a rendering label map rather than importing a
#: private name across a module boundary).
_EVENT_KIND_LABELS_HE_FALLBACK = {
    "contract_award": "זכייה בחוזה",
    "m_and_a": "מיזוג/רכישה",
    "partnership": "שותפות",
    "investment": "השקעה",
    "launch": "השקה",
    "test": "ניסוי",
    "deployment": "פריסה",
    "regulation": "רגולציה",
    "other": "אחר",
}


def _extend_registry_with_rows(citation_items: list[dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    """Mutate ``citation_items`` in place, appending any ``rows`` entry (a dict carrying ``id``)
    not already present by id, and stamping ``row["n"]`` with the (possibly pre-existing) registry
    number -- same convention as :func:`_extend_citation_registry`. Used by
    :func:`_deterministic_fallback_draft` to fold Israel-relevant items into the citation registry
    before ``daily_israel_tables`` runs its own (idempotent) extension of the same list later."""
    by_id = {it["id"]: it for it in citation_items if it.get("id") is not None}
    next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
    for row in rows:
        rid = row.get("id")
        if rid is None:
            continue
        entry = by_id.get(rid)
        if entry is None:
            entry = {
                "id": rid,
                "n": next_n,
                "title": row.get("title"),
                "source_name": row.get("source_name"),
                "url": row.get("url"),
                "published_at": row.get("published_at"),
            }
            citation_items.append(entry)
            by_id[rid] = entry
            next_n += 1
        row["n"] = entry["n"]


def _fallback_truncate(text: str | None, limit: int = _FALLBACK_TEXT_TRUNC_CHARS) -> str:
    text = (text or "").strip()
    if not text:
        return "—"
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _fallback_top_item_sentences(items: list[dict[str, Any]], *, limit: int) -> list[Sentence]:
    """One cited ``Sentence`` per top item (already score-ordered by ``collect_items``): title +
    level + a one-line ``so_what_he``/``summary_he`` -- judge finding 1's "top red/orange items".

    W9 (round 4): items that are really the same underlying story covered by more than one outlet
    (shared ``dedup_of``, or a near-identical title -- see ``eoa.report.clustering``) are folded
    into one sentence, citing the richest item first and every other outlet's own registry number
    right after it, with a "(+N מקורות נוספים)" note -- instead of one near-duplicate sentence per
    outlet."""
    from eoa.report.clustering import cluster_extra_ns, cluster_items, extra_sources_note_he

    clusters = cluster_items(items)
    sentences: list[Sentence] = []
    for cluster in clusters[:limit]:
        it = cluster.primary
        n = it.get("n")
        if n is None:
            continue
        level = _level_label(it.get("level"))
        title = it.get("title") or "—"
        so_what = _fallback_truncate(it.get("so_what_he") or it.get("summary_he"))
        extra_ns = cluster_extra_ns(cluster)
        note = extra_sources_note_he(cluster) if extra_ns else ""
        sentences.append(Sentence(text_he=f"{title} ({level}): {so_what}{note}", cites=[int(n), *extra_ns]))
    return sentences


def _fallback_event_sentences(events_with_n: list[dict[str, Any]], *, limit: int) -> list[Sentence]:
    """One cited ``Sentence`` per notable business event (kind, parties, customer/program, amount,
    date) -- judge finding 1's "day's notable events". Only events already carrying a registry
    ``n`` (every event with an ``item_id`` gets one via :func:`_extend_citation_registry`) can be
    cited here; an event with no linked item is skipped (it still renders in the events table)."""
    ranked = sorted(
        (ev for ev in events_with_n if ev.get("n") is not None), key=_event_sort_key, reverse=True
    )
    sentences: list[Sentence] = []
    for ev in ranked[:limit]:
        kind_label = _EVENT_KIND_LABELS_HE_FALLBACK.get(ev.get("kind"), ev.get("kind") or "אחר")
        parties = ", ".join(ev.get("parties") or []) or "—"
        customer_program = ev.get("customer") or ev.get("program") or "—"
        sentences.append(
            Sentence(
                text_he=(
                    f"{kind_label}: {parties} — {customer_program}, {fmt_amount(ev)}, "
                    f"בתאריך {fmt_date(ev.get('date'))}."
                ),
                cites=[int(ev["n"])],
            )
        )
    return sentences


def _fallback_israel_item_sentences(israel_items: list[dict[str, Any]], *, limit: int) -> list[Sentence]:
    """One cited ``Sentence`` per Israel-relevant item for the period -- judge finding 1's "Israel-
    relevant items". ``israel_items`` must already carry a registry ``n`` (see
    :func:`_extend_registry_with_rows`, called on this same list before this runs)."""
    sentences: list[Sentence] = []
    for it in israel_items[:limit]:
        n = it.get("n")
        if n is None:
            continue
        title = it.get("title") or "—"
        so_what = _fallback_truncate(it.get("so_what_he") or it.get("summary_he"))
        sentences.append(Sentence(text_he=f"רלוונטיות ישראלית — {title}: {so_what}", cites=[int(n)]))
    return sentences


def _deterministic_fallback_draft(
    items: list[dict[str, Any]],
    events_with_n: list[dict[str, Any]],
    israel_items: list[dict[str, Any]],
) -> DailyReportDraft:
    """Round 3 (2026-09-06, D6 judge finding 1): when the LLM-drafted narrative still fails
    citation QA after one corrective retry, this replaces
    :func:`_qa_failed_twice_draft`'s old "drop the executive summary entirely" behaviour with a
    deterministic (no LLM) substitute executive summary built straight from already-numbered
    report data: the top red/orange items, the day's notable business events, and the
    Israel-relevant items for the period. Every sentence here cites a real, already-registered
    item ``n`` pulled directly from the data itself -- there is no free-text generation step for an
    uncited claim to slip through, so this cannot fail :func:`eoa.report.qa_citations.check` (the
    caller still runs it once anyway, defensively -- see ``build_daily``). ``system_note_he``
    carries the honest explanation of why the model's own draft was dropped, labelled up front so a
    reader never mistakes this deterministic summary for the model's own analysis."""
    sentences: list[Sentence] = []
    sentences.extend(_fallback_top_item_sentences(items, limit=_FALLBACK_TOP_ITEMS))
    sentences.extend(_fallback_event_sentences(events_with_n, limit=_FALLBACK_TOP_EVENTS))
    sentences.extend(_fallback_israel_item_sentences(israel_items, limit=_FALLBACK_TOP_ISRAEL_ITEMS))
    return DailyReportDraft(
        exec_summary=sentences,
        sections=[],
        system_note_he=(
            "תקציר מובנה אוטומטית (ללא ניסוח מודל): הטיוטה הטקסטואלית של הדוח לא עברה את בדיקת "
            "האזכורים גם לאחר ניסיון תיקון, ולכן ניסוח המודל הושמט במלואו. התקציר שלעיל הופק ישירות "
            "מנתוני מסד הנתונים (ללא ניסוח חופשי של מודל), ומכיל את הפריטים המובילים, האירועים "
            "העסקיים הבולטים והפריטים הרלוונטיים לתעשייה הישראלית לתקופה זו — כל משפט כאן מצוטט "
            "למקורו. הטבלאות הדטרמיניסטיות (אירועים, מכרזים, מעקב טכנולוגי) ונספח המקורות שלהלן "
            "אינם מושפעים ומוצגים במלואם."
        ),
        outlook=[],
        open_points_he=[],
    )


def draft_report(
    items: list[dict[str, Any]],
    *,
    role: str = "resident",
    interactive: bool = False,
    table_counts: TableCounts | None = None,
) -> DailyReportDraft:
    """Draft the ``DailyReportDraft`` via the resident model; zero items skip the LLM call
    entirely -- Q3-14: falling back to :func:`_tables_only_draft` rather than
    :func:`_no_items_draft` when ``table_counts`` (events/tenders/forecasts/deep-search) shows
    there is other report content the exec summary should not contradict."""
    counts = table_counts or TableCounts()
    if not items:
        return _tables_only_draft(counts) if counts.total else _no_items_draft()
    prompt = render(
        "report_daily",
        date_he=hebrew_date_str(_today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        counts_context_he=counts.context_he(),
        items_block=wrap_data(_format_items_block(items), "report_items", "internal"),
    )
    draft = chat_structured(
        role,
        DailyReportDraft,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="report",
        interactive=interactive,
        options={"temperature": 0.3},
    )
    return _normalize_section_titles(draft)


def _corrective_retry(
    items: list[dict[str, Any]],
    draft: DailyReportDraft,
    qa: QAResult,
    *,
    role: str,
    interactive: bool,
    table_counts: TableCounts | None = None,
) -> DailyReportDraft:
    prompt = render(
        "report_daily",
        date_he=hebrew_date_str(_today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        counts_context_he=(table_counts or TableCounts()).context_he(),
        items_block=wrap_data(_format_items_block(items), "report_items", "internal"),
    )
    errors_text = "\n".join(f"- {e}" for e in qa.errors[:30])
    correction = (
        "הטיוטה הקודמת שלך נכשלה בבדיקת האזכורים האוטומטית. תקן את כל הבעיות הבאות והחזר טיוטה מלאה "
        "ותקינה מחדש (JSON לפי הסכמה בלבד, ללא הסברים נוספים), מבלי להמציא עובדות חדשות שלא הופיעו "
        'ברשימת הפריטים. שים לב: אסור לכתוב "[n]" בטקסט עצמו -- מספרי ההפניה שייכים אך ורק לשדה '
        "cites של כל משפט:\n" + errors_text
    )
    draft = chat_structured(
        role,
        DailyReportDraft,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": draft.model_dump_json()},
            {"role": "user", "content": correction},
        ],
        task="report",
        interactive=interactive,
        options={"temperature": 0.2},
    )
    return _normalize_section_titles(draft)


# --------------------------------------------------------------------------
# citation registry (items + any extra items only referenced by an event)
# --------------------------------------------------------------------------


def _extend_citation_registry(
    items: list[dict[str, Any]], events: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(citation_items, events)`` where every event carries an ``n`` into ``citation_items``
    — extending the numbered item list with any event source item that wasn't already in it, so the
    business table and the sources appendix stay in sync without touching the LLM-facing ``items``
    list used for citation-range QA."""
    citation_items = list(items)
    by_item_id = {it["id"]: it for it in citation_items if it.get("id") is not None}
    next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
    events_out: list[dict[str, Any]] = []
    for ev in events:
        item_id = ev.get("item_id")
        entry = by_item_id.get(item_id) if item_id is not None else None
        if entry is None and item_id is not None:
            entry = {
                "id": item_id,
                "n": next_n,
                "title": ev.get("item_title"),
                "source_name": ev.get("source_name"),
                "url": ev.get("item_url"),
                "published_at": ev.get("published_at"),
            }
            citation_items.append(entry)
            by_item_id[item_id] = entry
            next_n += 1
        ev_out = dict(ev)
        ev_out["n"] = entry.get("n") if entry else None
        events_out.append(ev_out)
    return citation_items, events_out


# --------------------------------------------------------------------------
# persistence / paths
# --------------------------------------------------------------------------


def _report_path(period_end: dt.date, ext: str) -> Path:
    out_dir = Path(settings().report.output_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    return out_dir / f"daily_{period_end.isoformat()}.{ext}"


def _persist_report(
    period_start: dt.date,
    period_end: dt.date,
    docx_path: Path,
    md_path: Path,
    html_path: Path,
    items: list[dict[str, Any]],
    qa: QAResult,
    *,
    report_state: dict[str, Any] | None = None,
) -> int:
    qa_report = {
        "passed": qa.passed,
        "errors": qa.errors,
        "uncited_sentences": qa.uncited_sentences,
        "bad_refs": qa.bad_refs,
        "duplicate_sentences": qa.duplicate_sentences,
    }
    item_ids = [it["id"] for it in items if it.get("id") is not None]
    # Round 5 P2 (docs/PLAN_ROUND5_REPORTS.md P2): `report_state` is the raw material
    # `eoa.report.deltas.previous_report_state` reads back for the *next* daily report's delta --
    # see `eoa.report.deltas.build_report_state`. `None` (a manual caller that doesn't pass it) is
    # persisted as SQL NULL, same as every report built before this migration.
    sql = """
        INSERT INTO reports (kind, period_start, period_end, path_docx, path_md, path_html,
                              items_included, qa_passed, qa_report, report_state)
        VALUES ('daily', %(start)s, %(end)s, %(docx)s, %(md)s, %(html)s, %(items)s, %(qa_passed)s,
                %(qa_report)s, %(report_state)s)
        RETURNING id
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            sql,
            {
                "start": period_start,
                "end": period_end,
                "docx": str(docx_path),
                "md": str(md_path),
                "html": str(html_path),
                "items": item_ids,
                "qa_passed": qa.passed,
                "qa_report": Json(qa_report),
                "report_state": Json(report_state) if report_state is not None else None,
            },
        )
        report_id: int = cur.fetchone()["id"]
    log.info("report_persisted", report_id=report_id, qa_passed=qa.passed)
    return report_id


def _recent_daily_report(period_end: dt.date, *, within_hours: int = 6) -> ReportPaths | None:
    """F4 idempotency guard: a ``daily`` report for ``period_end`` already built within the last
    ``within_hours`` hours, reconstructed as :class:`ReportPaths` — or ``None`` if there isn't one.
    Used by :func:`build_daily` (unless ``force=True``) to avoid building a second, near-identical
    report when e.g. both a ``daily_run`` and a ``weekly_run`` job land on the same night."""
    sql = """
        SELECT id, path_docx, path_md, path_html, qa_passed, qa_report
        FROM reports
        WHERE kind = 'daily' AND period_end = %(period_end)s
          AND created_at > now() - make_interval(hours => %(hours)s)
        ORDER BY created_at DESC
        LIMIT 1
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"period_end": period_end, "hours": within_hours})
        row = cur.fetchone()
    if row is None:
        return None
    qa_report = row.get("qa_report") or {}
    qa = QAResult(
        passed=bool(row.get("qa_passed")),
        errors=qa_report.get("errors", []),
        uncited_sentences=qa_report.get("uncited_sentences", []),
        bad_refs=qa_report.get("bad_refs", []),
        duplicate_sentences=qa_report.get("duplicate_sentences", []),
    )
    log.info("daily_report_reused", report_id=row["id"], period_end=str(period_end))
    return ReportPaths(
        docx=Path(row["path_docx"]),
        md=Path(row["path_md"]),
        html=Path(row["path_html"]),
        report_id=row["id"],
        qa=qa,
    )


def _tenders_forecast_table(
    tenders_data: dict[str, list[dict[str, Any]]], citation_items: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """F6: a compact tender-forecasts sub-table (platform | payload | likelihood | window | נימוק |
    מקורות), rendered here (not by ``eoa.tenders.report_section``) from ``collect_tenders``'s data
    (already deduped by topic -- W1, see ``report_section.dedupe_forecasts_by_topic``) — replaces
    the old ``tenders_extra_section`` bulleted prose block, which duplicated the open-tenders board
    (rendered separately by ``tenders_table``) and printed the full, unbounded rationale text.
    ``None`` when there is nothing to show.

    W6 (round 4): every row now carries a "מקורות" column citing the forecast's own trigger
    items — ``eoa.tenders.report_section.attach_forecast_citations`` (called here, mutating
    ``citation_items`` in place, same convention as ``eoa.report.tech_watch``) registers each
    forecast's trigger items into the report's own citation registry so those ``[n]`` markers
    resolve in the "נספח מקורות" appendix (with a real, clickable source link) exactly like every
    other cited claim in the report.
    """
    forecasts = tenders_data.get("new_forecasts") or []
    if not forecasts:
        return None
    from eoa.tenders.report_section import attach_forecast_citations

    attach_forecast_citations(citation_items, forecasts)
    headers = ["פלטפורמה", "צורך/Payload", "סבירות", "חלון", "נימוק", "מקורות"]
    rows: list[list[Any]] = []
    for f in forecasts[:10]:
        likelihood = f.get("likelihood")
        pct = f"{likelihood:.0%}" if isinstance(likelihood, int | float) else "—"
        window = f"{fmt_date(f.get('window_from'))} - {fmt_date(f.get('window_to'))}"
        rationale = (f.get("rationale_he") or "").strip() or "—"
        if len(rationale) > 200:
            rationale = rationale[:199] + "…"
        citation_ns = f.get("_citation_ns") or []
        sources_cell = "".join(f"[{n}]" for n in citation_ns) or "—"
        rows.append(
            [f.get("platform") or "—", f.get("payload_need") or "—", pct, window, rationale, sources_cell]
        )
    return {"title_he": "תחזיות מכרזים", "headers": headers, "rows": rows, "no_dedupe": True}


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def build_daily(
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
    *,
    role: str = "resident",
    interactive: bool = False,
    force: bool = False,
) -> ReportPaths:
    """Collect -> draft -> QA-gate -> render docx/md/html -> persist. Returns the written paths.

    F4: unless ``force=True`` (set by the manual ``eo run report`` CLI command), a daily report for
    the same ``period_end`` built less than 6 hours ago is returned as-is instead of building
    another one — guards against e.g. both ``daily_run`` and ``weekly_run`` landing on the same
    night and each triggering a full report build.
    """
    start_ts, _end_ts, label = _period(period_start, period_end)

    if not force:
        existing = _recent_daily_report(label)
        if existing is not None:
            return existing

    items = collect_items(period_start, period_end)
    events = collect_events(period_start, period_end)
    deep_search = collect_deep_search(period_start, period_end)
    open_clarifications = collect_open_clarifications()

    # Round 3 (2026-09-06): computed up front (moved from right before rendering) so
    # `_deterministic_fallback_draft` (the two-failure QA fallback below) has a real, already-
    # numbered events registry to cite from -- purely a computation-order change, `citation_items`/
    # `events_with_n` are otherwise used exactly as before (rendering, additive tables).
    citation_items, events_with_n = _extend_citation_registry(items, events)

    # Q3-15: the "חקירות עומק" section must not show an open question about an item that isn't
    # actually in this report.
    deep_search = _filter_deep_search_to_items_included(deep_search, items)

    # Q3-14 (docs/qa/findings_Q3_r1.md): tenders/forecasts are collected here, *before* drafting,
    # so their counts can be handed to draft_report -- an empty/thin `items` list must not produce
    # an exec summary claiming "no findings" when these tables (rendered further down, unchanged)
    # are not empty. Moved up from its previous position right before rendering; the tenders_data
    # this computes is reused there unchanged (no second `collect_tenders` call).
    tenders_data: dict[str, list[dict[str, Any]]] = {}
    try:
        from eoa.tenders.report_section import collect_tenders

        window_start = start_ts.astimezone(JERUSALEM).date()
        tenders_data = collect_tenders(window_start, label)
    except Exception as exc:
        log.warning("daily_report_tenders_collect_failed", error=str(exc)[:160])

    table_counts = TableCounts(
        events=len(events),
        open_tenders=len(tenders_data.get("open_tenders") or []),
        new_forecasts=len(tenders_data.get("new_forecasts") or []),
        deep_search=len(deep_search),
    )

    draft = draft_report(items, role=role, interactive=interactive, table_counts=table_counts)
    qa = check(draft, items)

    if not qa.passed and items:
        log.warning("report_qa_failed_retrying", errors=qa.errors[:10])
        draft = _corrective_retry(
            items, draft, qa, role=role, interactive=interactive, table_counts=table_counts
        )
        qa = check(draft, items)

    if not qa.passed and items:
        # Round 3 (2026-09-06, D6 judge finding 1): two failures (initial draft + one corrective
        # retry) replace the narrative with a deterministic, cited substitute synthesis built
        # straight from the data -- see `_deterministic_fallback_draft` (supersedes goal 1's
        # original "drop the narrative entirely" behaviour, kept only as `_qa_failed_twice_draft`
        # for the items-somehow-empty edge case). The original QA errors are kept in `qa_report`
        # (persisted below) for the analyst to review; `qa.passed` stays False either way -- the
        # model's own draft did genuinely fail, that fact is not hidden by having a better
        # fallback to show in its place.
        log.error("report_qa_failed_twice_using_deterministic_fallback", errors=qa.errors[:10])
        original_errors = qa
        israel_items: list[dict[str, Any]] = []
        try:
            from eoa.report.israel_section import collect_israel_items

            israel_items = collect_israel_items(start_ts, _end_ts)
            _extend_registry_with_rows(citation_items, israel_items)
        except Exception as exc:
            log.warning("daily_report_fallback_israel_collect_failed", error=str(exc)[:160])
        draft = _deterministic_fallback_draft(items, events_with_n, israel_items)
        fallback_qa = check(draft, citation_items)
        if not fallback_qa.passed:
            # Should not happen -- every sentence here cites an id already present in
            # `citation_items` -- but never silently ship an uncited fallback either.
            log.error("daily_report_fallback_draft_failed_citation_check", errors=fallback_qa.errors[:10])
        qa = QAResult(
            passed=False,
            errors=original_errors.errors,
            uncited_sentences=original_errors.uncited_sentences,
            bad_refs=original_errors.bad_refs,
            duplicate_sentences=original_errors.duplicate_sentences,
        )

    draft = normalize_draft(draft)

    # Round 5 P2 (docs/PLAN_ROUND5_REPORTS.md P2, D4/D5): "מה השתנה מאז הדוח הקודם" (deterministic
    # delta vs. the previous daily report) and the "מעקב אינדיקטורים" (I&W) watchlist table -- both
    # additive `extra_sections` entries, computed here (after the draft/fallback is final) so the
    # delta's "top new items" and the indicator maturation check both see this issue's real,
    # already-numbered item list. A failure in either must never break the daily report.
    extra_sections: list[dict[str, Any]] = []
    indicator_rows: list[dict[str, Any]] = []
    try:
        from eoa.report import deltas

        delta_result = deltas.compute_deltas("daily", items, before_period_end=label)
        extra_sections.append(deltas.delta_extra_section(delta_result))
    except Exception as exc:
        log.warning("daily_report_deltas_section_failed", error=str(exc)[:160])
    try:
        from eoa.report import indicators

        indicator_section, indicator_rows = indicators.build_indicator_watchlist_section(
            "daily", draft.outlook, items, citation_items
        )
        if indicator_section:
            extra_sections.append(indicator_section)
    except Exception as exc:
        log.warning("daily_report_indicator_watchlist_failed", error=str(exc)[:160])

    # section 5.2 / FR-5.2: tenders/RFI/RFP -- deterministic (not LLM-drafted), so it is rendered
    # via the additive tables hook below rather than touching DailyReportDraft or the citation QA
    # gate. F6: ONE rendering of tenders (the open-tenders board) plus a compact forecasts
    # sub-table -- the old tenders_extra_section bulleted block (duplicating the same open tenders
    # as prose, and printing the full unbounded forecast rationale) is no longer used here. A
    # failure here must never break the daily report. (tenders_data itself was already collected
    # above, before drafting, for Q3-14's table counts -- not re-fetched here.)
    tender_tables: list[dict[str, Any]] = []
    try:
        from eoa.tenders.report_section import tenders_table

        # W7 (round 4): a cheap, budget-bounded liveness+staleness probe over every open-tender
        # link before it renders as "open" -- see eoa.report.link_check module docstring for the
        # LITENING-2015 motivating example. One cache, built once per report build; a failure here
        # (network layer entirely unavailable, event loop issue, ...) degrades to no cache at all,
        # which `tenders_table` treats identically to "not checked" (renders unchanged).
        link_cache: dict[str, Any] | None = None
        try:
            from eoa.report.link_check import check_urls

            tender_urls = [t["url"] for t in (tenders_data.get("open_tenders") or []) if t.get("url")]
            if tender_urls:
                link_cache = check_urls(tender_urls)
        except Exception as exc:
            log.warning("daily_report_link_check_failed", error=str(exc)[:160])

        open_table = tenders_table(tenders_data, link_cache=link_cache)
        if open_table:
            tender_tables.append(open_table)
        forecast_table = _tenders_forecast_table(tenders_data, citation_items)
        if forecast_table:
            tender_tables.append(forecast_table)
    except Exception as exc:
        log.warning("daily_report_tenders_section_failed", error=str(exc)[:160])

    # A12 (מעקב טכנולוגי): deterministic (not LLM-drafted) tech_dev items-of-the-day table --
    # same additive-tables mechanism as the tenders section above; extends `citation_items` in
    # place so its `[n]` refs resolve in the "נספח מקורות" appendix. A failure here must never
    # break the daily report.
    try:
        from eoa.report.tech_watch import daily_tech_watch_table

        tech_table = daily_tech_watch_table(citation_items, start_ts, _end_ts)
        if tech_table:
            tender_tables.append(tech_table)
    except Exception as exc:
        log.warning("daily_report_tech_watch_section_failed", error=str(exc)[:160])

    # A13 (מיקוד תעשייה ישראלית, 2026-09-06): "תעשייה ישראלית" section -- deterministic category
    # tables extending `citation_items` in place, same additive mechanism as the tech-watch table
    # above. A failure here must never break the daily report.
    try:
        from eoa.report.israel_section import daily_israel_tables

        tender_tables.extend(daily_israel_tables(citation_items, start_ts, _end_ts))
    except Exception as exc:
        log.warning("daily_report_israel_section_failed", error=str(exc)[:160])

    docx_path = _report_path(label, "docx")
    md_path = _report_path(label, "md")
    html_path = _report_path(label, "html")

    # U8-4 (docs/adr/005-cloud-llm-cli.md, Revision 2026-09-06): a one-line cloud-usage footer --
    # "מודלים: X קריאות ענן, Y נפלו למקומי, עלות משוערת $Z" -- appended to every rendering of the
    # report. Empty (no line added) when there was no cloud activity in the last 24h (the common
    # case in local mode). Best-effort: a failure here must never break the report itself.
    llm_footer_he = ""
    try:
        from eoa.llm.cost import format_daily_report_footer
        from eoa.memory.relational import summarize_llm_calls

        llm_footer_he = format_daily_report_footer(summarize_llm_calls(24).get("totals", {}))
    except Exception as exc:
        log.debug("daily_report_llm_footer_unavailable", error=str(exc)[:120])

    doc = build_docx(
        draft,
        citation_items,
        events_with_n,
        period_end=label,
        deep_search=deep_search,
        open_clarifications=open_clarifications,
        qa=qa,
        extra_sections=extra_sections or None,
        tables=tender_tables,
    )
    if llm_footer_he:
        doc.add_paragraph(llm_footer_he)
    save_docx(doc, docx_path)
    validate_docx(docx_path)

    md_text = render_markdown(
        draft,
        citation_items,
        events_with_n,
        period_end=label,
        deep_search=deep_search,
        open_clarifications=open_clarifications,
        qa=qa,
        extra_sections=extra_sections or None,
        tables=tender_tables,
    )
    if llm_footer_he:
        md_text = f"{md_text}\n\n---\n\n{llm_footer_he}\n"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")

    html_text = render_html(
        draft,
        citation_items,
        events_with_n,
        period_end=label,
        deep_search=deep_search,
        open_clarifications=open_clarifications,
        qa=qa,
        extra_sections=extra_sections or None,
        tables=tender_tables,
    )
    if llm_footer_he:
        footer_html = f'<p class="llm-footer">{llm_footer_he}</p>'
        html_text = (
            html_text.replace("</body>", f"{footer_html}</body>")
            if "</body>" in html_text
            else html_text + footer_html
        )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")

    period_start_date = start_ts.astimezone(JERUSALEM).date()
    # Round 5 P2: this issue's own state, persisted for the *next* daily report's delta.
    try:
        from eoa.report import deltas

        open_indicator_ids = [
            r["id"]
            for r in indicator_rows
            if r.get("_row_status") in ("open", "new") and r.get("id") is not None
        ]
        report_state = deltas.build_report_state(items, indicator_ids=open_indicator_ids)
    except Exception as exc:
        log.warning("daily_report_state_build_failed", error=str(exc)[:160])
        report_state = None
    report_id = _persist_report(
        period_start_date, label, docx_path, md_path, html_path, items, qa, report_state=report_state
    )

    return ReportPaths(docx=docx_path, md=md_path, html=html_path, report_id=report_id, qa=qa)
