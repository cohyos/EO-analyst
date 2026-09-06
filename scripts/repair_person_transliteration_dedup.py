#!/usr/bin/env python
"""Round-2 repair (docs/qa/loop/round_0_judge.md item 3, D3): merges person-name duplicates that
differ only by Hebrew<->English transliteration/spelling -- verified live: item 81's Anduril-Israel
appointee is recorded as four distinct spellings across the corpus ("Amikam Norkin" / "Amiram
Norkin" in ``entities`` -- both stored with the wrong ``kind='company'`` -- and "עמירם נורקין" /
"אמירם נורקין" in event titles/parties text), none of which ``eoa.pipeline.entity_normalize``'s
existing dedup (``normalize_name_key``: case/punctuation only) or watchlist matching (a person is
never a watchlist entry) can catch.

Uses ``eoa.pipeline.entity_normalize.looks_like_person_name`` (scope: a 2-4 word name, not a
recognised watchlist/curated-org/country entity, not carrying a company-suffix word) and
``is_likely_same_person`` (a small Hebrew-consonant/Latin-consonant transliteration skeleton +
``difflib`` ratio >= 0.85, see that module for the mechanism) to group ``entities`` rows, then
merges each group onto one survivor:

- Prefer a Latin-script spelling over a Hebrew one (a Latin name is what the rest of the pipeline/
  reports render).
- Among Latin-script candidates (or if none), prefer whichever spelling is referenced by the most
  rows across ``items.entities_mentioned`` + ``events.parties/customer/program`` (the majority
  spelling); ties broken by lowest ``entities.id``.
- Also fixes ``kind`` to ``'person'`` on the survivor when it was misfiled (e.g. ``'company'``,
  the observed live bug: an edge endpoint with an unresolved kind defaults to ``'company'``).

Repoints every reference (``graph_edges``, ``items.entities_mentioned``, ``events.parties``/
``customer``/``program``, and event *title/summary text* -- a plain string replace of the losing
spelling with the winning one, since those are free Hebrew prose, not structured references) onto
the survivor, then deletes the loser rows. Idempotent: a group already merged has only one member
left, so a re-run is a no-op.

Use ``--dry-run`` to print the plan without writing anything. No LLM calls -- pure SQL/Python.

Run with the same ``DATABASE_URL`` as the app, e.g.:

    PYTHONPATH=agent DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
        python scripts/repair_person_transliteration_dedup.py [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa import db  # noqa: E402
from eoa.pipeline.entity_normalize import is_likely_same_person, looks_like_person_name  # noqa: E402


def _is_latin(name: str) -> bool:
    return all(not ("֐" <= ch <= "׿") for ch in name)


def _fetch_person_like_entities() -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, kind FROM entities ORDER BY id")
        rows = cur.fetchall()
    return [r for r in rows if looks_like_person_name(r["name"])]


def _reference_count(name: str) -> int:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                (SELECT count(*) FROM items WHERE %(name)s = ANY(entities_mentioned))
              + (SELECT count(*) FROM events WHERE %(name)s = ANY(parties))
              + (SELECT count(*) FROM events WHERE customer = %(name)s)
              + (SELECT count(*) FROM events WHERE program = %(name)s)
              AS n
            """,
            {"name": name},
        )
        return cur.fetchone()["n"]


def _group_duplicates(entities: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Union-find style grouping: two entities in the same group if
    :func:`is_likely_same_person` says so, transitively."""
    parent = {e["id"]: e["id"] for e in entities}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, a in enumerate(entities):
        for b in entities[i + 1 :]:
            if is_likely_same_person(a["name"], b["name"]):
                union(a["id"], b["id"])

    groups: dict[int, list[dict[str, Any]]] = {}
    for e in entities:
        groups.setdefault(find(e["id"]), []).append(e)
    return [g for g in groups.values() if len(g) > 1]


def _pick_winner(group: list[dict[str, Any]]) -> dict[str, Any]:
    latin = [e for e in group if _is_latin(e["name"])]
    candidates = latin or group
    scored = [(e, _reference_count(e["name"])) for e in candidates]
    scored.sort(key=lambda pair: (-pair[1], pair[0]["id"]))
    return scored[0][0]


def _repoint_and_merge(loser_id: int, loser_name: str, winner_id: int, winner_name: str) -> None:
    with db.connection() as conn, conn.cursor() as cur:
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
        # Free-text title/summary mentions (Hebrew prose, e.g. "מינוי עמירם נורקין ...") -- a plain
        # substring replace, best-effort (a name with special regex chars is used literally here,
        # not as a pattern).
        cur.execute(
            "UPDATE events SET title = replace(title, %(loser)s, %(winner)s) WHERE title LIKE '%%' || %(loser)s || '%%'",
            {"loser": loser_name, "winner": winner_name},
        )
        cur.execute(
            "UPDATE events SET summary_he = replace(summary_he, %(loser)s, %(winner)s) "
            "WHERE summary_he LIKE '%%' || %(loser)s || '%%'",
            {"loser": loser_name, "winner": winner_name},
        )
        cur.execute("DELETE FROM entities WHERE id = %(id)s", {"id": loser_id})


def repair(*, dry_run: bool = False) -> dict[str, Any]:
    entities = _fetch_person_like_entities()
    groups = _group_duplicates(entities)

    report = []
    for group in groups:
        winner = _pick_winner(group)
        losers = [e for e in group if e["id"] != winner["id"]]
        report.append(
            {
                "winner_id": winner["id"],
                "winner_name": winner["name"],
                "winner_kind_before": winner["kind"],
                "merged": [{"id": e["id"], "name": e["name"], "kind": e["kind"]} for e in losers],
            }
        )
        if dry_run:
            continue
        if winner["kind"] != "person":
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute("UPDATE entities SET kind = 'person' WHERE id = %(id)s", {"id": winner["id"]})
        for loser in losers:
            _repoint_and_merge(loser["id"], loser["name"], winner["id"], winner["name"])

    return {
        "groups_merged": len(report),
        "rows_merged": sum(len(g["merged"]) for g in report),
        "groups": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report the merge plan without writing it")
    args = parser.parse_args()

    report = repair(dry_run=args.dry_run)

    print(
        f"{'=' * 70}\nRound-2 person-transliteration dedup {'(DRY RUN)' if args.dry_run else '(APPLIED)'}\n{'=' * 70}"
    )
    print(f"\ngroups merged: {report['groups_merged']}  rows merged away: {report['rows_merged']}")
    for g in report["groups"]:
        print(
            f"  winner id={g['winner_id']} name={g['winner_name']!r} "
            f"(kind {g['winner_kind_before']!r} -> 'person') <- {g['merged']}"
        )

    print(f"\n{'=' * 70}")
    if args.dry_run:
        print("(dry run -- nothing written; re-run without --dry-run to apply)")


if __name__ == "__main__":
    main()
