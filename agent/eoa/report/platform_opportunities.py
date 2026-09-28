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
from eoa.product_lines.registry import ProductLineDef, get_product_line, product_line_defs
from eoa.product_lines.tagging import tag_product_lines
from eoa.report.claims_gate import soften_text

log = structlog.get_logger(__name__)

SECTION_TITLE_HE = "הזדמנויות אינטגרציה בפלטפורמות"
DAILY_MAX_ITEMS = 10

#: R-platform-mapping (2026-09-28, daily_2026-09-28.md item 1): shown when a platform item's own
#: ``product_lines`` column has NO line with actual textual evidence -- see
#: :func:`_product_line_names_he`'s docstring for why that column alone is no longer trusted here.
NO_PRODUCT_LINE_TEXT_HE = "פלטפורמה — ללא קו מוצר מזוהה"


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
          AND COALESCE(i.published_at, i.created_at) >= %(start)s
          AND COALESCE(i.published_at, i.created_at) < %(end)s
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


def _distinctive_opportunity_signal_hit(pl: ProductLineDef, text: str) -> bool:
    """R-platform-mapping item 1: a line's own ``opportunity_signals`` term counts as per-line
    evidence only when it is NOT duplicated verbatim (case-insensitive) in any OTHER configured
    line's own ``opportunity_signals`` list. ``config/product_lines.yaml``'s airborne-pod lines
    (``targeting_pods``/``mws_eo``/``lorop_pods``/``ball_gimbals_16in``) deliberately share an
    identical generic tail ("pylon"/"hardpoint"/"payload bay"/"CCA"/"NGAD"/"GCAP"/"KF-21"/...) --
    see ``eoa.pipeline.opportunity_signals``'s own module docstring: it exists purely to gate an
    item INTO the platform-opportunity table at all (a floor-level BD signal), even with zero real
    pod/sensor detail, not to say which SPECIFIC product line actually fits. Treating a shared term
    as decisive per-line evidence reproduces exactly the reported bug (an "NGAD engine prototypes"
    item -- about a jet ENGINE, no pod/sensor content at all -- was shown as compatible with
    targeting pods, MWS, LOROP pods and 16" gimbals alike, since all four lines list "NGAD" as a
    floor-level opportunity_signals entry). A term genuinely distinctive to one line (e.g.
    "gimbal", "missile warning", "reconnaissance pod") still counts."""
    if not pl.opportunity_signals or not text:
        return False
    shared_lower = {
        t.lower() for other in product_line_defs() if other.id != pl.id for t in other.opportunity_signals
    }
    lowered = text.lower()
    for term in pl.opportunity_signals:
        if not term or term.lower() in shared_lower:
            continue
        if term.lower() in lowered:
            return True
    return False


def _product_line_names_he(it: dict[str, Any]) -> str:
    """R-platform-mapping (2026-09-28, daily_2026-09-28.md item 1): the "קו מוצר תואם" column used
    to list every id in the item's own ``product_lines`` DB column unconditionally -- that column is
    tagged at analyze time by ``eoa.product_lines.tagging.tag_product_lines``, which also matches on
    a bare ``subdomain`` hit (e.g. "airborne_pods.*"), so ANY airborne platform item ends up tagged
    with every airborne product line regardless of whether that specific line's product is actually
    named in the item's own text (the live bug: an NGAD engine-contract item showing "compatible"
    with targeting pods/MWS/LOROP pods/16" gimbals alike). This now re-checks each already-tagged
    line against the item's own extracted text (title + so_what_he/summary_he -- never the full
    article body, same "check the story's own text, not the whole corpus" scoping
    ``eoa.report.bd_territory.collect_platform_events`` already applies for the identical reason)
    for real keyword/alias/exemplar-system evidence (:func:`tag_product_lines` called with
    ``subdomain=None`` so the over-broad subdomain-alone match can never carry a line on its own) or
    a distinctive (non-shared) ``opportunity_signals`` hit (:func:`_distinctive_opportunity_signal_hit`).
    A line with no such evidence is dropped from the cell; if none of the item's tagged lines have
    any, the cell shows :data:`NO_PRODUCT_LINE_TEXT_HE` instead of a wrong "every line fits" claim.
    An id not found in the product-line registry is always kept (raw id) -- there is no config to
    check evidence against, so it is never silently dropped."""
    line_ids = it.get("product_lines") or []
    if not line_ids:
        return NO_PRODUCT_LINE_TEXT_HE
    text_he = " ".join(filter(None, [it.get("so_what_he"), it.get("summary_he")]))
    title = it.get("title") or ""
    keyword_evidenced = set(
        tag_product_lines(
            text_he=text_he,
            text_en=title,
            entities=it.get("entities_mentioned"),
            subdomain=None,  # deliberately excluded -- see docstring above.
        )
    )
    combined_text = f"{text_he} {title}"
    names: list[str] = []
    for line_id in line_ids:
        pl = get_product_line(line_id)
        if pl is None:
            names.append(line_id)
            continue
        if line_id in keyword_evidenced or _distinctive_opportunity_signal_hit(pl, combined_text):
            names.append(pl.name_he)
    return ", ".join(names) if names else NO_PRODUCT_LINE_TEXT_HE


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
            _product_line_names_he(it),
            soften_text(it.get("so_what_he") or it.get("summary_he")) or "—",
            f"[{it['n']}]",
        ]
        for it in items
    ]
    return {"title_he": SECTION_TITLE_HE, "headers": headers, "rows": rows}
