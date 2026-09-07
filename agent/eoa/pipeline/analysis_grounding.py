"""Round-14 (2026-09-07): post-generation grounding guard for the *analysis* stage --
``summary_he``, ``so_what_he``, ``key_facts`` and ``entities_mentioned`` -- applied before every
persistence of those fields (``eoa.pipeline.analyze.persist_analysis``/``repair_so_what_text``)
and, for already-persisted rows, by ``scripts/repair_round14_grounding.py``.

Root cause (user report 2026-09-07, severe): item 39 (edrmagazine.eu, "The all-new 15-300 mm f/4
MWIR zoom engineered for 10 um SXGA detectors" -- a lens product announcement whose own source
text is entirely about the Ophir(R) SupIR-X lens and never once mentions Israel Aerospace
Industries) had its ``summary_he`` claim "Ophir Optronics, חברה בת של תעשייה אווירית (תע"א)"
(a subsidiary of IAI) -- false: Ophir Optronics is a subsidiary of MKS Instruments (already
recorded as the watchlist alias "MKS Ophir" on the Ophir Optronics entry in config/watchlist.yaml,
and confirmed by this very item's own source text photo credit, "photo courtesy MKS"). The
fabricated affiliation then seeded a fabricated ``so_what_he`` competitor list ("פלנטריוניקס
(Planar Optics) וטלסקופיקס (Telescopeics)" -- neither exists), which in turn flowed into
downstream weekly/BD reports. ``eoa.pipeline.analyze`` had no grounding check of any kind before
this round -- unlike the chat ``/api/ask`` path, which already has one (``eoa.api.ask_grounding``:
entity grounding, cross-source conflation, number grounding, ...). This module is the analysis-
stage counterpart, purpose-built for a *single-source* item (summary_he/so_what_he/key_facts are
FACT/ASSESSMENT mode over exactly one item's own text, not a multi-source retrieval-and-synthesis
answer) rather than a straight import of that module.

Per this codebase's own stated convention for a cross-module/cross-layer boundary helper (see
``eoa.pipeline.analyze._event_dedup_key``'s docstring: "kept as a small, local duplicate rather
than an import"), the small number-grounding primitives this module reuses in spirit from
``eoa.api.ask_grounding`` (money/year digit-boundary-safe matching, spelled-number normalization)
are duplicated here in a narrower form rather than imported -- ``eoa.pipeline`` importing from
``eoa.api`` would also be a layering inversion (``eoa.api`` depends on ``eoa.pipeline``, never the
reverse).

Four rules, each independently additive (a unit -- one sentence of ``summary_he``/``so_what_he``,
or one ``key_facts``/``entities_mentioned`` entry -- that fails any rule is dropped or trimmed;
the rest of the field is kept, "coherent" per the task brief, by operating at sentence/clause
granularity rather than blanking the whole field):

  a. **Entity grounding** -- every organisation name (Latin proper noun, or a Hebrew
     institution/company-shaped phrase, see :data:`_LATIN_PROPER_NOUN_RE`/:data:`_HEBREW_ORG_RE`)
     mentioned in the output must appear (literally, case-insensitive) in the item's own source
     corpus (title + clean/raw text + url + url domain, see :func:`_record_corpus`), OR resolve to
     a watchlist/curated-org record (:mod:`eoa.pipeline.entity_normalize`) at least one of whose
     *other* aliases the source corpus actually mentions -- being on the watchlist alone is not
     enough (the item-39 case exactly: "תעשייה אווירית"/"תע"א" resolves straight to IAI, but IAI is
     never mentioned in this item's source text under any of its aliases, so it is still stripped).
     A unit with an ungrounded organisation name is dropped whole.
  b. **Affiliation/ownership claims** (חברה בת של / בבעלות / חטיבה של / זרוע של / נרכשה על ידי /
     חברה אם / subsidiary of / owned by / division of / part of, see
     :data:`_AFFILIATION_CLAUSE_RE`) are resolved surgically, at clause granularity (not the whole
     sentence -- an affiliation clause has a clean regex boundary and the rest of the sentence is
     usually good FACT content worth keeping): the claim is valid when (i) ``config/
     company_facts.yaml`` has a curated record for either side and the *other* side matches one of
     its recorded ``parents`` -- a claim contradicting a fact we are confident in is hard-rejected
     even if the source text happens to also mention the wrong parent somewhere; else (ii), absent
     any registry record, both organisations must co-occur within the *same* sentence of the
     item's own source text. An invalid clause is excised; the rest of the sentence is kept.
  c. **Competitor/peer lists** (:func:`_strip_ungrounded_competitors`, ``so_what_he`` only, per the
     task brief) -- every name following a "מתחרים/מתחרותיה/competitors/rivals ... כמו/כגון/such
     as/including" cue must be grounded exactly like rule (a); an ungrounded name is dropped from
     the list (the rest of the list is kept), and the whole cue+list clause is dropped only when
     *no* listed name grounds.
  d. **Numbers/units** (:func:`_strip_ungrounded_numbers`, ``summary_he`` only, per the task brief)
     -- every money figure and year must appear in the source corpus, reusing the same digit-
     boundary-safe matching and Hebrew/English spelled-number normalization
     ``eoa.api.ask_grounding`` uses for the chat path (:func:`_digits_grounded`/
     :func:`_normalize_spelled_numbers`, both duplicated here in the narrower form this stage
     needs -- ``summary_he`` is meant to copy numbers verbatim from a *single* source per
     ``prompts/analyze.md``, not reconcile several retrieved sources the way a chat answer must,
     so this module deliberately does not port ``ask_grounding``'s magnitude-aware money-scale
     comparison or its plain-count mismatch guard). A unit naming an ungrounded money figure/year
     is dropped whole (numbers rarely appear alone in a FACT-mode sentence with nothing else worth
     keeping, unlike an organisation-name mention).

:func:`ground_analysis_fields` is the single entry point every caller uses -- returns a
:class:`GroundingResult` (grounded field values + a flat list of every removal, already logged as
``analysis.ungrounded_<category>_removed``). :func:`is_too_thin` is the caller's own signal for
"stripping left this field too damaged to keep as-is, re-run the analysis instead" (see
``eoa.pipeline.analyze.repair_so_what_text`` and ``scripts/repair_round14_grounding.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

import structlog

from eoa.config import settings
from eoa.pipeline import entity_normalize

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------------------------
# source corpus
# ---------------------------------------------------------------------------------------------


def _record_corpus(record: dict[str, Any]) -> str:
    """The "source text" a grounded claim about ``record`` (an ``items`` or ``patents`` row) is
    allowed to cite verbatim from: title, main body text (whichever of ``clean_text``/
    ``raw_text``/``abstract`` the row carries), the url and its domain, and -- for a patents row --
    the ``assignees`` list (the one place a patent's own "who owns this" fact lives)."""
    parts: list[str | None] = [
        record.get("title"),
        record.get("clean_text"),
        record.get("raw_text"),
        record.get("abstract"),
        record.get("claims_text"),
    ]
    url = record.get("url") or ""
    if url:
        parts.append(url)
        try:
            parts.append(urlsplit(url).netloc)
        except ValueError:
            pass
    assignees = record.get("assignees")
    if assignees:
        parts.append(" ".join(a for a in assignees if a))
    return "\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------------------------
# sentence/unit splitting -- deliberately simple: this stage's fields are short (1-4 sentences),
# LLM-generated, single-line Hebrew/English prose (see prompts/analyze.md), not free-form web text.
# ---------------------------------------------------------------------------------------------

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _split_units(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [p for p in _SENTENCE_SPLIT_RE.split(text) if p.strip()]


def _join_units(units: list[str]) -> str:
    return " ".join(u.strip() for u in units if u.strip())


def _tidy(text: str) -> str:
    """Cosmetic cleanup after a clause/sentence removal: dangling double commas/periods left by an
    excised clause, doubled whitespace."""
    text = re.sub(r"\s+([.,:;!?])", r"\1", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r",\s*\.", ".", text)
    text = re.sub(r"^[,\s]+", "", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# ---------------------------------------------------------------------------------------------
# organisation-name candidate detection + grounding (rule a)
# ---------------------------------------------------------------------------------------------

_HEBREW_LETTER_RE = re.compile(r"[א-ת]")
_HEBREW_PREFIX_LETTERS = "בלמוהשכ"

# Two-or-more capitalised Latin "words" (letters/digits, hyphen- or space-joined), each >= 2 chars
# -- same shape/rationale as eoa.api.ask_grounding._PROPER_NOUN_RE (see that module's own comment
# for why the >= 2 chars-per-segment floor exists: it keeps a short acronym-style compound like
# "C-UAS" from ever reaching this regex, so it is never flagged just because this specific item's
# text didn't also happen to spell it out).
_LATIN_PROPER_NOUN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]{1,}(?:[-\s][A-Z][A-Za-z0-9]{1,}){1,4}\b")

# Head-noun-shaped Hebrew organisation phrases: a (possibly single-prefix-letter-glued) head noun
# followed by 1-3 trailing name tokens (including a trailing parenthetical acronym gloss, e.g. the
# item-39 "(תע"א)"). Deliberately excludes "חברה"/"חברת" ("company") -- the most common Hebrew
# affiliation-clause connector is itself "חברה בת של" ("subsidiary of"), and a bare "חברה"-headed
# match would collide with that connector phrase far more often than it would ever catch a genuine
# organisation name (a real company name mentioned via "חברת X" is caught anyway, as a candidate,
# by the Latin proper-noun regex above when X is in Latin script, which is the overwhelmingly
# common case in this corpus's watchlist). Also excludes "תעשייה"/"תעשיית" ("industry") and
# "יחידת" ("unit") as *general* head nouns -- live-verified against the patents corpus (round-14
# dry run, 2026-09-07): "תעשייה"/"יחידת" are common ordinary vocabulary there ("התעשייה הביטחונית
# הישראלית" as a generic sector reference, "יחידת אינטגרציה וקריאה"/"יחידת פיקסל" as circuit
# components in patent claim language), not organisation-name claims, and flagging every such
# mention produced 84 false-positive removals across 74 of 86 patent rows in that dry run. The one
# real organisation-name usage "תעשייה"/"תעשיית" ever needs to catch here -- "תעשייה אווירית" as
# IAI's own colloquial Hebrew name, item 39's exact fabrication -- is instead matched narrowly by
# :data:`_IAI_COLLOQUIAL_RE` below. "קבוצת" ("group") is excluded for the same reason -- it
# collides with the standard DoD UAS size classification ("קבוצת משקל 3"/"Group 3", live-verified
# false positives on items 47/96) far more often than it catches a real "X Group" company name (a
# Latin "X Group" company name is caught anyway via the Latin proper-noun regex above).
_HEBREW_ORG_HEAD_NOUNS = (
    "אוניברסיטת",
    "אוניברסיטה",
    "מכון",
    "משרד",
    "רשות",
    "מעבדות",
    "מעבדת",
    "חטיבת",
    "זרוע",
)
_HEBREW_ORG_HEAD_NOUN_ALT = "|".join(sorted(_HEBREW_ORG_HEAD_NOUNS, key=len, reverse=True))
_HEBREW_ORG_TOKEN_RE = r"(?:[א-ת][א-ת'\"׳״]*|[A-Za-z][A-Za-z0-9]*|\([^)]{1,20}\))"
_HEBREW_ORG_RE = re.compile(
    rf"(?<![א-ת])[{_HEBREW_PREFIX_LETTERS}]?(?:{_HEBREW_ORG_HEAD_NOUN_ALT})"
    rf"(?:\s+{_HEBREW_ORG_TOKEN_RE}){{1,3}}"
)

# "תעשייה אווירית"/"תעשיית אווירית" ("aviation/aerospace industry") is IAI's own well-known
# colloquial Hebrew name (config/watchlist.yaml's IAI entry carries "התעשייה האווירית" as an
# alias) -- narrowly matched on its own (not via the general head-noun list above) so a *generic*
# "X industry" mention ("התעשייה הביטחונית", "תעשיית ההגנה העולמית") is never treated as an
# organisation-name claim.
_IAI_COLLOQUIAL_RE = re.compile(
    rf"(?<![א-ת])[{_HEBREW_PREFIX_LETTERS}]?תעשיי?ה\s+אווירית(?:\s*\([^)]{{1,20}}\))?"
)


_PAREN_SPAN_RE = re.compile(r"\([^)]*\)")


def _org_candidates(text: str) -> list[str]:
    """Rule (a)'s organisation-name candidates in ``text``: multi-word Latin proper nouns and
    head-noun-shaped Hebrew institution phrases (see the two regexes above) -- except a Latin
    candidate falling entirely *inside* a parenthetical span is skipped. ``prompts/analyze.md``
    explicitly instructs the model to gloss a Hebrew technical term with its English original in
    parentheses ("מונחים מקצועיים באנגלית בסוגריים") -- "MWIR (Mid-Wave Infrared)" is exactly this,
    a real, correct domain-vocabulary gloss, not a claim about a specific named entity, and it is
    routine enough in this corpus that treating every such gloss as an organisation-name candidate
    would be a standing false-positive source stripping legitimate FACT-mode content on nearly
    every item. A genuinely fabricated company mentioned only inside parentheses (never as the
    sentence's own subject) is a real, narrower coverage gap this leaves -- rule (c)'s own
    competitor-list check is unaffected (it never uses this function, and explicitly expects and
    handles a "פלנטריוניקס (Planar Optics)"-shaped Hebrew-name-with-Latin-gloss list entry)."""
    if not text:
        return []
    paren_spans = [m.span() for m in _PAREN_SPAN_RE.finditer(text)]
    candidates = [
        m.group(0)
        for m in _LATIN_PROPER_NOUN_RE.finditer(text)
        if not any(s <= m.start() and m.end() <= e for s, e in paren_spans)
    ]
    candidates += [m.group(0).strip() for m in _HEBREW_ORG_RE.finditer(text)]
    candidates += [m.group(0).strip() for m in _IAI_COLLOQUIAL_RE.finditer(text)]
    return list(dict.fromkeys(c for c in candidates if c))


def _resolve_org(candidate: str) -> dict[str, Any] | None:
    """:func:`eoa.pipeline.entity_normalize.resolve_canonical`, tried against ``candidate`` and a
    handful of cheap variants an organisation-name candidate pulled out of free Hebrew prose might
    need: its own parenthetical acronym gloss on its own (the item-39 "(תע"א)" case), the phrase
    with its outer parenthetical stripped, progressively shorter word-prefixes of it (mirroring
    ``ask_grounding._hebrew_entity_grounded``'s own "try shorter prefixes" step), and -- since
    Hebrew has no capitalisation to mark a definite article and the source's own spelling may or
    may not carry one -- both with and without a leading "ה"/single prefix-preposition letter."""
    if not candidate:
        return None
    attempts = [candidate]
    paren_m = re.search(r"\(([^)]{1,20})\)", candidate)
    if paren_m:
        attempts.append(paren_m.group(1).strip())
    base = re.split(r"\s*\(", candidate, maxsplit=1)[0].strip()
    if base and base != candidate:
        attempts.append(base)
    words = base.split()
    for length in range(len(words), 0, -1):
        attempts.append(" ".join(words[:length]))
    widened: list[str] = []
    for a in attempts:
        if not a:
            continue
        widened.append(a)
        if a[0] in _HEBREW_PREFIX_LETTERS:
            widened.append(a[1:])
        aw = a.split()
        if aw:
            widened.append(" ".join(["ה" + aw[0], *aw[1:]]))
    for a in dict.fromkeys(x.strip() for x in widened if x and x.strip()):
        record = entity_normalize.resolve_canonical(a)
        if record is not None:
            return record
    return None


@lru_cache(maxsize=1)
def _country_surface_forms() -> dict[str, list[str]]:
    """Reverse of ``eoa.pipeline.entity_normalize``'s own ``_COUNTRY_NAMES`` table (every
    Hebrew/English surface form -> canonical English country name): canonical name -> every
    surface form it was built from. ``entities_mentioned`` regularly carries a country in its
    English canonical form (e.g. "Estonia") even for an item whose own source text is entirely in
    Hebrew ("אסטוניה") -- :func:`entity_normalize.resolve_country_name` only compares a single
    candidate string against that table, it does not expose this reverse direction, which is what
    a country-name grounding check specifically needs."""
    table = getattr(entity_normalize, "_COUNTRY_NAMES", {})
    reverse: dict[str, list[str]] = {}
    for surface_key, canonical in table.items():
        reverse.setdefault(canonical, []).append(surface_key)
    return reverse


def _country_grounded(candidate: str, corpus: str) -> bool | None:
    """``None`` when ``candidate`` isn't a recognised country name at all (the caller falls
    through to its other checks); else whether *some* Hebrew or English surface form of the same
    country appears anywhere in ``corpus`` -- both sides compared via
    :func:`entity_normalize.normalize_name_key` (punctuation-insensitive), since the country
    table's own keys are stored in that normalized form and a raw Hebrew quotation mark
    (``ארה"ב``) would otherwise never literally match a plain-space-separated key."""
    canonical = entity_normalize.resolve_country_name(candidate)
    if canonical is None:
        return None
    normalized_corpus = entity_normalize.normalize_name_key(corpus)
    return any(
        surface_key and surface_key in normalized_corpus
        for surface_key in _country_surface_forms().get(canonical, [])
    )


def _resolved_name_keys(name: str) -> set[str]:
    keys = {entity_normalize.normalize_name_key(name)}
    record = _resolve_org(name)
    if record:
        keys |= {
            entity_normalize.normalize_name_key(s) for s in [record.get("name", ""), *record.get("aliases", [])]
        }
    return {k for k in keys if k}


def _words_grounded(text: str, corpus_cf: str) -> bool:
    """Every individual Latin word (>= 3 chars) of ``text`` appears *somewhere* in ``corpus_cf``
    (mirrors ``ask_grounding._proper_noun_grounded`` step 3) -- a real fact reworded/reordered (or,
    for a resolved record, a registry alias that itself is not a verbatim match for how the source
    happens to phrase it -- see :func:`_entity_grounded`'s own item-290 note) is not flagged just
    because the exact phrase isn't a contiguous substring."""
    words = [w for w in re.split(r"[-\s]+", text) if len(w) >= 3]
    return len(words) >= 2 and all(w.casefold() in corpus_cf for w in words)


def _entity_grounded(candidate: str, corpus: str, corpus_cf: str) -> bool:
    """Rule (a): ``candidate`` is grounded when it appears literally in ``corpus`` (case-
    insensitive), OR it resolves (:func:`_resolve_org`) to a watchlist/curated-org record at least
    one of whose *other* aliases the corpus actually mentions -- being on the watchlist by itself
    is never enough (see the module docstring's item-39 example). A registry record's own alias
    list is not always a verbatim match for how a *specific* source happens to phrase the same
    entity (live-verified, item 290: the curated "UK Ministry of Defence" record's aliases are
    "MoD UK"/"British Ministry of Defence"/"משרד ההגנה הבריטי" -- none of which is a literal
    substring of the source's own "the Ministry of Defence (MoD)" -- which very nearly stripped
    every mention of the article's own central, legitimate subject), so a resolved record also
    falls through to the same word-by-word paraphrase check (:func:`_words_grounded`) applied to
    each of its own Latin surface forms, not only the original (possibly Hebrew) candidate."""
    if not candidate or not candidate.strip():
        return True
    if candidate.casefold() in corpus_cf:
        return True
    country_hit = _country_grounded(candidate, corpus)
    if country_hit is not None:
        return country_hit
    record = _resolve_org(candidate)
    if record is not None:
        surfaces = [record.get("name", ""), *record.get("aliases", [])]
        if any(s and s.casefold() in corpus_cf for s in surfaces):
            return True
        return any(s and not _HEBREW_LETTER_RE.search(s) and _words_grounded(s, corpus_cf) for s in surfaces)
    return not _HEBREW_LETTER_RE.search(candidate) and _words_grounded(candidate, corpus_cf)


def _strip_ungrounded_entities(units: list[str], corpus: str) -> tuple[list[str], list[str]]:
    corpus_cf = corpus.casefold()
    kept: list[str] = []
    removed: list[str] = []
    for unit in units:
        bad = next(
            (c for c in _org_candidates(unit) if not _entity_grounded(c, corpus, corpus_cf)), None
        )
        if bad is not None:
            removed.append(bad)
        else:
            kept.append(unit)
    return kept, removed


# ---------------------------------------------------------------------------------------------
# affiliation/ownership claims (rule b)
# ---------------------------------------------------------------------------------------------

_AFFILIATION_CUE_ALT = (
    r"חברה[\s-]?בת של|חברת[\s-]?בת של|בבעלות(?:\s+של)?|חטיבה של|חטיבת|זרוע של|"
    r"נרכשה על ידי|חברה[\s-]?אם(?:\s+של)?|"
    r"\bsubsidiary of\b|\bowned by\b|\bdivision of\b|\bpart of\b"
)
# Captures (and, on rejection, removes) just the clause naming the claimed parent -- an optional
# leading comma, the cue phrase, the parent name up to the next comma/period/paren, and an optional
# trailing parenthetical acronym gloss -- leaving the rest of the sentence (usually genuine FACT
# content about the child company) untouched.
_AFFILIATION_CLAUSE_RE = re.compile(
    r"(?P<lead>,\s*)?"
    rf"(?P<cue>{_AFFILIATION_CUE_ALT})"
    r"\s*(?P<parent>[^,.()]{1,60}?)"
    r"(?:\s*\((?P<parent_acr>[^)]{1,20})\))?"
    r"(?=[,.()]|$)",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _company_facts_index() -> dict[str, dict[str, Any]]:
    """``{normalize_name_key(surface) -> entry}`` for every ``config/company_facts.yaml`` entry
    with ``confidence`` "high" or "medium" (a "low"/unconfirmed fact is deliberately never listed
    in that file at all, rather than being loaded and then filtered here -- see the file's own
    header note), indexed by the entry's own ``name`` and, where that name also resolves on the
    watchlist/curated-org table, every one of that record's own aliases too."""
    data = settings().company_facts or {}
    index: dict[str, dict[str, Any]] = {}
    for entry in data.get("entries", []) or []:
        name = entry.get("name", "")
        if not name or entry.get("confidence") not in ("high", "medium"):
            continue
        for key in _resolved_name_keys(name):
            index[key] = entry
    return index


def _company_facts_entry(name: str) -> dict[str, Any] | None:
    for key in _resolved_name_keys(name):
        entry = _company_facts_index().get(key)
        if entry is not None:
            return entry
    return None


def _matches_registered_parent(candidate_variants: list[str], parents: list[str]) -> bool:
    cand_keys: set[str] = set()
    for v in candidate_variants:
        if v:
            cand_keys |= _resolved_name_keys(v)
    return any(cand_keys & _resolved_name_keys(parent) for parent in parents)


def _affiliation_pair_valid(
    child: str, parent_variants: list[str], corpus_sentences: list[str]
) -> bool:
    """Rule (b)'s validity test for one claimed ``child`` .. ``parent`` affiliation: a
    ``config/company_facts.yaml`` record for either side, checked first, wins outright (a claim
    contradicting a fact this registry is confident in is invalid regardless of what the source
    text says); absent any registry record for either side, the claim is valid only when ``child``
    and (any variant of) ``parent`` both ground (:func:`_entity_grounded`) within one *same*
    sentence of the item's own source text."""
    entry = _company_facts_entry(child)
    if entry is not None:
        return _matches_registered_parent(parent_variants, entry.get("parents") or [])
    for pv in parent_variants:
        entry2 = _company_facts_entry(pv)
        if entry2 is not None:
            return _matches_registered_parent([child], entry2.get("parents") or [])
    for sentence in corpus_sentences:
        s_cf = sentence.casefold()
        if _entity_grounded(child, sentence, s_cf) and any(
            pv and _entity_grounded(pv, sentence, s_cf) for pv in parent_variants
        ):
            return True
    return False


def _last_org_candidate(text: str) -> str | None:
    """The organisation-name candidate (:func:`_org_candidates`, plus any watchlist alias found by
    :func:`eoa.pipeline.entity_normalize.find_watchlist_aliases_in_text`) whose literal occurrence
    sits latest in ``text`` -- used to find the claim's own subject/"child" company from the text
    immediately preceding an affiliation clause."""
    candidates = _org_candidates(text)
    for name in entity_normalize.find_watchlist_aliases_in_text(text):
        if name not in candidates:
            candidates.append(name)
    best: str | None = None
    best_pos = -1
    for c in candidates:
        pos = text.rfind(c)
        if pos > best_pos:
            best_pos = pos
            best = c
    return best


def _strip_affiliation_clauses(
    unit: str, corpus_sentences: list[str], fallback_child: str | None
) -> tuple[str, list[str]]:
    removed: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        parent = (m.group("parent") or "").strip()
        parent_acr = (m.group("parent_acr") or "").strip()
        parent_variants = [v for v in (parent, parent_acr) if v]
        if not parent_variants:
            return m.group(0)
        child = _last_org_candidate(unit[: m.start()]) or fallback_child
        if child is None:
            return m.group(0)  # nothing to validate the claim's subject against -- leave as-is
        if _affiliation_pair_valid(child, parent_variants, corpus_sentences):
            return m.group(0)
        parent_full = f"{parent} ({parent_acr})" if parent_acr else parent
        removed.append(f"{child} -> {parent_full}")
        return ""

    return _AFFILIATION_CLAUSE_RE.sub(_repl, unit), removed


# ---------------------------------------------------------------------------------------------
# competitor/peer lists (rule c, so_what_he only)
# ---------------------------------------------------------------------------------------------

_COMPETITOR_LIST_RE = re.compile(
    r"(?P<cue>מתחרותיה|מתחרים(?:\s+שלה)?|מתחרה(?:\s+עיקרית)?|competitors?|rivals?)"
    r"(?P<connector>\s+(?:כמו|כגון|including|such as|like)\s+)"
    r"(?P<list>.+?)(?=[.,;]|$)",
    re.IGNORECASE,
)
_NAME_SPLIT_RE = re.compile(r",\s*|\s+ו-?\s*|\s+and\s+|\s*&\s*", re.IGNORECASE)


def _strip_ungrounded_competitors(text: str, corpus: str) -> tuple[str, list[str]]:
    corpus_cf = corpus.casefold()
    removed: list[str] = []

    def _repl(m: re.Match[str]) -> str:
        names = [n.strip() for n in _NAME_SPLIT_RE.split(m.group("list")) if n.strip()]
        if not names:
            return m.group(0)
        kept_names = []
        for n in names:
            if _entity_grounded(n, corpus, corpus_cf):
                kept_names.append(n)
            else:
                removed.append(n)
        if not kept_names:
            return ""
        if len(kept_names) == len(names):
            return m.group(0)
        return m.group("cue") + m.group("connector") + ", ".join(kept_names)

    return _COMPETITOR_LIST_RE.sub(_repl, text), removed


# ---------------------------------------------------------------------------------------------
# numbers/units (rule d, summary_he only) -- narrow local port of eoa.api.ask_grounding's own
# digit-boundary-safe matching and spelled-number normalization; see the module docstring for why
# this is a duplicate, not an import, and why it is deliberately narrower in scope.
# ---------------------------------------------------------------------------------------------

_MONEY_RE = re.compile(
    r"[$€₪]\s?\d[\d,.]*\s?(?:[MBK]\b)?"
    r"|\b\d[\d,.]*\s?(?:מיליון|מיליארד|million|billion|USD|EUR)\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _grouped_digit_pattern(digits: str) -> str:
    groups: list[str] = []
    i = len(digits)
    while i > 0:
        groups.append(digits[max(0, i - 3) : i])
        i -= 3
    groups.reverse()
    return r"[,.]?".join(re.escape(g) for g in groups)


def _digits_grounded(candidate: str, corpus: str) -> bool:
    """Duplicated from ``eoa.api.ask_grounding._digits_grounded`` (round 9/13 fixes there): a
    digit run is grounded only by a genuine standalone occurrence of the same digits in ``corpus``
    -- never a substring of a differently-scaled number (a decimal-boundary check) -- and accepts
    an optional thousands separator at either side of the comparison."""
    digits = re.sub(r"[^\d]", "", candidate)
    if len(digits) < 2:
        return True
    pattern = r"(?<!\d)(?<!\d\.)" + _grouped_digit_pattern(digits) + r"(?!\d)(?!\.\d)"
    return re.search(pattern, corpus) is not None


_COUNT_NUMBER_WORDS_EN: dict[str, int] = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}  # fmt: skip
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
    word.casefold(): str(value) for word, value in {**_COUNT_NUMBER_WORDS_EN, **_COUNT_NUMBER_WORDS_HE}.items()
}
_COUNT_NUMBER_WORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in sorted(_COUNT_NUMBER_WORD_TO_DIGIT, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _normalize_spelled_numbers(text: str) -> str:
    """Duplicated from ``eoa.api.ask_grounding._normalize_spelled_numbers``: every spelled-out
    cardinal number word (English one..twenty, Hebrew אחד..עשרים, both genders/two-word teens) is
    replaced with its digit form, so a digit-form claim can still be grounded against a source that
    spells the same number out in words."""
    if not text:
        return text
    return _COUNT_NUMBER_WORD_RE.sub(lambda m: _COUNT_NUMBER_WORD_TO_DIGIT[m.group(0).casefold()], text)


def _strip_ungrounded_numbers(units: list[str], corpus: str) -> tuple[list[str], list[str]]:
    normalized_corpus = _normalize_spelled_numbers(corpus)
    kept: list[str] = []
    removed: list[str] = []
    for unit in units:
        bad = None
        for m in _MONEY_RE.finditer(unit):
            if not _digits_grounded(m.group(0), normalized_corpus):
                bad = m.group(0)
                break
        if bad is None:
            for m in _YEAR_RE.finditer(unit):
                if not _digits_grounded(m.group(0), normalized_corpus):
                    bad = m.group(0)
                    break
        if bad is not None:
            removed.append(bad)
        else:
            kept.append(unit)
    return kept, removed


# ---------------------------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------------------------


@dataclass
class GroundingResult:
    summary_he: str
    so_what_he: str
    key_facts: list[str]
    entities_mentioned: list[str]
    removed: list[dict[str, Any]] = field(default_factory=list)


def is_too_thin(text: str, *, min_chars: int = 15, require_prefix: str | None = None) -> bool:
    """True when ``text`` (a field's value *after* grounding has stripped from it) is damaged
    enough that persisting it as-is would be worse than re-running the analysis: empty/near-empty,
    or -- when ``require_prefix`` is given (``so_what_he`` must start with "להערכתנו") -- missing
    that required opening because the sentence carrying it was itself the one stripped."""
    t = (text or "").strip()
    if len(t) < min_chars:
        return True
    return bool(require_prefix) and not t.startswith(require_prefix)


def _ground_prose_field(
    field_name: str,
    text: str,
    corpus_sentences: list[str],
    corpus: str,
    fallback_child: str | None,
    log_removed: Any,
) -> str:
    if not text:
        return text
    units = _split_units(text)
    trimmed_units: list[str] = []
    for unit in units:
        unit2, removed_aff = _strip_affiliation_clauses(unit, corpus_sentences, fallback_child)
        for r in removed_aff:
            log_removed(field_name, "affiliation", r)
        trimmed_units.append(_tidy(unit2))
    kept, bad_entities = _strip_ungrounded_entities(trimmed_units, corpus)
    for name in bad_entities:
        log_removed(field_name, "entity", name)
    return _tidy(_join_units(kept))


def ground_analysis_fields(
    record: dict[str, Any],
    *,
    summary_he: str = "",
    so_what_he: str = "",
    key_facts: list[str] | None = None,
    entities_mentioned: list[str] | None = None,
) -> GroundingResult:
    """The single entry point every caller uses. ``record`` is the ``items`` (or ``patents``) row
    the fields were generated for -- only its own text/title/url/assignees are ever treated as
    "the source" a claim can be grounded against (see :func:`_record_corpus`). Every removal is
    both returned (``GroundingResult.removed``) and logged as
    ``analysis.ungrounded_<category>_removed`` (categories: ``entity``, ``affiliation``,
    ``competitor``, ``number``) with the item id and the exact value dropped."""
    corpus = _record_corpus(record)
    corpus_sentences = _split_units(corpus)
    record_id = record.get("id")
    existing_entities = record.get("entities_mentioned") or entities_mentioned or []
    fallback_child = existing_entities[0] if existing_entities else None
    removed_log: list[dict[str, Any]] = []

    def _log_removed(f: str, category: str, value: str) -> None:
        removed_log.append({"field": f, "category": category, "value": value})
        log.info(
            f"analysis.ungrounded_{category}_removed",
            item_id=record_id,
            field=f,
            value=value[:200],
        )

    new_summary = _ground_prose_field(
        "summary_he", summary_he, corpus_sentences, corpus, fallback_child, _log_removed
    )
    if new_summary:
        summary_units, bad_numbers = _strip_ungrounded_numbers(_split_units(new_summary), corpus)
        for n in bad_numbers:
            _log_removed("summary_he", "number", n)
        new_summary = _tidy(_join_units(summary_units))

    # Competitor-list trimming (rule c) runs *before* the generic entity/affiliation pass so an
    # ungrounded name inside an otherwise-fine competitor list is removed surgically, one name at a
    # time -- if it ran after, the generic entity check (rule a, sentence-granularity) would often
    # already have caught the very same ungrounded Latin/Hebrew name and dropped the whole sentence
    # first, leaving rule (c) nothing left to trim surgically.
    so_what_after_competitors = so_what_he
    if so_what_after_competitors:
        so_what_after_competitors, bad_competitors = _strip_ungrounded_competitors(
            so_what_after_competitors, corpus
        )
        for c in bad_competitors:
            _log_removed("so_what_he", "competitor", c)
        so_what_after_competitors = _tidy(so_what_after_competitors)
    new_so_what = _ground_prose_field(
        "so_what_he", so_what_after_competitors, corpus_sentences, corpus, fallback_child, _log_removed
    )

    new_key_facts = list(key_facts or [])
    if new_key_facts:
        kept_facts, bad_fact_entities = _strip_ungrounded_entities(new_key_facts, corpus)
        for name in bad_fact_entities:
            _log_removed("key_facts", "entity", name)
        new_key_facts = kept_facts

    new_entities = list(entities_mentioned or [])
    if new_entities:
        corpus_cf = corpus.casefold()
        kept_entities = []
        for name in new_entities:
            if _entity_grounded(name, corpus, corpus_cf):
                kept_entities.append(name)
            else:
                _log_removed("entities_mentioned", "entity", name)
        new_entities = kept_entities

    return GroundingResult(
        summary_he=new_summary,
        so_what_he=new_so_what,
        key_facts=new_key_facts,
        entities_mentioned=new_entities,
        removed=removed_log,
    )
