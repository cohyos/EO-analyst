"""Round 3 (docs/qa/loop/round_2_judge.md, D5 finding): deterministic, post-generation grounding
guards for `POST /api/ask` answers, applied in `eoa.api.routes.ask` after the full answer text is
assembled and before the (round 2) citation-repair / anchor guards run.

Round 2's guards (citation presence, topic anchor) both still passed on two live-found severe
fabrications because the fabricated text kept a real `[n]` marker and the nominal topic word:

- Q2 (Iron Beam): the answer attributed a Rafael "Iron Beam" contract narrative to a source that
  is actually AeroVironment's own, unrelated laser programme -- a *cross-source conflation*, not a
  missing citation or an off-topic answer.
- Q4 (DROIC): the answer invented a named professor, university and project by conflating two
  unrelated retrieved arXiv papers -- an *ungrounded entity*, not a missing citation.

Neither failure mode is caught by "does the answer cite something" or "does the answer mention the
question's own subject" -- both need a check that actually compares the answer's own claimed
entities against what the retrieved sources (or the question, or the canonical watchlist) actually
contain. This module implements two independent, additive passes over the finished answer text:

1. :func:`ground_and_filter_answer` -- for every sentence/bullet ("unit") in the answer:
   (a) grounded-entity check: every Latin-script multi-word proper noun, quoted multi-word phrase,
       money figure, and year mentioned must appear (case-insensitively) in the question, in *some*
       retrieved source's title/text, or resolve to a canonical watchlist/curated-org record
       (:mod:`eoa.pipeline.entity_normalize`) -- catches an invented name/project/institution
       wholesale (the Q4 pattern);
   (b) cross-source conflation guard: a unit that cites `[n]` and names a watchlist-recognised
       company/system (:func:`eoa.pipeline.entity_normalize.find_watchlist_aliases_in_text`) must
       have that same entity actually present in at least one of the cited sources' own text --
       catches attributing a real entity to the *wrong* source (the Q2 pattern).
   A flagged unit is dropped; if the only flagged unit(s) fell inside the leading "direct answer"
   paragraph and removing them leaves it blank, an explicit Hebrew gap sentence naming the missing
   entity is inserted in its place instead of leaving a blank answer.
2. :func:`sanitize_citation_markers` -- strips a literal, never-substituted citation-placeholder
   token (`[n]`, `[n=5]`, `{n}`) that a generation can leak into its own output (live-found in a
   Q3 heading, docs/qa/loop/round_2_judge.md) -- this is never a valid citation (a real one is
   always a plain `[<digit>+]`), so it is always safe to strip outright.

Both are deliberately conservative, precision-first heuristics (an exact-phrase/whole-word check,
not a semantic one) -- see each function's own docstring for the specific trade-off made and why.
Neither talks to the database or the LLM; both are pure, cheap, synchronous text functions safe to
call on every answer.
"""

from __future__ import annotations

import re
from typing import Any

from eoa.pipeline import entity_normalize

# A real citation is always a bare `[<digits>]` (see `eoa.api.routes.ask`'s own
# `re.search(r"\[\d+\]", ...)` checks) -- anything shaped like `[n]`/`[n=5]`/`{n}` is always a
# leaked, unsubstituted template placeholder, never a legitimate citation, so it is always safe to
# strip outright rather than trying to guess what number the model meant.
_TEMPLATE_LEAK_RE = re.compile(r"\[\s*[nN]\s*(?:=\s*\d+)?\s*\]|\{\s*[nN]\s*\}")

_HEADING_LINE_RE = re.compile(r"^#{1,6}[ \t].*$", re.MULTILINE)
_FIRST_SECTION_HEADING_RE = re.compile(r"^###\s", re.MULTILINE)
_SECTION_HEADING_TEXT_RE = re.compile(r"^#{1,6}[ \t]+(.*)$", re.MULTILINE)

# `ask_answer_format.md` rule 3 explicitly exempts this one section from sourcing at all: "פרוזה
# ... **אינה** דורשת [n] (זו דעה מבוססת, לא ציטוט)" -- it is the model's own disclosed
# interpretation/speculation, by design not expected to be a verbatim restatement of the sources.
# Live-verified 2026-09-06 (throwaway 8766, golden Q1, agy provider): applying the grounded-entity
# check there anyway flagged perfectly legitimate analytical speculation using standard domain
# vocabulary ("Edge AI", "Sensor Fusion") absent from that specific retrieval's own text, removing
# a real, reasonable analyst sentence -- exactly the false-positive failure mode this module's
# docstring warns about. The cross-source conflation guard is unaffected (it already only ever
# fires on a `[n]`-cited unit, and this section carries none by the same format rule).
_ANALYST_ASSESSMENT_MARKER = "הערכת האנליסט"
_SENTENCE_END_RE = re.compile(r"[.!?״]")  # ״ == ״ (Hebrew gershayim)
_CITATION_RE = re.compile(r"\[(\d+)\]")

# Two-or-more capitalised Latin "words" (letters/digits, hyphen- or space-joined), each >= 2 chars
# -- deliberately requires >= 2 segments (a bare "Rafael"/"XM30" is never flagged by this check
# alone; a single known watchlist/system name is either genuinely present or caught instead by the
# cross-source conflation guard below). Matches both space-separated names ("Kunat Pipatanakul")
# and hyphen-joined compounds ("Wayu-Paxa-OCR-Zero") -- both live-found Q4 fabrications.
#
# The >= 2 chars-per-segment floor is itself a live fix: an early version allowed a single-letter
# first segment and flagged a real live answer's own "(C-UAS)" gloss (a standard domain acronym
# named in `system_analyst.md`'s own domain description, not an invented entity) as an ungrounded
# two-segment candidate ("C" + "UAS") purely because that specific question/retrieval didn't happen
# to also contain the literal string "C-UAS" -- exactly the false-positive failure mode this
# module's docstring warns about. A short acronym-style compound like "C-UAS"/"K-9" never reaches
# this regex at all now; a genuinely fabricated multi-word name is unaffected since real invented
# names (both live Q4 examples included) use full words, not single letters.
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]{1,}(?:[-\s][A-Z][A-Za-z0-9]{1,}){1,5}\b")

# An ASCII-quoted multi-word phrase -- Hebrew academic prose in this corpus reserves the Hebrew
# gershayim (״) for acronyms (system_analyst.md rule 5), so a real quoted name/title uses ASCII
# quotes; only spans of >= 2 words are treated as candidates (a single quoted word is far more
# likely to be an ordinary emphasis-quote than an invented proper noun).
#
# Live-verified 2026-09-06 (throwaway 8766, golden Q2, local resident model): `system_analyst.md`
# rule 5's own ASCII-quote-for-acronyms prohibition is not always followed by the model itself --
# real generations kept writing ARA-style acronyms ("ארה\"ב", "כטב\"מים") with a literal ASCII `"`
# stuck directly between two Hebrew letters, no surrounding whitespace at all. A naive
# `"[^"]{3,80}"` pairs one such stray acronym-internal quote with the *next* one anywhere within
# 80 chars -- possibly a full sentence or more later -- and treats the huge, nonsensical span
# between them as a "quoted phrase" candidate, which then fails grounding and gets an entire
# otherwise-fine sentence removed. A genuine quoted phrase always has whitespace/punctuation (never
# a Hebrew letter with no gap) immediately outside both its quote marks, so requiring that -- via
# the negative lookbehind/lookahead below -- filters out every acronym-internal quote without
# needing the model to follow the gershayim rule in the first place.
_HEBREW_LETTERS = "א-ת"
_QUOTED_RE = re.compile(rf'(?<![{_HEBREW_LETTERS}])"([^"\n]{{3,80}})"(?![{_HEBREW_LETTERS}])')

_MONEY_RE = re.compile(
    r"[$€₪]\s?\d[\d,.]*\s?(?:[MBK]\b)?"
    r"|\b\d[\d,.]*\s?(?:מיליון|מיליארד|million|billion|USD|EUR)\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")

_GAP_SENTENCE = "המקורות שנשלפו אינם מזכירים {entity} — לא ניתן לאשר."


def sanitize_citation_markers(text: str) -> tuple[str, int]:
    """Strip any literal, unsubstituted citation-placeholder token (`[n]`, `[n=5]`, `{n}`, any
    case) from ``text`` -- never a valid citation, which is always a plain `[<digits>]`. Returns
    ``(cleaned_text, count_removed)``; ``count_removed == 0`` (and ``cleaned_text == text``) when
    nothing matched, so callers can cheaply skip re-emitting an unchanged answer."""
    if not text:
        return text, 0
    cleaned, count = _TEMPLATE_LEAK_RE.subn("", text)
    if count:
        cleaned = re.sub(r"[ \t]+([.,:;!?])", r"\1", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned, count


def _iter_units(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` spans over ``text`` granular enough to drop individually without mangling
    the rest of the answer: a heading line is never a unit (structural, always kept as-is); a
    markdown bullet line (``-``/``*``) is one whole unit (dropping it removes the bullet marker
    too, leaving no orphan ``- ``); any other line is split into sentences on ``.!?``/gershayim."""
    units: list[tuple[int, int]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        start = pos
        pos += len(line)
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(("-", "*")):
            units.append((start, start + len(line)))
            continue
        seg_start = 0
        for m in _SENTENCE_END_RE.finditer(line):
            end = m.end()
            units.append((start + seg_start, start + end))
            seg_start = end
        if line[seg_start:].strip():
            units.append((start + seg_start, start + len(line)))
    return units


def _proper_noun_grounded(candidate: str, corpus_cf: str) -> bool:
    """Whether ``candidate`` (a multi-word proper noun or quoted phrase pulled from the answer) is
    grounded, checked in three widening steps: (1) the exact phrase appears, case-insensitively, in
    ``corpus_cf`` (the question + every retrieved source's title/text); (2) it resolves to a
    canonical watchlist/curated-org record on its own (a real, known entity is grounded even when
    this particular retrieval didn't happen to include a source naming it); (3) every one of its
    individual words (>= 3 chars, so short connectors don't count) appears *somewhere* in
    ``corpus_cf`` -- a real fact the model paraphrased or reordered (e.g. "GDLS-built" .. "Lynx
    XM30" restated as "GDLS Lynx") must not be flagged just because the exact phrase isn't a
    contiguous substring; a genuinely invented multi-word name (e.g. "Kunat Pipatanakul") fails
    step 3 because *none* of its words appear anywhere, not just because the phrase isn't
    contiguous -- so this step trades a small amount of recall (a fabricated name built entirely
    out of otherwise-common corpus words would slip through) for much better precision against real
    paraphrase, which is the more common case live."""
    if candidate.casefold() in corpus_cf:
        return True
    if entity_normalize.resolve_canonical(candidate) is not None:
        return True
    words = [w for w in re.split(r"[-\s]+", candidate) if len(w) >= 3]
    return len(words) >= 2 and all(w.casefold() in corpus_cf for w in words)


def _digits_grounded(candidate: str, corpus: str) -> bool:
    """Whether the digit run inside ``candidate`` (a money figure or a year) appears, as a
    standalone number (not a substring of a longer one), anywhere in ``corpus``."""
    digits = re.sub(r"[^\d]", "", candidate)
    if len(digits) < 2:
        return True  # too short a number to carry any grounding signal on its own
    return re.search(r"(?<!\d)" + re.escape(digits) + r"(?!\d)", corpus) is not None


def _grounding_violation(unit_text: str, corpus_cf: str) -> str | None:
    """The first ungrounded candidate entity/figure found in ``unit_text``, or ``None`` if every
    candidate it contains is grounded. Checked in this order: multi-word Latin proper nouns,
    quoted multi-word phrases, money figures, years."""
    for m in _PROPER_NOUN_RE.finditer(unit_text):
        name = m.group(0)
        if not _proper_noun_grounded(name, corpus_cf):
            return name
    for m in _QUOTED_RE.finditer(unit_text):
        phrase = m.group(1).strip()
        if len(phrase.split()) >= 2 and not _proper_noun_grounded(phrase, corpus_cf):
            return phrase
    for m in _MONEY_RE.finditer(unit_text):
        figure = m.group(0)
        if not _digits_grounded(figure, corpus_cf):
            return figure
    for m in _YEAR_RE.finditer(unit_text):
        year = m.group(0)
        if not _digits_grounded(year, corpus_cf):
            return year
    return None


def _cited_ns(unit_text: str) -> list[int]:
    return [int(m.group(1)) for m in _CITATION_RE.finditer(unit_text)]


def _conflation_violation(unit_text: str, cited_ns: list[int], sources_by_n: dict[int, str]) -> str | None:
    """The first watchlist-recognised company/system named in ``unit_text`` that is *not* present
    in any of the sources ``unit_text`` itself cites via ``[n]`` -- the Q2 pattern (a real entity,
    attributed to the wrong retrieved source). ``None`` if every watchlist entity the unit names is
    independently confirmed present in at least one of its own cited sources."""
    entities = entity_normalize.find_watchlist_aliases_in_text(unit_text)
    if not entities or not cited_ns:
        return None
    for entity in entities:
        if any(
            entity in entity_normalize.find_watchlist_aliases_in_text(sources_by_n.get(n, ""))
            for n in cited_ns
        ):
            continue
        return entity
    return None


def _money_conflation_violation(
    unit_text: str, cited_ns: list[int], sources_by_n: dict[int, str], corpus_cf: str
) -> str | None:
    """The first money figure in a ``[n]``-cited ``unit_text`` that is genuinely real (appears
    *somewhere* in the retrieved corpus) but does not appear in the specific source(s) this unit
    itself cites -- live-found 2026-09-06 (throwaway 8766, golden Q2, local resident model): the
    model took a real, correctly-quoted "$465 million" figure from a genuinely retrieved
    AeroVironment laser-contract item and wrote it into a Rafael/Iron Beam paragraph citing an
    unrelated "top 30 companies" ranking item instead -- the exact live reproduction of the round-2
    Rafael/AeroVironment conflation this whole guard exists for, except the conflated content here
    is a specific number rather than a watchlist-recognised company name, so
    :func:`_conflation_violation` (which only inspects watchlist entities) never saw it and
    :func:`_grounding_violation`'s corpus-wide money check never flagged it either (the figure is
    real, just cited to the wrong source).

    Deliberately narrower than generalizing this same "must be grounded in *its own* citation, not
    just the wider corpus" rule to proper nouns too: a sentence legitimately synthesizing facts
    from multiple retrieved sources under a single citation number is common and not itself wrong
    (round 2 flagged exactly this as a cosmetic-only issue for golden Q6), so a strict per-citation
    check on every named entity would newly misfire on that ordinary pattern. A specific money
    figure is a much more atomic, single-attribution fact in practice -- it is realistically always
    reported by exactly one source -- so this narrower, stricter check is safe where a general one
    would not be."""
    if not cited_ns:
        return None
    cited_cf = " ".join(sources_by_n.get(n, "") for n in cited_ns).casefold()
    for m in _MONEY_RE.finditer(unit_text):
        figure = m.group(0)
        digits = re.sub(r"[^\d]", "", figure)
        if len(digits) < 2:
            continue
        if _digits_grounded(figure, cited_cf):
            continue  # correctly grounded in its own citation
        if _digits_grounded(figure, corpus_cf):
            return figure  # real, but reported by a *different* retrieved source
    return None


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    out: list[str] = []
    prev = 0
    for start, end in sorted(spans):
        out.append(text[prev:start])
        prev = end
    out.append(text[prev:])
    return "".join(out)


def _tidy_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+([.,:;!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def ground_and_filter_answer(
    answer_text: str,
    question: str,
    retrieved: list[dict[str, Any]],
) -> tuple[str, int]:
    """Drop every sentence/bullet ("unit") of ``answer_text`` that fails either the grounded-entity
    check or the cross-source conflation guard (see the module docstring). Returns
    ``(new_text, removed_count)``; ``removed_count == 0`` (and ``new_text == answer_text``) when no
    unit was flagged, so a caller can skip re-emitting an unchanged answer.

    ``retrieved`` must be the same rows -- in the same order-independent set -- that
    ``eoa.api.services.ask_build_messages`` built the `[n]`-indexed citations from; this function
    re-derives the identical context-first ordering (`services.ask_build_messages`'s own
    ``sorted(retrieved, key=lambda r: 0 if r.get("_is_context") else 1)``) so `[n]` here means the
    exact same source the model itself was citing.

    A no-op (returns ``answer_text`` unchanged) when ``answer_text`` is blank or ``retrieved`` is
    empty -- with zero retrieved sources the model is expected (and told, in the system prompt) to
    fall back to disclosed general knowledge, which this check must not penalise; the guard is
    specifically about claims made *under cover of* a citation to an actually-retrieved source.
    """
    if not answer_text or not answer_text.strip() or not retrieved:
        return answer_text, 0

    ordered = sorted(retrieved, key=lambda r: 0 if r.get("_is_context") else 1)
    sources_by_n: dict[int, str] = {
        i: f"{row.get('title') or ''} {row.get('clean_text') or row.get('summary_he') or ''}"
        for i, row in enumerate(ordered, start=1)
    }
    corpus_cf = (question + " " + " ".join(sources_by_n.values())).casefold()

    # last section heading's own title as of each offset -- computed once, not per-unit.
    section_titles: list[tuple[int, str]] = [
        (m.start(), m.group(1).strip()) for m in _SECTION_HEADING_TEXT_RE.finditer(answer_text)
    ]

    def _section_at(pos: int) -> str:
        title = ""
        for heading_start, heading_title in section_titles:
            if heading_start > pos:
                break
            title = heading_title
        return title

    flagged: list[tuple[int, int, str]] = []
    for start, end in _iter_units(answer_text):
        unit_text = answer_text[start:end]
        if not unit_text.strip():
            continue
        reason = None
        if _ANALYST_ASSESSMENT_MARKER not in _section_at(start):
            reason = _grounding_violation(unit_text, corpus_cf)
        if reason is None:
            cited_ns = _cited_ns(unit_text)
            if cited_ns:
                reason = _conflation_violation(
                    unit_text, cited_ns, sources_by_n
                ) or _money_conflation_violation(unit_text, cited_ns, sources_by_n, corpus_cf)
        if reason:
            flagged.append((start, end, reason))

    if not flagged:
        return answer_text, 0

    heading_match = _FIRST_SECTION_HEADING_RE.search(answer_text)
    lead_end = heading_match.start() if heading_match else len(answer_text)
    lead_flag = next((f for f in flagged if f[0] < lead_end), None)

    new_text = _tidy_whitespace(_remove_spans(answer_text, [(s, e) for s, e, _ in flagged]))

    if lead_flag is not None:
        new_heading = _FIRST_SECTION_HEADING_RE.search(new_text)
        new_lead = new_text[: new_heading.start()] if new_heading else new_text
        if not _HEADING_LINE_RE.sub("", new_lead).strip():
            gap = _GAP_SENTENCE.format(entity=lead_flag[2])
            new_text = gap + "\n\n" + new_text.lstrip()

    return new_text, len(flagged)
