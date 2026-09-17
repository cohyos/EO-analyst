"""Typed access to ``config/tech_supply_chain.yaml`` + deterministic keyword-based layer
assignment for the ``tech_daily`` report (daily EO/IR supply-chain technology-watch report, user
request 2026-09-17).

Mirrors ``eoa.product_lines.registry``/``eoa.product_lines.tagging``'s own read-through-
``settings()`` + word-boundary/substring keyword-matching conventions (see those modules' own
docstrings) -- ``settings.cache_clear()`` in a test also clears this registry cache, and a test
fixture can override ``Settings(tech_supply_chain={...})`` without touching the filesystem.

``assign_layers_keyword`` is the FIRST pass ``eoa.report.tech_daily`` runs over every candidate
item (pure function, no DB/network -- trivially unit-testable): a plain Hebrew-substring /
English-word-boundary keyword hit against a layer's own ``keywords_he``/``keywords_en``. Items the
keyword pass leaves with zero layers are handed to a second, LLM-based structured classification
pass (see ``eoa.llm.schemas.tech_daily.TechLayerAssignment`` /
``eoa.llm.prompts.tech_daily.md``) -- this module only owns the deterministic first pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from eoa.config import settings

_WORD_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}


@dataclass(frozen=True)
class TechLayerDef:
    """One EO/IR supply-chain layer's definition, per ``config/tech_supply_chain.yaml``'s own
    field docs."""

    key: str
    label_he: str
    description_he: str = ""
    keywords_he: tuple[str, ...] = field(default_factory=tuple)
    keywords_en: tuple[str, ...] = field(default_factory=tuple)
    domains_hint: tuple[str, ...] = field(default_factory=tuple)
    patent_cpc_hint: tuple[str, ...] = field(default_factory=tuple)


def _as_tuple(value: object) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(v) for v in value if v)


def _parse(row: dict) -> TechLayerDef | None:
    key = row.get("key")
    if not key:
        return None
    return TechLayerDef(
        key=str(key),
        label_he=str(row.get("label_he") or key),
        description_he=str(row.get("description_he") or "").strip(),
        keywords_he=_as_tuple(row.get("keywords_he")),
        keywords_en=_as_tuple(row.get("keywords_en")),
        domains_hint=_as_tuple(row.get("domains_hint")),
        patent_cpc_hint=_as_tuple(row.get("patent_cpc_hint")),
    )


@lru_cache(maxsize=1)
def _defs_cached(cache_token: int) -> tuple[TechLayerDef, ...]:
    rows = settings().tech_supply_chain.get("layers") or []
    out: list[TechLayerDef] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = _parse(row)
        if parsed is not None:
            out.append(parsed)
    return tuple(out)


def layer_defs() -> tuple[TechLayerDef, ...]:
    """Every configured layer, in ``config/tech_supply_chain.yaml`` order (the report's own
    rendering order). Cached per ``settings()`` object identity, same convention as
    ``eoa.product_lines.registry.product_line_defs``."""
    return _defs_cached(id(settings()))


def layer_keys() -> list[str]:
    return [layer.key for layer in layer_defs()]


def get_layer(key: str) -> TechLayerDef | None:
    for layer in layer_defs():
        if layer.key == key:
            return layer
    return None


def _word_pattern(term: str) -> re.Pattern[str]:
    pattern = _WORD_BOUNDARY_CACHE.get(term)
    if pattern is None:
        pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.IGNORECASE)
        _WORD_BOUNDARY_CACHE[term] = pattern
    return pattern


def _any_word_match(haystack: str, terms: tuple[str, ...]) -> bool:
    if not haystack:
        return False
    return any(term and _word_pattern(term).search(haystack) for term in terms)


def _any_substring_he(text_he: str, terms: tuple[str, ...]) -> bool:
    if not text_he:
        return False
    return any(term and term in text_he for term in terms)


def _layer_matches(layer: TechLayerDef, *, text_he: str, hay_en: str) -> bool:
    return _any_substring_he(text_he, layer.keywords_he) or _any_word_match(hay_en, layer.keywords_en)


def assign_layers_keyword(text_he: str | None = None, text_en: str | None = None) -> list[str]:
    """Returns the sorted-by-config-order list of layer keys matched by this content via the
    deterministic keyword pass alone. Never raises -- a bad/missing
    ``config/tech_supply_chain.yaml`` simply yields an empty result for every item, same
    fail-safe convention as ``eoa.product_lines.tagging.tag_product_lines``."""
    text_he = text_he or ""
    hay_en = (text_en or "").lower()
    return [layer.key for layer in layer_defs() if _layer_matches(layer, text_he=text_he, hay_en=hay_en)]


__all__ = [
    "TechLayerDef",
    "assign_layers_keyword",
    "get_layer",
    "layer_defs",
    "layer_keys",
]
