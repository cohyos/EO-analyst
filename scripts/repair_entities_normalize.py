#!/usr/bin/env python
"""Q3-13 repair (docs/qa/findings_Q3_r1.md): entity normalisation backfill over every existing
``entities`` row, built on ``eoa.pipeline.entity_normalize`` (the same helpers ``eoa.memory.
relational.upsert_entity`` now applies to every *new* write going forward).

Four independent passes, in order (each safe to re-run -- idempotent):

1. **Technique-like rejection**: an entity whose name is a method/algorithm, not a real entity
   (e.g. "image captioning", "RF-DETR vehicle detectors") is removed -- its references are cleaned
   up first (removed from ``items.entities_mentioned`` / ``events.parties``, nulled out of
   ``events.customer`` / ``events.program``), then the row itself is deleted (``graph_edges``
   referencing it cascade-delete automatically via its FK).
2. **Kind fixes**: every remaining entity's ``kind`` is re-derived via
   ``eoa.pipeline.entity_normalize.normalize_kind`` (weapon/system designations -> "system",
   government/military bodies -> "org", a watchlist company's own kind otherwise) and updated if
   it disagrees with what's on record.
3. **Country backfill**: an entity with no ``country`` that resolves to a watchlist record with a
   known country gets it filled in.
4. **Duplicate merge**: entities that normalise to the same watchlist canonical name (aliases,
   case variants, punctuation variants -- e.g. "Elbit Systems" / "elbit systems" / "אלביט") or, for
   non-watchlist names, the same case/punctuation-insensitive spelling, are merged into one row:
   the lowest id among the group survives (renamed to the canonical name when the group resolves
   to a watchlist company/program), the others are deleted after repointing every reference
   (``graph_edges.src_entity_id`` / ``dst_entity_id``, ``items.entities_mentioned``,
   ``events.parties`` / ``customer`` / ``program``) onto the survivor.

Use ``--dry-run`` to print the merge plan (and every other pass's planned changes) without writing
anything. No LLM calls -- pure SQL/Python.

Run with the same ``DATABASE_URL`` as the app, e.g.:

    PYTHONPATH=agent DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
        python scripts/repair_entities_normalize.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa import db  # noqa: E402
from eoa.pipeline.entity_normalize import (  # noqa: E402
    _is_system_designation,
    is_junk_entity,
    normalize_kind,
    normalize_name_key,
    resolve_canonical,
    resolve_company_country,
    resolve_country_name,
)


def _fetch_entities() -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, kind, country, aliases, focus, first_seen_item FROM entities ORDER BY id"
        )
        return cur.fetchall()


def _reject_junk(entities: list[dict[str, Any]], *, dry_run: bool) -> list[dict[str, Any]]:
    """Q3-13 r3 (docs/qa/findings_Q3_r2.md): reject every entity that fails the "real entity" gate
    -- a technique/algorithm name (as before) *or* a generic Hebrew concept/market/category phrase
    (e.g. "השוק הביטחוני", "תעשייה", "סטארט-אפים", "לקוחות בינלאומיים", "תמונות תרמיות", "מפעילים
    בשטח", "איומים בקבוצת משקל 3", "מלחמת איראן-עיראק", "מצר הורמוז") -- see
    ``eoa.pipeline.entity_normalize.is_junk_entity``."""
    report = []
    for ent in entities:
        if not is_junk_entity(ent["name"]):
            continue
        report.append({"id": ent["id"], "name": ent["name"]})
        if dry_run:
            continue
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE items SET entities_mentioned = array_remove(entities_mentioned, %(name)s) "
                "WHERE %(name)s = ANY(entities_mentioned)",
                {"name": ent["name"]},
            )
            cur.execute(
                "UPDATE events SET parties = array_remove(parties, %(name)s) WHERE %(name)s = ANY(parties)",
                {"name": ent["name"]},
            )
            cur.execute("UPDATE events SET customer = NULL WHERE customer = %(name)s", {"name": ent["name"]})
            cur.execute("UPDATE events SET program = NULL WHERE program = %(name)s", {"name": ent["name"]})
            cur.execute("DELETE FROM entities WHERE id = %(id)s", {"id": ent["id"]})
    return report


def _fix_kinds(entities: list[dict[str, Any]], *, dry_run: bool) -> list[dict[str, Any]]:
    report = []
    for ent in entities:
        new_kind = normalize_kind(ent["name"], ent["kind"])
        if new_kind == ent["kind"]:
            continue
        report.append(
            {"id": ent["id"], "name": ent["name"], "before_kind": ent["kind"], "after_kind": new_kind}
        )
        if not dry_run:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE entities SET kind = %(kind)s WHERE id = %(id)s",
                    {"kind": new_kind, "id": ent["id"]},
                )
    return report


def _backfill_country(entities: list[dict[str, Any]], *, dry_run: bool) -> list[dict[str, Any]]:
    """Q3-13 r3: fill ``country`` from (in order) a watchlist/curated-org match, then -- for a
    ``company``-kind entity not on either -- the static top-40 non-watchlist company->country map
    (:func:`resolve_company_country`). A ``country``-kind entity's own ``country`` column is left
    alone (it denotes *its* country, not a country it's *from*, which is a category error for a
    country entity itself -- covered instead by ``_fix_kinds``/``_merge_duplicates`` canonicalising
    the row's ``name`` itself)."""
    report = []
    for ent in entities:
        if ent.get("country") or ent.get("kind") == "country":
            continue
        canonical = resolve_canonical(ent["name"])
        country = (canonical or {}).get("country") or resolve_company_country(ent["name"])
        if not country:
            continue
        report.append({"id": ent["id"], "name": ent["name"], "country": country})
        if not dry_run:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE entities SET country = %(country)s WHERE id = %(id)s",
                    {"country": country, "id": ent["id"]},
                )
    return report


def _merge_key_and_target(ent: dict[str, Any]) -> tuple[str, str | None]:
    """``(group_key, canonical_target_name_or_None)`` for ``ent`` -- entities sharing the same
    ``group_key`` are candidates to merge; ``canonical_target_name`` (when not ``None``) is the
    watchlist canonical name the group should end up named after.

    Mirrors ``eoa.pipeline.entity_normalize.canonical_name_and_kind``'s own precedence exactly: a
    known system/weapon designation (e.g. "LOCUST", "TITAN") is never folded into the company it
    happens to be listed as a watchlist alias under (watchlist aliases mix true alternate
    spellings of the company with the company's own product/program names) -- it only groups with
    other case/punctuation variants of its own literal name."""
    if _is_system_designation(ent["name"]):
        return f"norm:{normalize_name_key(ent['name'])}", None
    canonical = resolve_canonical(ent["name"])
    if canonical:
        return f"canonical:{canonical['name']}", canonical["name"]
    country_name = resolve_country_name(ent["name"])
    if country_name:
        # Q3-13 r3: a genuine country entity, however spelled/language ("יפן" vs "Japan", "ארה\"ב"
        # vs "ארצות הברית") -- merge onto its canonical English display name.
        return f"country:{country_name}", country_name
    return f"norm:{normalize_name_key(ent['name'])}", None


def _repoint_and_merge(loser_id: int, loser_name: str, winner_id: int, winner_name: str) -> None:
    with db.connection() as conn, conn.cursor() as cur:
        # graph_edges: drop a loser-side edge that would exactly duplicate one the winner already
        # has (unique on src/dst/label/item_id), then repoint the rest.
        for col, other_col in (("src_entity_id", "dst_entity_id"), ("dst_entity_id", "src_entity_id")):
            cur.execute(
                f"""
                DELETE FROM graph_edges ge USING graph_edges winner_ge
                WHERE ge.{col} = %(loser)s AND winner_ge.{col} = %(winner)s
                  AND ge.{other_col} = winner_ge.{other_col} AND ge.label = winner_ge.label
                  AND COALESCE(ge.item_id, -1) = COALESCE(winner_ge.item_id, -1)
                """,
                {"loser": loser_id, "winner": winner_id},
            )
            cur.execute(
                f"UPDATE graph_edges SET {col} = %(winner)s WHERE {col} = %(loser)s",
                {"winner": winner_id, "loser": loser_id},
            )
        # items.entities_mentioned: replace the loser's name with the winner's, then dedupe.
        cur.execute(
            "SELECT id, entities_mentioned FROM items WHERE %(name)s = ANY(entities_mentioned)",
            {"name": loser_name},
        )
        for row in cur.fetchall():
            names = row["entities_mentioned"] or []
            deduped = list(dict.fromkeys(winner_name if n == loser_name else n for n in names))
            cur.execute(
                "UPDATE items SET entities_mentioned = %(names)s WHERE id = %(id)s",
                {"names": deduped, "id": row["id"]},
            )
        # events.parties (array, same treatment), customer/program (scalar).
        cur.execute("SELECT id, parties FROM events WHERE %(name)s = ANY(parties)", {"name": loser_name})
        for row in cur.fetchall():
            parties = row["parties"] or []
            deduped = list(dict.fromkeys(winner_name if p == loser_name else p for p in parties))
            cur.execute(
                "UPDATE events SET parties = %(parties)s WHERE id = %(id)s",
                {"parties": deduped, "id": row["id"]},
            )
        cur.execute(
            "UPDATE events SET customer = %(winner)s WHERE customer = %(loser)s",
            {"winner": winner_name, "loser": loser_name},
        )
        cur.execute(
            "UPDATE events SET program = %(winner)s WHERE program = %(loser)s",
            {"winner": winner_name, "loser": loser_name},
        )
        cur.execute("DELETE FROM entities WHERE id = %(id)s", {"id": loser_id})


def _merge_duplicates(entities: list[dict[str, Any]], *, dry_run: bool) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    targets: dict[str, str | None] = {}
    for ent in entities:
        key, target = _merge_key_and_target(ent)
        groups[key].append(ent)
        targets[key] = target

    report = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda e: e["id"])
        canonical_target = targets[key]
        winner = next((m for m in members if m["name"] == canonical_target), None) or members[0]
        winner_name = canonical_target or winner["name"]
        losers = [m for m in members if m["id"] != winner["id"]]
        report.append(
            {
                "winner_id": winner["id"],
                "winner_name": winner_name,
                "renamed_from": winner["name"] if winner["name"] != winner_name else None,
                "merged": [{"id": m["id"], "name": m["name"]} for m in losers],
            }
        )
        if dry_run:
            continue
        if winner["name"] != winner_name:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE entities SET name = %(name)s WHERE id = %(id)s",
                    {"name": winner_name, "id": winner["id"]},
                )
        for loser in losers:
            _repoint_and_merge(loser["id"], loser["name"], winner["id"], winner_name)
    return report


def repair(*, dry_run: bool = False) -> dict[str, Any]:
    entities = _fetch_entities()
    before_count = len(entities)

    rejected = _reject_junk(entities, dry_run=dry_run)
    rejected_ids = {r["id"] for r in rejected}
    entities = [e for e in entities if e["id"] not in rejected_ids]

    kind_fixed = _fix_kinds(entities, dry_run=dry_run)
    country_backfilled = _backfill_country(entities, dry_run=dry_run)
    merged = _merge_duplicates(entities, dry_run=dry_run)

    return {
        "before_count": before_count,
        "junk_rejected": rejected,
        "technique_like_rejected": rejected,  # backwards-compatible alias (same list)
        "kind_fixed": kind_fixed,
        "country_backfilled": country_backfilled,
        "duplicates_merged": merged,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report the merge/fix plan without writing it")
    args = parser.parse_args()

    report = repair(dry_run=args.dry_run)

    print(
        f"{'=' * 70}\nQ3-13 entity normalisation repair {'(DRY RUN)' if args.dry_run else '(APPLIED)'}\n{'=' * 70}"
    )
    print(f"\nentities before: {report['before_count']}")

    print(f"\njunk_rejected (technique-like + generic non-entities): {len(report['junk_rejected'])}")
    for r in report["junk_rejected"][:20]:
        print(f"  {r}")

    print(f"\nkind_fixed: {len(report['kind_fixed'])}")
    for r in report["kind_fixed"][:20]:
        print(f"  {r}")

    print(f"\ncountry_backfilled: {len(report['country_backfilled'])}")
    for r in report["country_backfilled"][:20]:
        print(f"  {r}")

    merge_count = len(report["duplicates_merged"])
    merged_rows = sum(len(g["merged"]) for g in report["duplicates_merged"])
    print(f"\nduplicates_merged: {merge_count} group(s), {merged_rows} row(s) merged away")
    for g in report["duplicates_merged"][:20]:
        print(
            f"  winner id={g['winner_id']} name={g['winner_name']!r} (renamed_from={g['renamed_from']!r}) <- {g['merged']}"
        )

    if not args.dry_run:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM entities")
            after_count = cur.fetchone()["n"]
        print(f"\nentities after: {after_count}  (removed: {report['before_count'] - after_count})")

    print(f"\n{'=' * 70}")
    if args.dry_run:
        print("(dry run -- nothing written; re-run without --dry-run to apply)")


if __name__ == "__main__":
    main()
