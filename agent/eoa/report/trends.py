"""Stage: report/trends — cross-item pattern detection (FR-4.3) and period aggregate stats feeding
the weekly/monthly reports. Pure SQL/Python: **no LLM call happens anywhere in this module**.

``weekly_stats(period)`` aggregates counts (items per domain/level, top entities by mentions with a
delta vs. the immediately preceding equal-length period, events by kind, deep-search outcomes) for
an arbitrary ``period = (start, end)``. Despite the name (kept verbatim from the task spec, which
names this exact signature), it is period-length agnostic and ``eoa.report.monthly`` calls it with
a full-month range.

``detect_trends(period)`` looks for five FR-4.3 pattern kinds over the same period (CR-monthly.md,
2026-09-08 user feedback: the original four's thresholds were loose enough to fire unsupported
"surge"/"increased activity" claims off a bare item-count floor with no real comparison behind
them — see each function's own docstring for the specific root cause it fixes):

(a) **entity clusters** — >=3 items about the same entity+domain, from >=2 distinct sources
(b) **domain active** — a domain has >=3 triaged items this period but no baseline to compare to
    (never called a "surge" -- see :func:`_domain_surges_from_counts`)
(c) **domain surge** — a domain's item count is >=2x a real (>=2/week) baseline AND >=5 items
(d) **market convergence** — >=3 M&A/partnership events touching the same subdomain, spanning >=2
    distinct party sets (not the same two companies' deal reported three times)
(e) **tech race** — >=2 different companies with launch/test events in the same subdomain

Every returned trend dict also carries ``n_items``/``n_sources``/``baseline_avg``/``ratio`` (the
last two ``None`` where not applicable) so the drafting prompt always has the real numbers behind
the claim to write from, instead of only a pre-composed Hebrew title.

Each row-fetching helper (``_*_rows`` / ``_*_counts``) below is a single, thin DB query; each
``_*_from_*`` function is pure Python over already-fetched rows, so the five detectors are
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
_ENTITY_CLUSTER_MIN_SOURCES = 2
_DOMAIN_SURGE_MIN_ITEMS = 3
_DOMAIN_SURGE_MIN_BASELINE_AVG = 2.0
_DOMAIN_SURGE_MIN_RATIO = 2.0
_DOMAIN_SURGE_MIN_ITEMS_FOR_RISE = 5
#: CR-monthly.md (2026-09-08 user feedback): >=3 events is no longer enough on its own -- see
#: :func:`_convergence_from_rows`'s docstring.
_CONVERGENCE_MIN_EVENTS = 3
_CONVERGENCE_MIN_PARTY_SETS = 2
_TECH_RACE_MIN_COMPANIES = 2
#: CR-monthly.md item 2 ("keep at most the 8 most relevant"): every trend's own
#: ``evidence_item_ids`` is capped at this many entries for display/citation purposes -- the
#: underlying ``n_items``/``n_sources`` counts (item e) still reflect the FULL matching set, not
#: just the capped sample.
_MAX_EVIDENCE_ITEMS_PER_TREND = 8


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
    """CR-monthly.md item 1(b): an entity cluster now also needs source diversity (>=2 distinct
    outlets), not just >=3 items -- three items from the same wire-service rewrite is not "ריכוז
    דיווחים". ``item_ids`` is ordered by score (desc) so the caller can keep only the most relevant
    ones (item 2, "keep at most the 8 most relevant") without a second query."""
    sql = """
        SELECT entity, domain,
               array_agg(id ORDER BY score DESC NULLS LAST) AS item_ids,
               count(DISTINCT id) AS n,
               count(DISTINCT source_id) AS n_sources
        FROM (
            SELECT id, score, source_id, domain, unnest(entities_mentioned) AS entity
            FROM items
            WHERE security_status = 'clean'
              AND dedup_of IS NULL
              AND entities_mentioned IS NOT NULL
              AND COALESCE(domain, '') <> 'out_of_scope' AND COALESCE(level, '') <> 'archive'
              AND cardinality(entities_mentioned) > 0
              AND COALESCE(published_at, fetched_at, created_at)::date BETWEEN %(start)s AND %(end)s
        ) sub
        GROUP BY entity, domain
        HAVING count(DISTINCT id) >= %(min)s
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": start, "end": end, "min": _ENTITY_CLUSTER_MIN_ITEMS})
        return cur.fetchall()


def _entity_clusters_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CR-monthly.md item 1(b): title now states the real counts ("ריכוז דיווחים: X בתחום Y (N
    פריטים, M מקורות)") instead of the unsupported "פעילות מוגברת" ("increased activity") framing
    that used to fire on a bare 3-item floor with no comparison to anything. Requires >=3 items
    FROM >=2 distinct sources -- either threshold missed means this is not a reportable cluster."""
    out: list[dict[str, Any]] = []
    for row in rows:
        # de-dupe while preserving the SQL's score-desc order (see the query's own ORDER BY) --
        # a plain `set()` would lose that ordering.
        item_ids = list(dict.fromkeys(row.get("item_ids") or []))
        n = row.get("n") or len(item_ids)
        n_sources = row.get("n_sources") or 0
        if n < _ENTITY_CLUSTER_MIN_ITEMS or n_sources < _ENTITY_CLUSTER_MIN_SOURCES or not item_ids:
            continue
        entity = row.get("entity") or ""
        domain = row.get("domain")
        out.append(
            {
                "kind": "entity_cluster",
                "title_he": f"ריכוז דיווחים: {entity} בתחום {_domain_label(domain)} ({n} פריטים, {n_sources} מקורות)",
                "evidence_item_ids": item_ids[:_MAX_EVIDENCE_ITEMS_PER_TREND],
                "entities": [entity] if entity else [],
                "strength": _clamp(n),
                "n_items": n,
                "n_sources": n_sources,
                "baseline_avg": None,
                "ratio": None,
            }
        )
    return out


# --------------------------------------------------------------------------
# (b) domain surge: domain item count >= 2x its 4-week baseline average
# --------------------------------------------------------------------------


#: CR-monthly.md item 1(a): domain trend evidence (both the count basis and the membership) is
#: restricted to triaged, meaningful items -- a domain full of un-triaged noise should never read
#: as "active"/"rising". Shared by every domain-scoped query below.
_DOMAIN_TREND_LEVELS = ("red", "orange", "yellow")


def _domain_counts(start: dt.date, end: dt.date) -> dict[str, int]:
    sql = """
        SELECT domain, count(*) AS n
        FROM items
        WHERE security_status = 'clean' AND dedup_of IS NULL AND domain IS NOT NULL
          AND domain <> 'out_of_scope' AND level = ANY(%(levels)s)
          AND COALESCE(published_at, fetched_at, created_at)::date BETWEEN %(start)s AND %(end)s
        GROUP BY domain
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": start, "end": end, "levels": list(_DOMAIN_TREND_LEVELS)})
        return {r["domain"]: r["n"] for r in cur.fetchall()}


def _domain_source_counts(start: dt.date, end: dt.date) -> dict[str, int]:
    """CR-monthly.md item 1(e): every domain trend dict carries ``n_sources`` -- distinct outlets
    covering the domain in the period, over the FULL matching set (not the capped evidence sample
    the trend dict displays)."""
    sql = """
        SELECT domain, count(DISTINCT source_id) AS n
        FROM items
        WHERE security_status = 'clean' AND dedup_of IS NULL AND domain IS NOT NULL
          AND domain <> 'out_of_scope' AND level = ANY(%(levels)s)
          AND COALESCE(published_at, fetched_at, created_at)::date BETWEEN %(start)s AND %(end)s
        GROUP BY domain
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": start, "end": end, "levels": list(_DOMAIN_TREND_LEVELS)})
        return {r["domain"]: r["n"] or 0 for r in cur.fetchall()}


def _domain_baseline_counts(start: dt.date, end: dt.date) -> dict[str, float]:
    """Average weekly item count per domain over the ``_BASELINE_WEEKS`` weeks immediately
    preceding ``start`` (excluding the period being evaluated itself)."""
    baseline_start = start - dt.timedelta(weeks=_BASELINE_WEEKS)
    baseline_end = start - dt.timedelta(days=1)
    sql = """
        SELECT domain, count(*) AS n
        FROM items
        WHERE security_status = 'clean' AND dedup_of IS NULL AND domain IS NOT NULL
          AND domain <> 'out_of_scope' AND level = ANY(%(levels)s)
          AND COALESCE(published_at, fetched_at, created_at)::date BETWEEN %(start)s AND %(end)s
        GROUP BY domain
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": baseline_start, "end": baseline_end, "levels": list(_DOMAIN_TREND_LEVELS)})
        return {r["domain"]: (r["n"] or 0) / _BASELINE_WEEKS for r in cur.fetchall()}


def _domain_item_ids(start: dt.date, end: dt.date) -> dict[str, list[int]]:
    """CR-monthly.md item 2 ("item.domain == domain and level in red/orange/yellow"): restricted to
    the same triaged levels as :func:`_domain_counts`; ``item_ids`` ordered by score (desc) so the
    caller keeps only the most relevant ones (item 2, "at most the 8 most relevant")."""
    sql = """
        SELECT domain, array_agg(id ORDER BY score DESC NULLS LAST) AS item_ids
        FROM items
        WHERE security_status = 'clean' AND dedup_of IS NULL AND domain IS NOT NULL
          AND domain <> 'out_of_scope' AND level = ANY(%(levels)s)
          AND COALESCE(published_at, fetched_at, created_at)::date BETWEEN %(start)s AND %(end)s
        GROUP BY domain
    """
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"start": start, "end": end, "levels": list(_DOMAIN_TREND_LEVELS)})
        return {r["domain"]: list(r["item_ids"] or []) for r in cur.fetchall()}


def _surge_strength(ratio: float) -> int:
    if ratio >= 4:
        return 5
    if ratio >= 3:
        return 4
    return 3


def _fmt_avg(avg: float) -> str:
    """``2`` for a whole number, ``2.3`` otherwise -- avoids a misleadingly precise "ממוצע 2.00"."""
    return f"{avg:g}"


def _domain_surges_from_counts(
    counts: dict[str, int],
    baseline: dict[str, float],
    items_by_domain: dict[str, list[int]],
    source_counts: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    """CR-monthly.md item 1(a) (2026-09-08 user feedback on monthly_2026-09-30.md): the old
    "no baseline -> fall back to a bare 3-item floor" rule is exactly the root cause the user
    flagged -- every domain in a first-ever monthly corpus (no baseline by construction) got
    labelled "זינוק" ("surge") off a bare item count with nothing to compare it to. Two distinct,
    honestly-labelled outcomes now:

    * **no baseline at all** (``avg == 0``) -- never a "surge": an informational "תחום פעיל בתקופה"
      entry (``kind="domain_active"``) naming the real counts and saying explicitly there is no
      comparison basis, gated only by the plain item-count floor (``_DOMAIN_SURGE_MIN_ITEMS``) so a
      domain with a single item still doesn't get a mention.
    * **has a real baseline** -- only a genuine rise (``kind="domain_surge"``) counts: baseline
      average >= 2/week AND ratio >= 2x AND at least 5 items this period. Anything below that bar is
      simply dropped (not relabeled), since a small blip around a small baseline is not a reportable
      rise either.

    ``source_counts`` (new, item 1(e)) is optional only so existing callers/tests that don't pass it
    still work (``n_sources`` falls back to 0 in that case)."""
    source_counts = source_counts or {}
    out: list[dict[str, Any]] = []
    for domain, week_count in counts.items():
        avg = baseline.get(domain, 0.0)
        n_sources = source_counts.get(domain, 0)
        item_ids = list(dict.fromkeys(items_by_domain.get(domain) or []))[:_MAX_EVIDENCE_ITEMS_PER_TREND]
        if avg <= 0:
            if week_count < _DOMAIN_SURGE_MIN_ITEMS:
                continue
            out.append(
                {
                    "kind": "domain_active",
                    "title_he": (
                        f"תחום פעיל בתקופה: {_domain_label(domain)} — {week_count} פריטים מ-{n_sources} "
                        "מקורות (אין בסיס השוואה מחודש קודם)"
                    ),
                    "evidence_item_ids": item_ids,
                    "entities": [],
                    "strength": _clamp(week_count),
                    "n_items": week_count,
                    "n_sources": n_sources,
                    "baseline_avg": 0.0,
                    "ratio": None,
                }
            )
            continue
        ratio = week_count / avg
        is_rise = (
            avg >= _DOMAIN_SURGE_MIN_BASELINE_AVG
            and ratio >= _DOMAIN_SURGE_MIN_RATIO
            and week_count >= _DOMAIN_SURGE_MIN_ITEMS_FOR_RISE
        )
        if not is_rise:
            continue
        out.append(
            {
                "kind": "domain_surge",
                "title_he": (
                    f"עלייה בפעילות בתחום {_domain_label(domain)}: {week_count} פריטים לעומת ממוצע "
                    f"{_fmt_avg(avg)}"
                ),
                "evidence_item_ids": item_ids,
                "entities": [],
                "strength": _surge_strength(ratio),
                "n_items": week_count,
                "n_sources": n_sources,
                "baseline_avg": avg,
                "ratio": ratio,
            }
        )
    return out


# --------------------------------------------------------------------------
# (c) market convergence: >=2 M&A/partnership events touching the same subdomain
# --------------------------------------------------------------------------


def _source_count_for_items(item_ids: list[int]) -> int:
    """CR-monthly.md item 1(e): ``n_sources`` for a trend kind that isn't keyed by a single
    domain/entity (market convergence, tech race) -- distinct outlets among its own evidence items.
    A trivial one-shot query; the number of trends per period is always small (single digits), so
    one extra round-trip per trend is not a real cost."""
    if not item_ids:
        return 0
    sql = "SELECT count(DISTINCT source_id) AS n FROM items WHERE id = ANY(%(ids)s)"
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"ids": list(item_ids)})
        row = cur.fetchone()
    return (row or {}).get("n") or 0


def _convergence_rows(start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    """CR-monthly.md item 1(d): raised from >=2 to >=3 events, AND now also requires >=2 distinct
    *party sets* among them (see :func:`_convergence_from_rows`) -- three deals all involving the
    same two companies is one ongoing relationship, not a market-wide convergence. ``event_parties``
    is kept one entry per event (not flattened, unlike the old query) so the caller can tell distinct
    deals apart by their own party sets."""
    sql = """
        WITH ev AS (
            SELECT e.id AS event_id, e.item_id, e.parties, i.subdomain
            FROM events e
            JOIN items i ON i.id = e.item_id
            WHERE e.kind IN ('m_and_a', 'partnership')
              AND i.subdomain IS NOT NULL AND i.subdomain <> ''
              AND COALESCE(i.domain, '') <> 'out_of_scope' AND COALESCE(i.level, '') <> 'archive'
              AND COALESCE(e.date, i.published_at::date, i.fetched_at::date, i.created_at::date)
                  BETWEEN %(start)s AND %(end)s
        )
        SELECT subdomain, count(DISTINCT event_id) AS n,
               array_agg(DISTINCT item_id) AS item_ids,
               -- `array_agg(parties)` raised "cannot accumulate arrays of different
               -- dimensionality" (confirmed live, EOA_PIPELINE=1 monthly rebuild 2026-09-08): an
               -- empty text[] (whether from a NULL COALESCE or a literally-stored '{}') reports 0
               -- dimensions in Postgres, which can't accumulate alongside a populated 1-D array in
               -- the same array_agg. jsonb_agg has no such dimension constraint -- psycopg decodes
               -- each element back into a plain Python list (or [] for a null/empty one).
               jsonb_agg(COALESCE(to_jsonb(parties), '[]'::jsonb)) AS event_parties
        FROM ev
        GROUP BY subdomain
        HAVING count(DISTINCT event_id) >= %(min)s
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


def _distinct_party_sets(event_parties: list[list[str] | None] | None) -> list[frozenset[str]]:
    """One ``frozenset`` per event's own ``parties`` list (empty/``None`` parties count as the
    empty set, which is still one distinct "set" if every event in the group has no parties at
    all — that case can never reach 2 distinct sets, so it simply won't qualify)."""
    return [frozenset(p or []) for p in (event_parties or [])]


def _convergence_from_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CR-monthly.md item 1(d): >=3 events AND >=2 distinct party sets among them -- three deals
    that are all really the same two companies (e.g. a staged acquisition reported three times) is
    one relationship, not "market convergence"."""
    out: list[dict[str, Any]] = []
    for row in rows:
        n = row.get("n") or 0
        party_sets = _distinct_party_sets(row.get("event_parties"))
        distinct_sets = {s for s in party_sets if s}
        if n < _CONVERGENCE_MIN_EVENTS or len(distinct_sets) < _CONVERGENCE_MIN_PARTY_SETS:
            continue
        subdomain = row.get("subdomain") or ""
        item_ids = sorted(set(row.get("item_ids") or []))
        entities = sorted({p for s in distinct_sets for p in s})
        n_sources = _source_count_for_items(item_ids)
        out.append(
            {
                "kind": "market_convergence",
                "title_he": (
                    f'התכנסות שוק בתת-התחום "{_subdomain_label(subdomain)}" — {n} עסקאות מיזוג/רכישה '
                    f"ושותפות בתקופה, {len(distinct_sets)} קבוצות צדדים נבדלות"
                ),
                "evidence_item_ids": item_ids[:_MAX_EVIDENCE_ITEMS_PER_TREND],
                "entities": entities,
                "strength": _clamp(n + 1, lo=3, hi=5),
                "n_items": len(item_ids),
                "n_sources": n_sources,
                "baseline_avg": None,
                "ratio": None,
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
        item_ids = sorted(set(row.get("item_ids") or []))
        n_sources = _source_count_for_items(item_ids)
        out.append(
            {
                "kind": "tech_race",
                "title_he": (
                    f'מירוץ טכנולוגי בתת-התחום "{_subdomain_label(subdomain)}" — {len(companies)} חברות עם השקות/ניסויים בתקופה'
                ),
                "evidence_item_ids": item_ids[:_MAX_EVIDENCE_ITEMS_PER_TREND],
                "entities": companies,
                "strength": _clamp(len(companies) + 1, lo=3, hi=5),
                "n_items": len(item_ids),
                "n_sources": n_sources,
                "baseline_avg": None,
                "ratio": None,
            }
        )
    return out


# --------------------------------------------------------------------------
# public: detect_trends
# --------------------------------------------------------------------------


def detect_trends(period: Period) -> list[dict[str, Any]]:
    """Cross-item pattern detection per FR-4.3 over ``period = (start, end)``. No LLM call.

    Each returned dict: ``{kind, title_he, evidence_item_ids, entities, strength, n_items,
    n_sources, baseline_avg, ratio}`` — ``kind`` in ``{"entity_cluster", "domain_active",
    "domain_surge", "market_convergence", "tech_race"}`` (CR-monthly.md item 1(a): "domain_active"
    is new -- an honestly-labelled "active domain, no comparison basis" entry, never called a
    surge), ``strength`` an int 1-5. ``baseline_avg``/``ratio`` are ``None`` for every kind except
    ``domain_surge``/``domain_active`` (item 1(e): the field is always present, even when not
    applicable, so the prompt formatter never needs a ``kind``-specific branch just to print it).
    Sorted strongest-first.
    """
    start, end = period
    trends: list[dict[str, Any]] = []
    trends += _entity_clusters_from_rows(_entity_cluster_rows(start, end))
    trends += _domain_surges_from_counts(
        _domain_counts(start, end),
        _domain_baseline_counts(start, end),
        _domain_item_ids(start, end),
        _domain_source_counts(start, end),
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
