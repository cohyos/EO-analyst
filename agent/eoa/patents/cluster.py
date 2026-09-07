"""Deterministic patent clustering, timeline/expiry math, business-relationship extraction, and
the "no-invented-negation" consistency check (A14b, docs/PLAN_WINDOWS_NATIVE.md row A14b, user
requirement 2026-09-06 points 2/3/6). Pure, DB-free, LLM-free logic over the same registry-row
shape ``eoa.patents.survey`` already builds (a dict per patent: ``n``/``id``/``title``/
``abstract``/``assignees``/``cpc``/``publication_date``/``filing_date``/``priority_date``/
``grant_date``/``family_id``) -- kept in its own module (rather than growing ``survey.py`` further)
so every piece here is unit-testable without a database, Ollama, or ``docx_builder``.

**Clustering method** (point 2 of the 2026-09-06 request): primarily the patent's first/primary
CPC code (already a coarse subclass-level code in this project's own data, e.g. ``"G01J5"`` --
see ``config/patents.yaml``), labelled in Hebrew via whichever configured watch topic already
names that CPC code (``config/patents.yaml``'s ``watch_topics``). A patent with no CPC code at all
(the common case for the keyless Google-Patents-search fallback -- see ``eoa.patents.scan``'s own
docstring) falls back to a keyword-overlap match against each watch topic's own query text; a
patent matching neither used to land in one flat "לא מסווג" bucket regardless of size. Round 5 D8
(2026-09-06, docs/REPORT_TEMPLATE_BENCHMARK.md P5 follow-up): that flat bucket is now itself
sub-clustered by a dependency-free "TF-IDF-lite" pass over each unclassified patent's own
title/abstract text (:func:`tfidf_subcluster_unclassified` -- term frequency x inverse document
frequency across just this survey's unclassified patents, no ``sklearn``/``numpy`` needed, at most
``UNCLASSIFIED_MAX_SUBCLUSTERS`` sub-clusters), each labelled by its own top terms rather than the
single generic "לא מסווג" label -- still an unsupervised text-similarity heuristic, not a legal CPC
classification, and still never fabricates a topic name the way a watch-topic match already has one
supplied.

**Cross-citation note**: this project's ``patents`` table only stores citation *counts*
(``forward_citations``/``backward_citations``), never the actual citing/cited publication numbers
(neither EPO OPS/PatentsView-when-configured nor the keyless search fallback surface that graph) --
so a genuine cross-citation edge between two specific patents cannot be constructed from this data.
:func:`same_family_groups` (shared ``family_id``) is the one real structural link available instead,
documented here as exactly that (a patent-family relationship, not a citation).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Protocol

PATENT_TERM_YEARS = 20
_DAYS_PER_YEAR = 365.25
EXPIRY_SOON_YEARS = 3

FLAG_EXPIRED_HE = "פג"
FLAG_EXPIRING_SOON_HE = "עומד לפוג ב-3 השנים הקרובות"
FLAG_PENDING_HE = "בבחינה"
UNCLASSIFIED_KEY = "_UNCLASSIFIED_"
UNCLASSIFIED_LABEL_HE = "לא מסווג"


class _TopicLike(Protocol):
    name_he: str
    query: str
    cpc: list[str]


# --------------------------------------------------------------------------
# expiry / timeline (point 6)
# --------------------------------------------------------------------------


def expiry_estimate(filing_date: dt.date | None, priority_date: dt.date | None) -> dt.date | None:
    """20 years from ``filing_date`` (falling back to ``priority_date`` when filing is unknown) --
    the standard utility-patent term proxy, "subject to maintenance fees" (בכפוף לתחזוקה) per the
    user's own phrasing; this is never a legal-certainty expiry date, only an estimate."""
    base = filing_date or priority_date
    if base is None:
        return None
    return base + dt.timedelta(days=round(PATENT_TERM_YEARS * _DAYS_PER_YEAR))


def expiry_flag(
    *, expiry_date: dt.date | None, grant_date: dt.date | None, today: dt.date | None = None
) -> str:
    """One of :data:`FLAG_PENDING_HE` / :data:`FLAG_EXPIRED_HE` / :data:`FLAG_EXPIRING_SOON_HE`, or
    ``""`` for a normal in-force patent. A patent with no recorded ``grant_date`` is always flagged
    "בבחינה" (still under examination / not confirmed granted in this project's own data) regardless
    of its estimated expiry -- an un-granted application has no term running yet."""
    today = today or dt.date.today()
    if grant_date is None:
        return FLAG_PENDING_HE
    if expiry_date is None:
        return ""
    if expiry_date <= today:
        return FLAG_EXPIRED_HE
    if expiry_date <= today + dt.timedelta(days=round(EXPIRY_SOON_YEARS * _DAYS_PER_YEAR)):
        return FLAG_EXPIRING_SOON_HE
    return ""


@dataclass
class TimelineRow:
    n: int
    pub_number: str
    title: str | None
    priority_date: dt.date | None
    filing_date: dt.date | None
    publication_date: dt.date | None
    grant_date: dt.date | None
    expiry_date: dt.date | None
    flag_he: str


def build_timeline_rows(
    registry_rows: list[dict[str, Any]], *, today: dt.date | None = None
) -> list[TimelineRow]:
    """One :class:`TimelineRow` per patent in ``registry_rows`` (each a dict carrying at least
    ``n``/``pub_number``; missing date fields are simply ``None`` -- never invented)."""
    today = today or dt.date.today()
    out: list[TimelineRow] = []
    for row in registry_rows:
        filing_date = row.get("filing_date")
        priority_date = row.get("priority_date")
        grant_date = row.get("grant_date")
        expiry = expiry_estimate(filing_date, priority_date)
        out.append(
            TimelineRow(
                n=row["n"],
                pub_number=row.get("pub_number") or "",
                title=row.get("title"),
                priority_date=priority_date,
                filing_date=filing_date,
                publication_date=row.get("publication_date"),
                grant_date=grant_date,
                expiry_date=expiry,
                flag_he=expiry_flag(expiry_date=expiry, grant_date=grant_date, today=today),
            )
        )
    return out


def filing_waves(rows: list[dict[str, Any]], key_fn: Any) -> dict[str, Counter[int]]:
    """``{key: {year: count}}`` filing/publication waves grouped by whatever ``key_fn(row)``
    returns (a cluster label, an assignee name, ...) -- ``None``/falsy keys and rows with no
    ``publication_date`` are skipped (never fabricate a year)."""
    out: dict[str, Counter[int]] = defaultdict(Counter)
    for row in rows:
        d = row.get("publication_date")
        if not d:
            continue
        key = key_fn(row)
        if not key:
            continue
        out[key][d.year] += 1
    return dict(out)


# --------------------------------------------------------------------------
# clustering (point 1 + 2)
# --------------------------------------------------------------------------


_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "using",
    "based",
    "system",
    "systems",
    "method",
    "methods",
    "device",
    "devices",
    "apparatus",
    "of",
    "in",
    "on",
    "to",
    "an",
    "or",
    # round 7 (live label "מעקב / not / has"): plain English function words that survived the
    # boilerplate stoplist on a content-free metadata row
    "not",
    "has",
    "have",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "by",
    "at",
    "as",
    "from",
    "that",
    "this",
    "these",
    "those",
    "it",
    "its",
    "which",
    "can",
    "may",
    "will",
    "one",
    "more",
    "than",
    "into",
    "such",
    "also",
    "said",
    "wherein",
    "thereof",
    "therein",
    "least",
    "each",
    "other",
    "first",
    "second",
}


def _keyword_tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "") if t.lower() not in _STOPWORDS}


def _topic_keyword_index(topics: list[_TopicLike]) -> list[tuple[_TopicLike, set[str]]]:
    return [(t, _keyword_tokens(t.query)) for t in topics]


# --------------------------------------------------------------------------
# round 7 D8 point 1/2 (2026-09-06/07, docs/qa/loop/round_6_judge.md D8 worst #10): the term pool
# feeding a TF-IDF sub-cluster label must never be built from assignee names -- a keyless-search
# abstract routinely embeds the raw USPTO assignment-record boilerplate verbatim (confirmed live
# against the ``patents`` table 2026-09-07, e.g. patent id 62's entire abstract is "2019-03-07
# Assigned to Anduril Industries Inc. reassignment Anduril Industries Inc. ASSIGNMENT OF
# ASSIGNORS INTEREST (SEE DOCUMENT FOR DETAILS)." -- zero technical content), which previously let
# a company name or plain assignment-paperwork jargon become the cluster's own displayed label
# term. Two stoplists cover the two distinct failure modes: a corporate-suffix stoplist (a bare
# suffix word that leaks out of an assignee name once the name itself is stripped, e.g. "Sabanci
# Universitesi" -> "universitesi") and a patent-administrative-boilerplate stoplist (recurring
# non-technical scrape/metadata noise that is not tied to any one assignee, e.g. "reassignment").
# --------------------------------------------------------------------------

_CORPORATE_SUFFIX_STOPWORDS = {
    "inc",
    "ltd",
    "llc",
    "corp",
    "corporation",
    "co",
    "gmbh",
    "ag",
    "sa",
    "systems",
    "technologies",
    "company",
    "university",
    "universitesi",
    "institute",
    "industries",
}

_PATENT_BOILERPLATE_STOPWORDS = {
    "assigned",
    "assignee",
    "assignees",
    "assignment",
    "assignors",
    "reassignment",
    "interest",
    "document",
    "details",
    "download",
    "pdf",
    "google",
    "patents",
    "patent",
    "definitions",
    "invention",
    "inventionbelongs",
    "inventionrefers",
    "embodiment",
    "embodiments",
    "see",
    # Google Patents' own standard "Legal status" disclaimer paragraph and per-record metadata
    # fields (confirmed live 2026-09-07 against patents.id=70/29 -- e.g. "Legal status (The legal
    # status is an assumption and is not a legal conclusion. Google has not performed a legal
    # analysis and makes no representation as to the accuracy of the status listed.)" and "Free
    # format text: JAPANESE INTERMEDIATE CODE: A523 - Effective date: 20190619") -- recurring
    # scrape noise, never a technology term, and not tied to any one assignee so it belongs here
    # rather than in the assignee-token strip.
    "legal",
    "status",
    "assumption",
    "conclusion",
    "analysis",
    "representation",
    "accuracy",
    "listed",
    "inaccurate",
    "performed",
    "free",
    "format",
    "text",
    "intermediate",
    "effective",
    "date",
    "japanese",
}


def _assignee_name_tokens(assignees: list[str] | None) -> set[str]:
    """Every lowercase word (``_WORD_RE``'s own ``{A-Za-z}{3,}`` shape) that appears in any of the
    row's own real assignee names -- e.g. ``["Anduril Industries Inc"]`` -> ``{"anduril",
    "industries", "inc"}`` -- so those tokens can be scrubbed out of the term pool wherever the
    assignee name itself has leaked into the scraped title/abstract text (round 7 D8 point 1)."""
    out: set[str] = set()
    for name in assignees or []:
        out.update(t.lower() for t in _WORD_RE.findall(name or ""))
    return out


# --------------------------------------------------------------------------
# round 5 D8 point 3 (2026-09-06): TF-IDF-lite sub-clustering of the "לא מסווג" bucket -- pure
# stdlib (no numpy/sklearn dependency, consistent with the rest of this DB-free/LLM-free module).
# Hebrew tokens are captured alongside the existing Latin-word tokenizer above (which stays
# untouched, and keeps driving the watch-topic keyword-overlap match in :func:`_keyword_cluster`)
# so a patent whose title/abstract does carry Hebrew text (e.g. a Hebrew-topic keyless-search
# result) still contributes real terms to its sub-cluster's own label -- "top terms in Hebrew+
# English" per the round-5 spec, mirroring how every *other* cluster label in this module already
# mixes a Hebrew frame with an English/Latin technical term (see config/patents.yaml's own
# ``name_he`` values, e.g. "SWIR / eSWIR (InGaAs, נקודות קוונטיות)").
# --------------------------------------------------------------------------

_HEBREW_WORD_RE = re.compile(r"[֐-׿]{2,}")
UNCLASSIFIED_MAX_SUBCLUSTERS = 5
_UNCLASSIFIED_LABEL_TOP_TERMS = 3
_UNCLASSIFIED_SIMILARITY_THRESHOLD = 0.15


def _subcluster_tokens(text: str) -> list[str]:
    """Latin words (≥3 chars, stopwords dropped -- same tokenizer as :func:`_keyword_tokens`) plus
    Hebrew words (≥2 chars) from ``text``, as a list (repeats kept, unlike :func:`_keyword_tokens`'s
    ``set`` -- term *frequency* is the whole point of a TF-IDF vector)."""
    latin = [t.lower() for t in _WORD_RE.findall(text or "") if t.lower() not in _STOPWORDS]
    hebrew = list(_HEBREW_WORD_RE.findall(text or ""))
    return latin + hebrew


# --------------------------------------------------------------------------
# round 7 D8 point 2/3/4 (2026-09-06/07, docs/qa/loop/round_6_judge.md D8 worst #10): a curated
# EO/IR technology vocabulary -- assembled from the *English* parts of config/taxonomy.yaml's own
# subdomain labels (e.g. ``detectors_fpa``'s "SWIR/MWIR/LWIR, Uncooled" and ``uav_gimbals``'s
# "EO/IR Gimbals") plus the round-6-judge finding's own worked example list -- so a TF-IDF
# sub-cluster label prefers a genuine EO/IR technology noun over an equally-frequent but generic
# word once one is present among the cluster's own top terms, and prefers a multi-word technical
# phrase ("focal plane array") over either of its own bare component words ("focal", "array") when
# that phrase actually occurs in the source text. Keys are lowercase; a term with no Hebrew entry
# here is displayed in English rather than invented (point 3's "else keep the English term").
# Deliberately NOT sourced by parsing ``config/taxonomy.yaml`` at runtime: this module stays
# DB-free/LLM-free/dependency-free by design (see module docstring), and a curated constant is
# exactly as auditable as a parsed one for a list this size.
# --------------------------------------------------------------------------

_EOIR_VOCAB_TERM_HE: dict[str, str] = {
    "gimbal": "ג'ימבל",
    "gimbals": "ג'ימבלים",
    "thermal": "תרמי",
    "detector": "גלאי",
    "detectors": "גלאים",
    "seeker": "ראש ביות",
    "seekers": "ראשי ביות",
    "tracking": "מעקב",
    "tracker": "עוקב",
    "laser": "לייזר",
    "designator": "מציין מטרה",
    "designators": "מציני מטרה",
    "hyperspectral": "היפרספקטרלי",
    "uncooled": "לא-מקורר",
    "microbolometer": "מיקרו-בולומטר",
    "lidar": "לידאר",
    "infrared": "אינפרא-אדום",
    "pixel": "פיקסל",
    "roic": "ROIC",
    "droic": "DROIC",
    "swir": "SWIR",
    "eswir": "eSWIR",
    "mwir": "MWIR",
    "lwir": "LWIR",
    "atr": "ATR",
    "metasurface": "מטא-משטח",
    "optronic": "אופטרוני",
}

#: Multi-word EO/IR phrases (each a tuple of lowercase words, longest-first so a longer phrase is
#: matched before a shorter one that is its own prefix, e.g. "focal plane array" before "focal
#: plane") -- mirrors :data:`_EOIR_VOCAB_TERM_HE`'s "keep English if no Hebrew entry" rule.
_EOIR_VOCAB_PHRASE_HE: dict[tuple[str, ...], str] = {
    ("focal", "plane", "array"): "מערך מישור מוקד",
    ("focal", "plane"): "מישור מוקד",
    ("quantum", "dot"): "נקודת קוונטית",
    ("quantum", "dots"): "נקודות קוונטיות",
    ("night", "vision"): "ראיית לילה",
    ("beam", "control"): "בקרת אלומה",
    ("super", "resolution"): "רזולוציית-על",
    ("target", "recognition"): "זיהוי מטרות",
    ("sensor", "fusion"): "שילוב חיישנים",
    ("edge", "ai"): "בינה מלאכותית קצה",
}
_EOIR_VOCAB_PHRASES: tuple[tuple[str, ...], ...] = tuple(sorted(_EOIR_VOCAB_PHRASE_HE, key=len, reverse=True))
#: The same phrases as ``"word1 word2"``-joined strings (matching :func:`_phrase_tokens`'s own
#: output shape) -- used by :func:`_term_boost` for membership testing, since a token flowing
#: through a TF-IDF vector is always a joined string, never the tuple key itself.
_EOIR_VOCAB_PHRASE_STRINGS: frozenset[str] = frozenset(" ".join(p) for p in _EOIR_VOCAB_PHRASE_HE)

#: Round 7 D8 point 4: a short CPC-code -> (English title, Hebrew title) table for exactly the
#: codes this project already tracks (``config/patents.yaml``'s own ``cpc:`` list and its trailing
#: comments -- the only "CPC title text" actually present anywhere in this project's own data,
#: since the ``patents`` table stores bare codes with no per-row title column). Used as a better
#: fallback than the bare ``f"אשכול טכנולוגי {code}"`` in :func:`_cpc_label`, and to seed extra
#: term-pool words for a row that does carry one of these CPC codes (point 1's "... + CPC-title
#: text").
_CPC_CODE_TITLES: dict[str, tuple[str, str]] = {
    "G01J5": ("radiation pyrometry infrared detector", "פירומטריית קרינה, גלאי אינפרא-אדום"),
    "G02B23": ("telescope binocular periscope observation", "טלסקופים ומכשירי תצפית"),
    "G02B26": ("optical scanning beam control gimbal", "בקרת קרן אופטית, סריקה"),
    "G02B27": ("optical system thermal sighting head-up", "מערכות אופטיות, כוונות תרמיות"),
    "F41G3": ("aiming fire control weapon", "כיוון וכוונת אש"),
    "F41G7": ("missile guidance seeker", "הנחיית טילים, ראשי ביות"),
    "H04N23": ("camera image sensor thermal imaging", "מצלמות וחיישני תמונה תרמיים"),
    "H01L27": ("semiconductor focal plane array", "רכיבי מוליכים למחצה, מערכי מישור מוקד"),
    "G06V10": ("image pattern recognition target recognition", "זיהוי דפוסים בתמונה"),
    "G06T7": ("image analysis target tracking sensor fusion", "ניתוח תמונה, מעקב מטרות"),
    "G01S17": ("lidar laser rangefinding", "לידאר וטווח-לייזר"),
    "B64D47": ("aircraft observation pod payload", "פודים ומטענים לתצפית ממטוסים"),
}


def _raw_words(text: str) -> list[str]:
    """Sequential (order-preserved, repeats kept) lowercase Latin words from ``text`` -- unlike
    :func:`_subcluster_tokens`/:func:`_keyword_tokens`, stopwords are NOT dropped here, since a
    stopword can sit inside a real multi-word phrase (kept only long enough for
    :func:`_phrase_tokens` to scan word-adjacency; the returned list is never itself fed straight
    into a TF-IDF vector)."""
    return [t.lower() for t in _WORD_RE.findall(text or "")]


def _phrase_tokens(words: list[str]) -> list[str]:
    """Every :data:`_EOIR_VOCAB_PHRASE_HE` phrase that actually occurs (word-adjacent, in order) in
    ``words``, returned as ``"word1 word2"``-joined strings -- one entry per occurrence, so a
    phrase appearing twice in a title+abstract contributes twice to that document's own term
    frequency, same convention as :func:`_subcluster_tokens`'s repeats-kept unigrams."""
    found: list[str] = []
    n = len(words)
    for i in range(n):
        for phrase in _EOIR_VOCAB_PHRASES:
            length = len(phrase)
            if i + length <= n and tuple(words[i : i + length]) == phrase:
                found.append(" ".join(phrase))
    return found


def _term_pool_tokens(row: dict[str, Any]) -> list[str]:
    """Round 7 D8 point 1: the term pool for a TF-IDF sub-cluster, built from ``row``'s own
    title + abstract + (when it carries a known CPC code -- see :data:`_CPC_CODE_TITLES`) that
    code's own English title text -- and NEVER from ``row["assignees"]``. Every token matching a
    word in one of the row's own assignee names, a :data:`_CORPORATE_SUFFIX_STOPWORDS` entry, or a
    :data:`_PATENT_BOILERPLATE_STOPWORDS` entry (patent-administrative scrape noise, e.g.
    "reassignment") is dropped before the vector is ever built -- neither a company-name fragment
    nor paperwork jargon can become a displayed cluster label term. Known EO/IR technology phrases
    (:data:`_EOIR_VOCAB_PHRASE_HE`) are additionally extracted as their own multi-word tokens
    (point 2's "bigrams over unigrams") alongside the ordinary unigrams and any Hebrew words."""
    title = row.get("title") or ""
    abstract = row.get("abstract") or ""
    cpc_title_text = " ".join(
        _CPC_CODE_TITLES[code][0] for code in (row.get("cpc") or []) if code in _CPC_CODE_TITLES
    )
    text = f"{title} {abstract} {cpc_title_text}"
    assignee_tokens = _assignee_name_tokens(row.get("assignees"))
    drop = _STOPWORDS | _CORPORATE_SUFFIX_STOPWORDS | _PATENT_BOILERPLATE_STOPWORDS | assignee_tokens
    words = _raw_words(text)
    unigrams = [w for w in words if w not in drop]
    phrases = _phrase_tokens(words)
    hebrew = list(_HEBREW_WORD_RE.findall(text))
    return unigrams + phrases + hebrew


def _term_boost(term: str) -> float:
    """Round 7 D8 point 2 ("prefer technology nouns... and bigrams over unigrams"): a multiplier
    applied to a term's own TF-IDF weight purely for *label-term selection* (never for the
    similarity/clustering math itself, which stays untouched) -- a known EO/IR phrase scores
    highest, a known EO/IR unigram next, and every other term (still real title/abstract text --
    just not in the curated vocabulary) keeps its own plain weight."""
    if " " in term:
        return 3.0 if term in _EOIR_VOCAB_PHRASE_STRINGS else 1.0
    return 2.0 if term in _EOIR_VOCAB_TERM_HE else 1.0


def _boosted_top_terms(vec: dict[str, float], n: int) -> list[str]:
    """Same contract as :func:`_top_terms` (deterministic: ties broken alphabetically by term) but
    ranks by ``weight * _term_boost(term)`` instead of the bare TF-IDF weight, so a real EO/IR
    technology term or phrase wins a label slot over an equally- (or even somewhat more-) frequent
    generic word once both survived :func:`_term_pool_tokens`'s stoplist filtering."""
    return [
        term
        for term, _weight in sorted(vec.items(), key=lambda kv: (-(kv[1] * _term_boost(kv[0])), kv[0]))[:n]
    ]


def _tfidf_vectors(token_lists: list[list[str]]) -> list[dict[str, float]]:
    """Classic tf (raw in-document count) x idf (smoothed log(N/df) + 1, always positive) weight
    per term per document, over ``token_lists`` -- ``N`` is ``len(token_lists)``. A term unique to
    one document scores highest; a term shared by every document scores near its raw tf alone
    (idf -> 1). Pure Python, no external numerical dependency."""
    n = len(token_lists)
    df: Counter[str] = Counter()
    for tokens in token_lists:
        for t in set(tokens):
            df[t] += 1
    vectors: list[dict[str, float]] = []
    for tokens in token_lists:
        tf = Counter(tokens)
        vectors.append(
            {term: count * (math.log((n + 1) / (df[term] + 1)) + 1.0) for term, count in tf.items()}
        )
    return vectors


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(weight * b[term] for term, weight in a.items() if term in b)
    if not dot:
        return 0.0
    norm_a = math.sqrt(sum(w * w for w in a.values()))
    norm_b = math.sqrt(sum(w * w for w in b.values()))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _average_vector(vectors: list[dict[str, float]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for vec in vectors:
        for term, weight in vec.items():
            out[term] += weight
    n = len(vectors) or 1
    return {term: weight / n for term, weight in out.items()}


def _top_terms(vec: dict[str, float], n: int) -> list[str]:
    return [term for term, _weight in sorted(vec.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]


def tfidf_subcluster_unclassified(
    rows: list[dict[str, Any]],
    *,
    max_clusters: int = UNCLASSIFIED_MAX_SUBCLUSTERS,
    similarity_threshold: float = _UNCLASSIFIED_SIMILARITY_THRESHOLD,
) -> list[dict[str, Any]]:
    """TF-IDF-lite sub-clustering (round 5 D8 point 3) of patents that carry no CPC code and
    matched no configured watch topic -- title/abstract term-similarity grouping into at most
    ``max_clusters`` sub-clusters, instead of one flat "לא מסווג" bucket. Greedy single-pass and
    fully deterministic (input order preserved, ties broken alphabetically by term): each row joins
    the most-similar existing sub-cluster centroid when that similarity clears
    ``similarity_threshold`` OR ``max_clusters`` has already been reached (so the bucket count never
    exceeds the cap); otherwise it opens a new sub-cluster of its own. A row with no title/abstract
    tokens at all (an empty vector, similarity 0.0 to everything) still lands somewhere rather than
    being dropped -- ``docs/CONVENTIONS.md`` rule 5, never silently discard a patent from its own
    survey. Returns ``[]`` for an empty ``rows``; each returned item is
    ``{"patent_ns": [...], "patent_ids": [...], "label_terms": [...]}`` (``label_terms`` -- see
    :func:`_boosted_top_terms` -- is ``[]`` only when every row in that sub-cluster had no tokens
    at all)."""
    if not rows:
        return []
    vectors = _tfidf_vectors([_term_pool_tokens(r) for r in rows])
    clusters: list[dict[str, Any]] = []
    for row, vec in zip(rows, vectors, strict=True):
        best_idx: int | None = None
        best_sim = -1.0
        for i, c in enumerate(clusters):
            sim = _cosine_similarity(vec, c["centroid"])
            if sim > best_sim:
                best_idx, best_sim = i, sim
        if best_idx is not None and (best_sim >= similarity_threshold or len(clusters) >= max_clusters):
            c = clusters[best_idx]
            c["rows"].append(row)
            c["vectors"].append(vec)
            c["centroid"] = _average_vector(c["vectors"])
        else:
            clusters.append({"rows": [row], "vectors": [vec], "centroid": dict(vec)})
    return [
        {
            "patent_ns": [r["n"] for r in c["rows"]],
            "patent_ids": [r.get("id") for r in c["rows"]],
            "label_terms": _boosted_top_terms(c["centroid"], _UNCLASSIFIED_LABEL_TOP_TERMS),
        }
        for c in clusters
    ]


#: Round 6 D8 finding 2 (2026-09-06/07, docs/qa/loop/round_5_judge.md D8): the deterministic
#: no-unclassified-cluster check (``eoa.qa.d8_patent_survey._no_unclassified_cluster_check``)
#: matches the literal "לא מסווג" ("unclassified") anywhere in a cluster heading or its body --
#: including the old "לא מסווג: sensor / drone" form, which still opened with that exact word. A
#: sub-cluster that DOES have real top terms should be named by them outright rather than prefixed
#: with a word meaning "unclassified" it no longer is; :data:`UNCLASSIFIED_LABEL_HE` stays reserved
#: for the one genuinely nameless case below.
_TERM_CLUSTER_LABEL_PREFIX_HE = "אשכול נושאי"


def _is_hebrew_text(term: str) -> bool:
    return bool(_HEBREW_WORD_RE.search(term))


def _display_term_he(term: str) -> str:
    """Round 7 D8 point 3: map a label term to its Hebrew equivalent when the curated EO/IR
    vocabulary (:data:`_EOIR_VOCAB_TERM_HE`/:data:`_EOIR_VOCAB_PHRASE_HE`) has one, otherwise keep
    the term exactly as given -- a term that is already Hebrew (from the tokenizer's own Hebrew
    capture) passes through unchanged either way since it matches neither dict's (Latin) keys."""
    key = tuple(term.split(" ")) if " " in term else term
    if isinstance(key, tuple):
        return _EOIR_VOCAB_PHRASE_HE.get(key, term)
    return _EOIR_VOCAB_TERM_HE.get(key, term)


def _unclassified_label_he(label_terms: list[str]) -> str:
    """A genuinely descriptive Hebrew label built from a TF-IDF sub-cluster's own top terms --
    e.g. "אשכול נושאי: גלאים / focal plane array / counter" -- never the literal
    :data:`UNCLASSIFIED_LABEL_HE` once there is at least one real term to name the cluster by
    (round 6 D8 finding 2: the old "לא מסווג: <terms>" form still tripped the deterministic
    no-unclassified-cluster check on its literal prefix, and read as a dead end to a human despite
    genuinely having real terms to show). Round 7 D8 point 3: each term is mapped through the
    curated EO/IR vocabulary to Hebrew where one exists (:func:`_display_term_he`), else kept in
    English rather than invented; the displayed terms are then ordered Hebrew-first (native Hebrew
    tokens and vocabulary-translated terms before any term left in English), per the round-7 spec's
    "up to 3 terms, Hebrew first" label shape. Falls back to :data:`UNCLASSIFIED_LABEL_HE` only when
    ``label_terms`` is empty -- a sub-cluster whose patents carry no title/abstract tokens at all
    has nothing honest to derive a name from."""
    if not label_terms:
        return UNCLASSIFIED_LABEL_HE
    displayed = [_display_term_he(t) for t in label_terms]
    hebrew_first = [t for t in displayed if _is_hebrew_text(t)]
    rest = [t for t in displayed if not _is_hebrew_text(t)]
    return f"{_TERM_CLUSTER_LABEL_PREFIX_HE}: " + " / ".join(hebrew_first + rest)


def _cpc_label(code: str, topics: list[_TopicLike]) -> str:
    for topic in topics:
        if code in (topic.cpc or []):
            return topic.name_he
    known = _CPC_CODE_TITLES.get(code)
    if known:
        return f"אשכול טכנולוגי: {known[1]} ({code})"
    return f"אשכול טכנולוגי {code}"


def _keyword_cluster(
    title: str, abstract: str, topic_index: list[tuple[_TopicLike, set[str]]], *, min_overlap: int = 2
) -> tuple[str, str] | None:
    tokens = _keyword_tokens(f"{title or ''} {abstract or ''}")
    if not tokens:
        return None
    best_topic: _TopicLike | None = None
    best_score = 0
    for topic, kw in topic_index:
        score = len(tokens & kw)
        if score > best_score:
            best_score = score
            best_topic = topic
    if best_topic is not None and best_score >= min_overlap:
        topic_cpc = best_topic.cpc or []
        key = topic_cpc[0] if topic_cpc else f"KW:{best_topic.name_he}"
        return key, best_topic.name_he
    return None


@dataclass
class PatentCluster:
    key: str
    label_he: str
    patent_ns: list[int] = field(default_factory=list)
    patent_ids: list[Any] = field(default_factory=list)
    cpc_codes: list[str] = field(default_factory=list)
    assignees: Counter[str] = field(default_factory=Counter)
    years: Counter[int] = field(default_factory=Counter)
    granted: int = 0
    total: int = 0

    @property
    def size(self) -> int:
        return len(self.patent_ns)

    @property
    def dominant_assignees(self) -> list[str]:
        return [a for a, _n in self.assignees.most_common(3)]

    @property
    def grant_ratio(self) -> float:
        return (self.granted / self.total) if self.total else 0.0

    @property
    def filing_velocity_per_year(self) -> float:
        """Mean filings/publications per year across the cluster's own observed year span
        (inclusive) -- 0.0 when fewer than one distinct year is known."""
        if not self.years:
            return 0.0
        span = max(self.years) - min(self.years) + 1
        return sum(self.years.values()) / span

    @property
    def maturity_label_he(self) -> str:
        """A coarse maturity proxy from the grant ratio -- a real legal-status determination needs
        a patent attorney; this is a landscape hint only."""
        ratio = self.grant_ratio
        if self.total == 0:
            return "—"
        if ratio >= 0.6:
            return "בשל (רוב הענקות)"
        if ratio >= 0.2:
            return "מתבגר (הענקות חלקיות)"
        return "מוקדם (רוב בבחינה)"


def _accumulate_into_cluster(cluster: PatentCluster, row: dict[str, Any]) -> None:
    cluster.patent_ns.append(row["n"])
    cluster.patent_ids.append(row.get("id"))
    for c in row.get("cpc") or []:
        if c not in cluster.cpc_codes:
            cluster.cpc_codes.append(c)
    for a in row.get("assignees") or []:
        if a:
            cluster.assignees[a] += 1
    pub = row.get("publication_date")
    if pub:
        cluster.years[pub.year] += 1
    cluster.total += 1
    if row.get("grant_date"):
        cluster.granted += 1


def cluster_patents(
    registry_rows: list[dict[str, Any]], *, topics: list[_TopicLike] | None = None
) -> list[PatentCluster]:
    """Group every patent registry row (each carrying at least ``n``/``id``/``cpc``/``assignees``/
    ``title``/``abstract``/``publication_date``/``grant_date``) into :class:`PatentCluster`\\ s,
    sorted largest-first. See the module docstring for the clustering method -- a row with neither a
    CPC code nor a watch-topic keyword match is set aside and, once every row has been placed,
    sub-clustered by :func:`tfidf_subcluster_unclassified` instead of joining one flat "לא מסווג"
    bucket (round 5 D8 point 3)."""
    topic_index = _topic_keyword_index(topics or [])
    clusters: dict[str, PatentCluster] = {}
    unclassified_rows: list[dict[str, Any]] = []
    for row in registry_rows:
        cpc_list = row.get("cpc") or []
        if cpc_list:
            key = cpc_list[0]
            label = _cpc_label(key, topics or [])
        else:
            kw = _keyword_cluster(row.get("title") or "", row.get("abstract") or "", topic_index)
            if kw is None:
                unclassified_rows.append(row)
                continue
            key, label = kw
        cluster = clusters.setdefault(key, PatentCluster(key=key, label_he=label))
        _accumulate_into_cluster(cluster, row)
    if unclassified_rows:
        by_n = {row["n"]: row for row in unclassified_rows}
        subclusters = tfidf_subcluster_unclassified(unclassified_rows)
        multi = len(subclusters) > 1
        for i, sub in enumerate(subclusters):
            key = f"{UNCLASSIFIED_KEY}_{i}" if multi else UNCLASSIFIED_KEY
            # Round 6 D8 finding 2: always name the sub-cluster by its own top terms when it has
            # any (never gated on `multi` -- a *single* leftover sub-cluster with real terms is
            # exactly as nameable as one of several) -- see _unclassified_label_he's own docstring.
            label = _unclassified_label_he(sub["label_terms"])
            cluster = clusters.setdefault(key, PatentCluster(key=key, label_he=label))
            for n in sub["patent_ns"]:
                _accumulate_into_cluster(cluster, by_n[n])
    return sorted(clusters.values(), key=lambda c: -c.size)


def cross_cluster_links(clusters: list[PatentCluster]) -> list[tuple[str, str, list[str]]]:
    """``(cluster_a_label, cluster_b_label, shared_assignee_names)`` for every pair of clusters
    that share at least one real assignee -- the cross-link signal available without a real
    citation graph (see module docstring)."""
    links: list[tuple[str, str, list[str]]] = []
    for i, a in enumerate(clusters):
        for b in clusters[i + 1 :]:
            shared = sorted(set(a.assignees) & set(b.assignees))
            if shared:
                links.append((a.label_he, b.label_he, shared))
    return links


# --------------------------------------------------------------------------
# business relationships (point 3)
# --------------------------------------------------------------------------


def co_assignment_pairs(rows: list[dict[str, Any]]) -> Counter[tuple[str, str]]:
    """``{(assignee_a, assignee_b): shared_patent_count}`` for every pair of distinct real
    assignees that co-appear on at least one patent (alphabetically ordered pair, so ``(A, B)`` and
    ``(B, A)`` never both appear)."""
    counter: Counter[tuple[str, str]] = Counter()
    for row in rows:
        names = sorted({a for a in (row.get("assignees") or []) if a})
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                counter[(names[i], names[j])] += 1
    return counter


def same_family_groups(registry_rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    """``{family_id: [n, ...]}`` for every ``family_id`` shared by 2+ registry rows -- a real
    patent-family relationship (not a citation, see module docstring)."""
    groups: dict[str, list[int]] = defaultdict(list)
    for row in registry_rows:
        fam = row.get("family_id")
        if fam:
            groups[fam].append(row["n"])
    return {k: v for k, v in groups.items() if len(v) > 1}


def relationship_edges_from_events(assignee_name: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Supplier/integrator/customer-chain edges out of ``assignee_name`` inferred from its own
    (already-fetched) ``events`` rows: an event's ``customer`` field, and any other named
    ``parties``, become a directed edge ``assignee_name -> other_party`` labelled by the event kind
    and citing the event's own registry number (``n``, set by
    ``eoa.patents.survey._extend_registry_with_db_records`` before this is called)."""
    edges: list[dict[str, Any]] = []
    for ev in events:
        others = set()
        customer = ev.get("customer")
        if customer:
            others.add(customer)
        others.update(p for p in (ev.get("parties") or []) if p)
        others.discard(assignee_name)
        for other in sorted(others):
            edges.append(
                {
                    "from": assignee_name,
                    "to": other,
                    "kind": ev.get("kind"),
                    "n": ev.get("n"),
                    "program": ev.get("program"),
                    "title": ev.get("title"),
                }
            )
    return edges


# --------------------------------------------------------------------------
# consistency check (point 6: reject "אין פטנטים של X" when X actually has patents)
# --------------------------------------------------------------------------

_NEGATION_RE = re.compile(
    r"(?:אין|לא\s+נרשמו|לא\s+נמצאו|לא\s+קיימים|לא\s+קיים)\s+(?:עוד\s+)?פטנטים?\b"
    r"[^.!?]{0,20}?(?:של|מ-|עבור|הרשומים\s+על\s+שם)\s+"
    r"([A-Za-z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})"
)


def _name_matches(known_name: str, candidate: str) -> bool:
    known = known_name.lower().strip()
    cand = candidate.lower().strip()
    return bool(known) and bool(cand) and (known in cand or cand in known)


def consistency_violations(text: str, assignee_counts: dict[str, int]) -> list[str]:
    """Scan ``text`` for a Hebrew "no patents of <company>" negation naming a company that
    ``assignee_counts`` (the survey's own deterministic per-assignee counts, computed straight off
    the appendix rows -- never the LLM's own claim) shows actually has 1+ patents on record.
    Returns one human-readable violation message per match found; an empty list means the text is
    consistent with the deterministic counts (or contains no such negation at all)."""
    violations: list[str] = []
    for m in _NEGATION_RE.finditer(text or ""):
        candidate = m.group(1).strip().rstrip(".,;:")
        for name, count in assignee_counts.items():
            if count > 0 and name and _name_matches(name, candidate):
                violations.append(
                    f'הטענה "{m.group(0)}" סותרת את הנספח: יש {count} פטנט(ים) רשומים של {name}.'
                )
    return violations
