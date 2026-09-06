"""Q3-13 (docs/qa/findings_Q3_r1.md): entity name/kind normalisation and watchlist alias
resolution, shared by:

- ``eoa.memory.relational.upsert_entity`` -- applied to every entity write going forward
  (classify's extracted entities, analyze's graph-edge endpoints, entity_relevance's kind
  reclassification, ...), so a duplicate spelling/case/alias of a watchlist company always lands
  on the same canonical ``entities`` row instead of a near-duplicate one.
- ``scripts/repair_entities_normalize.py`` -- the one-off backfill over every existing row.
- ``eoa.pipeline.analyze`` (Q3-8) -- ``find_watchlist_aliases_in_text`` deterministically fills
  ``items.entities_mentioned`` when the LLM returned an empty list but the item text plainly
  names a watchlist company/program.

Nothing here talks to the database; it is pure text/config lookups over ``config/watchlist.yaml``
(via ``eoa.config.settings()``), safe to import from anywhere (including at module scope) without
a live DB connection.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from eoa.config import settings

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# The `entities` table's actual CHECK constraint -- the only kinds that can ever be written.
# migration 0001_core.py originally allowed company/program/system/person/org only; migration
# 0007_entity_relevance.py later widened it to also allow "country" (matching
# `ClassifyOut.EntityMention.kind`, which had allowed "country" as a schema value since it was
# introduced, before the DB constraint caught up) -- verified live against the constraint
# (`pg_get_constraintdef` on `entities_kind_check`).
VALID_ENTITY_KINDS = frozenset({"company", "program", "system", "person", "org", "country"})

_SYSTEM_DESIGNATION_KEYWORDS = frozenset(
    {"locust", "smash", "anvil", "roadrunner", "titan", "leonidas", "hivemind"}
)
_GOVERNMENT_KEYWORDS = (
    "air force", "army", "navy", "ministry", "department", "command", "agency", "nato",
    "dod", "government", "govt", "ministry of defense", "ministry of defence",
)  # fmt: skip

# Technique/algorithm-like names an LLM sometimes extracts as if they were an "entity" -- these
# describe a method, not a company/program/system/person/org, and should never be stored.
_TECHNIQUE_LIKE_RE = re.compile(
    r"\b("
    r"image captioning|object detection|vehicle detector[s]?|edge detection|"
    r"feature extraction|semantic segmentation|pose estimation|super[- ]resolution|"
    r"anomaly detection|change detection|target tracking algorithm"
    r")\b",
    re.IGNORECASE,
)


def normalize_name_key(name: str) -> str:
    """Case-insensitive, punctuation-stripped comparison key for an entity name/alias --
    ``'Elbit Systems UK'`` and ``"Elbit-Systems  uk"`` both normalize to the same key."""
    if not name:
        return ""
    stripped = _PUNCT_RE.sub(" ", name)
    collapsed = _WS_RE.sub(" ", stripped).strip()
    return collapsed.casefold()


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, dict[str, Any]]:
    """``{normalize_name_key(surface) -> canonical record}`` for every watchlist company/program,
    keyed by both its canonical name and every alias. ``lru_cache`` is safe here: ``watchlist.yaml``
    is loaded once per process via ``eoa.config.settings()`` (itself ``lru_cache``d); tests that
    monkeypatch the watchlist should also clear this cache (``_alias_index.cache_clear()``)."""
    wl = settings().watchlist
    index: dict[str, dict[str, Any]] = {}
    for kind, records in (("company", wl.get("companies", []) or []), ("program", wl.get("programs", []) or [])):
        for rec in records:
            canonical = {
                "name": rec.get("name", ""),
                "kind": kind,
                "country": rec.get("country"),
                "aliases": list(rec.get("aliases") or []),
                "focus": list(rec.get("focus") or []),
            }
            for surface in [rec.get("name", ""), *canonical["aliases"]]:
                key = normalize_name_key(surface)
                if key:
                    index[key] = canonical
    return index


@lru_cache(maxsize=1)
def _surface_to_canonical_name() -> list[tuple[str, str]]:
    """``[(surface string, canonical name), ...]`` for regex text search (Q3-8), longest surface
    first so e.g. "Elbit Systems" is tried before a shorter alias substring of it would be."""
    pairs: list[tuple[str, str]] = []
    seen_surfaces: set[str] = set()
    for record in _alias_index().values():
        for surface in [record["name"], *record["aliases"]]:
            if surface and surface not in seen_surfaces:
                pairs.append((surface, record["name"]))
                seen_surfaces.add(surface)
    return sorted(pairs, key=lambda p: -len(p[0]))


def resolve_canonical(name: str) -> dict[str, Any] | None:
    """The watchlist canonical record (name/kind/country/aliases/focus) for ``name`` if it (or a
    recorded alias of it) is on the watchlist -- else ``None``."""
    if not name:
        return None
    return _alias_index().get(normalize_name_key(name))


def is_technique_like(name: str) -> bool:
    """True for a method/technique/algorithm name (not a real named entity) that should be
    rejected rather than stored -- e.g. "image captioning", "RF-DETR vehicle detectors"."""
    return bool(_TECHNIQUE_LIKE_RE.search(name or ""))


def normalize_kind(name: str, kind: str) -> str:
    """Map a raw ``kind`` onto one of the ``entities`` table's actually-allowed values
    (:data:`VALID_ENTITY_KINDS`, which -- since migration 0007 -- does include "country"). A
    watchlist-recognised name uses the watchlist's own kind unless the caller already supplied a
    more specific valid kind ("system"/"program"); otherwise: known weapon/system designations ->
    "system", government/military bodies -> "org" (even when the caller passed "country" for one
    -- "US Air Force" is a government body, not a country), anything else already valid
    (including a genuine "country" like "Israel") is kept, anything unknown defaults to "org"
    (never silently drops the row).

    A known system/weapon designation (e.g. "LOCUST", "SMASH") wins even when that same string
    also happens to be listed as a watchlist company's alias (watchlist aliases mix true
    alternate-spellings of the company itself with the company's own product/program names, used
    there purely for search-relevance matching) -- the *name itself* denotes a system, not the
    company, so it must not be typed (or, in :func:`canonical_name_and_kind`, renamed) as if it
    were just another spelling of the company."""
    if _is_system_designation(name):
        return "system"
    canonical = resolve_canonical(name)
    if canonical and canonical["kind"] in VALID_ENTITY_KINDS:
        if kind in ("system", "program"):
            return kind
        return canonical["kind"]
    if any(kw in (name or "").lower() for kw in _GOVERNMENT_KEYWORDS):
        return "org"
    if kind in VALID_ENTITY_KINDS:
        return kind
    return "org"


def _is_system_designation(name: str) -> bool:
    return any(kw in (name or "").lower() for kw in _SYSTEM_DESIGNATION_KEYWORDS)


def canonical_name_and_kind(name: str, kind: str) -> tuple[str, str]:
    """Resolve ``(name, kind)`` to their canonical forms: a watchlist-recognised alias maps to the
    watchlist's canonical name (e.g. "Elbit Systems" -> "Elbit"); ``kind`` is normalised via
    :func:`normalize_kind`. A known system/weapon designation (see :func:`normalize_kind`'s note)
    keeps its own name rather than being folded into the company it happens to be aliased under.
    Names not on the watchlist are returned unchanged (case-insensitive de-duplication against the
    DB happens separately, in ``upsert_entity`` itself)."""
    if _is_system_designation(name):
        return name, "system"
    canonical = resolve_canonical(name)
    canonical_name = canonical["name"] if canonical else name
    return canonical_name, normalize_kind(name, kind)


def find_watchlist_aliases_in_text(text: str) -> list[str]:
    """Q3-8: every watchlist canonical name whose name or alias literally appears in ``text``
    (whole-word/phrase, case-insensitive for Latin script) -- deduplicated, in order of first
    appearance in the surface-string list (longest surface first, so a full company name is
    preferred over a shorter alias substring of it)."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for surface, canonical_name in _surface_to_canonical_name():
        if canonical_name in seen:
            continue
        if re.search(r"\b" + re.escape(surface) + r"\b", text, re.IGNORECASE):
            found.append(canonical_name)
            seen.add(canonical_name)
    return found
