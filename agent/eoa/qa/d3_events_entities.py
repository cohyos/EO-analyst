"""D3 -- events & entities deterministic checks (docs/QA_CONTINUOUS_LOOP.md table row D3).

Unlike D1/D2, this domain's checks are not naturally per-``items``-row -- they run over the
``events``/``entities`` rows attached to the sampled items (plus a small recent window for
entities, so a newly-created junk entity that isn't yet referenced by any sampled item is still
caught). Requires a live ``conn`` (a ``psycopg`` connection with ``dict_row`` factory).
"""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from typing import Any

from eoa.pipeline.analyze import _is_narrative_event_title
from eoa.pipeline.entity_normalize import VALID_ENTITY_KINDS, is_junk_entity
from eoa.qa.types import Check, DomainScore, weighted_score
from eoa.report.geography import UNKNOWN_COUNTRY, normalize_country


def _fetch_events_for_items(conn: Any, item_ids: list[int]) -> list[dict[str, Any]]:
    if not item_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, item_id, kind, title, date, amount_usd, customer, program, parties "
            "FROM events WHERE item_id = ANY(%s)",
            (item_ids,),
        )
        return cur.fetchall()


def _fetch_entities(conn: Any, item_ids: list[int], recent_days: int = 7) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT e.id, e.name, e.kind, e.country "
            "FROM entities e "
            "LEFT JOIN items it ON it.id = ANY(%s) AND e.name = ANY(COALESCE(it.entities_mentioned, ARRAY[]::text[])) "
            "WHERE it.id IS NOT NULL OR e.created_at > now() - (%s || ' days')::interval",
            (item_ids, recent_days),
        )
        return cur.fetchall()


def _duplicate_event_groups(events: list[dict[str, Any]]) -> list[tuple[int, str, str]]:
    counts = Counter((ev["item_id"], ev.get("kind") or "", (ev.get("title") or "").strip().casefold()) for ev in events)
    return [key for key, c in counts.items() if c > 1]


def _narrative_events(events: list[dict[str, Any]]) -> list[int]:
    bad = []
    for ev in events:
        shim = SimpleNamespace(
            parties=ev.get("parties"), customer=ev.get("customer"), amount_usd=ev.get("amount_usd"), date=ev.get("date")
        )
        if _is_narrative_event_title(ev.get("title"), shim):
            bad.append(ev["id"])
    return bad


def _kind_country_bad(entities: list[dict[str, Any]]) -> tuple[list[int], list[int]]:
    """``entities.country`` stores the ISO-2/region-code convention documented in
    ``eoa.report.geography`` (its own module docstring names ``entities.country`` as one of the
    two columns that convention normalizes) -- NOT a full country name, so validation reuses
    ``normalize_country`` (falls back to ``UNKNOWN_COUNTRY``/"other" for anything it can't map),
    not ``eoa.pipeline.entity_normalize.resolve_country_name`` (which instead answers "is this
    *name* itself a country", e.g. for detecting an entity mistakenly named "Israel")."""
    kind_bad = [e["id"] for e in entities if e.get("kind") not in VALID_ENTITY_KINDS]
    country_bad = [
        e["id"]
        for e in entities
        if e.get("country") and normalize_country(e["country"]) == UNKNOWN_COUNTRY
    ]
    return kind_bad, country_bad


def score_D3(sample: list[dict[str, Any]], conn: Any) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D3: events/entities deterministic checks scoped to the items in ``sample``.

    ``sample`` is a list of ``items`` rows (only ``id``/``entities_mentioned`` are used).
    ``conn`` is required (queries ``events``/``entities``).
    """
    item_ids = [it["id"] for it in sample]
    events = _fetch_events_for_items(conn, item_ids)
    entities = _fetch_entities(conn, item_ids)
    n = len(events) + len(entities)
    if n == 0:
        return DomainScore(domain="D3", score_0_100=None, checks=[], n=0, note="no events/entities in sample scope")

    dup_groups = _duplicate_event_groups(events)
    narrative_bad = _narrative_events(events)
    junk_entities = [e["id"] for e in entities if is_junk_entity(e.get("name") or "")]
    kind_bad, country_bad = _kind_country_bad(entities)

    checks = [
        Check(
            "events_duplicate_groups_zero",
            passed=len(dup_groups) == 0,
            weight=2.0,
            evidence=f"{len(events)} events, {len(dup_groups)} duplicate (item_id,kind,title) groups: {dup_groups[:5]}",
        ),
        Check(
            "no_narrative_events",
            passed=len(narrative_bad) == 0,
            weight=2.0,
            evidence=f"{len(events) - len(narrative_bad)}/{len(events)} concrete; narrative event ids: {narrative_bad[:10]}",
        ),
        Check(
            "entity_junk_gate",
            passed=len(junk_entities) == 0,
            weight=2.0,
            evidence=f"{len(entities) - len(junk_entities)}/{len(entities)} clean; junk entity ids: {junk_entities[:10]}",
        ),
        Check(
            "entity_kind_valid",
            passed=len(kind_bad) == 0,
            weight=1.0,
            evidence=f"invalid-kind entity ids: {kind_bad[:10]}",
        ),
        Check(
            "entity_country_resolves",
            passed=len(country_bad) == 0,
            weight=1.0,
            evidence=f"unresolvable-country entity ids: {country_bad[:10]}",
        ),
    ]
    return DomainScore(domain="D3", score_0_100=weighted_score(checks), checks=checks, n=n)
