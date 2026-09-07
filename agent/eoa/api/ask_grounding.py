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

Round 6 (docs/qa/loop/round_5_judge.md D5, worst-list items 2/3 -- Q1/XM30, Q2/Iron Beam): the
round-5 judge's live sample found the guards above still miss a *claim-vs-source* fabrication built
entirely out of independently-real tokens (a real entity, a real figure, each genuinely present
*somewhere* in the corpus, combined into an invented relationship/capability that no single cited
source actually supports), plus three smaller, independent hygiene gaps:

7. :func:`filter_claim_grounding` -- a per-bullet check, scoped to the "עובדות מרכזיות" section and
   the leading direct-answer paragraph only (the two places `ask_answer_format.md` requires a claim
   to be grounded in a specific source at all): every distinctive token in a `[n]`-cited unit (Latin
   words/acronyms >= 3 chars, hyphen-split; digit runs >= 2 digits; Hebrew technical terms drawn
   from `config/taxonomy.yaml`'s own subdomain labels) is checked against *that unit's own cited
   source(s)* specifically (not the whole corpus, and without the common-acronym/taxonomy
   allowlists :func:`_single_token_grounded` uses -- those allowlists are exactly why a real-but-
   generic acronym like "HEL" waved through the live Q1 fabrication even though item 257's own text
   never mentions it). A unit is dropped only when *none* of its distinctive tokens is grounded this
   way (nor present in the question) -- a deliberately weak, precision-first bar (one real,
   correctly-cited token in an otherwise-fabricated bullet still saves it), the same trade-off every
   guard in this module makes: measure live false positives before tightening further.
8. :func:`strip_template_phrases` -- reuses (imports, never copies) the banned-phrase lists this
   project already maintains for report prose -- `eoa.report.qa_citations`'s
   ``SO_WHAT_TEMPLATE_PHRASES_HE`` and `eoa.report.style`'s ``BANNED_FILLER_PHRASES_HE`` -- live
   found leaking into a chat answer too (round-5 judge's D2/Q8 finding): a sentence that is *only*
   a banned template phrase is dropped outright; one with other content keeps that content with
   just the phrase clause deleted.
9. :func:`sanitize_citation_markers` (extended in place) -- a genuine `[n]` citation can still come
   out with a stray extra bracket glued onto it (`[9]]`, `[[6]`, `[9]]]`, live-found round-5 Q5) --
   collapsed to the bare `[n]` form the web UI's citation renderer actually recognises.
10. :func:`ensure_headings_on_own_line` -- a final, no-op-safe normalisation pass (live-found on an
    iPhone Safari e2e run) that inserts a line break before any ``###``-style heading marker a prior
    guard's removal left glued onto the end of the preceding line.

Round 7 (docs/qa/loop/round_6_judge.md D5, score 45): the round-6 judge's live sample found the
fabrication class round 6 was supposed to close recurring in a new shape, plus a distinct
weak-citation-confidence failure:

11. :func:`filter_claim_grounding` (tightened in place, same public entry point and
    ``(answer_text, question, retrieved)`` contract): round 6's own bar -- keep a `[n]`-cited unit
    if *any one* of its distinctive tokens is grounded in its own citation -- is exactly why the
    live Q1/XM30 fabrication ("MWIR, SWIR ו-VIS" attributed to item 257, whose text never mentions
    any EO/IR technology) sailed through when the same unit also happened to name the genuinely
    grounded "XM30" itself. Tightened to: a unit is now kept only when *either* (a) every
    *technical* token it contains -- a Latin acronym/term of 3-6 uppercase letters, any Latin token
    containing a digit (XM30, 640x512, 12µm), a digit-run (year/count), or a taxonomy subdomain
    Hebrew term -- is grounded in its own citation, *or* (b) at least half of *all* its distinctive
    tokens (technical or not) are grounded there. A unit with zero technical tokens falls back to
    (b) alone (a vacuous "all technical grounded" would otherwise trivially keep a unit whose sole
    ungrounded token is a fabricated proper noun, e.g. round 6's own "Zorblatt" test case -- (a) is
    only ever a keeping condition when there is at least one technical token to actually check).
    This closes the live MWIR/SWIR/VIS case (zero grounded technical tokens, 0% overall) while
    deliberately *not* regressing round 6's own "one grounded token saves the bullet" test (XM30
    grounded + HEL ungrounded is exactly 50%, still clears bar (b)) -- measured against every
    quoted live answer in `docs/qa/loop/round_5_chat_fixes.md` and `docs/qa/loop/round_6_judge.md`
    before picking the 50% threshold, per this round's own brief. Additionally now also checks a
    unit with *no* citation at all inside the lead paragraph/key-facts scope: if it contains a
    technical token grounded nowhere in the whole retrieved corpus (not just a citation, since
    there is none to check against), it is removed too -- round 6 only ever looked at cited units.
12. :func:`filter_uncited_factual_claims` (new) -- the live Q5/Skyranger finding: confident
    factual claims in the direct-answer paragraph/key-facts section carrying almost no `[n]`,
    correctly grounded when they do cite, but a distracting minority left uncited alongside cited
    siblings in the same section. When a scope (lead paragraph, or the key-facts section) already
    has at least one cited unit, every *factual* (`eoa.report.qa_citations.is_factual`) uncited
    unit in that same scope is dropped -- a scope with *zero* citations at all is left alone here
    (that is `retrieval_relevance_caveat`'s/the anchor guard's job, not this one's).
13. :func:`relocate_source_admission_caveat` (new) -- the model's own "N of M sources unrelated"
    admission (Q5 live: "6 of 8 sources have no direct connection") was buried in a trailing
    footer, after the confident claims it should have qualified. Detects such an admission
    sentence anywhere in the answer (Hebrew "אינם קשורים"/"לא רלוונטיים" or English "unrelated")
    and moves it to the very top as the answer's leading caveat instead.
14. :func:`low_citation_caveat` (new) -- when, after the two guards above have run, fewer than 2
    factual sentences in the *whole* answer are actually `[n]`-cited, prepends a generic Hebrew
    caveat ("mostly-uncited-or-indirect sources") once -- a no-op when
    :func:`relocate_source_admission_caveat` already supplied a more specific, model-authored
    caveat for the exact same underlying condition.
15. :func:`entailment_filter` (new, config-gated via ``ask.entailment_check``/
    ``ask.entailment_max_claims``, **chat only** -- never wired into the pipeline/report paths):
    an optional, additive probabilistic check on top of guards 1-14's deterministic floor -- asks
    the ``light`` role one structured yes/no/partial question per up to
    ``ask.entailment_max_claims`` `[n]`-cited lead/key-facts unit, against that unit's own cited
    source text (trimmed to 1500 chars each), and drops a unit answered "no". A hard 20s wall-clock
    budget (a background thread, never the caller's own event loop) skips gracefully -- returning
    the answer unchanged -- on a timeout or any LLM error, since this check is explicitly additive,
    never a substitute for the deterministic guards above.

Round 8 (package R8-chat-b, docs/qa/loop/round_7_judge_b.md D5 findings #1-2, score 80): a leaked
internal delimiter reaching the user-facing answer, and a numeric slip in a cited count.

16. :func:`_normalize_spelled_numbers` (new) + the count-mismatch half of this round's fix (wired
    into ``eoa.api.routes.ask`` as ``filter_claim_count_mismatch`` -- see that module for the
    ``[n]``-scoped guard itself): live finding #1, item 257's own text says the company plans to
    deliver "**seven** additional prototypes", the chat answer said "eight" -- a single digit
    changed from a real, cited fact, invisible to every existing guard above because both numbers
    are independently plausible small integers, not an invented entity/money-figure/year. This
    function normalises every spelled-out cardinal number word (English "one".."twenty", Hebrew
    "אחד".."עשרים", including the separate masculine/feminine forms and two-word teens) to its
    digit form wherever it appears in a *source*'s text, so a claimed digit count can be compared
    against a source that spells the same number out in words (exactly item 257's own shape: "seven"
    in English prose, or a Hebrew source spelling "שבעה"/"שמונה" the same way) -- without this, the
    two sides of the comparison are never in the same form to begin with.
17. ``ask.entailment_check`` live-verified (docs/qa/loop/round_8_fixes.md "### R8-chat-b status"):
    all 5 of 5 sampled live entailment attempts on 2026-09-07 logged
    ``ask.entailment_check_skipped reason=timeout_or_error`` -- zero successes, zero removals ever
    observed. Root cause found by reading :func:`entailment_filter`'s own ``_call`` closure: its
    ``chat_structured("light", ...)`` call never passed ``interactive=True``, so every attempt
    queued behind the resource gate's *batch* budget (``queue_timeout_min``, minutes) while this
    function's own outer :func:`_run_with_timeout` wall-clock (20s) was always going to expire
    first, regardless of how fast the light model itself would have answered once admitted -- the
    same "batch queue vs. interactive budget" gap ``routes.ask``'s own P1 fix (2026-09-06) already
    documented and fixed for the main chat generation, just never applied to this later addition.
    Fixed in place: ``_call`` now passes ``interactive=True``; separately, per this round's own
    brief, the outer wall-clock default is raised 20s -> 30s (extra headroom once actually admitted
    under the interactive budget) and ``config/config.yaml``'s ``ask.entailment_max_claims`` is
    lowered 6 -> 4 (fewer claims batched into one call, so each admitted call finishes faster).
"""

from __future__ import annotations

import concurrent.futures
import re
from functools import lru_cache
from typing import Any, Literal

import structlog
from pydantic import BaseModel

from eoa.config import settings
from eoa.pipeline import entity_normalize
from eoa.report.qa_citations import is_factual, strip_so_what_phrases
from eoa.report.style import strip_filler_phrases
from eoa.search.deep_search import extract_anchors

log = structlog.get_logger(__name__)

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
_HEBREW_LETTER_RE = re.compile(f"[{_HEBREW_LETTERS}]")

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


_LABEL_PAREN_RE = re.compile(r"\s*\(.*$")


@lru_cache(maxsize=1)
def _taxonomy_subdomain_terms() -> frozenset[str]:
    """The Hebrew half (everything before the first ``(...)`` English gloss) of every
    ``config/taxonomy.yaml`` ``domains.*.sub`` label -- e.g. ``'לייזר בעוצמה גבוהה'`` from
    ``hel: "לייזר בעוצמה גבוהה (High-Energy Laser, HEL)"`` -- used by
    :func:`filter_claim_grounding` (round 6, item 1) as one category of "distinctive Hebrew
    technical term" a claim can be grounded by. Deliberately narrower than
    :func:`_taxonomy_vocabulary` above (which walks every string in the whole taxonomy tree,
    domain labels included, and only ever collects ASCII words): this is specifically the
    curated Hebrew subdomain phrase, exactly as the round-6 brief names it, not every Hebrew word
    anywhere in the config."""
    terms: set[str] = set()
    try:
        domains = (settings().taxonomy or {}).get("domains") or {}
    except Exception:
        # Config loading is best-effort here, same rationale as `_taxonomy_vocabulary` above.
        return frozenset()
    if not isinstance(domains, dict):
        return frozenset()
    for domain in domains.values():
        if not isinstance(domain, dict):
            continue
        sub = domain.get("sub")
        if not isinstance(sub, dict):
            continue
        for label in sub.values():
            if not isinstance(label, str):
                continue
            hebrew_part = _LABEL_PAREN_RE.sub("", label).strip()
            if hebrew_part:
                terms.add(hebrew_part)
    return frozenset(terms)


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


# Round 6 item 4 (docs/qa/loop/round_5_judge.md D5, live Q5/Skyranger finding): a *real* citation
# marker can still come out malformed -- one or more stray brackets glued directly onto an
# otherwise-valid `[n]` (`[9]]`, `[[6]`, `[9]]]`). The web UI's own citation renderer
# (`web/src/components/CitationText.tsx`) splits strictly on `/(\[\d+\])/g`, turning only an exact
# `[<digits>]` run into a citation chip -- any stray bracket immediately outside that core is left
# over as a literal, orphaned "[" or "]" character rendered to the user. Collapsing every run of
# extra brackets touching a `[<digits>]` core down to the bare marker is always safe: a real
# citation is never anything but that bare form to begin with, and this never merges two distinct,
# separately-bracketed citations (`[1][2]` has no extra bracket touching either core, so neither
# side matches more than its own single pair).
_MALFORMED_CITATION_RE = re.compile(r"\[+(\d+)\]+")


def _fix_malformed_citations(text: str) -> tuple[str, int]:
    """Collapse every ``_MALFORMED_CITATION_RE`` match in ``text`` down to its bare ``[<digits>]``
    form. Returns ``(new_text, count)`` where ``count`` only tallies a match that actually *had*
    an extra bracket (an already-well-formed ``[7]`` matches the same regex but rewrites to itself
    byte-for-byte, so it must not inflate the count callers use to decide whether anything
    changed -- see the round-3 ``sanitize_citation_markers`` tests this must keep passing
    unchanged)."""
    out: list[str] = []
    last = 0
    count = 0
    for m in _MALFORMED_CITATION_RE.finditer(text):
        original = m.group(0)
        fixed = f"[{m.group(1)}]"
        out.append(text[last : m.start()])
        out.append(fixed)
        last = m.end()
        if fixed != original:
            count += 1
    out.append(text[last:])
    return "".join(out), count


def sanitize_citation_markers(text: str) -> tuple[str, int]:
    """Strip any literal, unsubstituted citation-placeholder token (`[n]`, `[n=5]`, `{n}`, any
    case) from ``text`` -- never a valid citation, which is always a plain `[<digits>]` -- and
    (round 6) collapse a malformed-but-real citation marker (`[9]]`, `[[6]`, `[9]]]`) down to that
    bare form (see :data:`_MALFORMED_CITATION_RE`). Returns ``(cleaned_text, count_removed)``;
    ``count_removed == 0`` (and ``cleaned_text == text``) when nothing matched, so callers can
    cheaply skip re-emitting an unchanged answer."""
    if not text:
        return text, 0
    cleaned, count = _TEMPLATE_LEAK_RE.subn("", text)
    cleaned, malformed_count = _fix_malformed_citations(cleaned)
    count += malformed_count
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

# Round 10 (docs/qa/loop/round_9_judge.md worst #3, D5): live-sampled 2026-09-07, 5 of 8 golden
# answers shipped a truncated/dangling opening sentence -- reproduced offline against
# `_iter_units` directly: a plain "." between two digits (a decimal point -- "1.53", "3.5", "12.7")
# was treated as a full sentence terminator exactly like a real "." ending a sentence, because
# `_SENTENCE_END_RE` matches *every* ".", "!", "?", "gershayim" character with no boundary check at
# all. Money figures with a decimal point are extremely common in this domain's own retrieved
# sources ("$1.53bn"), so a claim like "...מוערך ב-1.53 מיליארד דולר [1]." was silently chopped
# into two bogus half-sentence "units" ("...ב-1." and "53 מיליארד דולר [1].") the moment it passed
# through this function -- and if *either* half then failed a downstream grounding/conflation check
# (its citation marker no longer sits with the number it belongs to, its own text no longer parses
# as a complete claim, ...) and got removed, the *other* half survived on its own as exactly the
# garbled, non-sentence fragment the round-9 judge found live. A short list of common Latin
# abbreviations ("Inc.", "Corp.", "vs.", "e.g.", ...) gets the same treatment for the same reason:
# real-world entity/company names embedded in Hebrew prose ("...לחברת Aerojet Rocketdyne Inc.
# במסגרת...") carry a "." that ends a word, not a sentence.
_ABBREVIATION_WORDS = frozenset(
    {
        "inc",
        "corp",
        "ltd",
        "co",
        "mr",
        "mrs",
        "ms",
        "dr",
        "st",
        "ave",
        "no",
        "vs",
        "gen",
        "rep",
        "sgt",
        "col",
        "capt",
        "prof",
        "jr",
        "sr",
        "etc",
    }
)


def _is_real_sentence_terminator(line: str, pos: int) -> bool:
    """Whether ``line[pos]`` (one of the chars :data:`_SENTENCE_END_RE` matches) actually ends a
    sentence/unit, as opposed to being a decimal point inside a number or the closing "." of a
    common Latin abbreviation -- see the round-10 note above for the live repro this closes.
    "!"/"?"/gershayim are always real terminators (never ambiguous the way "." is); only "." is
    checked further."""
    ch = line[pos]
    if ch != ".":
        return True
    if pos > 0 and pos + 1 < len(line) and line[pos - 1].isdigit() and line[pos + 1].isdigit():
        return False  # decimal point, e.g. the "." in "1.53" or "12.7"
    # Round 12 (docs/qa/loop/round_11_judge.md worst #1, live Q6/AUSA 2026's "סי." fragment,
    # logged unaddressed since round 7): a dotted, transliterated Hebrew acronym or place name
    # ("די.סי." for "D.C.", "יו.אס.סי." for "U.S.C.", "אי.אר." for "A.R.") glues each 1-3-letter
    # Hebrew segment straight onto the next with zero whitespace anywhere in the run -- e.g.
    # "בוושינגטון, די.סי." has no space between "די." and "סי.". A "." sitting directly between
    # two Hebrew letters (no space on either side, exactly like the digit-digit decimal case just
    # above) is therefore never a real sentence boundary. This check runs independently at every
    # "." in the run (each match is its own call from `_iter_units`'s loop), so a 3+-segment chain
    # ("יו.אס.סי.") is handled the same way without any special-casing of chain length -- only the
    # run's own final "." (followed by real whitespace, not another Hebrew letter) is left as the
    # real terminator, so "בוושינגטון, די.סי." stays one glued token and the sentence around it
    # stays intact end-to-end instead of shedding a bare "סי." fragment.
    if (
        pos > 0
        and pos + 1 < len(line)
        and _HEBREW_LETTER_RE.match(line[pos - 1])
        and _HEBREW_LETTER_RE.match(line[pos + 1])
    ):
        return False
    j = pos
    while j > 0 and line[j - 1].isascii() and line[j - 1].isalpha():
        j -= 1
    word = line[j:pos].lower()
    return not (word and word in _ABBREVIATION_WORDS)


def _iter_units(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` spans over ``text`` granular enough to drop individually without mangling
    the rest of the answer: a heading line is never a unit (structural, always kept as-is); a
    markdown bullet line (``-``/``*``) is one whole unit (dropping it removes the bullet marker
    too, leaving no orphan ``- ``); a numbered-list item (``1. ...``/``1) ...``) is one whole unit
    together with its own continuation lines -- any following line that is itself neither blank,
    a heading, another list item, nor a bullet -- so a wrapped numbered item is removed as a whole
    entry too, not just its first physical line; any other line is split into sentences on
    ``.!?``/gershayim -- except a "." that is a decimal point (digit on both sides) or the closing
    "." of a common Latin abbreviation ("Inc.", "vs.", ...), neither of which is a real unit
    boundary (round 10, see :func:`_is_real_sentence_terminator`). Callers that remove units from a
    numbered list should follow up with :func:`_renumber_lists` so the surviving items stay
    sequential."""
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
            if not _is_real_sentence_terminator(line, m.start()):
                continue  # decimal point / abbreviation "." -- not a real unit boundary
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


def _grouped_digit_pattern(digits: str) -> str:
    """A regex fragment matching ``digits`` either bare or thousands-grouped (comma or period
    inserted every 3 digits from the right, e.g. "1,000"/"1.000" for ``digits == "1000"``) -- see
    :func:`_digits_grounded`'s own round-13 note for the live gap this closes. A no-op (returns
    ``digits`` re-escaped, unchanged) for a 1-3 digit run, which has no thousands-grouping position
    to begin with."""
    groups: list[str] = []
    i = len(digits)
    while i > 0:
        groups.append(digits[max(0, i - 3) : i])
        i -= 3
    groups.reverse()
    return r"[,.]?".join(re.escape(g) for g in groups)


def _digits_grounded(candidate: str, corpus: str) -> bool:
    """Whether the digit run inside ``candidate`` (a money figure or a year) appears, as a
    standalone number (not a substring of a longer one), anywhere in ``corpus``.

    Round 9 (docs/qa/loop/round_8_judge_b.md finding 3): the boundary check used to be just
    ``(?<!\\d)...(?!\\d)`` -- a plain digit-adjacency test. That treats a decimal point as a valid
    boundary, so a claimed "53" matched *inside* a corpus "$1.53bn": the digit run "53" really is
    surrounded by non-digit characters ('.' before, 'b' after), even though it is actually the
    fractional part of an entirely different, much smaller number (a live-found ~34x fabrication:
    "53 billion" cited against a source whose real figure was "$1.53bn"). Extended to also reject
    a match immediately preceded by "<digit>." or followed by ".<digit>" -- i.e. the digit run is
    itself a fragment of a longer decimal number in the corpus -- so a number is only ever grounded
    by a genuine standalone occurrence of the same digits, never a substring of a larger or
    differently-scaled figure like 1.53 or 2534.

    Round 13 (docs/qa/loop/round_12_judge.md worst #3, investigated for live Q5/Skyranger's "1,000
    rounds per minute" -- ultimately traced to a different bug, :func:`_count_mismatch_violation`'s
    own round-13 fix, since a bare rate-of-fire figure with no currency symbol or scale word never
    reaches this function via :data:`_MONEY_RE` in the first place; documented here as a genuine,
    independently-verified latent gap this investigation surfaced along the way, not the Q5 root
    cause itself): ``candidate``'s own digits are stripped of every non-digit character before the
    comparison (`"1,000"` -> `"1000"`), but ``corpus`` was never normalised the same way -- so a
    candidate grounded by a corpus occurrence that itself uses a thousands separator ("$1,000",
    "€1.000") never matched, because the literal substring search for the separator-stripped digit
    run can never find a comma or period sitting in the middle of it. :func:`_grouped_digit_pattern`
    now builds a pattern that accepts an *optional* comma or period at every thousands-grouping
    position (from the right, standard convention for both comma- and period-grouped numbers) --
    from ``"1000"`` for example, matches ``"1000"``, ``"1,000"``, or ``"1.000"`` in ``corpus`` alike.
    A no-op for any candidate under 1000 (<= 3 digits, i.e. no possible grouping position), so the
    round-9 decimal-boundary fix's own behaviour for short numbers is completely unchanged."""
    digits = re.sub(r"[^\d]", "", candidate)
    if len(digits) < 2:
        return True  # too short a number to carry any grounding signal on its own
    pattern = r"(?<!\d)(?<!\d\.)" + _grouped_digit_pattern(digits) + r"(?!\d)(?!\.\d)"
    return re.search(pattern, corpus) is not None


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
    grounded in ``corpus``. A single-digit magnitude is checked as a magnitude-with-unit via
    :func:`_money_magnitude_grounded` instead of auto-passing on :func:`_digits_grounded`'s own
    ``< 2`` digit floor. ``figure`` carrying no recognisable scale word (a bare "$5"/"$1,234" with
    nothing else) has no magnitude to compare, so this falls back to the literal digit-substring
    check (:func:`_digits_grounded`) rather than guessing.

    Round 9 (docs/qa/loop/round_8_judge_b.md finding 3): previously only a *single-digit*
    magnitude took the magnitude-aware path above -- a multi-digit one (e.g. "53" in "53 מיליארד
    דולר") fell straight through to the literal digit-substring check instead, which a differently
    -scaled figure sharing the same digits can satisfy even after :func:`_digits_grounded`'s own
    decimal-boundary fix, since "53" can genuinely be a standalone number somewhere unrelated in a
    large corpus. Any figure that carries a recognisable scale word (billion/million/thousand, any
    spelling/suffix :func:`_parse_money_value_scale` understands) now always compares as a
    magnitude, unit-aware, regardless of digit count -- closing the live-found "53 מיליארד דולר"
    vs. a real "$1.53bn" cited source (~34x off, no hedge) that a bare digit-count check never
    should have let through in the first place."""
    digits = re.sub(r"[^\d]", "", figure)
    if not digits:
        return _digits_grounded(figure, corpus)
    parsed = _parse_money_value_scale(figure)
    if parsed is not None and parsed[1] > 1.0:
        return _money_magnitude_grounded(figure, corpus)
    return _digits_grounded(figure, corpus)


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


# ---------------------------------------------------------------------------------------------
# Round 6 item 1 (docs/qa/loop/round_5_judge.md D5, worst-list item 3, live Q1/XM30): a per-bullet
# *claim* grounding check for the "עובדות מרכזיות" section and the leading direct-answer paragraph
# -- the two places `ask_answer_format.md` requires a factual claim to trace to a specific cited
# source. Deliberately checked against *that unit's own* `[n]` citation(s), not the whole corpus
# (unlike every check above) and without the common-acronym/taxonomy allowlists
# `_single_token_grounded` uses -- both of those are exactly why the live fabrication ("HEL laser
# missile-interception system", "ATR/GPS-denied navigation", attributed to item 257, whose actual
# text is only about vehicle deliveries/program value/supplier roster) sailed through every
# existing guard: "HEL" is a real, common defense acronym (on `_COMMON_DEFENSE_ACRONYMS`) and
# "ATR"/"GPS" are each independently real *somewhere* in this retrieval's wider corpus, so the
# corpus-wide/allowlisted checks above all pass them -- the fabrication is entirely in which
# *specific source* the claim is attributed to, not in whether the tokens are "real" words.
# ---------------------------------------------------------------------------------------------

_KEY_FACTS_MARKER = "עובדות מרכזיות"

# Latin "word" candidate for a distinctive token -- letters/digits, with internal hyphens kept in
# the raw match (split apart below, per the brief's "hyphen-split") rather than treated as a
# `_PROPER_NOUN_RE`-style segment separator.
_CLAIM_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
_CLAIM_DIGIT_RUN_RE = re.compile(r"\d{2,}")


def _claim_distinctive_tokens(unit_text: str) -> list[tuple[str, str]]:
    """Every ``(kind, token)`` distinctive-token candidate in ``unit_text`` -- ``kind`` is
    ``"latin"`` (a hyphen-split Latin word/acronym, >= 3 chars), ``"digit"`` (a standalone digit
    run, >= 2 digits), or ``"taxonomy"`` (a Hebrew subdomain phrase from
    :func:`_taxonomy_subdomain_terms` present verbatim in ``unit_text``). Order is
    latin-then-digit-then-taxonomy, stable but not otherwise meaningful (every token is checked
    independently, not positionally)."""
    tokens: list[tuple[str, str]] = []
    for m in _CLAIM_LATIN_TOKEN_RE.finditer(unit_text):
        for piece in m.group(0).split("-"):
            if len(piece) >= 3:
                tokens.append(("latin", piece))
    for m in _CLAIM_DIGIT_RUN_RE.finditer(unit_text):
        tokens.append(("digit", m.group(0)))
    for phrase in _taxonomy_subdomain_terms():
        if phrase and phrase in unit_text:
            tokens.append(("taxonomy", phrase))
    return tokens


def _claim_token_grounded(
    kind: str, token: str, cited_text: str, cited_text_cf: str, question: str, question_cf: str
) -> bool:
    """Whether a single distinctive ``(kind, token)`` claim -- see :func:`_claim_distinctive_tokens`
    -- is grounded in ``cited_text`` (the unit's own `[n]`-cited source(s) only) or in ``question``
    itself (the question's own terms are never a fabricated claim). No allowlist/taxonomy-vocabulary
    exemption here on purpose -- see the section note above."""
    if kind == "latin":
        return token.casefold() in cited_text_cf or token.casefold() in question_cf
    if kind == "digit":
        pat = re.compile(r"(?<!\d)" + re.escape(token) + r"(?!\d)")
        return bool(pat.search(cited_text)) or bool(pat.search(question))
    if kind == "taxonomy":
        return token in cited_text or token in question
    return True  # unreachable for the kinds `_claim_distinctive_tokens` ever emits


def _is_technical_claim_token(kind: str, token: str) -> bool:
    """Round 7 (docs/qa/loop/round_6_judge.md D5, live Q1/XM30 recurrence): whether a
    ``(kind, token)`` distinctive-token candidate (see :func:`_claim_distinctive_tokens`) counts as
    the brief's "technical token" -- a Latin acronym/term of 3-6 uppercase letters, any Latin token
    containing a digit (``XM30``, ``640x512``, ``12µm`` -- the digit run inside these is
    matched separately by ``kind == "digit"``), a bare digit run (``kind == "digit"``, e.g. a
    resolution/year/count), or a taxonomy subdomain Hebrew term (``kind == "taxonomy"``). An
    ordinary Latin word/name (``kind == "latin"``, not all-caps, no digit -- "Program", "Textron")
    is not technical: it still counts toward :func:`filter_claim_grounding`'s overall "at least
    half of all distinctive tokens" bar, just not the stricter "every technical token" one."""
    if kind in ("digit", "taxonomy"):
        return True
    if kind == "latin":
        if token.isupper() and 3 <= len(token) <= 6:
            return True
        return any(c.isdigit() for c in token)
    return False


def filter_claim_grounding(
    answer_text: str, question: str, retrieved: list[dict[str, Any]]
) -> tuple[str, int]:
    """Drop every `[n]`-cited unit inside the "עובדות מרכזיות" section or the leading direct-answer
    paragraph that fails the round-7-tightened claim-grounding bar (see the module docstring's
    round-7 item 11 note for the full live-repro rationale): kept only when *either* every
    "technical" token it contains (:func:`_is_technical_claim_token`) is grounded in its own `[n]`
    citation (or the question), *or* at least half of *all* its distinctive tokens
    (:func:`_claim_distinctive_tokens`) are -- round 6's own bar (kept if *any* token grounded at
    all) is exactly what let the live Q1/XM30 "MWIR, SWIR ו-VIS" fabrication through when the same
    unit also happened to name the genuinely grounded "XM30".

    A unit with no citation is normally left alone (nothing to check against) -- *except* now
    (round 7) also flagged when it sits in the same lead-paragraph/key-facts scope and contains a
    technical token grounded nowhere in the whole retrieved corpus (there is no citation to check
    it against specifically, so the whole corpus is the next-best check -- still far narrower than
    letting an uncited technical claim through unexamined). A unit with no distinctive token at all
    is always left alone (nothing to check either way). A no-op on a blank answer or empty
    ``retrieved`` (same rationale as every other guard in this module). The "### הערכת האנליסט"
    section is out of scope by construction (it is never inside the lead paragraph or the key-facts
    section)."""
    if not answer_text or not answer_text.strip() or not retrieved:
        return answer_text, 0
    sources_by_n = _sources_by_n(retrieved)
    question_cf = question.casefold()
    all_sources_text = " ".join(sources_by_n.values())
    all_sources_text_cf = all_sources_text.casefold()

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

    heading_match = _FIRST_SECTION_HEADING_RE.search(answer_text)
    lead_end = heading_match.start() if heading_match else len(answer_text)

    flagged: list[tuple[int, int]] = []
    for start, end in _iter_units(answer_text):
        unit_text = answer_text[start:end]
        if not unit_text.strip():
            continue
        in_scope = start < lead_end or _KEY_FACTS_MARKER in _section_at(start)
        if not in_scope:
            continue
        cited_ns = _cited_ns(unit_text)
        tokens = _claim_distinctive_tokens(unit_text)
        if not tokens:
            continue

        if not cited_ns:
            # Round 7: an uncited claim in-scope is still checked, but only against the whole
            # corpus (no specific citation to hold it to) and only its technical tokens (an
            # uncited ordinary proper noun/word is not this guard's concern).
            technical = [(k, t) for k, t in tokens if _is_technical_claim_token(k, t)]
            if not technical:
                continue
            if any(
                not _claim_token_grounded(k, t, all_sources_text, all_sources_text_cf, question, question_cf)
                for k, t in technical
            ):
                flagged.append((start, end))
            continue

        cited_text = " ".join(sources_by_n.get(n, "") for n in cited_ns)
        cited_text_cf = cited_text.casefold()
        grounded_flags = [
            _claim_token_grounded(kind, token, cited_text, cited_text_cf, question, question_cf)
            for kind, token in tokens
        ]
        technical = [(kind, token) for kind, token in tokens if _is_technical_claim_token(kind, token)]
        all_technical_grounded = bool(technical) and all(
            _claim_token_grounded(kind, token, cited_text, cited_text_cf, question, question_cf)
            for kind, token in technical
        )
        half_or_more_grounded = sum(grounded_flags) * 2 >= len(tokens)
        if all_technical_grounded or half_or_more_grounded:
            continue
        flagged.append((start, end))

    if not flagged:
        return answer_text, 0
    new_text = _renumber_lists(_tidy_whitespace(_remove_spans(answer_text, flagged)))
    return new_text, len(flagged)


# ---------------------------------------------------------------------------------------------
# Round 8 item 2 (docs/qa/loop/round_7_judge_b.md D5 finding #1, live Q1/XM30 recurrence, again):
# item 257's own text says the company plans to deliver "seven" additional prototypes; the chat
# answer said "eight" -- both are plausible small integers naming the *same* noun phrase from the
# *same* citation, so none of `_grounding_violation`'s digit/year/money checks above ever fire (a
# lone 1-digit number is explicitly auto-passed there -- see `_digits_grounded`'s own `< 2` digit
# floor, unaffected by this new, separate guard). This is a plain-count mismatch, not an invented
# entity/money-figure/year, and needs its own narrow check: for the *same* noun phrase, does this
# unit's own `[n]` citation actually say a *different* number?
# ---------------------------------------------------------------------------------------------

_COUNT_NUMBER_WORDS_EN: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}  # fmt: skip

# Hebrew cardinals -- both grammatical genders where they differ, and the two-word teens (11-19)
# listed before the bare units below so the alternation (built longest-first) tries the compound
# first; "עשרים" (20) has no gendered/compound form to worry about.
_COUNT_NUMBER_WORDS_HE: dict[str, int] = {
    "אחד עשר": 11, "אחת עשרה": 11,
    "שנים עשר": 12, "שתים עשרה": 12,
    "שלושה עשר": 13, "שלוש עשרה": 13,
    "ארבעה עשר": 14, "ארבע עשרה": 14,
    "חמישה עשר": 15, "חמש עשרה": 15,
    "שישה עשר": 16, "שש עשרה": 16,
    "שבעה עשר": 17, "שבע עשרה": 17,
    "שמונה עשר": 18, "שמונה עשרה": 18,
    "תשעה עשר": 19, "תשע עשרה": 19,
    "עשרים": 20,
    "עשרה": 10, "עשר": 10,
    "תשעה": 9, "תשע": 9,
    "שמונה": 8,
    "שבעה": 7, "שבע": 7,
    "שישה": 6, "שש": 6,
    "חמישה": 5, "חמש": 5,
    "ארבעה": 4, "ארבע": 4,
    "שלושה": 3, "שלוש": 3,
    "שניים": 2, "שתיים": 2, "שני": 2, "שתי": 2,
    "אחד": 1, "אחת": 1,
}  # fmt: skip

_COUNT_NUMBER_WORD_TO_DIGIT: dict[str, str] = {
    word.casefold(): str(value)
    for word, value in {**_COUNT_NUMBER_WORDS_EN, **_COUNT_NUMBER_WORDS_HE}.items()
}
# Longest-first so a two-word Hebrew teen ("שבעה עשר") is matched whole, not as its own bare-unit
# prefix ("שבעה") followed by a separately-matched, nonsensical leftover "עשר".
_COUNT_NUMBER_WORD_RE = re.compile(
    r"\b("
    + "|".join(re.escape(w) for w in sorted(_COUNT_NUMBER_WORD_TO_DIGIT, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


def _normalize_spelled_numbers(text: str) -> str:
    """Replace every spelled-out cardinal number word in ``text`` -- English "one".."twenty",
    Hebrew "אחד".."עשרים" (both genders, two-word teens) -- with its digit form, so a claimed digit
    count can be compared against a source that spells the same number out in words instead (see
    the section note above for the live "seven" vs. "8" finding this exists for)."""
    if not text:
        return text
    return _COUNT_NUMBER_WORD_RE.sub(lambda m: _COUNT_NUMBER_WORD_TO_DIGIT[m.group(0).casefold()], text)


# A small hedge/filler vocabulary skipped when walking forward from a count digit to find the noun
# phrase it actually quantifies ("up to eight **additional** prototypes" / "שמונה אב-טיפוסים
# **נוספים**" -- the noun sits on the far side of the filler word in either language/word order).
_COUNT_FILLER_WORDS = frozenset(
    {
        "up",
        "to",
        "additional",
        "more",
        "another",
        "some",
        "about",
        "עד",
        "כ",
        "כמעט",
        "נוספים",
        "נוספות",
        "עוד",
    }
)
_COUNT_DIGIT_RE = re.compile(r"(?<![\d.])\b(\d{1,3})\b(?!\d)")
_COUNT_WORD_TOKEN_RE = re.compile(r"[A-Za-zא-ת][A-Za-z0-9א-ת\-']*")


def _count_next_content_word(text: str, pos: int) -> str | None:
    """The first non-filler word token in ``text`` starting at or after ``pos`` (looking at most 6
    tokens ahead) -- the noun phrase a preceding count digit quantifies, skipping past a small
    hedge/filler word in between (see :data:`_COUNT_FILLER_WORDS`)."""
    for tok in list(_COUNT_WORD_TOKEN_RE.finditer(text, pos))[:6]:
        if tok.group(0).casefold() not in _COUNT_FILLER_WORDS:
            return tok.group(0)
    return None


def _count_candidates(text: str) -> list[tuple[int, int, str, str]]:
    """``(digit_start, digit_end, digit_str, noun)`` for every plain count-shaped digit token in
    ``text``: a 1-3 digit run that is not a citation marker (``[7]``), not part of a money figure
    (:data:`_MONEY_RE` -- that's `_money_conflation_violation`'s job, not this guard's), paired
    with the first substantive word found within a few tokens after it. A 4+-digit run (a year or a
    larger figure) is never a candidate here -- this guard is specifically about a plain count."""
    money_spans = [m.span() for m in _MONEY_RE.finditer(text)]
    candidates: list[tuple[int, int, str, str]] = []
    for m in _COUNT_DIGIT_RE.finditer(text):
        start, end = m.span()
        if start > 0 and text[start - 1] == "[":
            continue  # a citation marker digit, e.g. `[7]`
        if any(ms <= start < me for ms, me in money_spans):
            continue  # a money figure -- a different guard's job
        noun = _count_next_content_word(text, end)
        if noun:
            candidates.append((start, end, m.group(1), noun))
    return candidates


def _count_noun_match_kind(claim_noun: str, source_noun: str) -> str | None:
    """``"exact"``, ``"fuzzy"``, or ``None`` for how well ``claim_noun`` and ``source_noun`` (each
    a single word token from :func:`_count_candidates`) name the same thing: exact on an identical
    casefolded word (Hebrew final letters normalised, so a construct/plain-form pair like "אב-טיפוס"
    still matches "אב-טיפוסים" only if they share the same *prefix* relationship -- final-letter
    normalisation alone does not bridge a genuine plural, that is the fuzzy case below); fuzzy when
    one is a >= 4-char prefix of the other (a plural/construct-state variant of the same noun,
    e.g. "prototype" vs. "prototypes") but the two are not identical."""
    a, b = claim_noun.casefold(), source_noun.casefold()
    a_fin, b_fin = _normalize_hebrew_finals(a), _normalize_hebrew_finals(b)
    if a == b or a_fin == b_fin:
        return "exact"
    if len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a)):
        return "fuzzy"
    if len(a_fin) >= 4 and len(b_fin) >= 4 and (a_fin.startswith(b_fin) or b_fin.startswith(a_fin)):
        return "fuzzy"
    return None


def _count_mismatch_violation(
    unit_text: str, cited_ns: list[int], sources_by_n: dict[int, str]
) -> tuple[int, int, str, bool] | None:
    """``(digit_start, digit_end, corrected_digit, exact)`` -- relative to ``unit_text`` -- for the
    first claimed count in ``unit_text`` that names the same noun phrase as a *different* count
    found in its own ``[n]`` citation(s) (spelled-out source numbers normalised via
    :func:`_normalize_spelled_numbers` first, so "seven" in the source is compared against a
    claimed "8" on equal footing). ``exact`` is true only when the claim's and the citation's noun
    words match exactly (:func:`_count_noun_match_kind`); a fuzzy-only match (e.g. a plural/
    construct-state variant) still counts as a mismatch but is reported as inexact, per this
    section's own brief: prefer dropping the claim outright when the noun match is not exact,
    correct the digit in place only when it is. Returns ``None`` -- deliberately left alone, same
    precision-first stance as every other guard in this module -- when ``unit_text`` carries no
    citation, no count candidate at all, or its own citation(s) mention no comparable count for the
    same noun phrase (unverifiable is not the same as contradicted).

    Round 13 (docs/qa/loop/round_12_judge.md worst #3, live Q5/Skyranger): a cited source that
    discusses *more than one* system under the same generic noun ("rounds", "prototypes", ...) can
    carry several same-noun counts at different values -- item 1353's own article states both
    "Centurion, at a rate of fire of 4,500 rounds per minute" (an unrelated system, appearing
    earlier in the document) and "[Skyranger] ... a firing rate of 1,000 rounds per minute" (the
    actually-cited figure). A comma-grouped number is itself split into two separate count
    candidates by :func:`_count_candidates` (the "1" and the "000" of "1,000" are two different
    1-3-digit runs), so a correctly-quoted "1,000 rounds per minute [n]" claim has *both* a "1" and
    a "000" candidate, each independently compared against the source. The previous version of this
    function took the *first* same-noun source candidate in document order as ``exact_hit`` and
    declared a mismatch whenever it merely differed from the claim -- so the claim's "1" was
    compared only against the *earlier*, unrelated "4,500" mention (source noun "rounds" matches
    exactly), never checked against the *correct*, later "1,000" mention naming the same noun, and
    was "corrected" to "4", corrupting a verbatim-correct "1,000 rounds per minute" into a
    fabricated "4,000 rounds per minute" -- silently, with no guard afterwards able to tell the
    difference (the corrected figure carries a real citation and passes every other check). Fixed by
    checking *every* same-noun source candidate before deciding: if the claim's own digit already
    equals *any* of them, the claim is grounded and this candidate is not a mismatch at all,
    regardless of what any other same-noun occurrence elsewhere in a multi-topic source says. Only
    when the claim's digit matches *none* of the same-noun source candidates is it flagged, and the
    correction offered is still the first (document-order) same-noun value, unchanged from before --
    this only ever *narrows* when a mismatch fires, it never widens it, so every pre-existing
    single-value-per-noun repro (the "seven"/"8" prototypes case, docs/qa/loop/round_7_judge_b.md)
    is unaffected."""
    if not cited_ns:
        return None
    claim_candidates = _count_candidates(unit_text)
    if not claim_candidates:
        return None
    cited_text_norm = _normalize_spelled_numbers(" ".join(sources_by_n.get(n, "") for n in cited_ns))
    source_candidates = _count_candidates(cited_text_norm)
    if not source_candidates:
        return None

    fallback: tuple[int, int, str, bool] | None = None
    for c_start, c_end, c_digit, c_noun in claim_candidates:
        exact_hits: list[str] = []
        fuzzy_hits: list[str] = []
        for _s_start, _s_end, s_digit, s_noun in source_candidates:
            kind = _count_noun_match_kind(c_noun, s_noun)
            if kind == "exact":
                exact_hits.append(s_digit)
            elif kind == "fuzzy":
                fuzzy_hits.append(s_digit)
        if exact_hits:
            if c_digit in exact_hits:
                continue  # grounded by at least one same-noun occurrence -- not a mismatch
            return c_start, c_end, exact_hits[0], True
        if fallback is None and fuzzy_hits and c_digit not in fuzzy_hits:
            fallback = (c_start, c_end, fuzzy_hits[0], False)
    return fallback


def filter_claim_count_mismatch(answer_text: str, retrieved: list[dict[str, Any]]) -> tuple[str, int]:
    """For every ``[n]``-cited unit of ``answer_text``, when :func:`_count_mismatch_violation`
    finds a claimed count that disagrees with its own citation's count for the same noun phrase:
    correct just the digit in place (keeping the rest of the sentence) when the noun match was
    exact, or drop the whole unit when it was only a fuzzy match -- see
    :func:`_count_mismatch_violation`'s own docstring for the full rationale (live "seven" vs. "8"
    finding, docs/qa/loop/round_7_judge_b.md D5 finding #1). Returns ``(new_text, count)`` where
    ``count`` tallies both corrected and dropped units; a no-op (``count == 0``) on a blank answer,
    empty ``retrieved``, or when nothing is flagged."""
    if not answer_text or not answer_text.strip() or not retrieved:
        return answer_text, 0
    sources_by_n = _sources_by_n(retrieved)
    replacements: list[tuple[int, int, str]] = []
    count = 0
    for start, end in _iter_units(answer_text):
        unit_text = answer_text[start:end]
        if not unit_text.strip():
            continue
        violation = _count_mismatch_violation(unit_text, _cited_ns(unit_text), sources_by_n)
        if violation is None:
            continue
        rel_start, rel_end, corrected, exact = violation
        count += 1
        if exact:
            replacements.append((start + rel_start, start + rel_end, corrected))
        else:
            replacements.append((start, end, ""))
    if not replacements:
        return answer_text, 0
    new_text = _renumber_lists(_tidy_whitespace(_replace_spans(answer_text, replacements)))
    return new_text, count


# ---------------------------------------------------------------------------------------------
# Round 6 item 3 (docs/qa/loop/round_5_judge.md D2/D5, live Q8/SPECTRO finding): the so_what
# template-phrase crutch this project already bans from report prose (`eoa.report.qa_citations`'s
# `SO_WHAT_TEMPLATE_PHRASES_HE`) and the generic analyst-filler phrases banned from report prose
# (`eoa.report.style`'s `BANNED_FILLER_PHRASES_HE`) leak into chat answers too -- both lists are
# imported and reused here (never copied) so a future addition to either list closes the chat gap
# automatically, with no separate edit needed.
# ---------------------------------------------------------------------------------------------

_HAS_LETTER_RE = re.compile(r"[A-Za-zא-ת]")


def strip_template_phrases(answer_text: str) -> tuple[str, int]:
    """Remove every banned so_what/filler template phrase (see the section note above) from
    ``answer_text``, unit by unit: a unit that is *only* such a phrase (nothing but the phrase
    itself, plus a bullet/number marker and punctuation, survives its removal) is dropped outright;
    a unit with other content keeps that content, just without the banned clause. Returns
    ``(new_text, count)`` where ``count`` is the total number of banned phrases removed across the
    whole answer (whether or not their containing unit was itself dropped); ``count == 0`` (and
    ``new_text == answer_text``) when nothing matched."""
    if not answer_text or not answer_text.strip():
        return answer_text, 0

    flagged: list[tuple[int, int]] = []
    replacements: list[tuple[int, int, str]] = []
    count = 0
    for start, end in _iter_units(answer_text):
        unit_text = answer_text[start:end]
        if not unit_text.strip():
            continue
        cleaned, so_what_removed = strip_so_what_phrases(unit_text)
        cleaned, filler_removed = strip_filler_phrases(cleaned)
        removed_here = len(so_what_removed) + len(filler_removed)
        if not removed_here:
            continue
        count += removed_here
        if _HAS_LETTER_RE.search(cleaned):
            # Both reused strippers trim their own *leading* junk/whitespace, on the assumption
            # they're handed a standalone sentence -- here `unit_text` can instead be a
            # mid-paragraph unit whose own leading space is what separates it from the *previous*
            # unit (`_iter_units` includes it). Re-attach that leading space if the strippers ate
            # it, or this unit's surviving text would glue directly onto the previous unit's own
            # trailing punctuation with no space at all.
            leading_ws = unit_text[: len(unit_text) - len(unit_text.lstrip())]
            # Round 12 (docs/qa/loop/round_11_judge.md worst #6, live Q7's "stray leading space
            # before an otherwise complete sentence"): the re-attach above is only correct when
            # `unit_text` truly follows *inline* content on the same physical line -- that is the
            # only case where its own leading whitespace is a real separator from the previous
            # unit. When this unit is instead the first thing on its own line (right after a
            # heading or a blank line -- `answer_text[start - 1]` is a newline, or `start == 0`),
            # any leading whitespace it carried was never a separator; it was incidental raw-model
            # formatting noise the strippers above already discarded correctly, and blindly
            # re-adding it here is exactly what produced the live bug -- a rendered line/paragraph
            # starting with a bare stray space.
            follows_inline_content = start > 0 and answer_text[start - 1] != "\n"
            if leading_ws and follows_inline_content and not cleaned[:1].isspace():
                cleaned = leading_ws + cleaned
            replacements.append((start, end, cleaned))
        else:
            # nothing but punctuation/bullet/number markers survived -- the unit *was* the phrase.
            flagged.append((start, end))

    if not count:
        return answer_text, 0
    new_text = _replace_spans(answer_text, [*replacements, *((s, e, "") for s, e in flagged)])
    new_text = _renumber_lists(_tidy_whitespace(new_text))
    return new_text, count


# ---------------------------------------------------------------------------------------------
# Round 6 item 5 (live-found on an iPhone Safari e2e run): a prior guard's unit removal can leave a
# `###`-style section heading glued onto the tail of the preceding line (the newline that used to
# separate them belonged to the removed unit). A cheap, final, order-independent normalisation
# pass -- never removes or rewrites any actual content, just re-inserts the line break -- meant to
# run last, after every other guard above.
# ---------------------------------------------------------------------------------------------

_GLUED_HEADING_RE = re.compile(r"#{2,6}[ \t]")


def ensure_headings_on_own_line(answer_text: str) -> str:
    """Insert a line break before any ``###``-style heading marker (2-6 ``#`` chars, matching this
    project's own heading convention -- see ``ask_answer_format.md``) that is not already the first
    thing on its line, splitting the glued-on prefix onto its own preceding line. A no-op line (no
    heading marker, or one already at column 0) is returned unchanged; a no-``#`` ``answer_text`` is
    a fast no-op without even splitting into lines."""
    if not answer_text or "#" not in answer_text:
        return answer_text
    out_lines: list[str] = []
    for line in answer_text.split("\n"):
        m = _GLUED_HEADING_RE.search(line)
        if m and m.start() > 0:
            prefix = line[: m.start()].rstrip()
            if prefix:
                out_lines.append(prefix)
            out_lines.append(line[m.start() :])
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------------------------
# Round 12 (docs/qa/loop/round_11_judge.md worst #6, live Q7's "stray leading space before an
# otherwise complete sentence"): the trace (see `strip_template_phrases`'s own round-12 fix above)
# found and closed one concrete producer -- a phrase-stripper's leading-whitespace re-attachment
# firing on a section's very first unit, where that whitespace was never a real separator. But the
# module has several other unit-join/caveat-relocation/sanitizer call sites (see the module note
# above `strip_template_phrases`, and the various `.lstrip()` call sites elsewhere in this module)
# that each independently assemble text by concatenation, any one of which could produce the same
# cosmetic shape in a way not yet found live. Belt-and-suspenders, content-blind, and
# order-independent by design (never removes/rewrites real content, only trims whitespace at each
# line's own two ends) -- meant to run last, after every other guard, exactly like
# `ensure_headings_on_own_line` above. Skips code-fence content entirely (an odd leading/trailing
# space inside a fenced block could be meaningful, e.g. inside a quoted snippet) and never touches
# a line's *internal* spacing (table-cell padding, multi-word prose) -- only ever the two ends of
# each line, which is exactly and only where the live artifact appeared.
# ---------------------------------------------------------------------------------------------

_CODE_FENCE_LINE_RE = re.compile(r"^\s*```")


def strip_stray_line_edges(answer_text: str) -> str:
    """Strip leading/trailing spaces and tabs from every line of ``answer_text``, except lines
    inside a fenced code block (```` ``` ````-delimited, toggled on the fence marker lines
    themselves, which are also left untouched). Never touches a line's own internal content --
    only its two ends -- so table-row cell padding and ordinary multi-word prose are unaffected. A
    no-op (returns ``answer_text`` unchanged, including identity for the common case) when nothing
    would change."""
    if not answer_text or "\n" not in answer_text:
        return answer_text.strip(" \t") if answer_text else answer_text
    lines = answer_text.split("\n")
    out_lines: list[str] = []
    in_fence = False
    changed = False
    for line in lines:
        if _CODE_FENCE_LINE_RE.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            out_lines.append(line)
            continue
        stripped = line.strip(" \t")
        if stripped != line:
            changed = True
        out_lines.append(stripped)
    if not changed:
        return answer_text
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------------------------
# Round 10 (docs/qa/loop/round_9_judge.md worst #3, D5): the module-wide `_iter_units` decimal/
# abbreviation fix above closes the single most common way a removal guard could leave a truncated/
# dangling fragment in place of a real sentence -- but every guard in this module still removes by
# *unit*, and a unit boundary this module has not thought of yet (a markdown table row, an unusual
# abbreviation, a guard that only redacts part of a unit via `_replace_spans`) could still leave one
# behind. This is a final, order-independent, content-blind safety net -- run once, last, after
# every other guard (including `ensure_headings_on_own_line` above) -- that never tries to diagnose
# *why* a fragment is dangling, only whether the section's own leading unit still reads as a
# complete opening.
# ---------------------------------------------------------------------------------------------

_MIN_LEADING_FRAGMENT_WORDS = 4
_TERMINAL_UNIT_CHARS = ".!?״\"'”’)"  # ".", "!", "?", gershayim, closing quotes/paren

# Round 11 (docs/qa/loop/round_10_judge.md worst #3, live Q6/AUSA 2026): a *structurally complete*,
# correctly-punctuated opening unit can still be referentially dangling -- it opens with a
# cross-reference/continuation token ("שאר המקורות...", "לעומת זאת...") whose antecedent was a
# preceding sentence a guard removed earlier in the same pipeline. Neither existing check above
# catches this: the unit is long enough (>= `_MIN_LEADING_FRAGMENT_WORDS`) and ends on real
# terminal punctuation, so it reads as "complete" by every content-blind measure this module had
# until now -- only the *reference itself*, which points at nothing in a fresh opening, gives it
# away. This is a second, distinct heuristic (still content-blind: it only ever looks at the first
# token of the unit, never tries to verify whether an antecedent genuinely existed) layered onto
# the same leading-unit check `enforce_answer_coherence` already runs per section.
_CROSS_REF_OPENING_TOKENS = (
    "לעומת זאת",
    "כמו כן",
    "עם זאת",
    "שאר",
    "בנוסף",
    "גם",
    "מנגד",
    "לכן",
    "לפיכך",
    "אולם",
    "אך",
    "the other",
    "in addition",
    "however",
)
# Longest-first alternation so a multi-word phrase ("לעומת זאת") is never shadowed by a shorter
# token that happens to be one of its own words' prefix; `\b` on both sides (Python's `re` treats
# Hebrew letters as `\w` under Unicode matching, so this works for the Hebrew tokens too) so e.g.
# "אך" does not match inside an unrelated longer word, and the tokens are only ever tested at the
# very start of the unit (`^`), never mid-sentence.
_CROSS_REF_OPENING_RE = re.compile(
    r"^("
    + "|".join(re.escape(t) for t in sorted(_CROSS_REF_OPENING_TOKENS, key=len, reverse=True))
    + r")\b[,:]?\s*",
    re.IGNORECASE,
)
_MIN_CROSS_REF_REMAINDER_WORDS = 8


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


# ---------------------------------------------------------------------------------------------
# Round 12 (docs/qa/loop/round_11_judge.md worst #1, live Q6/AUSA 2026's "סי." fragment, logged
# unaddressed since round 7): the two leading-unit checks above (and `_is_real_sentence_terminator`
# 's own new dotted-Hebrew-acronym rule, which closes the specific root cause -- "בוושינגטון,
# די.סי." no longer splits at all) both only ever look at a *section's own first unit*. But the
# live "סי." fragment itself sat mid-flow, "between the main paragraph and the gaps section" per
# the round-11 judge -- an orphan left behind once an *earlier*, unrelated guard removed the unit
# in front of it ("...בוושינגטון, די." was dropped elsewhere; "סי." itself, on its own, is a
# syntactically complete-looking unit -- ends on real terminal punctuation, is not the section's
# only remaining unit -- so neither check above ever had a reason to touch it). This is a second,
# independent, content-blind safety net that runs over *every* plain-prose unit in the whole
# answer (never a bullet or numbered-list item -- same exception as above, for the same reason: a
# short-but-complete bullet like "- לא ידוע." is a normal, valid answer shape here): a unit is a
# "meaningless short fragment" -- and is dropped outright, never merged (there's no way to guess
# which neighbour it belonged to once it's already orphaned) -- when it is shorter than
# :data:`_MIN_ORPHAN_FRAGMENT_WORDS` words, ends on a literal "." (never "!"/"?"/gershayim/a closing
# quote -- those read as complete on their own even when short, e.g. a bare "לא!"), and contains no
# word of at least :data:`_MIN_ORPHAN_FRAGMENT_WORD_LEN` Hebrew-or-Latin letters (digits, brackets,
# and punctuation never count towards a word's length) -- i.e. no verb/noun long enough to carry
# any meaning on its own. "סי." (one word, 2 letters) matches this exactly; "לא ידוע." (two words,
# "ידוע" is 4 letters) does not, and is never touched, matching the exception documented above.
# ---------------------------------------------------------------------------------------------

_MIN_ORPHAN_FRAGMENT_WORDS = 3
_MIN_ORPHAN_FRAGMENT_WORD_LEN = 3
_FRAGMENT_LETTER_WORD_RE = re.compile(rf"[A-Za-z{_HEBREW_LETTERS}]+")


def _is_meaningless_short_fragment(unit_text: str) -> bool:
    """Whether ``unit_text`` (already an :func:`_iter_units` span, whitespace included) is a bare,
    meaningless leftover -- see the module note above for the full rationale and the live "סי."
    repro this exists to catch. Never true for a bullet/numbered-list item; callers filter those
    out before calling this (see :func:`_drop_orphan_short_fragments`), matching every other check
    in this module's own convention rather than re-checking it here."""
    stripped = unit_text.strip()
    if not stripped.endswith("."):
        return False
    if _word_count(stripped) >= _MIN_ORPHAN_FRAGMENT_WORDS:
        return False
    letter_words = _FRAGMENT_LETTER_WORD_RE.findall(stripped)
    # No letter-word at all (bare punctuation left over from something else entirely, e.g. a lone
    # "." out of a "(...)"-style ellipsis) is a different, out-of-scope cosmetic artifact -- this
    # check only ever targets a unit that *has* a word, just not a long enough one (the live "סי."
    # shape always has exactly one short letter-word).
    if not letter_words:
        return False
    return not any(len(w) >= _MIN_ORPHAN_FRAGMENT_WORD_LEN for w in letter_words)


def _drop_orphan_short_fragments(text: str) -> tuple[str, int]:
    """Remove every :func:`_is_meaningless_short_fragment` unit anywhere in ``text`` -- not just a
    section's leading unit, see the module note above. Returns ``(new_text, removed_count)``,
    matching every other guard's contract in this module; a no-op (``removed_count == 0``,
    ``new_text is text``... in value, not identity) on a blank ``text`` or when nothing qualifies."""
    if not text or not text.strip():
        return text, 0
    to_replace: list[tuple[int, int, str]] = []
    for s, e in _iter_units(text):
        stripped = text[s:e].strip()
        if not stripped:
            continue
        if stripped.startswith(("-", "*")) or _NUMBERED_ITEM_RE.match(stripped):
            continue
        if _is_meaningless_short_fragment(stripped):
            to_replace.append((s, e, ""))
    if not to_replace:
        return text, 0
    new_text = _renumber_lists(_tidy_whitespace(_replace_spans(text, to_replace)))
    new_text = _drop_empty_headings(new_text)
    return new_text, len(to_replace)


# ---------------------------------------------------------------------------------------------
# Round 13 (docs/qa/loop/round_12_judge.md worst #2, live Q6/AUSA 2026): the round-12 "סי." fix
# closes one *shape* of guard-removal leftover -- a short (<3-word), letter-poor orphan -- but the
# live round-12 answer still opened with `מים, ודירוג הכנסות של חברות ביטחון גלובליות)...`, a
# *longer* leftover that is not short (round 12's own orphan-fragment sweep only ever fires under
# :data:`_MIN_ORPHAN_FRAGMENT_WORDS` == 3 words; this is 7) and is not the module's other leading-
# unit shape either (long enough, ends on real terminal punctuation as far as this module's own
# `_iter_units` can tell). It is nonetheless headless: a bare chain of nouns/conjunctions with no
# predicate at all -- "מים," itself the truncated tail of an earlier removed word ("...דו|מים,"),
# then "ודירוג" (noun, "and-the-ranking-of"), "הכנסות" ("revenues"), "של" ("of"), "חברות ביטחון
# גלובליות" ("global defense companies") -- a parenthetical noun phrase with zero finite verb or
# copula anywhere in it, exactly the *same underlying defect class* as the old "סי." bug (a guard
# upstream removed the sentence that used to introduce this list, leaving the list itself stranded
# as if it were a sentence) in a new, longer shape neither of round 12's two checks was scoped to
# catch.
#
# This is a third, independent leading-unit check -- gated behind its own ``elif`` in
# :func:`enforce_answer_coherence`, same convention as the round-11 cross-reference check -- that
# asks a more general question than either round-12 check: does the section's own leading unit
# still read as one complete, independent clause at all, not just "long enough" or "not a short
# orphan"? Three content-blind, approximate (never true syntactic parsing -- this module has never
# attempted that, see every earlier round's own docstring) signals combine to answer that -- a
# section's own 4-word dangling-fragment floor (:data:`_MIN_LEADING_FRAGMENT_WORDS`, the ``if``
# above this ``elif`` in the same chain) already screens out anything shorter before this check ever
# runs, so it deliberately carries no separate word-count floor of its own (an earlier version did
# -- 6 words -- and wrongly dropped legitimate 5-word answers this project's own round-10/11 test
# suite already asserts must survive, e.g. "התוכנית אושרה השבוע במלואה [1]."):
#
# 1. **Opening character** -- the unit's first *real* character (see :data:`_LEADING_DECORATION_RE`
#    -- a leading blockquote marker or warning glyph this module's own guards prepend is stripped
#    first, never counted against the unit) must itself plausibly *start* a word: a capitalised
#    Latin letter, a Hebrew letter, a digit, or (handled by the caller, same as every other check in
#    this function) a bullet/numbered-list marker. Anything else -- a lowercase Latin letter, a
#    stray punctuation mark left dangling by an upstream removal -- fails outright.
# 2. **Not a bare continuation** -- the unit must not *open* with a conjunction/relative/adversative
#    token whose antecedent lives in a sentence that came before it: a Hebrew word glued with a
#    leading ו-/ש- conjunction prefix, a standalone "כי"/"אשר"/"אך", or an English "and"/"but"/
#    "which". This is deliberately narrower than round 11's own :data:`_CROSS_REF_OPENING_RE` (which
#    also matches "בנוסף", "גם", ...) -- it only ever looks at the unit's own *first word*, not a
#    curated phrase list, so it is a cheap, independent second opinion rather than a duplicate.
# 3. **Contains a finite verb or copula-like structure** -- see :func:`_has_finite_verb_or_copula`'s
#    own docstring for the exact (deliberately narrow, admittedly incomplete -- Hebrew has far more
#    verb morphology than any short heuristic can cover, see that function's own caveat) heuristic.
#    A number or colon anywhere in the unit also satisfies this on its own (a factual, data-bearing
#    opening -- "נכון ל-2026, קיימות 4 מערכות..." -- reads as complete even without a recognisable
#    verb form).
#
# The live "מים, ודירוג הכנסות..." repro fails specifically on (3): none of its words begins with
# one of the future/hifil/piel present prefixes י/ת/נ/מ at all, and its one plural-suffixed word
# ("הכנסות", ending "-ות") is preceded by "ודירוג" -- a bare noun, not a definite-article-marked one
# -- so the construct-chain fallback (a suffixed word immediately preceded by a ה-prefixed word,
# e.g. "המערכות פועלות") does not fire either. The round-
# 11 "שאר המקורות..." shape (docs/qa/loop/round_10_judge.md worst #3) is unaffected by this new
# check -- it is still caught first, and rewritten (not dropped outright), by the existing
# :data:`_CROSS_REF_OPENING_RE` branch in the same ``elif`` chain, which this check never reaches
# for that shape.
#
# Per this round's own brief: a unit failing this check is dropped outright, *unless* the section's
# very next unit is itself continuation-shaped (see point 2 above, reused via
# :func:`_starts_as_continuation`) -- dropping the first unit in that case would only crown the
# second, equally headless unit as the new leading fragment, trading one incoherent opening for
# another. When that happens, both units are left exactly as they were (a deliberate no-op, not a
# merge that rewrites anything) -- the module's precision-first convention throughout: prefer
# leaving a borderline case alone over compounding one guard's uncertain judgement with another's.
# ---------------------------------------------------------------------------------------------

_CLAUSE_NOUN_SUFFIXES = ("ים", "ות", "ה")
_HEBREW_WORD_RUN_RE = re.compile(rf"[{_HEBREW_LETTERS}]+")
_CLAUSE_VERB_PREFIXES = "יתנמ"

# A leading run of characters that are neither a Latin/Hebrew letter nor a digit -- a blockquote
# marker, a warning glyph, stray punctuation -- stripped once from the very start of a unit before
# :func:`_is_incoherent_leading_unit` looks at its first "real" character. See that function's own
# docstring for the two live end-to-end reproductions (the admission-caveat blockquote, the off-
# topic warning prefix) this exists to stop misclassifying as incoherent.
_LEADING_DECORATION_RE = re.compile(rf"^[^A-Za-z0-9{_HEBREW_LETTERS}]+")

# A handful of common Hebrew past-tense reporting verbs that carry none of the four future/hifil/
# piel present prefixes :data:`_CLAUSE_VERB_PREFIXES` covers (Hebrew past tense largely does not
# prefix at all) and are not reliably caught by the suffix-plus-preceding-ה-word fallback either --
# live-verified 2026-09-07 (this section's own end-to-end test suite): "Rheinmetall חתמה חוזה חדש
# [1]" ("Rheinmetall signed a new deal") was wrongly flagged as incoherent, because its verb
# ("חתמה", "signed") starts with ח (no prefix match) and the word immediately before it in the text
# is the Latin "Rheinmetall" -- invisible to :data:`_HEBREW_WORD_RUN_RE`, which only ever scans
# Hebrew letter runs, so the suffix fallback's own "preceded by a ה-word" check never had a Hebrew
# predecessor to look at. Matched via `str.startswith` so one stem also covers its own gender/number
# agreement suffix ("חתם"/"חתמה"/"חתמו" all share the "חת" stem -- kept as full words below rather
# than bare 2-letter stems, which would be too short to avoid coincidental matches elsewhere).
_CLAUSE_PAST_TENSE_VERB_STEMS_HE: tuple[str, ...] = (
    "חתם", "חתמה", "חתמו",
    "פרסם", "פרסמה", "פרסמו",
    "הודיע", "הודיעה", "הודיעו",
    "דיווח", "דיווחה", "דיווחו",
    "הכריז", "הכריזה", "הכריזו",
    "פיתח", "פיתחה", "פיתחו",
    "רכש", "רכשה", "רכשו",
    "השיק", "השיקה", "השיקו",
    "זכה", "זכתה", "זכו",
    "נחתם", "נחתמה", "נחתמו",
    "הוצג", "הוצגה", "הוצגו",
    "כלל", "כללה", "כללו",
)  # fmt: skip

# A bare "starts with one of the future/hifil/piel present prefixes י/ת/נ/מ" rule is the brief's own
# starting heuristic, but this project's own domain vocabulary is full of מ-initial *nouns* built on
# the identical templatic shape as a Hifil/Piel present-tense verb ("מערכת", "מטרה", "מרכזי",
# "מבחינת") -- a bare prefix-letter check would flag nearly any sentence that merely mentions a
# system or a target as "verb-shaped", whether or not it actually has a predicate, defeating this
# whole check's purpose (verified live against an early version of this section's own repro
# fixture, which slipped through specifically because it contained the unrelated noun "מבחינת").
# Rather than a curated *allowlist* of conjugated verb forms (which would have the opposite,
# arguably worse failure mode for a live guard -- silently dropping real content the moment a real
# verb happens not to be on the list, e.g. "מפתחת"/"develops", one of the single most common verbs
# in this exact domain), this keeps the broad prefix rule but carves out a small, curated
# *blocklist* of common domain nouns/adjectives sharing the same prefixes -- an incomplete blocklist
# only ever fails *open* (an unlisted noun is still wrongly treated as verb-shaped, same as the bare
# rule), never *closed* (a real verb is never wrongly excluded just for not being enumerated),
# matching this module's precision-first bias throughout: prefer under-flagging incoherence over
# over-deleting real content.
_CLAUSE_VERB_PREFIX_NOUN_BLOCKLIST_HE = frozenset(
    {
        "מערכת",
        "מערכות",
        "מערכתי",
        "מערכתית",
        "מערכתיים",
        "מערכתיות",
        "מטרה",
        "מטרות",
        "מרכז",
        "מרכזים",
        "מרכזי",
        "מרכזית",
        "מרכזיים",
        "מרכזיות",
        "מבחינת",
        "מבחינה",
        "מגזר",
        "מגזרים",
        "מגן",
        "מסגרת",
        "מסגרות",
        "מכרז",
        "מכרזים",
        "מוצר",
        "מוצרים",
        "מדינה",
        "מדינות",
        "מידע",
        "מקור",
        "מקורות",
        "נושא",
        "נושאים",
        "נושאות",
        "נתון",
        "נתונים",
        "תוכנית",
        "תוכניות",
        "תכנית",
        "תכניות",
        "תעשייה",
        "תעשיות",
        "יכולת",
        "יכולות",
        "ידע",
        "תחום",
        "תחומים",
        "תפקיד",
        "תפקידים",
        "נשק",
        "תקציב",
        "תקציבים",
        "מחיר",
        "מחירים",
        "מספר",
        "מספרים",
        "מבנה",
        "מבנים",
        "תוצאה",
        "תוצאות",
        "נתח",
        "תקן",
        "תקנים",
        "נציג",
        "נציגים",
        "נציגות",
        "יעד",
        "יעדים",
        "יתרון",
        "יתרונות",
        "תקופה",
        "תקופות",
        "מדד",
        "מדדים",
        "תחזית",
        "תחזיות",
        "מהלך",
        "מהלכים",
        "מודל",
        "מודלים",
        "תקדים",
    }
)

# A small stoplist of short, purely-functional Hebrew words -- excluded from the "at least one real
# content word" floor (point 4 above) so a unit made up entirely of prepositions/pronouns/generic
# fillers never counts as carrying a predicate just because one of those words happens to be >= 3
# letters long.
_CLAUSE_CONTENT_STOPWORDS_HE = frozenset(
    {
        "אשר", "אבל", "אולם", "למרות", "בנוסף", "כמו", "בין", "מתוך", "לגבי",
        "לפני", "אחרי", "כדי", "הזה", "הזו", "האלה", "הללו", "וכן", "וגם",
        "זאת", "זה", "זו", "הוא", "היא", "הם", "הן", "היה", "היתה", "כבר",
        "עדיין", "כלל", "כמובן", "כאמור", "כלומר", "למעשה", "בעיקר", "בפרט",
    }
)  # fmt: skip

# The unit's own first *word* only (never mid-unit, same convention as `_CROSS_REF_OPENING_RE`):
# a standalone Hebrew "כי"/"אשר"/"אך", or an English "and"/"but"/"which".
_CLAUSE_CONTINUATION_WORD_RE = re.compile(
    r"^(?:" + "|".join(("כי", "אשר", "אך", "and", "but", "which")) + r")\b",
    re.IGNORECASE,
)


def _starts_as_continuation(text: str) -> bool:
    """Whether ``text`` (already stripped of leading whitespace) *opens* with a conjunction/
    relative/adversative token that reads as a continuation of something before it -- either one of
    :data:`_CLAUSE_CONTINUATION_WORD_RE`'s standalone words, or a Hebrew word carrying a glued
    leading ו-/ש- conjunction prefix (e.g. "ודירוג", "שהוצג") -- see the section note above for why
    this is a narrower, independent second opinion alongside round 11's own
    :data:`_CROSS_REF_OPENING_RE`, not a replacement for it. An empty/blank ``text`` counts as a
    continuation too (nothing to open a clause with in the first place)."""
    if not text:
        return True
    if _CLAUSE_CONTINUATION_WORD_RE.match(text):
        return True
    first_word = _HEBREW_WORD_RUN_RE.match(text)
    return bool(first_word and len(first_word.group(0)) >= 2 and first_word.group(0)[0] in "וש")


def _has_finite_verb_or_copula(unit_text: str) -> bool:
    """Whether ``unit_text`` plausibly contains a finite verb or copula-like structure, per the
    deliberately narrow heuristic this whole check is built on (see the section note above and
    :data:`_CLAUSE_VERB_PREFIX_NOUN_BLOCKLIST_HE`'s own docstring for why a bare prefix rule needs
    that blocklist): first, at least one Hebrew word of >= 3 letters that is not in
    :data:`_CLAUSE_CONTENT_STOPWORDS_HE` (a floor against a unit made of nothing but prepositions/
    pronouns) -- and then either a digit or a colon anywhere in the unit outside of a `[n]` citation
    marker itself (a data-bearing statement reads as complete on its own; a trailing citation's own
    digit does not count -- a headless fragment can carry one just as easily as a real sentence
    can), or a Hebrew word of >= 4 letters that begins with one of the
    future/hifil/piel present prefixes :data:`_CLAUSE_VERB_PREFIXES` (י/ת/נ/מ) and is *not* one of
    :data:`_CLAUSE_VERB_PREFIX_NOUN_BLOCKLIST_HE`'s curated common domain nouns sharing that same
    templatic shape, or a word starting with one of :data:`_CLAUSE_PAST_TENSE_VERB_STEMS_HE`'s
    curated common past-tense reporting verbs (Hebrew past tense mostly carries none of the four
    prefixes above), or a plural/construct-suffixed word (:data:`_CLAUSE_NOUN_SUFFIXES`) immediately
    preceded by a definite-article-marked word (begins with "ה", >= 3 letters -- the "ה-subject
    ...-suffix predicate" agreement shape common to both present-tense and feminine past-tense
    Hebrew predicates alike, e.g. "המערכות פועלות", "החברה פרסמה").

    Known, accepted gap (documented rather than silently wrong): Hebrew verb morphology is far
    richer than four prefix letters, one curated past-tense stem list, and three suffix shapes -- a
    legitimate masculine-singular past-tense predicate whose verb is on none of these lists and
    whose subject is not ה-prefixed ("מכרז פורסם השבוע") matches none of these signals and would be
    judged to lack a verb. This check is one heuristic signal among the four this section's caller
    combines, applied only to a section's own *leading* unit (never every unit in the answer), with
    the same precision-first bias as every other guard in this module."""
    words = _HEBREW_WORD_RUN_RE.findall(unit_text)
    if not any(len(w) >= 3 and w not in _CLAUSE_CONTENT_STOPWORDS_HE for w in words):
        return False
    # A `[n]` citation marker's own digit does not count as a "data-bearing" number -- a bare,
    # headless fragment can carry a trailing citation just as easily as a real sentence can, and
    # `_CITATION_RE` is stripped first so the digit/colon check below only ever fires on a genuine
    # figure inside the unit's own prose.
    if re.search(r"[:0-9]", _CITATION_RE.sub("", unit_text)):
        return True
    for i, w in enumerate(words):
        if len(w) >= 4 and w[0] in _CLAUSE_VERB_PREFIXES and w not in _CLAUSE_VERB_PREFIX_NOUN_BLOCKLIST_HE:
            return True
        if w.startswith(_CLAUSE_PAST_TENSE_VERB_STEMS_HE):
            return True
        if (
            w.endswith(_CLAUSE_NOUN_SUFFIXES)
            and i > 0
            and words[i - 1].startswith("ה")
            and len(words[i - 1]) >= 3
        ):
            return True
    return False


def _is_incoherent_leading_unit(stripped_unit: str) -> bool:
    """Whether ``stripped_unit`` (a section's own leading :func:`_iter_units` span, already
    ``.strip()``-ed, never a bullet/numbered-list item -- callers filter those out the same way
    every other check in :func:`enforce_answer_coherence` does) fails to read as one complete,
    independent clause -- see the section note above for the full signal rationale and the live
    repro this exists to catch.

    The "starts with a capital Latin letter / Hebrew letter / digit" and "not a bare continuation"
    checks below run against ``stripped_unit`` with any leading *decoration* stripped first (see
    :data:`_LEADING_DECORATION_RE`) -- a run of characters that are neither Latin/Hebrew letters nor
    digits, e.g. a blockquote marker ("> ⚠️ ...", :func:`relocate_source_admission_caveat`'s own
    prepended admission caveat) or a bare warning glyph (``routes.ask._OFF_TOPIC_PREFIX``, "⚠
    ייתכן..."). Both are deliberate, this module/project's *own* leading markers, not a guard-
    removal artifact -- an earlier version of this check evaluated the raw first character instead
    and wrongly flagged both as incoherent, dropping the admission caveat and the off-topic gap
    statement outright (live-verified via this section's own end-to-end test suite). A unit that is
    *nothing but* decoration once stripped is left alone here (not this check's concern -- some
    other guard's, or simply an edge case no live round has ever produced)."""
    if not stripped_unit:
        return False
    core = _LEADING_DECORATION_RE.sub("", stripped_unit, count=1)
    if not core:
        return False
    first_char = core[0]
    starts_ok = (
        (first_char.isascii() and first_char.isalpha() and first_char.isupper())
        or bool(_HEBREW_LETTER_RE.match(first_char))
        or first_char.isdigit()
    )
    if not starts_ok:
        return True
    if _starts_as_continuation(core):
        return True
    return not _has_finite_verb_or_copula(stripped_unit)


def enforce_answer_coherence(answer_text: str) -> tuple[str, int]:
    """Drop a dangling leading fragment from the very start of ``answer_text`` (the unheaded
    "תשובה ישירה" lead) or from immediately under any ``#``-heading, then drop any heading left with
    no body at all as a result. Returns ``(new_text, removed_count)`` -- ``removed_count == 0`` (and
    ``new_text == answer_text``) when nothing was dropped, matching every other guard's contract in
    this module. A no-op on a blank ``answer_text``.

    A section's own leading unit (the first :func:`_iter_units` span in its body, skipping blank
    units) is dropped when either -- **and neither check ever applies to a bullet/numbered-list
    item**, which `_iter_units` always keeps whole and so can never be a half-sentence artifact of
    the plain-prose sentence splitter this pass exists to catch (a short-but-complete bullet, e.g.
    "- לא ידוע.", is a normal, valid answer shape in this domain) --:

    -- it is shorter than :data:`_MIN_LEADING_FRAGMENT_WORDS` words -- a bare word or two is never a
       complete opening sentence on its own, and this domain's answer format (``ask_answer_format.md``)
       always writes full, grammatical claims; or
    -- it is that section's *only* remaining unit and it does not end on real terminal punctuation
       (:data:`_TERMINAL_UNIT_CHARS`) -- a single leftover fragment with nothing following it and no
       sentence-ending mark is exactly the shape a guard's removal leaves behind (see the live
       "...ב-1." repro this round's own `_iter_units` fix targets directly; this pass is the
       belt-and-suspenders catch for every removal shape that fix does not cover).

    Dropping a section's only unit can leave a heading with nothing under it -- a second pass (see
    :func:`_drop_empty_headings`) then removes any heading immediately followed (modulo blank lines)
    by another heading or the end of the text, so this never trades a dangling sentence for an empty
    section instead.

    Round 11 (docs/qa/loop/round_10_judge.md worst #3, live Q6/AUSA 2026): a second, independent
    check on that same leading unit -- gated behind an ``elif`` so it only ever runs when the unit
    is *not* already caught by the two checks above -- catches a **referentially** dangling
    opening: one that is structurally complete (long enough, real terminal punctuation) but opens
    with a cross-reference/continuation token (:data:`_CROSS_REF_OPENING_TOKENS`, e.g. "שאר
    המקורות...", "לעומת זאת...") whose antecedent was a preceding sentence some earlier guard
    removed. When the token is stripped and at least :data:`_MIN_CROSS_REF_REMAINDER_WORDS` words
    remain, the remainder replaces the unit in place (`"בנוסף, X" -> "X"`) -- the now-antecedent-free
    reference is gone, and the rest of the sentence stands on its own; when fewer words remain, the
    whole unit is dropped instead, exactly like the two checks above.

    Round 12 (docs/qa/loop/round_11_judge.md worst #1): after the three leading-unit checks above
    run, a final pass (:func:`_drop_orphan_short_fragments`) sweeps the *entire* resulting text --
    not just each section's own first unit -- for a bare, meaningless leftover unit anywhere in the
    flow (the live "סי." shape: a short, orphaned fragment some *other*, earlier guard's removal
    left standing on its own, not this section's leading unit at all). See that function's own
    docstring for the exact criteria; its removals are folded into this function's own
    ``removed_count``.

    Round 13 (docs/qa/loop/round_12_judge.md worst #2, live Q6/AUSA 2026): a fourth leading-unit
    check -- gated behind its own ``elif``, so it only ever runs on a unit none of the three checks
    above already flagged -- catches a *longer*, structurally-complete-looking leading unit that
    still reads as headless: a bare noun/conjunction chain with no finite verb or copula anywhere in
    it (see :func:`_is_incoherent_leading_unit`'s own docstring and the module note above it for the
    full four-signal rationale and the live "מים, ודירוג הכנסות..." repro this closes). A unit this
    check flags is dropped outright, unless the section's very *next* unit is itself continuation-
    shaped (:func:`_starts_as_continuation`) -- in that case both units are left alone entirely
    (dropping only the first would just crown the second, equally headless unit as the new leading
    fragment)."""
    if not answer_text or not answer_text.strip():
        return answer_text, 0

    headings = [(m.start(), m.end()) for m in _HEADING_LINE_RE.finditer(answer_text)]
    bounds: list[tuple[int, int]] = [(0, headings[0][0] if headings else len(answer_text))]
    for i, (_h_start, h_end) in enumerate(headings):
        next_start = headings[i + 1][0] if i + 1 < len(headings) else len(answer_text)
        bounds.append((h_end, next_start))

    to_replace: list[tuple[int, int, str]] = []
    for body_start, body_end in bounds:
        segment = answer_text[body_start:body_end]
        units = [(s, e) for s, e in _iter_units(segment) if segment[s:e].strip()]
        if not units:
            continue
        first_s, first_e = units[0]
        stripped = segment[first_s:first_e].strip()
        # A bullet (`-`/`*`) or numbered-list item is always kept whole by `_iter_units` -- it can
        # never be a half-sentence artifact of the plain-prose sentence splitter this pass exists
        # to catch, and a short-but-complete bullet ("- לא ידוע.", "- גם ...") is a normal, valid
        # answer shape in this domain -- so none of the checks below ever apply to one.
        if stripped.startswith(("-", "*")) or _NUMBERED_ITEM_RE.match(stripped):
            continue
        is_only_unit = len(units) == 1
        dangling = _word_count(stripped) < _MIN_LEADING_FRAGMENT_WORDS or (
            is_only_unit and stripped[-1:] not in tuple(_TERMINAL_UNIT_CHARS)
        )
        if dangling:
            to_replace.append((body_start + first_s, body_start + first_e, ""))
            continue
        cross_ref = _CROSS_REF_OPENING_RE.match(stripped)
        if cross_ref:
            remainder = stripped[cross_ref.end() :].strip()
            span = (body_start + first_s, body_start + first_e)
            if remainder and _word_count(remainder) >= _MIN_CROSS_REF_REMAINDER_WORDS:
                to_replace.append((*span, remainder))
            else:
                to_replace.append((*span, ""))
        elif _is_incoherent_leading_unit(stripped):
            if len(units) > 1:
                second_s, second_e = units[1]
                second_stripped = segment[second_s:second_e].strip()
                if not (
                    second_stripped.startswith(("-", "*")) or _NUMBERED_ITEM_RE.match(second_stripped)
                ) and _starts_as_continuation(second_stripped):
                    continue  # next unit is itself continuation-shaped -- leave both alone
            to_replace.append((body_start + first_s, body_start + first_e, ""))

    if to_replace:
        new_text = _renumber_lists(_tidy_whitespace(_replace_spans(answer_text, to_replace)))
        new_text = _drop_empty_headings(new_text)
        removed = len(to_replace)
    else:
        new_text = answer_text
        removed = 0

    new_text, orphan_removed = _drop_orphan_short_fragments(new_text)
    removed += orphan_removed

    if removed == 0:
        return answer_text, 0
    return new_text, removed


def _drop_empty_headings(text: str) -> str:
    """Remove any ``#``-heading line whose own body (up to the next heading, or the end of the
    text) is empty/whitespace-only -- the "never leaves an empty section heading" half of the
    round-10 coherence pass, called from :func:`enforce_answer_coherence` after a section's only
    unit is dropped as a dangling fragment. A no-op when every heading already has real content
    under it."""
    headings = [(m.start(), m.end()) for m in _HEADING_LINE_RE.finditer(text)]
    if not headings:
        return text
    drop_spans: list[tuple[int, int]] = []
    for i, (h_start, h_end) in enumerate(headings):
        next_start = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        if not text[h_end:next_start].strip():
            drop_spans.append((h_start, next_start))
    if not drop_spans:
        return text
    return _tidy_whitespace(_remove_spans(text, drop_spans))


# ---------------------------------------------------------------------------------------------
# Round 7 item 2 (docs/qa/loop/round_6_judge.md D5 worst-list #9, live Q5/Skyranger): confident
# factual claims in the direct-answer paragraph / key-facts section, correctly cited when they do
# carry a `[n]`, but a minority left uncited alongside their cited siblings in the same scope --
# then a footer admission ("6 of 8 sources unrelated") the reader only reaches after already having
# read the confident, uncited claims as if they were equally well-supported.
# ---------------------------------------------------------------------------------------------


def filter_uncited_factual_claims(answer_text: str, retrieved: list[dict[str, Any]]) -> tuple[str, int]:
    """Drop every factual (:func:`eoa.report.qa_citations.is_factual`), `[n]`-less unit inside the
    leading direct-answer paragraph or the "עובדות מרכזיות" section, *but only* in whichever of
    those two scopes already has at least one `[n]`-cited unit of its own -- a scope with *zero*
    citations at all is left alone here (that is the anchor guard's/
    :func:`retrieval_relevance_caveat`'s job, not this one's: a wholesale uncited scope is a
    retrieval-relevance problem, while a *mixed* scope -- some claims cited, others not -- is this
    guard's live Q5 pattern, where the uncited claims read as if they carried the same evidentiary
    weight as their cited neighbours). A non-factual uncited unit (a connector sentence, a list
    intro) is left alone regardless -- this only ever removes a claim :func:`is_factual` itself
    would flag as making a checkable assertion. A no-op on a blank answer or empty ``retrieved``
    (same rationale as every other guard in this module).

    Live-verified 2026-09-07 (throwaway offline replay against golden Q5/Skyranger's actual live
    answer): the model rendered its comparison as a markdown *table* rather than the expected
    bullet list -- `_iter_units`' plain sentence-splitter is not table-aware (a pre-existing,
    documented limitation of this same helper elsewhere in this module, e.g.
    :func:`ground_and_filter_answer`'s own docstring), so it chopped each table row into several
    citation-less fragments and this guard, unlike the module's other more conservative checks,
    would have deleted most of the table. Any unit containing a literal ``|`` (a table row or
    separator) is therefore skipped outright here -- left for a future round to make this module's
    shared unit-splitter table-aware everywhere, the same follow-up round 3/5 already flagged for
    the removal guards."""
    if not answer_text or not answer_text.strip() or not retrieved:
        return answer_text, 0

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

    heading_match = _FIRST_SECTION_HEADING_RE.search(answer_text)
    lead_end = heading_match.start() if heading_match else len(answer_text)

    def _scope_key(pos: int) -> str | None:
        if pos < lead_end:
            return "lead"
        if _KEY_FACTS_MARKER in _section_at(pos):
            return "key_facts"
        return None

    units = [
        (s, e, answer_text[s:e])
        for s, e in _iter_units(answer_text)
        if answer_text[s:e].strip() and "|" not in answer_text[s:e]
    ]
    scoped: dict[str, list[tuple[int, int, str]]] = {}
    for s, e, t in units:
        key = _scope_key(s)
        if key is not None:
            scoped.setdefault(key, []).append((s, e, t))

    flagged: list[tuple[int, int]] = []
    for scope_units in scoped.values():
        if not any(_CITATION_RE.search(t) for _, _, t in scope_units):
            continue  # wholly-uncited scope -- not this guard's job (see docstring)
        for s, e, t in scope_units:
            if _CITATION_RE.search(t):
                continue
            if is_factual(t):
                flagged.append((s, e))

    if not flagged:
        return answer_text, 0
    new_text = _renumber_lists(_tidy_whitespace(_remove_spans(answer_text, flagged)))
    return new_text, len(flagged)


# ---------------------------------------------------------------------------------------------
# Round 7 item 2, continued: the model's own disclosed admission that most of its retrieved
# sources are unrelated is genuinely useful information -- it just needs to be the *first* thing a
# reader sees, not a footer discovered only after already reading confident-sounding claims above
# it (the exact live Q5 shape).
# ---------------------------------------------------------------------------------------------

_ADMISSION_KEYWORDS = (
    "אינם קשורים",
    "אינו קשור",
    "לא קשורים",
    "לא קשור",
    "לא רלוונטיים",
    "לא רלוונטי",
    "unrelated",
)


def _is_admission_unit(unit_text: str) -> bool:
    lowered = unit_text.casefold()
    return any(kw.casefold() in lowered for kw in _ADMISSION_KEYWORDS)


def relocate_source_admission_caveat(answer_text: str) -> tuple[str, bool]:
    """Move every unit of ``answer_text`` that reads as the model's own admission that some of its
    retrieved sources are unrelated/irrelevant (see :data:`_ADMISSION_KEYWORDS`) to the very top of
    the answer, as a single leading blockquote caveat -- instead of wherever it originally trailed
    (live Q5: a footer, reached only after the confident claims above it). Returns
    ``(new_text, moved)``; a no-op (``moved is False``, ``new_text == answer_text``) when no such
    admission is found, on a blank answer, *or* when the only matching unit is already the very
    first unit of the text (idempotent against being run a second time on text this function
    already rewrote -- see the ``start > 0`` guard below: once relocated, the admission sits at
    offset 0 and is never a candidate for relocation again)."""
    if not answer_text or not answer_text.strip():
        return answer_text, False
    admission_spans = [
        (s, e) for s, e in _iter_units(answer_text) if s > 0 and _is_admission_unit(answer_text[s:e])
    ]
    if not admission_spans:
        return answer_text, False
    admission_text = " ".join(answer_text[s:e].strip() for s, e in admission_spans)
    remainder = _renumber_lists(_tidy_whitespace(_remove_spans(answer_text, admission_spans)))
    new_text = f"> ⚠️ {admission_text}" + "\n\n" + remainder.lstrip()
    return new_text, True


# ---------------------------------------------------------------------------------------------
# Round 7 item 2, continued: even after the two guards above run, an answer can still end up with
# almost nothing actually `[n]`-cited (live Q5: confident HEL-capability/EMCON-parallel claims with
# "almost no inline citations") -- a generic, one-time caveat for whenever no more specific
# model-authored admission (see above) already covers the same ground.
# ---------------------------------------------------------------------------------------------

_LOW_CITATION_CAVEAT = "> ⚠️ התשובה מבוססת על מקורות מעטים/עקיפים; ראו רשימת המקורות."


_LOW_CITATION_MIN_FACTUAL_UNITS = 3


def low_citation_caveat(answer_text: str) -> tuple[str, int]:
    """Prepend :data:`_LOW_CITATION_CAVEAT` once, at the very top of ``answer_text``, when the
    answer makes at least :data:`_LOW_CITATION_MIN_FACTUAL_UNITS` factual
    (:func:`eoa.report.qa_citations.is_factual`) claims *and* fewer than 2 of them carry a `[n]`
    citation. Returns ``(new_text, added)`` with ``added`` either ``0`` or ``1``.

    The minimum-factual-units gate (round 7, added after live-verifying against the existing
    round-3/round-5 e2e fixtures per this round's own brief) keeps a short, single-fact,
    correctly-cited answer -- e.g. one "עובדות מרכזיות" bullet with its own `[n]` -- from being
    flagged: "fewer than 2 factual sentences cited" is only a meaningful low-confidence signal once
    the answer is actually making *several* claims and citing almost none of them (the live
    Q5/Skyranger shape: many confident factual sentences, "almost no inline citations") -- not
    when it is simply a short, complete answer with one fact and one citation.

    A no-op (``added == 0``) on a blank answer, below the minimum-factual-units gate, when the
    caveat is already present (idempotent against being run again on text this function already
    rewrote, or against :func:`relocate_source_admission_caveat` having already supplied a more
    specific caveat for the same underlying condition -- see the ``routes/ask.py`` wiring, which
    only calls this one when that one found nothing to relocate), or when >= 2 factual units are
    already cited."""
    if not answer_text or not answer_text.strip():
        return answer_text, 0
    if _LOW_CITATION_CAVEAT in answer_text:
        return answer_text, 0
    total_factual = 0
    cited_factual = 0
    for start, end in _iter_units(answer_text):
        unit_text = answer_text[start:end]
        if not unit_text.strip() or not is_factual(unit_text):
            continue
        total_factual += 1
        if _CITATION_RE.search(unit_text):
            cited_factual += 1
    if total_factual < _LOW_CITATION_MIN_FACTUAL_UNITS or cited_factual >= 2:
        return answer_text, 0
    return _LOW_CITATION_CAVEAT + "\n\n" + answer_text.lstrip(), 1


# ---------------------------------------------------------------------------------------------
# Round 7 item 3: optional light-model entailment check (config-gated, ``ask.entailment_check`` /
# ``ask.entailment_max_claims`` in ``config/config.yaml`` -- **chat only**, never wired into the
# pipeline/report paths). A purely probabilistic, additive pass on top of every deterministic guard
# above -- never a substitute for them, and always a silent no-op on any error, timeout, or empty
# candidate list.
# ---------------------------------------------------------------------------------------------


class _ClaimVerdict(BaseModel):
    index: int
    verdict: Literal["yes", "no", "partial"]


class _EntailmentResponse(BaseModel):
    verdicts: list[_ClaimVerdict]


_ENTAILMENT_SYSTEM = (
    "אתה בודק עקביות עובדתית בין טענות ממוספרות לבין קטעי מקור שצורפו להן. עבור כל טענה, קבע האם "
    'קטע המקור שלה תומך בה: "yes" -- תומך במלואה, "partial" -- תומך באופן חלקי/עקיף בלבד, "no" -- '
    "אינו תומך בה כלל. החזר verdict אחד לכל טענה, לפי מספרה, ורק עבורה -- אל תוסיף טענות."
)

# Round 11 (docs/qa/loop/round_10_judge.md worst #4): 1500 -> 800 -- see `entailment_filter`'s own
# round-11 docstring note for why a smaller per-claim payload gives the light-role call a better
# chance of finishing inside its own wall-clock budget.
_ENTAILMENT_SOURCE_EXCERPT_CHARS = 800


def _entailment_scope_candidates(
    answer_text: str, sources_by_n: dict[int, str], max_claims: int
) -> list[tuple[int, int, str]]:
    """``(start, end, cited_excerpt)`` for up to ``max_claims`` `[n]`-cited units inside the lead
    paragraph or the "עובדות מרכזיות" section, in document order -- the same two citation-required
    scopes :func:`filter_claim_grounding` checks. ``cited_excerpt`` is the unit's own cited
    source(s) text, joined and trimmed to :data:`_ENTAILMENT_SOURCE_EXCERPT_CHARS` chars (a cost/
    latency bound on what gets sent to the light model, not a grounding-precision one -- the
    deterministic guards above already ran on the full text)."""
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

    heading_match = _FIRST_SECTION_HEADING_RE.search(answer_text)
    lead_end = heading_match.start() if heading_match else len(answer_text)

    candidates: list[tuple[int, int, str]] = []
    for start, end in _iter_units(answer_text):
        unit_text = answer_text[start:end]
        if not unit_text.strip():
            continue
        in_scope = start < lead_end or _KEY_FACTS_MARKER in _section_at(start)
        if not in_scope:
            continue
        cited_ns = _cited_ns(unit_text)
        if not cited_ns:
            continue
        excerpt = " ".join(sources_by_n.get(n, "") for n in cited_ns)[:_ENTAILMENT_SOURCE_EXCERPT_CHARS]
        candidates.append((start, end, excerpt))
        if len(candidates) >= max_claims:
            break
    return candidates


def _run_with_timeout(fn: Any, timeout_s: float) -> tuple[Any, str | None]:
    """Run ``fn()`` (no args) on a background thread, returning ``(result, None)`` on success or
    ``(None, error)`` on a timeout *or any exception* (a failed/slow light-model call must never
    block or break the caller's own request). Deliberately does not join the worker thread on
    timeout (``shutdown(wait=False)``): the caller's hard budget must never itself wait on however
    much longer the underlying (already-abandoned) call takes to actually return.

    Round 9 (docs/qa/loop/round_8_judge_b.md finding 4): ``error`` is the failing exception's
    class name (``"TimeoutError"`` for a genuine wall-clock timeout, or e.g.
    ``"ValidationError"``/``"ConnectError"``/``"ResourceUnavailable"`` for a real failure) instead
    of a single undifferentiated ``None`` -- so the caller's own skip-reason log line can tell a
    timeout apart from a hard failure rather than collapsing both into the same
    ``reason="timeout_or_error"`` string, which round 8's own live sample could never actually
    distinguish."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout_s), None
        except concurrent.futures.TimeoutError:
            return None, "TimeoutError"
        except Exception as exc:
            return None, type(exc).__name__
    finally:
        executor.shutdown(wait=False)


# Round 10 (docs/qa/loop/round_9_judge.md finding 2): whether `entailment_unavailable` (see
# `entailment_filter` below) has already been logged once in this process's lifetime -- a
# module-level flag, not per-request, so a sustained RAM shortage does not fill the log with an
# identical line on every single chat request with no new information after the first.
_ENTAILMENT_UNAVAILABLE_LOGGED = False


def entailment_filter(
    answer_text: str,
    retrieved: list[dict[str, Any]],
    *,
    max_claims: int = 6,
    timeout_s: float = 30.0,
    chain_fallback: bool = False,
    chain_timeout_s: float = 40.0,
    fast_chain_timeout_s: float = 60.0,
    answer_elapsed_s: float | None = None,
) -> tuple[str, int]:
    """Optional light-model entailment check over up to ``max_claims`` `[n]`-cited units in the
    lead paragraph / "עובדות מרכזיות" section: asks the ``light`` role a single structured
    yes/no/partial question per candidate (see :data:`_ENTAILMENT_SYSTEM`), against that unit's own
    cited source excerpt -- removing a unit only when the model answers "no" (a real
    ``eoa.llm.ollama_client.chat_structured`` call is imported lazily, inside this function, purely
    so a unit test can monkeypatch it without importing the whole ollama client eagerly at module
    load, matching this project's existing lazy-import convention for optional LLM calls, e.g.
    ``eoa.security.guard._l2_judge``).

    This is a purely additive, probabilistic pass on top of every deterministic guard above --
    never a substitute for them. Always a silent no-op (returns ``answer_text`` unchanged) on a
    blank answer, empty ``retrieved``, no in-scope cited candidate at all, a timeout, or any LLM/
    validation error (see :func:`_run_with_timeout`) -- the caller decides, via
    ``ask.entailment_check``, whether to invoke this at all; this function itself does not read
    that setting, so it stays fully testable/callable independent of config.

    Round 8 (docs/qa/loop/round_7_judge_b.md / round_8_fixes.md "### R8-chat-b status"): live-found
    2026-09-07 that this check skipped on 5 of 5 sampled real answers (100%), every one logged
    ``reason=timeout_or_error`` -- root-caused to the ``_call`` closure below never passing
    ``interactive=True`` to ``chat_structured``, so every attempt queued behind the resource gate's
    *batch* budget (minutes) while this function's own :func:`_run_with_timeout` wall-clock was
    always going to expire first regardless of how fast the light model would have answered once
    actually admitted -- the exact same "batch queue vs. interactive budget" gap ``routes.ask``'s
    own P1 fix (2026-09-06) already closed for the main chat generation, just never applied here.
    Fixed below; ``timeout_s``'s default is also raised 20.0 -> 30.0 (extra headroom once actually
    admitted under the interactive budget, per this round's own brief) and the caller
    (``routes.ask``) now passes ``ask.entailment_max_claims`` lowered 6 -> 4 in
    ``config/config.yaml`` (fewer claims batched into one call, so each admitted call finishes
    faster).

    Round 9 (docs/qa/loop/round_8_judge_b.md finding 4): the round-8 fix above did not hold --
    live-sampled again 2026-09-07, this check still skipped on every attempt with
    ``reason=timeout_or_error``. Root cause this time is one level up: ``llm_providers.
    interactive_default`` is ``"chain"`` as of round 7 (Claude -> Gemini -> local, `config/
    config.yaml`), and the ``_call`` closure below passed no explicit ``provider`` -- so
    ``chat()`` resolved the *cloud* chain instead of the plain local Ollama path the round-8 fix
    assumed it was reaching. A cloud CLI leg's own budget (``llm_providers.timeout_s``, 360s) has
    nothing to do with this function's own hard wall-clock, and is routinely far longer than it --
    so every real attempt was, in effect, racing a subprocess it could never win against, and
    ``interactive=True`` (still passed, still correct) never even got the chance to matter, since
    it only governs the *local* resource-gate path this call no longer reached. Fixed by pinning
    ``provider="ollama"`` explicitly below: this optional, additive, best-effort probe now always
    stays on the local, resource-gated path -- bounded by ``resources.interactive_wait_s`` (20s)
    on a real RAM shortage rather than an unbounded cloud subprocess -- which is also cheaper and
    keeps this housekeeping check off the cloud provider entirely. Separately,
    :func:`_run_with_timeout` now reports the failing exception's class name instead of a single
    ``None``, so a genuine timeout is distinguishable in the logs from a hard failure going
    forward.

    Round 10 (docs/qa/loop/round_9_judge.md finding 2): live-sampled again 2026-09-07,
    ``ask.entailment_check_removed``/``_skipped`` together confirm this check is only actually
    *active* on 1 of 8 golden answers -- round 9's own ``provider="ollama"`` pin (correct on its
    own terms: it does keep this call off an unbounded cloud subprocess) put this probe on exactly
    the same resource-gated local path that is under the heaviest RAM pressure on this host, so the
    fix that closed round 9's "races an unbounded cloud subprocess" bug reopened "starves alongside
    every other local call under real RAM pressure" instead. A cloud leg bypasses the local
    resource gate entirely (see ``eoa.llm.ollama_client.chat``'s own docstring), so ``chain_fallback
    =True`` (the caller in ``routes.ask`` passes this; the default here stays ``False``) now tries
    the local ``ollama`` leg first exactly as before, and -- only when that first attempt fails for
    any reason -- a second attempt goes out through the configured cloud ``chain`` (``provider=
    "chain"``, :data:`chain_timeout_s`'s 40s default -- the cloud CLI itself can legitimately take
    25-40s, per this round's own brief -- instead of racing this function's local-attempt clock).
    When *both* attempts fail, ``ask.entailment_check_skipped`` still fires every time (unchanged),
    but this also now logs ``ask.entailment_unavailable`` once -- and only once -- per process (see
    :data:`_ENTAILMENT_UNAVAILABLE_LOGGED`): a sustained RAM shortage that starves every attempt for
    an entire night would otherwise repeat the identical skip line on every single request with no
    new information after the first one.

    ``chain_fallback`` defaults to ``False`` -- an opt-in, not the new default -- deliberately: the
    local-only, single-attempt contract round 9 shipped is exactly what ``test_ask_round9.py``'s
    ``TestEntailmentPinnedToOllama`` (a single successful call must carry ``provider="ollama"``) and
    ``test_ask_round7.py``'s ``TestEntailmentFilter.test_timeout_is_a_graceful_no_op`` (a primary
    call slower than ``timeout_s`` must be a graceful no-op, full stop -- not retried against a
    second, much longer budget) both assert -- both shared-suite tests this package does not own and
    must not edit. Flipping the fallback on only at the one real call site (``routes.ask``) keeps
    every existing caller's tested contract byte-for-byte unchanged while still shipping the actual
    live fix this round's brief asked for.

    Round 11 (docs/qa/loop/round_10_judge.md worst #4, D5 finding 3): round 10's own live sample
    found this check active on only 3/8 golden answers (up from 1/8, real progress, still short of
    "well above 1/8"). Three further changes, all still gated behind ``chain_fallback``/only
    reachable once the primary local leg has already failed, so round 9's pinned single-attempt
    contract is unaffected either way:

    1. **Smaller probe payload** (:data:`_ENTAILMENT_SOURCE_EXCERPT_CHARS` 1500 -> 800, plus the
       caller-side 4-claim cap already in place, one call for the whole batch either way -- never
       per-claim) -- a smaller request body gives a marginal/queued attempt a better chance of
       finishing inside whatever time it does get, independent of which leg answers it.
    2. **Elapsed-aware chain timeout**: ``chain_timeout_s`` (40s default) is only used when the
       caller does not report ``answer_elapsed_s``, or reports one already >= 90s (little of this
       question's own per-request budget left to spend). When the caller *does* report
       ``answer_elapsed_s < 90.0`` -- i.e. the main answer itself streamed back quickly and this
       optional housekeeping pass has real headroom before the request's own overall budget is at
       risk -- the chain attempt gets :data:`fast_chain_timeout_s` (60s default) instead, per this
       round's own brief. ``routes.ask`` computes this from the same ``t_answer_start`` wall clock
       its own ``_MAX_ANSWER_SECONDS`` abort check already uses.
    3. **Resident-chain fallback when the light role itself is unavailable, not merely slow**: the
       ``light`` role's own configured chain (``config/config.yaml``'s ``llm_providers.chains.
       light``) is deliberately ``agy(gemini-3.8-flash-medium) -> ollama`` with **no Claude entry
       at all** (2026-09-06 decision: cheap-flash-first, to stop freezing chat behind a slow local
       queue) -- so when *both* the direct-ollama attempt and the light-role-chain attempt fail,
       that is not "one leg slow", it is the entire light role unavailable. A third, explicitly
       paid attempt then goes out against the **resident** role's own chain instead (``chat_
       structured("resident", ..., provider="chain")`` -- resident's chain does carry a Claude
       entry first, per ``llm_providers.chains.resident``), logged as ``ask.entailment_resident_
       fallback_used`` when it is the attempt that actually produces a result. Documented cost:
       this is a real Claude call, not the light role's usual free/cheap flash-tier or local one --
       acceptable here because it only ever fires as a last resort, after two cheaper legs have
       already failed, on an optional pass that is skipped outright (no cost at all) when the
       caller does not opt into ``chain_fallback``.
    """
    if not answer_text or not answer_text.strip() or not retrieved:
        return answer_text, 0
    sources_by_n = _sources_by_n(retrieved)
    candidates = _entailment_scope_candidates(answer_text, sources_by_n, max_claims)
    if not candidates:
        return answer_text, 0

    claims_block = "\n\n".join(
        f"טענה {i}:\n{answer_text[s:e].strip()}\nקטע מקור מצוטט:\n{excerpt}"
        for i, (s, e, excerpt) in enumerate(candidates, start=1)
    )
    messages = [
        {"role": "system", "content": _ENTAILMENT_SYSTEM},
        {"role": "user", "content": claims_block},
    ]

    def _call(provider: str, *, role: str = "light") -> _EntailmentResponse:
        from eoa.llm.ollama_client import chat_structured

        # Round 9: an explicit `provider` is always passed (see the docstring above) -- an unset
        # `provider` resolves through `llm_providers.interactive_default` ("chain" as of round 7),
        # which round 10's own two-attempt strategy now controls explicitly instead. `interactive=
        # True` is still passed on both attempts so a real local RAM shortage fails fast via the
        # resource gate instead of hanging. Round 11: `role` defaults to "light" (unchanged for the
        # first two attempts) but the resident-chain fallback below passes `role="resident"` so it
        # reaches resident's own chain (which does carry a Claude entry) instead of light's.
        return chat_structured(
            role,
            _EntailmentResponse,
            messages,
            task="classify",
            interactive=True,
            provider=provider,
        )

    result, error = _run_with_timeout(lambda: _call("ollama"), timeout_s)
    if result is None and chain_fallback:
        # Round 10: the local leg failed/timed out -- try once more through the light role's own
        # configured cloud chain, which never touches the local resource gate at all, before giving
        # up. Gated behind `chain_fallback` (see the docstring above) so a caller that did not opt
        # in stays on round 9's exact single-attempt contract.
        # Round 11: the chain-attempt budget itself is elapsed-aware -- see the docstring's point 2.
        chain_budget = chain_timeout_s
        if answer_elapsed_s is not None and answer_elapsed_s < 90.0:
            chain_budget = fast_chain_timeout_s
        chain_result, chain_error = _run_with_timeout(lambda: _call("chain"), chain_budget)
        if chain_result is not None:
            result, error = chain_result, None
        else:
            error = chain_error or error
            # Round 11 (docstring point 3): both the direct-ollama and the light-role-chain
            # attempts failed -- the light role as a whole is unavailable this call, not just one
            # slow leg. One further, explicitly paid attempt against the resident role's own chain
            # (which does carry a Claude entry, unlike light's agy-then-ollama chain) before giving
            # up entirely.
            resident_result, resident_error = _run_with_timeout(
                lambda: _call("chain", role="resident"), chain_timeout_s
            )
            if resident_result is not None:
                result, error = resident_result, None
                log.info("ask.entailment_resident_fallback_used", claims=len(candidates))
            else:
                error = resident_error or error
    if result is None:
        global _ENTAILMENT_UNAVAILABLE_LOGGED
        if not _ENTAILMENT_UNAVAILABLE_LOGGED:
            _ENTAILMENT_UNAVAILABLE_LOGGED = True
            log.warning("ask.entailment_unavailable", reason=error or "timeout_or_error")
        log.info("ask.entailment_check_skipped", reason=error or "timeout_or_error", claims=len(candidates))
        return answer_text, 0

    no_indices = {v.index for v in result.verdicts if v.verdict == "no"}
    if not no_indices:
        return answer_text, 0
    flagged = [(s, e) for i, (s, e, _) in enumerate(candidates, start=1) if i in no_indices]
    if not flagged:
        return answer_text, 0
    log.info("ask.entailment_check_removed", claims=len(candidates), removed=len(flagged))
    new_text = _renumber_lists(_tidy_whitespace(_remove_spans(answer_text, flagged)))
    return new_text, len(flagged)
