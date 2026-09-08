"""Stage 3 (``docs/PLAN_PRODUCT_DOSSIER.md`` section 4.3): one structured-extraction LLM call
(cloud chain, JSON schema = :class:`~eoa.llm.schemas.product_dossier.ProductDossierOut`) over the
corpus + every research-topic finding, followed by deterministic, code-only post-checks that
enforce the report-style "never invent, cite every fact" discipline the prompt itself already asks
for -- a model that slips past the prompt's own rules is caught here, not trusted.

Every check below is independently additive (mirrors ``eoa.pipeline.analysis_grounding``'s own
"a unit that fails any rule is dropped or trimmed, the rest is kept" convention -- see that
module's docstring): an out-of-range citation clears that unit's ``cites``, an ungrounded number
blanks just the offending value (not the whole row), an ungrounded competitor/partner NAME drops
the whole row (there is nothing else worth keeping once the row's own subject is unverifiable), and
a price row missing a qualifying source kind/basis is dropped outright (section 1/6.3's own
strictest rule). Every drop is logged as ``dossier.field_dropped`` and returned in
``GroundingResult.dropped`` for the caller (``eoa.dossier.report``) to fold into the persisted
``qa_report``-equivalent record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

from eoa.dossier.corpus import CorpusResult
from eoa.dossier.plan import PlanResult
from eoa.errors import LLMOutputError
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.product_dossier import (
    CompetitorRow,
    DealRow,
    PartnerRow,
    PerformanceRow,
    PriceRow,
    ProductDossierOut,
    SpecRow,
)
from eoa.pipeline import entity_normalize
from eoa.report.claims_gate import gate_sentences

log = structlog.get_logger(__name__)

_NUM_PREDICT = 16000

#: Section 1/6.3: a price figure is only ever extracted from one of these source kinds.
_QUALIFYING_PRICE_SOURCE_KINDS = {"contract", "tender", "budget", "official"}


# --------------------------------------------------------------------------
# PD-fix (2026-09-08, item 3): deterministic deal-amount/date/country post-processing -- never
# LLM-authored, always derived from what the model already wrote (``amount``/``country``) or from
# the cited source's own metadata (``published_at``), same "code, not another model call" discipline
# ``eoa.dossier.diff`` already documents for "מה השתנה".
# --------------------------------------------------------------------------

_DEAL_NUM_RE = re.compile(r"(\d[\d,.']*)")

#: Hebrew + English scale words -> multiplier (a published figure is as likely to come from an
#: English-language source as a Hebrew one). Checked independently, first hit wins -- none of these
#: are substrings of each other (case-insensitive match on the English words).
_SCALE_HE: dict[str, float] = {
    "מיליארד": 1_000_000_000,
    "מיליארדי": 1_000_000_000,
    "billion": 1_000_000_000,
    "מיליון": 1_000_000,
    "מיליוני": 1_000_000,
    "million": 1_000_000,
    "אלפי": 1_000,
    "אלף": 1_000,
    "thousand": 1_000,
}

_CURRENCY_HE: dict[str, str] = {
    "דולר": "USD",
    "דולרים": "USD",
    "usd": "USD",
    "dollars": "USD",
    "dollar": "USD",
    "$": "USD",
    "שקל": "ILS",
    "שקלים": "ILS",
    "nis": "ILS",
    "ils": "ILS",
    "₪": "ILS",
    "יורו": "EUR",
    "eur": "EUR",
    "euros": "EUR",
    "euro": "EUR",
    "€": "EUR",
    "לירה שטרלינג": "GBP",
    "gbp": "GBP",
    "pounds": "GBP",
    "£": "GBP",
}


def parse_amount_he(text: str) -> tuple[float | None, str | None]:
    """``"כ-80 מיליון דולר"`` -> ``(80_000_000.0, "USD")``. Only ever applied to text the model
    itself already wrote in ``amount`` (never invents a figure) -- the leading digit run is taken
    as the base number, an Hebrew scale word anywhere after it multiplies it, and a currency
    word/symbol anywhere in the text sets the currency. Returns ``(None, None)`` when no digit run
    is found at all (nothing to parse -- not an error)."""
    if not text:
        return None, None
    m = _DEAL_NUM_RE.search(text)
    if not m:
        return None, None
    num_str = m.group(1).replace(",", "").replace("'", "")
    try:
        value = float(num_str)
    except ValueError:
        return None, None
    tail = text[m.end() :].casefold()
    for word, mult in _SCALE_HE.items():
        if word.casefold() in tail:
            value *= mult
            break
    currency = None
    haystack = text.casefold()
    for word, code in _CURRENCY_HE.items():
        if word.casefold() in haystack:
            currency = code
            break
    return value, currency


#: PD-fix item 3: a `country` value that actually names a broader region (not a specific country)
#: is reclassified into `region_he` instead -- deliberately conservative (English + Hebrew region
#: nouns only; a real country name never happens to contain one of these as a whole word).
_REGION_HINT_RE = re.compile(
    r"\b(region|area|asia.?pacific|middle\s*east|gulf|europe|africa|"
    r"אזור|אסיה|אפריקה|אירופה|המפרץ|פסיפיק|מזרח\s*תיכון)\b",
    re.IGNORECASE,
)


def split_country_region(country: str) -> tuple[str, str]:
    """``("country", "region_he")`` -- ``country`` stays empty (never a guessed country) when the
    text reads like a region descriptor; that text moves to ``region_he`` instead."""
    c = (country or "").strip()
    if not c:
        return "", ""
    if _REGION_HINT_RE.search(c):
        return "", c
    return c, ""


# --------------------------------------------------------------------------
# data block for the extraction prompt
# --------------------------------------------------------------------------


def _registry_lines(corpus: CorpusResult) -> list[str]:
    lines = []
    for r in corpus.registry:
        lines.append(
            f"[{r['n']}] ({r['kind']}) {r.get('title') or '—'} | {r.get('source_name') or r.get('url') or '—'}"
        )
    return lines


def build_data_block(corpus: CorpusResult, plan_result: PlanResult) -> str:
    parts = ["רשימת מקורות ממוספרת:", *_registry_lines(corpus)]
    if corpus.previous is not None:
        parts.append("\nנתוני הסקירה הקודמת (JSON, לשימוש בהשוואה ל-what_changed_he בלבד):")
        parts.append(str(corpus.previous.get("data") or {})[:4000])
    parts.append("\nממצאי חקירות העומק לפי נושא:")
    parts.extend(plan_result.context_blocks_he())
    return "\n".join(parts)


def extract_dossier(
    corpus: CorpusResult,
    plan_result: PlanResult,
    *,
    role: str = "resident",
    interactive: bool = False,
) -> ProductDossierOut:
    data_block = build_data_block(corpus, plan_result)
    prompt = render(
        "product_dossier_extract",
        product_name=corpus.product_name,
        vendor_suffix=f" ({corpus.vendor})" if corpus.vendor else "",
        aliases_he=", ".join(corpus.aliases) or "אין",
        n_registry=len(corpus.registry),
        data=wrap_data(data_block, "dossier", corpus.product_key),
    )
    return chat_structured(
        role,
        ProductDossierOut,
        [
            {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
            {"role": "user", "content": prompt},
        ],
        task="report",
        interactive=interactive,
        options={"temperature": 0.2, "num_predict": _NUM_PREDICT},
    )


# --------------------------------------------------------------------------
# grounding post-checks
# --------------------------------------------------------------------------


@dataclass
class DroppedField:
    field: str
    reason: str
    value: str = ""


@dataclass
class GroundingResult:
    dossier: ProductDossierOut
    dropped: list[DroppedField] = field(default_factory=list)


def _valid_ns(corpus: CorpusResult) -> set[int]:
    return {int(r["n"]) for r in corpus.registry if r.get("n") is not None}


def _registry_text_by_n(corpus: CorpusResult, plan_result: PlanResult) -> dict[int, str]:
    """Best-effort source text for each registry number -- used only by the number/name grounding
    checks below. A DB row's own text fields (title/summary/abstract/...) for a DB-kind entry; the
    matching topic's read-page summary (``eoa.search.deep_search._summarise_page`` output, carried
    on ``Investigation.read_summaries``) for a web-kind entry, falling back to the investigation's
    own ``answer_he``/``key_facts`` when no per-page summary matches (e.g. a synthesized answer)."""
    texts: dict[int, str] = {}
    by_id = {("item", it["id"]): it for it in corpus.items}
    by_id.update({("event", ev["id"]): ev for ev in corpus.events})
    by_id.update({("patent", p["id"]): p for p in corpus.patents})
    by_id.update({("tender", t["id"]): t for t in corpus.tenders})
    by_id.update({("forecast", f["id"]): f for f in corpus.forecasts})

    web_summaries: dict[str, str] = {}
    for finding in plan_result.findings:
        for s in finding.investigation.read_summaries:
            if s.get("url"):
                web_summaries[s["url"]] = f"{s.get('title', '')}\n{s.get('summary', '')}"

    for r in corpus.registry:
        n = r.get("n")
        kind = r.get("kind")
        if kind == "web":
            url = r.get("url") or ""
            texts[n] = web_summaries.get(url, r.get("title") or "")
        elif kind in ("item", "event", "patent", "tender", "forecast"):
            row = by_id.get((kind, r.get("id")))
            if row is None:
                texts[n] = r.get("title") or ""
                continue
            parts = [
                row.get("title"),
                row.get("summary_he"),
                row.get("so_what_he"),
                row.get("abstract"),
                row.get("claims_summary_he") if "claims_summary_he" in row else None,
                row.get("program"),
                row.get("payload_need"),
                row.get("rationale_he"),
                " ".join(row.get("key_facts") or []) if row.get("key_facts") else None,
                " ".join(row.get("assignees") or []) if row.get("assignees") else None,
            ]
            texts[n] = "\n".join(p for p in parts if p)
        else:
            texts[n] = r.get("title") or ""
    return texts


def _text_for_cites(cites: list[int], registry_text: dict[int, str]) -> str:
    return "\n".join(registry_text.get(n, "") for n in cites)


def _registry_published_at_by_n(corpus: CorpusResult) -> dict[int, Any]:
    """PD-fix item 3: ``n -> published_at`` for every registry row that has one (every DB-kind row
    already carries it; web rows currently don't -- ``eoa.dossier.plan`` has no reliable publish
    date for a fetched page today) -- used only to backfill a deal's missing ``date`` from its own
    cited source's publish date."""
    return {r["n"]: r.get("published_at") for r in corpus.registry if r.get("n") is not None and r.get("published_at")}


# Narrow local port of eoa.pipeline.analysis_grounding's digit-boundary-safe matching (see that
# module's own docstring for why this is a duplicate, not a cross-package import: eoa.dossier
# importing a private ``_``-prefixed name from eoa.pipeline would also be a layering inversion).
_DIGIT_RUN_RE = re.compile(r"\d[\d,.]*")


def _grouped_digit_pattern(digits: str) -> str:
    groups: list[str] = []
    i = len(digits)
    while i > 0:
        groups.append(digits[max(0, i - 3) : i])
        i -= 3
    groups.reverse()
    return r"[,.]?".join(re.escape(g) for g in groups)


def _digits_grounded(candidate: str, corpus_text: str) -> bool:
    digits = re.sub(r"[^\d]", "", candidate)
    if len(digits) < 2:
        return True
    pattern = r"(?<!\d)(?<!\d\.)" + _grouped_digit_pattern(digits) + r"(?!\d)(?!\.\d)"
    return re.search(pattern, corpus_text) is not None


def _numbers_grounded(text: str, corpus_text: str) -> bool:
    return all(_digits_grounded(m.group(0), corpus_text) for m in _DIGIT_RUN_RE.finditer(text or ""))


def _name_grounded(name: str, corpus_text: str) -> bool:
    if not name or not name.strip():
        return True
    if name.casefold() in corpus_text.casefold():
        return True
    canonical = entity_normalize.resolve_canonical(name)
    return canonical is not None


def _clean_cites(cites: list[int], valid_ns: set[int]) -> tuple[list[int], list[int]]:
    kept = [n for n in cites if n in valid_ns]
    bad = [n for n in cites if n not in valid_ns]
    return kept, bad


def _drop(dropped: list[DroppedField], field_name: str, reason: str, value: Any = "") -> None:
    entry = DroppedField(field=field_name, reason=reason, value=str(value)[:200])
    dropped.append(entry)
    log.info("dossier.field_dropped", field=field_name, reason=reason, value=str(value)[:200])


def _ground_sentences(
    sentences: list[Any], *, label: str, valid_ns: set[int], dropped: list[DroppedField]
) -> list[Any]:
    kept = []
    for s in sentences:
        good_cites, bad_cites = _clean_cites(s.cites, valid_ns)
        if bad_cites:
            _drop(dropped, label, f"cites out of range: {bad_cites}", s.text_he)
        if not good_cites:
            continue
        kept.append(s.model_copy(update={"cites": good_cites}))
    gated, _result = gate_sentences(kept, context=label)
    return gated


def _ground_spec_row(
    row: SpecRow, valid_ns: set[int], registry_text: dict[int, str], dropped: list[DroppedField]
) -> SpecRow:
    good_cites, bad_cites = _clean_cites(row.cites, valid_ns)
    if bad_cites:
        _drop(dropped, "specifications.cites", f"out of range: {bad_cites}", row.parameter_he)
    text = _text_for_cites(good_cites, registry_text)
    value = row.value
    if value and not _numbers_grounded(value, text):
        _drop(dropped, "specifications.value", "number not in cited source", value)
        value = ""
        good_cites = []
    return row.model_copy(update={"cites": good_cites, "value": value})


def _ground_performance_row(
    row: PerformanceRow, valid_ns: set[int], registry_text: dict[int, str], dropped: list[DroppedField]
) -> PerformanceRow:
    good_cites, bad_cites = _clean_cites(row.cites, valid_ns)
    if bad_cites:
        _drop(dropped, "performance.cites", f"out of range: {bad_cites}", row.metric_he)
    text = _text_for_cites(good_cites, registry_text)
    claimed = row.claimed_value
    if claimed and not _numbers_grounded(claimed, text):
        _drop(dropped, "performance.claimed_value", "number not in cited source", claimed)
        claimed = ""
    tested = row.tested_or_operational_value
    if tested and not _numbers_grounded(tested, text):
        _drop(dropped, "performance.tested_or_operational_value", "number not in cited source", tested)
        tested = None
    return row.model_copy(
        update={"cites": good_cites, "claimed_value": claimed, "tested_or_operational_value": tested}
    )


def _ground_deal_row(
    row: DealRow,
    valid_ns: set[int],
    registry_text: dict[int, str],
    dropped: list[DroppedField],
    *,
    published_by_n: dict[int, Any],
) -> DealRow:
    good_cites, bad_cites = _clean_cites(row.cites, valid_ns)
    if bad_cites:
        _drop(dropped, "deals.cites", f"out of range: {bad_cites}", row.customer)
    text = _text_for_cites(good_cites, registry_text)
    amount = row.amount
    if amount and not _numbers_grounded(amount, text):
        _drop(dropped, "deals.amount", "number not in cited source", amount)
        amount = ""

    # PD-fix item 3: amount_value/currency are always deterministically derived from the (already
    # grounded) `amount` text -- never authored by the model itself.
    amount_value: float | None = None
    currency = row.currency
    if amount:
        parsed_value, parsed_currency = parse_amount_he(amount)
        amount_value = parsed_value
        currency = currency or parsed_currency or ""

    # A `country` value that actually names a region, not a specific country, moves to region_he.
    country, region_from_country = split_country_region(row.country)
    region_he = row.region_he or region_from_country

    # A deal with no date of its own inherits its cited source's publish date, marked accordingly
    # (never indistinguishable from an actual deal-closing date).
    date = row.date
    date_kind = row.date_kind or "deal"
    if not date:
        for n in good_cites:
            published = published_by_n.get(n)
            if published:
                date = str(published)
                date_kind = "published"
                break

    return row.model_copy(
        update={
            "cites": good_cites,
            "amount": amount,
            "amount_value": amount_value,
            "currency": currency,
            "country": country,
            "region_he": region_he,
            "date": date,
            "date_kind": date_kind,
        }
    )


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 1): basis_he consistency. The prompt already asks the model for one of
# three literal Hebrew phrasings, but (same "a model that slips past the prompt's own rules is
# caught here" discipline as everywhere else in this module) it doesn't always comply -- the live
# SPECTRO XR dossier's own price row carried "לחוזה כולל (לא צוין מחיר ליחידה)" verbatim, not the
# prompt's "לתוכנית כולה". Rather than reject every variant, this classifies whatever free-text
# basis_he the model wrote into exactly one of three canonical forms (contract/programme total,
# per-unit, or a lot of N units, with N read off the model's own text -- never invented); a
# basis_he that fits none of them is unparseable and the whole row is dropped, per the module's own
# "nothing else worth keeping" rule for a row whose grounding can't be verified.
# --------------------------------------------------------------------------

BASIS_TOTAL_HE = "היקף חוזה (לא מחיר ליחידה)"
BASIS_UNIT_HE = "מחיר ליחידה"

#: A lot of N units -- checked first because it's the most specific pattern (and because its own
#: text often also contains "יחידות", which would otherwise false-match the per-unit keywords).
_LOT_BASIS_RE = re.compile(r"(?:למנה|מנה)\s+של\s+(\d+)|lot\s+of\s+(\d+)", re.IGNORECASE)

#: An explicit "no per-unit price stated" negation -- checked before the bare per-unit keywords
#: below, since a phrase like "לא צוין מחיר ליחידה" contains the word "ליחידה" itself.
_BASIS_UNIT_NEGATION_RE = re.compile(
    r"לא\s+(?:צוין\s+)?מחיר\s+ליחיד|not\s+(?:a\s+)?per[- ]unit|no\s+per[- ]unit", re.IGNORECASE
)

_BASIS_TOTAL_KEYWORDS = ("חוזה", "תוכנית", "כולל", "כוללת", "היקף", "contract", "programme", "program", "total")
_BASIS_UNIT_KEYWORDS = ("ליחידה", "יחידה", "per-unit", "per unit", "unit price")


def _classify_price_basis(basis_he: str) -> str | None:
    """Returns one of the three canonical forms, or ``None`` when ``basis_he`` is unparseable."""
    text = basis_he.strip()
    if not text:
        return None
    lot = _LOT_BASIS_RE.search(text)
    if lot:
        n = lot.group(1) or lot.group(2)
        return f"למנה של {n} יחידות"
    if _BASIS_UNIT_NEGATION_RE.search(text):
        return BASIS_TOTAL_HE
    low = text.lower()
    if any(kw in text or kw in low for kw in _BASIS_TOTAL_KEYWORDS):
        return BASIS_TOTAL_HE
    if any(kw in text or kw in low for kw in _BASIS_UNIT_KEYWORDS):
        return BASIS_UNIT_HE
    return None


def _ground_price_row(
    row: PriceRow, valid_ns: set[int], registry_text: dict[int, str], dropped: list[DroppedField]
) -> PriceRow | None:
    """Section 1/6.3's strictest rule: a price row survives only when it names a qualifying source
    kind, carries a stated basis that resolves to one of the three canonical ``basis_he`` forms
    (:func:`_classify_price_basis`), resolves to at least one valid citation, and its own figure's
    digits are grounded in that citation's text -- any failure drops the WHOLE row (never a
    half-grounded price)."""
    good_cites, bad_cites = _clean_cites(row.cites, valid_ns)
    if bad_cites:
        _drop(dropped, "pricing.cites", f"out of range: {bad_cites}", row.figure)
    if not good_cites:
        _drop(dropped, "pricing", "no valid citation", row.figure)
        return None
    if row.source_kind not in _QUALIFYING_PRICE_SOURCE_KINDS:
        _drop(
            dropped, "pricing", f"source_kind {row.source_kind!r} not a qualifying price source", row.figure
        )
        return None
    if not row.basis_he.strip():
        _drop(dropped, "pricing", "missing basis_he", row.figure)
        return None
    canonical_basis = _classify_price_basis(row.basis_he)
    if canonical_basis is None:
        _drop(dropped, "pricing", f"unparseable basis_he: {row.basis_he!r}", row.figure)
        return None
    text = _text_for_cites(good_cites, registry_text)
    if row.figure and not _numbers_grounded(row.figure, text):
        _drop(dropped, "pricing", "figure number not in cited source", row.figure)
        return None
    return row.model_copy(update={"cites": good_cites, "basis_he": canonical_basis})


def _ground_competitor_row(
    row: CompetitorRow, valid_ns: set[int], registry_text: dict[int, str], dropped: list[DroppedField]
) -> CompetitorRow | None:
    good_cites, _bad = _clean_cites(row.cites, valid_ns)
    text = _text_for_cites(good_cites, registry_text)
    if not _name_grounded(row.product, text):
        _drop(dropped, "competitors", "invented/ungrounded competitor product", row.product)
        return None
    return row.model_copy(update={"cites": good_cites})


def _ground_partner_row(
    row: PartnerRow, valid_ns: set[int], registry_text: dict[int, str], dropped: list[DroppedField]
) -> PartnerRow | None:
    good_cites, _bad = _clean_cites(row.cites, valid_ns)
    text = _text_for_cites(good_cites, registry_text)
    if not _name_grounded(row.partner, text):
        _drop(dropped, "partnerships", "invented/ungrounded partner", row.partner)
        return None
    return row.model_copy(update={"cites": good_cites})


def _ground_generic_row(row: Any, valid_ns: set[int], dropped: list[DroppedField], *, label: str) -> Any:
    good_cites, bad_cites = _clean_cites(row.cites, valid_ns)
    if bad_cites:
        _drop(dropped, label, f"cites out of range: {bad_cites}")
    return row.model_copy(update={"cites": good_cites})


def ground_dossier(
    draft: ProductDossierOut, corpus: CorpusResult, plan_result: PlanResult
) -> GroundingResult:
    """The single entry point ``eoa.dossier.report`` calls: applies every deterministic post-check
    to ``draft`` and returns the grounded record plus a flat drop log."""
    valid_ns = _valid_ns(corpus)
    registry_text = _registry_text_by_n(corpus, plan_result)
    published_by_n = _registry_published_at_by_n(corpus)
    dropped: list[DroppedField] = []

    identity = draft.identity.model_copy(update={"cites": _clean_cites(draft.identity.cites, valid_ns)[0]})
    maturity = draft.maturity.model_copy(update={"cites": _clean_cites(draft.maturity.cites, valid_ns)[0]})
    regulatory = draft.regulatory_export.model_copy(
        update={"cites": _clean_cites(draft.regulatory_export.cites, valid_ns)[0]}
    )

    summary_he = _ground_sentences(draft.summary_he, label="summary_he", valid_ns=valid_ns, dropped=dropped)
    risks = _ground_sentences(
        draft.risks_and_gaps_he, label="risks_and_gaps_he", valid_ns=valid_ns, dropped=dropped
    )
    bd_implications = _ground_sentences(
        draft.bd_implications_he, label="bd_implications_he", valid_ns=valid_ns, dropped=dropped
    )
    what_changed = (
        _ground_sentences(draft.what_changed_he, label="what_changed_he", valid_ns=valid_ns, dropped=dropped)
        if draft.what_changed_he is not None
        else None
    )

    specifications = [_ground_spec_row(r, valid_ns, registry_text, dropped) for r in draft.specifications]
    performance = [_ground_performance_row(r, valid_ns, registry_text, dropped) for r in draft.performance]
    deals = [
        _ground_deal_row(r, valid_ns, registry_text, dropped, published_by_n=published_by_n)
        for r in draft.deals
    ]
    pricing = [
        r for r in (_ground_price_row(r, valid_ns, registry_text, dropped) for r in draft.pricing) if r
    ]
    competitors = [
        r
        for r in (_ground_competitor_row(r, valid_ns, registry_text, dropped) for r in draft.competitors)
        if r
    ]
    partnerships = [
        r for r in (_ground_partner_row(r, valid_ns, registry_text, dropped) for r in draft.partnerships) if r
    ]
    variants = [
        _ground_generic_row(r, valid_ns, dropped, label="variants_and_versions")
        for r in draft.variants_and_versions
    ]
    patents = [_ground_generic_row(r, valid_ns, dropped, label="patents") for r in draft.patents]
    tenders = [
        _ground_generic_row(r, valid_ns, dropped, label="tenders_and_forecasts")
        for r in draft.tenders_and_forecasts
    ]

    grounded = draft.model_copy(
        update={
            "identity": identity,
            "maturity": maturity,
            "regulatory_export": regulatory,
            "summary_he": summary_he,
            "risks_and_gaps_he": risks,
            "bd_implications_he": bd_implications,
            "what_changed_he": what_changed,
            "specifications": specifications,
            "performance": performance,
            "deals": deals,
            "pricing": pricing,
            "competitors": competitors,
            "partnerships": partnerships,
            "variants_and_versions": variants,
            "patents": patents,
            "tenders_and_forecasts": tenders,
        }
    )
    return GroundingResult(dossier=grounded, dropped=dropped)


def build_dossier(
    corpus: CorpusResult, plan_result: PlanResult, *, role: str = "resident", interactive: bool = False
) -> GroundingResult:
    """``extract`` + ``ground`` in one call -- the shape ``eoa.dossier.report`` uses. A
    :class:`~eoa.errors.LLMOutputError` propagates (the caller decides the fallback -- an empty,
    honest "not_found" dossier -- rather than this module inventing one)."""
    try:
        draft = extract_dossier(corpus, plan_result, role=role, interactive=interactive)
    except LLMOutputError:
        raise
    return ground_dossier(draft, corpus, plan_result)


__all__ = [
    "DroppedField",
    "GroundingResult",
    "build_data_block",
    "build_dossier",
    "extract_dossier",
    "ground_dossier",
    "parse_amount_he",
    "split_country_region",
]
