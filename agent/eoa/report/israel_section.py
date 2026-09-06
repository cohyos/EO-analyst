"""A13 (מיקוד תעשייה ישראלית, 2026-09-06): report-layer rendering for the "תעשייה ישראלית" section.

Deterministic (non-LLM-drafted) tables, wired as additive ``tables=[...]`` entries into
``eoa.report.daily.build_daily`` / ``eoa.report.weekly.build_weekly`` -- the same mechanism
``eoa.tenders.report_section.tenders_table`` and ``eoa.report.tech_watch`` already use, so neither
report's LLM-drafted sections, citation QA gate (``eoa.report.qa_citations``), or existing
rendering paths change.

  - ``daily_israel_tables``: items with ``israel_relevance >= DAILY_MIN_RELEVANCE`` published/
    fetched in the collection window, grouped into four category tables per
    docs/PLAN_WINDOWS_NATIVE.md row A13 point 4 -- זכיות/חוזים, תחרות ומתחרים, הזדמנויות יצוא,
    איומים ורגולציה (an item's ``event`` kinds / ``israel_reasons`` decide which bucket(s) it
    lands in; an item can appear in more than one). Each bullet row carries a ``[n]`` citation.
  - ``weekly_israel_tables``: the same category buckets over the week, plus a small "תעשייה
    ישראלית -- שבועי" summary table: Israeli company | mentions | wins | competitors active.

Both functions take the caller's ``citation_items`` list and extend it in place (mirroring
``eoa.report.tech_watch``'s own ``_extend_registry``, duplicated locally per this codebase's
pipeline/report-boundary convention) so every ``[n]`` printed here also gets a real entry -- and a
working hyperlink -- in the report's "נספח מקורות" appendix.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import structlog

from eoa.db import connection

log = structlog.get_logger(__name__)

#: Q: docs/PLAN_WINDOWS_NATIVE.md row A13 point 4 -- "items with israel_relevance >= 0.5".
DAILY_MIN_RELEVANCE = 0.5
DAILY_MAX_ITEMS_PER_CATEGORY = 12
WEEKLY_MAX_ITEMS_PER_CATEGORY = 20
WEEKLY_MAX_COMPANIES = 15

_CATEGORY_WINS = "wins"
_CATEGORY_COMPETITION = "competition"
_CATEGORY_EXPORT = "export"
_CATEGORY_THREATS = "threats"

_CATEGORY_TITLES_HE = {
    _CATEGORY_WINS: "זכיות וחוזים",
    _CATEGORY_COMPETITION: "תחרות ומתחרים",
    _CATEGORY_EXPORT: "הזדמנויות יצוא",
    _CATEGORY_THREATS: "איומים ורגולציה",
}
_CATEGORY_ORDER = [_CATEGORY_WINS, _CATEGORY_COMPETITION, _CATEGORY_EXPORT, _CATEGORY_THREATS]

_WIN_EVENT_KINDS = {"contract_award", "m_and_a", "deployment"}
_THREAT_TAGS_HE = ("איום", "רגולצי", "סנקצי", "אמברגו", "חקיק")


def _fetchall(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def collect_israel_items(
    start: dt.datetime, end: dt.datetime, *, min_relevance: float = DAILY_MIN_RELEVANCE
) -> list[dict[str, Any]]:
    """Items in ``[start, end)`` with ``israel_relevance >= min_relevance``, richest-first."""
    return _fetchall(
        """
        SELECT i.id, i.url, i.title, i.domain, i.published_at, i.score, i.level,
               i.israel_relevance, i.israel_reasons, i.entities_mentioned,
               i.summary_he, i.so_what_he,
               COALESCE(src.name, i.url) AS source_name
        FROM items i
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE i.security_status = 'clean' AND i.dedup_of IS NULL
          AND COALESCE(i.israel_relevance, 0) >= %(min_relevance)s
          AND COALESCE(i.published_at, i.fetched_at, i.created_at) >= %(start)s
          AND COALESCE(i.published_at, i.fetched_at, i.created_at) < %(end)s
        ORDER BY i.israel_relevance DESC NULLS LAST, i.score DESC NULLS LAST
        """,
        {"min_relevance": min_relevance, "start": start, "end": end},
    )


def _item_event_kinds(item_id: int) -> set[str]:
    rows = _fetchall("SELECT kind FROM events WHERE item_id = %(item_id)s", {"item_id": item_id})
    return {r["kind"] for r in rows if r.get("kind")}


def _categorize(item: dict[str, Any]) -> set[str]:
    """Which of the four A13 category buckets `item` belongs to (may be more than one)."""
    cats: set[str] = set()
    reasons = item.get("israel_reasons") or []
    event_kinds = _item_event_kinds(item["id"])
    if event_kinds & _WIN_EVENT_KINDS:
        cats.add(_CATEGORY_WINS)
    if "competitor_to_israeli_company" in reasons:
        cats.add(_CATEGORY_COMPETITION)
    if "export_market_signal" in reasons:
        cats.add(_CATEGORY_EXPORT)
    text = " ".join(filter(None, [item.get("summary_he"), item.get("so_what_he"), item.get("title")]))
    if any(kw in text for kw in _THREAT_TAGS_HE) or "regulation" in event_kinds:
        cats.add(_CATEGORY_THREATS)
    if not cats:
        # Uncategorized-but-relevant items still surface somewhere -- default to competition/
        # market-context, the broadest of the four buckets, rather than being silently dropped.
        cats.add(_CATEGORY_COMPETITION)
    return cats


def _extend_registry(
    citation_items: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {it["id"]: it for it in citation_items if it.get("id") is not None}
    next_n = (max((it.get("n") or 0) for it in citation_items) + 1) if citation_items else 1
    for row in rows:
        entry = by_id.get(row["id"])
        if entry is None:
            entry = {
                "id": row["id"],
                "n": next_n,
                "title": row.get("title"),
                "source_name": row.get("source_name"),
                "url": row.get("url"),
                "published_at": row.get("published_at"),
            }
            citation_items.append(entry)
            by_id[row["id"]] = entry
            next_n += 1
        row["n"] = entry["n"]
    return citation_items


def _category_table(
    category: str, items: list[dict[str, Any]], *, max_items: int
) -> dict[str, Any] | None:
    picked = items[:max_items]
    if not picked:
        return None
    headers = ["כותרת", "ישויות", "מה זה אומר", "מקור"]
    rows = [
        [
            it.get("title") or "—",
            ", ".join((it.get("entities_mentioned") or [])[:4]) or "—",
            it.get("so_what_he") or it.get("summary_he") or "—",
            f"[{it['n']}]",
        ]
        for it in picked
    ]
    return {"title_he": f"תעשייה ישראלית — {_CATEGORY_TITLES_HE[category]}", "headers": headers, "rows": rows}


def _build_category_tables(
    citation_items: list[dict[str, Any]], items: list[dict[str, Any]], *, max_items_per_category: int
) -> list[dict[str, Any]]:
    if not items:
        return []
    _extend_registry(citation_items, items)
    by_category: dict[str, list[dict[str, Any]]] = {c: [] for c in _CATEGORY_ORDER}
    for it in items:
        for cat in _categorize(it):
            by_category[cat].append(it)
    tables: list[dict[str, Any]] = []
    for cat in _CATEGORY_ORDER:
        table = _category_table(cat, by_category[cat], max_items=max_items_per_category)
        if table:
            tables.append(table)
    return tables


def daily_israel_tables(
    citation_items: list[dict[str, Any]],
    start: dt.datetime,
    end: dt.datetime,
    *,
    min_relevance: float = DAILY_MIN_RELEVANCE,
    max_items_per_category: int = DAILY_MAX_ITEMS_PER_CATEGORY,
) -> list[dict[str, Any]]:
    """Additive `tables=[...]` entries for the daily report's "תעשייה ישראלית" section -- up to
    four category tables (see module docstring). `[]` when nothing qualifies today, same
    convention as `eoa.tenders.report_section.tenders_table`."""
    items = collect_israel_items(start, end, min_relevance=min_relevance)
    return _build_category_tables(citation_items, items, max_items_per_category=max_items_per_category)


def _company_summary_rows(items: list[dict[str, Any]]) -> list[list[Any]]:
    """"Israeli company | mentions | wins | competitors active" summary table
    (docs/PLAN_WINDOWS_NATIVE.md row A13 point 4's weekly table)."""
    from eoa.pipeline.israel_focus import israeli_watchlist_names

    counts: dict[str, dict[str, Any]] = {
        name: {"mentions": 0, "wins": 0, "competitors": set()} for name in israeli_watchlist_names()
    }
    for it in items:
        entities = it.get("entities_mentioned") or []
        israeli_here = [e for e in entities if e in counts]
        if not israeli_here:
            continue
        event_kinds = _item_event_kinds(it["id"])
        others = [e for e in entities if e not in israeli_here]
        for name in israeli_here:
            counts[name]["mentions"] += 1
            if event_kinds & _WIN_EVENT_KINDS:
                counts[name]["wins"] += 1
            counts[name]["competitors"].update(others)
    rows = [
        [name, c["mentions"], c["wins"], len(c["competitors"])]
        for name, c in counts.items()
        if c["mentions"] > 0
    ]
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:WEEKLY_MAX_COMPANIES]


def weekly_israel_tables(
    citation_items: list[dict[str, Any]],
    start: dt.datetime,
    end: dt.datetime,
    *,
    min_relevance: float = DAILY_MIN_RELEVANCE,
    max_items_per_category: int = WEEKLY_MAX_ITEMS_PER_CATEGORY,
) -> list[dict[str, Any]]:
    """Additive `tables=[...]` entries for the weekly report: the same four category tables as
    the daily report (over the week), plus the per-company mentions/wins/competitors summary
    table. `[]` when nothing qualifies for the week."""
    items = collect_israel_items(start, end, min_relevance=min_relevance)
    tables = _build_category_tables(citation_items, items, max_items_per_category=max_items_per_category)
    company_rows = _company_summary_rows(items)
    if company_rows:
        tables.append(
            {
                "title_he": "תעשייה ישראלית — סיכום שבועי לפי חברה",
                "headers": ["חברה", "אזכורים", "זכיות", "מתחרים פעילים"],
                "rows": company_rows,
            }
        )
    return tables
