"""Stage: report/weekly — FR-5.4 / FR-4.3 / FR-11.4 / FR-12.5 weekly analyst report.

Pipeline: collect the week's red/orange items (+ a yellow-level domain-count summary, context
only) -> ``eoa.report.trends.detect_trends`` (no LLM) -> ``draft_weekly`` (resident model: one
prose paragraph per detected trend, plus the usual per-domain sections) -> ``qa_citations.check``
(extended to also validate the trend paragraphs) -> on failure, one corrective retry, then strip ->
render docx/md/html reusing ``docx_builder``'s additive ``extra_sections``/``tables`` hooks for the
trend prose, the FR-11.4 meta-summary (deterministic, not LLM-authored — see
``collect_meta_summary``), and the "לוח 90 הימים הקרובים" conference table -> insert a ``reports``
row (``kind='weekly'``).
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
from eoa.llm.schemas.analysis import ReportSection
from eoa.llm.schemas.reports import TrendParagraph, WeeklyReportDraft
from eoa.report import trends as trends_mod
from eoa.report.daily import collect_deep_search, collect_events, collect_open_clarifications
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

WEEKLY_TITLE_TEXT = "דוח שבועי — אלקטרואופטיקה ובינה חזותית ביטחונית"

_LEVELS_MAIN = ("red", "orange")

_TREND_KIND_LABELS_HE = {
    "entity_cluster": "מגמה סביב ישות",
    "domain_surge": "זינוק בתחום",
    "market_convergence": "התכנסות שוק",
    "tech_race": "מירוץ טכנולוגי",
}


@dataclass
class ReportPaths:
    docx: Path
    md: Path
    html: Path
    report_id: int
    qa: QAResult


def _today_jerusalem() -> dt.date:
    return dt.datetime.now(JERUSALEM).date()


def _week_range(period_end: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The 7-day window ending on ``period_end`` (default: today, Asia/Jerusalem), inclusive."""
    end = period_end or _today_jerusalem()
    start = end - dt.timedelta(days=6)
    return start, end


# --------------------------------------------------------------------------
# taxonomy label / grouping helpers (small, local copies of daily.py's private helpers so this
# module doesn't depend on daily.py's internals — see docs/CONVENTIONS.md "config, not code")
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


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------


def collect_week_items(
    period_start: dt.date, period_end: dt.date, max_items: int | None = None
) -> list[dict[str, Any]]:
    """All red/orange items in the week, ordered by score desc, each carrying a stable 1-based
    ``n``. Capped at ``config.triage.daily_report_max_items * 7`` by default (a week's worth of
    daily caps) — unlike the daily report there is no yellow-level fallback here; yellow items get
    a domain-count summary instead (:func:`collect_yellow_domain_summary`)."""
    cap = max_items or settings().triage.daily_report_max_items * 7
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
        cur.execute(
            sql, {"levels": list(_LEVELS_MAIN), "start": period_start, "end": period_end, "limit": cap}
        )
        rows = cur.fetchall()
    for row in rows:
        row.setdefault("key_facts", [])
    for idx, row in enumerate(rows, start=1):
        row["n"] = idx
    log.info("weekly_items_collected", count=len(rows), start=str(period_start), end=str(period_end))
    return rows


def collect_yellow_domain_summary(period_start: dt.date, period_end: dt.date) -> list[dict[str, Any]]:
    """Background-level (yellow) item counts per domain for the week — a summary only, not full
    items (per FR-5.4: "דוח שבועי: ניתוח מגמות")."""
    sql = """
        SELECT domain, count(*) AS n
        FROM items
        WHERE security_status = 'clean' AND dedup_of IS NULL AND level = 'yellow'
          AND COALESCE(published_at, fetched_at, created_at)::date BETWEEN %(start)s AND %(end)s
        GROUP BY domain
        ORDER BY n DESC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"start": period_start, "end": period_end})
        return cur.fetchall()


def collect_meta_summary(period_start: dt.date, period_end: dt.date) -> dict[str, Any]:
    """FR-11.4 hook: meta lessons (``lessons.kind='meta'``) added this week, plus
    ``triage_feedback`` rows where the user's level disagreed with the agent's (a calibration
    delta) — the raw material for the "מה השתנה בעקבות המשוב" transparency loop. Deliberately
    **not** fed to the LLM: it is rendered as a deterministic prose block
    (:func:`format_meta_summary_he`) via ``docx_builder``'s ``extra_sections`` hook, since none of
    it can carry an ``[n]`` citation into the item list and rule 4 ("Never invent") rules out asking
    the model to narrate ungrounded numbers."""
    sql_lessons = """
        SELECT id, text, source_ref, created_at
        FROM lessons
        WHERE kind = 'meta' AND created_at::date BETWEEN %(start)s AND %(end)s
        ORDER BY created_at
    """
    sql_feedback = """
        SELECT tf.id, tf.item_id, tf.user_level, tf.agent_level, tf.comment, tf.created_at,
               i.title AS item_title
        FROM triage_feedback tf
        LEFT JOIN items i ON i.id = tf.item_id
        WHERE tf.created_at::date BETWEEN %(start)s AND %(end)s
        ORDER BY tf.created_at
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql_lessons, {"start": period_start, "end": period_end})
        lessons = cur.fetchall()
        cur.execute(sql_feedback, {"start": period_start, "end": period_end})
        feedback = cur.fetchall()
    deltas = [
        f
        for f in feedback
        if f.get("user_level") and f.get("agent_level") and f["user_level"] != f["agent_level"]
    ]
    return {"lessons": lessons, "feedback_total": len(feedback), "feedback_deltas": deltas}


def format_meta_summary_he(meta: dict[str, Any]) -> str:
    """Render :func:`collect_meta_summary`'s output as deterministic Hebrew prose (FR-11.4)."""
    lines: list[str] = []
    if meta.get("lessons"):
        lines.append("תובנות מטא שנוספו השבוע:")
        lines += [f"- {lesson.get('text') or ''}" for lesson in meta["lessons"]]
    deltas = meta.get("feedback_deltas") or []
    if deltas:
        lines.append("כיולים שבוצעו בעקבות משוב המשתמש:")
        for d in deltas:
            subject = d.get("item_title") or f"פריט #{d.get('item_id')}"
            lines.append(f'- "{subject}": {d.get("agent_level")} ← {d.get("user_level")} (הכרעת המשתמש)')
    total = meta.get("feedback_total") or 0
    lines.append(f'סה"כ פריטי משוב שהתקבלו השבוע: {total}.')
    if not lines:
        return "לא נרשמו תובנות מטא או כיולי משוב חדשים השבוע."
    return "\n".join(lines)


def upcoming_conferences(days: int = 90) -> list[dict[str, Any]]:
    """FR-12.5: "לוח 90 הימים הקרובים". Lazily imports the concurrently-developed
    ``eoa.conferences`` package; falls back to ``[]`` (no calendar table rendered) if it isn't
    built yet or the call fails — a missing conference calendar never blocks the weekly report."""
    try:
        from eoa.conferences.tracker import upcoming
    except ImportError:
        return []
    try:
        return upcoming(days) or []
    except Exception as exc:
        log.warning("conferences_upcoming_failed", error=str(exc)[:200])
        return []


# --------------------------------------------------------------------------
# citation registry
# --------------------------------------------------------------------------


def _extend_registry_with_events(
    items: list[dict[str, Any]], events: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Same numbering-extension convention as ``daily._extend_citation_registry``: append any event
    source item not already numbered, so the events table's מקור[n] stays in sync."""
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


def _extend_registry_with_ids(items: list[dict[str, Any]], item_ids: set[int]) -> list[dict[str, Any]]:
    """Append any of ``item_ids`` (typically trend evidence) not already numbered, fetched fresh
    from the DB, so a trend paragraph can cite evidence even when it falls outside the red/orange
    item list (e.g. a yellow item that fed an entity cluster)."""
    citation_items = list(items)
    by_id = {it["id"]: it for it in citation_items if it.get("id") is not None}
    missing = sorted(iid for iid in item_ids if iid not in by_id)
    if not missing:
        return citation_items
    sql = """
        SELECT i.id, i.url, i.title, i.published_at, COALESCE(src.name, i.url) AS source_name
        FROM items i LEFT JOIN sources src ON src.id = i.source_id
        WHERE i.id = ANY(%(ids)s)
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"ids": missing})
        fetched = {r["id"]: r for r in cur.fetchall()}
    next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
    for iid in missing:
        row = fetched.get(iid)
        if row is None:
            continue
        row = dict(row)
        row["n"] = next_n
        citation_items.append(row)
        by_id[iid] = row
        next_n += 1
    return citation_items


# --------------------------------------------------------------------------
# prompt formatting
# --------------------------------------------------------------------------


def format_items_block(items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for domain, group in _group_by_domain(items):
        lines.append(f"### {_domain_label(domain)}")
        for it in group:
            lines.append(
                f"[{it['n']}] כותרת: {it.get('title') or '—'} | מקור: "
                f"{it.get('source_name') or it.get('url') or '—'} | תאריך: {fmt_date(it.get('published_at'))} "
                f"| רמה: {_level_label(it.get('level'))}"
            )
            lines.append(f"תקציר: {it.get('summary_he') or '—'}")
            lines.append(f"מה זה אומר: {it.get('so_what_he') or '—'}")
            lines.append("")
    return "\n".join(lines)


def format_yellow_summary_block(yellow_summary: list[dict[str, Any]]) -> str:
    if not yellow_summary:
        return "אין פריטי רקע (yellow) נוספים בתקופה זו."
    return "\n".join(f"- {_domain_label(row['domain'])}: {row['n']} פריטים" for row in yellow_summary)


def format_trends_block(trend_list: list[dict[str, Any]], id_to_n: dict[int, int]) -> str:
    if not trend_list:
        return "לא זוהו מגמות רוחב מובהקות בתקופה זו."
    lines: list[str] = []
    for i, t in enumerate(trend_list, start=1):
        ns = sorted({id_to_n[iid] for iid in t.get("evidence_item_ids", []) if iid in id_to_n})
        refs = "".join(f"[{n}]" for n in ns) or "—"
        entities = ", ".join(t.get("entities") or []) or "—"
        lines.append(
            f"{i}. [{_TREND_KIND_LABELS_HE.get(t['kind'], t['kind'])}] {t['title_he']} | "
            f"חוזק: {t['strength']}/5 | ישויות: {entities} | ראיות: {refs}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# drafting
# --------------------------------------------------------------------------


def _no_items_draft() -> WeeklyReportDraft:
    return WeeklyReportDraft(
        exec_summary_he=("לא זוהו בתקופה זו פריטים חדשים ברמת חשיבות red/orange. אין ממצאים לדיווח השבועי."),
        trend_paragraphs=[],
        sections=[],
        outlook_he="",
        open_points_he=[],
    )


def draft_weekly(
    items: list[dict[str, Any]],
    yellow_summary: list[dict[str, Any]],
    trends_block: str,
    *,
    role: str = "resident",
    interactive: bool = False,
) -> WeeklyReportDraft:
    """Draft the ``WeeklyReportDraft`` via the resident model; zero items skip the LLM call."""
    if not items:
        return _no_items_draft()
    prompt = render(
        "report_weekly",
        date_he=hebrew_date_str(_today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        trends_block=wrap_data(trends_block, "report_trends", "internal"),
        items_block=wrap_data(format_items_block(items), "report_items", "internal"),
        yellow_summary_block=wrap_data(
            format_yellow_summary_block(yellow_summary), "report_yellow", "internal"
        ),
    )
    return chat_structured(
        role,
        WeeklyReportDraft,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="report",
        interactive=interactive,
        options={"temperature": 0.3},
    )


def _corrective_retry(
    items: list[dict[str, Any]],
    yellow_summary: list[dict[str, Any]],
    trends_block: str,
    draft: WeeklyReportDraft,
    qa: QAResult,
    *,
    role: str,
    interactive: bool,
) -> WeeklyReportDraft:
    prompt = render(
        "report_weekly",
        date_he=hebrew_date_str(_today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        trends_block=wrap_data(trends_block, "report_trends", "internal"),
        items_block=wrap_data(format_items_block(items), "report_items", "internal"),
        yellow_summary_block=wrap_data(
            format_yellow_summary_block(yellow_summary), "report_yellow", "internal"
        ),
    )
    errors_text = "\n".join(f"- {e}" for e in qa.errors[:30])
    correction = (
        "הטיוטה הקודמת שלך נכשלה בבדיקת האזכורים האוטומטית. תקן את כל הבעיות הבאות והחזר טיוטה מלאה "
        "ותקינה מחדש (JSON לפי הסכמה בלבד, ללא הסברים נוספים), מבלי להמציא עובדות חדשות שלא הופיעו "
        "ברשימת הפריטים או ברשימת המגמות:\n" + errors_text
    )
    return chat_structured(
        role,
        WeeklyReportDraft,
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


def _strip_uncited(draft: WeeklyReportDraft, qa: QAResult) -> WeeklyReportDraft:
    """Drop the sentences ``qa`` flagged (uncited-factual or out-of-range refs) from the exec
    summary, every trend paragraph, and every section — mirrors ``daily._strip_uncited``."""
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
    new_trends: list[TrendParagraph] = []
    for tp in draft.trend_paragraphs:
        cleaned = _clean(tp.prose_he)
        if cleaned:
            new_trends.append(TrendParagraph(title_he=tp.title_he, prose_he=cleaned))
    new_sections: list[ReportSection] = []
    for section in draft.sections:
        cleaned = _clean(section.prose_he)
        if cleaned:
            new_sections.append(
                ReportSection(title_he=section.title_he, domain=section.domain, prose_he=cleaned)
            )
    return draft.model_copy(
        update={"exec_summary_he": new_summary, "trend_paragraphs": new_trends, "sections": new_sections}
    )


# --------------------------------------------------------------------------
# persistence / paths
# --------------------------------------------------------------------------


def _report_path(period_end: dt.date, ext: str) -> Path:
    out_dir = Path(settings().report.output_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    return out_dir / f"weekly_{period_end.isoformat()}.{ext}"


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
        VALUES ('weekly', %(start)s, %(end)s, %(docx)s, %(md)s, %(html)s, %(items)s, %(qa_passed)s, %(qa_report)s)
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
    log.info("weekly_report_persisted", report_id=report_id, qa_passed=qa.passed)
    return report_id


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def build_weekly(
    period_end: dt.date | None = None, *, role: str = "resident", interactive: bool = False
) -> ReportPaths:
    """Collect -> detect trends -> draft -> QA-gate -> render docx/md/html -> persist."""
    start, end = _week_range(period_end)

    items = collect_week_items(start, end)
    yellow_summary = collect_yellow_domain_summary(start, end)
    events = collect_events(start, end)
    deep_search = collect_deep_search(start, end)
    open_clarifications = collect_open_clarifications()
    trend_list = trends_mod.detect_trends((start, end))
    meta = collect_meta_summary(start, end)
    conferences_90 = upcoming_conferences(90)

    all_evidence_ids = {iid for t in trend_list for iid in t.get("evidence_item_ids", [])}
    citation_items = _extend_registry_with_ids(items, all_evidence_ids)
    citation_items, events_with_n = _extend_registry_with_events(citation_items, events)
    id_to_n = {it["id"]: it["n"] for it in citation_items if it.get("id") is not None}
    trends_block = format_trends_block(trend_list, id_to_n)

    draft = draft_weekly(items, yellow_summary, trends_block, role=role, interactive=interactive)
    extra_prose = [(tp.title_he, tp.prose_he) for tp in draft.trend_paragraphs]
    qa = check(draft, citation_items, extra_sections=extra_prose)

    if not qa.passed and items:
        log.warning("weekly_qa_failed_retrying", errors=qa.errors[:10])
        draft = _corrective_retry(
            items, yellow_summary, trends_block, draft, qa, role=role, interactive=interactive
        )
        extra_prose = [(tp.title_he, tp.prose_he) for tp in draft.trend_paragraphs]
        qa = check(draft, citation_items, extra_sections=extra_prose)

    if not qa.passed and items:
        log.error("weekly_qa_failed_stripping", errors=qa.errors[:10])
        original_errors = qa
        draft = _strip_uncited(draft, qa)
        extra_prose = [(tp.title_he, tp.prose_he) for tp in draft.trend_paragraphs]
        check(draft, citation_items, extra_sections=extra_prose)
        qa = QAResult(
            passed=False,
            errors=original_errors.errors,
            uncited_sentences=original_errors.uncited_sentences,
            bad_refs=original_errors.bad_refs,
        )

    trend_sections = [
        {"title_he": tp.title_he, "body_he": tp.prose_he, "position": "after_summary"}
        for tp in draft.trend_paragraphs
    ]
    meta_section = {
        "title_he": "סיכום מטא שבועי — משוב משתמש (FR-11.4)",
        "body_he": format_meta_summary_he(meta),
        "position": "after_outlook",
    }
    extra_sections = [*trend_sections, meta_section]

    tables: list[dict[str, Any]] = []
    if conferences_90:
        rows = [
            [
                c.get("name") or "—",
                f"{fmt_date(c.get('start_date'))} - {fmt_date(c.get('end_date'))}",
                c.get("city") or "—",
                c.get("relevance") if c.get("relevance") is not None else "—",
            ]
            for c in conferences_90
        ]
        tables.append(
            {
                "title_he": "לוח 90 הימים הקרובים",
                "headers": ["שם", "תאריכים", "עיר", "רלוונטיות"],
                "rows": rows,
            }
        )

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
        title_text=WEEKLY_TITLE_TEXT,
        extra_sections=extra_sections,
        tables=tables or None,
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
        title_text=WEEKLY_TITLE_TEXT,
        extra_sections=extra_sections,
        tables=tables or None,
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
        title_text=WEEKLY_TITLE_TEXT,
        extra_sections=extra_sections,
        tables=tables or None,
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")

    report_id = _persist_report(start, end, docx_path, md_path, html_path, items, qa)

    return ReportPaths(docx=docx_path, md=md_path, html=html_path, report_id=report_id, qa=qa)
