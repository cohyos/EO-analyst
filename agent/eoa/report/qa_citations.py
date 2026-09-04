"""QA gate: blocks daily-report drafts that contain uncited factual claims.

``check()`` splits the Hebrew prose of a :class:`DailyReportDraft` into sentences, decides which
sentences are "factual" (contain a number, a currency sign, a capitalized Latin token, a month
name, or one of a small set of announcement verbs), and requires every factual sentence to carry
at least one ``[n]`` citation whose ``n`` is a valid index into the item list that was given to the
model. The ``outlook_he`` section is exempt from the citation requirement but must instead open
with an explicit assessment marker, since it is the analyst's own forward-looking judgement rather
than a restatement of sourced facts.

Two optional, additive parameters generalize ``check()`` beyond :class:`DailyReportDraft` for the
weekly/monthly report drafts (``eoa.report.weekly`` / ``eoa.report.monthly``), which carry extra
LLM-authored prose blocks outside ``draft.sections`` (e.g. one paragraph per detected trend):
``extra_sections`` are checked exactly like ``draft.sections`` (citation required on every factual
sentence); ``exempt_sections`` get the same treatment as ``outlook_he`` — no citation requirement,
but any ``[n]`` present must still resolve to a real item. Neither parameter is used by the daily
report, so passing neither reproduces the original behaviour exactly.
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


def check(
    draft: DailyReportDraft | Any,
    items: list[dict],
    *,
    extra_sections: list[tuple[str, str]] | None = None,
    exempt_sections: list[tuple[str, str]] | None = None,
) -> QAResult:
    """Validate every factual sentence in ``draft`` carries an ``[n]`` citation into ``items``.

    ``draft`` only needs ``exec_summary_he``, ``sections`` (each with ``title_he``/``prose_he``)
    and ``outlook_he`` — duck-typed so :class:`~eoa.llm.schemas.reports.WeeklyReportDraft` /
    ``MonthlyReportDraft`` work here unchanged. ``extra_sections``/``exempt_sections`` are
    ``(label, text)`` pairs checked in addition to ``draft.sections`` — the former like a normal
    section, the latter like ``outlook_he`` (citation-exempt, out-of-range refs still flagged).
    """
    valid_ns = _valid_range(items)
    errors: list[str] = []
    uncited: list[str] = []
    bad_refs: set[int] = set()

    _check_prose("תקציר המנהלים", draft.exec_summary_he, valid_ns, errors, uncited, bad_refs)
    for section in draft.sections:
        _check_prose(f"סעיף '{section.title_he}'", section.prose_he, valid_ns, errors, uncited, bad_refs)
    for label, text in extra_sections or []:
        _check_prose(f"'{label}'", text, valid_ns, errors, uncited, bad_refs)
    for label, text in exempt_sections or []:
        for n in citations_in(text):
            if n not in valid_ns:
                bad_refs.add(n)
                errors.append(f"ב'{label}': ההפניה [{n}] אינה מצביעה על פריט קיים ברשימה")

    outlook = (draft.outlook_he or "").strip()
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
    )
