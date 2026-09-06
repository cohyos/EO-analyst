"""QA gate: blocks report drafts that contain uncited factual claims.

Two shapes are supported, dispatched on the draft's own attributes (duck-typed, per
``_is_structured_draft``):

- **Structured** (goal 1, 2026-09-06): :class:`~eoa.llm.schemas.analysis.DailyReportDraft` —
  ``exec_summary``/``sections[].sentences`` are already lists of
  :class:`~eoa.llm.schemas.analysis.Sentence` (``text_he`` + non-empty ``cites``), so the "does
  every factual sentence carry a citation" question is answered by construction (a pydantic
  validation error, not a QA finding) — ``check()`` only still needs to verify, at the *registry*
  level (which varies per report run, so it can't live in the schema itself), that every ``cites``
  entry is a valid item number, plus the F5 duplicate-sentence rule (an exec-summary sentence
  copied verbatim from a section).
- **Legacy** (``eoa.report.weekly``/``monthly``/``bd_territory``, unchanged): free Hebrew prose
  (``exec_summary_he`` + ``sections[].prose_he``) is split into sentences, each decided "factual"
  (contains a number, a currency sign, a capitalized Latin token, a month name, or one of a small
  set of announcement verbs), and every factual sentence must carry an ``[n]`` marker resolving to
  a valid item.

In both shapes, the forward-looking indicators (``outlook`` / ``outlook_he``) are exempt from the
per-sentence citation requirement (they are the analyst's own judgement) but any reference given
must still resolve to a real item, and legacy ``outlook_he`` must open with an explicit assessment
marker.

Two optional, additive parameters generalize the legacy path beyond a single draft for the
weekly/monthly report drafts, which carry extra LLM-authored prose blocks outside ``draft.sections``
(e.g. one paragraph per detected trend): ``extra_sections`` are checked exactly like
``draft.sections`` (citation required on every factual sentence); ``exempt_sections`` get the same
treatment as ``outlook_he`` — no citation requirement, but any ``[n]`` present must still resolve to
a real item. Neither parameter is used by the (structured) daily report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from eoa.llm.schemas.analysis import DailyReportDraft

# -- sentence splitting ------------------------------------------------------

# Hebrew abbreviations that contain a period/gershayim but must not be treated as sentence
# boundaries. Matched case-sensitively against the text immediately before the break point.
_ABBREVIATIONS = (
    'ד"ר',
    'עו"ד',
    'רו"ח',
    'ח"כ',
    "אלוף",
    'סא"ל',
    'רס"ן',
    "סרן",
    'ארה"ב',
    'בריה"מ',
    'צה"ל',
    'משה"ב',
    "וכו'",
    "תואר",
    'ע"י',
    'ע"פ',
    'א"א',
    'י"ג',
    'י"ד',
)

# A sentence boundary is `.`, `?`, `!`, or `:` immediately followed by whitespace/newline/EOS —
# but never a bare decimal point (digit.digit) and never right after one of the abbreviations above.
_BOUNDARY_RE = re.compile(r"(?<=[.?!:])(?=\s|$)")
_DECIMAL_RE = re.compile(r"\d\.\d")


def split_sentences(text: str) -> list[str]:
    """Split Hebrew prose into sentences, respecting decimals and common abbreviations."""
    if not text or not text.strip():
        return []
    normalized = text.strip()
    raw_parts = _BOUNDARY_RE.split(normalized)
    sentences: list[str] = []
    buf = ""
    for part in raw_parts:
        if not part:
            continue
        buf = f"{buf}{part}" if buf else part
        stripped = buf.strip()
        if not stripped:
            continue
        # Don't split on a decimal point (e.g. "3.5 מיליון").
        boundary_is_decimal = False
        if len(stripped) >= 3 and stripped[-1] in ".?!:":
            window = stripped[-3:]
            if _DECIMAL_RE.search(window):
                boundary_is_decimal = True
        # Don't split right after a known abbreviation (the abbreviation itself never carries the
        # boundary punctuation, e.g. 'ארה"ב' immediately followed by a sentence-ending period).
        ends_with_abbrev = stripped[-1] in ".?!:" and any(
            stripped[:-1].rstrip().endswith(abbr) for abbr in _ABBREVIATIONS
        )
        if boundary_is_decimal or ends_with_abbrev:
            continue
        sentences.append(stripped)
        buf = ""
    if buf.strip():
        sentences.append(buf.strip())
    return [s for s in sentences if s.strip()]


# -- factual-sentence detection ----------------------------------------------

_CITATION_RE = re.compile(r"\[(\d+)\]")
_DIGIT_RE = re.compile(r"\d")
_CURRENCY_RE = re.compile(r"[$€₪£]|USD|ILS|EUR|GBP")
_LATIN_CAPITALIZED_RE = re.compile(r"\b[A-Z][A-Za-z0-9.&\-]{1,}\b")
_MONTH_NAMES = (
    "ינואר",
    "פברואר",
    "מרץ",
    "אפריל",
    "מאי",
    "יוני",
    "יולי",
    "אוגוסט",
    "ספטמבר",
    "אוקטובר",
    "נובמבר",
    "דצמבר",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
_FACTUAL_VERBS = (
    "זכתה",
    "חתמה",
    "רכשה",
    "הודיעה",
    "השיקה",
    "נבחרה",
    "קיבלה",
    "סיפקה",
    "נחתם",
    "הוענק",
)

_ASSESSMENT_MARKERS = ("להערכתנו", "נראה ש", "ייתכן")


def is_factual(sentence: str) -> bool:
    """A sentence is "factual" if it plausibly makes a checkable claim (number, entity, date, verb)."""
    if _DIGIT_RE.search(sentence):
        return True
    if _CURRENCY_RE.search(sentence):
        return True
    if _LATIN_CAPITALIZED_RE.search(sentence):
        return True
    if any(month in sentence for month in _MONTH_NAMES):
        return True
    return any(verb in sentence for verb in _FACTUAL_VERBS)


def citations_in(sentence: str) -> list[int]:
    """All ``[n]`` reference numbers found in a sentence."""
    return [int(m) for m in _CITATION_RE.findall(sentence)]


# -- result -------------------------------------------------------------------


@dataclass
class QAResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    uncited_sentences: list[str] = field(default_factory=list)
    bad_refs: list[int] = field(default_factory=list)
    duplicate_sentences: list[str] = field(default_factory=list)


_NORMALIZE_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")
_MIN_DUP_WORDS = 4


def _normalize_for_dup_check(sentence: str) -> str:
    """Normalize a sentence for verbatim-duplication comparison (F5): strip ``[n]`` citation
    markers (a summary sentence and its section-body twin often carry different reference
    numbers), drop punctuation, collapse whitespace, casefold."""
    text = _CITATION_RE.sub("", sentence)
    text = _NORMALIZE_PUNCT_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip().casefold()
    return text


def _duplicate_summary_sentences(summary_text: str, section_texts: list[tuple[str, str]]) -> list[str]:
    """Executive-summary sentences (F5) that also appear — verbatim, once normalized — inside a
    section/trend-paragraph body: the model copying a section sentence into the summary instead of
    synthesizing across items. Returns the *original* (non-normalized) summary sentences so callers
    can both report and strip them. Very short sentences are ignored (``_MIN_DUP_WORDS``) since a
    trivial phrase ("היום.") repeating by chance is not a real duplication."""
    summary_sentences = split_sentences(summary_text)
    if not summary_sentences:
        return []
    section_norms: set[str] = set()
    for _label, text in section_texts:
        for sentence in split_sentences(text):
            norm = _normalize_for_dup_check(sentence)
            if norm:
                section_norms.add(norm)
    duplicates = []
    for sentence in summary_sentences:
        norm = _normalize_for_dup_check(sentence)
        if len(norm.split()) >= _MIN_DUP_WORDS and norm in section_norms:
            duplicates.append(sentence)
    return duplicates


def _valid_range(items: list[dict]) -> set[int]:
    valid: set[int] = set()
    for it in items:
        n = it.get("n") if isinstance(it, dict) else getattr(it, "n", None)
        if n is not None:
            valid.add(int(n))
    return valid


def _check_prose(
    label: str, text: str, valid_ns: set[int], errors: list[str], uncited: list[str], bad_refs: set[int]
) -> None:
    for sentence in split_sentences(text):
        refs = citations_in(sentence)
        for n in refs:
            if n not in valid_ns:
                bad_refs.add(n)
                errors.append(f'ב{label}: ההפניה [{n}] אינה מצביעה על פריט קיים ברשימה — "{sentence}"')
        if is_factual(sentence) and not refs:
            uncited.append(sentence)
            errors.append(f'ב{label}: משפט עובדתי ללא הפניה [n] — "{sentence}"')


def _is_structured_draft(draft: Any) -> bool:
    """True for the new goal-1 :class:`~eoa.llm.schemas.analysis.DailyReportDraft` shape
    (``exec_summary``/``sections[].sentences``), false for the legacy free-prose shape used by
    weekly/monthly/bd_territory (``exec_summary_he``/``sections[].prose_he``)."""
    return hasattr(draft, "exec_summary") and not hasattr(draft, "exec_summary_he")


def _normalize_sentence_text(text: str) -> str:
    """Like :func:`_normalize_for_dup_check` but for a :class:`Sentence`'s ``text_he``, which never
    contains a ``[n]`` marker to begin with (goal 1: the model doesn't write markers itself)."""
    text = _NORMALIZE_PUNCT_RE.sub("", text)
    return _WHITESPACE_RE.sub(" ", text).strip().casefold()


def _check_structured(draft: Any, valid_ns: set[int]) -> tuple[list[str], set[int], list[str]]:
    """The goal-1 structured-schema equivalent of the legacy prose loop below: every ``cites``
    entry must be a valid registry number, and an exec-summary sentence must not verbatim-duplicate
    a section sentence (F5). Uncited sentences can't occur here — the schema itself
    (``Sentence.cites`` ``min_length=1``) already rejects them before ``check()`` ever runs."""
    errors: list[str] = []
    bad_refs: set[int] = set()

    section_norms: set[str] = set()
    for section in draft.sections:
        for sentence in section.sentences:
            for n in sentence.cites:
                if n not in valid_ns:
                    bad_refs.add(n)
                    errors.append(
                        f"בסעיף '{section.title_he}': ההפניה [{n}] אינה מצביעה על פריט קיים "
                        f'ברשימה — "{sentence.text_he}"'
                    )
            norm = _normalize_sentence_text(sentence.text_he)
            if norm:
                section_norms.add(norm)

    duplicate_sentences: list[str] = []
    for sentence in draft.exec_summary:
        for n in sentence.cites:
            if n not in valid_ns:
                bad_refs.add(n)
                errors.append(
                    f'בתקציר המנהלים: ההפניה [{n}] אינה מצביעה על פריט קיים ברשימה — "{sentence.text_he}"'
                )
        norm = _normalize_sentence_text(sentence.text_he)
        if norm and len(norm.split()) >= _MIN_DUP_WORDS and norm in section_norms:
            duplicate_sentences.append(sentence.text_he)
            errors.append(
                f'בתקציר המנהלים: המשפט "{sentence.text_he}" מועתק כלשונו מתוך גוף אחד הסעיפים — '
                "התקציר חייב לסכם ולקשר בין ממצאי הסעיפים, לא לצטט אותם במדויק."
            )

    for indicator in getattr(draft, "outlook", None) or []:
        for n in indicator.cites:
            if n not in valid_ns:
                bad_refs.add(n)
                errors.append(f"במבט קדימה: ההפניה [{n}] אינה מצביעה על פריט קיים ברשימה")

    note = getattr(draft, "analyst_note_he", None)
    if note is not None and len(note.sentences_he) > 3:
        errors.append('"הערכת האנליסט" חייבת להכיל עד 3 משפטים בלבד')

    return errors, bad_refs, duplicate_sentences


def check(
    draft: DailyReportDraft | Any,
    items: list[dict],
    *,
    extra_sections: list[tuple[str, str]] | None = None,
    exempt_sections: list[tuple[str, str]] | None = None,
) -> QAResult:
    """Validate every factual claim in ``draft`` resolves to a valid ``[n]`` in ``items``.

    Dispatches on ``draft``'s own shape (see module docstring / :func:`_is_structured_draft`):
    the new goal-1 :class:`~eoa.llm.schemas.analysis.DailyReportDraft` (``exec_summary`` +
    ``sections[].sentences``, both lists of :class:`~eoa.llm.schemas.analysis.Sentence`), or the
    legacy free-prose shape (``exec_summary_he`` + ``sections[].prose_he``) still used by
    :class:`~eoa.llm.schemas.reports.WeeklyReportDraft` / ``MonthlyReportDraft`` /
    ``BdTerritoryReportDraft``. ``extra_sections``/``exempt_sections`` are ``(label, text)`` pairs
    checked with the legacy free-prose rules regardless of ``draft``'s own shape (used only by the
    weekly report's trend paragraphs today).
    """
    valid_ns = _valid_range(items)
    errors: list[str] = []
    uncited: list[str] = []
    bad_refs: set[int] = set()
    duplicate_sentences: list[str] = []
    section_texts: list[tuple[str, str]] = []

    if _is_structured_draft(draft):
        errors, bad_refs, duplicate_sentences = _check_structured(draft, valid_ns)
    else:
        _check_prose("תקציר המנהלים", draft.exec_summary_he, valid_ns, errors, uncited, bad_refs)
        for section in draft.sections:
            _check_prose(f"סעיף '{section.title_he}'", section.prose_he, valid_ns, errors, uncited, bad_refs)
        section_texts = [(section.title_he, section.prose_he) for section in draft.sections]

    for label, text in extra_sections or []:
        _check_prose(f"'{label}'", text, valid_ns, errors, uncited, bad_refs)
    section_texts += list(extra_sections or [])

    if not _is_structured_draft(draft) and section_texts:
        # F5, legacy path only — the structured path already ran its own duplicate check above
        # (it also needs to compare against `extra_sections`, e.g. weekly trend paragraphs).
        extra_duplicates = _duplicate_summary_sentences(draft.exec_summary_he, section_texts)
        for sentence in extra_duplicates:
            if sentence in duplicate_sentences:
                continue
            duplicate_sentences.append(sentence)
            errors.append(
                f'בתקציר המנהלים: המשפט "{sentence}" מועתק כלשונו מתוך גוף אחד הסעיפים — התקציר חייב '
                "לסכם ולקשר בין ממצאי הסעיפים, לא לצטט אותם במדויק."
            )

    for label, text in exempt_sections or []:
        for n in citations_in(text):
            if n not in valid_ns:
                bad_refs.add(n)
                errors.append(f"ב'{label}': ההפניה [{n}] אינה מצביעה על פריט קיים ברשימה")

    if not _is_structured_draft(draft):
        outlook = (getattr(draft, "outlook_he", "") or "").strip()
        if outlook:
            if not any(outlook.startswith(marker) for marker in _ASSESSMENT_MARKERS):
                errors.append(
                    f'במבט קדימה: חובה לפתוח במילת הערכה מפורשת (להערכתנו / נראה ש / ייתכן) — "{outlook}"'
                )
            # Outlook is exempt from the citation requirement, but out-of-range refs are still an error.
            for n in citations_in(outlook):
                if n not in valid_ns:
                    bad_refs.add(n)
                    errors.append(f"במבט קדימה: ההפניה [{n}] אינה מצביעה על פריט קיים ברשימה")

    return QAResult(
        passed=not errors,
        errors=errors,
        uncited_sentences=uncited,
        bad_refs=sorted(bad_refs),
        duplicate_sentences=duplicate_sentences,
    )
