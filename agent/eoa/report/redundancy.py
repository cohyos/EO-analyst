"""Stage: report/redundancy -- deterministic cross-section restatement guard.

Root cause (docs/qa/content_review/REPORT-REDUNDANCY.md, 2026-09-08 user feedback on the daily
report as shown on the morning page): "the report repeats the information overview needlessly;
the repetition does not advance the consumer of the information". A measured pass over
``output/reports/daily_2026-09-08.md``/``weekly_2026-09-07.md``/``monthly_2026-09-30.md`` found
the worst offenders were near-verbatim restatements of the SAME fact across BLUF, exec summary,
domain-review sections, trend sections, the analyst note, and deep-search key facts -- e.g. an
Ophir MWIR-zoom so-what sentence appearing byte-for-byte in both a deep-search investigation's
context and the "תעשייה ישראלית" table's "מה זה אומר" cell, or a domain-review sentence just
re-describing (different wording, same numbers/organisation) what the week's trend section
already said.

This module is a deterministic (no LLM), ADDITIVE pass over an already-drafted, already-
claims-gated report: it never rewrites a sentence's wording, only DROPS a lower-priority
sentence that restates one already kept in a higher-priority section, merging any new citation
numbers the dropped sentence carried into the surviving sentence. Section priority (highest
first, matching report reading order and where a first-time reader expects a fact to actually
live):

    BLUF > exec summary > trends (weekly/monthly) > domain review (``sections``) > analyst note

A ``StructuredSection``/``WeeklyTrendSection``/``MonthlyTrendSection`` that would lose every one
of its sentences to this pass keeps exactly one deterministic pointer sentence ("פורט בתקציר
המנהלים.") carrying the union of the emptied section's own original citations, rather than
rendering an empty/heading-only section or (for ``MonthlyTrendSection``) breaking that schema's
own "at least one sentence unless change='gone'" validator.

``analyst_note_he`` sentences carry no ``cites`` (goal 1's one deliberately-unsourced field) --
a redundant analyst-note sentence is simply dropped, nothing to merge.

Two further, narrower helpers used by the callers (``eoa.report.daily``/``weekly``/``monthly``,
narrative assembly only) rather than by this pass itself, since neither operates on the draft's
own pydantic fields:

* :func:`narrative_citation_numbers` -- the citation numbers already used by BLUF/exec summary,
  for ``eoa.report.deltas.render_delta_section_he`` to exclude an "already covered" new item from
  the "מה השתנה" bullet list (showing only the uncovered ones plus a count of the covered ones).
* :func:`filter_facts_against_narrative` -- drops a deep-search ``key_facts`` bullet that restates
  a sentence already kept somewhere in the narrative (same Jaccard/number+org test as the main
  pass), so "חקירות עומק" only ever adds facts the narrative did not already state.

Similarity test (the user's own spec): two sentences are considered the same restated fact when
EITHER (a) their word-level token-set Jaccard similarity is >= ``threshold`` (default 0.6), OR
(b) they share a "distinctive" number (not a bare calendar year or a generic single-digit count)
AND a recognizable organisation/entity token. (a) alone catches near-identical phrasing; (b) alone
catches the common case in this corpus of the SAME fact re-told in materially different Hebrew
wording but anchored to the same contract value/percentage and the same company -- see
``docs/qa/content_review/REPORT-REDUNDANCY.md`` for the worked examples that motivated (b).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# --------------------------------------------------------------------------
# similarity primitives
# --------------------------------------------------------------------------

_WORD_RE = re.compile(r"[0-9A-Za-z֐-׿]+")
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
_ORG_LATIN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]{2,}\b")
_CITATION_MARKER_RE = re.compile(r"\[\d+\]")

#: A short list of recurring Hebrew company/org names in this corpus that would otherwise be
#: invisible to :data:`_ORG_LATIN_RE` (Latin-script capitalization has no Hebrew equivalent) --
#: mirrors the same names ``eoa.report.israel_section``/``eoa.report.tech_watch`` already treat as
#: first-class Israeli-industry entities. Not exhaustive by design: a false negative here only
#: costs a missed near-duplicate merge (never a wrongly-dropped sentence), while a false positive
#: (a name too generic) would risk conflating two unrelated facts -- so the list stays short and
#: high-precision.
_ORG_HEBREW_HINTS = (
    "אלביט",
    "רפאל",
    "עין שלישית",
    "אלישרא",
    "תעשייה אווירית",
    "אקסון",
)

_STOPWORDS_HE_EN = {
    "של", "על", "עם", "אל", "כי", "לא", "גם", "או", "אם", "זה", "זו", "אך",
    "וכן", "כדי", "בין", "לפי", "אחר", "אחרי", "לפני", "כאשר", "מה", "מי",
    "אשר", "כל", "יש", "אין", "היה", "היו", "הוא", "היא", "הם", "הן", "בו",
    "בה", "להם", "לה", "לו", "אני", "אנו", "אנחנו",
    "the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "with",
    "is", "are", "was", "were",
}

#: Deterministic pointer sentence for a domain-review/trend section that this pass emptied out
#: entirely -- the reader is told where the (now-consolidated) fact actually lives.
POINTER_SENTENCE_HE = "פורט בתקציר המנהלים."

DEFAULT_THRESHOLD = 0.6


def _tokenize(text: str) -> set[str]:
    cleaned = _CITATION_MARKER_RE.sub(" ", text or "")
    toks = {w.lower() for w in _WORD_RE.findall(cleaned)}
    return toks - _STOPWORDS_HE_EN


def _distinctive_numbers(text: str) -> set[str]:
    """Numeric facts worth matching on: drops bare calendar years (2000-2099, endemic in a
    defense-news corpus) and generic single-digit counters ("5 פריטים חדשים", footnote indices) --
    both cause false-positive matches unrelated to the actual claim. See module docstring."""
    out: set[str] = set()
    for n in _NUMBER_RE.findall(text or ""):
        core = n.replace(",", "")
        if re.fullmatch(r"20\d\d", core):
            continue
        try:
            val = float(core)
        except ValueError:
            continue
        if "." not in core and val < 10:
            continue
        out.add(n)
    return out


def _orgs(text: str) -> set[str]:
    found = set(_ORG_LATIN_RE.findall(text or ""))
    for name in _ORG_HEBREW_HINTS:
        if name in (text or ""):
            found.add(name)
    return found


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


@dataclass(frozen=True)
class _SentenceMeta:
    tokens: frozenset[str]
    numbers: frozenset[str]
    orgs: frozenset[str]


def _meta(text: str) -> _SentenceMeta:
    return _SentenceMeta(
        tokens=frozenset(_tokenize(text)),
        numbers=frozenset(_distinctive_numbers(text)),
        orgs=frozenset(_orgs(text)),
    )


def is_redundant_text(a: str, b: str, *, threshold: float = DEFAULT_THRESHOLD) -> bool:
    """``True`` when ``a`` restates ``b`` (or vice versa) per the module's similarity test:
    token-set Jaccard >= ``threshold``, OR a shared distinctive number AND a shared org token."""
    ma, mb = _meta(a), _meta(b)
    if _jaccard(ma.tokens, mb.tokens) >= threshold:
        return True
    return bool(ma.numbers & mb.numbers) and bool(ma.orgs & mb.orgs)


# --------------------------------------------------------------------------
# running pool of already-kept sentences, in strict priority order
# --------------------------------------------------------------------------


@dataclass
class _PoolEntry:
    text: str
    meta: _SentenceMeta
    section_label: str
    sentence_obj: Any | None  # the kept Sentence, for citation merging -- None for plain strings


class _Pool:
    def __init__(self, threshold: float) -> None:
        self._threshold = threshold
        self._entries: list[_PoolEntry] = []

    def find_match(self, text: str, meta: _SentenceMeta) -> _PoolEntry | None:
        for entry in self._entries:
            ja = _jaccard(meta.tokens, entry.meta.tokens)
            if ja >= self._threshold or (bool(meta.numbers & entry.meta.numbers) and bool(meta.orgs & entry.meta.orgs)):
                return entry
        return None

    def add(self, text: str, *, section_label: str, sentence_obj: Any | None = None) -> None:
        self._entries.append(_PoolEntry(text=text, meta=_meta(text), section_label=section_label, sentence_obj=sentence_obj))

    @property
    def texts(self) -> list[str]:
        return [e.text for e in self._entries]


# --------------------------------------------------------------------------
# result
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DroppedSentence:
    section_label: str
    text_he: str
    kept_section_label: str
    kept_text_he: str


@dataclass
class RedundancyResult:
    dropped: list[DroppedSentence] = field(default_factory=list)
    pointer_sections: list[str] = field(default_factory=list)
    #: Final flattened pool of every kept narrative sentence (bluf/exec_summary/trends/sections/
    #: analyst_note), in priority order -- handed to :func:`filter_facts_against_narrative` by the
    #: caller for the deep-search bullets, and to :func:`narrative_citation_numbers`'s callers for
    #: the "מה השתנה" gate.
    kept_sentence_texts: list[str] = field(default_factory=list)

    @property
    def n_dropped(self) -> int:
        return len(self.dropped)

    def log_all(self, *, report_kind: str = "") -> None:
        for d in self.dropped:
            log.info(
                "report.redundant_sentence_dropped",
                report_kind=report_kind,
                dropped_section=d.section_label,
                kept_section=d.kept_section_label,
                text=d.text_he[:200],
                kept_text=d.kept_text_he[:200],
            )
        for label in self.pointer_sections:
            log.info("report.redundant_section_pointered", report_kind=report_kind, section=label)


# --------------------------------------------------------------------------
# main pass
# --------------------------------------------------------------------------


def _make_pointer_sentence(sentence_cls: type, original_sentences: list[Any]) -> Any:
    """A single deterministic replacement ``Sentence`` for a section this pass emptied out,
    carrying the union of the emptied sentences' own original citations (every ``Sentence`` this
    pass ever drops came from a schema requiring ``cites`` non-empty, so the union is always
    non-empty too -- see the schema's own ``Sentence.cites`` ``min_length=1``)."""
    cites: set[int] = set()
    for s in original_sentences:
        cites.update(getattr(s, "cites", None) or [])
    return sentence_cls(text_he=POINTER_SENTENCE_HE, cites=sorted(cites))


def _process_sentence_list(
    sentences: list[Any], *, section_label: str, pool: _Pool, result: RedundancyResult
) -> list[Any]:
    """Drop a redundant ``Sentence`` (merging its ``cites`` into the higher-priority match it
    restated), keep everything else, and add every kept sentence's text to ``pool``."""
    kept: list[Any] = []
    for sent in sentences:
        text = getattr(sent, "text_he", None)
        if not text:
            kept.append(sent)
            continue
        meta = _meta(text)
        match = pool.find_match(text, meta)
        if match is not None:
            new_cites = getattr(sent, "cites", None) or []
            if match.sentence_obj is not None and new_cites:
                merged = sorted(set(getattr(match.sentence_obj, "cites", []) or []) | set(new_cites))
                if merged != list(getattr(match.sentence_obj, "cites", []) or []):
                    match.sentence_obj.cites = merged
            result.dropped.append(
                DroppedSentence(
                    section_label=section_label,
                    text_he=text,
                    kept_section_label=match.section_label,
                    kept_text_he=match.text,
                )
            )
            continue
        kept.append(sent)
        pool.add(text, section_label=section_label, sentence_obj=sent)
    return kept


def apply_redundancy_pass(
    draft: Any, *, report_kind: str = "daily", threshold: float = DEFAULT_THRESHOLD
) -> tuple[Any, RedundancyResult]:
    """Duck-typed across ``DailyReportDraft``/``WeeklyReportDraft``/``MonthlyReportDraft`` (same
    ``getattr``-based traversal convention as ``eoa.report.claims_gate.apply_claims_gate``/
    ``eoa.report.style.apply_style_guard`` -- a field a given draft doesn't carry is skipped).
    Call this AFTER drafting and AFTER the claims gate (``apply_claims_gate``, weekly/monthly) --
    softened/dropped claims-gate text must be final before this pass compares sentences, and a
    section this pass empties out must not be re-examined by a later stage. Returns
    ``(possibly-updated draft, RedundancyResult)``; call ``result.log_all(...)`` for the
    structured log trail."""
    result = RedundancyResult()
    pool = _Pool(threshold)
    updates: dict[str, Any] = {}

    # 1. BLUF -- highest priority, nothing to compare against yet.
    bluf = getattr(draft, "bluf", None) or []
    for s in bluf:
        text = getattr(s, "text_he", None)
        if text:
            pool.add(text, section_label="שורה תחתונה", sentence_obj=s)

    # 2. exec summary
    exec_summary = getattr(draft, "exec_summary", None)
    if isinstance(exec_summary, list) and exec_summary:
        updates["exec_summary"] = _process_sentence_list(
            exec_summary, section_label="תקציר מנהלים", pool=pool, result=result
        )

    # 3. trends (weekly/monthly only) -- appear in the report between the exec summary and the
    # domain-review sections, and are the more synthesized of the two, so they are deduped against
    # bluf/exec_summary and win priority over sections below.
    trends = getattr(draft, "trends", None)
    if isinstance(trends, list) and trends:
        new_trends = []
        for trend in trends:
            title = getattr(trend, "title_he", "") or "?"
            label = f"מגמות > {title}"
            sentences = getattr(trend, "sentences", None) or []
            if not sentences:
                new_trends.append(trend)  # e.g. a MonthlyTrendSection change="gone" entry
                continue
            new_sentences = _process_sentence_list(sentences, section_label=label, pool=pool, result=result)
            if not new_sentences:
                pointer = _make_pointer_sentence(type(sentences[0]), sentences)
                new_sentences = [pointer]
                result.pointer_sections.append(label)
                pool.add(pointer.text_he, section_label=label, sentence_obj=pointer)
            new_trends.append(trend.model_copy(update={"sentences": new_sentences}))
        updates["trends"] = new_trends

    # 4. sections (domain review)
    sections = getattr(draft, "sections", None)
    if isinstance(sections, list) and sections:
        new_sections = []
        for section in sections:
            title = getattr(section, "title_he", "") or "?"
            label = f"סקירה לפי תחום > {title}"
            sentences = getattr(section, "sentences", None) or []
            if not sentences:
                new_sections.append(section)
                continue
            new_sentences = _process_sentence_list(sentences, section_label=label, pool=pool, result=result)
            if not new_sentences:
                pointer = _make_pointer_sentence(type(sentences[0]), sentences)
                new_sentences = [pointer]
                result.pointer_sections.append(label)
                pool.add(pointer.text_he, section_label=label, sentence_obj=pointer)
            new_sections.append(section.model_copy(update={"sentences": new_sentences}))
        updates["sections"] = new_sections

    # 5. analyst note -- plain strings, no cites to merge; a redundant one is just dropped.
    note = getattr(draft, "analyst_note_he", None)
    note_sentences = getattr(note, "sentences_he", None) if note is not None else None
    if note_sentences:
        kept_strings: list[str] = []
        for text in note_sentences:
            if not text:
                continue
            meta = _meta(text)
            match = pool.find_match(text, meta)
            if match is not None:
                result.dropped.append(
                    DroppedSentence(
                        section_label="הערכת האנליסט",
                        text_he=text,
                        kept_section_label=match.section_label,
                        kept_text_he=match.text,
                    )
                )
                continue
            kept_strings.append(text)
            pool.add(text, section_label="הערכת האנליסט", sentence_obj=None)
        updates["analyst_note_he"] = note.model_copy(update={"sentences_he": kept_strings})

    result.kept_sentence_texts = pool.texts
    new_draft = draft.model_copy(update=updates) if updates else draft
    return new_draft, result


# --------------------------------------------------------------------------
# helpers used by the callers (deltas.py / deep-search wiring), not by this pass itself
# --------------------------------------------------------------------------


def narrative_citation_numbers(draft: Any) -> set[int]:
    """Every citation number already used by BLUF + exec summary -- for
    ``eoa.report.deltas.render_delta_section_he`` to exclude a "new item" from the "מה השתנה"
    bullet list when the reader already saw it there (spec item 1: "excludes items already cited
    in the BLUF/exec summary")."""
    cites: set[int] = set()
    for s in getattr(draft, "bluf", None) or []:
        cites.update(getattr(s, "cites", None) or [])
    for s in getattr(draft, "exec_summary", None) or []:
        cites.update(getattr(s, "cites", None) or [])
    return cites


def filter_facts_against_narrative(
    facts: list[str], narrative_texts: Iterable[str], *, threshold: float = DEFAULT_THRESHOLD
) -> tuple[list[str], int]:
    """Drops a deep-search ``key_facts`` bullet that restates a sentence already kept in the
    narrative (spec item 1: "deep-search entries render only facts absent from the narrative").
    Returns ``(kept_facts, n_dropped)``."""
    if not facts:
        return facts, 0
    narrative = list(narrative_texts)
    kept: list[str] = []
    dropped = 0
    for fact in facts:
        if not fact:
            continue
        if any(is_redundant_text(fact, n, threshold=threshold) for n in narrative):
            dropped += 1
            continue
        kept.append(fact)
    return kept, dropped


__all__ = [
    "DEFAULT_THRESHOLD",
    "POINTER_SENTENCE_HE",
    "DroppedSentence",
    "RedundancyResult",
    "apply_redundancy_pass",
    "filter_facts_against_narrative",
    "is_redundant_text",
    "narrative_citation_numbers",
]
