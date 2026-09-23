"""A12 (מעקב טכנולוגי, 2026-09-06): report-layer rendering for the `tech_dev` domain.

Two deterministic (non-LLM-drafted) sections, wired as additive ``tables=[...]`` entries into
``eoa.report.daily.build_daily`` / ``eoa.report.weekly.build_weekly`` -- the same mechanism
``eoa.tenders.report_section.tenders_table`` already uses, so neither report's LLM-drafted
sections, citation QA gate (``eoa.report.qa_citations``), or existing rendering paths change:

  - ``daily_tech_watch_table``: every `tech_dev` item of the day (title, actor, TRL/maturity,
    so-what, ``[n]``), regardless of triage level -- see ``eoa.pipeline.tech_watch`` module
    docstring for why level-filtering would hide most of what this feature exists to surface.
  - ``weekly_tech_watch_tables``: the per-subdomain aggregation table (new papers, actors,
    momentum arrow, so-what) from ``eoa.pipeline.tech_watch.run_tech_watch_weekly``, plus a
    short "התפתחויות שכדאי לעקוב" pick of the 3-5 highest-scoring notable items across every
    subdomain.

Both functions take the caller's ``citation_items`` list and extend it in place (mirroring
``eoa.report.daily._extend_citation_registry``'s event-registry pattern, duplicated locally per
this codebase's pipeline/report-boundary convention -- see
``eoa.pipeline.analyze._event_dedup_key``) so every `[n]` printed here also gets a real entry --
and a working hyperlink -- in the report's "נספח מקורות" appendix, without a fresh code path
needing to know the main draft's own private numbering.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

import structlog

from eoa.db import connection
from eoa.report.claims_gate import soften_text

if TYPE_CHECKING:
    from eoa.pipeline.tech_watch import SubdomainAggregate

log = structlog.get_logger(__name__)

DOMAIN = "tech_dev"
DAILY_MAX_ITEMS = 20
NOTABLE_TO_FOLLOW = 5

_MOMENTUM_ARROW = {"up": "⬆", "down": "⬇", "flat": "➡"}
_MATURITY_HE = {"lab": "מעבדה", "prototype": "אב-טיפוס", "qualified": "מוסמך", "fielded": "מבצעי"}
_ACTOR_HE = {
    "academia": "אקדמיה",
    "lab": "מעבדה",
    "startup": "סטארטאפ",
    "prime": "יצרן ביטחוני",
    "government": "ממשלתי",
}


def _fetchall(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def collect_daily_tech_items(
    start: dt.datetime, end: dt.datetime, *, max_items: int = DAILY_MAX_ITEMS
) -> list[dict[str, Any]]:
    """Every `tech_dev` item published/fetched in `[start, end)`, ordered by score -- deliberately
    not filtered by `level` (see module docstring)."""
    return _fetchall(
        """
        SELECT i.id, i.url, i.title, i.subdomain, i.published_at, i.score, i.trl,
               i.tech_maturity, i.tech_actor_kind, i.so_what_he, i.summary_he,
               COALESCE(src.name, i.url) AS source_name
        FROM items i
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE i.domain = %(domain)s AND i.security_status = 'clean' AND i.dedup_of IS NULL
          AND COALESCE(i.published_at, i.created_at) >= %(start)s
          AND COALESCE(i.published_at, i.created_at) < %(end)s
        ORDER BY i.score DESC NULLS LAST, i.published_at DESC NULLS LAST
        LIMIT %(limit)s
        """,
        {"domain": DOMAIN, "start": start, "end": end, "limit": max_items},
    )


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


def _actor_label(kind: str | None) -> str:
    return _ACTOR_HE.get(kind or "", kind or "—")


def _maturity_label(kind: str | None) -> str:
    return _MATURITY_HE.get(kind or "", kind or "—")


def daily_tech_watch_table(
    citation_items: list[dict[str, Any]],
    start: dt.datetime,
    end: dt.datetime,
    *,
    max_items: int = DAILY_MAX_ITEMS,
) -> dict[str, Any] | None:
    """Additive `tables=[...]` entry for the daily report's "מעקב טכנולוגי" section (title,
    actor, TRL/maturity, so-what, `[n]`). `None` when there is nothing to show today -- the caller
    skips an empty table, same convention as `eoa.tenders.report_section.tenders_table`."""
    items = collect_daily_tech_items(start, end, max_items=max_items)
    if not items:
        return None
    _extend_registry(citation_items, items)
    headers = ["כותרת", "שחקן", "TRL / בגרות", "מה זה אומר", "מקור"]
    rows = [
        [
            it.get("title") or "—",
            _actor_label(it.get("tech_actor_kind")),
            f"{it.get('trl') or '—'} / {_maturity_label(it.get('tech_maturity'))}",
            soften_text(it.get("so_what_he") or it.get("summary_he")) or "—",
            f"[{it['n']}]",
        ]
        for it in items
    ]
    return {"title_he": "מעקב טכנולוגי (Technology Watch)", "headers": headers, "rows": rows}


def weekly_tech_watch_tables(
    citation_items: list[dict[str, Any]], aggregates: list[SubdomainAggregate]
) -> list[dict[str, Any]]:
    """Additive `tables=[...]` entries for the weekly report: the per-subdomain radar matrix, and
    a short "התפתחויות שכדאי לעקוב" pick across every subdomain. Returns `[]` when `aggregates`
    has no activity at all in the period."""
    tables: list[dict[str, Any]] = []

    agg_rows = []
    for agg in aggregates:
        if agg.new_count == 0 and not agg.notable_items:
            continue
        arrow = _MOMENTUM_ARROW.get(agg.momentum, "➡")
        delta = f" ({agg.momentum_delta_pct:+.0f}%)" if agg.momentum_delta_pct is not None else ""
        agg_rows.append(
            [
                agg.label_he,
                str(agg.new_count),
                ", ".join(agg.actors[:4]) or "—",
                f"{arrow}{delta}",
                soften_text(agg.so_what_he) or "—",
            ]
        )
    if agg_rows:
        tables.append(
            {
                "title_he": "רדאר טכנולוגי — סיכום שבועי לפי תת-תחום",
                "headers": ["תת-תחום", "פרסומים חדשים", "שחקנים", "מומנטום", "מה זה אומר למוצרי EO/IR"],
                "rows": agg_rows,
            }
        )

    all_notable = [it for agg in aggregates for it in agg.notable_items]
    all_notable.sort(key=lambda it: it.get("score") or 0, reverse=True)
    picks = all_notable[:NOTABLE_TO_FOLLOW]
    if picks:
        _extend_registry(citation_items, picks)
        tables.append(
            {
                "title_he": "התפתחויות שכדאי לעקוב",
                "headers": ["כותרת", "מקור"],
                "rows": [[it.get("title") or "—", f"[{it['n']}]"] for it in picks],
            }
        )
    return tables
