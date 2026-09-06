"""Entity relevance scoring (F15): keeps noise entities out of the Entities & Graph screen
without ever deleting them (CONVENTIONS.md rule 4 -- never invent, never destroy provenance).

Problem this solves (docs/REVIEW_2026-09-05.md F15): NER in `eoa.pipeline.classify` has no
domain filter, so a name mentioned once in an out-of-scope, low-level item -- e.g. "Zipline"
(a delivery-drone company) name-dropped in a Houston-highway news story -- enters `entities`
with the same visual weight as a tracked watchlist company like "Elbit". This module scores
every entity 0..1 from four *evidence sources* and persists the score to `entities.relevance`
(migration 0007) so the Entities list (U10) can default to `relevance >= 0.4` with a "show
all" toggle, instead of showing every NER hit undifferentiated.

2026-09-06 rework (docs/REVIEW_2026-09-05.md follow-up): the original version scored purely
from `items.entities_mentioned`, but that array is only populated on 32 of 353 items (an
earlier classify.py gap, since fixed going forward), so 271/408 entities scored 0 and were
hidden -- including entities that are obviously in-scope by every *other* signal already in
the DB: "US Navy" (org, 8 `graph_edges`), "Air Force" (5 edges + an event), etc. This version
adds two more evidence sources the score was previously blind to:

  - **Graph degree** (`graph_edges`, migration 0006): how many edges name this entity as
    src or dst, plus whether the items those edges were extracted from are in-scope.
  - **Events** (`events.parties`/`customer`/`program`): whether this entity is a named party,
    the customer, or appears in the program text of a contract/investment/partnership/etc.
    event, plus whether that event's item is in-scope.

Score composition (`score_entity`, a pure function -- no I/O, fully unit-testable):
  1. Watchlist match (name or alias, case-insensitive, against `config/watchlist.yaml`
     companies + programs) -> always 1.0, checked first. A tracked entity is relevant
     regardless of how little it has been mentioned or how it happened to be classified.
  2. News-source / media-outlet names (`is_news_source`: a static list of common defense
     trade press -- "The War Zone", "Breaking Defense", "Reuters", ... -- plus a fuzzy match
     against the `sources` table this install actually polls) -> always 0.1, checked second
     (below watchlist, since a media outlet is never itself tracked). These are correctly
     *low* relevance -- they are where an item came from, not a market participant -- but
     they are legitimate NER hits (a story bylined/attributed to "The War Zone"), so they
     stay visible-on-request rather than being silently dropped.
  3. Countries (kind resolved to `'country'` -- see `resolve_country_kind`): a flat two-tier
     score, since a country is a buyer/market, not a tracked player, and its relevance to
     *this* analyst's mandate is binary-ish rather than a smooth function of mention volume:
     0.45 if it has at least one graph edge or event (it showed up as an actual party to
     something), else 0.2 (name-dropped only, e.g. a "made in France" aside).
  4. Otherwise, a weighted combination of:
     - `mention_component` (weight 0.25): `mention_count` (from `items.entities_mentioned`,
       matched against the entity's name *or* any alias) log-scaled so 1 mention -> ~0.32,
       8+ mentions -> 1.0.
     - `edge_component` (weight 0.25): `edge_count` (`graph_edges` rows where the entity is
       src or dst) log-scaled the same way -- 8+ edges -> 1.0.
     - `event_component` (weight 0.20): `event_count` (`events` rows naming the entity as a
       party/customer/program) log-scaled to saturate faster (events are rarer and a
       stronger signal than a passing mention) -- 3+ events -> 1.0.
     - `in_scope_fraction` (weight 0.30): across the *union* of every item backing any of
       the three evidence sources above (deduplicated by item id), the fraction that are
       in-scope (`items.domain != 'out_of_scope'`) AND triaged at red/orange/yellow (not
       `archive`).
     A `kind_multiplier` then applies: `company`/`program`/`system`/`org` keep the combined
     score as-is; anything else (`person`, or an unexpected kind) is downweighted to 0.35x
     when *all* evidence together amounts to one hit or fewer, else 0.7x -- a name that keeps
     recurring is more likely a genuine recurring subject than a passing name-drop (the F15
     "Zipline"/"Eric Trump" shape).
     Finally, a **kind floor**: `org`/`program`/`system` entities (this DB's `org` kind
     covers agencies and military branches -- "US Navy", "Air Force", "DoD" -- since
     `entities_kind_check` has no separate `agency`/`military` value) with at least one edge
     or event are floored at 0.5 -- an institutional/organizational actor that participates
     in *any* tracked relationship or transaction is inherently in-scope, never a passing
     mention to be hidden by a low mention count.

`score_and_persist_entity(name)` does the DB I/O: looks up the entity row, aggregates all
four evidence sources, resolves country kind, calls `score_entity`, and writes
`relevance`/`is_watchlist`/`kind` back onto the `entities` row. It is called from
`eoa.pipeline.analyze.run_analyze` (one call per newly-analyzed item's `entities_mentioned`,
guarded so a failure here never breaks analysis) and from
`scripts/repair_entity_relevance.py` (one-off backfill over every existing row, via
`compute_relevance_row` on pre-aggregated evidence so the backfill does a handful of
aggregate queries total, not one (or four) per entity).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import structlog

from eoa.config import settings

log = structlog.get_logger(__name__)

RELEVANCE_THRESHOLD = 0.4

# kinds that are never downweighted by the "weak/generic evidence" multiplier below.
_KEEP_KINDS = {"company", "program", "system", "org"}
# kinds that get the institutional-actor floor (0.5) once they have >=1 edge/event.
# `org` is this DB's only kind covering agencies and military branches
# (`entities_kind_check` has no separate 'agency'/'military' value -- see migration 0007).
_AGENCY_FLOOR_KINDS = {"org", "program", "system"}
_AGENCY_FLOOR = 0.5

_IN_SCOPE_LEVELS = {"red", "orange", "yellow"}

_IN_SCOPE_WEIGHT = 0.30
_MENTION_WEIGHT = 0.25
_EDGE_WEIGHT = 0.25
_EVENT_WEIGHT = 0.20

_MENTION_SCALE = math.log2(9)  # 8 mentions -> mention_component == 1.0
_EDGE_SCALE = math.log2(9)  # 8 edges -> edge_component == 1.0
_EVENT_SCALE = math.log2(4)  # 3 events -> event_component == 1.0

_NEWS_SOURCE_SCORE = 0.1
_COUNTRY_WITH_EVIDENCE_SCORE = 0.45
_COUNTRY_BARE_SCORE = 0.2

# -- country detection (kind fix-up) -----------------------------------------------------
# A deliberately simple, static list (no `pycountry` dependency in this project's venv) --
# good enough to catch the F15 shape ("Japan"/"Germany"/"Israel" typed as 'company' by the
# LLM's NER heuristic) without pulling in a new package for ~60 names. Known limitation:
# a handful of these overlap with common given names or US states (e.g. "Georgia", "Jordan")
# -- see `resolve_country_kind`'s guard (only promotes from 'company'/'org', never 'person').
_COUNTRY_NAMES = {
    "united states",
    "usa",
    "u.s.",
    "u.s.a.",
    "united states of america",
    "united kingdom",
    "uk",
    "britain",
    "great britain",
    "israel",
    "japan",
    "germany",
    "france",
    "italy",
    "spain",
    "portugal",
    "poland",
    "ukraine",
    "russia",
    "china",
    "india",
    "south korea",
    "north korea",
    "turkey",
    "türkiye",
    "canada",
    "australia",
    "new zealand",
    "brazil",
    "mexico",
    "egypt",
    "saudi arabia",
    "united arab emirates",
    "uae",
    "qatar",
    "jordan",
    "lebanon",
    "syria",
    "iran",
    "iraq",
    "pakistan",
    "taiwan",
    "vietnam",
    "philippines",
    "indonesia",
    "singapore",
    "thailand",
    "malaysia",
    "netherlands",
    "belgium",
    "norway",
    "sweden",
    "finland",
    "denmark",
    "switzerland",
    "austria",
    "greece",
    "czech republic",
    "czechia",
    "slovakia",
    "hungary",
    "romania",
    "bulgaria",
    "croatia",
    "serbia",
    "estonia",
    "latvia",
    "lithuania",
    "south africa",
    "nigeria",
    "kenya",
    "colombia",
    "chile",
    "argentina",
    "peru",
    "morocco",
    "algeria",
    "tunisia",
    "libya",
    "yemen",
    "bahrain",
    "kuwait",
    "oman",
    "azerbaijan",
    "georgia",
    "armenia",
    "kazakhstan",
    # Hebrew
    "ישראל",
    'ארה"ב',
    "ארצות הברית",
    "יפן",
    "גרמניה",
    "צרפת",
    "בריטניה",
    "סין",
    "רוסיה",
    "הודו",
    "טורקיה",
    "קנדה",
    "מצרים",
    "ירדן",
}

# -- news-source / media-outlet detection ------------------------------------------------
# Common defense trade press and general wire services -- entities that legitimately show
# up in NER (a story attributed to, or quoting, an outlet) but are never a market
# participant. Kept short and curated (substring-matched both ways) rather than exhaustive;
# the `sources` table heuristic below catches anything this install actually polls that
# isn't on the static list.
_NEWS_SOURCE_NAMES = {
    "the war zone",
    "twz",
    "breaking defense",
    "defense news",
    "defensenews",
    "jane's",
    "janes",
    "jane's defence weekly",
    "reuters",
    "associated press",
    "ap news",
    "c4isrnet",
    "defense one",
    "aviation week",
    "aviationweek",
    "army technology",
    "naval technology",
    "naval news",
    "military times",
    "the drive",
    "bloomberg",
    "the wall street journal",
    "the new york times",
    "washington post",
    "politico",
    "axios",
    "cnn",
    "bbc",
    "afp",
    "flightglobal",
    "spacenews",
    "the diplomat",
    "unmanned systems technology",
    "shephard media",
    "dronexl",
    "defense update",
    "israel defense",
    "dvids",
    # NOTE: deliberately NOT included: generic military-branch-shaped outlet names like
    # "Air Force Magazine" or "Army Times" -- their substring overlap with legitimate org
    # entities ("Air Force", "US Army") is a worse failure than missing the odd outlet hit.
}


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
            for k in known:
                if not k:
                    continue
                # Whole-word containment only, and never for very short tokens: a bare "AI"
                # must not match "Israel Aerospace Industries" (observed live 2026-09-06).
                shorter, longer = (c, k) if len(c) <= len(k) else (k, c)
                if len(shorter) < 4:
                    continue
                if re.search(r"(?<!\w)" + re.escape(shorter) + r"(?!\w)", longer):
                    return True
    return False


def _normalize_source_name(name: str) -> str:
    """Strip a trailing parenthetical (`"The War Zone (TWZ)"`) and any `" - suffix"`
    (`"Anduril - News"`, `"Thales Group - Press Releases"`) so a source's feed/page title
    reduces to (roughly) the outlet's own name for comparison against an entity name."""
    n = (name or "").strip().lower()
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n)
    n = re.split(r"\s+-\s+", n)[0]
    return n.strip()


def is_news_source(name: str, source_names: Iterable[str] | None = None) -> bool:
    """True if `name` looks like a news outlet / media source rather than a market participant.

    Checked against a small curated list of common defense trade press (substring match
    both ways -- the list is short and controlled, so this is low-risk), and, if
    `source_names` (this install's `sources.name` values) is given, an *exact* match after
    normalizing both sides (`_normalize_source_name`) -- exact rather than substring, since
    a substring match against arbitrary polled source names would false-positive even more
    often (e.g. a short entity name appearing inside an unrelated longer source title).

    Known limitation: a watchlist company that publishes its own newsroom feed (e.g. source
    "Saab - Newsroom Press Releases") normalizes down to the bare company name, so this
    function alone *can* return True for "Saab". That never mis-scores the real entity in
    practice because `score_entity` checks `is_watchlist` first and returns before
    `is_news_source` is even considered -- this function's result only matters for entities
    that are *not* on the watchlist.
    """
    n = _normalize_source_name(name)
    if not n:
        return False
    if n in _NEWS_SOURCE_NAMES:
        return True
    for candidate in _NEWS_SOURCE_NAMES:
        if n in candidate or candidate in n:
            return True
    if source_names:
        for s in source_names:
            if n == _normalize_source_name(s):
                return True
    return False


def resolve_country_kind(name: str, current_kind: str | None) -> str:
    """Return `'country'` if `name` is a recognized country name and `current_kind` is one
    of the LLM's generic fallback kinds (`'company'`/`'org'`) -- never overrides a kind the
    classifier was more specific about (`'person'`, `'system'`, `'program'`), which limits
    the blast radius of the static list's known ambiguities (e.g. "Jordan" as a person name,
    "Georgia" as a US state)."""
    kind = current_kind or "company"
    if kind in ("company", "org") and (name or "").strip().lower() in _COUNTRY_NAMES:
        return "country"
    return kind


@dataclass
class EntityEvidence:
    """Pre-aggregated evidence for one entity, in the shape `score_entity` consumes.

    `total_evidence_count`/`in_scope_evidence_count` are computed over the *deduplicated
    union* of item ids backing mentions, edges, and events -- an item that both mentions the
    entity and backs one of its graph edges counts once, not twice, in the in-scope fraction.
    """

    mention_count: int = 0
    edge_count: int = 0
    event_count: int = 0
    in_scope_evidence_count: int = 0
    total_evidence_count: int = 0
    evidence_item_ids: set[int] = field(default_factory=set)


def score_entity(
    *,
    kind: str,
    mention_count: int,
    edge_count: int,
    event_count: int,
    in_scope_evidence_count: int,
    total_evidence_count: int,
    is_watchlist: bool,
    is_news_source: bool = False,
) -> float:
    """Pure 0..1 relevance score. See module docstring for the formula."""
    if is_watchlist:
        return 1.0
    if is_news_source:
        return _NEWS_SOURCE_SCORE
    if kind == "country":
        return _COUNTRY_WITH_EVIDENCE_SCORE if (edge_count > 0 or event_count > 0) else _COUNTRY_BARE_SCORE
    if mention_count <= 0 and edge_count <= 0 and event_count <= 0:
        return 0.0

    in_scope_fraction = (
        min(1.0, max(0.0, in_scope_evidence_count / total_evidence_count))
        if total_evidence_count > 0
        else 0.0
    )
    mention_component = min(1.0, math.log2(max(0, mention_count) + 1) / _MENTION_SCALE)
    edge_component = min(1.0, math.log2(max(0, edge_count) + 1) / _EDGE_SCALE)
    event_component = min(1.0, math.log2(max(0, event_count) + 1) / _EVENT_SCALE)

    base = (
        _IN_SCOPE_WEIGHT * in_scope_fraction
        + _MENTION_WEIGHT * mention_component
        + _EDGE_WEIGHT * edge_component
        + _EVENT_WEIGHT * event_component
    )

    # person / anything unexpected: a single passing hit (mention, edge, or event) is the
    # classic false-positive shape (F15's "Zipline"/"Eric Trump" cases); recurring evidence
    # for the same person/unknown-kind entity is more likely a genuine tracked subject.
    total_hits = mention_count + edge_count + event_count
    weak_evidence = total_hits <= 1
    multiplier = 1.0 if kind in _KEEP_KINDS else (0.35 if weak_evidence else 0.7)

    score = base * multiplier

    if kind in _AGENCY_FLOOR_KINDS and (edge_count > 0 or event_count > 0):
        score = max(score, _AGENCY_FLOOR)

    return round(min(1.0, max(0.0, score)), 4)


def _rows_to_item_map(rows: list[dict[str, Any]], id_key: str = "id") -> dict[int, dict[str, Any]]:
    return {r[id_key]: r for r in rows}


def _is_in_scope(item: dict[str, Any] | None) -> bool:
    if item is None:
        return False
    return item.get("domain") != "out_of_scope" and item.get("level") in _IN_SCOPE_LEVELS


def _build_evidence(
    mention_items: list[dict[str, Any]],
    edge_items: list[dict[str, Any]],
    event_items: list[dict[str, Any]],
) -> EntityEvidence:
    """Combine per-source item lists (each `{"id"/"item_id", "domain", "level"}`) into one
    `EntityEvidence`, deduplicating the in-scope fraction's denominator across all three."""
    all_ids: set[int] = set()
    in_scope_ids: set[int] = set()
    for row in mention_items:
        iid = row.get("id")
        if iid is None:
            continue
        all_ids.add(iid)
        if _is_in_scope(row):
            in_scope_ids.add(iid)
    for row in edge_items:
        iid = row.get("item_id")
        if iid is None:
            continue
        all_ids.add(iid)
        if _is_in_scope(row):
            in_scope_ids.add(iid)
    for row in event_items:
        iid = row.get("item_id")
        if iid is None:
            continue
        all_ids.add(iid)
        if _is_in_scope(row):
            in_scope_ids.add(iid)

    return EntityEvidence(
        mention_count=len(mention_items),
        edge_count=len(edge_items),
        event_count=len(event_items),
        in_scope_evidence_count=len(in_scope_ids),
        total_evidence_count=len(all_ids),
        evidence_item_ids=all_ids,
    )


def _entity_evidence(entity_id: int, name: str, aliases: list[str] | None) -> EntityEvidence:
    """Gather all three evidence sources for one entity, doing DB I/O (used by
    `score_and_persist_entity`; `scripts/repair_entity_relevance.py` builds `EntityEvidence`
    itself from bulk-fetched tables instead, to avoid four queries per entity)."""
    from eoa.db import connection

    name_variants = [name, *(aliases or [])]
    with connection() as conn:
        mention_items = conn.execute(
            "SELECT id, domain, level FROM items WHERE entities_mentioned && %s::text[]",
            (name_variants,),
        ).fetchall()
        edge_items = conn.execute(
            """
            SELECT ge.item_id AS item_id, i.domain AS domain, i.level AS level
            FROM graph_edges ge
            JOIN items i ON i.id = ge.item_id
            WHERE ge.src_entity_id = %(eid)s OR ge.dst_entity_id = %(eid)s
            """,
            {"eid": entity_id},
        ).fetchall()
        event_items = conn.execute(
            """
            SELECT e.item_id AS item_id, i.domain AS domain, i.level AS level
            FROM events e
            JOIN items i ON i.id = e.item_id
            WHERE %(name)s = ANY(COALESCE(e.parties, '{}'))
               OR e.customer = %(name)s
               OR e.program ILIKE %(pat)s
            """,
            {"name": name, "pat": f"%{name}%"},
        ).fetchall()
    return _build_evidence(mention_items, edge_items, event_items)


def _fetch_source_names() -> list[str]:
    from eoa.db import connection

    with connection() as conn:
        rows = conn.execute("SELECT name FROM sources").fetchall()
    return [r["name"] for r in rows if r.get("name")]


def score_and_persist_entity(name: str) -> float | None:
    """Recompute and persist relevance/is_watchlist/kind for the entity named `name`.

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

        evidence = _entity_evidence(row["id"], name, row.get("aliases"))
        watchlist = is_watchlist_match(name, row.get("aliases"))
        resolved_kind = resolve_country_kind(name, row.get("kind"))
        news = is_news_source(name, _fetch_source_names())
        score = score_entity(
            kind=resolved_kind,
            mention_count=evidence.mention_count,
            edge_count=evidence.edge_count,
            event_count=evidence.event_count,
            in_scope_evidence_count=evidence.in_scope_evidence_count,
            total_evidence_count=evidence.total_evidence_count,
            is_watchlist=watchlist,
            is_news_source=news,
        )
        with connection() as conn:
            conn.execute(
                "UPDATE entities SET relevance = %s, is_watchlist = %s, kind = %s WHERE id = %s",
                (score, watchlist, resolved_kind, row["id"]),
            )
        return score
    except Exception as exc:
        log.warning("entity_relevance_failed", name=name, error=str(exc)[:160])
        return None


def compute_relevance_row(
    row: dict[str, Any],
    evidence: EntityEvidence,
    *,
    is_news_source: bool = False,
) -> tuple[float, bool, str]:
    """Same computation as `score_and_persist_entity`, but for a pre-fetched `entities` row
    and a pre-aggregated `EntityEvidence` -- used by `scripts/repair_entity_relevance.py` so
    a full-table backfill does a handful of bulk queries total instead of four per entity.

    Returns `(score, is_watchlist, resolved_kind)` -- the caller persists all three
    (`resolved_kind` may differ from `row["kind"]` when a country was mis-typed as
    `'company'`/`'org'`, see `resolve_country_kind`).
    """
    watchlist = is_watchlist_match(row["name"], row.get("aliases"))
    resolved_kind = resolve_country_kind(row["name"], row.get("kind"))
    score = score_entity(
        kind=resolved_kind,
        mention_count=evidence.mention_count,
        edge_count=evidence.edge_count,
        event_count=evidence.event_count,
        in_scope_evidence_count=evidence.in_scope_evidence_count,
        total_evidence_count=evidence.total_evidence_count,
        is_watchlist=watchlist,
        is_news_source=is_news_source,
    )
    return score, watchlist, resolved_kind
