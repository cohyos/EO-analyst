"""Deterministic executive-summary style guard (Round-4b W26, docs/REVIEW_2026-09-06_evening.md).

The user's complaint (W26): report exec-summary/section prose reads badly -- padded with generic
analyst filler ("יש לציין", "חשוב להדגיש", "בהקשר זה", ...) that adds no decision-relevant
information. `deep_search_system.md`'s sibling fix (W27) is a full re-assembly of `answer_he`; this
module is narrower and deliberately more conservative, because report prose lives inside
`Sentence.cites`-tracked objects (`agent/eoa/llm/schemas/analysis.py`) where CONVENTIONS.md rule 4
("provenance everywhere") makes rewriting text riskier than stripping it:

- :func:`strip_filler_phrases` is a **safe, deterministic removal** -- every phrase in
  :data:`BANNED_FILLER_PHRASES_HE` is pure padding with no factual content, so deleting it (and
  cleaning up the resulting whitespace/dangling punctuation) can never change what a sentence
  claims or orphan its ``cites``.
- Sentence-length (>20 words), "one idea per sentence", and "no sentence repeated across sections"
  are **detection-only**: this module never splits, merges, or rewrites a sentence to fix these,
  because doing so safely would require re-deriving which `cites` apply to which half of a split
  sentence -- exactly the kind of silent-rewrite risk CONVENTIONS.md rule 4 warns against. Instead
  :func:`apply_style_guard` collects every violation into a :class:`StyleReport` that the caller
  logs (`structlog`, matching CONVENTIONS.md rule 8) for QA follow-up; report prompts
  (`report_daily.md`/`report_weekly.md`/`report_bd_territory.md`) carry the actual writing rules
  the model is asked to follow, this module is the deterministic backstop that catches what slips
  through.

Traversal is duck-typed across every report draft shape in this codebase (structured
`Sentence`/`StructuredSection`/`OutlookIndicator`/`AnalystNote` objects used by daily/weekly/
bd_territory, and the legacy free-prose `*_he` string fields used by monthly) via `getattr`/
`hasattr`, mirroring `eoa.report.textnorm.normalize_draft`'s own traversal exactly (same field
names, same shapes) so a field neither module recognizes is simply skipped rather than erroring.

NOT wired into `eoa.report.daily`/`weekly`/`bd_territory`/`monthly` by this change (out of this
round's file scope -- those report builders belong to other engineers' concurrent work this round);
call :func:`apply_style_guard` from those pipelines' draft-QA step, the same point
`textnorm.normalize_draft` is already called from, to actually put this guard into effect.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------------------------
# W26: curated Hebrew analyst-filler phrases -- generic hedges/throat-clearing that carry no
# decision-relevant information on their own. Not an exhaustive list of "bad Hebrew style" (that
# would need an LLM judge, not a deterministic gate) -- just the concrete, safe-to-delete-outright
# phrases the user named plus their common close variants seen in this project's own report output.
# ---------------------------------------------------------------------------------------------
BANNED_FILLER_PHRASES_HE: tuple[str, ...] = (
    "יש לציין כי",
    "יש לציין ש",
    "יש לציין",
    "חשוב להדגיש כי",
    "חשוב להדגיש ש",
    "חשוב להדגיש",
    "בהקשר זה",
    "ראוי לציין כי",
    "ראוי לציין",
    "יש לזכור כי",
    "יש לזכור",
    "כפי שניתן לראות",
    "כפי שצוין לעיל",
    "כאמור לעיל",
    "יצוין כי",
    "נציין כי",
    "מן הראוי לציין",
    "בהתייחס לכך",
    "במסגרת זו",
    "ניתן לומר כי",
    "ניתן לציין כי",
    "באופן כללי ניתן לומר",
)  # fmt: skip

#: W26: "≤20 words" -- a sentence longer than this is flagged (not rewritten) for QA follow-up.
MAX_WORDS_PER_SENTENCE_HE = 20

_MULTI_SPACE_RE = re.compile(r" {2,}")
_LEADING_JUNK_RE = re.compile(r"^\s*[,:;]+\s*")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,:;)\]])")
_DOUBLE_PUNCT_RE = re.compile(r"([.,:;])\1+")


def _build_filler_pattern(phrases: tuple[str, ...]) -> re.Pattern[str]:
    """Longest-phrase-first so e.g. "יש לציין כי" matches whole before the shorter "יש לציין"
    substring would otherwise consume part of it and leave a dangling "כי"."""
    ordered = sorted(set(phrases), key=len, reverse=True)
    return re.compile("|".join(re.escape(p) for p in ordered))


_FILLER_RE = _build_filler_pattern(BANNED_FILLER_PHRASES_HE)


def strip_filler_phrases(text: str) -> tuple[str, list[str]]:
    """Remove every banned filler phrase from ``text``, cleaning up the whitespace/dangling
    punctuation left behind. Returns ``(cleaned_text, phrases_removed)`` -- ``phrases_removed`` is
    empty (and ``cleaned_text is text``) when nothing matched, so this is always a safe no-op call.
    """
    if not text:
        return text, []
    removed: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        removed.append(m.group(0))
        return " "

    cleaned = _FILLER_RE.sub(_sub, text)
    if not removed:
        return text, []
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned)
    cleaned = _LEADING_JUNK_RE.sub("", cleaned)
    cleaned = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = _DOUBLE_PUNCT_RE.sub(r"\1", cleaned)
    return cleaned.strip(), removed


def check_sentence_length_he(text: str) -> int | None:
    """Word count if ``text`` exceeds :data:`MAX_WORDS_PER_SENTENCE_HE`, else ``None``.

    Deliberately simple (whitespace split) -- this is a detection-only heuristic for QA follow-up,
    never a basis for automatically splitting/rewriting a sentence (see module docstring)."""
    if not text:
        return None
    n = len(text.split())
    return n if n > MAX_WORDS_PER_SENTENCE_HE else None


def _normalize_for_dup_check(text: str) -> str:
    return _MULTI_SPACE_RE.sub(" ", (text or "").strip().casefold())


# ---------------------------------------------------------------------------------------------
# Draft-wide guard: strips filler everywhere, flags long/duplicate sentences, never rewrites.
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StyleViolation:
    field: str
    kind: str  # "filler_removed" | "too_long" | "duplicate_sentence"
    detail: str


@dataclass
class StyleReport:
    violations: list[StyleViolation] = field(default_factory=list)

    def add(self, field_name: str, kind: str, detail: str) -> None:
        self.violations.append(StyleViolation(field=field_name, kind=kind, detail=detail))

    @property
    def filler_removed(self) -> list[StyleViolation]:
        return [v for v in self.violations if v.kind == "filler_removed"]

    @property
    def too_long(self) -> list[StyleViolation]:
        return [v for v in self.violations if v.kind == "too_long"]

    @property
    def duplicate_sentences(self) -> list[StyleViolation]:
        return [v for v in self.violations if v.kind == "duplicate_sentence"]

    def log_all(self, *, report_kind: str = "", job_id: int | None = None) -> None:
        """Emit one structlog event per violation (CONVENTIONS.md rule 8: structlog, bind job_id
        where available) -- call once after :func:`apply_style_guard` returns."""
        for v in self.violations:
            log.info(
                "report_style_guard_violation",
                report_kind=report_kind,
                job_id=job_id,
                field=v.field,
                kind=v.kind,
                detail=v.detail[:200],
            )


def _clean_field(text: str, field_name: str, report: StyleReport) -> str:
    cleaned, removed = strip_filler_phrases(text)
    for phrase in removed:
        report.add(field_name, "filler_removed", phrase)
    n_words = check_sentence_length_he(cleaned)
    if n_words is not None:
        report.add(field_name, "too_long", f"{n_words} words: {cleaned[:120]}")
    return cleaned


def _check_duplicates(seen: dict[str, str], field_name: str, text: str, report: StyleReport) -> None:
    key = _normalize_for_dup_check(text)
    if not key:
        return
    if key in seen:
        report.add(field_name, "duplicate_sentence", f"same as {seen[key]}: {text[:120]}")
    else:
        seen[key] = field_name


_M = TypeVar("_M")


def apply_style_guard(
    draft: _M, *, report_kind: str = "", job_id: int | None = None
) -> tuple[_M, StyleReport]:
    """Strip banned filler from every ``*_he`` text field a report draft renders, and flag
    (never fix) over-length or duplicate-across-sections sentences.

    Duck-typed across both draft shapes in this codebase -- same field names/traversal as
    :func:`eoa.report.textnorm.normalize_draft` (see its own docstring), so a field a given
    ``draft`` doesn't carry is simply skipped. Returns ``(possibly-updated draft, StyleReport)``;
    call ``report.log_all(...)`` on the result if the caller wants the violations logged.
    """
    report = StyleReport()
    seen: dict[str, str] = {}
    updates: dict[str, Any] = {}

    def clean_sentence(s: Any, field_name: str) -> Any:
        text = getattr(s, "text_he", None)
        if not isinstance(text, str) or not text:
            return s
        cleaned = _clean_field(text, field_name, report)
        _check_duplicates(seen, field_name, cleaned, report)
        return s.model_copy(update={"text_he": cleaned}) if cleaned != text else s

    # exec_summary: list[Sentence] (structured) -- always exists on daily/weekly/bd_territory.
    exec_summary = getattr(draft, "exec_summary", None)
    if isinstance(exec_summary, list) and exec_summary:
        updates["exec_summary"] = [clean_sentence(s, "exec_summary") for s in exec_summary]

    # exec_summary_he: str (legacy: monthly/bd_territory-old)
    if hasattr(draft, "exec_summary_he") and isinstance(draft.exec_summary_he, str) and draft.exec_summary_he:
        cleaned = _clean_field(draft.exec_summary_he, "exec_summary_he", report)
        updates["exec_summary_he"] = cleaned

    # market_bullets: list[Sentence] (bd_territory, current structured schema)
    market_bullets = getattr(draft, "market_bullets", None)
    if isinstance(market_bullets, list) and market_bullets:
        updates["market_bullets"] = [clean_sentence(s, "market_bullets") for s in market_bullets]

    # market_bullets_he: list[str] (bd_territory legacy)
    market_bullets_he = getattr(draft, "market_bullets_he", None)
    if isinstance(market_bullets_he, list) and market_bullets_he:
        new_list = []
        for b in market_bullets_he:
            cleaned = _clean_field(b, "market_bullets_he", report) if isinstance(b, str) else b
            _check_duplicates(seen, "market_bullets_he", cleaned, report)
            new_list.append(cleaned)
        updates["market_bullets_he"] = new_list

    # competitor_moves: list[Sentence] (bd_territory)
    competitor_moves = getattr(draft, "competitor_moves", None)
    if isinstance(competitor_moves, list) and competitor_moves:
        updates["competitor_moves"] = [clean_sentence(s, "competitor_moves") for s in competitor_moves]

    # sections: list[StructuredSection] (.sentences) or list[ReportSection] (.prose_he)
    sections = getattr(draft, "sections", None)
    if isinstance(sections, list) and sections:
        new_sections = []
        for section in sections:
            sec_updates: dict[str, Any] = {}
            title = getattr(section, "title_he", "") or "?"
            field_label = f"section[{title}]"
            if hasattr(section, "sentences"):
                sec_updates["sentences"] = [clean_sentence(s, field_label) for s in section.sentences]
            if hasattr(section, "prose_he") and isinstance(section.prose_he, str) and section.prose_he:
                sec_updates["prose_he"] = _clean_field(section.prose_he, field_label, report)
            new_sections.append(section.model_copy(update=sec_updates) if sec_updates else section)
        updates["sections"] = new_sections

    # trends: list[WeeklyTrendSection] (weekly only)
    trends = getattr(draft, "trends", None)
    if isinstance(trends, list) and trends:
        new_trends = []
        for trend in trends:
            title = getattr(trend, "title_he", "") or "?"
            field_label = f"trend[{title}]"
            t_updates: dict[str, Any] = {}
            if hasattr(trend, "sentences"):
                t_updates["sentences"] = [clean_sentence(s, field_label) for s in trend.sentences]
            new_trends.append(trend.model_copy(update=t_updates) if t_updates else trend)
        updates["trends"] = new_trends

    # trend_paragraphs: list[TrendParagraph] (monthly, legacy, .prose_he)
    trend_paragraphs = getattr(draft, "trend_paragraphs", None)
    if isinstance(trend_paragraphs, list) and trend_paragraphs:
        new_tp = []
        for tp in trend_paragraphs:
            title = getattr(tp, "title_he", "") or "?"
            field_label = f"trend_paragraph[{title}]"
            prose = getattr(tp, "prose_he", None)
            if isinstance(prose, str) and prose:
                cleaned = _clean_field(prose, field_label, report)
                new_tp.append(tp.model_copy(update={"prose_he": cleaned}) if cleaned != prose else tp)
            else:
                new_tp.append(tp)
        updates["trend_paragraphs"] = new_tp

    # recommended_actions: list[BdRecommendedAction] (.action_he str, .rationale list[Sentence]) or
    # list[BdAction] (legacy: .action_he str, .rationale_he str)
    recommended_actions = getattr(draft, "recommended_actions", None)
    if isinstance(recommended_actions, list) and recommended_actions:
        new_actions = []
        for i, action in enumerate(recommended_actions):
            field_label = f"recommended_actions[{i}]"
            a_updates: dict[str, Any] = {}
            action_he = getattr(action, "action_he", None)
            if isinstance(action_he, str) and action_he:
                cleaned = _clean_field(action_he, f"{field_label}.action_he", report)
                if cleaned != action_he:
                    a_updates["action_he"] = cleaned
            if hasattr(action, "rationale") and isinstance(action.rationale, list):
                a_updates["rationale"] = [
                    clean_sentence(s, f"{field_label}.rationale") for s in action.rationale
                ]
            rationale_he = getattr(action, "rationale_he", None)
            if isinstance(rationale_he, str) and rationale_he:
                cleaned = _clean_field(rationale_he, f"{field_label}.rationale_he", report)
                if cleaned != rationale_he:
                    a_updates["rationale_he"] = cleaned
            new_actions.append(action.model_copy(update=a_updates) if a_updates else action)
        updates["recommended_actions"] = new_actions

    # analyst_note_he: AnalystNote | None
    note = getattr(draft, "analyst_note_he", None)
    if note is not None and hasattr(note, "sentences_he"):
        new_sentences = []
        for s in note.sentences_he:
            cleaned = _clean_field(s, "analyst_note_he", report) if isinstance(s, str) else s
            new_sentences.append(cleaned)
        updates["analyst_note_he"] = note.model_copy(update={"sentences_he": new_sentences})

    # outlook: list[OutlookIndicator] (structured)
    outlook = getattr(draft, "outlook", None)
    if isinstance(outlook, list) and outlook:
        updates["outlook"] = [clean_sentence(o, "outlook") for o in outlook]

    # outlook_he: str (legacy)
    if hasattr(draft, "outlook_he") and isinstance(draft.outlook_he, str) and draft.outlook_he:
        updates["outlook_he"] = _clean_field(draft.outlook_he, "outlook_he", report)

    # risks_assumptions_he: str (bd_territory) -- free text, exempt from the cites discipline
    # already per report_bd_territory.md, so safe to clean the same way.
    if (
        hasattr(draft, "risks_assumptions_he")
        and isinstance(draft.risks_assumptions_he, str)
        and draft.risks_assumptions_he
    ):
        updates["risks_assumptions_he"] = _clean_field(
            draft.risks_assumptions_he, "risks_assumptions_he", report
        )

    # open_points_he: list[str] (both shapes) -- not sentence-cited prose, but still analyst text;
    # filler-stripped for consistency, not checked for cross-field duplication (points are meant to
    # be distinct questions, not summary sentences that might legitimately restate a fact).
    open_points = getattr(draft, "open_points_he", None)
    if isinstance(open_points, list) and open_points:
        updates["open_points_he"] = [
            _clean_field(p, "open_points_he", report) if isinstance(p, str) and p else p for p in open_points
        ]

    updated = draft.model_copy(update=updates) if updates else draft
    return updated, report


# ---------------------------------------------------------------------------------------------
# Round-14 (CR-editing.md, "repeated sentences across sections"): `apply_style_guard` above is
# deliberately detection-only for duplicates (see the module docstring's CONVENTIONS.md rule 4
# rationale -- rewriting/splitting a sentence risks orphaning its `cites`). This pass is narrower
# and safe to actually apply: it only ever *drops* a whole `Sentence` object whose `text_he` is
# byte-identical (after whitespace/case normalization) to one already kept earlier in the draft --
# never rewrites, merges, or touches a near-duplicate. Dropping an exact repeat can never orphan a
# claim's citations (the earlier, surviving occurrence already carries the identical `cites` for
# the identical sentence) and never leaves a section/trend with zero sentences (the first sentence
# in a collection is always kept even if it duplicates an earlier one, rather than emptying the
# collection -- see :func:`dedupe_exact_sentences_across_sections`'s own docstring).
# ---------------------------------------------------------------------------------------------


def dedupe_exact_sentences_across_sections(draft: _M) -> tuple[_M, int]:
    """Drop a later exact-duplicate ``Sentence`` (same normalized ``text_he``) that repeats one
    already kept earlier in reading order: ``bluf`` -> ``exec_summary`` -> ``sections[].sentences``
    -> ``trends[].sentences`` (weekly/monthly only) -> ``outlook``. Legacy free-prose ``*_he`` string fields
    (monthly/bd_territory-old) are left untouched -- slicing a sentence out of a paragraph string
    is exactly the unsafe rewrite this function's narrower, structured-``Sentence``-only scope
    avoids (:func:`apply_style_guard`'s detection-only duplicate check still covers that shape).

    A collection (a section's/trend's own ``sentences`` list, or ``outlook``) never ends up empty
    because of this pass: its first sentence is always kept, even when it repeats something from an
    earlier collection, so no section goes blank as a side effect of deduping.

    Returns ``(possibly-updated draft, n_dropped)`` -- ``n_dropped == 0`` (draft returned as-is)
    when nothing repeated.
    """
    seen: set[str] = set()
    dropped = 0

    def _dedupe_list(sentences: list[Any]) -> list[Any]:
        # Two-phase, not a single pass: phase 1 filters out every exact repeat of a
        # already-``seen`` sentence; only *after* seeing the whole collection do we know whether
        # that left it non-empty. A single-pass "keep the first sentence in this collection
        # unconditionally" rule would also protect a repeat that happens to come first even when a
        # later, non-duplicate sentence in the same collection would have kept it non-empty anyway
        # (e.g. trend.sentences == [<repeat of an exec_summary sentence>, <a real, unique
        # sentence>] should still drop the repeat -- only the *all-of-it-is-a-repeat* case needs
        # the empty-collection guard).
        nonlocal dropped
        kept: list[Any] = []
        removed: list[Any] = []
        for s in sentences:
            text = getattr(s, "text_he", None)
            if not isinstance(text, str) or not text:
                kept.append(s)
                continue
            key = _normalize_for_dup_check(text)
            if not key:
                kept.append(s)
                continue
            if key in seen:
                removed.append(s)
                continue
            seen.add(key)
            kept.append(s)
        if not kept and sentences:
            # Every sentence in this collection was an exact repeat of something kept earlier --
            # restore the very first one rather than leave the collection (and its heading) with
            # nothing under it; that one sentence is no longer counted as "dropped".
            kept = [sentences[0]]
            removed = [r for r in removed if r is not sentences[0]]
        dropped += len(removed)
        return kept

    updates: dict[str, Any] = {}

    # bluf: list[Sentence] ("שורה תחתונה") -- read first by design (BLUF-first structure), so
    # processed first here too: a sentence repeated later in exec_summary/a section is what gets
    # dropped, never the BLUF line itself.
    bluf = getattr(draft, "bluf", None)
    if isinstance(bluf, list) and bluf:
        updates["bluf"] = _dedupe_list(bluf)

    exec_summary = getattr(draft, "exec_summary", None)
    if isinstance(exec_summary, list) and exec_summary:
        updates["exec_summary"] = _dedupe_list(exec_summary)

    sections = getattr(draft, "sections", None)
    if isinstance(sections, list) and sections:
        new_sections = []
        for section in sections:
            if hasattr(section, "sentences") and isinstance(section.sentences, list):
                new_sentences = _dedupe_list(section.sentences)
                new_sections.append(
                    section.model_copy(update={"sentences": new_sentences})
                    if new_sentences != section.sentences
                    else section
                )
            else:
                new_sections.append(section)
        updates["sections"] = new_sections

    trends = getattr(draft, "trends", None)
    if isinstance(trends, list) and trends:
        new_trends = []
        for trend in trends:
            if hasattr(trend, "sentences") and isinstance(trend.sentences, list):
                new_sentences = _dedupe_list(trend.sentences)
                new_trends.append(
                    trend.model_copy(update={"sentences": new_sentences})
                    if new_sentences != trend.sentences
                    else trend
                )
            else:
                new_trends.append(trend)
        updates["trends"] = new_trends

    outlook = getattr(draft, "outlook", None)
    if isinstance(outlook, list) and outlook:
        updates["outlook"] = _dedupe_list(outlook)

    updated = draft.model_copy(update=updates) if updates else draft
    return updated, dropped
