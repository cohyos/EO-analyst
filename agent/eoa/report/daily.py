"""Stage: report/daily — collect analyzed items, draft the daily report, QA-gate citations, render.

Pipeline: ``collect_items`` (+ ``collect_events`` / ``collect_deep_search`` / ``collect_open_clarifications``)
-> ``draft_report`` (resident model) -> ``qa_citations.check`` -> on failure, one corrective LLM retry,
then (if still failing) strip the offending sentences and mark the report unverified -> render
docx/md/html -> persist a ``reports`` row.
"""

from __future__ import annotations

import datetime as dt
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
from eoa.llm.schemas.analysis import DailyReportDraft, ReportSection
from eoa.report.docx_builder import (
    build_docx,
    fmt_date,
    hebrew_date_str,
    render_html,
    render_markdown,
    save_docx,
    validate_docx,
)
from eoa.report.qa_citations import QAResult, check, citations_in, split_sentences

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
    the reader — drop it rather than render an almost-empty row (F9/F16)."""
    return bool(
        ev.get("parties") or ev.get("customer") or ev.get("program") or ev.get("amount_usd") is not None
    )


def _event_sort_key(ev: dict[str, Any]) -> tuple[dt.date, float]:
    date = ev.get("date") or dt.date.min
    amount = ev.get("amount_usd")
    try:
        amount_val = float(amount) if amount is not None else 0.0
    except (TypeError, ValueError):
        amount_val = 0.0
    return (date, amount_val)


def collect_events(
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Business events (contract awards, M&A, ...) in the period, deduplicated, stripped of
    information-free rows, sorted date desc then amount desc (F9/F16), most recent first.
    ``limit`` (used by the weekly report to cap its table at 40 rows) is applied last, after
    dedup/filter/sort."""
    start_ts, end_ts, _label = _period(period_start, period_end)
    start_date, end_date = start_ts.astimezone(JERUSALEM).date(), end_ts.astimezone(JERUSALEM).date()
    sql = """
        SELECT e.id, e.item_id, e.kind, e.title, e.date, e.amount_usd, e.currency, e.parties,
               e.customer, e.program, e.summary_he, e.confidence,
               i.url AS item_url, i.title AS item_title, i.published_at,
               COALESCE(src.name, i.url) AS source_name
        FROM events e
        JOIN items i ON i.id = e.item_id
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE COALESCE(e.date, i.published_at::date, i.fetched_at::date, i.created_at::date)
              BETWEEN %(start)s AND %(end)s
        ORDER BY e.date DESC NULLS LAST, e.id DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"start": start_date, "end": end_date})
        rows = cur.fetchall()
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
        d for d in deep_search if d.get("trigger_item_id") is None or d.get("trigger_item_id") in included_item_ids
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
        exec_summary_he=(
            "לא זוהו בתקופה זו פריטים חדשים ברמת חשיבות red/orange (ואף לא ברמת yellow כחלופה). "
            "אין ממצאים לדיווח היום."
        ),
        sections=[],
        outlook_he="",
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
        exec_summary_he=(
            "לא זוהו בתקופה זו פריטי חדשות חדשים ברמת חשיבות red/orange/yellow, אך קיים תוכן "
            f"רלוונטי בטבלאות הדוח: {counts.context_he()} פירוט מלא בטבלאות בהמשך הדוח."
        ),
        sections=[],
        outlook_he="",
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
        "ברשימת הפריטים:\n" + errors_text
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


def _strip_uncited(draft: DailyReportDraft, qa: QAResult) -> DailyReportDraft:
    """Drop the sentences ``qa`` flagged (uncited-factual, out-of-range refs, or an exec-summary
    sentence duplicated verbatim from a section — F5), keep the rest."""
    bad_refs = set(qa.bad_refs)
    uncited = set(qa.uncited_sentences)
    duplicates = set(qa.duplicate_sentences)

    def _clean(text: str, *, extra_drop: set[str] = frozenset()) -> str:
        kept = []
        for sentence in split_sentences(text):
            if sentence in uncited or sentence in extra_drop:
                continue
            if bad_refs and set(citations_in(sentence)) & bad_refs:
                continue
            kept.append(sentence)
        return " ".join(kept)

    new_summary = _clean(draft.exec_summary_he, extra_drop=duplicates)
    if not new_summary:
        new_summary = "תקציר המנהלים קוצץ במלואו עקב בדיקת אזכורים שנכשלה; ראו qa_report לפרטים."
    new_sections: list[ReportSection] = []
    for section in draft.sections:
        cleaned = _clean(section.prose_he)
        if cleaned:
            new_sections.append(
                ReportSection(title_he=section.title_he, domain=section.domain, prose_he=cleaned)
            )
    return draft.model_copy(update={"exec_summary_he": new_summary, "sections": new_sections})


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
) -> int:
    qa_report = {
        "passed": qa.passed,
        "errors": qa.errors,
        "uncited_sentences": qa.uncited_sentences,
        "bad_refs": qa.bad_refs,
        "duplicate_sentences": qa.duplicate_sentences,
    }
    item_ids = [it["id"] for it in items if it.get("id") is not None]
    sql = """
        INSERT INTO reports (kind, period_start, period_end, path_docx, path_md, path_html,
                              items_included, qa_passed, qa_report)
        VALUES ('daily', %(start)s, %(end)s, %(docx)s, %(md)s, %(html)s, %(items)s, %(qa_passed)s, %(qa_report)s)
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


def _tenders_forecast_table(tenders_data: dict[str, list[dict[str, Any]]]) -> dict[str, Any] | None:
    """F6: a compact tender-forecasts sub-table (platform | payload | likelihood | window |
    one-line rationale), rendered here (not by ``eoa.tenders.report_section``) from
    ``collect_tenders``'s data — replaces the old ``tenders_extra_section`` bulleted prose block,
    which duplicated the open-tenders board (rendered separately by ``tenders_table``) and printed
    the full, unbounded rationale text. ``None`` when there is nothing to show."""
    forecasts = tenders_data.get("new_forecasts") or []
    if not forecasts:
        return None
    headers = ["פלטפורמה", "צורך/Payload", "סבירות", "חלון", "נימוק"]
    rows: list[list[Any]] = []
    for f in forecasts[:10]:
        likelihood = f.get("likelihood")
        pct = f"{likelihood:.0%}" if isinstance(likelihood, int | float) else "—"
        window = f"{fmt_date(f.get('window_from'))} - {fmt_date(f.get('window_to'))}"
        rationale = (f.get("rationale_he") or "").strip() or "—"
        if len(rationale) > 200:
            rationale = rationale[:199] + "…"
        rows.append([f.get("platform") or "—", f.get("payload_need") or "—", pct, window, rationale])
    return {"title_he": "תחזיות מכרזים", "headers": headers, "rows": rows}


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
        draft = _corrective_retry(items, draft, qa, role=role, interactive=interactive, table_counts=table_counts)
        qa = check(draft, items)

    if not qa.passed and items:
        log.error("report_qa_failed_stripping", errors=qa.errors[:10])
        original_errors = qa
        draft = _strip_uncited(draft, qa)
        # The stripped draft should now be clean, but re-check to be sure nothing else slipped
        # through; either way the report is marked unverified and the original QA errors are kept
        # in qa_report for the analyst to review.
        check(draft, items)
        qa = QAResult(
            passed=False,
            errors=original_errors.errors,
            uncited_sentences=original_errors.uncited_sentences,
            bad_refs=original_errors.bad_refs,
            duplicate_sentences=original_errors.duplicate_sentences,
        )

    citation_items, events_with_n = _extend_citation_registry(items, events)

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

        open_table = tenders_table(tenders_data)
        if open_table:
            tender_tables.append(open_table)
        forecast_table = _tenders_forecast_table(tenders_data)
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
        tables=tender_tables,
    )
    if llm_footer_he:
        footer_html = f'<p class="llm-footer">{llm_footer_he}</p>'
        html_text = html_text.replace("</body>", f"{footer_html}</body>") if "</body>" in html_text else html_text + footer_html
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")

    period_start_date = start_ts.astimezone(JERUSALEM).date()
    report_id = _persist_report(period_start_date, label, docx_path, md_path, html_path, items, qa)

    return ReportPaths(docx=docx_path, md=md_path, html=html_path, report_id=report_id, qa=qa)
