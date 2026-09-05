#!/usr/bin/env python
"""Backfill `entities.relevance` / `entities.is_watchlist` / `entities.kind` for every
existing row (F15, 2026-09-06 rework).

`eoa.pipeline.entity_relevance.score_and_persist_entity` is wired into the `analyze`
pipeline stage going forward, but every entity already in the DB needs re-scoring under the
new formula (see `eoa.pipeline.entity_relevance`'s module docstring): the original version
scored purely from `items.entities_mentioned` (populated on only ~9% of items), so 271/408
entities scored 0 and were hidden below the Entities list's default `relevance >= 0.4`
filter -- including entities that are obviously in-scope by graph/event evidence the old
score never looked at ("US Navy", "Air Force", ...). This script computes the new score for
every existing entity in one pass and prints a report: how many land above/below the 0.4
threshold, how many entities were reclassified to kind='country', and the 15 highest/lowest
scores after rescoring, so a human can sanity-check the formula before trusting it in the UI.

A handful of bulk queries up front (entities, items, `graph_edges` joined to items, `events`
joined to items, `sources`) rather than one (or four) queries per entity -- all the
evidence-aggregation happens in Python via `eoa.pipeline.entity_relevance._build_evidence` /
`EntityEvidence`, then `compute_relevance_row` does the same pure scoring
`score_and_persist_entity` does for the live pipeline.

Usage:
    DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \
    PYTHONPATH=agent python scripts/repair_entity_relevance.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from typing import Any

sys.path.insert(0, "agent")

import structlog

from eoa.pipeline.entity_relevance import (
    RELEVANCE_THRESHOLD,
    EntityEvidence,
    _build_evidence,
    compute_relevance_row,
    is_news_source,
)

log = structlog.get_logger(__name__)


def _fetch_all(cur, query: str) -> list[dict[str, Any]]:
    cur.execute(query)
    return cur.fetchall()


def _mentions_by_name(entities: list[dict[str, Any]], items: list[dict[str, Any]]) -> dict[str, list[dict]]:
    """`{name-or-alias: [item rows]}` -- reverse index over every surface string that
    appears in some item's `entities_mentioned`, built once and shared across all entities."""
    index: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        names = item.get("entities_mentioned") or []
        if not names:
            continue
        row = {"id": item["id"], "domain": item["domain"], "level": item["level"]}
        for n in names:
            index[n].append(row)
    return index


def _edges_by_entity(edge_rows: list[dict[str, Any]]) -> dict[int, list[dict]]:
    """`{entity_id: [item rows]}` -- one entry per edge endpoint (an entity that is both
    src and dst of the same edge -- shouldn't happen, but would count twice, matching
    `graph_edges`' actual degree)."""
    index: dict[int, list[dict]] = defaultdict(list)
    for row in edge_rows:
        item_row = {"item_id": row["item_id"], "domain": row["domain"], "level": row["level"]}
        index[row["src_entity_id"]].append(item_row)
        index[row["dst_entity_id"]].append(item_row)
    return index


def _events_by_name(
    entities: list[dict[str, Any]], event_rows: list[dict[str, Any]]
) -> dict[str, list[dict]]:
    """`{entity name: [item rows]}` for events where the entity is a listed party, the
    customer, or appears (substring, case-insensitive) in the program text. Brute-force
    (entities x events -- ~400 x ~120 here) since both tables are small; a name shorter than
    3 characters is skipped for the program substring check only, to avoid noise matches."""
    index: dict[str, list[dict]] = defaultdict(list)
    party_index: dict[str, list[dict]] = defaultdict(list)
    customer_index: dict[str, list[dict]] = defaultdict(list)
    program_rows: list[tuple[dict, str]] = []
    for ev in event_rows:
        item_row = {"item_id": ev["item_id"], "domain": ev["domain"], "level": ev["level"]}
        for p in ev.get("parties") or []:
            party_index[p].append(item_row)
        if ev.get("customer"):
            customer_index[ev["customer"]].append(item_row)
        if ev.get("program"):
            program_rows.append((item_row, ev["program"].lower()))

    for entity in entities:
        name = entity["name"]
        hits: list[dict] = []
        hits.extend(party_index.get(name, []))
        hits.extend(customer_index.get(name, []))
        if len(name) >= 3:
            needle = name.lower()
            hits.extend(item_row for item_row, program_lower in program_rows if needle in program_lower)
        if hits:
            index[name] = hits
    return index


def run_repair() -> tuple[int, int, int, int, list[dict], list[dict]]:
    """Score every `entities` row.

    Returns `(total, above_threshold, below_threshold, reclassified_to_country,
    lowest_15, highest_15)`.
    """
    from eoa.db import connection

    with connection() as conn, conn.cursor() as cur:
        entities = _fetch_all(cur, "SELECT id, name, kind, aliases FROM entities ORDER BY id")
        items = _fetch_all(cur, "SELECT id, domain, level, entities_mentioned FROM items")
        edge_rows = _fetch_all(
            cur,
            """
            SELECT ge.src_entity_id, ge.dst_entity_id, ge.item_id, i.domain, i.level
            FROM graph_edges ge
            JOIN items i ON i.id = ge.item_id
            """,
        )
        event_rows = _fetch_all(
            cur,
            """
            SELECT e.parties, e.customer, e.program, e.item_id, i.domain, i.level
            FROM events e
            JOIN items i ON i.id = e.item_id
            """,
        )
        source_names = [r["name"] for r in _fetch_all(cur, "SELECT name FROM sources") if r.get("name")]

        mention_index = _mentions_by_name(entities, items)
        edge_index = _edges_by_entity(edge_rows)
        event_index = _events_by_name(entities, event_rows)

        above = 0
        below = 0
        reclassified = 0
        scored: list[dict] = []
        for row in entities:
            name_variants = [row["name"], *(row.get("aliases") or [])]
            mention_items: list[dict] = []
            for variant in name_variants:
                mention_items.extend(mention_index.get(variant, []))

            evidence: EntityEvidence = _build_evidence(
                mention_items=mention_items,
                edge_items=edge_index.get(row["id"], []),
                event_items=event_index.get(row["name"], []),
            )
            news = is_news_source(row["name"], source_names)
            score, watchlist, resolved_kind = compute_relevance_row(row, evidence, is_news_source=news)

            if resolved_kind != (row.get("kind") or "company"):
                reclassified += 1
            cur.execute(
                "UPDATE entities SET relevance = %s, is_watchlist = %s, kind = %s WHERE id = %s",
                (score, watchlist, resolved_kind, row["id"]),
            )
            if score >= RELEVANCE_THRESHOLD:
                above += 1
            else:
                below += 1
            scored.append(
                {
                    "id": row["id"],
                    "name": row["name"],
                    "kind": resolved_kind,
                    "mention_count": evidence.mention_count,
                    "edge_count": evidence.edge_count,
                    "event_count": evidence.event_count,
                    "in_scope": evidence.in_scope_evidence_count,
                    "total_evidence": evidence.total_evidence_count,
                    "relevance": score,
                    "is_watchlist": watchlist,
                }
            )
        conn.commit()

    scored.sort(key=lambda h: h["relevance"])
    lowest_15 = scored[:15]
    highest_15 = list(reversed(scored[-15:]))
    return len(entities), above, below, reclassified, lowest_15, highest_15


def _print_row(e: dict) -> None:
    print(
        f"    #{e['id']:<5} {e['name']:<40} kind={e['kind']:<8} "
        f"mentions={e['mention_count']:<3} edges={e['edge_count']:<3} events={e['event_count']:<3} "
        f"in_scope={e['in_scope']:<3}/{e['total_evidence']:<3} "
        f"wl={'Y' if e['is_watchlist'] else 'n':<1} relevance={e['relevance']}"
    )


if __name__ == "__main__":
    # Entity names include Hebrew/mixed-script text; Windows consoles often default to a
    # legacy code page (cp1252) that can't encode them -- widen stdout rather than crash
    # after the DB write (which already committed) has succeeded.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    total, above, below, reclassified, lowest_15, highest_15 = run_repair()
    print(f"\n{'=' * 70}\nEntity relevance backfill\n{'=' * 70}")
    print(f"  Total entities scored:            {total}")
    print(f"  Above threshold (>= {RELEVANCE_THRESHOLD}):      {above}")
    print(f"  Below threshold (hidden):         {below}")
    print(f"  Reclassified to kind='country':   {reclassified}")
    print("\n  15 lowest-relevance entities (what the default view now hides):")
    for e in lowest_15:
        _print_row(e)
    print("\n  15 highest-relevance entities:")
    for e in highest_15:
        _print_row(e)
    print(f"{'=' * 70}\n")
