"""Stage: report/trends — cross-item pattern detection (FR-4.3) and period aggregate stats feeding
the weekly/monthly reports. Pure SQL/Python: **no LLM call happens anywhere in this module**.

``weekly_stats(period)`` aggregates counts (items per domain/level, top entities by mentions with a
delta vs. the immediately preceding equal-length period, events by kind, deep-search outcomes) for
an arbitrary ``period = (start, end)``. Despite the name (kept verbatim from the task spec, which
names this exact signature), it is period-length agnostic and ``eoa.report.monthly`` calls it with
a full-month range.

``detect_trends(period)`` looks for four FR-4.3 pattern kinds over the same period:

(a) **entity clusters** — >=3 items about the same entity+domain in the period ("מגמה")
(b) **domain surge** — a domain's item count in the period >=2x its 4-week baseline average
(c) **market convergence** — >=2 M&A/partnership events touching the same subdomain
(d) **tech race** — >=2 different companies with launch/test events in the same subdomain

Each row-fetching helper (``_*_rows`` / ``_*_counts``) below is a single, thin DB query; each
``_*_from_*`` function is pure Python over already-fetched rows, so the four detectors are
unit-testable against synthetic rows without a database — see
``tests/unit/test_report_weekly_monthly.py``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import structlog
from psycopg.rows import dict_row

from eoa.config import settings
from eoa.db import connection

log = structlog.get_logger(__name__)

Period = tuple[dt.date, dt.date]

_BASELINE_WEEKS = 4
_ENTITY_CLUSTER_MIN_ITEMS = 3
_DOMAIN_SURGE_MIN_ITEMS = 3
_CONVERGENCE_MIN_EVENTS = 2
_TECH_RACE_MIN_COMPANIES = 2


#: Round-14 (CR-editing.md): matches ``eoa.report.daily._UNKNOWN_DOMAIN_LABEL_HE`` verbatim -- the
#: fallback Hebrew label for a ``domain``/pseudo-domain value with no real taxonomy entry, never
#: the raw slug itself.
_UNKNOWN_DOMAIN_LABEL_HE = "תחומים נוספים"


def _domain_label(domain: str | None) -> str:
    """Round-14 (CR-editing.md, "headings that are English keys or taxonomy slugs"): never falls
    back to the raw ``domain`` string itself -- this function feeds every trend ``title_he`` this
    module generates (entity clusters, domain surges), so a stray ``out_of_scope``/``archive``
    value used to leak straight into a Hebrew trend heading. Mirrors ``eoa.report.daily``'s own
    ``_domain_label`` fix for the same bug (Q3-15)."""
    if not domain:
        return "כללי"
    domains = settings().taxonomy.get("domains", {})
    entry = domains.get(domain, {})
    label = entry.get("label")
    return label if isinstance(label, str) and label else _UNKNOWN_DOMAIN_LABEL_HE


def _clamp(n: float, lo: int = 1, hi: int = 5) -> int:
    return max(lo, min(hi, round(n)))


# --------------------------------------------------------------------------
# (a) entity clusters: >=3 items in the period about the same entity+domain
# --------------------------------------------------------------------------


def _entity_cluster_rows(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    sql = """
        SELECT unnest(entities_mentioned) AS entity, domain,
               array_agg(DISTINCT id) AS item_ids, count(*) AS n
        FROM items
        WHERE security_status = 'clean'
          AND dedup_of IS NULL
          AND entities_mentioned IS NOT NULL
          AND COALESCE(domain, '') <> 'out_of_scope' AND COALESCE(level, '') <> 'archive'
          AND cardinality(entities_mentioned) > 0
          AND COALESCE(published_at, fetched_at, created_at)::date BETWEEN %(start)s AND %(end)s
        GROUP BY entity, domain
        HAVING count(*) >= %(min)s
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": start, "end": end, "min": _ENTITY_CLUSTER_MIN_ITEMS})
        return cur.fetchall()


def _entity_clusters_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        item_ids = sorted(set(row.get("item_ids") or []))
        n = row.get("n") or len(item_ids)
        if n < _ENTITY_CLUSTER_MIN_ITEMS or not item_ids:
            continue
        entity = row.get("entity") or ""
        domain = row.get("domain")
        out.append(
            {
                "kind": "entity_cluster",
                "title_he": f"מגמה: פעילות מוגברת סביב {entity} בתחום {_domain_label(domain)}",
                "evidence_item_ids": item_ids,
                "entities": [entity] if entity else [],
                "strength": _clamp(n),
            }
        )
    return out


# --------------------------------------------------------------------------
# (b) domain surge: domain item count >= 2x its 4-week baseline average
# --------------------------------------------------------------------------


def _domain_counts(start: dt.date, end: dt.date) -> dict[str, int]:
    sql = """
        SELECT domain, count(*) AS n
        FROM items
        WHERE security_status = 'clean' AND dedup_of IS NULL AND domain IS NOT NULL
          AND domain <> 'out_of_scope' AND COALESCE(level, '') <> 'archive'
          AND COALESCE(published_at, fetched_at, created_at)::date BETWEEN %(start)s AND %(end)s
        GROUP BY domain
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": start, "end": end})
        return {r["domain"]: r["n"] for r in cur.fetchall()}


def _domain_baseline_counts(start: dt.date, end: dt.date) -> dict[str, float]:
    """Average weekly item count per domain over the ``_BASELINE_WEEKS`` weeks immediately
    preceding ``start`` (excluding the period being evaluated itself)."""
    baseline_start = start - dt.timedelta(weeks=_BASELINE_WEEKS)
    baseline_end = start - dt.timedelta(days=1)
    sql = """
        SELECT domain, count(*) AS n
        FROM items
        WHERE security_status = 'clean' AND dedup_of IS NULL AND domain IS NOT NULL
          AND domain <> 'out_of_scope' AND COALESCE(level, '') <> 'archive'
          AND COALESCE(published_at, fetched_at, created_at)::date BETWEEN %(start)s AND %(end)s
        GROUP BY domain
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": baseline_start, "end": baseline_end})
        return {r["domain"]: (r["n"] or 0) / _BASELINE_WEEKS for r in cur.fetchall()}


def _domain_item_ids(start: dt.date, end: dt.date) -> dict[str, list[int]]:
    sql = """
        SELECT domain, array_agg(id) AS item_ids
        FROM items
        WHERE security_status = 'clean' AND dedup_of IS NULL AND domain IS NOT NULL
          AND domain <> 'out_of_scope' AND COALESCE(level, '') <> 'archive'
          AND COALESCE(published_at, fetched_at, created_at)::date BETWEEN %(start)s AND %(end)s
        GROUP BY domain
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": start, "end": end})
        return {r["domain"]: list(r["item_ids"] or []) for r in cur.fetchall()}


def _surge_strength(ratio: float) -> int:
    if ratio >= 4:
        return 5
    if ratio >= 3:
        return 4
    return 3


def _domain_surges_from_counts(
    counts: dict[str, int],
    baseline: dict[str, float],
    items_by_domain: dict[str, list[int]],
) -> list[dict[str, Any]]:
    """``counts``/``baseline``/``items_by_domain`` are keyed by domain — see ``_domain_counts``,
    ``_domain_baseline_counts``, ``_domain_item_ids``. When a domain has no baseline history at all
    (``avg == 0``, e.g. a brand-new domain), the 2x-ratio test is meaningless, so the surge
    threshold falls back to the plain ``_DOMAIN_SURGE_MIN_ITEMS`` floor instead of firing on any
    nonzero count."""
    out: list[dict[str, Any]] = []
    for domain, week_count in counts.items():
        if week_count < _DOMAIN_SURGE_MIN_ITEMS:
            continue
        avg = baseline.get(domain, 0.0)
        is_surge = (week_count >= avg * 2) if avg > 0 else week_count >= _DOMAIN_SURGE_MIN_ITEMS
        if not is_surge:
            continue
        ratio = (week_count / avg) if avg > 0 else float(week_count)
        out.append(
            {
                "kind": "domain_surge",
                "title_he": f"זינוק בכמות הפריטים בתחום {_domain_label(domain)}",
                "evidence_item_ids": sorted(items_by_domain.get(domain) or [])[:20],
                "entities": [],
                "strength": _surge_strength(ratio),
            }
        )
    return out


# --------------------------------------------------------------------------
# (c) market convergence: >=2 M&A/partnership events touching the same subdomain
# --------------------------------------------------------------------------


def _convergence_rows(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    sql = """
        SELECT i.subdomain AS subdomain, count(DISTINCT e.id) AS n,
               array_agg(DISTINCT e.item_id) AS item_ids,
               array_agg(DISTINCT p) FILTER (WHERE p IS NOT NULL) AS parties
        FROM events e
        JOIN items i ON i.id = e.item_id
        LEFT JOIN LATERAL unnest(e.parties) AS p ON true
        WHERE e.kind IN ('m_and_a', 'partnership')
          AND i.subdomain IS NOT NULL AND i.subdomain <> ''
          AND COALESCE(i.domain, '') <> 'out_of_scope' AND COALESCE(i.level, '') <> 'archive'
          AND COALESCE(e.date, i.published_at::date, i.fetched_at::date, i.created_at::date)
              BETWEEN %(start)s AND %(end)s
        GROUP BY i.subdomain
        HAVING count(DISTINCT e.id) >= %(min)s
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": start, "end": end, "min": _CONVERGENCE_MIN_EVENTS})
        return cur.fetchall()


def _subdomain_label(key: str | None) -> str:
    """Hebrew taxonomy label for a subdomain key -- the raw slug ("atr", "isr_pods") leaked into
    monthly/weekly trend headings (live monthly_2026-09-30, 2026-09-07).

    Round-14 (CR-editing.md): the docstring above already named this exact bug but the code still
    fell back to the raw ``key`` itself when the taxonomy had no entry for it -- the same "English
    key/taxonomy slug leaked into a Hebrew heading" defect :func:`_domain_label` had, now fixed the
    same way (never the raw slug)."""
    if not key:
        return "כללי"
    for entry in (settings().taxonomy.get("domains") or {}).values():
        subs = (entry or {}).get("sub") or {}
        if key in subs:
            label = subs[key]
            if isinstance(label, dict):
                label = label.get("label_he") or label.get("label") or key
            return str(label)
    return _UNKNOWN_DOMAIN_LABEL_HE


def _convergence_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        n = row.get("n") or 0
        if n < _CONVERGENCE_MIN_EVENTS:
            continue
        subdomain = row.get("subdomain") or ""
        out.append(
            {
                "kind": "market_convergence",
                "title_he": (
                    f'התכנסות שוק בתת-התחום "{_subdomain_label(subdomain)}" — {n} עסקאות מיזוג/רכישה ושותפות בתקופה'
                ),
                "evidence_item_ids": sorted(set(row.get("item_ids") or [])),
                "entities": sorted(set(row.get("parties") or [])),
                "strength": _clamp(n + 1, lo=3, hi=5),
            }
        )
    return out


# --------------------------------------------------------------------------
# (d) tech race: >=2 different companies with launch/test events in the same subdomain
# --------------------------------------------------------------------------


def _tech_race_rows(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    sql = """
        SELECT i.subdomain AS subdomain,
               array_agg(DISTINCT e.item_id) AS item_ids,
               array_agg(DISTINCT COALESCE(p, e.customer))
                   FILTER (WHERE COALESCE(p, e.customer) IS NOT NULL) AS companies
        FROM events e
        JOIN items i ON i.id = e.item_id
        LEFT JOIN LATERAL unnest(e.parties) AS p ON true
        WHERE e.kind IN ('launch', 'test')
          AND COALESCE(i.domain, '') <> 'out_of_scope' AND COALESCE(i.level, '') <> 'archive'
          AND i.subdomain IS NOT NULL AND i.subdomain <> ''
          AND COALESCE(e.date, i.published_at::date, i.fetched_at::date, i.created_at::date)
              BETWEEN %(start)s AND %(end)s
        GROUP BY i.subdomain
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": start, "end": end})
        return cur.fetchall()


def _tech_race_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        companies = sorted(set(row.get("companies") or []))
        if len(companies) < _TECH_RACE_MIN_COMPANIES:
            continue
        subdomain = row.get("subdomain") or ""
        out.append(
            {
                "kind": "tech_race",
                "title_he": (
                    f'מירוץ טכנולוגי בתת-התחום "{_subdomain_label(subdomain)}" — {len(companies)} חברות עם השקות/ניסויים בתקופה'
                ),
                "evidence_item_ids": sorted(set(row.get("item_ids") or [])),
                "entities": companies,
                "strength": _clamp(len(companies) + 1, lo=3, hi=5),
            }
        )
    return out


# --------------------------------------------------------------------------
# public: detect_trends
# --------------------------------------------------------------------------


def detect_trends(period: Period) -> list[dict[str, Any]]:
    """Cross-item pattern detection per FR-4.3 over ``period = (start, end)``. No LLM call.

    Each returned dict: ``{kind, title_he, evidence_item_ids, entities, strength}`` — ``kind`` in
    ``{"entity_cluster", "domain_surge", "market_convergence", "tech_race"}``, ``strength`` an int
    1-5. Sorted strongest-first.
    """
    start, end = period
    trends: list[dict[str, Any]] = []
    trends += _entity_clusters_from_rows(_entity_cluster_rows(start, end))
    trends += _domain_surges_from_counts(
        _domain_counts(start, end), _domain_baseline_counts(start, end), _domain_item_ids(start, end)
    )
    trends += _convergence_from_rows(_convergence_rows(start, end))
    trends += _tech_race_from_rows(_tech_race_rows(start, end))
    trends.sort(key=lambda t: t["strength"], reverse=True)
    log.info("trends_detected", period_start=str(start), period_end=str(end), count=len(trends))
    return trends


# --------------------------------------------------------------------------
# public: weekly_stats
# --------------------------------------------------------------------------


def _items_by_domain_level(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    sql = """
        SELECT domain, level, count(*) AS n
        FROM items
        WHERE security_status = 'clean' AND dedup_of IS NULL
          AND COALESCE(published_at, fetched_at, created_at)::date BETWEEN %(start)s AND %(end)s
        GROUP BY domain, level
        ORDER BY domain, level
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": start, "end": end})
        return cur.fetchall()


def _top_entities_with_delta(start: dt.date, end: dt.date, limit: int = 10) -> list[dict[str, Any]]:
    """Top entities by mention count in the period, with ``delta`` = mentions here minus mentions
    in the immediately preceding period of equal length (week-over-week when ``period`` is a week)."""
    prev_end = start - dt.timedelta(days=1)
    prev_start = prev_end - (end - start)
    sql = """
        WITH this_period AS (
            SELECT unnest(entities_mentioned) AS entity, count(*) AS n
            FROM items
            WHERE security_status = 'clean' AND dedup_of IS NULL AND entities_mentioned IS NOT NULL
              AND COALESCE(published_at, fetched_at, created_at)::date BETWEEN %(start)s AND %(end)s
            GROUP BY entity
        ), prev_period AS (
            SELECT unnest(entities_mentioned) AS entity, count(*) AS n
            FROM items
            WHERE security_status = 'clean' AND dedup_of IS NULL AND entities_mentioned IS NOT NULL
              AND COALESCE(published_at, fetched_at, created_at)::date
                  BETWEEN %(prev_start)s AND %(prev_end)s
            GROUP BY entity
        )
        SELECT tp.entity AS entity, tp.n AS mentions, tp.n - COALESCE(pp.n, 0) AS delta
        FROM this_period tp
        LEFT JOIN prev_period pp ON pp.entity = tp.entity
        ORDER BY tp.n DESC
        LIMIT %(limit)s
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            sql,
            {"start": start, "end": end, "prev_start": prev_start, "prev_end": prev_end, "limit": limit},
        )
        return cur.fetchall()


def _events_by_kind(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    sql = """
        SELECT kind, count(*) AS n
        FROM events
        WHERE COALESCE(date, created_at::date) BETWEEN %(start)s AND %(end)s
        GROUP BY kind
        ORDER BY n DESC
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": start, "end": end})
        return cur.fetchall()


def _deep_search_outcomes(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    sql = """
        SELECT COALESCE(result->>'outcome', state) AS outcome, count(*) AS n
        FROM jobs
        WHERE kind = 'deep_search' AND state IN ('done', 'partial')
          AND finished_at IS NOT NULL AND finished_at::date BETWEEN %(start)s AND %(end)s
        GROUP BY outcome
        ORDER BY n DESC
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": start, "end": end})
        return cur.fetchall()


def weekly_stats(period: Period) -> dict[str, Any]:
    """Aggregate counts for ``period = (start, end)``: items per domain/level, top entities by
    mentions with a delta vs. the equal-length preceding period, events by kind, and deep-search
    outcome counts. Named per the task spec; period-length agnostic (``eoa.report.monthly`` reuses
    it for a full-month range — see module docstring)."""
    start, end = period
    return {
        "items_by_domain_level": _items_by_domain_level(start, end),
        "top_entities": _top_entities_with_delta(start, end),
        "events_by_kind": _events_by_kind(start, end),
        "deep_search_outcomes": _deep_search_outcomes(start, end),
    }
