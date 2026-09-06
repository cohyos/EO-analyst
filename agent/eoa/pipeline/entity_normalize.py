"""Q3-13 (docs/qa/findings_Q3_r1.md): entity name/kind normalisation and watchlist alias
resolution, shared by:

- ``eoa.memory.relational.upsert_entity`` -- applied to every entity write going forward
  (classify's extracted entities, analyze's graph-edge endpoints, entity_relevance's kind
  reclassification, ...), so a duplicate spelling/case/alias of a watchlist company always lands
  on the same canonical ``entities`` row instead of a near-duplicate one.
- ``scripts/repair_entities_normalize.py`` -- the one-off backfill over every existing row.
- ``eoa.pipeline.analyze`` (Q3-8) -- ``find_watchlist_aliases_in_text`` deterministically fills
  ``items.entities_mentioned`` when the LLM returned an empty list but the item text plainly
  names a watchlist company/program.

Nothing here talks to the database; it is pure text/config lookups over ``config/watchlist.yaml``
(via ``eoa.config.settings()``), safe to import from anywhere (including at module scope) without
a live DB connection.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from eoa.config import settings

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

# The `entities` table's actual CHECK constraint -- the only kinds that can ever be written.
# migration 0001_core.py originally allowed company/program/system/person/org only; migration
# 0007_entity_relevance.py later widened it to also allow "country" (matching
# `ClassifyOut.EntityMention.kind`, which had allowed "country" as a schema value since it was
# introduced, before the DB constraint caught up) -- verified live against the constraint
# (`pg_get_constraintdef` on `entities_kind_check`).
VALID_ENTITY_KINDS = frozenset({"company", "program", "system", "person", "org", "country"})

_SYSTEM_DESIGNATION_KEYWORDS = frozenset(
    {"locust", "smash", "anvil", "roadrunner", "titan", "leonidas", "hivemind"}
)
_GOVERNMENT_KEYWORDS = (
    "air force", "army", "navy", "ministry", "department", "command", "agency", "nato",
    "dod", "government", "govt", "ministry of defense", "ministry of defence",
)  # fmt: skip

# Technique/algorithm-like names an LLM sometimes extracts as if they were an "entity" -- these
# describe a method, not a company/program/system/person/org, and should never be stored. Kept
# as an explicit literal list (in addition to the generic patterns below it) so an exact known
# phrase is always caught even if it doesn't happen to end in one of _TECHNIQUE_SUFFIX_RE's nouns.
_TECHNIQUE_LIKE_RE = re.compile(
    r"\b("
    r"image captioning|object detection|vehicle detector[s]?|edge detection|"
    r"feature extraction|semantic segmentation|pose estimation|super[- ]resolution|"
    r"anomaly detection|change detection|target tracking algorithm"
    r")\b",
    re.IGNORECASE,
)

# Q3-13 r4 (2026-09-06 root-cause investigation): the literal list above only ever caught the
# handful of exact phrases spelled in it -- a live LLM re-analyze backfill created junk entities
# it didn't cover: "camouflaged military vehicle detection" (kind=company, item 156; the singular
# "vehicle detection" wasn't in the literal list, only the plural "vehicle detector[s]?"), "GenAI
# image editing" (entity 1009). This generalises to "<one to five words> <technique noun>[s]"
# (matched as a substring, same as the literal list above, so "RF-DETR vehicle detectors" still
# matches via its "vehicle detectors" tail) and "<GenAI/LLM/deep-learning prefix> <anything>".
_TECHNIQUE_SUFFIX_RE = re.compile(
    r"\b[a-zA-Z][\w'-]*(?:[\s-]+[a-zA-Z][\w'-]*){0,4}[\s-]+"
    r"(?:detection|detector|detectors|segmentation|estimation|extraction|synthesis|"
    r"translation|recognition|tracking|classification|captioning|editing|generation|"
    r"fusion|calibration|registration)s?\b",
    re.IGNORECASE,
)
_TECHNIQUE_PREFIX_RE = re.compile(
    r"\b(?:GenAI|generative\s+AI|LLM|deep\s+learning|neural\s+network)s?\b[\s:-]+\S",
    re.IGNORECASE,
)

# Q3-13 r4: a lowercase-starting, multi-word name that also contains one of these generic-concept
# keywords (substring match on the casefolded phrase) -- both signals required, not just the
# lowercase-start shape alone. An earlier version of this gate rejected *any* lowercase-starting
# multi-word phrase with no Title-Case token, but that false-positived on a perfectly ordinary
# company name typed in lowercase (e.g. a case-insensitive-dedup scenario: "acme corp" re-extracted
# for an existing "Acme Corp" row) -- ``is_technique_like`` is a pure function with no DB access,
# so it cannot itself check "does a case-insensitively-matching row already exist" the way
# ``eoa.memory.relational.upsert_entity`` does downstream. Requiring a keyword keeps the false-
# positive rate near zero while still catching every lowercase-start junk row observed live
# (2026-09-06) in the whole `entities` table: "potential suppliers", "target behaviors",
# "proxy-guided placement", "transformer-based architectures", "existing C-UAS approaches",
# "future uncrewed ground vehicles (UGVs)", "binary visual question answering", "defense-tech
# startups", "software developers", "hardware engineers", "anti-satellite weapons", "global
# satellite market", "space defense needs", "international customer", "commercial off-the-shelf
# components", "potential technologies", "targeted grayscale patch attacks" -- while a single-word
# stylised brand ("arXiv", "ePlane", "e200X", "exMHR") never matches (the multi-word requirement)
# and a real two-word name never matches unless it happens to also contain one of these keywords.
_LOWERCASE_MULTIWORD_RE = re.compile(r"^[a-z][\w'-]*(?:\s+\S+)+$")
_GENERIC_ENGLISH_KEYWORDS: tuple[str, ...] = (
    "startup", "developer", "engineer", "weapon", "market", "customer", "supplier",
    "technolog", "behavior", "placement", "architecture", "approach", "capabilit",
    "attack", "answering", "industry", "component", " need", "dataset", "threat",
    "vehicle",
)  # fmt: skip


def _is_lowercase_multiword_junk(name: str) -> bool:
    stripped = (name or "").strip()
    if not _LOWERCASE_MULTIWORD_RE.match(stripped):
        return False
    lowered = stripped.lower()
    return any(kw in lowered for kw in _GENERIC_ENGLISH_KEYWORDS)


def normalize_name_key(name: str) -> str:
    """Case-insensitive, punctuation-stripped comparison key for an entity name/alias --
    ``'Elbit Systems UK'`` and ``"Elbit-Systems  uk"`` both normalize to the same key."""
    if not name:
        return ""
    stripped = _PUNCT_RE.sub(" ", name)
    collapsed = _WS_RE.sub(" ", stripped).strip()
    return collapsed.casefold()


@lru_cache(maxsize=1)
def _alias_index() -> dict[str, dict[str, Any]]:
    """``{normalize_name_key(surface) -> canonical record}`` for every watchlist company/program,
    keyed by both its canonical name and every alias. ``lru_cache`` is safe here: ``watchlist.yaml``
    is loaded once per process via ``eoa.config.settings()`` (itself ``lru_cache``d); tests that
    monkeypatch the watchlist should also clear this cache (``_alias_index.cache_clear()``)."""
    wl = settings().watchlist
    index: dict[str, dict[str, Any]] = {}
    for kind, records in (
        ("company", wl.get("companies", []) or []),
        ("program", wl.get("programs", []) or []),
    ):
        for rec in records:
            canonical = {
                "name": rec.get("name", ""),
                "kind": kind,
                "country": rec.get("country"),
                "aliases": list(rec.get("aliases") or []),
                "focus": list(rec.get("focus") or []),
            }
            for surface in [rec.get("name", ""), *canonical["aliases"]]:
                key = normalize_name_key(surface)
                if key:
                    index[key] = canonical
    return index


@lru_cache(maxsize=1)
def _surface_to_canonical_name() -> list[tuple[str, str]]:
    """``[(surface string, canonical name), ...]`` for regex text search (Q3-8), longest surface
    first so e.g. "Elbit Systems" is tried before a shorter alias substring of it would be. Q3-13
    r3: includes the curated org table (below) as well as the watchlist, so a deep-search/analyze
    text mention of e.g. "US Army"/"IDF"/"NATO" also deterministically backfills
    ``entities_mentioned``, not just watchlist companies/programs."""
    pairs: list[tuple[str, str]] = []
    seen_surfaces: set[str] = set()
    for record in {**_alias_index(), **_curated_org_index()}.values():
        for surface in [record["name"], *record["aliases"]]:
            if surface and surface not in seen_surfaces:
                pairs.append((surface, record["name"]))
                seen_surfaces.add(surface)
    return sorted(pairs, key=lambda p: -len(p[0]))


def resolve_canonical(name: str) -> dict[str, Any] | None:
    """The canonical record (name/kind/country/aliases/focus) for ``name`` if it (or a recorded
    alias of it) is on the watchlist or the curated defense-org table (Q3-13 r3, below) -- else
    ``None``. The watchlist is checked first, so a name that happens to collide with both (none do
    today) would resolve to the watchlist's own kind (company/program)."""
    if not name:
        return None
    key = normalize_name_key(name)
    return _alias_index().get(key) or _curated_org_index().get(key)


def is_technique_like(name: str) -> bool:
    """True for a method/technique/algorithm name (not a real named entity) that should be
    rejected rather than stored -- e.g. "image captioning", "RF-DETR vehicle detectors",
    "vehicle detection" (Q3-13 r4: no longer needs to be spelled out in the literal list),
    "GenAI image editing", or a lowercase-starting multi-word descriptive phrase with no
    proper-noun token ("potential suppliers", "transformer-based architectures").

    Checked *after* the watchlist/curated-org/country/system-designation resolution (mirroring
    :func:`is_generic_non_entity`'s own guard) so a real recognised entity is never rejected on a
    substring/shape coincidence -- this is what lets "Iron Beam", "Drone Dome", "Sniper ATP",
    "LITENING" (all real watchlist aliases) survive even though the patterns below are otherwise
    quite permissive.
    """
    if not name or not name.strip():
        return False
    if resolve_canonical(name) or resolve_country_name(name) or _is_system_designation(name):
        return False
    if _TECHNIQUE_LIKE_RE.search(name):
        return True
    if _TECHNIQUE_SUFFIX_RE.search(name):
        return True
    if _TECHNIQUE_PREFIX_RE.search(name):
        return True
    return _is_lowercase_multiword_junk(name)


# Q3-13 r4: publication venues / aggregators / media platforms an LLM sometimes extracts as if
# they were the *subject* of an item rather than where it was found -- observed live: "arXiv"
# stored with kind='person' (entity 132). None of these are ever a real person/company/org, and
# the `entities` table has no separate 'source' kind (VALID_ENTITY_KINDS) to type them under
# instead, so they are rejected outright by :func:`is_junk_entity` rather than stored under a
# misleading kind.
_SOURCE_LIKE_NAMES = frozenset(
    {"arxiv", "ieee", "spie", "nature", "reddit", "wikipedia", "youtube", "google scholar"}
)


def is_source_like_name(name: str) -> bool:
    """True when ``name`` denotes a publication venue/aggregator/media platform (:data:`
    _SOURCE_LIKE_NAMES`) rather than a market participant -- the bare name ("arXiv") or a
    leading-source-name category tag ("arXiv cs.CV", "IEEE Xplore")."""
    key = normalize_name_key(name)
    if not key:
        return False
    if key in _SOURCE_LIKE_NAMES:
        return True
    return key.split(" ", 1)[0] in _SOURCE_LIKE_NAMES


def normalize_kind(name: str, kind: str) -> str:
    """Map a raw ``kind`` onto one of the ``entities`` table's actually-allowed values
    (:data:`VALID_ENTITY_KINDS`, which -- since migration 0007 -- does include "country"). A
    watchlist-recognised name uses the watchlist's own kind unless the caller already supplied a
    more specific valid kind ("system"/"program"); otherwise: known weapon/system designations ->
    "system", government/military bodies -> "org" (even when the caller passed "country" for one
    -- "US Air Force" is a government body, not a country), anything else already valid
    (including a genuine "country" like "Israel") is kept, anything unknown defaults to "org"
    (never silently drops the row).

    A known system/weapon designation (e.g. "LOCUST", "SMASH") wins even when that same string
    also happens to be listed as a watchlist company's alias (watchlist aliases mix true
    alternate-spellings of the company itself with the company's own product/program names, used
    there purely for search-relevance matching) -- the *name itself* denotes a system, not the
    company, so it must not be typed (or, in :func:`canonical_name_and_kind`, renamed) as if it
    were just another spelling of the company."""
    if _is_system_designation(name):
        return "system"
    canonical = resolve_canonical(name)
    if canonical and canonical["kind"] in VALID_ENTITY_KINDS:
        if kind in ("system", "program"):
            return kind
        return canonical["kind"]
    if resolve_country_name(name):
        # Q3-13 r3: a genuine country name (HE or EN) is always "country", even when the caller
        # (or a stale existing row) had it typed "company" -- "איראן"/"ארצות הברית"/"יוון" are
        # countries, not companies.
        return "country"
    if any(kw in (name or "").lower() for kw in _GOVERNMENT_KEYWORDS):
        return "org"
    if kind in VALID_ENTITY_KINDS:
        return kind
    return "org"


def _is_system_designation(name: str) -> bool:
    return any(kw in (name or "").lower() for kw in _SYSTEM_DESIGNATION_KEYWORDS)


def canonical_name_and_kind(name: str, kind: str) -> tuple[str, str]:
    """Resolve ``(name, kind)`` to their canonical forms: a watchlist-recognised alias maps to the
    watchlist's canonical name (e.g. "Elbit Systems" -> "Elbit"); ``kind`` is normalised via
    :func:`normalize_kind`. A known system/weapon designation (see :func:`normalize_kind`'s note)
    keeps its own name rather than being folded into the company it happens to be aliased under.
    Names not on the watchlist are returned unchanged (case-insensitive de-duplication against the
    DB happens separately, in ``upsert_entity`` itself)."""
    if _is_system_designation(name):
        return name, "system"
    canonical = resolve_canonical(name)
    if canonical:
        return canonical["name"], normalize_kind(name, kind)
    country_name = resolve_country_name(name)
    if country_name:
        # Q3-13 r3: canonicalise a Hebrew country name onto its English display name so e.g.
        # "יפן" and "Japan" (or "ארה\"ב" and "ארצות הברית") land on the very same entity row.
        return country_name, "country"
    return name, normalize_kind(name, kind)


# --------------------------------------------------------------------------
# Q3-13 r3 (docs/qa/findings_Q3_r2.md): curated Hebrew<->English alias table for the most common
# defense actors/agencies/services that are *not* EO/IR companies/programs (so don't belong in
# `config/watchlist.yaml`, which tracks watchlist-relevance for search/NER of the domain's own
# vendor landscape) -- government/military bodies. Every record resolves to `kind="org"`. This is
# what actually merges "צבא ארה\"ב" / "צבא ארצות הברית" (US Army), "הצי האמריקאי" / "חיל הים
# האמריקאי" (US Navy), etc. into one canonical entity going forward and in the repair script.
_CURATED_ORG_RECORDS: list[dict[str, Any]] = [
    {
        "name": "US Army",
        "country": "US",
        "aliases": ["U.S. Army", "United States Army", 'צבא ארה"ב', "צבא ארצות הברית", "הצבא האמריקאי"],
    },
    {
        "name": "US Navy",
        "country": "US",
        "aliases": ["U.S. Navy", "United States Navy", "חיל הים האמריקאי", "הצי האמריקאי"],
    },
    {
        "name": "US Air Force",
        "country": "US",
        "aliases": ["USAF", "U.S. Air Force", "United States Air Force", "חיל האוויר האמריקאי"],
    },
    {
        "name": "US Marine Corps",
        "country": "US",
        "aliases": ["USMC", "U.S. Marine Corps", "United States Marine Corps", "חיל הנחתים האמריקאי"],
    },
    {
        "name": "US Space Force",
        "country": "US",
        "aliases": ["USSF", "United States Space Force", "חיל החלל האמריקאי"],
    },
    {
        "name": "US Coast Guard",
        "country": "US",
        "aliases": ["USCG", "United States Coast Guard", "משמר החופים האמריקאי"],
    },
    {"name": "US National Guard", "country": "US", "aliases": ["National Guard", "המשמר הלאומי האמריקאי"]},
    {
        "name": "US Department of Defense",
        "country": "US",
        "aliases": [
            "DoD",
            "U.S. DoD",
            "Pentagon",
            "The Pentagon",
            "משרד ההגנה האמריקאי",
            'משרד ההגנה של ארה"ב',
            "הפנטגון",
        ],
    },
    {"name": "DIU", "country": "US", "aliases": ["Defense Innovation Unit"]},
    {"name": "DARPA", "country": "US", "aliases": ["Defense Advanced Research Projects Agency"]},
    {
        "name": "DHS",
        "country": "US",
        "aliases": ["Department of Homeland Security", "משרד הביטחון הפנים האמריקאי"],
    },
    {"name": "DIA", "country": "US", "aliases": ["Defense Intelligence Agency"]},
    {"name": "CIA", "country": "US", "aliases": ["Central Intelligence Agency"]},
    {"name": "FBI", "country": "US", "aliases": ["Federal Bureau of Investigation"]},
    {"name": "IDF", "country": "IL", "aliases": ["Israel Defense Forces", 'צה"ל', "צבא ההגנה לישראל"]},
    {
        "name": "Israeli Ministry of Defense",
        "country": "IL",
        "aliases": ["MoD Israel", "משרד הביטחון", "משרד הביטחון הישראלי"],
    },
    {"name": "Government of Israel", "country": "IL", "aliases": ["ממשלת ישראל"]},
    {"name": "Mossad", "country": "IL", "aliases": ["המוסד"]},
    {"name": "Shin Bet", "country": "IL", "aliases": ['שב"כ', "שירות הביטחון הכללי"]},
    {"name": "NATO", "country": None, "aliases": ["North Atlantic Treaty Organization", 'נאט"ו']},
    {"name": "NSPA", "country": None, "aliases": ["NATO Support and Procurement Agency"]},
    {"name": "Bundeswehr", "country": "DE", "aliases": ["German Armed Forces", "הצבא הגרמני"]},
    {"name": "German Federal Ministry of Defence", "country": "DE", "aliases": ["BMVg", "משרד ההגנה הגרמני"]},
    {"name": "French Armed Forces", "country": "FR", "aliases": ["Armée française", "הצבא הצרפתי"]},
    {
        "name": "UK Ministry of Defence",
        "country": "UK",
        "aliases": ["MoD UK", "British Ministry of Defence", "משרד ההגנה הבריטי"],
    },
    {"name": "Royal Navy", "country": "UK", "aliases": ["הצי המלכותי הבריטי"]},
    {"name": "Royal Air Force", "country": "UK", "aliases": ["RAF", "חיל האוויר המלכותי הבריטי"]},
    {"name": "British Army", "country": "UK", "aliases": ["הצבא הבריטי"]},
    {
        "name": "Ukrainian Armed Forces",
        "country": "UA",
        "aliases": ["Armed Forces of Ukraine", "הצבא האוקראיני", "הכוחות המזוינים של אוקראינה"],
    },
    {"name": "Russian Armed Forces", "country": "RU", "aliases": ["הצבא הרוסי"]},
    {"name": "Japan Self-Defense Forces", "country": "JP", "aliases": ["JSDF", "כוחות ההגנה העצמית של יפן"]},
    {
        "name": "South Korean Armed Forces",
        "country": "KR",
        "aliases": ["ROK Armed Forces", "הצבא הדרום קוריאני"],
    },
    {"name": "Taiwan Armed Forces", "country": "TW", "aliases": ["הצבא הטייוואני"]},
    {"name": "Indian Ministry of Defence", "country": "IN", "aliases": ["MoD India", "משרד ההגנה ההודי"]},
    {"name": "Indian Armed Forces", "country": "IN", "aliases": ["הצבא ההודי"]},
    {"name": "Saudi Ministry of Defense", "country": "SA", "aliases": ["משרד ההגנה הסעודי"]},
    {"name": "UAE Armed Forces", "country": "AE", "aliases": ["הכוחות המזוינים של האמירויות"]},
    {"name": "Turkish Armed Forces", "country": "TR", "aliases": ["TSK", "הצבא הטורקי"]},
    {"name": "Polish Armed Forces", "country": "PL", "aliases": ["הצבא הפולני"]},
    {"name": "Australian Defence Force", "country": "AU", "aliases": ["ADF", "כוחות ההגנה האוסטרליים"]},
    {"name": "Canadian Armed Forces", "country": "CA", "aliases": ["הכוחות המזוינים הקנדיים"]},
    {"name": "Europe", "country": None, "aliases": ["אירופה"]},
]


@lru_cache(maxsize=1)
def _curated_org_index() -> dict[str, dict[str, Any]]:
    """Same shape as :func:`_alias_index` (``{normalize_name_key(surface) -> canonical record}``)
    but built from :data:`_CURATED_ORG_RECORDS` instead of the watchlist -- every record resolves
    to ``kind="org"``."""
    index: dict[str, dict[str, Any]] = {}
    for rec in _CURATED_ORG_RECORDS:
        canonical = {
            "name": rec["name"],
            "kind": "org",
            "country": rec.get("country"),
            "aliases": list(rec.get("aliases") or []),
            "focus": [],
        }
        for surface in [rec["name"], *canonical["aliases"]]:
            key = normalize_name_key(surface)
            if key:
                index[key] = canonical
    return index


# Q3-13 r3: country names (English + Hebrew, including the common transliterations seen in this
# corpus) -> canonical English display name. Used both to fix a country mistakenly typed as
# "company" (e.g. "איראן", "ארצות הברית", "יוון") and to merge Hebrew/English duplicates of the
# same country entity (e.g. "יפן" and "Japan").
_COUNTRY_NAMES: dict[str, str] = {
    k: v
    for surfaces, v in [
        (["united states", "usa", "u.s.a", "us", "u.s.", 'ארה"ב', "ארצות הברית"], "United States"),
        (["israel", "ישראל", "מדינת ישראל"], "Israel"),
        (["japan", "יפן"], "Japan"),
        (["china", "prc", "סין"], "China"),
        (["russia", "רוסיה"], "Russia"),
        (["iraq", "עיראק"], "Iraq"),
        (["iran", "איראן"], "Iran"),
        (["germany", "גרמניה"], "Germany"),
        (["turkey", "turkiye", "טורקיה"], "Turkey"),
        (["india", "הודו"], "India"),
        (["greece", "יוון"], "Greece"),
        (["serbia", "סרביה"], "Serbia"),
        (["ukraine", "אוקראינה"], "Ukraine"),
        (["belgium", "בלגיה"], "Belgium"),
        (["france", "צרפת"], "France"),
        (["united kingdom", "uk", "britain", "great britain", "בריטניה"], "United Kingdom"),
        (["south korea", "republic of korea", "rok", "דרום קוריאה"], "South Korea"),
        (["north korea", "dprk", "צפון קוריאה"], "North Korea"),
        (["taiwan", "טייוואן"], "Taiwan"),
        (["poland", "פולין"], "Poland"),
        (["australia", "אוסטרליה"], "Australia"),
        (["canada", "קנדה"], "Canada"),
        (["saudi arabia", "ערב הסעודית"], "Saudi Arabia"),
        (["united arab emirates", "uae", "איחוד האמירויות", "האמירויות"], "United Arab Emirates"),
        (["netherlands", "holland", "הולנד"], "Netherlands"),
        (["spain", "ספרד"], "Spain"),
        (["italy", "איטליה"], "Italy"),
        (["sweden", "שוודיה"], "Sweden"),
        (["norway", "נורווגיה"], "Norway"),
        (["finland", "פינלנד"], "Finland"),
        (["denmark", "דנמרק"], "Denmark"),
        (["egypt", "מצרים"], "Egypt"),
        (["jordan", "ירדן"], "Jordan"),
        (["lebanon", "לבנון"], "Lebanon"),
        (["syria", "סוריה"], "Syria"),
        (["yemen", "תימן"], "Yemen"),
        (["qatar", "קטאר"], "Qatar"),
        (["bahrain", "בחריין"], "Bahrain"),
        (["brazil", "ברזיל"], "Brazil"),
        (["singapore", "סינגפור"], "Singapore"),
        (["philippines", "הפיליפינים"], "Philippines"),
    ]
    for k in surfaces
}
_COUNTRY_NAMES = {normalize_name_key(k): v for k, v in _COUNTRY_NAMES.items()}


def resolve_country_name(name: str) -> str | None:
    """The canonical English country display name if ``name`` (English or Hebrew, any of the
    common transliterations in :data:`_COUNTRY_NAMES`) literally denotes a country -- else
    ``None``. Used to (a) fix a country mistakenly stored with ``kind="company"`` and (b) merge
    Hebrew/English duplicates of the same country entity."""
    if not name:
        return None
    return _COUNTRY_NAMES.get(normalize_name_key(name))


# Q3-13 r3: static country map for well-known defense-industry companies that are deliberately
# *not* on `config/watchlist.yaml` (not EO/IR-relevant enough to track for search/NER expansion)
# but still show up as entities in this corpus and deserve a filled-in `country` rather than being
# left blank forever. Keys are `normalize_name_key`-ready surface strings (English name and, where
# an existing entity row is spelled in Hebrew, that transliteration too); values are the same
# country-code convention as `config/watchlist.yaml` (ISO-ish, "EU" for pan-European primes).
_COMPANY_COUNTRY_MAP: dict[str, str] = {
    "rolls-royce": "UK",
    "rolls royce": "UK",
    "רולס-רויס": "UK",
    "רולס רויס": "UK",
    "thyssenkrupp": "DE",
    "תיסנקרופ": "DE",
    "baykar": "TR",
    "באייקר baykar": "TR",
    "באייקר": "TR",
    "boeing": "US",
    "general dynamics": "US",
    "bae systems": "UK",
    "textron": "US",
    "general atomics": "US",
    "leidos": "US",
    "palantir": "US",
    "palantir technologies": "US",
    "kratos defense": "US",
    "kratos": "US",
    "diehl defence": "DE",
    "diehl": "DE",
    "knds": "DE",
    "nexter": "FR",
    "cmi defence": "BE",
    "patria": "FI",
    "indra": "ES",
    "embraer": "BR",
    "denel": "ZA",
    "st engineering": "SG",
    "singapore technologies engineering": "SG",
    "hyundai rotem": "KR",
    "doosan": "KR",
    "israel shipyards": "IL",
    "plasan": "IL",
    "general robotics": "IL",
    "roboteam": "IL",
    "percepto": "IL",
    "simlat": "IL",
    "robin radar systems": "NL",
    "robin radar": "NL",
    "detect": "US",
    "echodyne": "US",
    "droneshield": "AU",
    "qinetiq": "UK",
    "ultra electronics": "UK",
    "chemring group": "UK",
    "chemring": "UK",
    "meggitt": "UK",
    "cobham": "UK",
    "dassault aviation": "FR",
    "dassault": "FR",
    "airbus": "EU",
    "airbus defence and space": "EU",
    "naval group": "FR",
    "fincantieri": "IT",
    "damen": "NL",
    "babcock international": "UK",
    "babcock": "UK",
    "honeywell": "US",
    "ge aerospace": "US",
    "imi systems": "IL",
}
_COMPANY_COUNTRY_MAP = {normalize_name_key(k): v for k, v in _COMPANY_COUNTRY_MAP.items()}


def resolve_company_country(name: str) -> str | None:
    """The static-map country for a well-known non-watchlist defense company (Q3-13 r3), or
    ``None`` when ``name`` isn't in :data:`_COMPANY_COUNTRY_MAP`."""
    if not name:
        return None
    return _COMPANY_COUNTRY_MAP.get(normalize_name_key(name))


# Q3-13 r3: substrings that mark a Hebrew phrase as a generic concept/category/market description
# rather than a specific, named entity -- e.g. "השוק הביטחוני" (the defense market), "תעשייה"
# (industry), "לקוחות בינלאומיים" (international customers), "תמונות תרמיות" (thermal images),
# "מפעילים בשטח" (field operators), "איומים בקבוצת משקל 3" (weight-class-3 threats), "מלחמת
# איראן-עיראק" (Iran-Iraq war), "מצר הורמוז" (Strait of Hormuz). Checked only *after* a name has
# failed to resolve against the watchlist, the curated org table, and the country table above --
# so a real, specific, recognised entity is never rejected just because a keyword happens to
# appear inside it.
_GENERIC_HEBREW_KEYWORDS: tuple[str, ...] = (
    "שוק",
    "תעשיי",
    "סטארט",
    "לקוח",
    "תמונ",
    "מפעיל",
    "איומ",
    "מלחמ",
    "מצר",
    "משבר",
    "משקיע",
    "תשתי",
    "אבטח",
    "ספק",
    "תצוג",
    "סביב",
    "חברות",
    "תחום",
    "של מדינה",
)


def is_generic_non_entity(name: str) -> bool:
    """True for a Hebrew (or mixed) phrase that describes a generic concept/market/category
    rather than one specific named actor -- see :data:`_GENERIC_HEBREW_KEYWORDS` -- or a
    comma-separated enumeration of two or more countries (e.g. "יפן, דנמרק, גרמניה", a list, not
    a single entity). Never true for anything recognised via the watchlist, the curated org
    table, a known system designation, or the country table."""
    if not name or not name.strip():
        return True
    if resolve_canonical(name) or resolve_country_name(name) or _is_system_designation(name):
        return False
    if "," in name:
        parts = [p.strip() for p in name.split(",") if p.strip()]
        if sum(1 for p in parts if resolve_country_name(p)) >= 2:
            return True
    return any(kw in name for kw in _GENERIC_HEBREW_KEYWORDS)


def is_junk_entity(name: str) -> bool:
    """The "real entity" gate (Q3-13 r3, broadened r4): true when ``name`` should never be stored
    as an entity at all -- a technique/algorithm name (:func:`is_technique_like`), a generic
    Hebrew concept/category/market phrase (:func:`is_generic_non_entity`), or a publication
    venue/aggregator name (:func:`is_source_like_name`)."""
    return is_technique_like(name) or is_generic_non_entity(name) or is_source_like_name(name)


def find_watchlist_aliases_in_text(text: str) -> list[str]:
    """Q3-8: every watchlist canonical name whose name or alias literally appears in ``text``
    (whole-word/phrase, case-insensitive for Latin script) -- deduplicated, in order of first
    appearance in the surface-string list (longest surface first, so a full company name is
    preferred over a shorter alias substring of it)."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for surface, canonical_name in _surface_to_canonical_name():
        if canonical_name in seen:
            continue
        if re.search(r"\b" + re.escape(surface) + r"\b", text, re.IGNORECASE):
            found.append(canonical_name)
            seen.add(canonical_name)
    return found
