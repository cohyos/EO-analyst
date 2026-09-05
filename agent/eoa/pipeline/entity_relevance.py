"""Entity relevance scoring (F15): keeps noise entities out of the Entities & Graph screen
without ever deleting them (CONVENTIONS.md rule 4 -- never invent, never destroy provenance).

Problem this solves (docs/REVIEW_2026-09-05.md F15): NER in `eoa.pipeline.classify` has no
domain filter, so a name mentioned once in an out-of-scope, low-level item -- e.g. "Zipline"
(a delivery-drone company) name-dropped in a Houston-highway news story -- enters `entities`
with the same visual weight as a tracked watchlist company like "Elbit". This module scores
every entity 0..1 from four signals and persists the score to `entities.relevance` (migration
0007) so the Entities list (U10) can default to `relevance >= 0.4` with a "show all" toggle,
instead of showing every NER hit undifferentiated.

Score composition (`score_entity`, a pure function -- no I/O, fully unit-testable):
  1. Watchlist match (name or alias, case-insensitive, against `config/watchlist.yaml`
     companies + programs) -> always 1.0. A tracked entity is relevant regardless of how
     little it has been mentioned or how it happened to be classified.
  2. Otherwise, combine:
     - `in_scope_fraction` (weight 0.55): of the entity's mentioning items, the fraction
       that are in-scope (`items.domain != 'out_of_scope'`) AND triaged at red/orange/yellow
       (not `archive`) -- i.e. items the rest of the app already treats as worth showing.
     - `mention_component` (weight 0.45): `mention_count` log-scaled so 1 mention -> ~0.33,
       8+ mentions -> 1.0 (`log2(n+1) / log2(9)`, clamped to 1.0) -- a name that keeps
       recurring across unrelated items is more likely a real, tracked player.
     - A `kind_multiplier`: `company`/`program`/`system`/`org` (the kinds classify.py's
       `EntityMention` schema and `_heuristic_kind` actually produce for organizations)
       keep the combined score as-is; `person`/`country` -- generic, frequently a
       passing name-drop rather than a tracked player -- are downweighted to 0.35x when
       mentioned only once, 0.7x otherwise.

`score_and_persist_entity(name)` does the DB I/O: looks up the entity row, aggregates its
mention stats from `items`, calls `score_entity`, and writes `relevance`/`is_watchlist` back
onto the `entities` row. It is called from `eoa.pipeline.analyze.run_analyze` (one call per
newly-analyzed item's `entities_mentioned`, guarded so a failure here never breaks analysis)
and from `scripts/repair_entity_relevance.py` (one-off backfill over every existing row).
"""

from __future__ import annotations

import math
from typing import Any

import structlog

from eoa.config import settings

log = structlog.get_logger(__name__)

RELEVANCE_THRESHOLD = 0.4

_KEEP_KINDS = {"company", "program", "system", "org"}
_IN_SCOPE_LEVELS = {"red", "orange", "yellow"}

_IN_SCOPE_WEIGHT = 0.55
_MENTION_WEIGHT = 0.45
_MENTION_SCALE = math.log2(9)  # 8 mentions -> mention_component == 1.0


def _watchlist_names() -> dict[str, list[str]]:
    """`{lowercased canonical name: [lowercased aliases]}` from `config/watchlist.yaml`."""
    wl = settings().watchlist or {}
    out: dict[str, list[str]] = {}
    for entry in (wl.get("companies") or []) + (wl.get("programs") or []):
        name = entry.get("name")
        if not name:
            continue
        out[name.lower()] = [a.lower() for a in entry.get("aliases", []) if a]
    return out


def is_watchlist_match(name: str, aliases: list[str] | None = None) -> bool:
    """True if `name` (or any of `aliases`) matches a watchlist company/program by name or alias."""
    candidates = {name.lower(), *[a.lower() for a in (aliases or [])]}
    for wl_name, wl_aliases in _watchlist_names().items():
        known = {wl_name, *wl_aliases}
        if candidates & known:
            return True
        # substring match both ways, mirroring `triage._watchlist_hits`'s tolerance for
        # e.g. "Rafael" matching a longer canonical "Rafael Advanced Defense Systems".
        for c in candidates:
            if not c:
                continue
            if any(c in k or k in c for k in known):
                return True
    return False


def score_entity(
    *,
    kind: str,
    mention_count: int,
    in_scope_mentions: int,
    is_watchlist: bool,
) -> float:
    """Pure 0..1 relevance score. See module docstring for the formula."""
    if is_watchlist:
        return 1.0
    if mention_count <= 0:
        return 0.0

    in_scope_fraction = min(1.0, max(0.0, in_scope_mentions / mention_count))
    mention_component = min(1.0, math.log2(mention_count + 1) / _MENTION_SCALE)
    base = _IN_SCOPE_WEIGHT * in_scope_fraction + _MENTION_WEIGHT * mention_component

    # person / country / anything unexpected: a single passing mention is the classic
    # false-positive shape (F15's "Zipline" case); repeated mentions of the same
    # person/country are more likely a genuine recurring subject.
    multiplier = 1.0 if kind in _KEEP_KINDS else (0.35 if mention_count <= 1 else 0.7)

    return round(min(1.0, base * multiplier), 4)


def _entity_mention_stats(name: str) -> tuple[int, int]:
    """`(mention_count, in_scope_mentions)` for `name` across `items.entities_mentioned`."""
    from eoa.db import connection

    with connection() as conn:
        rows = conn.execute(
            "SELECT domain, level FROM items WHERE %s = ANY(COALESCE(entities_mentioned, '{}'))",
            (name,),
        ).fetchall()
    mention_count = len(rows)
    in_scope = sum(
        1 for r in rows if r.get("domain") != "out_of_scope" and (r.get("level") in _IN_SCOPE_LEVELS)
    )
    return mention_count, in_scope


def score_and_persist_entity(name: str) -> float | None:
    """Recompute and persist relevance/is_watchlist for the entity named `name`.

    Returns the new score, or ``None`` if no `entities` row with that name exists.
    Never raises: DB/config errors are logged and swallowed, matching every other
    best-effort write in `eoa.pipeline.analyze` (a relevance-scoring failure must never
    break analysis of the item that triggered it).
    """
    try:
        from eoa.db import connection

        with connection() as conn:
            row = conn.execute("SELECT id, kind, aliases FROM entities WHERE name = %s", (name,)).fetchone()
        if row is None:
            return None
        mention_count, in_scope = _entity_mention_stats(name)
        watchlist = is_watchlist_match(name, row.get("aliases"))
        score = score_entity(
            kind=row.get("kind") or "company",
            mention_count=mention_count,
            in_scope_mentions=in_scope,
            is_watchlist=watchlist,
        )
        with connection() as conn:
            conn.execute(
                "UPDATE entities SET relevance = %s, is_watchlist = %s WHERE id = %s",
                (score, watchlist, row["id"]),
            )
        return score
    except Exception as exc:
        log.warning("entity_relevance_failed", name=name, error=str(exc)[:160])
        return None


def compute_relevance_row(
    row: dict[str, Any], mention_count: int, in_scope_mentions: int
) -> tuple[float, bool]:
    """Same computation as `score_and_persist_entity`, but for a pre-fetched `entities` row
    and pre-aggregated mention stats -- used by `scripts/repair_entity_relevance.py` so a
    full-table backfill does one aggregate query instead of one query per entity."""
    watchlist = is_watchlist_match(row["name"], row.get("aliases"))
    score = score_entity(
        kind=row.get("kind") or "company",
        mention_count=mention_count,
        in_scope_mentions=in_scope_mentions,
        is_watchlist=watchlist,
    )
    return score, watchlist
