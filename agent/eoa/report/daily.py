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


def _period(period_start: dt.date | None, period_end: dt.date | None) -> tuple[dt.date, dt.date]:
    end = period_end or _today_jerusalem()
    start = period_start or end
    return start, end


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
    start, end = _period(period_start, period_end)
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
              AND COALESCE(i.published_at, i.fetched_at, i.created_at)::date
                  BETWEEN %(start)s AND %(end)s
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


def collect_events(
    period_start: dt.date | None = None, period_end: dt.date | None = None
) -> list[dict[str, Any]]:
    """Business events (contract awards, M&A, ...) in the period, most recent first."""
    start, end = _period(period_start, period_end)
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
        cur.execute(sql, {"start": start, "end": end})
        return cur.fetchall()


def collect_deep_search(
    period_start: dt.date | None = None, period_end: dt.date | None = None
) -> list[dict[str, Any]]:
    """Deep-search jobs (``kind='deep_search'``) finished in the period, with the investigation's
    final result (``jobs.result``, an ``InvestigationOut``-shaped payload) when present."""
    start, end = _period(period_start, period_end)
    sql = """
        SELECT j.id AS job_id, j.payload, j.result, j.state, j.finished_at,
               i.id AS trigger_item_id, i.title AS trigger_title, i.url AS trigger_url
        FROM jobs j
        LEFT JOIN items i ON i.id = NULLIF(j.payload->>'item_id', '')::bigint
        WHERE j.kind = 'deep_search'
          AND j.state IN ('done', 'partial')
          AND j.finished_at IS NOT NULL
          AND j.finished_at::date BETWEEN %(start)s AND %(end)s
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


def _domain_label(domain: str | None) -> str:
    domains = settings().taxonomy.get("domains", {})
    entry = domains.get(domain or "", {})
    label = entry.get("label")
    return label if isinstance(label, str) and label else (domain or "כללי")


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


def draft_report(
    items: list[dict[str, Any]], *, role: str = "resident", interactive: bool = False
) -> DailyReportDraft:
    """Draft the ``DailyReportDraft`` via the resident model; zero items skip the LLM call entirely."""
    if not items:
        return _no_items_draft()
    prompt = render(
        "report_daily",
        date_he=hebrew_date_str(_today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        items_block=wrap_data(_format_items_block(items), "report_items", "internal"),
    )
    return chat_structured(
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


def _corrective_retry(
    items: list[dict[str, Any]], draft: DailyReportDraft, qa: QAResult, *, role: str, interactive: bool
) -> DailyReportDraft:
    prompt = render(
        "report_daily",
        date_he=hebrew_date_str(_today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        items_block=wrap_data(_format_items_block(items), "report_items", "internal"),
    )
    errors_text = "\n".join(f"- {e}" for e in qa.errors[:30])
    correction = (
        "הטיוטה הקודמת שלך נכשלה בבדיקת האזכורים האוטומטית. תקן את כל הבעיות הבאות והחזר טיוטה מלאה "
        "ותקינה מחדש (JSON לפי הסכמה בלבד, ללא הסברים נוספים), מבלי להמציא עובדות חדשות שלא הופיעו "
        "ברשימת הפריטים:\n" + errors_text
    )
    return chat_structured(
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


def _strip_uncited(draft: DailyReportDraft, qa: QAResult) -> DailyReportDraft:
    """Drop the sentences ``qa`` flagged (uncited-factual or out-of-range refs), keep the rest."""
    bad_refs = set(qa.bad_refs)
    uncited = set(qa.uncited_sentences)

    def _clean(text: str) -> str:
        kept = []
        for sentence in split_sentences(text):
            if sentence in uncited:
                continue
            if bad_refs and set(citations_in(sentence)) & bad_refs:
                continue
            kept.append(sentence)
        return " ".join(kept)

    new_summary = _clean(draft.exec_summary_he)
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


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def build_daily(
    period_start: dt.date | None = None,
    period_end: dt.date | None = None,
    *,
    role: str = "resident",
    interactive: bool = False,
) -> ReportPaths:
    """Collect -> draft -> QA-gate -> render docx/md/html -> persist. Returns the written paths."""
    start, end = _period(period_start, period_end)

    items = collect_items(start, end)
    events = collect_events(start, end)
    deep_search = collect_deep_search(start, end)
    open_clarifications = collect_open_clarifications()

    draft = draft_report(items, role=role, interactive=interactive)
    qa = check(draft, items)

    if not qa.passed and items:
        log.warning("report_qa_failed_retrying", errors=qa.errors[:10])
        draft = _corrective_retry(items, draft, qa, role=role, interactive=interactive)
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
        )

    citation_items, events_with_n = _extend_citation_registry(items, events)

    docx_path = _report_path(end, "docx")
    md_path = _report_path(end, "md")
    html_path = _report_path(end, "html")

    doc = build_docx(
        draft,
        citation_items,
        events_with_n,
        period_end=end,
        deep_search=deep_search,
        open_clarifications=open_clarifications,
        qa=qa,
    )
    save_docx(doc, docx_path)
    validate_docx(docx_path)

    md_text = render_markdown(
        draft,
        citation_items,
        events_with_n,
        period_end=end,
        deep_search=deep_search,
        open_clarifications=open_clarifications,
        qa=qa,
    )
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(md_text, encoding="utf-8")

    html_text = render_html(
        draft,
        citation_items,
        events_with_n,
        period_end=end,
        deep_search=deep_search,
        open_clarifications=open_clarifications,
        qa=qa,
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")

    report_id = _persist_report(start, end, docx_path, md_path, html_path, items, qa)

    return ReportPaths(docx=docx_path, md=md_path, html=html_path, report_id=report_id, qa=qa)
