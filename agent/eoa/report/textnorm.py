"""Shared Hebrew punctuation normaliser for rendered report text.

Round-3 (D7 finding 2, docs/qa/loop/round_1_judge.md): the BD territory report's prose and table
cells sometimes carry a doubled ASCII quote (two literal ``"`` characters back to back, e.g. in
the Hebrew abbreviation for "USA") where a single Hebrew gershayim mark was intended -- the model
(or a downstream string join) occasionally emits the doubled form instead of the one correct
punctuation mark. This module is a small, pure, idempotent set of string-normalisation helpers any
report renderer can apply to *display* text right before it reaches the page.

Applied only to rendered/display text -- never to raw DB fields at rest, citation markers
(``[n]``), URLs, or file paths. Three passes, always run in this order:

1. :func:`collapse_doubled_quotes` -- two-or-more literal ASCII ``"`` in a row collapse to one.
   Always safe: a correctly-typed doubled quote never occurs in Hebrew or English prose.
2. :func:`ascii_quote_to_gershayim` -- a single ASCII ``"`` directly between two Hebrew-script
   characters becomes the Hebrew gershayim character (U+05F4) -- the correct punctuation mark for
   a Hebrew acronym/abbreviation, for which a plain ``"`` is only ever a keyboard stand-in.
3. :func:`ascii_apostrophe_to_geresh` -- an ASCII apostrophe directly after a Hebrew-script
   character becomes the Hebrew geresh character (U+05F3), same rationale for the
   single-character mark.

:func:`normalize_hebrew_punctuation` runs all three in order and is the one function report
renderers should actually call.

Hebrew-script detection uses raw Unicode codepoint ranges (the Hebrew block and the Hebrew
presentation-forms block -- same convention as ``eoa.report.docx_builder``'s own
``_HEBREW_RANGES``), built via ``chr()``/integer comparisons rather than embedding Hebrew
characters directly inside a regex character-class literal, which is fragile across tools/editors
that may re-encode or reorder bidirectional text in source files.
"""

from __future__ import annotations

import re
from typing import Any, TypeVar

# (Hebrew block, Hebrew presentation forms) -- matches eoa.report.docx_builder's `_HEBREW_RANGES`.
_HEBREW_CODEPOINT_RANGES = ((0x0590, 0x05FF), (0xFB1D, 0xFB4F))

GERSHAYIM = chr(0x05F4)
GERESH = chr(0x05F3)

_DOUBLED_QUOTE_RE = re.compile(r'"{2,}')


def _is_hebrew_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _HEBREW_CODEPOINT_RANGES)


def collapse_doubled_quotes(text: str) -> str:
    """Collapse any run of two-or-more literal ``"`` characters into a single ``"``."""
    if not text:
        return text
    return _DOUBLED_QUOTE_RE.sub('"', text)


def ascii_quote_to_gershayim(text: str) -> str:
    """Convert a lone ASCII ``"`` sitting directly between two Hebrew-script characters into the
    Hebrew gershayim mark (U+05F4)."""
    if not text:
        return text
    chars = list(text)
    last = len(chars) - 1
    for i, ch in enumerate(chars):
        if ch != '"' or i == 0 or i == last:
            continue
        if _is_hebrew_char(chars[i - 1]) and _is_hebrew_char(chars[i + 1]):
            chars[i] = GERSHAYIM
    return "".join(chars)


def ascii_apostrophe_to_geresh(text: str) -> str:
    """Convert an ASCII ``'`` sitting directly after a Hebrew-script character into the Hebrew
    geresh mark (U+05F3)."""
    if not text:
        return text
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch == "'" and i > 0 and _is_hebrew_char(chars[i - 1]):
            chars[i] = GERESH
    return "".join(chars)


def normalize_hebrew_punctuation(text: str | None) -> str | None:
    """Apply all three normalisation passes, in order, idempotently.

    ``None``/empty input is returned unchanged (falsy short-circuit), so this is always safe to
    call on an optional field without a separate ``is None`` check at the call site.
    """
    if not text:
        return text
    text = collapse_doubled_quotes(text)
    text = ascii_quote_to_gershayim(text)
    text = ascii_apostrophe_to_geresh(text)
    return text


# --------------------------------------------------------------------------
# Round 3 (2026-09-06, D6 judge finding 4): daily/weekly/monthly report drafts wire in
# :func:`normalize_draft` below (this module's ``normalize_hebrew_punctuation`` was already landed
# for the BD territory report as part of a concurrent D7 fix -- reused here as-is rather than
# duplicated, so both report families share one normalisation implementation). ``normalize_draft``
# walks a whole draft object -- either the structured, sentence-per-claim shape
# (``DailyReportDraft``/``WeeklyReportDraft``: ``Sentence``/``StructuredSection``/
# ``OutlookIndicator``/``AnalystNote`` objects) or the legacy free-prose shape
# (``MonthlyReportDraft``/``BdTerritoryReportDraft``: plain ``*_he`` strings) -- and returns a
# normalized copy, duck-typed via ``getattr``/``hasattr`` so it needs no import of
# ``eoa.llm.schemas.*`` (avoids a circular import between ``eoa.report`` and ``eoa.llm.schemas``).
# Wired into ``eoa.report.daily``/``eoa.report.weekly``/``eoa.report.monthly`` (each calls
# ``normalize_draft(draft)`` once, right after the QA-gate loop resolves and before the draft is
# handed to ``eoa.report.docx_builder``); ``eoa.report.bd_territory`` already normalizes its own
# draft inline (see ``_normalize_draft_text`` there) and does not need this function.
# --------------------------------------------------------------------------

_M = TypeVar("_M")


def _normalize_str_fields(obj: _M, fields: tuple[str, ...]) -> _M:
    """Return a copy of the pydantic model ``obj`` with :func:`normalize_hebrew_punctuation`
    applied to each of ``fields`` that is a non-empty string -- ``model_copy`` (not the
    constructor) so no field validator re-runs against the now-normalized text."""
    updates: dict[str, Any] = {}
    for field in fields:
        value = getattr(obj, field, None)
        if isinstance(value, str) and value:
            normalized = normalize_hebrew_punctuation(value)
            if normalized != value:
                updates[field] = normalized
    return obj.model_copy(update=updates) if updates else obj


def normalize_draft(draft: _M) -> _M:
    """Apply :func:`normalize_hebrew_punctuation` to every text field a report draft renders.

    Duck-typed across both draft shapes in this codebase (see the module note above); a field the
    given ``draft`` doesn't carry is simply skipped (``getattr``/``hasattr`` guarded throughout),
    so this one function works unchanged for ``DailyReportDraft``, ``WeeklyReportDraft`` and
    ``MonthlyReportDraft`` without importing any of their classes.
    """
    updates: dict[str, Any] = {}

    # exec_summary: list[Sentence] (structured: daily/weekly)
    exec_summary = getattr(draft, "exec_summary", None)
    if isinstance(exec_summary, list) and exec_summary:
        updates["exec_summary"] = [_normalize_str_fields(s, ("text_he",)) for s in exec_summary]

    # exec_summary_he: str (legacy: monthly/bd_territory)
    if hasattr(draft, "exec_summary_he"):
        updates["exec_summary_he"] = normalize_hebrew_punctuation(draft.exec_summary_he) or ""

    # market_bullets_he: list[str] (legacy: bd_territory only, harmless no-op elsewhere)
    market_bullets = getattr(draft, "market_bullets_he", None)
    if isinstance(market_bullets, list) and market_bullets:
        updates["market_bullets_he"] = [normalize_hebrew_punctuation(b) or "" for b in market_bullets]

    # sections: list[StructuredSection] (structured, .sentences) or list[ReportSection] (legacy,
    # .prose_he) -- both also carry a title_he.
    sections = getattr(draft, "sections", None)
    if isinstance(sections, list) and sections:
        new_sections = []
        for section in sections:
            sec_updates: dict[str, Any] = {}
            if hasattr(section, "title_he"):
                sec_updates["title_he"] = normalize_hebrew_punctuation(section.title_he) or ""
            if hasattr(section, "sentences"):
                sec_updates["sentences"] = [_normalize_str_fields(s, ("text_he",)) for s in section.sentences]
            if hasattr(section, "prose_he"):
                sec_updates["prose_he"] = normalize_hebrew_punctuation(section.prose_he) or ""
            new_sections.append(section.model_copy(update=sec_updates) if sec_updates else section)
        updates["sections"] = new_sections

    # trends: list[WeeklyTrendSection] (weekly only)
    trends = getattr(draft, "trends", None)
    if isinstance(trends, list) and trends:
        new_trends = []
        for trend in trends:
            t_updates: dict[str, Any] = {"title_he": normalize_hebrew_punctuation(trend.title_he) or ""}
            if hasattr(trend, "sentences"):
                t_updates["sentences"] = [_normalize_str_fields(s, ("text_he",)) for s in trend.sentences]
            new_trends.append(trend.model_copy(update=t_updates))
        updates["trends"] = new_trends

    # trend_paragraphs: list[TrendParagraph] (monthly, legacy)
    trend_paragraphs = getattr(draft, "trend_paragraphs", None)
    if isinstance(trend_paragraphs, list) and trend_paragraphs:
        updates["trend_paragraphs"] = [
            _normalize_str_fields(t, ("title_he", "prose_he")) for t in trend_paragraphs
        ]

    # system_note_he: str (structured only -- deterministic system text, normalized too since it
    # can embed a raw DB/title fragment).
    if hasattr(draft, "system_note_he"):
        updates["system_note_he"] = normalize_hebrew_punctuation(draft.system_note_he) or ""

    # analyst_note_he: AnalystNote | None
    note = getattr(draft, "analyst_note_he", None)
    if note is not None and hasattr(note, "sentences_he"):
        updates["analyst_note_he"] = note.model_copy(
            update={"sentences_he": [normalize_hebrew_punctuation(s) or "" for s in note.sentences_he]}
        )

    # outlook: list[OutlookIndicator] (structured)
    outlook = getattr(draft, "outlook", None)
    if isinstance(outlook, list) and outlook:
        updates["outlook"] = [_normalize_str_fields(o, ("text_he",)) for o in outlook]

    # outlook_he: str (legacy)
    if hasattr(draft, "outlook_he"):
        updates["outlook_he"] = normalize_hebrew_punctuation(draft.outlook_he) or ""

    # risks_assumptions_he: str (bd_territory, legacy; harmless no-op elsewhere)
    if hasattr(draft, "risks_assumptions_he"):
        updates["risks_assumptions_he"] = normalize_hebrew_punctuation(draft.risks_assumptions_he) or ""

    # open_points_he: list[str] (both shapes)
    open_points = getattr(draft, "open_points_he", None)
    if isinstance(open_points, list) and open_points:
        updates["open_points_he"] = [normalize_hebrew_punctuation(p) or "" for p in open_points]

    return draft.model_copy(update=updates) if updates else draft
