"""Deterministic per-product-line statistics (PL-backend, 2026-09-07) -- backs the ``stats`` field
of ``GET /api/product-lines`` / ``GET /api/product-lines/{id}`` (frozen contract,
``web/src/types/api.ts`` ``ProductLineStats``)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from eoa.db import connection
from eoa.product_lines.registry import get_product_line


def _fetchone(query: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query, params or {})
        return cur.fetchone()


def _count(table: str, where_sql: str, params: dict[str, Any]) -> int:
    row = _fetchone(f"SELECT count(*) AS n FROM {table} WHERE {where_sql}", params)
    return int(row["n"]) if row else 0


def product_line_stats(line_id: str) -> dict[str, int]:
    """``{items_7d, items_30d, events_30d, open_tenders, forecasts, patents_90d,
    active_competitors}`` for one product line -- every count scoped to
    ``product_lines @> ARRAY[line_id]``. A DB failure never raises here (mirrors
    ``eoa.api.services._attach_corroboration``'s degrade-to-default convention) -- every field
    defaults to ``0`` on error, since a stats card showing zero counts is a normal, honest UI state,
    while a 500 on the product-lines list page is not."""
    today = dt.date.today()
    base = {"line": line_id}
    try:
        items_7d = _count(
            "items",
            "product_lines @> ARRAY[%(line)s]::text[] AND security_status = 'clean' AND dedup_of IS NULL "
            "AND COALESCE(published_at, fetched_at, created_at)::date >= %(since)s",
            {**base, "since": today - dt.timedelta(days=7)},
        )
        items_30d = _count(
            "items",
            "product_lines @> ARRAY[%(line)s]::text[] AND security_status = 'clean' AND dedup_of IS NULL "
            "AND COALESCE(published_at, fetched_at, created_at)::date >= %(since)s",
            {**base, "since": today - dt.timedelta(days=30)},
        )
        events_30d = _count(
            "events",
            "product_lines @> ARRAY[%(line)s]::text[] AND COALESCE(date, created_at::date) >= %(since)s",
            {**base, "since": today - dt.timedelta(days=30)},
        )
        open_tenders = _count(
            "tenders",
            "product_lines @> ARRAY[%(line)s]::text[] AND status IN ('open', 'unknown') "
            "AND (deadline IS NULL OR deadline >= %(today)s)",
            {**base, "today": today},
        )
        forecasts = _count("tender_forecasts", "product_lines @> ARRAY[%(line)s]::text[]", base)
        patents_90d = _count(
            "patents",
            "product_lines @> ARRAY[%(line)s]::text[] "
            "AND COALESCE(publication_date, filing_date, created_at::date) >= %(since)s",
            {**base, "since": today - dt.timedelta(days=90)},
        )
        active_competitors = _active_competitors_count(line_id, today - dt.timedelta(days=30))
    except Exception:
        return {
            "items_7d": 0,
            "items_30d": 0,
            "events_30d": 0,
            "open_tenders": 0,
            "forecasts": 0,
            "patents_90d": 0,
            "active_competitors": 0,
        }
    return {
        "items_7d": items_7d,
        "items_30d": items_30d,
        "events_30d": events_30d,
        "open_tenders": open_tenders,
        "forecasts": forecasts,
        "patents_90d": patents_90d,
        "active_competitors": active_competitors,
    }


def _active_competitors_count(line_id: str, since: dt.date) -> int:
    """Distinct configured competitor entities (``config/product_lines.yaml`` this line's
    ``competitors`` list) mentioned by one of this line's own items in the last 30 days."""
    pl = get_product_line(line_id)
    names = list(pl.competitors) if pl else []
    if not names:
        return 0
    row = _fetchone(
        """
        SELECT count(DISTINCT c) AS n FROM (
            SELECT unnest(entities_mentioned) AS c FROM items
            WHERE product_lines @> ARRAY[%(line)s]::text[] AND security_status = 'clean'
              AND dedup_of IS NULL AND COALESCE(published_at, fetched_at, created_at)::date >= %(since)s
        ) mentioned
        WHERE c = ANY(%(names)s)
        """,
        {"line": line_id, "since": since, "names": names},
    )
    return int(row["n"]) if row else 0
