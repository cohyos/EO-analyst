"""CR-platform-opportunity (2026-09-16, docs/qa/content_review/CR-platform-opportunity.md):
report-layer rendering for the "הזדמנויות אינטגרציה בפלטפורמות" (platform-integration business-
development opportunity) section -- mirrors ``eoa.report.tech_watch``'s shape: a deliberately
separate, deterministic (no LLM) collect+render helper wired into ``eoa.report.daily``/
``eoa.report.product_line`` through the same additive ``tables=[...]`` hook those two modules
already use, so neither report's LLM-drafted sections, citation QA gate
(``eoa.report.qa_citations``), or redundancy pass are touched.

Feeds off every item ``eoa.pipeline.classify.apply_platform_opportunity_gate`` tagged
``platform_integration_opportunity`` (see ``eoa.pipeline.opportunity_signals`` for the
deterministic pre-check that drives that gate) -- a platform (aircraft/UAV/CCA/vessel/vehicle)
entering production/testing with an open external EO/IR/sensor/pod slot, a business-development
signal for BD even when the source article itself carries no EO/IR technical depth.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import structlog

from eoa.db import connection
from eoa.pipeline.opportunity_signals import TAG
from eoa.product_lines.registry import get_product_line
from eoa.report.claims_gate import soften_text

log = structlog.get_logger(__name__)

SECTION_TITLE_HE = "הזדמנויות אינטגרציה בפלטפורמות"
DAILY_MAX_ITEMS = 10


def _fetchall(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or {})
        return cur.fetchall()


def collect_platform_opportunity_items(
    start: dt.datetime, end: dt.datetime, *, max_items: int = DAILY_MAX_ITEMS, line_id: str | None = None
) -> list[dict[str, Any]]:
    """Every item tagged ``platform_integration_opportunity`` (see module docstring) published/
    fetched in ``[start, end)``, ordered by score -- not filtered by ``level`` beyond the standard
    clean/non-duplicate gate, since the whole point of the deterministic gate this section reads is
    to surface a signal the level/domain pipeline alone would otherwise archive or under-rank.

    ``line_id`` (``eoa.report.product_line``'s own call site): additionally restricts to items
    whose ``product_lines`` column already names that line -- same
    ``product_lines @> ARRAY[...]`` convention ``eoa.report.product_line.collect_market_items``
    uses."""
    line_filter = "AND i.product_lines @> ARRAY[%(line)s]::text[]" if line_id else ""
    return _fetchall(
        f"""
        SELECT i.id, i.url, i.title, i.published_at, i.score, i.level, i.product_lines,
               i.so_what_he, i.summary_he, i.entities_mentioned,
               COALESCE(src.name, i.url) AS source_name
        FROM items i
        LEFT JOIN sources src ON src.id = i.source_id
        WHERE %(tag)s = ANY(COALESCE(i.tags, '{{}}')) AND i.security_status = 'clean' AND i.dedup_of IS NULL
          AND COALESCE(i.published_at, i.fetched_at, i.created_at) >= %(start)s
          AND COALESCE(i.published_at, i.fetched_at, i.created_at) < %(end)s
          {line_filter}
        ORDER BY i.score DESC NULLS LAST, i.published_at DESC NULLS LAST
        LIMIT %(limit)s
        """,
        {"tag": TAG, "start": start, "end": end, "limit": max_items, "line": line_id},
    )


def _extend_registry(
    citation_items: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Local copy of ``eoa.report.tech_watch``'s own ``_extend_registry`` -- see that module's
    docstring for why this is duplicated locally rather than imported (this codebase's "no
    cross-module private-name imports" convention, e.g. ``eoa.report.acquisition_watch``'s own
    ``_extend_registry_for_item``)."""
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


def _product_line_names_he(line_ids: list[str] | None) -> str:
    if not line_ids:
        return "—"
    names = []
    for line_id in line_ids:
        pl = get_product_line(line_id)
        names.append(pl.name_he if pl is not None else line_id)
    return ", ".join(names) or "—"


def platform_opportunity_table(
    citation_items: list[dict[str, Any]],
    start: dt.datetime,
    end: dt.datetime,
    *,
    max_items: int = DAILY_MAX_ITEMS,
    line_id: str | None = None,
) -> dict[str, Any] | None:
    """Additive ``tables=[...]`` entry (``eoa.report.daily``/``eoa.report.product_line`` both use
    this shape -- see e.g. ``eoa.report.tech_watch.daily_tech_watch_table``): title, matching
    Israeli product line(s), the grounded so-what (already claims-gate-softened), and ``[n]``.
    ``None`` when there is nothing to show for the window -- the caller skips an empty table, same
    convention as every other deterministic table in this codebase. Kept to 4 columns (well under
    the report layer's 6-column limit for a mobile-friendly table). ``line_id`` scopes to one
    product line, same as :func:`collect_platform_opportunity_items`."""
    items = collect_platform_opportunity_items(start, end, max_items=max_items, line_id=line_id)
    if not items:
        return None
    _extend_registry(citation_items, items)
    headers = ["פלטפורמה / פריט", "קו מוצר תואם", "הזדמנות (להערכתנו)", "מקור"]
    rows = [
        [
            it.get("title") or "—",
            _product_line_names_he(it.get("product_lines")),
            soften_text(it.get("so_what_he") or it.get("summary_he")) or "—",
            f"[{it['n']}]",
        ]
        for it in items
    ]
    return {"title_he": SECTION_TITLE_HE, "headers": headers, "rows": rows}
