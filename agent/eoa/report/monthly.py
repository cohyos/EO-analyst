"""Stage: report/monthly — FR-5.4 / FR-4.3 / FR-12.5 monthly analyst report: full competitive
landscape ("נוף תחרותי") and players map.

Pipeline mirrors ``eoa.report.weekly`` (collect the month's red/orange items ->
``eoa.report.trends.detect_trends`` -> draft -> QA-gate -> render -> persist), reusing several of
its small, private helper functions (grouping/label helpers, the citation-registry extension, the
items/trends prompt-block formatters) rather than duplicating them, since both modules live in this
same package and both are owned by this task.

Four things are deliberately **not** sent to the LLM and are instead rendered as deterministic
``docx_builder`` ``extra_sections``/``tables`` straight from the DB/graph (per rule 4, "Never
invent" — none of them can carry an ``[n]`` citation into the item list): the players map
(:func:`players_map`), the top-10-events-by-amount table (:func:`top_events_by_amount`), the
24-month conference horizon (:func:`full_horizon_table`), and the watchlist-changes prose
(:func:`watchlist_changes`).
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
from eoa.llm.schemas.reports import MonthlyReportDraft, TrendParagraph
from eoa.memory import graph as graph_mod
from eoa.report import trends as trends_mod
from eoa.report.daily import collect_deep_search, collect_events, collect_open_clarifications
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
from eoa.report.qa_citations import QAResult, check, citations_in, split_sentences
from eoa.report.weekly import (
    _domain_label,
    _extend_registry_with_events,
    _extend_registry_with_ids,
    collect_yellow_domain_summary,
    format_items_block,
    format_trends_block,
    format_yellow_summary_block,
)

log = structlog.get_logger(__name__)

JERUSALEM = ZoneInfo("Asia/Jerusalem")

MONTHLY_TITLE_TEXT = "דוח חודשי — אלקטרואופטיקה ובינה חזותית ביטחונית"

_LEVELS_MAIN = ("red", "orange")
_PLAYER_EDGE_LABELS = ("COMPETITOR_OF", "SUPPLIER_OF", "PARTNER_OF")

# re-exported so callers/tests importing eoa.report.monthly don't need to know these live in weekly.py
__all__ = [
    "MonthlyReportDraft",
    "ReportPaths",
    "build_monthly",
    "collect_month_items",
    "draft_monthly",
    "players_map",
    "top_events_by_amount",
    "full_horizon_table",
    "watchlist_changes",
]


@dataclass
class ReportPaths:
    docx: Path
    md: Path
    html: Path
    report_id: int
    qa: QAResult


def _today_jerusalem() -> dt.date:
    return dt.datetime.now(JERUSALEM).date()


def _month_range(period_end: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """Default: the previous full calendar month — sensible when this runs on
    ``config.schedule.monthly_run.day`` (day 1), reporting on the month that just ended. Pass an
    explicit ``period_end`` (any date within the target month) to report on a different month."""
    if period_end is None:
        today = _today_jerusalem()
        last_day_prev_month = today.replace(day=1) - dt.timedelta(days=1)
        return last_day_prev_month.replace(day=1), last_day_prev_month
    start = period_end.replace(day=1)
    if period_end.month == 12:
        next_month_start = period_end.replace(year=period_end.year + 1, month=1, day=1)
    else:
        next_month_start = period_end.replace(month=period_end.month + 1, day=1)
    end = next_month_start - dt.timedelta(days=1)
    return start, end


# --------------------------------------------------------------------------
# collection
# --------------------------------------------------------------------------


def collect_month_items(
    period_start: dt.date, period_end: dt.date, max_items: int | None = None
) -> list[dict[str, Any]]:
    """All red/orange items in the month, ordered by score desc, each carrying a stable 1-based
    ``n``. Capped at ``config.triage.daily_report_max_items * 30`` by default."""
    cap = max_items or settings().triage.daily_report_max_items * 30
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
        cur.execute(sql, {"levels": list(_LEVELS_MAIN), "start": period_start, "end": period_end, "limit": cap})
        rows = cur.fetchall()
    for row in rows:
        row.setdefault("key_facts", [])
    for idx, row in enumerate(rows, start=1):
        row["n"] = idx
    log.info("monthly_items_collected", count=len(rows), start=str(period_start), end=str(period_end))
    return rows


def players_map() -> dict[str, list[dict[str, Any]]]:
    """FR-5.4 "מפת שחקנים": for every entity, infer its primary domain from the items that mention
    it (``entities`` has no ``domain`` column of its own — only ``items`` does, same bridge join
    used elsewhere, e.g. ``eoa.api.services``/``eoa.memory.graph.entity_timeline``), then attach
    COMPETITOR_OF/SUPPLIER_OF/PARTNER_OF edge counts from the knowledge graph. Returns
    ``{domain: [{"entity_id", "name", "COMPETITOR_OF", "SUPPLIER_OF", "PARTNER_OF"}, ...]}``, each
    domain's list sorted by total edge count descending."""
    sql = """
        SELECT e.id, e.name, i.domain, count(*) AS n
        FROM entities e
        JOIN items i ON e.name = ANY(i.entities_mentioned)
        WHERE i.domain IS NOT NULL AND i.domain <> ''
        GROUP BY e.id, e.name, i.domain
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    best_domain: dict[int, dict[str, Any]] = {}
    for row in rows:
        eid = row["id"]
        if eid not in best_domain or row["n"] > best_domain[eid]["n"]:
            best_domain[eid] = row

    out: dict[str, list[dict[str, Any]]] = {}
    for eid, row in best_domain.items():
        counts: dict[str, int] = {}
        for label in _PLAYER_EDGE_LABELS:
            try:
                counts[label] = len(graph_mod.neighbors(eid, label))
            except Exception as exc:
                log.warning("players_map_graph_query_failed", entity_id=eid, label=label, error=str(exc)[:150])
                counts[label] = 0
        out.setdefault(row["domain"], []).append({"entity_id": eid, "name": row["name"], **counts})
    for domain_rows in out.values():
        domain_rows.sort(key=lambda r: sum(r[label] for label in _PLAYER_EDGE_LABELS), reverse=True)
    return out


def top_events_by_amount(period_start: dt.date, period_end: dt.date, limit: int = 10) -> list[dict[str, Any]]:
    """FR-5.4: the 10 largest business events (by ``amount_usd``) in the month."""
    sql = """
        SELECT e.id, e.kind, e.title, e.date, e.amount_usd, e.currency, e.parties, e.customer,
               e.program, e.item_id, i.url AS item_url, i.title AS item_title, i.published_at,
               COALESCE(src.name, i.url) AS source_name
        FROM events e
        JOIN items i ON i.id = e.item_id
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE e.amount_usd IS NOT NULL
          AND COALESCE(e.date, i.published_at::date, i.fetched_at::date, i.created_at::date)
              BETWEEN %(start)s AND %(end)s
        ORDER BY e.amount_usd DESC NULLS LAST
        LIMIT %(limit)s
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"start": period_start, "end": period_end, "limit": limit})
        return cur.fetchall()


def watchlist_changes(period_start: dt.date, period_end: dt.date) -> list[dict[str, Any]]:
    """Entities first seen this month (``entities.created_at`` in the period — the schema has no
    dedicated "first seen" date column beyond ``created_at``/``first_seen_item``)."""
    sql = """
        SELECT id, name, kind, country, created_at
        FROM entities
        WHERE created_at::date BETWEEN %(start)s AND %(end)s
        ORDER BY created_at
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"start": period_start, "end": period_end})
        return cur.fetchall()


def full_horizon_table() -> list[dict[str, Any]]:
    """FR-12.5: the full 24-month conference horizon, with changes vs. the previous month. Lazily
    imports the concurrently-developed ``eoa.conferences`` package; falls back to ``[]`` (no
    horizon table rendered) if it isn't built yet or the call fails."""
    try:
        from eoa.conferences.tracker import full_horizon_table as _impl
    except ImportError:
        return []
    try:
        return _impl() or []
    except Exception as exc:
        log.warning("conferences_full_horizon_failed", error=str(exc)[:200])
        return []


def format_watchlist_he(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "לא נוספו ישויות חדשות לרשימת המעקב (watchlist) החודש."
    lines = ["ישויות חדשות שנוספו לרשימת המעקב החודש:"]
    for e in entries:
        country = f" ({e['country']})" if e.get("country") else ""
        lines.append(f"- {e.get('name') or '—'}{country} — {e.get('kind') or '—'}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# drafting
# --------------------------------------------------------------------------


def _no_items_draft() -> MonthlyReportDraft:
    return MonthlyReportDraft(
        exec_summary_he=(
            "לא זוהו בתקופה זו פריטים חדשים ברמת חשיבות red/orange. אין ממצאים לדיווח החודשי."
        ),
        trend_paragraphs=[],
        sections=[],
        outlook_he="",
        open_points_he=[],
    )


def draft_monthly(
    items: list[dict[str, Any]],
    yellow_summary: list[dict[str, Any]],
    trends_block: str,
    *,
    role: str = "resident",
    interactive: bool = False,
) -> MonthlyReportDraft:
    """Draft the ``MonthlyReportDraft`` via the resident model; zero items skip the LLM call."""
    if not items:
        return _no_items_draft()
    prompt = render(
        "report_monthly",
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
        MonthlyReportDraft,
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
    draft: MonthlyReportDraft,
    qa: QAResult,
    *,
    role: str,
    interactive: bool,
) -> MonthlyReportDraft:
    prompt = render(
        "report_monthly",
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
        MonthlyReportDraft,
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


def _strip_uncited(draft: MonthlyReportDraft, qa: QAResult) -> MonthlyReportDraft:
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
    return out_dir / f"monthly_{period_end.isoformat()}.{ext}"


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
        VALUES ('monthly', %(start)s, %(end)s, %(docx)s, %(md)s, %(html)s, %(items)s, %(qa_passed)s, %(qa_report)s)
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
    log.info("monthly_report_persisted", report_id=report_id, qa_passed=qa.passed)
    return report_id


# --------------------------------------------------------------------------
# orchestration
# --------------------------------------------------------------------------


def build_monthly(
    period_end: dt.date | None = None, *, role: str = "resident", interactive: bool = False
) -> ReportPaths:
    """Collect -> detect trends -> draft -> QA-gate -> render docx/md/html (players map, top
    events, conference horizon, watchlist changes as deterministic tables/sections) -> persist."""
    start, end = _month_range(period_end)

    items = collect_month_items(start, end)
    yellow_summary = collect_yellow_domain_summary(start, end)
    events = collect_events(start, end)
    deep_search = collect_deep_search(start, end)
    open_clarifications = collect_open_clarifications()
    trend_list = trends_mod.detect_trends((start, end))

    all_evidence_ids = {iid for t in trend_list for iid in t.get("evidence_item_ids", [])}
    citation_items = _extend_registry_with_ids(items, all_evidence_ids)
    citation_items, events_with_n = _extend_registry_with_events(citation_items, events)
    id_to_n = {it["id"]: it["n"] for it in citation_items if it.get("id") is not None}
    trends_block = format_trends_block(trend_list, id_to_n)

    draft = draft_monthly(items, yellow_summary, trends_block, role=role, interactive=interactive)
    extra_prose = [(tp.title_he, tp.prose_he) for tp in draft.trend_paragraphs]
    qa = check(draft, citation_items, extra_sections=extra_prose)

    if not qa.passed and items:
        log.warning("monthly_qa_failed_retrying", errors=qa.errors[:10])
        draft = _corrective_retry(
            items, yellow_summary, trends_block, draft, qa, role=role, interactive=interactive
        )
        extra_prose = [(tp.title_he, tp.prose_he) for tp in draft.trend_paragraphs]
        qa = check(draft, citation_items, extra_sections=extra_prose)

    if not qa.passed and items:
        log.error("monthly_qa_failed_stripping", errors=qa.errors[:10])
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

    # deterministic, non-LLM data (rule 4: never ask the model to narrate ungrounded numbers)
    players = players_map()
    top_events = top_events_by_amount(start, end, limit=10)
    horizon = full_horizon_table()
    watchlist_new = watchlist_changes(start, end)

    trend_sections = [
        {"title_he": tp.title_he, "body_he": tp.prose_he, "position": "after_summary"}
        for tp in draft.trend_paragraphs
    ]
    watchlist_section = {
        "title_he": "שינויים ברשימת המעקב (Watchlist) — ישויות חדשות החודש",
        "body_he": format_watchlist_he(watchlist_new),
        "position": "after_outlook",
    }
    extra_sections = [*trend_sections, watchlist_section]

    tables: list[dict[str, Any]] = []
    for domain, rows in players.items():
        tables.append(
            {
                "title_he": f"נוף תחרותי — {_domain_label(domain)}",
                "headers": ["ישות", "מתחרים", "ספקים", "שותפים"],
                "rows": [
                    [r["name"], r["COMPETITOR_OF"], r["SUPPLIER_OF"], r["PARTNER_OF"]] for r in rows
                ],
            }
        )
    if top_events:
        tables.append(
            {
                "title_he": "10 האירועים המובילים לפי היקף כספי",
                "headers": ["תאריך", "סוג", "צדדים", "סכום", "מקור"],
                "rows": [
                    [
                        fmt_date(e.get("date")),
                        e.get("kind") or "—",
                        ", ".join(e.get("parties") or []) or "—",
                        fmt_amount(e),
                        f"[{id_to_n[e['item_id']]}]" if id_to_n.get(e.get("item_id")) else "—",
                    ]
                    for e in top_events
                ],
            }
        )
    if horizon:
        tables.append(
            {
                "title_he": "לוח הכנסים הדו-שנתי (24 חודשים) — עם סימון שינויים מהחודש הקודם",
                "headers": ["שם", "תאריכים", "עיר", "רלוונטיות", "סטטוס", "שינוי"],
                "rows": [
                    [
                        c.get("name") or "—",
                        f"{fmt_date(c.get('start_date'))} - {fmt_date(c.get('end_date'))}",
                        c.get("city") or "—",
                        c.get("relevance") if c.get("relevance") is not None else "—",
                        c.get("status") or "—",
                        c.get("change_note") or c.get("change") or "—",
                    ]
                    for c in horizon
                ],
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
        title_text=MONTHLY_TITLE_TEXT,
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
        title_text=MONTHLY_TITLE_TEXT,
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
        title_text=MONTHLY_TITLE_TEXT,
        extra_sections=extra_sections,
        tables=tables or None,
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")

    report_id = _persist_report(start, end, docx_path, md_path, html_path, items, qa)

    return ReportPaths(docx=docx_path, md=md_path, html=html_path, report_id=report_id, qa=qa)
