"""Shared Hebrew punctuation normaliser for rendered report text.

Round-3 (D7 finding 2, docs/qa/loop/round_1_judge.md): the BD territory report's prose and table
cells sometimes carry a doubled ASCII quote (two literal ``"`` characters back to back, e.g. in
the Hebrew abbreviation for "USA") where a single Hebrew gershayim mark was intended -- the model
(or a downstream string join) occasionally emits the doubled form instead of the one correct
punctuation mark. This module is a small, pure, idempotent set of string-normalisation helpers any
report renderer can apply to *display* text right before it reaches the page.

Applied only to rendered/display text -- never to raw DB fields at rest, citation markers
(``[n]``), URLs, or file paths. Eight passes, always run in this order (Round-14, CR-editing.md,
added the first, second and sixth; Round-16, BIDI-REPORTS.md, added the last two -- see each
function's own docstring):

1. :func:`unescape_stray_backslash_quotes` -- a literal ``\"``/``\'`` (an unescaped JSON/string
   escape that leaked into display text, most often inside a Hebrew acronym like כטב\"ם) drops its
   backslash, leaving the quote/apostrophe for passes 3-4 below to normalize properly.
2. :func:`strip_bidi_isolates` -- removes embedded LRI/RLI/FSI/PDI bidi-isolate control characters
   (U+2066-U+2069), pure noise this renderer's own paragraph/run-level bidi handling never needed.
3. :func:`collapse_doubled_quotes` -- two-or-more literal ASCII ``"`` in a row collapse to one.
   Always safe: a correctly-typed doubled quote never occurs in Hebrew or English prose.
4. :func:`ascii_quote_to_gershayim` -- a single ASCII ``"`` directly between two Hebrew-script
   characters becomes the Hebrew gershayim character (U+05F4) -- the correct punctuation mark for
   a Hebrew acronym/abbreviation, for which a plain ``"`` is only ever a keyboard stand-in.
5. :func:`ascii_apostrophe_to_geresh` -- an ASCII apostrophe directly after a Hebrew-script
   character becomes the Hebrew geresh character (U+05F3), same rationale for the
   single-character mark.
6. :func:`collapse_space_before_closing_punctuation` -- a stray space between a closing
   bracket/paren/quote and the sentence punctuation right after it (often left behind by passes
   1-2 above) collapses away, so ``"[1, 5, 6] ."`` reads ``"[1, 5, 6]."``.
7. :func:`fix_percent_sign_order` -- a percent sign that landed before its number (with or without
   a stray space) swaps back to ``NUMBER%``, e.g. ``"% 75"`` -> ``"75%"``.
8. :func:`collapse_space_after_hebrew_prefix_hyphen` -- a stray space between a Hebrew
   single-letter prefix's maqaf hyphen and the digit/Latin token it binds to collapses away, e.g.
   ``"ב- 75%"`` -> ``"ב-75%"``.

:func:`normalize_hebrew_punctuation` runs all eight in order and is the one function report
renderers should actually call.

Hebrew-script detection uses raw Unicode codepoint ranges (the Hebrew block and the Hebrew
presentation-forms block -- same convention as ``eoa.report.docx_builder``'s own
``_HEBREW_RANGES``), built via ``chr()``/integer comparisons rather than embedding Hebrew
characters directly inside a regex character-class literal, which is fragile across tools/editors
that may re-encode or reorder bidirectional text in source files.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from typing import Any, TypeVar

# (Hebrew block, Hebrew presentation forms) -- matches eoa.report.docx_builder's `_HEBREW_RANGES`.
_HEBREW_CODEPOINT_RANGES = ((0x0590, 0x05FF), (0xFB1D, 0xFB4F))

GERSHAYIM = chr(0x05F4)
GERESH = chr(0x05F3)

_DOUBLED_QUOTE_RE = re.compile(r'"{2,}')

# Round-14 (content-review editing pass, docs/qa/content_review/CR-editing.md): the LRI/PDI
# bidi-isolate pair (U+2066 LEFT-TO-RIGHT ISOLATE / U+2069 POP DIRECTIONAL ISOLATE) shows up
# wrapped around every English/number token inside `deep_search`/`ask` investigation answers
# (``eoa.search.deep_search``/``eoa.api.routes.ask`` -- upstream of this module, not owned by the
# report layer). They are meant to be invisible formatting characters, but (a) several viewers /
# copy-paste paths render them as visible glyphs, and (b) this renderer's own bidi handling
# (per-paragraph ``w:bidi``, per-run RTL font direction -- see ``docx_builder.split_runs``)
# already produces correct bidi presentation without them, so the embedded isolates are pure
# redundant noise that (worse) sit between a token and adjacent punctuation and produce a visible
# stray space before a following ``.``/``,`` (e.g. ``"...% [3, 6] ."``). Stripped outright, never
# reinterpreted -- this is display cleanup, not a bidi-correctness fix (the paragraph/run-level
# handling already carries that).
_BIDI_ISOLATE_RE = re.compile("[\u2066\u2067\u2068\u2069]")

# A stray ``\"``/``\'`` (a literal backslash immediately before a quote/apostrophe) is a JSON/string
# escape sequence that leaked into rendered text unescaped -- most often inside a Hebrew acronym
# like כטב\"ם (should read כטב"ם, i.e. כטב״ם after :func:`ascii_quote_to_gershayim`). A legitimate
# Hebrew or English sentence never contains a literal backslash directly before a quote mark, so
# this is always safe to strip (the backslash), leaving the quote for the existing
# ascii_quote_to_gershayim/ascii_apostrophe_to_geresh passes to normalize properly.
_STRAY_BACKSLASH_QUOTE_RE = re.compile(r"\\([\"'])")

# A sentence-final period (or comma/colon/semicolon) directly after a closing bracket/paren with a
# stray space in between (e.g. ``"...כטב\"מים [1, 5, 6] ."``) -- collapse the space so the mark
# hugs the bracket the way normal punctuation does. Scoped to *right after* `)`/`]`/`"` specifically
# (never touches a mid-sentence space) so it can never merge two otherwise-unrelated words.
_SPACE_BEFORE_CLOSING_PUNCT_RE = re.compile(r'([\]\)"])\s+([.,;:!?])')

# Round 16 (docs/qa/content_review/BIDI-REPORTS.md, "ב- %75" finding): a percent sign that landed
# *before* its number -- with or without a stray space in between -- is always wrong reading order
# in both Hebrew and English prose (the sign always follows the number: "75%", never "%75"). This
# only ever shows up as a genuine text-level defect (a stray artifact from upstream extraction, or
# an LLM draft quirk); it is never legitimate content, so unconditionally safe to normalize. Scoped
# to *immediately* before a digit run so it can never misfire on an unrelated ``%`` elsewhere in a
# sentence.
_PERCENT_BEFORE_NUMBER_RE = re.compile(r"%\s*(\d+(?:\.\d+)?)")

# Round 16: a Hebrew single-letter grammatical prefix's maqaf hyphen (ב-, כ-, ל-, מ-, ש-, ו-, ה-,
# e.g. "ב-75%" = "at 75%") binds directly to the token that follows it with *no* space -- a stray
# space there (e.g. "ב- 75%") is always a defect, never legitimate Hebrew (the maqaf construction is
# specifically the zero-space form; a genuine word-initial "-" with a following space would just be
# a dash, not this prefix). Scoped to the single-letter-prefix set specifically (not every Hebrew
# letter) so it never touches an unrelated hyphenated compound.
_HEBREW_PREFIX_HYPHEN_SPACE_RE = re.compile(r"([בכלמשוה]-)\s+(?=[0-9A-Za-z])")


def fix_percent_sign_order(text: str) -> str:
    """Swap a percent sign that landed before its number back to the correct ``NUMBER%`` order,
    dropping any stray space between them -- see :data:`_PERCENT_BEFORE_NUMBER_RE`. E.g. ``"%75"``
    -> ``"75%"``, ``"% 75"`` -> ``"75%"``."""
    if not text:
        return text
    return _PERCENT_BEFORE_NUMBER_RE.sub(r"\1%", text)


def collapse_space_after_hebrew_prefix_hyphen(text: str) -> str:
    """Collapse a stray space between a Hebrew single-letter prefix's maqaf hyphen and the
    digit/Latin token it binds to -- see :data:`_HEBREW_PREFIX_HYPHEN_SPACE_RE`. E.g. ``"ב- 75%"``
    -> ``"ב-75%"``."""
    if not text:
        return text
    return _HEBREW_PREFIX_HYPHEN_SPACE_RE.sub(r"\1", text)


def _is_hebrew_char(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _HEBREW_CODEPOINT_RANGES)


def collapse_doubled_quotes(text: str) -> str:
    """Collapse any run of two-or-more literal ``"`` characters into a single ``"``."""
    if not text:
        return text
    return _DOUBLED_QUOTE_RE.sub('"', text)


def strip_bidi_isolates(text: str) -> str:
    """Remove embedded LRI/RLI/FSI/PDI bidi-isolate control characters (U+2066-U+2069) -- see the
    module-level note above :data:`_BIDI_ISOLATE_RE`. A no-op (returns ``text`` unchanged) for text
    that carries none, so it is always safe to call."""
    if not text:
        return text
    return _BIDI_ISOLATE_RE.sub("", text)


def unescape_stray_backslash_quotes(text: str) -> str:
    """Drop a literal backslash sitting directly before a ``"``/``'`` (an unescaped JSON/string
    escape sequence that leaked into display text), leaving the quote mark itself for the
    gershayim/geresh passes below to normalize."""
    if not text:
        return text
    return _STRAY_BACKSLASH_QUOTE_RE.sub(r"\1", text)


def collapse_space_before_closing_punctuation(text: str) -> str:
    """Collapse a stray space between a closing bracket/paren/quote and the sentence punctuation
    that immediately follows it (e.g. ``"[1, 5, 6] ."`` -> ``"[1, 5, 6]."``)."""
    if not text:
        return text
    return _SPACE_BEFORE_CLOSING_PUNCT_RE.sub(r"\1\2", text)


def trim_at_word_boundary(text: str, max_chars: int, *, suffix: str = "…") -> str:
    """Cap ``text`` at ``max_chars``, cutting at the nearest preceding word boundary (space) so a
    trimmed value never ends mid-word -- unlike a bare ``text[:n] + "…"`` slice, which routinely
    cuts through a word (or even through an internal ``[item N]`` marker). ``text`` at or under the
    cap is returned unchanged (no suffix appended). ``suffix`` is appended once, after trimming
    trailing whitespace/punctuation from the cut point."""
    if not text or len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last_space = cut.rfind(" ")
    if last_space > max_chars * 0.6:
        cut = cut[:last_space]
    return cut.rstrip(" ,;:.\u2013\u2014") + suffix


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
    """Apply every normalisation pass, in order, idempotently.

    Round-14 (CR-editing.md): extended from the original three quote-only passes to also strip
    stray backslash-escapes and embedded bidi-isolate control characters, and to collapse the
    stray space they and similar leaks left before closing sentence punctuation -- run first (in
    that order) since the quote/gershayim passes below reason about *adjacent* characters, which
    only makes sense once the backslash/isolate noise between them is gone.

    Round-16 (BIDI-REPORTS.md): also repairs a percent sign that landed before its number and a
    stray space after a Hebrew prefix's maqaf hyphen (``fix_percent_sign_order``,
    ``collapse_space_after_hebrew_prefix_hyphen``) -- narrowly scoped source-text defenses, run
    last since they reason about digits/Latin letters the earlier passes don't touch. Deliberately
    *not* included: a blanket "insert a space at every bare Hebrew/Latin boundary" pass -- checked
    against the actual report corpus (docs/qa/content_review/BIDI-REPORTS.md) and rejected, because
    a single-letter Hebrew conjunction/prefix (most commonly ו-, "and") *correctly* glues directly
    to a following Latin/digit token with zero space per ordinary Hebrew grammar (e.g. "וH04N5" =
    "and H04N5", a real patent-code citation already in the corpus) -- a blanket rule would corrupt
    that. The actual "3דולר"-style glued-word bug this round investigated turned out to live in the
    HTML/DOCX *rendering* layer (an isolate boundary swallowing a real space that was already
    correctly present in the source -- see ``docx_builder.split_runs``), not in the source text
    itself, so no general source-level spacing pass was needed to fix it.

    ``None``/empty input is returned unchanged (falsy short-circuit), so this is always safe to
    call on an optional field without a separate ``is None`` check at the call site.
    """
    if not text:
        return text
    text = unescape_stray_backslash_quotes(text)
    text = strip_bidi_isolates(text)
    text = collapse_doubled_quotes(text)
    text = ascii_quote_to_gershayim(text)
    text = ascii_apostrophe_to_geresh(text)
    text = collapse_space_before_closing_punctuation(text)
    text = fix_percent_sign_order(text)
    text = collapse_space_after_hebrew_prefix_hyphen(text)
    return text


# --------------------------------------------------------------------------
# Hebrew company-name canonicalisation (round-17, 2026-09-17 -- feedback: "ראפאל" instead of
# "רפאל" in generated reports). LLM output transliterates company names freely and nothing
# canonicalised the result before this. Driven by the `hebrew_names` registry in
# config/company_facts.yaml (schema documented in that file's own header comment): each entry
# names ONE canonical Hebrew spelling (`name_he`) plus known misspellings/alternate
# transliterations (`variants_he`) to fold onto it.
#
# Unlike :func:`normalize_hebrew_punctuation` (display-only, never touches a raw DB field at
# rest), :func:`canonicalize_hebrew_names` is also called explicitly at pipeline write time
# (`eoa.pipeline.analyze`, `eoa.pipeline.classify`) -- a misspelled company name is a factual
# error, not a punctuation/display nicety, so it is worth fixing in the row itself, not just its
# rendered copy. It is ALSO wired into the report-rendering path via :func:`normalize_report_text`
# below (used by :func:`normalize_draft` and every report kind's own draft-normalisation hook) and
# via `eoa.report.docx_builder`'s three renderers (`canonicalize_hebrew_names_deep` over
# `tables`), so already-persisted text that predates this pass, or text that never goes through
# the pipeline write path at all (deterministic table rows built straight from other DB columns),
# still renders correctly.
# --------------------------------------------------------------------------

#: Hebrew single-letter grammatical prefixes that glue directly onto a following word with zero
#: space (ו/ב/ל/מ/ש/ה/כ) -- same set as :data:`_HEBREW_PREFIX_HYPHEN_SPACE_RE` above. A variant
#: match is allowed to carry 0-2 of these immediately before it (e.g. "ולראפאל" = "and to Rafael")
#: without losing the match; the prefix run is preserved verbatim in the replacement.
_HEBREW_NAME_PREFIX_CHARS = "ובלמשהכ"

_HEBREW_LETTER_CLASS = "א-ת"

#: A literal URL or a ``<bdi>...</bdi>`` span (an already-isolated Latin/URL run some HTML-stage
#: text may carry) is never touched -- a Hebrew company-name variant cannot legitimately occur
#: inside either.
_URL_RE = re.compile(r"https?://\S+")
_BDI_SPAN_RE = re.compile(r"<bdi[^>]*>.*?</bdi>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class _HebrewNameRule:
    name_he: str
    variant: str
    pattern: re.Pattern[str]


@functools.lru_cache(maxsize=1)
def _hebrew_name_rules() -> tuple[_HebrewNameRule, ...]:
    """One compiled rule per (`hebrew_names` entry, variant) pair, longest variant text first (so
    a longer variant is tried before a shorter one that happens to be its own prefix could shadow
    it). Each pattern matches an optional 0-2 run of :data:`_HEBREW_NAME_PREFIX_CHARS` immediately
    followed by the variant text, anchored on both sides against an adjacent Hebrew letter (so it
    never fires mid-word: neither as the tail of a longer prefix run nor as the head of a longer,
    unrelated Hebrew word) -- lazily built once and cached, mirroring
    ``eoa.pipeline.analysis_grounding._company_facts_index``'s own lazy-import-of-settings pattern
    to avoid a hard import-time dependency between ``eoa.report`` and ``eoa.config``.
    """
    from eoa.config import settings

    entries = (settings().company_facts or {}).get("hebrew_names") or []
    rules: list[_HebrewNameRule] = []
    for entry in entries:
        name_he = (entry.get("name_he") or "").strip()
        if not name_he:
            continue
        for variant in entry.get("variants_he") or []:
            variant = (variant or "").strip()
            if not variant or variant == name_he:
                continue
            pattern = re.compile(
                r"(?<![" + _HEBREW_LETTER_CLASS + r"])"
                r"([" + _HEBREW_NAME_PREFIX_CHARS + r"]{0,2})"
                + re.escape(variant)
                + r"(?![" + _HEBREW_LETTER_CLASS + r"])"
            )
            rules.append(_HebrewNameRule(name_he=name_he, variant=variant, pattern=pattern))
    rules.sort(key=lambda r: len(r.variant), reverse=True)
    return tuple(rules)


def _canonicalize_hebrew_names_segment(segment: str, rules: tuple[_HebrewNameRule, ...]) -> str:
    if not segment:
        return segment
    for rule in rules:
        if rule.variant not in segment:
            continue  # cheap pre-check before paying for the regex substitution
        segment = rule.pattern.sub(lambda m, name_he=rule.name_he: m.group(1) + name_he, segment)
    return segment


def canonicalize_hebrew_names(text: str | None) -> str | None:
    """Fold a known Hebrew misspelling/alternate transliteration of a tracked company name onto
    its canonical spelling (e.g. ``"ראפאל"`` -> ``"רפאל"``), per the ``hebrew_names`` registry in
    config/company_facts.yaml.

    Word-boundary aware for Hebrew: a single-letter grammatical prefix (ו/ב/ל/מ/ש/ה/כ) glued
    directly onto the company name is preserved (``"וראפאל"`` -> ``"ורפאל"``, ``"לראפאל"`` ->
    ``"לרפאל"``), while a variant that is itself just the head of a longer, unrelated Hebrew word
    (or the tail of one) is left untouched. Never rewrites text inside a ``<bdi>...</bdi>`` span or
    a literal URL. Idempotent -- a canonical spelling is never also registered as a variant of
    itself (enforced when the rule table is built), so re-running this on already-canonical text
    is always a no-op. ``None``/empty input is returned unchanged.
    """
    if not text:
        return text
    rules = _hebrew_name_rules()
    if not rules:
        return text

    protected = sorted([*_BDI_SPAN_RE.finditer(text), *_URL_RE.finditer(text)], key=lambda m: m.start())
    if not protected:
        return _canonicalize_hebrew_names_segment(text, rules)

    out: list[str] = []
    pos = 0
    for m in protected:
        start, end = m.span()
        if start < pos:
            continue  # overlapping span (a URL that already sits inside a matched <bdi> span)
        out.append(_canonicalize_hebrew_names_segment(text[pos:start], rules))
        out.append(text[start:end])
        pos = end
    out.append(_canonicalize_hebrew_names_segment(text[pos:], rules))
    return "".join(out)


def canonicalize_hebrew_names_deep(value: Any) -> Any:
    """Recursively apply :func:`canonicalize_hebrew_names` to every string leaf of a JSON-shaped
    value (``dict``/``list``/``str``/other, unchanged otherwise) -- used for report ``tables``
    payloads (``eoa.report.docx_builder``'s ``build_docx``/``render_markdown``/``render_html``),
    where company-name-bearing text can sit at any depth (row cells, captions, note fields) rather
    than at the small set of fixed pydantic-model fields :func:`normalize_draft` walks."""
    if isinstance(value, str):
        return canonicalize_hebrew_names(value) or value
    if isinstance(value, list):
        return [canonicalize_hebrew_names_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: canonicalize_hebrew_names_deep(v) for k, v in value.items()}
    return value


def normalize_report_text(text: str | None) -> str | None:
    """The one function report renderers should call on free Hebrew prose: punctuation
    normalisation (:func:`normalize_hebrew_punctuation`) followed by company-name canonicalisation
    (:func:`canonicalize_hebrew_names`). Kept as two composed, independently-testable passes rather
    than folded into :func:`normalize_hebrew_punctuation` itself, since that function's contract is
    deliberately punctuation-only and also used at spots (e.g. ``eoa.tenders.forecast``'s own,
    differently-scoped ``normalize_hebrew_punctuation``) that have nothing to do with company
    names. ``None``/empty input is returned unchanged."""
    return canonicalize_hebrew_names(normalize_hebrew_punctuation(text))


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
            normalized = normalize_report_text(value)
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
        updates["exec_summary_he"] = normalize_report_text(draft.exec_summary_he) or ""

    # market_bullets_he: list[str] (legacy: bd_territory only, harmless no-op elsewhere)
    market_bullets = getattr(draft, "market_bullets_he", None)
    if isinstance(market_bullets, list) and market_bullets:
        updates["market_bullets_he"] = [normalize_report_text(b) or "" for b in market_bullets]

    # sections: list[StructuredSection] (structured, .sentences) or list[ReportSection] (legacy,
    # .prose_he) -- both also carry a title_he.
    sections = getattr(draft, "sections", None)
    if isinstance(sections, list) and sections:
        new_sections = []
        for section in sections:
            sec_updates: dict[str, Any] = {}
            if hasattr(section, "title_he"):
                sec_updates["title_he"] = normalize_report_text(section.title_he) or ""
            if hasattr(section, "sentences"):
                sec_updates["sentences"] = [_normalize_str_fields(s, ("text_he",)) for s in section.sentences]
            if hasattr(section, "prose_he"):
                sec_updates["prose_he"] = normalize_report_text(section.prose_he) or ""
            new_sections.append(section.model_copy(update=sec_updates) if sec_updates else section)
        updates["sections"] = new_sections

    # trends: list[WeeklyTrendSection] (weekly only)
    trends = getattr(draft, "trends", None)
    if isinstance(trends, list) and trends:
        new_trends = []
        for trend in trends:
            t_updates: dict[str, Any] = {"title_he": normalize_report_text(trend.title_he) or ""}
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
        updates["system_note_he"] = normalize_report_text(draft.system_note_he) or ""

    # analyst_note_he: AnalystNote | None
    note = getattr(draft, "analyst_note_he", None)
    if note is not None and hasattr(note, "sentences_he"):
        updates["analyst_note_he"] = note.model_copy(
            update={"sentences_he": [normalize_report_text(s) or "" for s in note.sentences_he]}
        )

    # outlook: list[OutlookIndicator] (structured)
    outlook = getattr(draft, "outlook", None)
    if isinstance(outlook, list) and outlook:
        updates["outlook"] = [_normalize_str_fields(o, ("text_he",)) for o in outlook]

    # outlook_he: str (legacy)
    if hasattr(draft, "outlook_he"):
        updates["outlook_he"] = normalize_report_text(draft.outlook_he) or ""

    # risks_assumptions_he: str (bd_territory, legacy; harmless no-op elsewhere)
    if hasattr(draft, "risks_assumptions_he"):
        updates["risks_assumptions_he"] = normalize_report_text(draft.risks_assumptions_he) or ""

    # open_points_he: list[str] (both shapes)
    open_points = getattr(draft, "open_points_he", None)
    if isinstance(open_points, list) and open_points:
        updates["open_points_he"] = [normalize_report_text(p) or "" for p in open_points]

    return draft.model_copy(update=updates) if updates else draft
