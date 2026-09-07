"""Deterministic (no LLM) product-line tagging (PL-backend, 2026-09-07).

``tag_product_lines`` is a pure function over already-extracted text/entities/subdomain -- no DB
access, no network -- so it is trivially unit-testable and safe to call from both the live pipeline
hook (``eoa.pipeline.analyze.persist_analysis``) and ``scripts/backfill_product_lines.py``.

Matching rules (see ``config/product_lines.yaml``'s own field docs for the per-line data):

1. **Hebrew keyword match** -- a plain substring hit of any ``keywords_he`` entry in ``text_he``.
2. **English/alias keyword match** -- a word-boundary-delimited (not substring) match of any
   ``keywords_en``/``aliases`` entry against ``text_en`` -- word-boundary matters for short Latin
   acronyms (e.g. ``"MWS"``), which would otherwise false-positive inside an unrelated longer word.
3. **Exemplar-system match** -- a word-boundary text hit, OR an exact (case-insensitive) hit in
   ``entities``, of any ``exemplar_systems`` entry. A named real system is a strong, distinctive
   signal on its own.
4. **Subdomain match** -- the item/patent's own ``subdomain`` is one of this product line's
   ``subdomains`` (config/taxonomy.yaml keys) -- the classifier already decided the taxonomy bucket,
   so this is as strong a signal as an explicit keyword.
5. **Competitor entity match** -- a word-boundary text hit, OR an exact ``entities`` hit, of any
   ``competitors`` entry -- but a competitor name **alone is never enough**: `Rafael`/`Elbit`/etc.
   recur across many unrelated stories, so this only contributes when the subdomain match (4) also
   holds (in which case (4) alone would already tag the line -- this rule exists so a caller can
   reason about "why" a competitor-heavy but subdomain-confirmed row was tagged, not because it
   changes the final decision on its own).
"""

from __future__ import annotations

import re

from eoa.product_lines.registry import ProductLineDef, product_line_defs

_WORD_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}


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


def _line_matches(
    pl: ProductLineDef, *, text_he: str, hay_en: str, entity_lower: set[str], subdomain: str | None
) -> bool:
    subdomain_hit = bool(subdomain) and subdomain in pl.subdomains
    keyword_hit = (
        _any_substring_he(text_he, pl.keywords_he)
        or _any_word_match(hay_en, pl.keywords_en)
        or _any_word_match(hay_en, pl.aliases)
    )
    exemplar_hit = _any_word_match(hay_en, pl.exemplar_systems) or bool(
        entity_lower & {s.lower() for s in pl.exemplar_systems}
    )
    if keyword_hit or exemplar_hit or subdomain_hit:
        return True
    # Rule 5: a competitor name alone is never enough -- only counts alongside a subdomain match,
    # which (per rule 4 above) would already have returned True. Kept as an explicit, independently
    # testable branch rather than folded away, per this module's own docstring.
    competitor_hit = _any_word_match(hay_en, pl.competitors) or bool(
        entity_lower & {c.lower() for c in pl.competitors}
    )
    return bool(competitor_hit and subdomain_hit)


def tag_product_lines(
    text_he: str | None = None,
    text_en: str | None = None,
    entities: list[str] | None = None,
    subdomain: str | None = None,
) -> list[str]:
    """Returns the sorted-by-config-order list of product-line ids matched by this content. Never
    raises: a bad/missing ``config/product_lines.yaml`` (see
    :func:`eoa.product_lines.registry.product_line_defs`) simply yields an empty result, same
    convention as every other config-driven deterministic gate in this codebase (e.g.
    ``eoa.pipeline.classify._has_eoir_vocabulary``)."""
    text_he = text_he or ""
    hay_en = (text_en or "").lower()
    entity_lower = {e.lower() for e in (entities or []) if e}
    return [
        pl.id
        for pl in product_line_defs()
        if _line_matches(pl, text_he=text_he, hay_en=hay_en, entity_lower=entity_lower, subdomain=subdomain)
    ]
