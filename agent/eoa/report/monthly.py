"""Stage: report/monthly — FR-5.4 / FR-4.3 / FR-12.5 monthly analyst report: full competitive
landscape ("נוף תחרותי") and players map.

Pipeline mirrors ``eoa.report.weekly`` (collect the month's red/orange items ->
``eoa.report.trends.detect_trends`` -> draft (structured, sentence-per-claim shape, round 5 P1 —
see below) -> ``qa_citations.check`` -> on failure, one corrective retry, then (if still failing)
replace the narrative with a deterministic, cited substitute synthesis built straight from the data
(mirrors ``eoa.report.daily``/``eoa.report.weekly``'s own two-failure fallback) -> render docx/md/
html reusing ``docx_builder``'s additive ``extra_sections``/``tables`` hooks for the trend prose and
the players/top-events/horizon/watchlist deterministic tables -> insert a ``reports`` row
(``kind='monthly'``)), reusing several of ``eoa.report.weekly``'s small, private helper functions
(grouping/label helpers, the citation-registry extension, the items/trends prompt-block formatters,
the fallback-sentence builders) rather than duplicating them, since both modules live in this same
package and both are owned by this task (round 5 P1).

Round 5 P1 (2026-09-06, docs/REPORT_TEMPLATE_BENCHMARK.md M1-M3): migrated ``MonthlyReportDraft``
from free-prose (``exec_summary_he``/``sections[].prose_he``/``trend_paragraphs``/``outlook_he``,
still readable via ``MonthlyReportDraftLegacy``) to the same structured, sentence-per-claim shape
the daily/weekly reports already use (goal 1 / round-2) — see
``eoa.llm.schemas.reports.MonthlyReportDraft``'s own docstring for why this makes every citation
correct by construction with zero changes needed in ``eoa.report.qa_citations`` or
``eoa.report.textnorm`` (both owned by other round-5 packages). Also adds month-over-month trend
tracking (M2, :func:`collect_previous_monthly_trends`/:class:`~eoa.llm.schemas.reports.
MonthlyTrendSection`) and migrates ``outlook_he`` to the same ``OutlookIndicator`` list the
daily/weekly reports use (M3).

Four things are deliberately **not** sent to the LLM and are instead rendered as deterministic
``docx_builder`` ``extra_sections``/``tables`` straight from the DB/graph (per rule 4, "Never
invent" — none of them can carry an ``[n]`` citation into the item list): the players map
(:func:`players_map`), the top-10-events-by-amount table (:func:`top_events_by_amount`), the
24-month conference horizon (:func:`full_horizon_table`), and the watchlist-changes prose
(:func:`watchlist_changes`). A fifth, new in round 5 P1, is also deterministic: a trend from the
previous monthly report with no matching evidence this month is never left for the model to notice
or narrate — ``build_monthly`` appends a ``change="gone"`` :class:`MonthlyTrendSection` for it
itself (:func:`_gone_trend_sections`).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import structlog
from psycopg.rows import dict_row
from psycopg.types.json import Json

from eoa.config import REPO_ROOT, settings
from eoa.db import connection
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.analysis import Sentence
from eoa.llm.schemas.reports import MonthlyReportDraft, MonthlyTrendSection
from eoa.memory import graph as graph_mod
from eoa.report import trends as trends_mod
from eoa.report.claims_gate import apply_claims_gate, gate_deep_search_entries, gate_item_texts
from eoa.report.daily import (
    _append_event_corroboration_markers,
    _append_item_corroboration_markers,
    collect_deep_search,
    collect_events,
    collect_open_clarifications,
)
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
from eoa.report.qa_citations import QAResult, check, strip_so_what_phrases_from_draft
from eoa.report.redundancy import apply_redundancy_pass, filter_facts_against_narrative
from eoa.report.style import apply_style_guard, dedupe_exact_sentences_across_sections
from eoa.report.textnorm import normalize_draft
from eoa.report.weekly import (
    _TREND_KIND_LABELS_HE,
    _domain_label,
    _extend_registry_with_events,
    _extend_registry_with_ids,
    _extend_registry_with_rows,
    _fallback_event_sentences,
    _fallback_israel_item_sentences,
    _fallback_top_item_sentences,
    _format_trend_numbers_he,
    _normalize_section_titles,
    canonical_domain_key,
    collect_yellow_domain_summary,
    format_items_block,
    format_yellow_summary_block,
    select_items_for_prompt,
    strip_trend_out_of_scope_sentences,
)

log = structlog.get_logger(__name__)

JERUSALEM = ZoneInfo("Asia/Jerusalem")

MONTHLY_TITLE_TEXT = "דוח חודשי — אלקטרואופטיקה ובינה חזותית ביטחונית"

_LEVELS_MAIN = ("red", "orange")
_PLAYER_EDGE_LABELS = ("COMPETITOR_OF", "SUPPLIER_OF", "PARTNER_OF")

#: R12-reports #3 (round-11 judge D3/D6 worst #9): "Operation Atlantic City" -- a NATO exercise
#: name that lives correctly on ``events.program`` (rendered in :func:`top_events_by_amount`'s own
#: table, "events" being exactly where an exercise belongs) -- also got extracted as its own
#: ``entities`` row (``kind='program'``) by the upstream entity-extraction pipeline, which is out
#: of this round's file scope (not ``eoa.report.*``). That spurious entity then leaked into two
#: unrelated entity-shaped tables built straight from ``entities``: :func:`players_map` ("נוף
#: תחרותי", a fake player with 0/0/0 competitor/supplier/partner edges — confirmed live on the
#: 2026-09-30 monthly) and :func:`watchlist_changes` ("שינויים ברשימת המעקב", rendered as a
#: glossary-style "Name — kind" bullet by :func:`format_watchlist_he` — confirmed live as
#: "Operation Atlantic City — program"). Not every ``kind='program'`` entity is bogus (e.g. "Arctic
#: Sentry"/"Defense Innovation Unit (DIU)" are legitimate named programs on the same live monthly),
#: so this can't filter on ``kind`` -- it targets the specific exercise/operation *naming* pattern
#: instead: an English "Operation ..." prefix, or a bare Hebrew "תרגיל"/"מבצע" token anywhere in the
#: name. :func:`_is_exercise_or_operation_label` is the single shared test both collectors call.
_EXERCISE_NAME_RE = re.compile(r"^operation\s", re.IGNORECASE)
_EXERCISE_HEBREW_TOKENS = ("תרגיל", "מבצע")


def _is_exercise_or_operation_label(name: str | None) -> bool:
    """True for an entity name that is actually a military exercise/operation label (belongs only
    in the events table, e.g. :func:`top_events_by_amount`'s ``events.program`` column) rather than
    a real competitive-landscape/watchlist entity. See the module-level note above
    :data:`_EXERCISE_NAME_RE` for the round-11 finding this fixes."""
    if not name:
        return False
    if _EXERCISE_NAME_RE.match(name.strip()):
        return True
    return any(tok in name for tok in _EXERCISE_HEBREW_TOKENS)


# round 5 P1 (2026-09-06): same input-side reduction rationale as weekly.py's own -- a month can
# have hundreds of red/orange items, so only a reduced, still score-ordered subset is shown to the
# model (see eoa.report.weekly.select_items_for_prompt); a month gets a somewhat larger per-domain
# allowance than a week since it spans roughly 4x the time.
_PROMPT_ITEMS_PER_DOMAIN = 10

# R6-weekly (docs/qa/loop/round_6_fixes.md, docs/REPORT_TEMPLATE_BENCHMARK.md sec 3.2): "same
# grouping as weekly" -- the monthly report's own per-item top-level headings (one per trend, one
# per domain section, one per competitive-landscape/tech table) are grouped under these parent
# headings; see ``eoa.report.docx_builder``'s ``group_he``/``domain_group_he`` support and
# ``eoa.report.weekly``'s identical constants.
_TRENDS_GROUP_HE = "מגמות החודש"
_DOMAIN_SECTIONS_GROUP_HE = "סקירה לפי תחום"
_MARKET_LANDSCAPE_GROUP_HE = "נוף השוק החודשי"
_TECH_IP_GROUP_HE = "טכנולוגיה ו-IP"
# R11-reports (round-10 judge D6 worst #5): the monthly never wired the "תעשייה ישראלית" section
# the daily/weekly both render -- see ``eoa.report.israel_section``'s module docstring and
# ``eoa.report.weekly``'s identical ``_ISRAEL_GROUP_HE`` constant/wiring, reused verbatim here.
_ISRAEL_GROUP_HE = "תעשייה ישראלית"

# re-exported so callers/tests importing eoa.report.monthly don't need to know these live in weekly.py
__all__ = [
    "MonthlyReportDraft",
    "ReportPaths",
    "build_monthly",
    "collect_month_items",
    "collect_previous_monthly_trends",
    "draft_monthly",
    "full_horizon_table",
    "players_map",
    "top_events_by_amount",
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            sql, {"levels": list(_LEVELS_MAIN), "start": period_start, "end": period_end, "limit": cap}
        )
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
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql)
        rows = cur.fetchall()
    best_domain: dict[int, dict[str, Any]] = {}
    for row in rows:
        if _is_exercise_or_operation_label(row.get("name")):
            continue
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
                log.warning(
                    "players_map_graph_query_failed", entity_id=eid, label=label, error=str(exc)[:150]
                )
                counts[label] = 0
        out.setdefault(row["domain"], []).append({"entity_id": eid, "name": row["name"], **counts})
    for domain_rows in out.values():
        domain_rows.sort(key=lambda r: sum(r[label] for label in _PLAYER_EDGE_LABELS), reverse=True)
    return out


def _top_event_kind_he(event: dict[str, Any]) -> str:
    """Hebrew label for the top-events-by-amount table's ``סוג`` cell, with a ``(לא סופי)``
    suffix when the persisted confidence is below 0.75 (CR round 14: an in-progress $10B funding
    round was rendered as a bare ``investment`` row in the monthly's most prominent table)."""
    from eoa.report.docx_builder import _EVENT_KIND_LABELS_HE

    kind = event.get("kind") or ""
    label = _EVENT_KIND_LABELS_HE.get(kind, kind) or "—"
    conf = event.get("confidence")
    try:
        if conf is not None and float(conf) < 0.75:
            label = f"{label} (לא סופי)"
    except (TypeError, ValueError):
        pass
    return label


def top_events_by_amount(period_start: dt.date, period_end: dt.date, limit: int = 10) -> list[dict[str, Any]]:
    """FR-5.4: the 10 largest business events (by ``amount_usd``) in the month."""
    sql = """
        SELECT e.id, e.kind, e.title, e.date, e.amount_usd, e.currency, e.parties, e.customer,
               e.program, e.confidence, e.item_id, i.url AS item_url, i.title AS item_title, i.published_at,
               COALESCE(src.name, i.url) AS source_name
        FROM events e
        JOIN items i ON i.id = e.item_id
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE e.amount_usd IS NOT NULL
          AND COALESCE(e.date, i.published_at::date, i.fetched_at::date, i.created_at::date)
              BETWEEN %(start)s AND %(end)s
          AND COALESCE(i.domain, '') <> 'out_of_scope' AND COALESCE(i.level, '') <> 'archive'
        ORDER BY e.amount_usd DESC NULLS LAST
        LIMIT %(limit)s
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": period_start, "end": period_end, "limit": limit})
        return cur.fetchall()


def watchlist_changes(period_start: dt.date, period_end: dt.date) -> list[dict[str, Any]]:
    """Entities first seen this month (``entities.created_at`` in the period — the schema has no
    dedicated "first seen" date column beyond ``created_at``/``first_seen_item``).

    R12-reports #3: a row whose name is an exercise/operation label
    (:func:`_is_exercise_or_operation_label`, e.g. "Operation Atlantic City") is dropped after the
    fetch — :func:`format_watchlist_he` renders every remaining row as a "Name (country) — kind"
    glossary-style bullet, and an exercise name is not a watchlist entity, same rationale as
    :func:`players_map`'s identical filter."""
    sql = """
        SELECT e.id, e.name, e.kind, e.country, e.created_at
        FROM entities e
        WHERE e.created_at::date BETWEEN %(start)s AND %(end)s
          -- 2026-09-07 (round-5 judge / live monthly): entities extracted only from out-of-scope or
          -- archived items ('Western Burrowing Owl', 'Rees Training Center') are not watchlist news
          AND EXISTS (
              SELECT 1 FROM items i
              WHERE e.name = ANY(i.entities_mentioned)
                AND COALESCE(i.domain, '') <> 'out_of_scope' AND COALESCE(i.level, '') <> 'archive'
          )
        ORDER BY e.created_at
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": period_start, "end": period_end})
        rows = cur.fetchall()
    return [r for r in rows if not _is_exercise_or_operation_label(r.get("name"))]


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
# M2 (round 5 P1): month-over-month trend strength — read the previous monthly report's own
# persisted trend snapshot back out of ``reports.qa_report`` (see :func:`_persist_report`'s
# ``"trends"`` key) and feed it to the drafting prompt as grounded DATA.
# --------------------------------------------------------------------------


def collect_previous_monthly_trends(period_start: dt.date) -> list[dict[str, Any]]:
    """The most recent monthly report strictly before ``period_start``'s own persisted trend
    snapshot (``[{"title_he", "domain", "strength"}, ...]``), or ``[]`` when there is no earlier
    monthly report yet, or its ``qa_report`` predates round 5 P1 (no ``"trends"`` key) — every
    trend is then treated as "new" by construction, and the prompt says so explicitly."""
    sql = """
        SELECT qa_report
        FROM reports
        WHERE kind = 'monthly' AND period_end < %(period_start)s
        ORDER BY period_end DESC
        LIMIT 1
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"period_start": period_start})
        row = cur.fetchone()
    if not row:
        return []
    qa_report = row.get("qa_report") or {}
    if not isinstance(qa_report, dict):
        return []
    trends = qa_report.get("trends") or []
    return [t for t in trends if isinstance(t, dict) and t.get("title_he")]


def _normalize_trend_title(title: str) -> str:
    return " ".join((title or "").split()).strip().casefold()


def _match_previous_trend(title_he: str, previous_trends: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Best-effort month-over-month identity match: exact (whitespace/case normalized) title text.

    ``eoa.report.trends.detect_trends`` titles are fully deterministic given the same underlying
    (entity, domain) or (subdomain) key, so this matches reliably for the common ``entity_cluster``/
    ``domain_surge`` kinds. ``market_convergence``/``tech_race`` titles embed a changing event/
    company count (e.g. "3 עסקאות...בתקופה"), so the same real-world trend continuing with a
    different count will not match here and is reported as "new" again — a known, documented
    limitation rather than a fuzzy-matching engine, acceptable for M2's "is this trend new/
    strengthening/weakening" purpose."""
    key = _normalize_trend_title(title_he)
    if not key:
        return None
    for prev in previous_trends:
        if _normalize_trend_title(prev.get("title_he") or "") == key:
            return prev
    return None


def format_monthly_trends_block(
    trend_list: list[dict[str, Any]], id_to_n: dict[int, int], previous_trends: list[dict[str, Any]]
) -> str:
    """Like ``eoa.report.weekly.format_trends_block``, but appends the previous monthly report's
    strength for the same trend (M2) so the model can ground ``strength_prev``/``change`` in real
    data instead of inventing them. A trend with no match in ``previous_trends`` is marked
    explicitly as having no prior-month record, so the model knows to write ``change="new"``."""
    if not trend_list:
        return "לא זוהו מגמות רוחב מובהקות בתקופה זו."
    lines: list[str] = []
    for i, t in enumerate(trend_list, start=1):
        ns = sorted({id_to_n[iid] for iid in t.get("evidence_item_ids", []) if iid in id_to_n})
        refs = "".join(f"[{n}]" for n in ns) or "—"
        entities = ", ".join(t.get("entities") or []) or "—"
        prev = _match_previous_trend(t.get("title_he", ""), previous_trends)
        if prev:
            prev_note = f"חוזק בדוח החודשי הקודם: {prev['strength']}/5"
        elif not previous_trends:
            # CR round 14: with no previous monthly report there is no comparison basis -- the
            # model must not call the trend "new" (it wrote "מגמה חדשה החודש" for all ten).
            prev_note = "אין דוח חודשי קודם להשוואה (אל תכתוב 'מגמה חדשה'; change='new' רק כי אין בסיס)"
        else:
            prev_note = "לא הופיעה בדוח החודשי הקודם — מגמה חדשה"
        lines.append(
            f"{i}. [{_TREND_KIND_LABELS_HE.get(t['kind'], t['kind'])}] {t['title_he']} | "
            f"חוזק החודש: {t['strength']}/5 | נתונים: {_format_trend_numbers_he(t)} | {prev_note} | "
            f"ישויות: {entities} | ראיות: {refs}"
        )
    return "\n".join(lines)


def _has_previous_monthly_report(period_start: dt.date) -> bool:
    """CR-monthly.md item 1(c): distinguishes "no previous monthly report exists at all" (this is
    the first one) from "a previous report exists but this particular trend wasn't in it" (a
    genuinely new trend) -- :func:`collect_previous_monthly_trends` returns ``[]`` in BOTH cases,
    which is exactly why every trend on the very first monthly ever built got mislabelled
    "מגמה חדשה החודש" ("new trend this month") instead of the honest "no prior report to compare
    to". Used only by :func:`_render_trend_body`."""
    # Lead fix 2026-09-08: an earlier monthly built for an EMPTY period (0 items -- e.g. the
    # accidental August 2026 row) is not a comparison basis; only a previous monthly that
    # actually covered items counts, otherwise every trend is again mislabelled "new".
    # `items_included` is `bigint[]` (the item-id list), not a count -- `cardinality()` is the
    # array-length function; comparing it directly to an integer (as a prior attempt at this same
    # fix did) raises `psycopg.errors.DatatypeMismatch` ("COALESCE types bigint[] and integer
    # cannot be matched"), confirmed live against the real DB on 2026-09-08.
    sql = (
        "SELECT 1 FROM reports WHERE kind = 'monthly' AND period_end < %(period_start)s "
        "AND COALESCE(cardinality(items_included), 0) > 0 LIMIT 1"
    )
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, {"period_start": period_start})
        return cur.fetchone() is not None


def _gone_trend_sections(
    current_titles: set[str], previous_trends: list[dict[str, Any]]
) -> list[MonthlyTrendSection]:
    """M2: every previous-month trend title with no match among ``current_titles`` (already
    normalized the same way :func:`_match_previous_trend` does) becomes a deterministic
    ``change="gone"`` entry — never written by the model (rule 5, "never invent": there is no new
    evidence this month to ask it to cite or narrate)."""
    out: list[MonthlyTrendSection] = []
    seen: set[str] = set()
    for prev in previous_trends:
        title = prev.get("title_he") or ""
        key = _normalize_trend_title(title)
        if not key or key in current_titles or key in seen:
            continue
        seen.add(key)
        out.append(
            MonthlyTrendSection(
                title_he=title,
                domain=prev.get("domain") or "secondary",
                sentences=[],
                strength_now=None,
                strength_prev=prev.get("strength"),
                change="gone",
            )
        )
    return out


def _render_sentence(sentence: Sentence) -> str:
    """Local copy of ``eoa.report.docx_builder``'s private sentence-rendering helper (same
    convention ``eoa.report.weekly._render_trend_sentences`` follows), needed here because trend
    prose is rendered via the ``extra_sections`` hook (a plain string) rather than through
    ``draft.sections``'s own duck-typed structured rendering."""
    text = sentence.text_he.rstrip()
    markers = "".join(f"[{n}]" for n in sentence.cites)
    return f"{text} {markers}".rstrip() if markers else text


def _render_trend_body(trend: MonthlyTrendSection, *, has_previous_report: bool) -> str:
    """Deterministic prose for one :class:`MonthlyTrendSection`'s ``extra_sections`` body (M2):
    the model's own cited sentences, preceded by a deterministic, code-authored month-over-month
    change note derived from ``strength_now``/``strength_prev``/``change`` — never itself a
    factual claim requiring a citation, since it only restates numbers already computed from
    ``eoa.report.trends``/the previous report's own persisted snapshot.

    CR-monthly.md item 1(c): ``change == "new"`` covers both "a previous monthly report exists but
    didn't have this trend" (a real new-trend claim) and "there is no previous monthly report at
    all" (nothing to compare to, ever) -- the model can't tell these apart (both look identical in
    :func:`format_monthly_trends_block`'s "לא הופיעה בדוח החודשי הקודם" line), so ``has_previous_
    report`` (:func:`_has_previous_monthly_report`, computed once per build) disambiguates here,
    deterministically, instead of trusting the model to have inferred it."""
    if trend.change == "gone":
        prev = f"{trend.strength_prev}/5" if trend.strength_prev is not None else "—"
        return f"מגמה זו הופיעה בדוח החודשי הקודם (חוזק {prev}) ולא נמצאו לה ראיות חדשות החודש."
    if trend.change == "new":
        note = "מגמה חדשה החודש." if has_previous_report else "לא נמדדה בחודש הקודם (אין דוח קודם)."
    elif trend.change == "stronger":
        note = f"התחזקה מ-{trend.strength_prev}/5 בחודש הקודם ל-{trend.strength_now}/5 החודש."
    else:  # weaker
        note = f"נחלשה מ-{trend.strength_prev}/5 בחודש הקודם ל-{trend.strength_now}/5 החודש."
    body = " ".join(_render_sentence(s) for s in trend.sentences)
    return f"{note} {body}".strip()


# --------------------------------------------------------------------------
# drafting
# --------------------------------------------------------------------------


def _no_items_draft() -> MonthlyReportDraft:
    return MonthlyReportDraft(
        exec_summary=[],
        trends=[],
        sections=[],
        system_note_he=("לא זוהו בתקופה זו פריטים חדשים ברמת חשיבות red/orange. אין ממצאים לדיווח החודשי."),
        analyst_note_he=None,
        outlook=[],
        open_points_he=[],
    )


def draft_monthly(
    items: list[dict[str, Any]],
    yellow_summary: list[dict[str, Any]],
    trends_block: str,
    *,
    evidence_item_ids: set[int] | None = None,
    role: str = "resident",
    interactive: bool = False,
) -> MonthlyReportDraft:
    """Draft the ``MonthlyReportDraft`` via the resident model; zero items skip the LLM call.

    ``items`` stays the full citation registry (unchanged ``n`` numbering); only the prompt-visible
    item list is reduced (:func:`eoa.report.weekly.select_items_for_prompt`) — same rationale as
    the weekly report's own round-2 migration (module docstring).
    """
    if not items:
        return _no_items_draft()
    prompt_items = select_items_for_prompt(items, evidence_item_ids, per_domain=_PROMPT_ITEMS_PER_DOMAIN)
    prompt = render(
        "report_monthly",
        date_he=hebrew_date_str(_today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        trends_block=wrap_data(trends_block, "report_trends", "internal"),
        items_block=wrap_data(format_items_block(prompt_items), "report_items", "internal"),
        yellow_summary_block=wrap_data(
            format_yellow_summary_block(yellow_summary), "report_yellow", "internal"
        ),
    )
    draft = chat_structured(
        role,
        MonthlyReportDraft,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="report",
        interactive=interactive,
        # round 5 P1: same rationale as weekly's own round-2 migration -- the structured schema
        # plus the prompt-item reduction above keep the output bounded, but num_predict stays
        # generous per the task (capped at 14000, mirroring weekly).
        options={"temperature": 0.3, "num_predict": 14000},
    )
    return cast(MonthlyReportDraft, _normalize_section_titles(draft))


def _corrective_retry(
    items: list[dict[str, Any]],
    yellow_summary: list[dict[str, Any]],
    trends_block: str,
    draft: MonthlyReportDraft,
    qa: QAResult,
    *,
    evidence_item_ids: set[int] | None = None,
    role: str,
    interactive: bool,
) -> MonthlyReportDraft:
    prompt_items = select_items_for_prompt(items, evidence_item_ids, per_domain=_PROMPT_ITEMS_PER_DOMAIN)
    prompt = render(
        "report_monthly",
        date_he=hebrew_date_str(_today_jerusalem()),
        data_guard=DATA_GUARD_SYSTEM,
        trends_block=wrap_data(trends_block, "report_trends", "internal"),
        items_block=wrap_data(format_items_block(prompt_items), "report_items", "internal"),
        yellow_summary_block=wrap_data(
            format_yellow_summary_block(yellow_summary), "report_yellow", "internal"
        ),
    )
    errors_text = "\n".join(f"- {e}" for e in qa.errors[:30])
    correction = (
        "הטיוטה הקודמת שלך נכשלה בבדיקת האזכורים האוטומטית. תקן את כל הבעיות הבאות והחזר טיוטה מלאה "
        "ותקינה מחדש (JSON לפי הסכמה בלבד, ללא הסברים נוספים), מבלי להמציא עובדות חדשות שלא הופיעו "
        'ברשימת הפריטים או ברשימת המגמות. שים לב: אסור לכתוב "[n]" בטקסט עצמו -- מספרי ההפניה '
        "שייכים אך ורק לשדה cites של כל משפט:\n" + errors_text
    )
    draft = chat_structured(
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
    return cast(MonthlyReportDraft, _normalize_section_titles(draft))


# --------------------------------------------------------------------------
# round 5 P1: deterministic substitute synthesis, mirroring eoa.report.daily/eoa.report.weekly's
# own two-failure fallback -- reuses weekly.py's generic (draft-type-agnostic) sentence builders
# rather than duplicating them (both modules are owned by this task).
# --------------------------------------------------------------------------

_FALLBACK_TOP_ITEMS = 8
_FALLBACK_TOP_EVENTS = 8
_FALLBACK_TOP_ISRAEL_ITEMS = 6


def _deterministic_fallback_draft(
    items: list[dict[str, Any]],
    events_with_n: list[dict[str, Any]],
    israel_items: list[dict[str, Any]],
) -> MonthlyReportDraft:
    """Mirrors ``eoa.report.weekly._deterministic_fallback_draft``: a deterministic (no LLM)
    substitute executive summary built straight from the month's already-numbered data -- the top
    red/orange items, the month's notable business events, and the Israel-relevant items -- used
    when the LLM-drafted narrative still fails citation QA after one corrective retry. Every
    sentence cites a real, already-registered item ``n``, so this cannot itself fail
    :func:`eoa.report.qa_citations.check` (the caller still runs it once anyway, defensively -- see
    ``build_monthly``).

    Round 5 P3: ``bluf`` stays at its default ``[]`` -- see
    ``eoa.report.daily._deterministic_fallback_draft``'s own docstring for why (P4's
    ``docx_builder._draft_bluf_info`` already synthesizes a labelled BLUF from this exact shape)."""
    sentences: list[Sentence] = []
    sentences.extend(_fallback_top_item_sentences(items, limit=_FALLBACK_TOP_ITEMS))
    sentences.extend(_fallback_event_sentences(events_with_n, limit=_FALLBACK_TOP_EVENTS))
    sentences.extend(_fallback_israel_item_sentences(israel_items, limit=_FALLBACK_TOP_ISRAEL_ITEMS))
    return MonthlyReportDraft(
        exec_summary=sentences,
        trends=[],
        sections=[],
        system_note_he=(
            "תקציר מובנה אוטומטית (ללא ניסוח מודל): הטיוטה הטקסטואלית של הדוח החודשי לא עברה את "
            "בדיקת האזכורים גם לאחר ניסיון תיקון, ולכן ניסוח המודל הושמט במלואו. התקציר שלעיל הופק "
            "ישירות מנתוני מסד הנתונים (ללא ניסוח חופשי של מודל), ומכיל את הפריטים המובילים, "
            "האירועים העסקיים הבולטים והפריטים הרלוונטיים לתעשייה הישראלית לחודש זה — כל משפט כאן "
            "מצוטט למקורו. הטבלאות הדטרמיניסטיות (נוף תחרותי, אירועים מובילים, לוח כנסים, רשימת "
            "מעקב) ונספח המקורות שלהלן אינם מושפעים ומוצגים במלואם."
        ),
        analyst_note_he=None,
        outlook=[],
        open_points_he=[],
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
    draft: MonthlyReportDraft,
    *,
    extra_qa_fields: dict[str, Any] | None = None,
) -> int:
    # M2 (round 5 P1): persist a small trend snapshot (title/domain/strength) alongside the usual
    # QA fields, purely so the *next* monthly report's :func:`collect_previous_monthly_trends` can
    # read it back -- excludes this month's own "gone" entries (they carry no strength_now/no new
    # evidence, so they are not a real trend to carry forward).
    trend_snapshot = [
        {"title_he": t.title_he, "domain": t.domain, "strength": t.strength_now}
        for t in draft.trends
        if t.change != "gone"
    ]
    qa_report = {
        "passed": qa.passed,
        "errors": qa.errors,
        "uncited_sentences": qa.uncited_sentences,
        "bad_refs": qa.bad_refs,
        "duplicate_sentences": qa.duplicate_sentences,
        "trends": trend_snapshot,
        # CR-monthly.md items 2/3/5 (D6 "unsupported intensifier"/"trend cites non-member item"
        # checks) -- see eoa.qa.d6_daily_report._trend_membership_check.
        **(extra_qa_fields or {}),
    }
    item_ids = [it["id"] for it in items if it.get("id") is not None]
    sql = """
        INSERT INTO reports (kind, period_start, period_end, path_docx, path_md, path_html,
                              items_included, qa_passed, qa_report)
        VALUES ('monthly', %(start)s, %(end)s, %(docx)s, %(md)s, %(html)s, %(items)s, %(qa_passed)s, %(qa_report)s)
        RETURNING id
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
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
        report_id: int = cast("dict[str, Any]", cur.fetchone())["id"]
    log.info("monthly_report_persisted", report_id=report_id, qa_passed=qa.passed)
    return report_id


def _israel_month_tables(
    citation_items: list[dict[str, Any]], start: dt.date, end: dt.date
) -> list[dict[str, Any]]:
    """R11-reports (round-10 judge D6 worst #5, docs/qa/loop/round_10_judge.md sec 2 "D6"): the
    daily/weekly both render a single merged "תעשייה ישראלית" table (one row per item, a "סוג"
    type column -- see `eoa.report.israel_section`'s module docstring and the D6
    `israel_single_table_with_type_column` check); the monthly report never called into
    `eoa.report.israel_section` for its own `tables=[...]` list at all, so it rendered no
    Israeli-industry section whatsoever (Israeli-industry content only ever showed up
    incidentally inside the LLM-drafted "סקירה לפי תחום" narrative, with no dedicated heading a
    reader -- or the D6 checker -- could find).

    Mirrors `eoa.report.weekly`'s identical block (`weekly_israel_tables` + the same `group_he`/
    title-stripping dance so the merged table's own title equals the group heading and renders
    with no redundant child heading -- see `eoa.report.docx_builder`'s `_group_entries` note),
    over the month's own `[start, end)` window instead of a week's, with a monthly-sized item cap
    (`israel_section.MONTHLY_MAX_ITEMS_PER_CATEGORY`, 30 vs weekly's 20 -- a month has roughly
    4x a week's worth of qualifying items) and a "חודשי" (monthly) company-summary title suffix
    instead of weekly's "שבועי" (both via `weekly_israel_tables`'s new `period_label_he` param;
    the merged item table's own title is unaffected either way -- it is always exactly
    `israel_section._MERGED_TABLE_TITLE_HE`, matched by the D6 check regardless of report kind).

    Returns `[]` (never raises to the caller) when nothing qualifies for the month; the caller
    wraps this in its own try/except so an israel_section failure never breaks the monthly build,
    matching every other additive `tables` block in `build_monthly`.
    """
    from eoa.report.israel_section import MONTHLY_MAX_ITEMS_PER_CATEGORY, weekly_israel_tables

    month_start_ts = dt.datetime.combine(start, dt.time.min, tzinfo=JERUSALEM).astimezone(dt.UTC)
    month_end_ts = dt.datetime.combine(end, dt.time.max, tzinfo=JERUSALEM).astimezone(dt.UTC)
    tables: list[dict[str, Any]] = []
    for tbl in weekly_israel_tables(
        citation_items,
        month_start_ts,
        month_end_ts,
        max_items_per_category=MONTHLY_MAX_ITEMS_PER_CATEGORY,
        period_label_he="חודשי",
    ):
        tbl["group_he"] = _ISRAEL_GROUP_HE
        title = tbl.get("title_he") or ""
        if title and title != _ISRAEL_GROUP_HE:
            tbl["title_he"] = title.split("— ", 1)[-1]
        tables.append(tbl)
    return tables


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
    items = gate_item_texts(items)  # claims gate on quoted item text (lead, 2026-09-08)
    yellow_summary = collect_yellow_domain_summary(start, end)
    events = collect_events(start, end)
    deep_search = collect_deep_search(start, end)
    deep_search = gate_deep_search_entries(deep_search)
    open_clarifications = collect_open_clarifications()
    trend_list = trends_mod.detect_trends((start, end))
    previous_trends = collect_previous_monthly_trends(start)
    has_previous_report = _has_previous_monthly_report(start)

    all_evidence_ids = {iid for t in trend_list for iid in t.get("evidence_item_ids", [])}
    citation_items = _extend_registry_with_ids(items, all_evidence_ids)
    citation_items, events_with_n = _extend_registry_with_events(citation_items, events)
    id_to_n = {it["id"]: it["n"] for it in citation_items if it.get("id") is not None}
    trends_block = format_monthly_trends_block(trend_list, id_to_n, previous_trends)

    draft = draft_monthly(
        items,
        yellow_summary,
        trends_block,
        evidence_item_ids=all_evidence_ids,
        role=role,
        interactive=interactive,
    )
    # round 5 P1: draft.trends' cites/duplicates are validated directly by
    # qa_citations._check_structured (MonthlyReportDraft is now the structured shape, same
    # generic `getattr(draft, "trends", None)` handling WeeklyReportDraft already uses) -- no
    # extra_sections needed here any more (unlike the pre-round-5 legacy free-prose draft).
    qa = check(draft, citation_items)

    if not qa.passed and items:
        log.warning("monthly_qa_failed_retrying", errors=qa.errors[:10])
        draft = _corrective_retry(
            items,
            yellow_summary,
            trends_block,
            draft,
            qa,
            evidence_item_ids=all_evidence_ids,
            role=role,
            interactive=interactive,
        )
        qa = check(draft, citation_items)

    if not qa.passed and items:
        # Mirrors eoa.report.daily/eoa.report.weekly: two failures (initial draft + one corrective
        # retry) replace the narrative with a deterministic, cited substitute synthesis built
        # straight from the data -- see `_deterministic_fallback_draft`. The original QA errors are
        # kept in `qa_report` (persisted below) for the analyst to review; `qa.passed` stays False
        # either way.
        log.error("monthly_qa_failed_twice_using_deterministic_fallback", errors=qa.errors[:10])
        original_errors = qa
        israel_items: list[dict[str, Any]] = []
        try:
            from eoa.report.israel_section import collect_israel_items

            month_start_ts = dt.datetime.combine(start, dt.time.min, tzinfo=JERUSALEM).astimezone(dt.UTC)
            month_end_ts = dt.datetime.combine(end, dt.time.max, tzinfo=JERUSALEM).astimezone(dt.UTC)
            israel_items = collect_israel_items(month_start_ts, month_end_ts)
            _extend_registry_with_rows(citation_items, israel_items)
        except Exception as exc:
            log.warning("monthly_report_fallback_israel_collect_failed", error=str(exc)[:160])
        draft = _deterministic_fallback_draft(items, events_with_n, israel_items)
        fallback_qa = check(draft, citation_items)
        if not fallback_qa.passed:
            log.error("monthly_report_fallback_draft_failed_citation_check", errors=fallback_qa.errors[:10])
        qa = QAResult(
            passed=False,
            errors=original_errors.errors,
            uncited_sentences=original_errors.uncited_sentences,
            bad_refs=original_errors.bad_refs,
            duplicate_sentences=original_errors.duplicate_sentences,
        )

    draft = normalize_draft(draft)

    # Round 5 P3 (docs/REPORT_TEMPLATE_BENCHMARK.md sec 4 items 4/11; docs/qa/loop/round_3_judge.md
    # D2): same draft-QA step `textnorm.normalize_draft` is already called from -- strip the W26
    # filler-phrase list and the D2 so_what template-phrase list from the final draft before it
    # renders (see `eoa.report.daily.build_daily`'s identical wiring for the full rationale).
    draft, _style_report = apply_style_guard(draft, report_kind="monthly")
    _style_report.log_all(report_kind="monthly")
    draft, _so_what_removed = strip_so_what_phrases_from_draft(draft, report_kind="monthly")
    # Round-14 (CR-editing.md, "repeated sentences across sections") -- see
    # `eoa.report.daily.build_daily`'s identical wiring for the full rationale.
    draft, _n_dupes_dropped = dedupe_exact_sentences_across_sections(draft)
    if _n_dupes_dropped:
        log.info("monthly_report_exact_duplicate_sentences_dropped", n=_n_dupes_dropped)

    # CR-monthly.md item 2 (last sentence): a trend section may only cite its OWN evidence -- strip
    # any sentence that leaked a citation from another trend or from outside any trend's evidence.
    new_trends, _n_trend_sentences_out_of_scope = strip_trend_out_of_scope_sentences(
        draft.trends, trend_list, id_to_n, report_kind="monthly"
    )
    if _n_trend_sentences_out_of_scope:
        log.info("monthly_report_trend_sentences_out_of_scope_dropped", n=_n_trend_sentences_out_of_scope)
    draft = draft.model_copy(update={"trends": new_trends})

    # CR-monthly.md item 3: deterministic claims gate -- an evaluative/intensifier sentence
    # ("ניכרת התעצמות דרמטית", "מגמה חדשה" as a bare adjective, "קפיצת מדרגה"...) with no supporting
    # quantity in the same sentence gets softened or, if nothing evidentiary survives, dropped.
    draft, _claims_gate_report = apply_claims_gate(draft, report_kind="monthly")
    if _claims_gate_report.softened or _claims_gate_report.dropped:
        log.info(
            "monthly_report_claims_gate_applied",
            softened=_claims_gate_report.softened,
            dropped=_claims_gate_report.dropped,
        )

    # M2: a previous-month trend with no matching evidence this month is never left for the model
    # to notice -- appended deterministically, after QA (these carry no cites to validate) and
    # after normalization (their own text is already normalized-clean, code-authored).
    current_titles = {_normalize_trend_title(t.title_he) for t in draft.trends}
    gone = _gone_trend_sections(current_titles, previous_trends)
    if gone:
        draft = draft.model_copy(update={"trends": [*draft.trends, *gone]})

    # docs/qa/content_review/REPORT-REDUNDANCY.md (2026-09-08 user feedback: "the report repeats
    # the information overview needlessly"): a deterministic cross-section near-duplicate pass,
    # AFTER drafting and AFTER the claims gate above -- see
    # `eoa.report.redundancy.apply_redundancy_pass`'s own docstring for the BLUF > exec summary >
    # trends > domain review > analyst note priority order. Run after the "gone" trend append
    # above so those (already ``sentences=[]``, untouched by this pass) are included when trend
    # sections are rendered below; a "gone" trend is passed straight through (never pointered --
    # ``apply_redundancy_pass`` only pointers a trend that HAD sentences and lost every one of
    # them to a redundancy drop, never one that already had none).
    draft, _redundancy_result = apply_redundancy_pass(draft, report_kind="monthly")
    _redundancy_result.log_all(report_kind="monthly")
    if _redundancy_result.dropped:
        log.info(
            "monthly_report_redundancy_pass_applied",
            n_dropped=_redundancy_result.n_dropped,
            n_pointer_sections=len(_redundancy_result.pointer_sections),
        )
    for _entry in deep_search:
        if _entry.get("key_facts"):
            _entry["key_facts"], _n_facts_dropped = filter_facts_against_narrative(
                _entry["key_facts"], _redundancy_result.kept_sentence_texts
            )
            if _n_facts_dropped:
                log.info(
                    "monthly_report_deep_search_facts_redundant_dropped",
                    job_id=_entry.get("job_id"),
                    n=_n_facts_dropped,
                )

    # deterministic, non-LLM data (rule 4: never ask the model to narrate ungrounded numbers)
    players = players_map()
    top_events = top_events_by_amount(start, end, limit=10)
    horizon = full_horizon_table()
    watchlist_new = watchlist_changes(start, end)

    trend_sections = [
        {
            "title_he": tp.title_he,
            "body_he": _render_trend_body(tp, has_previous_report=has_previous_report),
            "position": "after_summary",
            # R6-weekly: grouped under one "מגמות החודש" parent, each trend as an "###" child.
            "group_he": _TRENDS_GROUP_HE,
        }
        for tp in draft.trends
    ]
    # Round 5 P3: BLUF ("שורה תחתונה") and "הנחות והפרכות" need no wiring here -- `docx_builder`
    # (P4) renders both natively from `draft.bluf`/`draft.assumptions` (docs/MODULES.md "Round 5
    # P3").
    extra_sections: list[dict[str, Any]] = list(trend_sections)

    # R12-reports #1 (round-11 judge D6 worst #3): the monthly never rendered a "מעקב אינדיקטורים"
    # (I&W watchlist) section at all -- `grep "אינדיקטור"` was 0 matches on a live monthly, while
    # daily/weekly both have one (`eoa.report.weekly.build_weekly`'s identical block, which this
    # mirrors). ``indicators.process_indicator_watchlist``/``check_maturation`` are already
    # generic over ``kind`` (the ``indicator_watchlist`` table's own ``kind`` column just needs a
    # third value here, "monthly", to key its own maturation/dedupe state independently of the
    # daily/weekly rows -- see `eoa.report.indicators`'s module docstring) -- passing "monthly"
    # here is the only wiring this needed. The per-story/8-row cap
    # (`indicators.render_watchlist_table`, R12-reports #2) applies uniformly regardless of kind,
    # so the monthly table starts out capped like the other two. A failure here must never break
    # the monthly report, same as every other additive `extra_sections`/`tables` block below.
    try:
        from eoa.report import indicators

        indicator_section, _indicator_rows = indicators.build_indicator_watchlist_section(
            "monthly", draft.outlook, items, citation_items
        )
        if indicator_section:
            extra_sections.append(indicator_section)
    except Exception as exc:
        log.warning("monthly_report_indicator_watchlist_failed", error=str(exc)[:160])

    tables: list[dict[str, Any]] = []
    # R6-weekly (docs/qa/loop/round_6_fixes.md): the players map used to render one top-level
    # "נוף תחרותי — {domain}" heading per domain (plus top-events/horizon/watchlist each getting
    # their own), 36 H2s total on the live 2026-09-30 monthly. All four now group under one
    # "נוף השוק החודשי" parent, each as an "###" child (docx_builder's `group_he`) -- the
    # watchlist prose becomes a `tables`-list *prose* member (a dict with `body_he`, no
    # `headers`/`rows`) so it can share the parent with the real tables (see
    # `eoa.report.weekly`'s identical patents/IP note for why: an `extra_sections` group and a
    # `tables` group would otherwise render as two separate parent headings).
    for domain, rows in players.items():
        domain = canonical_domain_key(domain) or domain
        if domain == "out_of_scope":
            # 2026-09-07 (live monthly rebuild): "נוף תחרותי — out_of_scope" is never a report
            # section; the weekly's section normaliser already drops this key (F7 follow-up).
            continue
        tables.append(
            {
                "title_he": f"נוף תחרותי — {_domain_label(domain)}",
                "headers": ["ישות", "מתחרים", "ספקים", "שותפים"],
                "rows": [[r["name"], r["COMPETITOR_OF"], r["SUPPLIER_OF"], r["PARTNER_OF"]] for r in rows],
                "group_he": _MARKET_LANDSCAPE_GROUP_HE,
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
                        # CR round 14: Hebrew kind label (was the raw enum slug) and an explicit
                        # 'not final' marker for low-confidence rows (e.g. a funding round still
                        # in progress) so the top-10-by-value table never reads as settled fact.
                        _top_event_kind_he(e),
                        ", ".join(e.get("parties") or []) or "—",
                        fmt_amount(e),
                        f"[{id_to_n[e['item_id']]}]" if id_to_n.get(e.get("item_id")) else "—",
                    ]
                    for e in top_events
                ],
                "group_he": _MARKET_LANDSCAPE_GROUP_HE,
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
                "group_he": _MARKET_LANDSCAPE_GROUP_HE,
            }
        )
    # U13 (same class of issue fixed for the weekly report): suppress the watchlist section when
    # nothing changed this month, rather than a heading over a "nothing new" placeholder line.
    if watchlist_new:
        tables.append(
            {
                "title_he": "שינויים ברשימת המעקב (Watchlist) — ישויות חדשות החודש",
                "body_he": format_watchlist_he(watchlist_new),
                "group_he": _MARKET_LANDSCAPE_GROUP_HE,
            }
        )

    # R11-reports (round-10 judge D6 worst #5): the daily/weekly both render a single merged
    # "תעשייה ישראלית" table (headers + a "סוג" type column, per `eoa.report.israel_section`'s
    # module docstring and the D6 `israel_single_table_with_type_column` check) -- the monthly
    # never wired it in at all. Same mechanism as `eoa.report.weekly`'s identical block, factored
    # out into :func:`_israel_month_tables` so it's unit-testable without the full collect/draft/
    # QA orchestration this function otherwise requires. A failure here must never break the
    # monthly report.
    try:
        tables.extend(_israel_month_tables(citation_items, start, end))
    except Exception as exc:
        log.warning("monthly_report_israel_section_failed", error=str(exc)[:160])

    # A14 (פטנטים ו-IP, 2026-09-06): monthly landscape summary by subdomain + top assignees, same
    # additive mechanism as the tables above. A failure here must never break the monthly report.
    # R6-weekly: both grouped under "טכנולוגיה ו-IP" -- the prose again as a `tables`-list prose
    # member (see the market-landscape note above).
    try:
        from eoa.patents.report_section import (
            collect_patents_landscape,
            patents_landscape_extra_section,
            patents_landscape_table,
        )

        patents_landscape_data = collect_patents_landscape()
        patents_prose = patents_landscape_extra_section(patents_landscape_data)
        tables.append(
            {
                "title_he": patents_prose["title_he"],
                "body_he": patents_prose["body_he"],
                "group_he": _TECH_IP_GROUP_HE,
            }
        )
        patents_landscape_tbl = patents_landscape_table(patents_landscape_data)
        if patents_landscape_tbl:
            patents_landscape_tbl["group_he"] = _TECH_IP_GROUP_HE
            tables.append(patents_landscape_tbl)
    except Exception as exc:
        log.warning("monthly_report_patents_section_failed", error=str(exc)[:160])

    # R8-reports #1 (round-7 judge D6 #4): monthly never wired the corroboration markers in at all
    # (0/59 appendix rows) -- `collect_month_items` doesn't call the marker function on collection
    # (unlike `collect_items`/`collect_week_items`), so mark the fully extended registry once, here,
    # right before rendering. Both marker functions are idempotent (see `eoa.report.daily`'s
    # docstrings) -- events already carry their marker from `collect_events`'s own internal call,
    # this just also covers any item rows `citation_items` picked up afterwards (evidence-id/event
    # fallbacks, the QA-fallback path's Israel-industry rows).
    _append_item_corroboration_markers(citation_items)
    _append_event_corroboration_markers(events_with_n)

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
        include_toc=True,
        domain_group_he=_DOMAIN_SECTIONS_GROUP_HE,
        open_points_in_outlook=True,
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
        domain_group_he=_DOMAIN_SECTIONS_GROUP_HE,
        open_points_in_outlook=True,
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
        include_toc=True,
        domain_group_he=_DOMAIN_SECTIONS_GROUP_HE,
        open_points_in_outlook=True,
    )
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")

    report_id = _persist_report(
        start,
        end,
        docx_path,
        md_path,
        html_path,
        items,
        qa,
        draft,
        extra_qa_fields={
            "trend_sentences_out_of_scope": _n_trend_sentences_out_of_scope,
            "claims_gate_softened": _claims_gate_report.softened,
            "claims_gate_dropped": _claims_gate_report.dropped,
        },
    )

    return ReportPaths(docx=docx_path, md=md_path, html=html_path, report_id=report_id, qa=qa)
