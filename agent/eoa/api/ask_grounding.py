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

Round 5 (docs/qa/loop/round_3_judge.md, worst-list items 3 and 8, and its "ranked by expected gain"
item 6): the round-3 judge, re-sampling a *third* time, found three more hallucination shapes that
slip past every guard above because -- exactly as with Q2/Q4 in round 3 -- the individual entities
involved are each independently real:

- **Q4 (DROIC):** the answer fabricated plausible-sounding ML jargon ("MAEC", "RSPEOT") attributed
  to Leonardo DRS -- a *single-word* invented acronym/product token, not a multi-word proper noun,
  so :func:`_PROPER_NOUN_RE` (which requires >= 2 capitalised segments) never saw it. This is the
  round-3 judge's own ranked-by-effort item 6: "extend D5's grounded-entity check to single-word
  technical acronyms, not just multi-word proper nouns."
- **Q7 (EO/IR RFI):** the answer called a Finnish MoD RFI "published by the US government" -- a
  self-contradiction between the document's real origin (per its own cited source) and the
  publisher/country the sentence asserts -- an *attribution* error, not an ungrounded entity (both
  "Finland" and "the US" are real countries).
- **Q5 (Skyranger vs. Israeli C-UAS):** the answer equated Israel's David's Sling with Germany's
  unrelated Skynex system -- an *entity-equivalence* error: both names are real, watchlist-adjacent
  systems, so neither the grounded-entity check nor the cross-source conflation guard (which only
  ever checks a *cited* entity against *its own* source, not against a *second* entity the sentence
  claims it is identical to) catches a false "X is Y" claim between two genuinely real things.

Four additive functions close these, all following the same pure/deterministic/precision-first
design as the round-3 guards above -- each is documented in full at its own definition:

3. :func:`_grounding_violation` (extended in place, same public :func:`ground_and_filter_answer`
   entry point and return contract as round 3 -- verified against every existing round-3 test to
   introduce zero new false positives) -- now also flags a single ALL-CAPS token (3-8 chars, may
   contain digits/hyphens -- "MAEC", "RSPEOT") or a CamelCase product-like single token
   ("SkyDefender") that is grounded in none of: the question, the retrieved sources, the canonical
   watchlist, the domain taxonomy's own vocabulary (:data:`config/taxonomy.yaml`'s Hebrew/English
   labels -- a real domain term like "DIRCM" or "Hyperspectral" must not be flagged just because
   this specific retrieval's sources didn't happen to repeat it), or a small allowlist of common,
   generic defense/EO-IR acronyms (:data:`_COMMON_DEFENSE_ACRONYMS`) that are domain vocabulary, not
   claims about a specific source.
4. :func:`filter_entity_equivalence` -- drops (replacing with an explicit gap sentence naming both
   names) a sentence asserting identity between two distinct, real-looking named systems/companies
   ("X הוא Y", "X, הידוע גם כ-Y", "X (Y)") unless some retrieved source's own text mentions *both*
   names together (the co-occurrence a genuine equivalence/translation-gloss claim would actually
   be sourced from).
5. :func:`filter_attribution_mismatches` -- when a sentence attributes a document (RFI/RFP/tender/
   contract) to a publisher/country, verifies the sentence's *own* cited source(s) actually mention
   that publisher/country; if not, drops only the attribution clause (keeping the underlying fact
   that such a document exists).
6. :func:`filter_self_contradictions` -- a cheap, deterministic cross-sentence pass over the whole
   answer: when two units assign the *same* figure/year to two disjoint sets of named
   entities/countries, keeps whichever unit's own cited source(s) actually contain that figure and
   drops the other.

None of these four talk to the database or the LLM either; each takes the same
``(answer_text, retrieved)`` shape (:func:`filter_entity_equivalence` and the two below it) or
``(answer_text, question, retrieved)`` (the extended :func:`ground_and_filter_answer`) as the round-3
guards, and each is a no-op on a blank answer or an empty ``retrieved`` list for the same reason
documented on :func:`ground_and_filter_answer` itself.

P10 (docs/qa/loop/round_5_chat_fixes.md, "New findings this round"): the round-5 write-up's own live
8-question x 2-sample verification pass found five more residual gaps in the guards above -- each
independently real, none a regression in what rounds 3/5 were built to fix:

1. A single-digit money magnitude ("5" in "5 מיליארד דולר") bypassed :func:`_digits_grounded`'s own
   ``< 2`` digit floor entirely -- extended via :func:`_money_figure_grounded`/
   :func:`_money_magnitude_grounded`, which check a single-digit money candidate as a magnitude
   (value + billion/million/thousand scale) against every money mention in the corpus instead of
   auto-passing it; a non-money single-digit number (a plain count) never reaches this path at all,
   since it was never matched by :data:`_MONEY_RE` in the first place.
2. :func:`_equivalence_violation` treated the question's own acronym/expansion gloss ("DROIC" /
   "Digital Read-Out Integrated Circuit") as a fabricated equivalence, because only a watchlist
   entity resolves via :func:`entity_normalize.resolve_canonical` -- fixed via
   :func:`_is_acronym_expansion_pair` (an acronym whose letters match the expansion's own initials)
   and an optional ``question`` parameter (both :func:`_equivalence_violation` and
   :func:`filter_entity_equivalence`; defaults to ``""`` so the existing call site in
   ``routes/ask.py`` keeps working unchanged) so either side appearing in the question is never
   treated as a fabricated claim either.
3. Every proper-noun/entity pattern in this module was Latin-script-only, so a fabricated Hebrew
   institution name (a live-found "מאוניברסיטת אריזונה סטייט" attributed to no real source) was
   invisible to every guard. :func:`_hebrew_entity_violation` closes this narrowly: only the
   institution/organisation head-noun shapes (אוניברסיטת/מכון/משרד/חיל/...), only on a ``[n]``-cited
   unit, exempting the question's own entities, any retrieved source (not only the cited one), and a
   small generic-institution allowlist -- reusing the existing citation-gated wiring so the
   ``### הערכת האנליסט`` exemption (which never carries a citation) already applies for free.
4. :func:`_iter_units` treated a numbered-list item (``1. ...``) as ordinary sentence-split prose,
   so dropping a flagged item left the list truncated mid-item; it is now a numbered-list-aware unit
   (the item line plus its continuation lines, mirroring the existing bullet handling), and
   :func:`_renumber_lists` renumbers the surviving items of each list 1..k after a removal.
5. :func:`retrieval_relevance_caveat` closes the Q3(LORA)/Q6(AUSA) "anchor-echo" pattern: the model
   can satisfy the existing anchor-presence guard (``routes.ask``'s own anchor-miss check, which
   looks at the *answer*) by repeating the question's own anchor term throughout an answer whose
   *retrieved sources* never once mention it. Using the same anchor-extraction approach as
   ``routes.ask``'s ``_strong_anchors``/``_primary_anchors`` (necessarily duplicated here, not
   imported, since ``routes.ask`` itself imports this module), it prepends an explicit Hebrew caveat
   paragraph -- never removing content -- when no retrieved source mentions any primary anchor.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from eoa.config import settings
from eoa.pipeline import entity_normalize
from eoa.search.deep_search import extract_anchors

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

# ---------------------------------------------------------------------------------------------
# Round 5 item 1: single-token ALL-CAPS acronym / CamelCase product-jargon check (round-3 judge's
# own ranked item 6, live Q4: "MAEC"/"RSPEOT" attributed to Leonardo DRS -- a single invented word,
# not a multi-word proper noun, so `_PROPER_NOUN_RE` above (which requires >= 2 capitalised
# segments) never saw it).
# ---------------------------------------------------------------------------------------------

# A small, deliberately short allowlist of common defense/EO-IR acronyms that are ordinary domain
# vocabulary, not a claim about any specific retrieved source -- flagging these every time this
# specific retrieval's sources didn't happen to also spell them out would be exactly the
# false-positive failure mode this module's docstring warns about (the round-3 "(C-UAS)" fix, same
# idea, generalised into a standing list instead of a one-off regex carve-out). Matched against the
# *uppercased* candidate token, so any casing a model uses ("MoD"/"MOD", "SWaP"/"SWAP") still hits.
_COMMON_DEFENSE_ACRONYMS = frozenset(
    {
        "EO/IR",
        "C-UAS",
        "RFI",
        "RFP",
        "ISR",
        "SWAP",
        "LRF",
        "FPA",
        "ROIC",
        "DROIC",
        "MWIR",
        "LWIR",
        "SWIR",
        "HEL",
        "DEW",
        "AI",
        "ML",
        "UAV",
        "UAS",
        "MOD",
        "DOD",
        "NATO",
        "EU",
        "US",
        "UK",
        "IDF",
        "IAF",
    }
)

# A single "word" token candidate for the ALL-CAPS/CamelCase check below -- letters/digits, with
# internal hyphens kept as part of one token (so "C-UAS" is one candidate, matching the brief's "may
# contain digits/hyphen"), not split into "C" and "UAS" the way the multi-word `_PROPER_NOUN_RE`
# treats hyphens as *segment* separators.
_SINGLE_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*\b")


def _is_allcaps_jargon_candidate(token: str) -> bool:
    """A 3-8 char token that is entirely uppercase letters/digits/hyphens and contains at least one
    letter (so a bare number is never a candidate here -- that is `_MONEY_RE`/`_YEAR_RE`'s job) --
    the brief's "ALL-CAPS token (3-8 chars, may contain digits/hyphen)" shape, e.g. "MAEC",
    "RSPEOT", "DROIC", "XM30", "C-UAS"."""
    if not (3 <= len(token) <= 8):
        return False
    if any(c.islower() for c in token):
        return False
    return any(c.isalpha() for c in token)


def _is_camelcase_candidate(token: str) -> bool:
    """A single hyphen-free word that starts uppercase and contains a lowercase run followed by a
    later uppercase letter -- a "CamelCase product-like token" per the brief (e.g. an invented
    "SkyDefender"), while a genuine plain-English Title-Case word ("The", "Program") or a real,
    watchlist-grounded name written the same way ("AeroVironment") is only rejected downstream by
    the grounding check itself, not by this shape test."""
    if "-" in token or len(token) < 4 or not token[0].isupper():
        return False
    seen_lower = False
    for ch in token[1:]:
        if ch.islower():
            seen_lower = True
        elif ch.isupper() and seen_lower:
            return True
    return False


@lru_cache(maxsize=1)
def _taxonomy_vocabulary() -> frozenset[str]:
    """Every ASCII word (casefolded) appearing anywhere in ``config/taxonomy.yaml``'s own labels
    (e.g. "DIRCM", "Hyperspectral", "Gimbals", "EO/IR") -- the domain's own standing vocabulary, so
    a real technical term this specific retrieval's sources didn't happen to repeat is never treated
    as an invented one just because it is jargon. Loaded via :func:`eoa.config.settings` (itself
    cached), so this adds no extra I/O beyond what the process already pays once at startup;
    ``lru_cache`` here only avoids re-walking the (small) parsed dict on every single answer."""
    words: set[str] = set()

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)
        elif isinstance(node, str):
            for m in re.finditer(r"[A-Za-z][A-Za-z0-9/\-]*", node):
                words.add(m.group(0).casefold())

    try:
        _walk(settings().taxonomy)
    except Exception:
        # Config loading is best-effort here -- a taxonomy load failure must never break the
        # grounding guard itself; it just falls back to the corpus/allowlist checks alone.
        return frozenset()
    return frozenset(words)


def _single_token_grounded(token: str, corpus_cf: str) -> bool:
    """Whether a single suspicious ALL-CAPS/CamelCase ``token`` is grounded: it (case-insensitively)
    appears in ``corpus_cf`` (question + retrieved sources), resolves to a canonical watchlist/
    curated-org record on its own, is one of the common domain acronyms in
    :data:`_COMMON_DEFENSE_ACRONYMS`, or is part of the domain taxonomy's own standing vocabulary
    (:func:`_taxonomy_vocabulary`)."""
    if token.casefold() in corpus_cf:
        return True
    if entity_normalize.resolve_canonical(token) is not None:
        return True
    if token.upper() in _COMMON_DEFENSE_ACRONYMS:
        return True
    return token.casefold() in _taxonomy_vocabulary()


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


# P10 item 4 (docs/qa/loop/round_5_chat_fixes.md, "New findings this round" #5, live Q5): a plain
# numbered-list item (``1. ...``) was previously just ordinary prose to `_iter_units` -- split on
# sentence punctuation like any other line -- so dropping a flagged numbered item left the list
# truncated mid-item rather than cleanly removing the whole entry, the same "not list-aware"
# limitation round 3 already documented for markdown tables.
_NUMBERED_ITEM_RE = re.compile(r"^\s*\d+[.)]\s")


def _iter_units(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` spans over ``text`` granular enough to drop individually without mangling
    the rest of the answer: a heading line is never a unit (structural, always kept as-is); a
    markdown bullet line (``-``/``*``) is one whole unit (dropping it removes the bullet marker
    too, leaving no orphan ``- ``); a numbered-list item (``1. ...``/``1) ...``) is one whole unit
    together with its own continuation lines -- any following line that is itself neither blank,
    a heading, another list item, nor a bullet -- so a wrapped numbered item is removed as a whole
    entry too, not just its first physical line; any other line is split into sentences on
    ``.!?``/gershayim. Callers that remove units from a numbered list should follow up with
    :func:`_renumber_lists` so the surviving items stay sequential."""
    units: list[tuple[int, int]] = []
    lines = text.splitlines(keepends=True)
    line_starts: list[int] = []
    pos = 0
    for line in lines:
        line_starts.append(pos)
        pos += len(line)

    i = 0
    n_lines = len(lines)
    while i < n_lines:
        line = lines[i]
        start = line_starts[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        if stripped.startswith(("-", "*")):
            units.append((start, start + len(line)))
            i += 1
            continue
        if _NUMBERED_ITEM_RE.match(line):
            end = start + len(line)
            j = i + 1
            while j < n_lines:
                nxt = lines[j]
                nxt_stripped = nxt.strip()
                if (
                    not nxt_stripped
                    or nxt_stripped.startswith("#")
                    or nxt_stripped.startswith(("-", "*"))
                    or _NUMBERED_ITEM_RE.match(nxt)
                ):
                    break
                end = line_starts[j] + len(nxt)
                j += 1
            units.append((start, end))
            i = j
            continue
        seg_start = 0
        for m in _SENTENCE_END_RE.finditer(line):
            end = m.end()
            units.append((start + seg_start, start + end))
            seg_start = end
        if line[seg_start:].strip():
            units.append((start + seg_start, start + len(line)))
        i += 1
    return units


_NUMBERED_ITEM_PREFIX_RE = re.compile(r"^(\s*)(\d+)([.)])(\s)")


def _renumber_lists(text: str) -> str:
    """Renumber every numbered-list item in ``text`` sequentially (1..k) within each contiguous
    list block -- called after a unit removal so a list that had an item dropped from its middle
    (e.g. items 1/2/4 surviving out of an original 1/2/3/4) reads as a clean 1/2/3 again instead of
    keeping the gap. A list block ends (and the next one restarts at 1) at a blank line, a heading,
    or a bullet line; an ordinary continuation line of the current item is left untouched and does
    not itself break the block."""
    out: list[str] = []
    counter = 0
    for line in text.splitlines(keepends=True):
        m = _NUMBERED_ITEM_PREFIX_RE.match(line)
        if m:
            counter += 1
            leading_ws, _old_num, punct, sep = m.groups()
            out.append(f"{leading_ws}{counter}{punct}{sep}{line[m.end() :]}")
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(("-", "*")):
            counter = 0
        out.append(line)
    return "".join(out)


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


# ---------------------------------------------------------------------------------------------
# P10 item 1: single-digit money-magnitude check (docs/qa/loop/round_5_chat_fixes.md, "New findings
# this round" #1 -- live Q1: a real "$1.53bn" cited figure was restated as "5 מיליארד דולר" and
# `_digits_grounded`'s own `< 2` digit floor (meant only to avoid flagging noise like a lone stray
# digit) waved it through untouched). Deliberately scoped to money figures only -- the floor exists
# specifically so a plain count ("3 מערכות") is never flagged, and a count is never even a candidate
# here since it was never matched by `_MONEY_RE` (which requires a currency symbol or scale word) in
# the first place.
# ---------------------------------------------------------------------------------------------

# Ordered longest-prefix-first so e.g. a "billion"/"bn" tail is matched before the bare "b" entry
# would otherwise shadow it via `str.startswith`.
_MONEY_SCALE_WORDS: tuple[tuple[str, float], ...] = (
    ("מיליארד", 1_000_000_000.0),
    ("billion", 1_000_000_000.0),
    ("bn", 1_000_000_000.0),
    ("b", 1_000_000_000.0),
    ("מיליון", 1_000_000.0),
    ("million", 1_000_000.0),
    ("m", 1_000_000.0),
    ("אלף", 1_000.0),
    ("thousand", 1_000.0),
    ("k", 1_000.0),
)

# A broader money-mention finder than `_MONEY_RE` (adds "£", "bn", "thousand"/"אלף" as scale
# indicators) used only to scan the *corpus* for a compatible magnitude to compare a single-digit
# money candidate against -- never used to decide what counts as a "money figure" candidate in the
# answer itself (that stays `_MONEY_RE`, unchanged, so no existing behaviour shifts).
_MONEY_MAGNITUDE_RE = re.compile(
    r"[$€₪£]\s?\d[\d,]*\.?\d*\s?(?:מיליארד|מיליון|אלף|billion|million|thousand|bn|[MBK])?"
    r"|\b\d[\d,]*\.?\d*\s?(?:מיליארד|מיליון|אלף|billion|million|thousand|bn)\b",
    re.IGNORECASE,
)


def _parse_money_value_scale(text: str) -> tuple[float, float] | None:
    """``(value, scale)`` for the first number in ``text`` -- ``scale`` is the billion/million/
    thousand multiplier of whatever scale word or letter suffix immediately follows the number
    (``1.0`` when none is present, i.e. a bare currency amount with no magnitude word at all).
    ``None`` if ``text`` contains no parseable number."""
    m = re.search(r"\d[\d,]*\.?\d*", text)
    if not m:
        return None
    try:
        value = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    tail = text[m.end() :].strip().lower()
    for word, mult in _MONEY_SCALE_WORDS:
        if tail.startswith(word):
            return value, mult
    return value, 1.0


def _money_magnitude_grounded(figure: str, corpus: str) -> bool:
    """Whether the money figure ``figure`` -- which must itself carry a recognisable billion/
    million/thousand scale word or suffix -- is grounded in ``corpus`` as a *magnitude*: some money
    mention in ``corpus``, converted to a common scale, rounds to the same value. This is how "5
    מיליארד" is grounded by "$5bn"/"5 billion"/"5,000 million"/"5.0 billion" (all the same value,
    however phrased) or by any corpus figure that itself rounds to 5 at the billion scale -- but NOT
    by "$1.53 billion" (rounds to 2, not 5), which is the exact live fabrication this closes.
    Returns ``False`` outright (never grounded by this check) when ``figure`` itself carries no
    recognisable scale word at all -- a bare, unscaled amount is the caller's own fallback to make,
    not this function's to guess at."""
    parsed = _parse_money_value_scale(figure)
    if parsed is None or parsed[1] <= 1.0:
        return False
    value, scale = parsed
    for m in _MONEY_MAGNITUDE_RE.finditer(corpus):
        corpus_parsed = _parse_money_value_scale(m.group(0))
        if corpus_parsed is None or corpus_parsed[1] <= 1.0:
            continue
        corpus_value, corpus_scale = corpus_parsed
        if round((corpus_value * corpus_scale) / scale) == round(value):
            return True
    return False


def _money_figure_grounded(figure: str, corpus: str) -> bool:
    """Whether a money-figure candidate ``figure`` (already matched by :data:`_MONEY_RE`) is
    grounded in ``corpus``. A figure with >= 2 digits defers unchanged to :func:`_digits_grounded`
    (the literal-substring check, exactly as before this fix). A single-digit magnitude is checked
    as a magnitude-with-unit via :func:`_money_magnitude_grounded` instead of auto-passing on
    :func:`_digits_grounded`'s own ``< 2`` digit floor -- unless ``figure`` itself carries no
    recognisable scale word (a bare "$5" with nothing else), in which case there is no magnitude to
    compare and this falls back to the original literal check rather than guessing."""
    digits = re.sub(r"[^\d]", "", figure)
    if len(digits) != 1:
        return _digits_grounded(figure, corpus)
    parsed = _parse_money_value_scale(figure)
    if parsed is None or parsed[1] <= 1.0:
        return _digits_grounded(figure, corpus)
    return _money_magnitude_grounded(figure, corpus)


def _grounding_violation(unit_text: str, corpus_cf: str) -> str | None:
    """The first ungrounded candidate entity/figure found in ``unit_text``, or ``None`` if every
    candidate it contains is grounded. Checked in this order: multi-word Latin proper nouns,
    quoted multi-word phrases, single-token ALL-CAPS/CamelCase jargon, money figures, years."""
    for m in _PROPER_NOUN_RE.finditer(unit_text):
        name = m.group(0)
        if not _proper_noun_grounded(name, corpus_cf):
            return name
    for m in _QUOTED_RE.finditer(unit_text):
        phrase = m.group(1).strip()
        if len(phrase.split()) >= 2 and not _proper_noun_grounded(phrase, corpus_cf):
            return phrase
    for m in _SINGLE_TOKEN_RE.finditer(unit_text):
        token = m.group(0)
        if not (_is_allcaps_jargon_candidate(token) or _is_camelcase_candidate(token)):
            continue
        if not _single_token_grounded(token, corpus_cf):
            return token
    for m in _MONEY_RE.finditer(unit_text):
        figure = m.group(0)
        if not _money_figure_grounded(figure, corpus_cf):
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
        if not digits:
            continue
        if len(digits) == 1:
            # P10 item 1: a single-digit magnitude ("5 מיליארד") -- checked the same
            # magnitude-with-unit way as `_money_figure_grounded`'s own single-digit branch, not
            # skipped outright the way this loop used to. A wholly invented single-digit magnitude
            # (grounded nowhere) is already this guard's cousin's job (`_grounding_violation`'s own
            # money loop, above in `_grounding_violation`) to catch, so this only ever needs to act
            # when the magnitude is real *somewhere* but not in its own citation.
            if not _money_magnitude_grounded(figure, cited_cf) and _money_magnitude_grounded(
                figure, corpus_cf
            ):
                return figure  # real magnitude, but reported by a *different* retrieved source
            continue
        if len(digits) < 2:
            continue
        if _digits_grounded(figure, cited_cf):
            continue  # correctly grounded in its own citation
        if _digits_grounded(figure, corpus_cf):
            return figure  # real, but reported by a *different* retrieved source
    return None


# ---------------------------------------------------------------------------------------------
# P10 item 3 (docs/qa/loop/round_5_chat_fixes.md, "New findings this round" #3, live Q4): every
# proper-noun/entity pattern in this module (`_PROPER_NOUN_RE`, `_QUOTED_RE`, `_SINGLE_TOKEN_RE`) is
# Latin-script-only, so a fabricated Hebrew institution name ("מאוניברסיטת אריזונה סטייט" -- Arizona
# State University -- attributed to an arXiv paper that names no institution at all) is invisible to
# every guard. Deliberately narrow: only the institution/organisation head-noun shapes below, never
# a bare Hebrew word, and gated on the unit carrying a citation (mirroring `_conflation_violation`/
# `_money_conflation_violation` above, both of which the `### הערכת האנליסט` section already
# structurally never triggers, since that section carries no `[n]` at all by format rule 3 -- so
# wiring this guard into the same `cited_ns`-gated call site the two guards above use gets that same
# exemption for free, with no separate section check needed).
# ---------------------------------------------------------------------------------------------

_HEBREW_PREFIX_LETTERS = "בלמוהשכ"

_HEBREW_HEAD_NOUNS = (
    "אוניברסיטת",
    "מכון",
    "חברת",
    "המכון",
    "משרד",
    "סוכנות",
    "מעבדת",
    "מעבדות",
    "מרכז",
    "קבוצת",
    "תאגיד",
    "מפעל",
    "אגף",
    "חיל",
    "זרוע",
    "פיקוד",
)
# Longest-first: a strict prefix relationship among the words above (e.g. "מעבדת"/"מעבדות") would
# otherwise let the shorter alternative pre-empt a match of the longer one at the same position.
_HEBREW_HEAD_NOUN_ALT = "|".join(sorted(_HEBREW_HEAD_NOUNS, key=len, reverse=True))

# A small set of Hebrew function words excluded from the "1-3 following tokens" span below so a
# genuine institution name doesn't greedily swallow the rest of its containing sentence (e.g.
# "משרד ההגנה של פינלנד פרסם..." must stop at "משרד ההגנה", not continue through "של"/"פינלנד").
_HEBREW_ENTITY_STOP_TOKENS = ("של", "עם", "על", "את", "אל", "כי", "גם", "רק", "לא", "כן", "זה", "זו")
_HEBREW_ENTITY_TOKEN_RE = (
    r"(?!(?:" + "|".join(_HEBREW_ENTITY_STOP_TOKENS) + r")\b)(?:[א-ת]+(?:[\"'][א-ת]+)?|[A-Za-z][A-Za-z0-9]*)"
)

# An optional single glued Hebrew prefix-preposition (ב/ל/מ/ו/ה/ש/כ -- Hebrew attaches these with no
# separating space, e.g. "מ" + "אוניברסיטת" = "מאוניברסיטת") in front of the head noun, followed by
# 1-3 Hebrew/Latin "name" tokens. `(?<![א-ת])` keeps the optional prefix letter from being consumed
# out of the middle of some earlier, unrelated Hebrew word.
_HEBREW_INSTITUTION_RE = re.compile(
    rf"(?<![א-ת])[{_HEBREW_PREFIX_LETTERS}]?(?:{_HEBREW_HEAD_NOUN_ALT})"
    rf"(?:\s+{_HEBREW_ENTITY_TOKEN_RE}){{1,3}}"
)

# Generic Israeli/US institutional names that are ordinary domain vocabulary, not a claim about any
# specific retrieved source -- the same rationale as `_COMMON_DEFENSE_ACRONYMS` above, just for
# Hebrew institution phrases instead of Latin acronyms.
_HEBREW_INSTITUTION_ALLOWLIST = (
    "משרד הביטחון",
    "משרד ההגנה",
    "חיל האוויר",
    "חיל הים",
    "חיל היבשה",
    "הפנטגון",
    'צה"ל',
    "הצבא",
    "הממשלה",
    "הקונגרס",
    "הסנאט",
    'נאט"ו',
    "האיחוד האירופי",
)

_HEBREW_FINAL_TO_REGULAR = {"ך": "כ", "ם": "מ", "ן": "נ", "ף": "פ", "ץ": "צ"}


def _normalize_hebrew_finals(text: str) -> str:
    """Map Hebrew final-letter forms (ך/ם/ן/ף/ץ) to their regular counterparts (כ/מ/נ/פ/צ) so a
    name compared across two different grammatical positions still matches on its consonants."""
    return "".join(_HEBREW_FINAL_TO_REGULAR.get(ch, ch) for ch in text)


def _strip_hebrew_head_prefix(phrase: str) -> str:
    """Strip a single leading Hebrew prefix-preposition letter glued directly onto the head noun of
    ``phrase`` (e.g. "מאוניברסיטת אריזונה" -> "אוניברסיטת אריזונה") -- the source text a fabricated
    mention like this would be checked against is very likely to spell the bare head noun with no
    such prefix attached at all."""
    if phrase and phrase[0] in _HEBREW_PREFIX_LETTERS:
        candidate = phrase[1:]
        if any(candidate.startswith(noun) for noun in _HEBREW_HEAD_NOUNS):
            return candidate
    return phrase


def _hebrew_entity_grounded(phrase: str, corpus_cf: str, corpus_finals_cf: str) -> bool:
    """Whether the Hebrew institution-shaped ``phrase`` (already stripped of its own outer
    whitespace) is grounded in ``corpus_cf`` -- checked at every trailing-token length from the full
    phrase down to just "head noun + first token", not only the full greedy match. The 1-3 trailing
    tokens :data:`_HEBREW_INSTITUTION_RE` captures are a shape heuristic, not a guarantee that every
    one of them is actually part of the institution's own name (Hebrew has no capitalisation to mark
    where a proper noun ends) -- e.g. "מכון ויצמן פרסם" greedily includes the following verb
    "פרסם" ("published"), which a real source would never itself repeat verbatim. Trying
    progressively shorter prefixes lets a real, correctly-cited institution ("מכון ויצמן") still
    ground even when the regex captured extra trailing words that happen not to be on the small
    stop-word list -- while a genuinely fabricated name still fails at every length, since none of
    its prefixes appear anywhere either."""
    stripped = _strip_hebrew_head_prefix(phrase)
    latin_m = re.search(r"[A-Za-z][A-Za-z0-9]*", phrase)
    if latin_m and latin_m.group(0).casefold() in corpus_cf:
        return True
    words = stripped.split()
    for length in range(len(words), 1, -1):
        candidate = " ".join(words[:length])
        if any(
            candidate == entry or candidate.startswith(entry + " ") for entry in _HEBREW_INSTITUTION_ALLOWLIST
        ):
            return True
        candidate_cf = candidate.casefold()
        if candidate_cf in corpus_cf or _normalize_hebrew_finals(candidate_cf) in corpus_finals_cf:
            return True
    return False


def _hebrew_entity_violation(unit_text: str, corpus_cf: str) -> str | None:
    """The first Hebrew institution/organisation-shaped phrase in ``unit_text`` (see
    :data:`_HEBREW_INSTITUTION_RE`) that :func:`_hebrew_entity_grounded` finds grounded nowhere in
    ``corpus_cf`` (the question plus every retrieved source, already casefolded -- so a mention in
    the question, or in *any* retrieved source and not only the ones this unit itself cites, counts
    as grounded) -- else ``None``. Only ever called on a unit that carries a citation (see the
    module docstring's P10 item 3 note for why that alone gives the ``### הערכת האנליסט`` exemption
    for free)."""
    corpus_finals_cf = _normalize_hebrew_finals(corpus_cf)
    for m in _HEBREW_INSTITUTION_RE.finditer(unit_text):
        phrase = m.group(0).strip()
        if not _hebrew_entity_grounded(phrase, corpus_cf, corpus_finals_cf):
            return phrase
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
                reason = (
                    _conflation_violation(unit_text, cited_ns, sources_by_n)
                    or _money_conflation_violation(unit_text, cited_ns, sources_by_n, corpus_cf)
                    or _hebrew_entity_violation(unit_text, corpus_cf)
                )
        if reason:
            flagged.append((start, end, reason))

    if not flagged:
        return answer_text, 0

    heading_match = _FIRST_SECTION_HEADING_RE.search(answer_text)
    lead_end = heading_match.start() if heading_match else len(answer_text)
    lead_flag = next((f for f in flagged if f[0] < lead_end), None)

    new_text = _renumber_lists(_tidy_whitespace(_remove_spans(answer_text, [(s, e) for s, e, _ in flagged])))

    if lead_flag is not None:
        new_heading = _FIRST_SECTION_HEADING_RE.search(new_text)
        new_lead = new_text[: new_heading.start()] if new_heading else new_text
        if not _HEADING_LINE_RE.sub("", new_lead).strip():
            gap = _GAP_SENTENCE.format(entity=lead_flag[2])
            new_text = gap + "\n\n" + new_text.lstrip()

    return new_text, len(flagged)


def _replace_spans(text: str, replacements: list[tuple[int, int, str]]) -> str:
    """Like :func:`_remove_spans`, but each span is replaced with its own ``replacement`` text
    rather than deleted outright -- used by the guards below that redact or explain a claim in
    place instead of dropping the whole containing unit."""
    out: list[str] = []
    prev = 0
    for start, end, replacement in sorted(replacements, key=lambda r: r[0]):
        out.append(text[prev:start])
        out.append(replacement)
        prev = end
    out.append(text[prev:])
    return "".join(out)


def _sources_by_n(retrieved: list[dict[str, Any]]) -> dict[int, str]:
    """The same ``{[n] -> "title text"}`` mapping, in the same context-first ``[n]`` numbering, that
    :func:`ground_and_filter_answer` builds -- shared by every guard below so `[n]` always means the
    same source everywhere in this module."""
    ordered = sorted(retrieved, key=lambda r: 0 if r.get("_is_context") else 1)
    return {
        i: f"{row.get('title') or ''} {row.get('clean_text') or row.get('summary_he') or ''}"
        for i, row in enumerate(ordered, start=1)
    }


# ---------------------------------------------------------------------------------------------
# Round 5 item 2: entity-equivalence guard -- live Q5, David's Sling (Israel) wrongly equated with
# Skynex (Rheinmetall/Germany, unrelated). Both names are real, so neither the grounded-entity check
# nor the cross-source conflation guard above (which only ever checks a *cited* entity against its
# *own* source, never against a *second* entity the sentence claims it is identical to) catches a
# false "X is Y" claim between two genuinely real things.
# ---------------------------------------------------------------------------------------------

# A capitalised Latin "name" -- one or more space/hyphen-joined capitalised words, each allowed an
# internal apostrophe ("David's", "Iron's" wouldn't matter either way) -- deliberately looser than
# `_PROPER_NOUN_RE` (which requires >= 2 segments): a single-word system name ("Skynex") must be
# just as eligible a side of an equivalence claim as a multi-word one ("David's Sling").
_EQUIV_NAME = r"[A-Z][A-Za-z0-9'’]*(?:[-\s][A-Z][A-Za-z0-9'’]*)*"
_EQUIV_COPULA_RE = re.compile(
    rf"(?P<a>{_EQUIV_NAME})\s+(?:הוא|היא|הינו|הינה)(?:\s+למעשה)?\s+(?P<b>{_EQUIV_NAME})"
)
_EQUIV_KNOWN_AS_RE = re.compile(
    rf"(?P<a>{_EQUIV_NAME}),?\s*(?:ה)?ידוע(?:ה)?\s+גם\s+כ-?\s*(?P<b>{_EQUIV_NAME})"
)
_EQUIV_PAREN_RE = re.compile(rf"(?P<a>{_EQUIV_NAME})\s*\(\s*(?P<b>{_EQUIV_NAME})\s*\)")
_EQUIV_PATTERNS = (_EQUIV_COPULA_RE, _EQUIV_KNOWN_AS_RE, _EQUIV_PAREN_RE)

_EQUIV_GAP = "לא ניתן לאשר זהות בין {a} ל-{b} — המקורות אינם מזכירים את שניהם יחד."


# P10 item 2 (docs/qa/loop/round_5_chat_fixes.md, "New findings this round" #2, live Q4): the
# question's own acronym/expansion gloss ("DROIC" / "Digital Read-Out Integrated Circuit") was
# treated as a fabricated equivalence claim, because the "same canonical entity" skip below only
# ever fires when *both* sides resolve via `entity_normalize.resolve_canonical` -- which only knows
# watchlist companies/systems, not a generic technical acronym like "DROIC". A true acronym paired
# with its own expansion is a gloss, not a claim that two distinct things are the same.
_ACRONYM_STOP_WORDS = frozenset({"of", "the", "and", "for", "a", "an", "in", "on", "to"})


def _acronym_initials(expansion: str) -> str:
    """The initial letter of each meaningful word in ``expansion`` -- hyphenated words counted
    separately (``"Read-Out"`` contributes both "R" and "O"), common stop words ("of"/"the"/"and"/
    "for"/...) ignored -- e.g. "Digital Read-Out Integrated Circuit" (Digital, Read, Out,
    Integrated, Circuit) -> "DROIC"."""
    letters: list[str] = []
    for word in re.split(r"[-\s]+", expansion.strip()):
        cleaned = word.strip(" ,.")
        if not cleaned or cleaned.lower() in _ACRONYM_STOP_WORDS:
            continue
        if cleaned[0].isalpha():
            letters.append(cleaned[0].upper())
    return "".join(letters)


def _is_acronym_expansion_pair(name_a: str, name_b: str) -> bool:
    """Whether ``(name_a, name_b)`` -- checked in either order -- looks like an acronym paired with
    its own gloss/expansion (e.g. "DROIC" / "Digital Read-Out Integrated Circuit", or "ROIC" /
    "Read-Out Integrated Circuit"): the acronym's own letters equal, or appear as a contiguous run
    within, the initials of the expansion's words (:func:`_acronym_initials`) -- "allowing partial"
    per the brief, since a real gloss doesn't always spell out every word the acronym stands for.
    This is a legitimate acronym=expansion gloss, not a fabricated equivalence claim between two
    distinct things."""
    for acronym, expansion in ((name_a, name_b), (name_b, name_a)):
        letters = re.sub(r"[^A-Za-z]", "", acronym).upper()
        if len(letters) < 2:
            continue
        if len(re.split(r"[-\s]+", expansion.strip())) < 2:
            continue  # an expansion needs at least 2 words to have "initials" at all
        initials = _acronym_initials(expansion)
        if len(initials) >= 2 and (letters in initials or initials in letters):
            return True
    return False


def _looks_like_named_system_or_company(candidate: str) -> bool:
    """Whether ``candidate`` looks like a real named system/company for the purposes of this guard:
    either it resolves on the watchlist/curated-org table on its own, or it has the shape of one (a
    capitalised word or phrase, not a stray fragment) -- deliberately permissive on its own (a
    genuinely generic capitalised phrase would just as often be independently grounded by the
    co-occurrence check below), matching the module's usual precision-comes-from-the-grounding-
    check-not-the-shape-test design (see e.g. `_is_camelcase_candidate`'s own docstring)."""
    candidate = candidate.strip()
    if len(candidate) < 3:
        return False
    if entity_normalize.resolve_canonical(candidate) is not None:
        return True
    return bool(re.fullmatch(_EQUIV_NAME, candidate))


def _question_asserts_gloss(question_cf: str, name_a: str, name_b: str) -> bool:
    """True when the *question* itself presents ``name_a`` and ``name_b`` as one thing: one sits in a
    parenthetical gloss right after the other (``DROIC (Digital Read-Out Integrated Circuit)``) or
    they are joined by an explicit "/" or "או"/"or". Merely mentioning both ("what is the relation
    between David's Sling and Skynex?") does NOT make an answer's "X is Y" claim the question's own --
    that was the round-5 P10 false negative on the live David's Sling/Skynex true positive."""
    a, b = re.escape(name_a.casefold()), re.escape(name_b.casefold())
    for x, y in ((a, b), (b, a)):
        if re.search(rf"{x}\s*[\(\[]\s*{y}\s*[\)\]]", question_cf):
            return True
        if re.search(rf"{x}\s*(?:/|\bאו\b|\bor\b)\s*{y}", question_cf):
            return True
    return False


def _equivalence_violation(
    unit_text: str, sources_by_n: dict[int, str], question: str = ""
) -> tuple[str, str] | None:
    """The ``(name_a, name_b)`` pair of a false equivalence claim in ``unit_text``, or ``None``.
    Skips a pair that resolves to the *same* canonical watchlist record (a legitimate bilingual/
    alias gloss, e.g. "Rafael (Rafael Advanced Defense Systems)"), a pair that is itself an
    acronym/expansion gloss (:func:`_is_acronym_expansion_pair` -- e.g. "DROIC" / "Digital Read-Out
    Integrated Circuit"), a pair where either side is named in ``question`` itself (the question's
    own gloss is never a fabricated claim -- ``question`` defaults to ``""`` so an existing caller
    that doesn't have it handy keeps working unchanged, just without this particular exemption),
    and skips a pair grounded by at least one retrieved source's own text mentioning both names
    together -- approximated here as "the same source item's title+text", the closest available
    proxy for "the same paragraph" given this module never sees the sources' own internal paragraph
    breaks."""
    question_cf = question.casefold()
    for pattern in _EQUIV_PATTERNS:
        for m in pattern.finditer(unit_text):
            name_a, name_b = m.group("a").strip(), m.group("b").strip()
            if not name_a or not name_b or name_a.casefold() == name_b.casefold():
                continue
            if not (
                _looks_like_named_system_or_company(name_a) and _looks_like_named_system_or_company(name_b)
            ):
                continue
            canon_a = entity_normalize.resolve_canonical(name_a)
            canon_b = entity_normalize.resolve_canonical(name_b)
            if canon_a is not None and canon_b is not None and canon_a["name"] == canon_b["name"]:
                continue  # same entity via alias -- not an equivalence claim between two things
            if _is_acronym_expansion_pair(name_a, name_b):
                continue  # a gloss ("DROIC" / "Digital Read-Out Integrated Circuit"), not a claim
            if question_cf and _question_asserts_gloss(question_cf, name_a, name_b):
                continue  # the question itself glosses one as the other: "DROIC (Digital Read-Out ...)"
            a_cf, b_cf = name_a.casefold(), name_b.casefold()
            grounded = any(
                a_cf in text.casefold() and b_cf in text.casefold() for text in sources_by_n.values()
            )
            if not grounded:
                return name_a, name_b
    return None


def filter_entity_equivalence(
    answer_text: str, retrieved: list[dict[str, Any]], question: str = ""
) -> tuple[str, int]:
    """Replace every unit of ``answer_text`` asserting a false equivalence between two distinct,
    real-looking named systems/companies (see :func:`_equivalence_violation`) with an explicit gap
    sentence naming both. A no-op on a blank answer or empty ``retrieved`` (same rationale as
    :func:`ground_and_filter_answer`). ``question`` is optional (defaults to ``""``) so the existing
    call site in ``routes/ask.py`` keeps working unchanged; passing it lets the question's-own-gloss
    exemption in :func:`_equivalence_violation` actually take effect."""
    if not answer_text or not answer_text.strip() or not retrieved:
        return answer_text, 0
    sources_by_n = _sources_by_n(retrieved)
    replacements: list[tuple[int, int, str]] = []
    for start, end in _iter_units(answer_text):
        unit_text = answer_text[start:end]
        if not unit_text.strip():
            continue
        violation = _equivalence_violation(unit_text, sources_by_n, question)
        if violation is not None:
            name_a, name_b = violation
            replacements.append((start, end, _EQUIV_GAP.format(a=name_a, b=name_b) + "\n"))
    if not replacements:
        return answer_text, 0
    new_text = _tidy_whitespace(_replace_spans(answer_text, replacements))
    return new_text, len(replacements)


# ---------------------------------------------------------------------------------------------
# Round 5 item 3: attribution-consistency guard -- live Q7, a Finnish MoD RFI called "published by
# the US government". Both "Finland" and "the US" are real countries, so the grounded-entity check
# never fires; the fabrication is entirely in *which* country the sentence attributes the document
# to, not in any single named entity being invented.
# ---------------------------------------------------------------------------------------------

_DOC_TYPE_WORDS = ("RFI", "RFP", "מכרז", "מכרזים", "טנדר", "tender", "חוזה", "contract", "קול קורא")

# The clause naming *who* published/led the document -- captured separately from the surrounding
# sentence so only this clause (never the underlying "a document exists" fact) is ever removed.
_ATTRIBUTION_RE = re.compile(
    r"(?P<clause>(?:ש?פורסם(?:ה)?|ש?פרסמ(?:ה|ו)|מטעם|בהובלת)\s+"
    r"(?:על\s+ידי\s+)?(?:ה)?(?:ממשלת\s+)?"
    r"(?P<who>[A-Za-zא-ת\"'׳״.\-\s]{2,30}?))"
    r"(?=[\s,.;)\]]|$)"
)

# A single Latin or Hebrew "word" -- the building block `_countries_mentioned` below slides a
# 1-3-word window over, since a country name is one word ("פינלנד") or, for a few, two
# ("ארצות הברית") -- trying only the longest possible window (as a single combined regex would)
# would greedily swallow an unrelated following word ("פינלנד בשנת") and never separately try the
# single word alone, missing the match `resolve_country_name` would otherwise find.
_WORD_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z.\-]*|[א-ת\"'״]+")


def _countries_mentioned(text: str) -> set[str]:
    """Every country's canonical English display name (:func:`entity_normalize.resolve_country_name`)
    mentioned anywhere in ``text`` -- tries every 1-, 2-, and 3-word window of ``text``'s own word
    tokens as a candidate rather than requiring the caller to already know which substring to test."""
    words = [m.group(0) for m in _WORD_TOKEN_RE.finditer(text)]
    found: set[str] = set()
    for i in range(len(words)):
        for size in (1, 2, 3):
            if i + size > len(words):
                break
            country = entity_normalize.resolve_country_name(" ".join(words[i : i + size]))
            if country:
                found.add(country)
    return found


def _attribution_violation(
    unit_text: str, cited_ns: list[int], sources_by_n: dict[int, str]
) -> tuple[int, int] | None:
    """The ``(start, end)`` span (relative to ``unit_text``) of an attribution clause that names a
    publisher/country for a document (RFI/RFP/tender/contract) mentioned in ``unit_text``, when that
    publisher/country is verifiably absent from every one of ``unit_text``'s own cited sources --
    else ``None``. Deliberately does nothing when the claimed publisher cannot be resolved to either
    a country or a canonical watchlist/curated-org record at all (an unverifiable claim is not the
    same as a contradicted one -- this guard only ever acts on a claim it can actually check)."""
    if not cited_ns:
        return None
    if not any(kw.casefold() in unit_text.casefold() for kw in _DOC_TYPE_WORDS):
        return None
    cited_text = " ".join(sources_by_n.get(n, "") for n in cited_ns)
    cited_countries = _countries_mentioned(cited_text)
    # Scan *every* candidate clause in the unit, not just the first: a sentence naming the document
    # type itself ("פורסם RFI חדש...") can spuriously match the same verb before the real
    # publisher-naming clause later in the same sentence ("...שפורסם על ידי ממשלת X") -- the first
    # match alone is not necessarily the attribution claim worth checking.
    for m in _ATTRIBUTION_RE.finditer(unit_text):
        who = m.group("who").strip(' .,;)״"')
        if not who:
            continue
        claimed_countries = _countries_mentioned(who)
        claimed_org = entity_normalize.resolve_canonical(who)
        if not claimed_countries and claimed_org is None:
            continue  # nothing verifiable extracted from this candidate -- do not guess

        if claimed_countries and claimed_countries & cited_countries:
            continue  # attribution confirmed by its own citation
        if claimed_org is not None and claimed_org["name"] in entity_normalize.find_watchlist_aliases_in_text(
            cited_text
        ):
            continue  # attribution confirmed by its own citation
        return m.span("clause")
    return None


def filter_attribution_mismatches(answer_text: str, retrieved: list[dict[str, Any]]) -> tuple[str, int]:
    """Remove just the attribution clause (see :func:`_attribution_violation`) from every unit of
    ``answer_text`` that attributes a document to a publisher/country its own cited source(s) do not
    mention -- keeping the rest of the sentence (the underlying fact that the document exists)
    intact. A no-op on a blank answer or empty ``retrieved``."""
    if not answer_text or not answer_text.strip() or not retrieved:
        return answer_text, 0
    sources_by_n = _sources_by_n(retrieved)
    replacements: list[tuple[int, int, str]] = []
    for start, end in _iter_units(answer_text):
        unit_text = answer_text[start:end]
        if not unit_text.strip():
            continue
        cited_ns = _cited_ns(unit_text)
        span = _attribution_violation(unit_text, cited_ns, sources_by_n)
        if span is not None:
            rel_start, rel_end = span
            replacements.append((start + rel_start, start + rel_end, ""))
    if not replacements:
        return answer_text, 0
    new_text = _replace_spans(answer_text, replacements)
    # Tidy the punctuation/whitespace seam a mid-sentence clause removal leaves behind (e.g. ", [1]."
    # after the clause between the comma and the citation is gone) -- `_tidy_whitespace` alone only
    # collapses runs of spaces/tabs, not a now-dangling comma.
    new_text = re.sub(r"[ \t]*,[ \t]*(?=[.\[\]])", " ", new_text)
    new_text = _tidy_whitespace(new_text)
    return new_text, len(replacements)


# ---------------------------------------------------------------------------------------------
# Round 5 item 4: self-contradiction pass -- a cheap, deterministic cross-sentence check for two
# units in the same answer assigning the same figure/year to two disjoint sets of named entities/
# countries (round 3's judge worst-list item 1 documented this same shape live in a weekly report's
# deep-investigation section, out of this module's/D5's own scope to fix there -- this closes the
# analogous chat-answer case, e.g. two sentences dating or attributing the same document
# differently).
# ---------------------------------------------------------------------------------------------


def _unit_subjects(unit_text: str) -> set[str]:
    """Every watchlist-recognised company/system name plus every country mentioned in
    ``unit_text`` -- the "who/what this sentence is about" set two units are compared on."""
    subjects = set(entity_normalize.find_watchlist_aliases_in_text(unit_text))
    subjects |= _countries_mentioned(unit_text)
    return subjects


def _unit_figures(unit_text: str) -> set[str]:
    """The digit-only form of every money figure/year in ``unit_text`` that is long enough (>= 2
    digits) to carry any grounding signal on its own -- matching :func:`_digits_grounded`'s own
    floor so a figure too short to mean anything never drives a contradiction verdict."""
    raw = _MONEY_RE.findall(unit_text) + _YEAR_RE.findall(unit_text)
    digits = {re.sub(r"[^\d]", "", f) for f in raw}
    return {d for d in digits if len(d) >= 2}


def filter_self_contradictions(answer_text: str, retrieved: list[dict[str, Any]]) -> tuple[str, int]:
    """Drop one unit of every pair of units in ``answer_text`` that assign the *same* figure/year
    (:func:`_unit_figures`) to two disjoint sets of named entities/countries
    (:func:`_unit_subjects`) -- keeping whichever unit's own cited source(s) actually contain that
    figure and dropping the other. Takes no action on a pair where neither or both units are
    grounded in their own citation (genuinely ambiguous -- this guard only acts when it can tell
    which sentence is right, never to arbitrarily pick one). A no-op on a blank answer or empty
    ``retrieved``."""
    if not answer_text or not answer_text.strip() or not retrieved:
        return answer_text, 0
    sources_by_n = _sources_by_n(retrieved)
    units = [(s, e, answer_text[s:e]) for s, e in _iter_units(answer_text) if answer_text[s:e].strip()]

    drop: set[int] = set()
    for i in range(len(units)):
        if i in drop:
            continue
        _, _, text_i = units[i]
        figures_i = _unit_figures(text_i)
        if not figures_i:
            continue
        subjects_i = _unit_subjects(text_i)
        if not subjects_i:
            continue
        for j in range(i + 1, len(units)):
            if j in drop:
                continue
            _, _, text_j = units[j]
            shared = figures_i & _unit_figures(text_j)
            if not shared:
                continue
            subjects_j = _unit_subjects(text_j)
            if not subjects_j or (subjects_i & subjects_j):
                continue  # no genuine subject conflict -- e.g. corroborating, not contradicting
            own_i = " ".join(sources_by_n.get(n, "") for n in _cited_ns(text_i)).casefold()
            own_j = " ".join(sources_by_n.get(n, "") for n in _cited_ns(text_j)).casefold()
            grounded_i = any(_digits_grounded(f, own_i) for f in shared)
            grounded_j = any(_digits_grounded(f, own_j) for f in shared)
            if grounded_i and not grounded_j:
                drop.add(j)
            elif grounded_j and not grounded_i:
                drop.add(i)
                break  # unit i is gone -- stop comparing it against later units

    if not drop:
        return answer_text, 0
    spans = [(units[k][0], units[k][1]) for k in drop]
    new_text = _renumber_lists(_tidy_whitespace(_remove_spans(answer_text, spans)))
    return new_text, len(drop)


# ---------------------------------------------------------------------------------------------
# P10 item 5 (docs/qa/loop/round_5_chat_fixes.md, "New findings this round" #4, live Q3/LORA and
# Q6/AUSA): the existing anchor-miss guard in `eoa.api.routes.ask` checks whether the *answer*
# mentions the question's own anchor -- but a model can satisfy that trivially by repeating the
# anchor term throughout an answer whose *retrieved sources* never once mention it (Q6/AUSA: the
# model wrote about an unrelated "Commercial UAV Expo" source while framing the whole answer as if
# it were about "AUSA", which the anchor-miss check never catches since "AUSA" genuinely appears
# throughout the answer body). This is a retrieval-relevance problem, not an answer-wording one, so
# it is checked the other way around: does *any retrieved source* mention the anchor at all.
#
# Reuses the same anchor-extraction approach as `eoa.api.routes.ask`'s own `_strong_anchors`/
# `_primary_anchors` (Latin-script tokens embedded in each `extract_anchors` anchor, narrowed to the
# non-parenthetical ones when any exist) -- necessarily duplicated here rather than imported, since
# `routes.ask` itself imports this module (`from eoa.api import ask_grounding, services`), so the
# reverse import would be circular.
# ---------------------------------------------------------------------------------------------

_CAVEAT_LATIN_ANCHOR_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
_CAVEAT_PAREN_SPAN_RE = re.compile(r"\(([^()]*)\)")


def _caveat_strong_anchors(anchors: list[str]) -> list[str]:
    """Same extraction as ``eoa.api.routes.ask._strong_anchors`` (duplicated, see the section note
    above): embedded Latin-script tokens (len >= 2) pulled out of each raw ``extract_anchors``
    anchor, deduplicated case-insensitively, in order of first appearance."""
    strong: list[str] = []
    seen: set[str] = set()
    for anchor in anchors:
        for m in _CAVEAT_LATIN_ANCHOR_RE.finditer(anchor):
            token = m.group(0)
            if len(token) < 2:
                continue
            key = token.casefold()
            if key not in seen:
                seen.add(key)
                strong.append(token)
    return strong


def _caveat_primary_anchors(question: str, strong_anchors: list[str]) -> list[str]:
    """Same logic as ``eoa.api.routes.ask._primary_anchors`` (duplicated, see the section note
    above): ``strong_anchors`` that do not come from a parenthetical gloss in ``question`` --
    falls back to every strong anchor when none of them is primary."""
    gloss_text = " ".join(m.group(1) for m in _CAVEAT_PAREN_SPAN_RE.finditer(question)).casefold()
    primary = [a for a in strong_anchors if a.casefold() not in gloss_text]
    return primary or strong_anchors


_RETRIEVAL_RELEVANCE_CAVEAT = (
    '> ⚠️ אף אחד מהמקורות שאותרו אינו מזכיר במפורש את "{anchor}"; התשובה שלהלן מתבססת על מקורות '
    "סמוכים בלבד ויש להתייחס אליה כהקשר כללי ולא כממצא ישיר."
)


def retrieval_relevance_caveat(
    answer_text: str, question: str, retrieved: list[dict[str, Any]]
) -> tuple[str, bool]:
    """Prepend an explicit Hebrew caveat paragraph -- at the very top of ``answer_text``, before any
    heading -- when there is at least one primary anchor (see the module-level note above) extracted
    from ``question`` and *none* of ``retrieved``'s own title/summary/text (casefolded, same
    normalisation the anchor guard uses) mentions any of them. Returns ``(new_text, caveat_added)``;
    never removes any content, and is a no-op (``caveat_added == False``, ``new_text ==
    answer_text``) when ``answer_text`` is blank, ``retrieved`` is empty (that case -- no sources at
    all -- is a different, pre-existing guard's job), ``question`` yields no anchors at all, or any
    retrieved source does mention some primary anchor."""
    if not answer_text or not answer_text.strip() or not retrieved:
        return answer_text, False
    anchors = extract_anchors(question)
    strong_anchors = _caveat_strong_anchors(anchors)
    primary_anchors = _caveat_primary_anchors(question, strong_anchors) if strong_anchors else anchors
    if not primary_anchors:
        return answer_text, False
    sources_cf = " ".join(
        f"{row.get('title') or ''} {row.get('summary_he') or ''} {row.get('clean_text') or ''}"
        for row in retrieved
    ).casefold()
    if any(a.casefold() in sources_cf for a in primary_anchors):
        return answer_text, False
    caveat = _RETRIEVAL_RELEVANCE_CAVEAT.format(anchor=primary_anchors[0])
    return caveat + "\n\n" + answer_text.lstrip(), True
