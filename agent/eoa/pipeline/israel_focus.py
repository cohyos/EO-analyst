"""A13 (מיקוד תעשייה ישראלית, 2026-09-06): deterministic Israeli-industry relevance scoring.

docs/PLAN_WINDOWS_NATIVE.md row A13 (user requirement, 2026-09-06 morning): Israeli defense
companies -- especially electro-optics -- must be a focus of this analyst. This module computes a
purely deterministic (no LLM) ``israel_relevance`` score in ``[0, 1]`` for one item, from four
signals:

  1. **Israeli company/agency mentioned** -- a ``config/watchlist.yaml`` company with
     ``country: IL``, or a ``config/watchlist.yaml`` ``agencies`` entry, matched by name/alias.
  2. **Israeli customer/program** -- an Israeli government/military body (IDF, IMOD, IAF, ...)
     named as a party/customer, via the same agency match.
  3. **Direct competitor to an Israeli company in the same subdomain** -- a non-Israeli watchlist
     company sharing a ``focus`` tag with at least one Israeli watchlist company.
  4. **Export-market signal** -- the item's ``geography`` (or a country name in the text) is one
     of Israel's known EO/IR export markets (India, Greece, Azerbaijan, Germany, Philippines,
     Vietnam, South Korea, ...).
  5. **Hebrew-language source** -- ``lang == "he"``.

Called from ``eoa.pipeline.classify.persist_classification`` (initial score, right after entity
extraction) and refreshed from ``eoa.pipeline.analyze.persist_analysis`` (once events/edges and any
watchlist-alias backfill have landed) -- see the ``# --- A13`` delimited blocks in both files.
Nothing here talks to the database except :func:`score_and_persist_entity_israeli`; the scoring
function itself is pure text/config lookups, safe to unit test without a live DB connection.
"""

from __future__ import annotations

import re
from functools import lru_cache

import structlog

from eoa.config import settings

log = structlog.get_logger(__name__)

#: Known export markets for Israeli EO/IR systems (ISO-2-ish codes, matching
#: `config/taxonomy.yaml`'s `geographies` convention where possible; IN/GR/AZ/DE/PH/VN/KR are the
#: ones named explicitly in docs/PLAN_WINDOWS_NATIVE.md row A13).
EXPORT_MARKET_COUNTRIES = frozenset({"IN", "GR", "AZ", "DE", "PH", "VN", "KR"})

#: Hebrew/English names for the same export-market countries, for a text-mention fallback when
#: `items.geography` itself is "other"/unset but the country is named in the title/summary.
_EXPORT_MARKET_NAMES: dict[str, tuple[str, ...]] = {
    "IN": ("India", "הודו"),
    "GR": ("Greece", "יוון"),
    "AZ": ("Azerbaijan", "אזרבייג'ן", "אזרביג'ן"),
    "DE": ("Germany", "גרמניה"),
    "PH": ("Philippines", "הפיליפינים"),
    "VN": ("Vietnam", "וייטנאם"),
    "KR": ("South Korea", "Korea", "דרום קוריאה"),
}

REASON_ISRAELI_COMPANY = "israeli_company_mentioned"
REASON_ISRAELI_AGENCY = "israeli_agency_or_customer"
REASON_COMPETITOR = "competitor_to_israeli_company"
REASON_EXPORT_MARKET = "export_market_signal"
REASON_HEBREW_SOURCE = "hebrew_language_source"

_SCORE_WEIGHTS: dict[str, float] = {
    REASON_ISRAELI_COMPANY: 0.6,
    REASON_ISRAELI_AGENCY: 0.5,
    REASON_COMPETITOR: 0.3,
    REASON_EXPORT_MARKET: 0.25,
    REASON_HEBREW_SOURCE: 0.2,
}


def _word_boundary_pattern(terms: list[str]) -> re.Pattern[str] | None:
    escaped = [re.escape(t) for t in terms if t and t.strip()]
    if not escaped:
        return None
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


@lru_cache(maxsize=1)
def _israeli_company_records() -> list[dict]:
    wl = settings().watchlist or {}
    return [c for c in (wl.get("companies") or []) if (c.get("country") or "").upper() == "IL"]


@lru_cache(maxsize=1)
def _non_israeli_company_records() -> list[dict]:
    wl = settings().watchlist or {}
    return [c for c in (wl.get("companies") or []) if (c.get("country") or "").upper() != "IL"]


@lru_cache(maxsize=1)
def _agency_records() -> list[dict]:
    wl = settings().watchlist or {}
    return list(wl.get("agencies") or [])


def israeli_watchlist_names() -> list[str]:
    """Canonical names of every Israeli watchlist company -- used by
    ``eoa.report.bd_territory`` so its BD competitors table's "is_israeli_industry" flag stays in
    sync with the watchlist instead of a separately maintained hardcoded list."""
    return [c["name"] for c in _israeli_company_records() if c.get("name")]


@lru_cache(maxsize=1)
def _israeli_alias_pattern() -> re.Pattern[str] | None:
    terms: list[str] = []
    for c in _israeli_company_records():
        terms.append(c.get("name", ""))
        terms.extend(c.get("aliases") or [])
    return _word_boundary_pattern(terms)


@lru_cache(maxsize=1)
def _agency_alias_pattern() -> re.Pattern[str] | None:
    terms: list[str] = []
    for a in _agency_records():
        terms.append(a.get("name", ""))
        terms.extend(a.get("aliases") or [])
    return _word_boundary_pattern(terms)


@lru_cache(maxsize=1)
def _competitor_focus_map() -> dict[str, set[str]]:
    """``{non-Israeli watchlist company name -> set of focus tags it shares with at least one
    Israeli watchlist company}`` -- used to flag a non-Israeli company as a direct competitor
    "in the same subdomain" per docs/PLAN_WINDOWS_NATIVE.md row A13."""
    israeli_focus: set[str] = set()
    for c in _israeli_company_records():
        israeli_focus.update(c.get("focus") or [])
    out: dict[str, set[str]] = {}
    for c in _non_israeli_company_records():
        shared = set(c.get("focus") or []) & israeli_focus
        if shared:
            out[c["name"]] = shared
    return out


def _entities_hit_israeli_company(entities: list[str] | None) -> bool:
    if not entities:
        return False
    for name in entities:
        if any((name or "").casefold() == c.get("name", "").casefold() for c in _israeli_company_records()):
            return True
    return False


def _entities_hit_competitor(entities: list[str] | None) -> bool:
    if not entities:
        return False
    comp_map = _competitor_focus_map()
    return any(name in comp_map for name in entities)


def _text_hits_export_market(text: str, geography: str | None) -> bool:
    if geography and geography.upper() in EXPORT_MARKET_COUNTRIES:
        return True
    if not text:
        return False
    for code, names in _EXPORT_MARKET_NAMES.items():
        if code == (geography or "").upper():
            continue
        pattern = _word_boundary_pattern(list(names))
        if pattern and pattern.search(text):
            return True
    return False


def israel_relevance(
    item_text: str,
    entities: list[str] | None = None,
    *,
    lang: str | None = None,
    geography: str | None = None,
) -> dict:
    """Deterministic Israeli-industry relevance for one item.

    ``item_text`` should be the item's title + summary/clean_text (whatever is available);
    ``entities`` is ``items.entities_mentioned`` (canonical watchlist names where resolved).
    Returns ``{"score": float in [0, 1], "reasons": [str, ...]}`` -- ``reasons`` is a list of the
    stable reason codes above (module constants), in the order they were checked, never empty
    unless ``score == 0``.
    """
    text = item_text or ""
    reasons: list[str] = []

    israeli_alias_pat = _israeli_alias_pattern()
    if _entities_hit_israeli_company(entities) or (israeli_alias_pat and israeli_alias_pat.search(text)):
        reasons.append(REASON_ISRAELI_COMPANY)

    agency_pat = _agency_alias_pattern()
    if agency_pat and agency_pat.search(text):
        reasons.append(REASON_ISRAELI_AGENCY)

    if _entities_hit_competitor(entities):
        reasons.append(REASON_COMPETITOR)

    if _text_hits_export_market(text, geography):
        reasons.append(REASON_EXPORT_MARKET)

    if (lang or "").lower() == "he":
        reasons.append(REASON_HEBREW_SOURCE)

    score = min(1.0, sum(_SCORE_WEIGHTS[r] for r in reasons))
    return {"score": round(score, 3), "reasons": reasons}


def score_and_persist_entity_israeli(name: str) -> bool | None:
    """Recompute and persist ``entities.is_israeli`` for the entity named ``name``.

    True when ``name`` resolves to an Israeli watchlist company/program (or one of its aliases) or
    to a curated Israeli government/military ``agencies`` entry. Mirrors
    ``eoa.pipeline.entity_relevance.score_and_persist_entity``'s shape/contract (best-effort, never
    raises, returns ``None`` when the entity doesn't exist)."""
    try:
        from eoa.db import connection

        with connection() as conn:
            row = conn.execute(
                "SELECT id, country FROM entities WHERE name = %s", (name,)
            ).fetchone()
        if row is None:
            return None
        is_il = (row.get("country") or "").upper() == "IL"
        if not is_il:
            name_cf = name.casefold()
            is_il = any(
                name_cf == c.get("name", "").casefold() or name_cf in {a.casefold() for a in (c.get("aliases") or [])}
                for c in (_israeli_company_records() + _agency_records())
            )
        with connection() as conn:
            conn.execute("UPDATE entities SET is_israeli = %s WHERE id = %s", (is_il, row["id"]))
        return is_il
    except Exception as exc:
        log.warning("israel_focus_entity_flag_failed", name=name, error=str(exc)[:160])
        return None


def clear_caches() -> None:
    """Test helper: drop every ``lru_cache`` here after monkeypatching ``settings().watchlist``."""
    _israeli_company_records.cache_clear()
    _non_israeli_company_records.cache_clear()
    _agency_records.cache_clear()
    _israeli_alias_pattern.cache_clear()
    _agency_alias_pattern.cache_clear()
    _competitor_focus_map.cache_clear()
