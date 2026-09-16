"""Deterministic (no LLM) platform-integration-opportunity pre-check.

CR-platform-opportunity (2026-09-16, docs/qa/content_review/CR-platform-opportunity.md): a new
combat platform (an aircraft/UAV/CCA/vessel/vehicle entering production or testing) that states or
implies an OPEN external EO/IR/targeting/sensor pod slot is a business-development opportunity for
the matching Israeli product line(s) -- EVEN WHEN the article carries no substantive EO/IR
technical detail of its own. The existing classify-stage "platform vs. EO/IR payload" rule
(``agent/eoa/llm/prompts/classify.md``'s "כלל פלטפורמה מול מטע\"ד") is deliberately conservative
about exactly this shape of story (a platform/weapons-integration piece with only a passing pod/
sensor mention) and sends it to ``out_of_scope`` -- which then never reaches ``triage``/``analyze``
(see ``eoa.memory.relational._ANALYZE_STAGE_SCOPE_FILTER``), so no product-line tag, so-what, or
report row is ever produced for what is, from a BD perspective, exactly the kind of signal this
pipeline exists to surface (the live miss: items 22760/23252, TWZ/Breaking Defense reporting on
Anduril's YFQ-44A Fury CCA being "fit checked" with an open pod/sensor slot).

This module is the deterministic, LLM-independent pre-check that closes that gap: it never invents
a fact and never classifies anything by itself -- it only detects, from the item's own title/
clean_text, whether BOTH a named ``platforms`` entry AND an ``opportunity_signals`` term (see
``config/product_lines.yaml``'s field docs) are present, for any configured product line. The
result (:class:`PlatformOpportunityHint`) is:

  1. rendered into the classify prompt as extra context (``eoa.pipeline.classify``'s
     ``{opportunity_hint}`` template variable) so the model can weigh it, and
  2. used by ``eoa.pipeline.classify.apply_platform_opportunity_gate`` as a deterministic override
     -- when the model still returns ``out_of_scope`` despite a hint, the gate forces the item
     in-scope (dimension ``business``, the closest EO domain, tag ``platform_integration_opportunity``)
     regardless of the model's own conservatism, exactly mirroring the existing
     ``apply_no_eoir_gate``/``apply_generic_ai_market_gate`` deterministic-override convention in
     that module.

Matching is deliberately a plain case-insensitive substring search (NOT
``eoa.product_lines.tagging``'s stricter word-boundary matcher): a real headline plurals its pod
term ("targeting pods", not the configured singular "targeting pod"), and a strict word-boundary
match on the singular would silently miss it. The short multi-word phrases configured here (see
``config/product_lines.yaml``) are specific enough that a plain substring match carries acceptably
low false-positive risk for this deterministic pre-check's purpose (a hint fed to a classify
prompt / a forced-in-scope override, not a final unappealable classification).

Calibration note (2026-09-16, item 20162 "Vengeance and Fury: US Air Force names new CCAs"): a
platform's own CLASS name ("CCA"/"collaborative combat aircraft"/"loyal wingman"/"MUM-T") is
itself a configured ``opportunity_signals`` term for the airborne lines, since any CCA/loyal-
wingman airframe is, by class, an open-architecture payload carrier -- so a pure naming/budget
story with zero actual pod/sensor content can still match this pre-check (platform name "Fury" +
signal "CCA" both present) even though it has no genuine EO/IR angle of its own. This is accepted
by design rather than excluded: the deterministic gate only forces the item IN SCOPE as a business
opportunity, it does not inflate its importance -- ``eoa.pipeline.triage``'s own floor
(``apply_platform_opportunity_floor``) only guarantees ``level >= yellow`` (the lowest in-scope
tier), and the model's own novelty/magnitude scoring is free to keep such a thin story right at
that floor while a richer story (22760/23252, which both state the pod plan explicitly) scores
higher on its own merits.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from eoa.product_lines.registry import ProductLineDef, product_line_defs

#: The tag every gated-in item carries (classify.py) and every downstream consumer (triage's
#: level floor, the analyze-stage so-what framing, the report-layer sections) keys off of.
TAG = "platform_integration_opportunity"


@dataclass(frozen=True)
class PlatformOpportunityHint:
    """One or more configured product lines whose own ``platforms`` + ``opportunity_signals``
    both hit this item's text. Never constructed with an empty ``line_ids`` -- see
    :func:`detect_platform_opportunity`, the only place that builds one."""

    line_ids: tuple[str, ...]
    platforms: tuple[str, ...] = field(default_factory=tuple)
    signals: tuple[str, ...] = field(default_factory=tuple)

    def prompt_text_he(self) -> str:
        """Short Hebrew line describing the hit, rendered into the classify prompt's
        ``{opportunity_hint}`` slot -- informative only, never an instruction the model must obey
        verbatim (mirrors ``eoa.pipeline.triage``'s own ``watchlist_hits`` convention)."""
        platforms_text = ", ".join(self.platforms) or "—"
        signals_text = ", ".join(self.signals) or "—"
        return (
            f"זוהתה התאמה דטרמיניסטית (לא-LLM) לפלטפורמה מוכרת ({platforms_text}) יחד עם ביטוי "
            f"שמרמז על חריץ אינטגרציה חיצוני לחיישן/פוד ({signals_text}). זהו רמז בלבד — שקול אם "
            "מדובר בהזדמנות עסקית לאינטגרציית פוד/חיישן, גם אם אין בפריט פירוט טכני EO/IR ממשי."
        )


def _substring_hits(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    if not text or not terms:
        return ()
    lowered = text.lower()
    return tuple(t for t in terms if t and t.lower() in lowered)


def _line_hint(pl: ProductLineDef, text: str) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    if not pl.platforms or not pl.opportunity_signals:
        return None
    platform_hits = _substring_hits(text, pl.platforms)
    if not platform_hits:
        return None
    signal_hits = _substring_hits(text, pl.opportunity_signals)
    if not signal_hits:
        return None
    return platform_hits, signal_hits


def detect_platform_opportunity(
    title: str | None, clean_text: str | None
) -> PlatformOpportunityHint | None:
    """``None`` when no configured product line has BOTH a ``platforms`` hit AND an
    ``opportunity_signals`` hit in ``title``+``clean_text`` (plain substring, case-insensitive --
    see module docstring for why). Otherwise a :class:`PlatformOpportunityHint` naming every
    matching line id plus the union of matched platform names/signal terms across all of them.

    Never raises: a bad/missing ``config/product_lines.yaml`` (``product_line_defs`` itself never
    raises, see that module) simply yields no matches for any line, i.e. ``None`` here -- same
    fail-open convention as every other config-driven deterministic gate in this codebase."""
    text = " ".join(filter(None, [title, clean_text]))
    if not text.strip():
        return None
    matched_lines: list[str] = []
    matched_platforms: set[str] = set()
    matched_signals: set[str] = set()
    for pl in product_line_defs():
        hit = _line_hint(pl, text)
        if hit is None:
            continue
        platform_hits, signal_hits = hit
        matched_lines.append(pl.id)
        matched_platforms.update(platform_hits)
        matched_signals.update(signal_hits)
    if not matched_lines:
        return None
    return PlatformOpportunityHint(
        line_ids=tuple(matched_lines),
        platforms=tuple(sorted(matched_platforms)),
        signals=tuple(sorted(matched_signals)),
    )
