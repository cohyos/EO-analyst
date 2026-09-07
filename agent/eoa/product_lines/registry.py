"""Typed access to ``config/product_lines.yaml`` (PL-backend, 2026-09-07).

Mirrors the read-through-``settings()`` convention every other config-driven module in this
codebase follows (``eoa.config.settings().taxonomy`` / ``.watchlist``) rather than reading the YAML
file directly, so ``settings.cache_clear()`` in a test also clears this, and a test fixture can
override ``Settings(product_lines={...})`` without touching the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from eoa.config import settings


@dataclass(frozen=True)
class ConditionalKeyword:
    """One ``conditional_keywords_en`` entry (R8-tagging, 2026-09-07): ``term`` only counts as a
    tagging signal when at least one of ``context`` also appears in the same item's text -- see
    ``eoa.product_lines.tagging._conditional_match``."""

    term: str
    context: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ProductLineDef:
    """One product line's definition, per ``config/product_lines.yaml``'s own field docs."""

    id: str
    name_he: str
    name_en: str
    keywords_he: tuple[str, ...] = field(default_factory=tuple)
    keywords_en: tuple[str, ...] = field(default_factory=tuple)
    aliases: tuple[str, ...] = field(default_factory=tuple)
    subdomains: tuple[str, ...] = field(default_factory=tuple)
    exemplar_systems: tuple[str, ...] = field(default_factory=tuple)
    competitors: tuple[str, ...] = field(default_factory=tuple)
    our_products: tuple[str, ...] = field(default_factory=tuple)
    conditional_keywords_en: tuple[ConditionalKeyword, ...] = field(default_factory=tuple)


def _as_tuple(value: object) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(v) for v in value if v)


def _parse_conditional_keywords(value: object) -> tuple[ConditionalKeyword, ...]:
    if not value:
        return ()
    out: list[ConditionalKeyword] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        term = row.get("term")
        if not term:
            continue
        out.append(ConditionalKeyword(term=str(term), context=_as_tuple(row.get("context"))))
    return tuple(out)


def _parse(row: dict) -> ProductLineDef | None:
    line_id = row.get("id")
    if not line_id:
        return None
    return ProductLineDef(
        id=str(line_id),
        name_he=str(row.get("name_he") or line_id),
        name_en=str(row.get("name_en") or line_id),
        keywords_he=_as_tuple(row.get("keywords_he")),
        keywords_en=_as_tuple(row.get("keywords_en")),
        aliases=_as_tuple(row.get("aliases")),
        subdomains=_as_tuple(row.get("subdomains")),
        exemplar_systems=_as_tuple(row.get("exemplar_systems")),
        competitors=_as_tuple(row.get("competitors")),
        our_products=_as_tuple(row.get("our_products")),
        conditional_keywords_en=_parse_conditional_keywords(row.get("conditional_keywords_en")),
    )


@lru_cache(maxsize=1)
def _defs_cached(cache_token: int) -> tuple[ProductLineDef, ...]:
    rows = settings().product_lines.get("product_lines") or []
    out: list[ProductLineDef] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = _parse(row)
        if parsed is not None:
            out.append(parsed)
    return tuple(out)


def product_line_defs() -> tuple[ProductLineDef, ...]:
    """Every configured product line, in ``config/product_lines.yaml`` order. Cached per
    ``settings()`` object identity (``id(settings())``) so ``settings.cache_clear()`` -- the
    standard way tests/CLI reload config -- transparently invalidates this cache too, without this
    module needing its own explicit ``clear_cache()`` call wired into every test fixture."""
    return _defs_cached(id(settings()))


def product_line_ids() -> list[str]:
    return [pl.id for pl in product_line_defs()]


def get_product_line(line_id: str) -> ProductLineDef | None:
    for pl in product_line_defs():
        if pl.id == line_id:
            return pl
    return None


def llm_tagging_enabled() -> bool:
    """True when ``config/product_lines.yaml``'s top-level ``llm_tagging`` key is set -- gates
    ``eoa.pipeline.analyze``'s post-tagging hook and ``scripts/backfill_product_lines.py --llm``
    (see :mod:`eoa.product_lines.llm_tagging`). Defaults to ``False`` (same fail-safe convention as
    ``product_line_defs`` degrading to an empty tuple) so an older/unedited copy of the config file
    never silently starts spending LLM calls."""
    return bool(settings().product_lines.get("llm_tagging", False))
