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
    same noun phrase (unverifiable is not the same as contradicted)."""
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
        exact_hit: str | None = None
        fuzzy_hit: str | None = None
        for _s_start, _s_end, s_digit, s_noun in source_candidates:
            kind = _count_noun_match_kind(c_noun, s_noun)
            if kind == "exact":
                exact_hit = s_digit
                break
            if kind == "fuzzy" and fuzzy_hit is None:
                fuzzy_hit = s_digit
        if exact_hit is not None and exact_hit != c_digit:
            return c_start, c_end, exact_hit, True
        if fallback is None and fuzzy_hit is not None and fuzzy_hit != c_digit:
            fallback = (c_start, c_end, fuzzy_hit, False)
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
            if leading_ws and not cleaned[:1].isspace():
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

_ENTAILMENT_SOURCE_EXCERPT_CHARS = 1500


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


def _run_with_timeout(fn: Any, timeout_s: float) -> Any:
    """Run ``fn()`` (no args) on a background thread, returning its result -- or ``None`` on a
    timeout *or any exception* (a failed/slow light-model call must never block or break the
    caller's own request). Deliberately does not join the worker thread on timeout
    (``shutdown(wait=False)``): the caller's hard budget must never itself wait on however much
    longer the underlying (already-abandoned) call takes to actually return."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout_s)
        except Exception:
            return None
    finally:
        executor.shutdown(wait=False)


def entailment_filter(
    answer_text: str,
    retrieved: list[dict[str, Any]],
    *,
    max_claims: int = 6,
    timeout_s: float = 30.0,
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

    def _call() -> _EntailmentResponse:
        from eoa.llm.ollama_client import chat_structured

        # Round 8: `interactive=True` is the actual fix (see the docstring above) -- without it
        # this call queued behind the resource gate's patient batch budget, not the short
        # interactive one, and skipped on every single sampled live answer as a result.
        return chat_structured("light", _EntailmentResponse, messages, task="classify", interactive=True)

    result = _run_with_timeout(_call, timeout_s)
    if result is None:
        log.info("ask.entailment_check_skipped", reason="timeout_or_error", claims=len(candidates))
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
