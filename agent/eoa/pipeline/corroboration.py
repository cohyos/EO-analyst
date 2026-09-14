"""Stage: cross-source corroboration (2026-09-07 user requirement, verbatim intent).

When an item is brought in, determine whether other independent sources corroborate it or it is
a single-source report, and surface that everywhere the item appears. This module is the entire
deterministic core (no LLM, no network call) -- it runs as the ``corroborate`` pipeline stage,
right after ``analyze`` (see ``eoa.orchestrator.jobs``), for every in-scope item (``level`` in
red/orange/yellow).

Algorithm (design doc, 2026-09-07)
-----------------------------------
For item ``I``, candidates are other items ``J`` published within +/-7 days of ``I`` whose *own*
registrable domain differs from ``I``'s (two articles from the same outlet never corroborate each
other -- :func:`registrable_domain`). Any of the following makes ``J`` a corroborating source:

  a) **duplicate** -- ``J`` and ``I`` are dedup-linked (``dedup_of`` either direction, or shared
     canonical) -- see ``eoa.memory.relational.get_dedup_linked_item_ids``.
  b) **same_event** -- ``J`` shares >= 2 *distinctive* entities with ``I`` (a small generic
     stoplist -- "US Army", "DoD", "Israel", "IDF" and the like -- doesn't count *unless* a third
     shared entity backs it up, :func:`distinctive_shared_entities`), the two items share a
     taxonomy ``domain``, and at least one of ``J``'s own ``events`` rows matches one of ``I``'s
     on ``kind`` + (``customer`` or ``program`` or ``amount_usd`` within 10%) -- :func:`_events_match`.
  c) **official** -- ``I``'s *own* source is itself an official/primary outlet
     (:func:`is_official_primary_source`) -- this alone (without any ``J``) sets
     ``status='official_primary'`` even when no corroborating item exists, because "does a primary
     release need corroboration" isn't the question a single-source flag is trying to answer.

Status: ``corroborated`` when >= 1 duplicate/same_event source was found (regardless of whether
``I`` is also a primary outlet -- corroboration outranks primary-ness once it exists), else
``official_primary`` if (c), else ``single_source``. ``unknown`` only when ``I`` itself has no
``published_at`` or no ``domain`` to key the check on at all.

Deviation from the design doc, documented here per the task brief's instruction to flag any: the
brief says to derive the "official/primary" set by inspecting ``sources.kind`` -- but that column
(``db/migrations/versions/0001_core.py``) only ever holds a *fetch-mechanism* value (rss/html/api/
search), never a trust/officialness classification, and no such classification exists anywhere
else in the schema or ``config/sources.yaml``. :func:`is_official_primary_source` instead applies
a URL-hostname/path heuristic against the item's own ``url`` -- gov/mil/EU-institution TLDs, the
concrete portals the brief names (SAM.gov, TED), and known primary company-wire distribution
hosts (PR Newswire/Business Wire/GlobeNewswire) plus an ``ir./investors.`` + "press release" path
heuristic for a company's own investor-relations pages. This is intentionally conservative (a
generic defense-trade outlet that happens to *carry* a press release is never treated as
"official" by this heuristic) rather than exhaustive.

The optional LLM/web-corroboration probe mentioned in the design doc as an extension point was
not implemented -- the deterministic core above fully satisfies the stated requirement without a
network or LLM call, and this task's file scope does not include ``config/config.yaml``/
``eoa.config`` (where its ``corroboration.web_probe`` flag would need to live).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import structlog

from eoa.errors import DeadlineExceeded, LeaseLost
from eoa.execution import checkpoint
from eoa.memory.relational import (
    get_corroboration_candidates,
    get_dedup_linked_item_ids,
    get_events_for_item,
    get_item_corroboration_map,
    get_item_for_corroboration,
    get_items_for_stage,
    get_recent_in_scope_item_ids,
    mark_stage,
    upsert_item_corroboration,
)

log = structlog.get_logger(__name__)

STAGE = "corroborate"
METHOD = "deterministic_v1"

#: Half-width of the corroboration lookback/lookahead window (design doc: "within +/-7 days").
WINDOW_DAYS = 7

#: A candidate must clear this fraction-of-amount tolerance to count as the "same amount" leg of
#: :func:`_events_match` ("amount_usd within 10%").
AMOUNT_TOLERANCE = 0.10

#: Shared entities that don't by themselves establish "same event" (too generic -- half the
#: defense-news corpus mentions "IDF" or "DoD") -- see :func:`distinctive_shared_entities`.
_GENERIC_ENTITY_STOPLIST = frozenset(
    {
        "us army",
        "us navy",
        "us air force",
        "us marine corps",
        "dod",
        "department of defense",
        "pentagon",
        "israel",
        "idf",
        "imod",
        "nato",
        "mod",
        "ministry of defense",
        "ministry of defence",
    }
)

#: Hostname suffixes treated as an official/primary outlet outright (government portals, EU
#: institutions) -- see the module docstring's "Deviation" note.
_OFFICIAL_HOST_SUFFIXES = (".gov", ".gov.il", ".mil", ".europa.eu")

#: Specific official portals/wire services named in the design doc (SAM.gov, TED) plus the major
#: primary company-press-release distribution wires (a wire release is the company's own primary
#: statement, not third-party reporting of it).
_OFFICIAL_HOSTS_EXACT = frozenset(
    {
        "sam.gov",
        "beta.sam.gov",
        "ted.europa.eu",
        "mod.gov.il",
        "sibat.mod.gov.il",
        "www.sibat.mod.gov.il",
        "prnewswire.com",
        "www.prnewswire.com",
        "businesswire.com",
        "www.businesswire.com",
        "globenewswire.com",
        "www.globenewswire.com",
    }
)

#: A subdomain prefix conventionally used for a company's own investor-relations pages; combined
#: with a press-release path segment this is treated as the company's own primary release.
_IR_HOST_PREFIXES = ("ir.", "investor.", "investors.")
_PRESS_PATH_MARKERS = ("/press-release", "/press-releases", "/newsroom", "/press-room")


@dataclass
class CorroborationStats:
    checked: int = 0
    single_source: int = 0
    corroborated: int = 0
    official_primary: int = 0
    unknown: int = 0
    failed: int = 0


def registrable_domain(url: str | None) -> str | None:
    """Best-effort registrable-domain-ish host for ``url``: the netloc, minus a leading ``www.``
    and any port -- same simplification ``eoa.report.docx_builder._domain_from_url`` already uses
    for "what outlet is this" comparisons. Not full public-suffix-list parsing (a ``co.uk``-style
    second-level TLD is out of scope), but sufficient for "is this a different outlet"."""
    if not url:
        return None
    try:
        netloc = urlparse(url).netloc or url
    except ValueError:
        netloc = url
    netloc = netloc.split("@")[-1].split(":")[0].lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or None


def is_official_primary_source(url: str | None) -> bool:
    """True if ``url`` looks like an official/primary outlet -- see the module docstring's
    "Deviation" note for why this is a URL heuristic rather than a ``sources.kind`` lookup."""
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.netloc or "").split("@")[-1].split(":")[0].lower()
    if not host:
        return False
    if host in _OFFICIAL_HOSTS_EXACT:
        return True
    if any(host.endswith(suffix) for suffix in _OFFICIAL_HOST_SUFFIXES):
        return True
    path = (parsed.path or "").lower()
    return any(host.startswith(prefix) for prefix in _IR_HOST_PREFIXES) and any(
        marker in path for marker in _PRESS_PATH_MARKERS
    )


def distinctive_shared_entities(a: list[str] | None, b: list[str] | None) -> list[str]:
    """Entities shared between two ``entities_mentioned`` lists that count toward the "same event"
    threshold. A generic stoplist entry (see :data:`_GENERIC_ENTITY_STOPLIST`) counts only when it
    is backed up by a third shared entity (>= 3 shared total) -- otherwise two items both merely
    mentioning "IDF" and "DoD" (extremely common, no actual overlap in substance) would falsely
    read as corroborating."""
    shared = sorted(set(a or []) & set(b or []))
    if len(shared) >= 3:
        return shared
    return [e for e in shared if e.strip().lower() not in _GENERIC_ENTITY_STOPLIST]


def _amounts_close(x: Any, y: Any, tolerance: float = AMOUNT_TOLERANCE) -> bool:
    if x is None or y is None:
        return False
    try:
        xf, yf = float(x), float(y)
    except (TypeError, ValueError):
        return False
    if xf == 0 or yf == 0:
        return xf == yf
    return abs(xf - yf) / max(abs(xf), abs(yf)) <= tolerance


def _events_match(ev_a: dict[str, Any], ev_b: dict[str, Any]) -> bool:
    """Same ``kind`` plus at least one of: matching ``customer``, matching ``program``, or
    ``amount_usd`` within :data:`AMOUNT_TOLERANCE` -- design doc's same-event leg."""
    if not ev_a.get("kind") or ev_a.get("kind") != ev_b.get("kind"):
        return False
    a_customer, b_customer = (
        (ev_a.get("customer") or "").strip().lower(),
        (ev_b.get("customer") or "").strip().lower(),
    )
    if a_customer and b_customer and a_customer == b_customer:
        return True
    a_program, b_program = (
        (ev_a.get("program") or "").strip().lower(),
        (ev_b.get("program") or "").strip().lower(),
    )
    if a_program and b_program and a_program == b_program:
        return True
    return _amounts_close(ev_a.get("amount_usd"), ev_b.get("amount_usd"))


def _source_entry(item: dict[str, Any], *, kind: str) -> dict[str, Any]:
    published = item.get("published_at")
    return {
        "item_id": item.get("id"),
        "source_name": item.get("source_name"),
        "url": item.get("url"),
        "published_at": published.isoformat() if isinstance(published, dt.datetime | dt.date) else published,
        "kind": kind,
    }


def compute_for_item(item_id: int) -> dict[str, Any] | None:
    """Compute and persist the corroboration record for one item. Returns the stored record
    (``eoa.memory.relational.get_item_corroboration``'s shape), or ``None`` if the item itself no
    longer exists."""
    item = get_item_for_corroboration(item_id)
    if item is None:
        return None

    own_url = item.get("url") or item.get("source_url")
    is_official = is_official_primary_source(own_url)

    # 2026-09-07 follow-up: 20 of 63 backfilled items were 'unknown' only because their outlet
    # (Globes) publishes no date -- anchor the window on fetched_at/created_at in that case.
    anchor_date = item.get("published_at") or item.get("fetched_at") or item.get("created_at")
    if anchor_date is None or item.get("domain") is None:
        status = "unknown"
        count = 0
        sources: list[dict[str, Any]] = []
    else:
        matches: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        dedup_ids = set(get_dedup_linked_item_ids(item_id, item.get("dedup_of")))

        published_at = anchor_date
        window = dt.timedelta(days=WINDOW_DAYS)
        start, end = published_at - window, published_at + window
        candidates = get_corroboration_candidates(item_id, start, end)

        own_domain_host = registrable_domain(own_url)
        own_entities = item.get("entities_mentioned") or []
        own_events: list[dict[str, Any]] | None = None

        for cand in candidates:
            cand_id = cand["id"]
            if cand_id in seen_ids:
                continue
            cand_url = cand.get("url") or cand.get("source_url")
            cand_host = registrable_domain(cand_url)
            if own_domain_host and cand_host and own_domain_host == cand_host:
                continue  # same outlet never corroborates itself

            if cand_id in dedup_ids:
                matches.append(_source_entry(cand, kind="duplicate"))
                seen_ids.add(cand_id)
                continue

            if item.get("domain") and cand.get("domain") == item.get("domain"):
                shared = distinctive_shared_entities(own_entities, cand.get("entities_mentioned"))
                if len(shared) >= 2:
                    if own_events is None:
                        own_events = get_events_for_item(item_id)
                    if own_events:
                        cand_events = get_events_for_item(cand_id)
                        if any(_events_match(a, b) for a in own_events for b in cand_events):
                            matches.append(_source_entry(cand, kind="same_event"))
                            seen_ids.add(cand_id)

        count = len(matches)
        if count > 0:
            status = "corroborated"
            sources = matches
        elif is_official:
            status = "official_primary"
            sources = []
        else:
            status = "single_source"
            sources = []

    upsert_item_corroboration(item_id=item_id, status=status, count=count, sources=sources, method=METHOD)
    log.info("corroboration.computed", item_id=item_id, status=status, count=count)
    return {
        "item_id": item_id,
        "status": status,
        "count": count,
        "sources": sources,
        "method": METHOD,
        "checked_at": dt.datetime.now(dt.UTC),
    }


def _eligible_level(item: dict[str, Any]) -> bool:
    return item.get("level") in ("red", "orange", "yellow")


def run_corroboration(limit: int = 200, *, item_ids: list[int] | None = None) -> CorroborationStats:
    """Stage entrypoint: compute corroboration for in-scope items that haven't gone through the
    ``corroborate`` stage yet (or, when ``item_ids`` is given -- the per-item flow after
    ``analyze`` -- for just those ids and whatever they link to).

    An item whose ``level`` is archive (or anything other than red/orange/yellow) is stage-marked
    done without a computed record -- it stays ``unknown`` per the API contract. If a later triage
    re-run ever promotes it to an in-scope level, ``level`` is stored back on ``items`` at that
    point, not here, so this stage's own ``mark_stage`` bookkeeping is irrelevant to whether it
    gets reconsidered; the nightly :func:`recheck_recent` sweep (keyed on ``published_at``, not on
    ``processed_stages``) is what actually re-covers items over time regardless of this stage's
    marks.
    """
    checkpoint()
    stats = CorroborationStats()
    to_recompute: set[int] = set()
    for item in get_items_for_stage(STAGE, limit, item_ids=item_ids):
        checkpoint()
        if item.get("level") is None:
            continue  # not triaged yet -- leave for the next pass, do not mark
        if not _eligible_level(item):
            mark_stage(item["id"], STAGE)
            continue
        try:
            record = compute_for_item(item["id"])
        except (DeadlineExceeded, LeaseLost):
            raise
        except Exception as exc:  # a single bad item must never abort the whole batch
            log.warning("corroboration_item_failed", item_id=item["id"], error=str(exc)[:200])
            stats.failed += 1
            continue
        mark_stage(item["id"], STAGE)
        stats.checked += 1
        if record is not None:
            setattr(stats, record["status"], getattr(stats, record["status"]) + 1)
            to_recompute.update(s["item_id"] for s in record["sources"] if s.get("item_id") is not None)

    # design doc point 3: "run corroboration for it and for the items it links to, so both sides
    # update" -- one hop, not a recursive expansion (avoids an unbounded chain reaction).
    for linked_id in to_recompute:
        checkpoint()
        try:
            compute_for_item(linked_id)
        except (DeadlineExceeded, LeaseLost):
            raise
        except Exception as exc:
            log.warning("corroboration_linked_item_failed", item_id=linked_id, error=str(exc)[:200])

    return stats


def recheck_recent(days: int = WINDOW_DAYS, limit: int = 500) -> CorroborationStats:
    """Nightly re-check (design doc point 3: "corroboration arrives late") -- re-runs
    :func:`compute_for_item` for every in-scope item from the last ``days`` days, regardless of
    whether the ``corroborate`` stage already marked it done, since a corroborating second article
    can be ingested well after the original."""
    checkpoint()
    stats = CorroborationStats()
    ids = get_recent_in_scope_item_ids(days)[:limit]
    for item_id in ids:
        checkpoint()
        try:
            record = compute_for_item(item_id)
        except (DeadlineExceeded, LeaseLost):
            raise
        except Exception as exc:
            log.warning("corroboration_recheck_failed", item_id=item_id, error=str(exc)[:200])
            stats.failed += 1
            continue
        stats.checked += 1
        if record is not None:
            setattr(stats, record["status"], getattr(stats, record["status"]) + 1)
    return stats


def corroboration_payload(item_id: int) -> dict[str, Any]:
    """The API contract's ``corroboration`` object for one item -- ``unknown``/0/[]/``None`` when
    never checked. Thin wrapper over :func:`corroboration_payload_map` for a single id."""
    return corroboration_payload_map([item_id]).get(
        item_id, {"status": "unknown", "count": 0, "sources": [], "checked_at": None}
    )


#: Every caller of :func:`corroboration_payload_map` (the API item list/detail endpoints,
#: the daily/weekly report markers) is a best-effort *read* on an already-rendered page -- it must
#: degrade to "no markers" quickly rather than block on the connection pool's much longer default
#: wait if the DB is briefly unreachable. `compute_for_item`/`run_corroboration`/`recheck_recent`
#: (the actual pipeline write path) deliberately do NOT use this timeout -- a nightly batch job
#: waiting longer for a real connection is an acceptable, even correct, trade-off there.
PAYLOAD_LOOKUP_TIMEOUT_SECONDS = 0.3


def corroboration_payload_map(item_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Bulk form of :func:`corroboration_payload` -- one row per id that *was* checked; the caller
    (``eoa.api.services``) fills in the ``unknown`` default for every id absent from the result."""
    rows = get_item_corroboration_map(item_ids, timeout=PAYLOAD_LOOKUP_TIMEOUT_SECONDS)
    out: dict[int, dict[str, Any]] = {}
    for item_id, row in rows.items():
        checked_at = row.get("checked_at")
        out[item_id] = {
            "status": row.get("status") or "unknown",
            "count": row.get("count") or 0,
            "sources": row.get("sources") or [],
            "checked_at": checked_at.isoformat() if isinstance(checked_at, dt.datetime) else checked_at,
        }
    return out
