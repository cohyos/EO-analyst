"""A16 (מעקב רכישות ושותפויות, user requirement 2026-09-06): acquisition/M&A watchlist lookups.

``config/watchlist.yaml``'s ``acquisition_watch`` top-level list names a small set of companies of
interest in an acquisition/consolidation context (e.g. Teledyne as a possible acquirer/consolidator,
PVP Advanced EO Systems as a possible target, and PVP's direct EO/IR competitors via each entry's
``peers_of``). This module is the single reader of that list -- deliberately separate from
``eoa.pipeline.israel_focus`` (which owns the *Israeli-industry* watchlist reading, a different
axis) and from ``eoa.pipeline.entity_normalize`` (which owns general NER/alias resolution against
the whole ``companies:`` watchlist) -- so a name here is always resolved against ``companies:``'s
own ``aliases`` (never a separately-maintained alias list) before being matched.

Two independent things are exposed:

1. **Which companies are on the watch** (:func:`acquisition_watch_list`, :func:`acquisition_watch_names`,
   :func:`peers_of`, :func:`all_watch_and_peer_names`) -- pure config lookups, no text/DB involved.
2. **Whether one item/event carries an M&A/investment/partnership signal**
   (:func:`has_ma_signal`) -- a small deterministic keyword/event-kind check (English + Hebrew),
   used both by ``eoa.pipeline.triage``'s deterministic alert (text-only, since ``events`` rows
   don't exist yet at triage time -- ``analyze``, which extracts them, runs *after* ``triage`` --
   see ``docs/PLAN_WINDOWS_NATIVE.md``'s stage order) and by ``eoa.report.acquisition_watch``'s
   weekly section (real ``events.kind`` values, once they exist).

Nothing here talks to the database -- every function is a pure config/text lookup, safe to unit
test without a live DB connection (mirrors ``eoa.pipeline.israel_focus``'s own contract).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

import structlog

from eoa.config import settings

log = structlog.get_logger(__name__)

#: Event kinds that, on their own, count as an M&A/investment/partnership signal for
#: :func:`has_ma_signal`. ``db/migrations/versions/0001_core.py``'s ``events.kind`` CHECK
#: constraint only actually allows ``m_and_a``/``partnership``/``investment`` of these (not
#: ``acquisition``) -- ``acquisition`` is kept in this set defensively/for forward-compatibility
#: per the user's own phrasing of this requirement; it simply never matches a real row today.
_MA_SIGNAL_EVENT_KINDS = frozenset({"m_and_a", "acquisition", "investment", "partnership"})

#: English M&A/investment/partnership vocabulary (word-boundary, case-insensitive).
_MA_KEYWORDS_EN: tuple[str, ...] = (
    "acquire",
    "acquires",
    "acquired",
    "acquisition",
    "acquisitions",
    "merger",
    "mergers",
    "merge",
    "buyout",
    "buyouts",
    "stake",
    "funding round",
    "investment",
    "joint venture",
)

#: Hebrew M&A/investment/partnership vocabulary (plain substring match -- Hebrew has no simple
#: word-boundary regex equivalent that behaves well with prefixed conjunctions/prepositions like
#: "ל-"/"ב-"/"ו-", so this deliberately mirrors ``eoa.pipeline.israel_focus``'s own plain-substring
#: convention for Hebrew keyword checks rather than ``\b``-anchoring).
_MA_KEYWORDS_HE: tuple[str, ...] = ("רכישה", "רכישת", "מיזוג", "השקעה", "גיוס", "שותפות")

_MA_KEYWORDS_EN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in _MA_KEYWORDS_EN) + r")\b", re.IGNORECASE
)


def _acquisition_watch_records() -> list[dict[str, Any]]:
    wl = settings().watchlist or {}
    return list(wl.get("acquisition_watch") or [])


def acquisition_watch_list() -> list[dict[str, Any]]:
    """Raw ``acquisition_watch`` records from ``config/watchlist.yaml`` (``name``/``role``/
    ``country``/``peers_of``), unmodified."""
    return _acquisition_watch_records()


def acquisition_watch_names() -> list[str]:
    """Canonical names of every ``acquisition_watch`` entry, in ``config/watchlist.yaml`` order."""
    return [rec["name"] for rec in _acquisition_watch_records() if rec.get("name")]


def peers_of(name: str) -> list[str]:
    """The ``peers_of`` list of the ``acquisition_watch`` entry ``name`` resolves to (directly or
    via a ``companies:`` alias) -- ``[]`` if ``name`` isn't on the watch, or that entry carries no
    ``peers_of``."""
    canonical = canonicalize_name(name)
    if canonical is None:
        return []
    for rec in _acquisition_watch_records():
        if rec.get("name") == canonical:
            return list(rec.get("peers_of") or [])
    return []


def all_watch_and_peer_names() -> list[str]:
    """Every ``acquisition_watch`` company name plus every name listed in any entry's
    ``peers_of``, deduplicated, first-seen order -- the broader set
    ``eoa.report.acquisition_watch``'s weekly events table reports on (a move by any one of a
    watched company's peers is read against it too)."""
    names: list[str] = []
    seen: set[str] = set()
    for rec in _acquisition_watch_records():
        for n in [rec.get("name"), *(rec.get("peers_of") or [])]:
            if n and n not in seen:
                seen.add(n)
                names.append(n)
    return names


def _companies_by_name() -> dict[str, dict[str, Any]]:
    wl = settings().watchlist or {}
    return {c["name"]: c for c in (wl.get("companies") or []) if c.get("name")}


def _build_alias_map(names: Iterable[str]) -> dict[str, str]:
    """``{casefolded name/alias -> canonical name}`` for every name in ``names`` -- aliases are
    read from that same name's ``config/watchlist.yaml`` ``companies:`` record when one exists
    (never a separately-maintained alias list); a name with no ``companies:`` record still maps
    from its own casefolded self."""
    companies = _companies_by_name()
    out: dict[str, str] = {}
    for name in names:
        out.setdefault(name.casefold(), name)
        rec = companies.get(name)
        for alias in (rec.get("aliases") if rec else None) or []:
            out.setdefault(alias.casefold(), name)
    return out


@lru_cache(maxsize=1)
def _watch_alias_map() -> dict[str, str]:
    return _build_alias_map(acquisition_watch_names())


@lru_cache(maxsize=1)
def _watch_and_peer_alias_map() -> dict[str, str]:
    return _build_alias_map(all_watch_and_peer_names())


def canonicalize_name(name: str | None) -> str | None:
    """Resolve ``name`` (an ``acquisition_watch`` canonical name, or any ``companies:`` alias of
    one) to its canonical ``acquisition_watch`` name -- ``None`` if it matches no watch company."""
    if not name:
        return None
    return _watch_alias_map().get(name.casefold())


def canonicalize_watch_or_peer_name(name: str | None) -> str | None:
    """Like :func:`canonicalize_name`, but resolves against :func:`all_watch_and_peer_names`
    (watch companies AND their peers) rather than just the watch companies themselves."""
    if not name:
        return None
    return _watch_and_peer_alias_map().get(name.casefold())


def is_acquisition_watch_name(name: str | None) -> bool:
    """True if ``name`` refers (directly, or via a ``companies:`` alias) to an
    ``acquisition_watch`` company."""
    return canonicalize_name(name) is not None


def acquisition_watch_hit(names: list[str] | None) -> str | None:
    """The first canonical ``acquisition_watch`` company name found among ``names`` (typically an
    item's ``entities_mentioned`` or an event's ``parties``), or ``None``."""
    for n in names or []:
        canonical = canonicalize_name(n)
        if canonical:
            return canonical
    return None


def watch_or_peer_hit(names: list[str] | None) -> str | None:
    """Like :func:`acquisition_watch_hit`, but matches watch companies AND their peers (see
    :func:`canonicalize_watch_or_peer_name`)."""
    for n in names or []:
        canonical = canonicalize_watch_or_peer_name(n)
        if canonical:
            return canonical
    return None


def has_ma_signal(text: str | None, *, event_kinds: Iterable[str] | None = None) -> bool:
    """True if ``text`` carries M&A/investment/partnership vocabulary (English or Hebrew,
    case-insensitive) OR ``event_kinds`` contains one of :data:`_MA_SIGNAL_EVENT_KINDS`."""
    if event_kinds and _MA_SIGNAL_EVENT_KINDS.intersection(event_kinds):
        return True
    text = text or ""
    if _MA_KEYWORDS_EN_RE.search(text):
        return True
    return any(kw in text for kw in _MA_KEYWORDS_HE)


def clear_caches() -> None:
    """Test helper: drop every ``lru_cache`` here after monkeypatching ``settings().watchlist``
    (mirrors ``eoa.pipeline.israel_focus.clear_caches``)."""
    _watch_alias_map.cache_clear()
    _watch_and_peer_alias_map.cache_clear()
