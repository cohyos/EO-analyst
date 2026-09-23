"""QA gate: blocks report drafts that contain uncited factual claims.

Two shapes are supported, dispatched on the draft's own attributes (duck-typed, per
``_is_structured_draft``):

- **Structured** (goal 1, 2026-09-06; round-2, 2026-09-06 for weekly):
  :class:`~eoa.llm.schemas.analysis.DailyReportDraft` and
  :class:`~eoa.llm.schemas.reports.WeeklyReportDraft` — ``exec_summary``/``sections[].sentences``
  (plus, for the weekly draft only, ``trends[].sentences``) are already lists of
  :class:`~eoa.llm.schemas.analysis.Sentence` (``text_he`` + non-empty ``cites``), so the "does
  every factual sentence carry a citation" question is answered by construction (a pydantic
  validation error, not a QA finding) — ``check()`` only still needs to verify, at the *registry*
  level (which varies per report run, so it can't live in the schema itself), that every ``cites``
  entry is a valid item number, plus the F5 duplicate-sentence rule (an exec-summary sentence
  copied verbatim from a section/trend).
- **Legacy** (``eoa.report.monthly``/``bd_territory``, unchanged): free Hebrew prose
  (``exec_summary_he`` + ``sections[].prose_he``) is split into sentences, each decided "factual"
  (contains a number, a currency sign, a capitalized Latin token, a month name, or one of a small
  set of announcement verbs), and every factual sentence must carry an ``[n]`` marker resolving to
  a valid item.

In both shapes, the forward-looking indicators (``outlook`` / ``outlook_he``) are exempt from the
per-sentence citation requirement (they are the analyst's own judgement) but any reference given
must still resolve to a real item, and legacy ``outlook_he`` must open with an explicit assessment
marker.

Two optional, additive parameters generalize the legacy path beyond a single draft for the
monthly/bd_territory report drafts, which carry extra LLM-authored prose blocks outside
``draft.sections`` (e.g. one paragraph per detected trend for monthly): ``extra_sections`` are
checked exactly like ``draft.sections`` (citation required on every factual sentence);
``exempt_sections`` get the same treatment as ``outlook_he`` — no citation requirement, but any
``[n]`` present must still resolve to a real item. None of the three parameters is used by the
(structured) daily/weekly reports — the weekly report's own trend paragraphs are validated via
``draft.trends`` directly (see :func:`_check_structured`) instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import structlog

from eoa.llm.schemas.analysis import DailyReportDraft

log = structlog.get_logger(__name__)

# =================================================================================================
# PL-REPORT-FIX (user screenshot 2026-09-08 20:30, pl_targeting_pods report id 184): defect #2 --
# a product-line report's every row and every recommended action rested on ONE source
# (usarfp.com, "Litening advanced targeting pod Tender in USA, ID 3483356"), a tender
# aggregator/reseller site, rendered with reliability "—" and date "—" in the sources appendix
# because no collector populates ``sources.reliability``/an item's own ``reliability`` key for an
# aggregator domain (``eoa.report.docx_builder._reliability_for`` only ever reads those two).
#
# This is a small, static domain -> reliability map -- deliberately NOT DB-backed (unlike the
# primary/secondary ``sources.reliability`` scale above/in docx_builder) so a tender/forecast
# report-layer caller (``eoa.report.product_line`` today) can grade a known aggregator/reseller
# domain deterministically, offline, with no DB round-trip or collector-side change (out of scope
# for this file -- see the callers for what they do with it: attach a reliability descriptor,
# backfill the appendix date from the linked ``tenders`` row, downgrade an aggregator-only
# recommended action, and cap an aggregator-only opportunity's tier at "C").
# =================================================================================================

#: Known tender-aggregator / reseller sites -- third parties that re-list a government/OEM tender
#: notice (sometimes for a fee, sometimes scraped) rather than being the primary procurement portal
#: or OEM/government source itself. Not exhaustive; extend as new aggregator domains are confirmed.
TENDER_AGGREGATOR_DOMAINS: frozenset[str] = frozenset(
    {
        "usarfp.com",
        "tendersinfo.com",
        "bidnetdirect.com",
        "bidnet.com",
        "globaltenders.com",
        "tenderdetail.com",
        "tendersontime.com",
        "biddingo.com",
        "tenderswala.com",
        "eibidding.com",
        "tenderguru.com",
        "biddetail.com",
        "tendersgate.com",
    }
)

TENDER_AGGREGATOR_RELIABILITY_LABEL_HE = "מצבור מכרזים (אמינות נמוכה)"
TENDER_AGGREGATOR_RELIABILITY_SCORE = 0.3


def _normalize_domain(raw: str) -> str:
    """``raw`` (a URL, a bare host, or a free-text source name that may or may not be a URL)
    reduced to a lowercase, ``www.``-stripped registrable-looking host for comparison against
    :data:`TENDER_AGGREGATOR_DOMAINS`. Not a full public-suffix-list normalisation -- good enough
    for exact/subdomain matching against a short, manually-curated allowlist."""
    value = raw.strip().lower()
    if "://" in value:
        value = urlparse(value).netloc or value
    elif "/" in value:
        # A bare "usarfp.com/tender/..." with no scheme -- urlparse alone would put it all in
        # `.path`, so split on the first "/" ourselves.
        value = value.split("/", 1)[0]
    value = value.split("@")[-1]  # drop a userinfo prefix, if any
    value = value.split(":")[0]  # drop a port, if any
    if value.startswith("www."):
        value = value[4:]
    return value


def is_tender_aggregator_domain(*, url: str | None = None, source_name: str | None = None) -> bool:
    """True when ``url`` and/or ``source_name`` resolve to a known tender-aggregator/reseller
    domain (:data:`TENDER_AGGREGATOR_DOMAINS`) -- checks whichever of the two is given, a bare
    ``source_name`` that already looks like a domain (e.g. ``"usarfp.com"``) included. A domain
    that cannot be determined from either argument (both empty, or neither looks like a URL/host)
    returns ``False`` -- never guessed."""
    for raw in (url, source_name):
        if not raw:
            continue
        domain = _normalize_domain(str(raw))
        if not domain or "." not in domain:
            continue
        if domain in TENDER_AGGREGATOR_DOMAINS or any(
            domain == d or domain.endswith(f".{d}") for d in TENDER_AGGREGATOR_DOMAINS
        ):
            return True
    return False


def tender_aggregator_reliability() -> dict[str, Any]:
    """The ``reliability`` dict shape ``eoa.report.docx_builder.reliability_label`` already knows
    how to render (``{"kind", "score", "label"}``, see that function's own docstring) for a
    confirmed tender-aggregator source -- ``kind`` is left out (``None``) so the appendix cell
    reads just "<label> · <score>" rather than a redundant "מקור משני · <label> · <score>"."""
    return {
        "kind": None,
        "score": TENDER_AGGREGATOR_RELIABILITY_SCORE,
        "label": TENDER_AGGREGATOR_RELIABILITY_LABEL_HE,
    }


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


def _find_cross_group_duplicates(
    groups: list[tuple[str, list[str]]], *, normalize: Any
) -> list[tuple[str, str, str]]:
    """D6 round-1 fix (docs/qa/loop/round_1_fixes.md, ``no_duplicate_sentences``): a sentence
    (normalized, >= ``_MIN_DUP_WORDS`` words) that verbatim-duplicates a sentence already seen in
    an *earlier* group in ``groups`` -- generalizes the old exec-summary-vs-sections-only check
    (:func:`_duplicate_summary_sentences`/the exec-summary loop in :func:`_check_structured`) to
    catch two ordinary sections/paragraphs restating the same sentence, which neither of those
    ever compared against each other. Returns ``(original_sentence, first_group_label,
    duplicate_group_label)`` for the *second and later* occurrence only -- the first occurrence of
    a sentence is never itself flagged, and a sentence repeated twice within the very same group
    is not flagged here (that is a same-section repetition, a different problem this check does
    not address)."""
    seen: dict[str, str] = {}  # normalized sentence -> label of the group it first appeared in
    duplicates: list[tuple[str, str, str]] = []
    for label, sentences in groups:
        local_seen: set[str] = set()
        for sentence in sentences:
            norm = normalize(sentence)
            if not norm or len(norm.split()) < _MIN_DUP_WORDS:
                continue
            if norm in local_seen:
                continue
            local_seen.add(norm)
            first_label = seen.get(norm)
            if first_label is not None:
                duplicates.append((sentence, first_label, label))
            else:
                seen[norm] = label
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
    (``Sentence.cites`` ``min_length=1``) already rejects them before ``check()`` ever runs.

    Round-2 (2026-09-06, weekly structured migration): also validates ``draft.trends`` (a
    ``WeeklyReportDraft``-only field shaped like ``[{title_he, sentences}]``, e.g.
    ``eoa.llm.schemas.reports.WeeklyTrendSection``) exactly like ``draft.sections`` — same
    cite-validity check, and included in the same cross-group duplicate detection. ``getattr``-
    guarded so ``DailyReportDraft`` (no ``trends`` field) is unaffected.
    """
    errors: list[str] = []
    bad_refs: set[int] = set()

    section_norms: set[str] = set()
    section_groups: list[tuple[str, list[str]]] = []

    def _collect_group(label: str, sentences: Any, *, kind_he: str) -> None:
        sentences_he: list[str] = []
        for sentence in sentences:
            for n in sentence.cites:
                if n not in valid_ns:
                    bad_refs.add(n)
                    errors.append(
                        f"ב{kind_he} '{label}': ההפניה [{n}] אינה מצביעה על פריט קיים "
                        f'ברשימה — "{sentence.text_he}"'
                    )
            sentences_he.append(sentence.text_he)
            norm = _normalize_sentence_text(sentence.text_he)
            if norm:
                section_norms.add(norm)
        section_groups.append((label, sentences_he))

    for section in draft.sections:
        _collect_group(section.title_he, section.sentences, kind_he="סעיף")

    for trend in getattr(draft, "trends", None) or []:
        _collect_group(trend.title_he, trend.sentences, kind_he="מגמה")

    duplicate_sentences: list[str] = []

    # D6 round-1 fix (docs/qa/loop/round_1_fixes.md): a section sentence that verbatim-duplicates
    # a sentence in an *earlier* section/trend -- not just an exec-summary-vs-section duplicate
    # (below).
    for sentence, first_label, dup_label in _find_cross_group_duplicates(
        section_groups, normalize=_normalize_sentence_text
    ):
        duplicate_sentences.append(sentence)
        errors.append(
            f"ב'{dup_label}': המשפט \"{sentence}\" מועתק כלשונו מ'{first_label}' — "
            "כל סעיף/מגמה חייב להביא ניתוח משלו, לא לחזור על משפט שכבר הופיע במקום אחר."
        )

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
        if not sentence.cites:  # round 9: every structured exec-summary sentence is a sourced claim by design
            # Round 5 (2026-09-07, live daily_2026-09-06 / D6 checker): the model closed the
            # summary with an uncited count sentence ("נרשמו בתקופה זו 8 אירועים ... 11 תחזיות");
            # a factual exec-summary sentence must carry at least one [n], same bar the legacy
            # prose path applies via _check_prose.
            errors.append(
                f'בתקציר המנהלים: המשפט "{sentence.text_he}" מכיל טענה עובדתית (מספר/ישות/תאריך) '
                "ללא הפניה [n] — כל משפט עובדתי בתקציר חייב הפניה לפריט ברשימה."
            )

    for indicator in getattr(draft, "outlook", None) or []:
        for n in indicator.cites:
            if n not in valid_ns:
                bad_refs.add(n)
                errors.append(f"במבט קדימה: ההפניה [{n}] אינה מצביעה על פריט קיים ברשימה")

    note = getattr(draft, "analyst_note_he", None)
    if note is not None and len(note.sentences_he) > 3:
        errors.append('"הערכת האנליסט" חייבת להכיל עד 3 משפטים בלבד')

    # F18: BLUF (``draft.bluf``, a ``list[Sentence]``) and the "הנחות והפרכות" key-assumptions
    # check (``draft.assumptions``, a ``list[AssumptionFalsifier]``) are both rendered fields
    # (``eoa.report.docx_builder`` reads them natively) but, unlike ``sections``/``trends``/
    # ``exec_summary`` above, were never registry-checked here -- a ``cites`` entry pointing at a
    # non-existent item number could pass QA and render a dangling ``[n]``. ``Sentence.cites`` is
    # already non-empty by construction (goal 1 schema) for ``bluf``; ``AssumptionFalsifier.cites``
    # may legitimately be empty (a structural premise, not itself a citable claim) -- only a
    # non-empty ``cites`` is checked for registry validity, same as ``outlook`` above.
    for sentence in getattr(draft, "bluf", None) or []:
        for n in sentence.cites:
            if n not in valid_ns:
                bad_refs.add(n)
                errors.append(
                    f'ב-BLUF ("שורה תחתונה"): ההפניה [{n}] אינה מצביעה על פריט קיים ברשימה — '
                    f'"{sentence.text_he}"'
                )

    for pair in getattr(draft, "assumptions", None) or []:
        for n in getattr(pair, "cites", None) or []:
            if n not in valid_ns:
                bad_refs.add(n)
                errors.append(
                    f"בהנחות והפרכות: ההפניה [{n}] אינה מצביעה על פריט קיים ברשימה — "
                    f'"{pair.assumption_he}"'
                )

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

        # D6 round-1 fix (docs/qa/loop/round_1_fixes.md): the legacy free-prose path (weekly/
        # monthly/bd_territory) never compared two ordinary sections/trend-paragraphs against each
        # other, only exec-summary-vs-sections (above) -- generalizes the same
        # ``_find_cross_group_duplicates`` the structured path now runs.
        cross_groups = [(label, split_sentences(text)) for label, text in section_texts]
        for sentence, first_label, dup_label in _find_cross_group_duplicates(
            cross_groups, normalize=_normalize_for_dup_check
        ):
            if sentence in duplicate_sentences:
                continue
            duplicate_sentences.append(sentence)
            errors.append(
                f"ב'{dup_label}': המשפט \"{sentence}\" מועתק כלשונו מתוך '{first_label}' — כל סעיף "
                "חייב להביא תוכן משלו, לא לחזור על משפט שכבר הופיע בסעיף אחר."
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


# =================================================================================================
# Round 5 P3 (docs/REPORT_TEMPLATE_BENCHMARK.md sec 1 item 6, sec 4 item 11;
# docs/qa/loop/round_3_judge.md D2): the so_what template-phrase post-pass.
#
# BLUF ("שורה תחתונה"), the outlook likelihood/confidence separated-clause rendering, and the
# "הנחות והפרכות" assumptions section were ALSO originally planned as extra_sections helpers here
# (see git history) -- dropped once it turned out ``eoa.report.docx_builder`` (P4, landed the same
# evening) already reads ``draft.bluf``/``OutlookIndicator.likelihood``/``confidence_level``/
# ``confidence_basis_he``/``draft.assumptions`` NATIVELY (``_draft_bluf_text``/
# ``_render_outlook_indicator``/``_draft_assumptions``+``_render_assumption``), including a
# "before_summary" extra_sections position and a synthesized-BLUF fallback for a zero-narrative
# draft (``eoa.report.daily._deterministic_fallback_draft``'s own shape). Adding the extra_sections/
# render-copy path on top of that would have DOUBLE-rendered every one of these sections/suffixes --
# see docs/MODULES.md "Round 5 P3" for the full P3<->P4 coordination note, including the one
# non-fatal value-format mismatch (this schema uses Hebrew literals for
# ``likelihood``/``confidence_level``; P4's formatters were written expecting a numeric ratio/
# English key, but both fall back to rendering the value verbatim, so the correct Hebrew text is
# what actually reaches the page).
# =================================================================================================

# -- so_what template-phrase ban (docs/qa/loop/round_3_judge.md D2) --------------------------------

#: Round 5 P3: the round-3 judge's D2 finding -- the generic "so_what" template phrasing (originally
#: only banned in ``analyze.md``, round 3) independently recurs in *report-generation* prose too
#: (its own example: "לחזק את מעמדה" on a Hensoldt/Elbit item in a cloud-drafted weekly report).
#: Extends the same ban to report exec_summary/section prose. Deliberately a separate list/pattern
#: from ``eoa.report.style.BANNED_FILLER_PHRASES_HE`` (that module/list is out of this round's file
#: ownership to edit) rather than a generic "no template phrases" catch-all -- these are specific,
#: safe-to-delete-outright formulaic phrases, not general style guidance.
SO_WHAT_TEMPLATE_PHRASES_HE: tuple[str, ...] = (
    "מחזק את מעמדה",
    "מחזקת את מעמדה",
    "לחזק את מעמדה",
    "מחזק את מעמדו",
    "מחזקת את מעמדו",
    "לחזק את מעמדו",
    "מהווה צעד משמעותי",
    "מהווה צעד נוסף",
    "מהווה צעד חשוב",
    "מעיד על מגמה",
    "מעידה על מגמה",
)  # fmt: skip

_MULTI_SPACE_RE = re.compile(r" {2,}")
_LEADING_JUNK_RE = re.compile(r"^\s*[,:;]+\s*")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([.,:;)\]])")
_DOUBLE_PUNCT_RE = re.compile(r"([.,:;])\1+")


def _build_phrase_pattern(phrases: tuple[str, ...]) -> re.Pattern[str]:
    """Small local copy of ``eoa.report.style._build_filler_pattern``'s algorithm (longest-phrase-
    first, so a longer phrase matches whole before a shorter substring of it could) -- not imported
    across the module boundary since that name is private and ``style.py`` is out of this round's
    file-ownership scope to edit (see the module-level note above)."""
    ordered = sorted(set(phrases), key=len, reverse=True)
    return re.compile("|".join(re.escape(p) for p in ordered))


_SO_WHAT_RE = _build_phrase_pattern(SO_WHAT_TEMPLATE_PHRASES_HE)


def strip_so_what_phrases(text: str) -> tuple[str, list[str]]:
    """Remove every banned so_what template phrase from ``text``, cleaning up leftover
    whitespace/dangling punctuation -- same safe, deterministic removal mechanics as
    ``eoa.report.style.strip_filler_phrases`` (this module's own local copy, see
    :func:`_build_phrase_pattern`). ``(text, [])`` (input returned unchanged) when nothing matched."""
    if not text:
        return text, []
    removed: list[str] = []

    def _sub(m: re.Match[str]) -> str:
        removed.append(m.group(0))
        return " "

    cleaned = _SO_WHAT_RE.sub(_sub, text)
    if not removed:
        return text, []
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned)
    cleaned = _LEADING_JUNK_RE.sub("", cleaned)
    cleaned = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", cleaned)
    cleaned = _DOUBLE_PUNCT_RE.sub(r"\1", cleaned)
    return cleaned.strip(), removed


def strip_so_what_phrases_from_draft(
    draft: Any, *, report_kind: str = "", job_id: int | None = None
) -> tuple[Any, int]:
    """Deterministic post-pass (round 5 P3, docs/qa/loop/round_3_judge.md D2): strips every banned
    :data:`SO_WHAT_TEMPLATE_PHRASES_HE` phrase from ``draft.exec_summary`` and every
    ``draft.sections[].sentences`` entry -- the two places the judge finding's evidence actually
    appeared (report narrative prose), duck-typed so this runs unchanged on the Daily/Weekly/Monthly
    structured draft shapes. Call this from the same draft-QA step ``eoa.report.style.
    apply_style_guard`` is already called from (after ``normalize_draft``, before rendering).

    Returns ``(possibly-updated draft, phrases_removed_count)``; the caller should log the count
    (``log.info("so_what_template_phrases_stripped", ...)``) when non-zero, per this task's "log
    counts" requirement -- done here directly so every call site gets it for free."""
    removed_total = 0

    def _clean(text: str) -> str:
        nonlocal removed_total
        cleaned, removed = strip_so_what_phrases(text)
        removed_total += len(removed)
        return cleaned

    updates: dict[str, Any] = {}

    exec_summary = getattr(draft, "exec_summary", None)
    if isinstance(exec_summary, list) and exec_summary:
        new_summary = []
        for s in exec_summary:
            text = getattr(s, "text_he", None)
            if isinstance(text, str) and text:
                cleaned = _clean(text)
                new_summary.append(s.model_copy(update={"text_he": cleaned}) if cleaned != text else s)
            else:
                new_summary.append(s)
        updates["exec_summary"] = new_summary

    sections = getattr(draft, "sections", None)
    if isinstance(sections, list) and sections:
        new_sections = []
        for section in sections:
            if not hasattr(section, "sentences"):
                new_sections.append(section)
                continue
            new_sentences = []
            for s in section.sentences:
                text = getattr(s, "text_he", None)
                if isinstance(text, str) and text:
                    cleaned = _clean(text)
                    new_sentences.append(s.model_copy(update={"text_he": cleaned}) if cleaned != text else s)
                else:
                    new_sentences.append(s)
            new_sections.append(section.model_copy(update={"sentences": new_sentences}))
        updates["sections"] = new_sections

    updated = draft.model_copy(update=updates) if updates else draft
    if removed_total:
        log.info(
            "so_what_template_phrases_stripped",
            report_kind=report_kind,
            job_id=job_id,
            count=removed_total,
        )
    return updated, removed_total
