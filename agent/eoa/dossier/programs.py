"""Programme deals (PD-datasheet, 2026-09-09, LESSONS-1 item 2): identify the platforms a product
is integrated on, then treat a deal for one of those platforms as evidence about the product too
-- "a page about a carrier platform is never dropped as irrelevant when the platform is in this
list" (``docs/qa/content_review/LESSONS-fable-dossier.md`` finding 2: a Watchkeeper X purchase page
was thrown away by the old relevance filter even though SPECTRO XR is Watchkeeper X's payload).

Deliberately pure/data-in-data-out, like ``eoa.dossier.datasheet``: no import of
``eoa.dossier.corpus``/``eoa.dossier.plan`` (``plan`` imports this module -- the reverse import
would cycle), and no network calls of its own. The actual platform-deal SEARCH reuses
``eoa.search.deep_search.investigate()`` -- the exact same robust search-plan-read-synthesize loop
every other dossier topic already runs -- which ``eoa.dossier.plan.run_plan`` (the caller) already
has wired up; this module only supplies (1) which platform names to search for and (2) how to turn
one such investigation's own ``InvestigationOut`` (``answer_he`` + ``key_facts``) into structured
``programme_deals`` rows, deterministically (regex/lookup, never a further LLM call).
"""

from __future__ import annotations

import re
from typing import Any

from eoa.pipeline.text_match import word_present

#: Real-world EO/IR-carrying platforms worth checking for by name -- UAVs, manned aircraft and
#: naval/ground platforms that commonly carry a targeting pod / MWS / gimbal / LOROP payload.
#: Deliberately a flat, curated list (same "small, conservative, checkable" spirit as
#: ``eoa.dossier.plan._VENDOR_DOMAIN_HINTS``) rather than an attempt at full generality -- a
#: platform this list misses simply gets no programme-deal search this run, never a wrong one.
KNOWN_PLATFORMS: tuple[str, ...] = (
    "Hermes 900",
    "Hermes 450",
    "Hermes StarLiner",
    "StarLiner",
    "Watchkeeper X",
    "Watchkeeper",
    "IAI 330 Puma",
    "IAR 330 Puma",
    "IAR-330 Puma",
    "IAR 330",
    "Heron TP",
    "Heron",
    "Predator B",
    "MQ-9 Reaper",
    "MQ-9B",
    "Reaper",
    "MQ-1 Predator",
    "Global Hawk",
    "RQ-4",
    "Orbiter 4",
    "Hermes 900 StarLiner",
    "F-16",
    "F-35",
    "F-15",
    "AH-64 Apache",
    "Apache",
    "UH-60 Black Hawk",
    "Black Hawk",
    "AS332 Puma",
    "Super Puma",
    "Camcopter S-100",
    "ScanEagle",
    "Sea Hunter",
    "Protector USV",
    "C-130",
    "P-8 Poseidon",
    "Reaper TB2",
    "Bayraktar TB2",
    "Anka",
    "Aquila",
)

#: How many identified platforms get their own dedicated deal search -- a real network cost per
#: platform (one full `investigate()` call), so kept small and only the first N (in the order
#: :func:`identify_platforms` found them, i.e. earliest-mentioned-first in the topic finding).
MAX_PROGRAMME_PLATFORMS = 4

#: Country -> ISO 639-1 search language, used both for :func:`detect_customer_countries`'s own
#: output and (by ``eoa.dossier.plan``) to extend a topic's own search languages once a customer
#: country is known -- LESSONS-fable-dossier finding 9: "מקורות ברומנית ובעברית חשפו את הלקוח-העוגן"
#: (Romanian/Hebrew sources surfaced the anchor customer that English-only search missed).
COUNTRY_LANGS: dict[str, str] = {
    "Romania": "ro",
    "Israel": "he",
    "Germany": "de",
    "Spain": "es",
    "France": "fr",
    "Italy": "it",
    "Poland": "pl",
    "Turkey": "tr",
    "Portugal": "pt",
    "Greece": "el",
    "Netherlands": "nl",
    "Brazil": "pt",
    "Mexico": "es",
    "Colombia": "es",
    "Chile": "es",
    "Austria": "de",
    "Switzerland": "de",
}

#: Hebrew name for every :data:`COUNTRY_LANGS` key -- an ``investigate()`` answer built with
#: ``langs_primary`` including Hebrew (always, per finding 9) very often names the customer country
#: in Hebrew even when the rest of the sentence is English/mixed; matching only the English name
#: (as the earlier, single-language version of this map did) missed exactly that case.
_COUNTRY_HE_NAMES: dict[str, str] = {
    "Romania": "רומניה",
    "Israel": "ישראל",
    "Germany": "גרמניה",
    "Spain": "ספרד",
    "France": "צרפת",
    "Italy": "איטליה",
    "Poland": "פולין",
    "Turkey": "טורקיה",
    "Portugal": "פורטוגל",
    "Greece": "יוון",
    "Netherlands": "הולנד",
    "Brazil": "ברזיל",
    "Mexico": "מקסיקו",
    "Colombia": "קולומביה",
    "Chile": "צ'ילה",
    "Austria": "אוסטריה",
    "Switzerland": "שוויץ",
}


def identify_platforms(texts: list[str], *, known_platforms: tuple[str, ...] = KNOWN_PLATFORMS, limit: int = 6) -> list[str]:
    """Deterministic, order-preserving: every ``known_platforms`` entry that appears as a whole
    word/phrase (``eoa.pipeline.text_match.word_present``, the same match discipline
    ``eoa.dossier.corpus``'s own precise-alias gate uses) anywhere in ``texts`` -- typically the
    "platforms and programmes" topic's own ``answer_he``/``key_facts`` plus the corpus's own known
    item/event text. A shorter platform name contained inside a longer already-matched one (e.g.
    "Hermes 900" already matched; "Hermes StarLiner" is a distinct real platform, both kept) is not
    deduped away -- only an exact case-insensitive repeat is."""
    hay = "\n".join(t for t in texts if t)
    if not hay.strip():
        return []
    found: list[str] = []
    seen: set[str] = set()
    for platform in known_platforms:
        key = platform.casefold()
        if key in seen:
            continue
        if word_present(hay, platform):
            seen.add(key)
            found.append(platform)
        if len(found) >= limit:
            break
    return found


def platform_deal_question_he(product_name: str, vendor: str | None, platform: str) -> str:
    """One research question per identified platform -- names both the platform (the actual search
    anchor) and the product/vendor (so an ``investigate()`` reader can judge relevance/frame the
    answer around "this deal is a carrier for the product"), matching ``eoa.dossier.plan.TOPICS``'
    own "always name the product explicitly" anchoring discipline."""
    return (
        f"אילו עסקאות/חוזים/רכש ידועים עבור הפלטפורמה {platform}, שעליה משולב {product_name} "
        f"({vendor or 'היצרן'}) -- מי הלקוח/המדינה, מתי, ומה ההיקף הכספי? ציין תאריך וסכום מדויקים "
        "כפי שפורסמו."
    )


# --------------------------------------------------------------------------
# deterministic amount/currency/date/country parsing over an investigation's own free text --
# intentionally independent of eoa.dossier.extract's own (private, out-of-scope-to-import)
# _DEAL_NUM_RE/_SCALE_HE/_CURRENCY_HE: same idea, kept local so this module has no dependency on a
# file a parallel lane (LESSONS-2) owns and may change independently.
# --------------------------------------------------------------------------

#: Two orderings, tried in turn: currency-before-number (English convention, "$72 million") and
#: number-before-currency (Hebrew convention, "72 מיליון דולר") -- a real English-language
#: ``investigate()`` answer overwhelmingly writes the former, a Hebrew one the latter; supporting
#: only one direction silently dropped every deal stated the other way.
_AMOUNT_RE_CURRENCY_FIRST = re.compile(
    r"(?P<currency>\$|USD|usd|₪|ILS|ils|€|EUR|eur)\s*(?P<num>\d[\d,.']*)\s*"
    r"(?P<scale>מיליארד|מיליארדי|billion|bn|מיליון|מיליוני|million|m|אלף|אלפי|thousand|k)?",
    re.IGNORECASE,
)
_AMOUNT_RE_NUMBER_FIRST = re.compile(
    r"(?P<num>\d[\d,.']*)\s*(?P<scale>מיליארד|מיליארדי|billion|bn|מיליון|מיליוני|million|m|אלף|אלפי|thousand|k)?"
    r"\s*(?P<currency>\$|USD|usd|דולר|דולרים|₪|ILS|ils|שקל|שקלים|€|EUR|eur|יורו)",
    re.IGNORECASE,
)
_SCALE_MULT: dict[str, float] = {
    "מיליארד": 1_000_000_000,
    "מיליארדי": 1_000_000_000,
    "billion": 1_000_000_000,
    "bn": 1_000_000_000,
    "מיליון": 1_000_000,
    "מיליוני": 1_000_000,
    "million": 1_000_000,
    "m": 1_000_000,
    "אלף": 1_000,
    "אלפי": 1_000,
    "thousand": 1_000,
    "k": 1_000,
}
_CURRENCY_MAP: dict[str, str] = {
    "$": "USD",
    "usd": "USD",
    "דולר": "USD",
    "דולרים": "USD",
    "₪": "ILS",
    "ils": "ILS",
    "שקל": "ILS",
    "שקלים": "ILS",
    "€": "EUR",
    "eur": "EUR",
    "יורו": "EUR",
}
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_MONTHS_HE = (
    "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר",
)
_MONTHS_EN = (
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
)


def _parse_amount(text: str) -> tuple[str | None, float | None, str | None]:
    """``(amount_text, amount_value, currency)`` from the first monetary pattern found (currency-
    before-number, e.g. "$72 million", tried first since it is the shorter/more specific match;
    falling back to number-before-currency, e.g. "72 מיליון דולר"), or ``(None, None, None)`` when
    neither matches. ``amount_value`` is always in raw currency units (a matched "180M$" ->
    ``180_000_000.0``)."""
    m = _AMOUNT_RE_CURRENCY_FIRST.search(text) or _AMOUNT_RE_NUMBER_FIRST.search(text)
    if not m:
        return None, None, None
    raw_num = m.group("num").replace(",", "").replace("'", "")
    try:
        num = float(raw_num)
    except ValueError:
        return None, None, None
    scale_key = (m.group("scale") or "").casefold()
    mult = _SCALE_MULT.get(scale_key, 1.0)
    currency_key = (m.group("currency") or "").casefold()
    currency = _CURRENCY_MAP.get(currency_key) or _CURRENCY_MAP.get(m.group("currency") or "")
    return m.group(0).strip(), num * mult, currency


def _parse_date_hint(text: str) -> str | None:
    """A best-effort ``"<month> <year>"``/``"<year>"`` textual date hint, never a fabricated
    ISO date -- this is a raw sighting from the sentence, not a normalized/validated date (the
    downstream extraction stage owns real date normalization/grounding)."""
    year_match = _YEAR_RE.search(text)
    if not year_match:
        return None
    year = year_match.group(0)
    lower = text.casefold()
    for month in (*_MONTHS_HE, *_MONTHS_EN):
        if month in text or month in lower:
            return f"{month} {year}"
    return year


def _country_present(text: str, country: str) -> bool:
    """``True`` when either the English (word-boundary) or Hebrew (plain substring -- Hebrew has no
    word-boundary ambiguity the way a short Latin token does, same dispatch
    ``eoa.pipeline.text_match.synonym_present`` already uses) name for ``country`` appears."""
    if word_present(text, country):
        return True
    he_name = _COUNTRY_HE_NAMES.get(country)
    return bool(he_name and he_name in text)


def detect_customer_countries(texts: list[str], *, countries: dict[str, str] = COUNTRY_LANGS) -> list[str]:
    """Every ``COUNTRY_LANGS`` key (English or Hebrew name, see :func:`_country_present`) present
    in ``texts`` -- used both as a ``programme_deals`` ``customer`` fallback (when no more specific
    customer name was written) and to drive :func:`extra_langs_for_countries`."""
    hay = "\n".join(t for t in texts if t)
    if not hay.strip():
        return []
    found: list[str] = []
    for country in countries:
        if _country_present(hay, country):
            found.append(country)
    return found


def extra_langs_for_countries(countries: list[str], *, countries_map: dict[str, str] = COUNTRY_LANGS) -> list[str]:
    """Ordered, deduped ISO language codes for ``countries`` -- Hebrew is deliberately never added
    here (``eoa.search.deep_search``'s own ``cfg.langs_primary`` already always includes it per
    finding 9's own "עברית תמיד" -- adding it again here would just be a harmless duplicate, so this
    function leaves that to the caller's existing defaults rather than hardcoding an assumption
    about what the primary language list already contains)."""
    out: list[str] = []
    seen: set[str] = set()
    for country in countries:
        lang = countries_map.get(country)
        if lang and lang not in seen:
            seen.add(lang)
            out.append(lang)
    return out


def parse_programme_deals(
    platform: str, *, answer_he: str, key_facts: list[str], cites: list[int]
) -> list[dict[str, Any]]:
    """Turns one platform's own investigation result into zero or more ``programme_deals`` rows.
    Scans ``answer_he`` and each ``key_facts`` sentence independently -- a finding with several
    distinct dollar figures (e.g. a framework value AND a follow-on order, LESSONS-fable-dossier's
    own Romania Watchkeeper X example: ~410M$ framework + ~180M$ order) yields one row per sentence
    that actually carries a monetary figure, never merged into one. A sentence with no money in it
    contributes nothing (this function only ever reports a *deal*, not general platform narrative).

    ``cites`` is the flat list of registry numbers the platform's own investigation read -- exactly
    ``eoa.dossier.plan``'s existing per-topic citation convention (a topic-level citation set, not
    a claim proven against one specific source's own text -- that finer grounding is
    ``eoa.dossier.extract``'s job, downstream, on the parallel LESSONS-2 lane)."""
    sentences = [s for s in (answer_he, *key_facts) if s and s.strip()]
    countries = COUNTRY_LANGS.keys()
    rows: list[dict[str, Any]] = []
    seen_notes: set[str] = set()
    for sentence in sentences:
        amount_text, amount_value, currency = _parse_amount(sentence)
        if amount_text is None:
            continue
        note = sentence.strip()
        if note in seen_notes:
            continue
        seen_notes.add(note)
        customer = next((c for c in countries if _country_present(sentence, c)), None)
        rows.append(
            {
                "platform": platform,
                "customer": customer,
                "date": _parse_date_hint(sentence),
                "amount_text": amount_text,
                "amount_value": amount_value,
                "currency": currency,
                "cites": list(cites),
                "note_he": note,
                "component_of_package": True,
            }
        )
    return rows


__all__ = [
    "COUNTRY_LANGS",
    "KNOWN_PLATFORMS",
    "MAX_PROGRAMME_PLATFORMS",
    "detect_customer_countries",
    "extra_langs_for_countries",
    "identify_platforms",
    "parse_programme_deals",
    "platform_deal_question_he",
]
