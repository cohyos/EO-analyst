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
from pydantic import BaseModel, Field

from eoa.dossier.corpus import CorpusResult, patent_relevance_he
from eoa.dossier.plan import PlanResult, normalize_url
from eoa.dossier.vocabulary import (
    SpecParam,
    effective_vocabulary,
    match_key_by_synonym,
    param_by_key,
    vocabulary_prompt_block_he,
)
from eoa.errors import LLMOutputError
from eoa.llm.ollama_client import DATA_GUARD_SYSTEM, chat_structured, wrap_data
from eoa.llm.prompts import render
from eoa.llm.schemas.product_dossier import (
    ClaimReviewRow,
    CompetitorRow,
    DealRow,
    DossierPatentRow,
    PartnerRow,
    PerformanceRow,
    PlatformRow,
    PriceRow,
    PricingEstimateBlock,
    ProductDossierOut,
    RowConfidenceHe,
    SpecRow,
    TimelineRow,
    VersionRow,
)
from eoa.pipeline import entity_normalize
from eoa.report.claims_gate import gate_sentences

log = structlog.get_logger(__name__)

_NUM_PREDICT = 16000

#: Section 1/6.3: a price figure is only ever extracted from one of these source kinds.
_QUALIFYING_PRICE_SOURCE_KINDS = {"contract", "tender", "budget", "official"}


# --------------------------------------------------------------------------
# LESSONS-2 (2026-09-09, docs/qa/content_review/LESSONS-fable-dossier.md item 7): per-row
# confidence, computed deterministically AFTER a row's own ``cites``/``source_kind`` are already
# grounded -- never authored by the model. Definition (the lessons doc's own table):
#   high   = vendor/datasheet/exchange filing OR two independent (>=2) surviving citations
#   medium = a single surviving citation, or an unverifiable vendor claim (no qualifying
#            source_kind, exactly one citation)
#   low    = inference -- no citation survived grounding at all
# --------------------------------------------------------------------------

#: source_kind values that on their own already mean "vendor/datasheet/exchange filing" --
#: SpecRow/PriceRow's own SourceKind literal reused verbatim (never re-derived independently).
_HIGH_CONFIDENCE_SOURCE_KINDS = {"datasheet", "official", "contract", "tender", "budget"}


def _row_confidence_he(cites: list[int], source_kind: str | None = None) -> RowConfidenceHe:
    """The shared definition every row-confidence call site below applies -- see this section's own
    docstring for the exact three-way rule."""
    if not cites:
        return "low"
    if (source_kind in _HIGH_CONFIDENCE_SOURCE_KINDS) or len(cites) >= 2:
        return "high"
    return "medium"


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
    llm_leg: str | None = None,
) -> ProductDossierOut:
    """``llm_leg`` (PD-cloud-tools, 2026-09-09): ``"<provider>[:<model>][@<power>]"`` or
    ``"local"``/``None`` -- when given, this one structured-extraction call tries that leg first,
    ahead of ``role``'s normally-configured chain (``eoa.llm.chain.build_chain_with_leg_override``),
    which stays the fallback. ``None`` (the default) preserves the exact prior dispatch."""
    data_block = build_data_block(corpus, plan_result)
    #: PD-vocab-extract (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md section 3.3): the effective
    #: vocabulary (common + this run's product line, or common alone with no line set) is handed to
    #: the model as an enumerated list it must fill from verbatim -- never invent/reword a
    #: parameter name. `corpus.product_line` is threaded through from `eoa.dossier.corpus.
    #: build_corpus`'s own param (unchanged call signature elsewhere -- see CorpusResult's own
    #: docstring for why it's read off `corpus` rather than a new function parameter here).
    vocabulary_block = vocabulary_prompt_block_he(effective_vocabulary(corpus.product_line))
    prompt = render(
        "product_dossier_extract",
        product_name=corpus.product_name,
        vendor_suffix=f" ({corpus.vendor})" if corpus.vendor else "",
        aliases_he=", ".join(corpus.aliases) or "אין",
        n_registry=len(corpus.registry),
        data=wrap_data(data_block, "dossier", corpus.product_key),
        vocabulary_block=vocabulary_block,
    )
    chain_override = None
    if llm_leg:
        from eoa.llm.chain import build_chain_with_leg_override

        chain_override = build_chain_with_leg_override(role, llm_leg)
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
        chain_override=chain_override,
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
    confidence = _row_confidence_he(good_cites, row.source_kind)
    return row.model_copy(update={"cites": good_cites, "value": value, "confidence": confidence})


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
    confidence = _row_confidence_he(good_cites)
    return row.model_copy(
        update={
            "cites": good_cites,
            "claimed_value": claimed,
            "tested_or_operational_value": tested,
            "confidence": confidence,
        }
    )


# --------------------------------------------------------------------------
# PD-fix-4 (2026-09-09, item B.2): vague-value nulling. The live run-4 SPECTRO XR dossier kept rows
# like "לייזרים מתקדמים (סוג לא צוין)" / "ייצוב ברמה גבוהה (ערך מספרי לא צוין)" -- these are the
# model admitting it found nothing concrete, dressed up as if they were values. A value carrying one
# of these hand-wavy markers AND no digit anywhere in it is not a fact; nulling it lets the row
# render its own honest "לא נמצא במקורות" placeholder (``eoa.dossier.report``'s existing ``_cell()``
# path) instead of a value that only looks informative.
# --------------------------------------------------------------------------

_VAGUE_VALUE_MARKERS_HE = ("לא צוין", "ערך מספרי לא צוין", "מתקדם", "ברמה גבוהה")
_HAS_DIGIT_RE = re.compile(r"\d")


def _is_vague_value_he(value: str) -> bool:
    """A value is "vague" -- never worth keeping -- when it carries one of
    :data:`_VAGUE_VALUE_MARKERS_HE` and no digit at all; a real number anywhere in the same text
    (e.g. "15 מטר, לייזר מתקדם") means the value is still concrete enough to keep as-is."""
    if not value or not value.strip():
        return False
    if _HAS_DIGIT_RE.search(value):
        return False
    return any(marker in value for marker in _VAGUE_VALUE_MARKERS_HE)


def _null_vague_values(
    specifications: list[SpecRow],
    performance: list[PerformanceRow],
    other_specifications: list[SpecRow],
    dropped: list[DroppedField],
) -> tuple[list[SpecRow], list[PerformanceRow], list[SpecRow]]:
    """Applies :func:`_is_vague_value_he` to every specifications/other_specifications ``value`` and
    every performance ``claimed_value``/``tested_or_operational_value`` -- a hit nulls just that
    field (and, for a specifications/other_specifications row -- which carry only one value each --
    its now-unsupported ``cites`` too); a performance row's two values are independent (one can be
    vague while the other stays a real, grounded number), so its own ``cites`` is left untouched."""
    new_specs: list[SpecRow] = []
    for row in specifications:
        if _is_vague_value_he(row.value):
            _drop(dropped, "specifications.value", "vague_value_no_number", row.value)
            row = row.model_copy(update={"value": "", "cites": []})
        new_specs.append(row)
    new_other: list[SpecRow] = []
    for row in other_specifications:
        if _is_vague_value_he(row.value):
            _drop(dropped, "other_specifications.value", "vague_value_no_number", row.value)
            row = row.model_copy(update={"value": "", "cites": []})
        new_other.append(row)
    new_perf: list[PerformanceRow] = []
    for row in performance:
        update: dict[str, Any] = {}
        if _is_vague_value_he(row.claimed_value):
            _drop(dropped, "performance.claimed_value", "vague_value_no_number", row.claimed_value)
            update["claimed_value"] = ""
        if row.tested_or_operational_value and _is_vague_value_he(row.tested_or_operational_value):
            _drop(
                dropped,
                "performance.tested_or_operational_value",
                "vague_value_no_number",
                row.tested_or_operational_value,
            )
            update["tested_or_operational_value"] = None
        new_perf.append(row.model_copy(update=update) if update else row)
    return new_specs, new_perf, new_other


#: PD-fix-3 (2026-09-08, item 4): a "customer" value the model wrote as a literal placeholder
#: ("—", "-", "לא ידוע", "unknown", ...) is never kept as-is -- see :func:`_normalize_customer`.
_CUSTOMER_PLACEHOLDER_RE = re.compile(
    r"^[\s\-—–_.]*$|^(?:n/?a|unknown|לא\s*ידוע|לא\s*צוין|אין\s*מידע|לא\s*מזוהה)[\s.]*$", re.IGNORECASE
)


def _normalize_customer(customer: str | None) -> str | None:
    """``None`` (never a placeholder string) when ``customer`` is empty or literal placeholder
    text -- the live SPECTRO XR deals table rendered the raw "—" a model wrote into ``customer``
    verbatim, because the general ``_cell()`` placeholder check in ``eoa.dossier.report`` only ever
    catches ``None``/``""``, not a truthy-but-meaningless string. Normalizing here means the
    persisted/API ``customer`` value is a real null: ``eoa.dossier.report`` renders its own "לא
    צוין" for it, and ``eoa.dossier.diff`` independently falls back to (amount, kind) identity
    whenever a customer is missing on either side of a comparison."""
    if not customer:
        return None
    text = customer.strip()
    if not text or _CUSTOMER_PLACEHOLDER_RE.match(text):
        return None
    return text


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
    customer = _normalize_customer(row.customer)
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

    confidence_level = _row_confidence_he(good_cites)
    return row.model_copy(
        update={
            "cites": good_cites,
            "customer": customer,
            "amount": amount,
            "amount_value": amount_value,
            "currency": currency,
            "country": country,
            "region_he": region_he,
            "date": date,
            "date_kind": date_kind,
            "confidence_level": confidence_level,
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
    return row.model_copy(update={"cites": good_cites, "confidence": _row_confidence_he(good_cites)})


def _ground_partner_row(
    row: PartnerRow, valid_ns: set[int], registry_text: dict[int, str], dropped: list[DroppedField]
) -> PartnerRow | None:
    good_cites, _bad = _clean_cites(row.cites, valid_ns)
    text = _text_for_cites(good_cites, registry_text)
    if not _name_grounded(row.partner, text):
        _drop(dropped, "partnerships", "invented/ungrounded partner", row.partner)
        return None
    return row.model_copy(update={"cites": good_cites, "confidence": _row_confidence_he(good_cites)})


# --------------------------------------------------------------------------
# LESSONS-2 item 3 (claims_review): cites-cleaning + drop-if-unsourced (same "nothing else worth
# keeping" rule competitors/partners already apply to an ungrounded name), capped at 5 rows --
# a schema-level ``max_length`` would fail the WHOLE extraction call if the model overshot, so the
# cap is enforced here, deterministically, after the fact instead.
# --------------------------------------------------------------------------

_MAX_CLAIMS_REVIEW_ROWS = 5


def _ground_claim_review_row(
    row: ClaimReviewRow, valid_ns: set[int], dropped: list[DroppedField]
) -> ClaimReviewRow | None:
    good_cites, bad_cites = _clean_cites(row.cites, valid_ns)
    if bad_cites:
        _drop(dropped, "claims_review.cites", f"out of range: {bad_cites}", row.claim_he)
    if not good_cites:
        _drop(dropped, "claims_review", "no valid citation", row.claim_he)
        return None
    return row.model_copy(update={"cites": good_cites})


def _ground_claims_review(
    rows: list[ClaimReviewRow], valid_ns: set[int], dropped: list[DroppedField]
) -> list[ClaimReviewRow]:
    kept = [r for r in (_ground_claim_review_row(row, valid_ns, dropped) for row in rows) if r]
    if len(kept) > _MAX_CLAIMS_REVIEW_ROWS:
        for extra in kept[_MAX_CLAIMS_REVIEW_ROWS:]:
            _drop(dropped, "claims_review", "exceeds 5-row cap", extra.claim_he)
        kept = kept[:_MAX_CLAIMS_REVIEW_ROWS]
    return kept


# --------------------------------------------------------------------------
# LESSONS-2 item 1 (timeline): the model's own LLM-extracted half -- cites-cleaned, drop-if-
# unsourced (same rule as claims_review above), AND drop-if-no-real-date (an undated row has no
# place in a CHRONOLOGICAL table -- section 3's own instruction to the model already asks it to
# omit these, this is the deterministic backstop). The deterministic launch/contract/variant half
# is built separately by :func:`build_timeline` and merged in by ``build_dossier`` after grounding.
# --------------------------------------------------------------------------


def _ground_timeline_row(
    row: TimelineRow, valid_ns: set[int], dropped: list[DroppedField]
) -> TimelineRow | None:
    good_cites, bad_cites = _clean_cites(row.cites, valid_ns)
    if bad_cites:
        _drop(dropped, "timeline.cites", f"out of range: {bad_cites}", row.event_he)
    if not good_cites:
        _drop(dropped, "timeline", "no valid citation", row.event_he)
        return None
    if not (row.date or "").strip():
        _drop(dropped, "timeline", "no date -- not chronological", row.event_he)
        return None
    return row.model_copy(update={"cites": good_cites})


def _ground_timeline(rows: list[TimelineRow], valid_ns: set[int], dropped: list[DroppedField]) -> list[TimelineRow]:
    return [r for r in (_ground_timeline_row(row, valid_ns, dropped) for row in rows) if r]


# --------------------------------------------------------------------------
# LESSONS-2 item 1 (timeline, deterministic half): launch (identity.first_announced), contract
# (every dated deal), variant (every variant with a year) -- merged with the model's own
# LLM-extracted rows (integration/exhibition/milestone) by :func:`build_dossier`, AFTER grounding,
# sorted chronologically (undated rows -- there should be none, both halves are already filtered
# to real dates only -- sort last, defensively).
# --------------------------------------------------------------------------


def _timeline_sort_key(row: TimelineRow) -> tuple[int, str]:
    date = (row.date or "").strip()
    return (0, date) if date else (1, "")


def build_timeline(dossier: ProductDossierOut, corpus: CorpusResult | None = None) -> list[TimelineRow]:
    """The deterministic half, PLUS whatever the model already contributed to
    ``dossier.timeline`` (grounded by :func:`_ground_timeline` upstream) -- merged and sorted
    chronologically. Reads only the already-grounded ``dossier`` itself, plus (optionally)
    ``corpus.programme_deals`` (LESSONS-1's per-platform programme-deal search, landed on
    ``CorpusResult`` -- read via ``getattr`` so this stays a no-op against an older corpus build,
    never a crash)."""
    rows: list[TimelineRow] = list(dossier.timeline)

    identity = dossier.identity
    if identity.first_announced:
        rows.append(
            TimelineRow(
                date=identity.first_announced,
                event_he=f"הכרזה ראשונה על {identity.product_name}.",
                kind="launch",
                cites=identity.cites,
            )
        )

    for deal in dossier.deals:
        if not deal.date or not deal.cites:
            continue
        customer = (deal.customer or "").strip() or "לקוח לא צוין"
        amount_part = f" בהיקף {deal.amount}" if deal.amount else ""
        rows.append(
            TimelineRow(
                date=deal.date,
                event_he=f"עסקה: {customer}{amount_part}.",
                kind="contract",
                cites=deal.cites,
            )
        )

    for version in dossier.variants_and_versions:
        if not version.year or not version.cites:
            continue
        rows.append(
            TimelineRow(date=version.year, event_he=f"גרסה/דגם: {version.name}.", kind="variant", cites=version.cites)
        )

    #: LESSONS-fable-dossier item 3 explicitly names "programme deals" as one of the timeline's
    #: deterministic inputs, alongside deals/variants/identity above.
    for pdeal in getattr(corpus, "programme_deals", None) or []:
        date = pdeal.get("date") if isinstance(pdeal, dict) else getattr(pdeal, "date", None)
        cites = (pdeal.get("cites") if isinstance(pdeal, dict) else getattr(pdeal, "cites", None)) or []
        if not date or not cites:
            continue
        platform = (pdeal.get("platform") if isinstance(pdeal, dict) else getattr(pdeal, "platform", "")) or ""
        customer = (pdeal.get("customer") if isinstance(pdeal, dict) else getattr(pdeal, "customer", "")) or ""
        amount_text = (
            pdeal.get("amount_text") if isinstance(pdeal, dict) else getattr(pdeal, "amount_text", "")
        ) or ""
        parts = [p for p in (customer, platform) if p]
        who = " · ".join(parts) or "לקוח לא צוין"
        amount_part = f" בהיקף {amount_text}" if amount_text else ""
        rows.append(
            TimelineRow(
                date=str(date),
                event_he=f"עסקת תוכנית (רכיב בפלטפורמה): {who}{amount_part}.",
                kind="contract",
                cites=list(cites),
            )
        )

    rows.sort(key=_timeline_sort_key)
    return rows


# --------------------------------------------------------------------------
# LESSONS-2 item 7 (platforms table): built deterministically, never by the model -- from
# ``maturity.platforms_integrated`` (a flat name list, kept as-is) merged with every distinct
# ``DealRow.platform`` (a platform a deal actually names, cited by that deal's own citations). A
# platform named in BOTH sources merges into one row (the deal-derived evidence/cites win, since
# they are more specific than the bare maturity name list).
# --------------------------------------------------------------------------


def build_platforms(dossier: ProductDossierOut) -> list[PlatformRow]:
    by_name: dict[str, PlatformRow] = {}
    for name in dossier.maturity.platforms_integrated:
        name = (name or "").strip()
        if not name or name.casefold() in by_name:
            continue
        by_name[name.casefold()] = PlatformRow(platform=name, cites=dossier.maturity.cites)
    for deal in dossier.deals:
        name = (deal.platform or "").strip()
        if not name or not deal.cites:
            continue
        key = name.casefold()
        existing = by_name.get(key)
        evidence = "מוזכר בעסקה" + (f" עם {deal.customer}" if deal.customer else "") + "."
        if existing is None:
            by_name[key] = PlatformRow(platform=name, integration_evidence_he=evidence, cites=deal.cites)
        else:
            merged_cites = list(dict.fromkeys([*existing.cites, *deal.cites]))
            by_name[key] = existing.model_copy(
                update={"integration_evidence_he": existing.integration_evidence_he or evidence, "cites": merged_cites}
            )
    return list(by_name.values())


# --------------------------------------------------------------------------
# LESSONS-2 item 2 (pricing_estimate): the gate -- kept only when at least one CITED contract total
# with a duration/scope AND at least one CITED market anchor are both present. "duration/scope" is
# read, deliberately conservatively, off a real deal (amount + quantity or platform named) or a
# totalled ``pricing`` row (``basis_he == BASIS_TOTAL_HE``, section 6.3's own canonical "program
# total, not per-unit" form) -- never invented from the estimate block itself.
# --------------------------------------------------------------------------


def _has_cited_contract_total_with_scope(dossier: ProductDossierOut) -> bool:
    for deal in dossier.deals:
        if deal.amount and deal.cites and (deal.quantity or deal.platform):
            return True
    return any(price.basis_he == BASIS_TOTAL_HE and price.cites for price in dossier.pricing)


def _ground_pricing_estimate(
    block: PricingEstimateBlock | None, valid_ns: set[int], dossier_for_gate: ProductDossierOut, dropped: list[DroppedField]
) -> PricingEstimateBlock | None:
    if block is None:
        return None
    assumptions = []
    for a in block.assumptions:
        good, _bad = _clean_cites(a.cites, valid_ns)
        if good:
            assumptions.append(a.model_copy(update={"cites": good}))
        else:
            _drop(dropped, "pricing_estimate.assumptions", "no valid citation", a.text_he)
    anchors = []
    for m in block.market_anchors:
        good, _bad = _clean_cites(m.cites, valid_ns)
        if good:
            anchors.append(m.model_copy(update={"cites": good}))
        else:
            _drop(dropped, "pricing_estimate.market_anchors", "no valid citation", m.product_he)
    grounded_block = block.model_copy(
        update={"assumptions": assumptions, "market_anchors": anchors, "confidence": "low"}
    )
    if not grounded_block.market_anchors:
        _drop(dropped, "pricing_estimate", "no cited market anchor", block.method_he)
        return None
    if not _has_cited_contract_total_with_scope(dossier_for_gate):
        _drop(dropped, "pricing_estimate", "no cited contract total with duration/scope", block.method_he)
        return None
    return grounded_block


def _ground_generic_row(row: Any, valid_ns: set[int], dropped: list[DroppedField], *, label: str) -> Any:
    good_cites, bad_cites = _clean_cites(row.cites, valid_ns)
    if bad_cites:
        _drop(dropped, label, f"cites out of range: {bad_cites}")
    return row.model_copy(update={"cites": good_cites})


def _ground_version_row(
    row: VersionRow, valid_ns: set[int], dropped: list[DroppedField]
) -> VersionRow:
    """LESSONS-2 item 7: same cites-cleaning as :func:`_ground_generic_row`, plus the deterministic
    row-confidence :func:`_row_confidence_he` computes for every other confidence-bearing row --
    ``VersionRow`` needs its own function (not a bigger generic) purely because it's the one caller
    of :func:`_ground_generic_row` that also needs a confidence field set afterward."""
    good_cites, bad_cites = _clean_cites(row.cites, valid_ns)
    if bad_cites:
        _drop(dropped, "variants_and_versions", f"cites out of range: {bad_cites}", row.name)
    return row.model_copy(update={"cites": good_cites, "confidence": _row_confidence_he(good_cites)})


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 1): a patent row's own `relevance_he` is never trusted as the model
# wrote it -- the live SPECTRO XR dossier had rows whose `relevance_he` already admitted "אין אישור
# במקורות לקשר" (no confirmed link in the sources) yet were kept anyway. `relevance_he` here is
# entirely code-derived (`eoa.dossier.corpus.patent_relevance_he` -- the same deterministic rule
# `build_corpus` already applies upstream to keep an ungrounded patent out of the registry in the
# first place): it states only the one concrete, checkable link -- the row's own `assignee` field
# matching the vendor/an alias, or the product name literally appearing in the row's own cited
# source text -- and OVERWRITES whatever text the model wrote. A row with neither link is dropped
# outright, same "nothing else worth keeping" rule `_ground_competitor_row`/`_ground_partner_row`
# already apply to an ungrounded name -- a second, independent line of defense in case a model ever
# cites a patent registry row (already relevance-filtered upstream) but still invents its own
# `assignee`/`title` text that doesn't actually match.
# --------------------------------------------------------------------------


def _ground_patent_row(
    row: DossierPatentRow,
    valid_ns: set[int],
    registry_text: dict[int, str],
    dropped: list[DroppedField],
    *,
    product_name: str,
    vendor: str | None,
    aliases: list[str],
) -> DossierPatentRow | None:
    good_cites, bad_cites = _clean_cites(row.cites, valid_ns)
    if bad_cites:
        _drop(dropped, "patents.cites", f"out of range: {bad_cites}", row.pub_number)
    text = _text_for_cites(good_cites, registry_text)
    relevance = patent_relevance_he(
        assignees=[row.assignee] if row.assignee else [],
        title=row.title,
        source_text=text,
        product_name=product_name,
        vendor=vendor,
        aliases=aliases,
    )
    if relevance is None:
        _drop(dropped, "patents", "no grounded applicant/product-name link", row.pub_number)
        return None
    return row.model_copy(update={"cites": good_cites, "relevance_he": relevance})


# --------------------------------------------------------------------------
# PD-vocab-extract (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md section 3.5): fixed-vocabulary
# post-checks -- applied AFTER the existing per-row grounding above, over the already-grounded
# specifications/performance/other_specifications lists. Every step is independently additive, same
# "a unit that fails a rule is dropped/trimmed/demoted, the rest kept" discipline as the rest of
# this module -- a model that slips past the prompt's own vocabulary instructions is caught here,
# never trusted. Order matters: (1) invalid keys are demoted to other_specifications before anything
# else can key off them, (2) a valid key placed in the wrong table (config/spec_vocabulary.yaml's
# own `table` field, section 3.4) is relocated, (3) parameter_he/metric_he is overwritten from the
# vocabulary's own canonical label for every surviving keyed row, (4) same-key/same-variant
# duplicates collapse, (5) every `required: true` vocabulary param still missing after all of the
# above gets a deterministic placeholder row.
# --------------------------------------------------------------------------


def _spec_row_to_performance_row(row: SpecRow, param: SpecParam) -> PerformanceRow:
    return PerformanceRow(
        metric_he=param.label_he,
        key=row.key,
        claimed_value=row.value,
        conditions_he=row.variant,
        cites=row.cites,
    )


def _performance_row_to_spec_row(row: PerformanceRow, param: SpecParam) -> SpecRow:
    return SpecRow(
        parameter_he=param.label_he,
        key=row.key,
        value=row.claimed_value,
        unit=param.unit or "",
        cites=row.cites,
    )


def _demote_spec_to_other(row: SpecRow) -> SpecRow:
    return row.model_copy(update={"key": ""}) if row.key else row


def _demote_performance_to_other(row: PerformanceRow) -> SpecRow:
    return SpecRow(parameter_he=row.metric_he, key="", value=row.claimed_value, cites=row.cites)


def _split_invalid_keys(
    specifications: list[SpecRow],
    performance: list[PerformanceRow],
    params: dict[str, SpecParam],
    dropped: list[DroppedField],
) -> tuple[list[SpecRow], list[PerformanceRow], list[SpecRow]]:
    """Step 1: ``specifications``/``performance`` only ever hold rows with a REAL vocabulary key
    after this step -- a row whose ``key`` is empty (the model wrote a free-text fact directly into
    the wrong list instead of ``other_specifications``) or non-empty-but-hallucinated is demoted
    into ``other_specifications`` either way (never dropped outright -- the fact itself may still be
    real, only the placement/key assignment was wrong)."""
    kept_specs: list[SpecRow] = []
    demoted: list[SpecRow] = []
    for row in specifications:
        if not row.key:
            _drop(dropped, "specifications.key", "missing_key_demoted_to_other", row.parameter_he)
            demoted.append(_demote_spec_to_other(row))
        elif row.key not in params:
            _drop(dropped, "specifications.key", "invalid_vocabulary_key", row.key)
            demoted.append(_demote_spec_to_other(row))
        else:
            kept_specs.append(row)
    kept_perf: list[PerformanceRow] = []
    for row in performance:
        if not row.key:
            _drop(dropped, "performance.key", "missing_key_demoted_to_other", row.metric_he)
            demoted.append(_demote_performance_to_other(row))
        elif row.key not in params:
            _drop(dropped, "performance.key", "invalid_vocabulary_key", row.key)
            demoted.append(_demote_performance_to_other(row))
        else:
            kept_perf.append(row)
    return kept_specs, kept_perf, demoted


def _relocate_by_table(
    specifications: list[SpecRow],
    performance: list[PerformanceRow],
    params: dict[str, SpecParam],
    dropped: list[DroppedField],
) -> tuple[list[SpecRow], list[PerformanceRow]]:
    """Step 2: a valid-keyed row the model placed in the wrong table (config/spec_vocabulary.yaml's
    own ``table`` field, section 3.4 -- DATA, not model judgment) is moved to the correct one. This
    is the deterministic fix for the id=1/2/3 drift the whole vocabulary exists to close: a fact
    like ``common.size_to_performance_ratio`` always lands in ``performance`` now, never split
    across both tables run to run."""
    final_specs: list[SpecRow] = []
    moved_to_perf: list[PerformanceRow] = []
    for row in specifications:
        param = params.get(row.key) if row.key else None
        if param is not None and param.table == "performance":
            _drop(dropped, "specifications", "table_relocated_to_performance", row.key)
            moved_to_perf.append(_spec_row_to_performance_row(row, param))
        else:
            final_specs.append(row)
    final_perf: list[PerformanceRow] = []
    moved_to_spec: list[SpecRow] = []
    for row in performance:
        param = params.get(row.key) if row.key else None
        if param is not None and param.table == "specifications":
            _drop(dropped, "performance", "table_relocated_to_specifications", row.key)
            moved_to_spec.append(_performance_row_to_spec_row(row, param))
        else:
            final_perf.append(row)
    final_specs.extend(moved_to_spec)
    final_perf.extend(moved_to_perf)
    return final_specs, final_perf


def _normalize_labels(
    specifications: list[SpecRow], performance: list[PerformanceRow], params: dict[str, SpecParam]
) -> tuple[list[SpecRow], list[PerformanceRow]]:
    """Step 3: for every row with a valid key, ``parameter_he``/``metric_he`` is overwritten from
    the vocabulary's own canonical ``label_he`` unconditionally -- never trust the model's own copy,
    even when it typed it correctly (one canonical write path, not a "matches" check)."""
    new_specs = [
        row.model_copy(update={"parameter_he": params[row.key].label_he}) if row.key in params else row
        for row in specifications
    ]
    new_perf = [
        row.model_copy(update={"metric_he": params[row.key].label_he}) if row.key in params else row
        for row in performance
    ]
    return new_specs, new_perf


def _collapse_duplicate_spec_keys(rows: list[SpecRow], dropped: list[DroppedField]) -> list[SpecRow]:
    """Step 4 (specifications): two rows sharing a key AND the same/empty ``variant`` collapse to
    whichever carries a non-empty ``value`` (first one wins on a further tie); different non-empty
    ``variant`` values are kept as separate, legitimate per-variant rows."""
    result: list[SpecRow] = []
    seen: dict[tuple[str, str], int] = {}
    for row in rows:
        if not row.key:
            result.append(row)
            continue
        ident = (row.key, row.variant or "")
        idx = seen.get(ident)
        if idx is None:
            seen[ident] = len(result)
            result.append(row)
            continue
        _drop(dropped, "specifications", "duplicate_vocabulary_key", row.key)
        if not result[idx].value and row.value:
            result[idx] = row
    return result


def _collapse_duplicate_performance_keys(
    rows: list[PerformanceRow], dropped: list[DroppedField]
) -> list[PerformanceRow]:
    """Step 4 (performance): same rule as :func:`_collapse_duplicate_spec_keys`, using
    ``conditions_he`` as the per-variant differentiator (``PerformanceRow`` has no ``variant`` field
    of its own)."""
    result: list[PerformanceRow] = []
    seen: dict[tuple[str, str], int] = {}
    for row in rows:
        if not row.key:
            result.append(row)
            continue
        ident = (row.key, row.conditions_he or "")
        idx = seen.get(ident)
        if idx is None:
            seen[ident] = len(result)
            result.append(row)
            continue
        _drop(dropped, "performance", "duplicate_vocabulary_key", row.key)
        if not result[idx].claimed_value and row.claimed_value:
            result[idx] = row
    return result


def _backfill_required(
    specifications: list[SpecRow], performance: list[PerformanceRow], product_line: str | None
) -> tuple[list[SpecRow], list[PerformanceRow]]:
    """Step 5: every ``required: true`` vocabulary param still missing a keyed row (in its own
    ``table``) after every step above gets a deterministic placeholder row (``value``/
    ``claimed_value`` == ``""``, ``cites`` == ``[]``) -- renders as "לא נמצא במקורות" via the
    existing ``_cell()`` path in ``eoa.dossier.report``, no renderer change needed."""
    have_spec = {row.key for row in specifications if row.key}
    have_perf = {row.key for row in performance if row.key}
    new_specs = list(specifications)
    new_perf = list(performance)
    for param in effective_vocabulary(product_line):
        if not param.required:
            continue
        if param.table == "performance":
            if param.key not in have_perf:
                new_perf.append(PerformanceRow(metric_he=param.label_he, key=param.key, claimed_value=""))
        elif param.key not in have_spec:
            new_specs.append(SpecRow(parameter_he=param.label_he, key=param.key, value="", unit=param.unit or ""))
    return new_specs, new_perf


def _finalize_other_specifications(
    rows: list[SpecRow], product_line: str | None, dropped: list[DroppedField]
) -> list[SpecRow]:
    """Every ``other_specifications`` row always has ``key == ""`` (section 3.4 -- enforced here,
    not just relied on from the model). Also runs the promoted synonym matcher (section 3.2) as a
    non-destructive QA signal: a row whose own ``parameter_he`` matches a known vocabulary
    label/synonym is logged (never auto-reclassified -- the model already had the full vocabulary
    and chose not to key this row; a non-trivial rate of these hints the vocabulary itself needs a
    new entry, section 9 item 3, not a silent code-side override)."""
    final: list[SpecRow] = []
    for row in rows:
        row = row.model_copy(update={"key": ""}) if row.key else row
        hit = match_key_by_synonym(row.parameter_he, product_line)
        if hit:
            _drop(dropped, "other_specifications", f"matches_known_vocabulary_key:{hit}", row.parameter_he)
        final.append(row)
    return final


def apply_vocabulary(
    specifications: list[SpecRow],
    performance: list[PerformanceRow],
    other_specifications: list[SpecRow],
    *,
    product_line: str | None,
    dropped: list[DroppedField],
) -> tuple[list[SpecRow], list[PerformanceRow], list[SpecRow]]:
    """The single entry point for the section-3.5 vocabulary post-checks, steps 1-5 in order (see
    each helper's own docstring). Public (not ``_``-prefixed) so tests can exercise it directly
    without a full ``ground_dossier`` draft."""
    params = param_by_key(product_line)
    specifications, performance, demoted = _split_invalid_keys(specifications, performance, params, dropped)
    other_specifications = [*other_specifications, *demoted]
    specifications, performance = _relocate_by_table(specifications, performance, params, dropped)
    specifications, performance = _normalize_labels(specifications, performance, params)
    specifications = _collapse_duplicate_spec_keys(specifications, dropped)
    performance = _collapse_duplicate_performance_keys(performance, dropped)
    specifications, performance = _backfill_required(specifications, performance, product_line)
    other_specifications = _finalize_other_specifications(other_specifications, product_line, dropped)
    return specifications, performance, other_specifications


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
    other_specifications = [
        _ground_spec_row(r, valid_ns, registry_text, dropped) for r in draft.other_specifications
    ]
    specifications, performance, other_specifications = _null_vague_values(
        specifications, performance, other_specifications, dropped
    )
    specifications, performance, other_specifications = apply_vocabulary(
        specifications,
        performance,
        other_specifications,
        product_line=corpus.product_line,
        dropped=dropped,
    )
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
    variants = [_ground_version_row(r, valid_ns, dropped) for r in draft.variants_and_versions]
    claims_review = _ground_claims_review(draft.claims_review, valid_ns, dropped)
    timeline = _ground_timeline(draft.timeline, valid_ns, dropped)
    patents = [
        r
        for r in (
            _ground_patent_row(
                r,
                valid_ns,
                registry_text,
                dropped,
                product_name=corpus.product_name,
                vendor=corpus.vendor,
                aliases=corpus.aliases,
            )
            for r in draft.patents
        )
        if r
    ]
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
            "other_specifications": other_specifications,
            "performance": performance,
            "deals": deals,
            "pricing": pricing,
            "competitors": competitors,
            "partnerships": partnerships,
            "variants_and_versions": variants,
            "patents": patents,
            "tenders_and_forecasts": tenders,
            "claims_review": claims_review,
            "timeline": timeline,
        }
    )
    # LESSONS-2 items 1/2/7: the deterministic timeline half, the pricing_estimate gate, and the
    # platforms table all need the ALREADY-grounded dossier (deals/variants/identity/maturity) as
    # their input -- computed as one more pass over ``grounded`` itself, not the original ``draft``.
    full_timeline = build_timeline(grounded, corpus)
    pricing_estimate = _ground_pricing_estimate(draft.pricing_estimate, valid_ns, grounded, dropped)
    platforms = build_platforms(grounded)
    grounded = grounded.model_copy(
        update={"timeline": full_timeline, "pricing_estimate": pricing_estimate, "platforms": platforms}
    )
    return GroundingResult(dossier=grounded, dropped=dropped)


# --------------------------------------------------------------------------
# PD-fix-4 (2026-09-09, item B.1): fact-retention scan + one bounded re-ask. Run 4's cited sources
# genuinely carried concrete specs (7-inch spotter, up to 9 digital sensors, Jetson Xavier...) that
# never made it into ``other_specifications`` at all -- the prompt's own rule 2 ("a real fact
# matching no vocabulary key goes to other_specifications") was simply not followed. Rather than
# trust the model harder, this scans every CITED source's own text for a number+unit pattern and
# checks whether that same number is already represented somewhere in the extraction's own kept
# values; a snippet that isn't gets logged (``dossier.fact_missing``, operator-visible even if the
# re-ask below can't recover it) and offered back to the model exactly once, scoped to only those
# snippets -- never a second full extraction pass.
# --------------------------------------------------------------------------

#: Deliberately narrow unit/count vocabulary (Hebrew + English) -- a false negative here just means
#: one fewer re-ask candidate (the source text itself is never discarded), a false positive just
#: means one wasted re-ask line, so precision is not critical either way.
_SPEC_FACT_RE = re.compile(
    r"\d[\d.,]*[\s-]*"
    r"(?:mm|מ\"מ|מ״מ|inch(?:es)?|אינץ['\"׳]|kg|ק\"ג|ק״ג|קג\b|°|km|ק\"מ|ק״מ|hz|הרץ|µm|מיקרומטר|"
    r"w\b|וואט|חיישנ(?:ים|י)?|sensors?)",
    re.IGNORECASE,
)

_FACT_SNIPPET_CONTEXT_CHARS = 40
_FACT_RETENTION_MAX_SNIPPETS = 20


def _spec_like_snippets(text: str) -> list[tuple[str, str]]:
    """Every :data:`_SPEC_FACT_RE` match in ``text`` as ``(matched_fact, context_snippet)`` --
    ``matched_fact`` is just the number+unit itself (what groundedness is checked against: a wider
    window would mix in unrelated nearby digits, e.g. a date, into one nonsensical combined number);
    ``context_snippet`` is a short window around it (what a human/re-ask prompt actually reads).
    Deduped (whitespace-insensitive, case-insensitive) by ``matched_fact``."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in _SPEC_FACT_RE.finditer(text or ""):
        fact = m.group(0)
        key = re.sub(r"\s+", "", fact).casefold()
        if key in seen:
            continue
        seen.add(key)
        start = max(0, m.start() - _FACT_SNIPPET_CONTEXT_CHARS)
        end = min(len(text), m.end() + _FACT_SNIPPET_CONTEXT_CHARS)
        snippet = text[start:end].strip()
        if snippet:
            out.append((fact, snippet))
    return out


def _all_kept_value_texts(dossier: ProductDossierOut) -> str:
    """Every ``value``/``claimed_value``/``tested_or_operational_value`` the extraction actually
    kept, joined into one text -- what :func:`find_missing_spec_facts` checks a source's own
    spec-like snippet against before deciding it was never captured anywhere."""
    parts: list[str] = [row.value for row in dossier.specifications]
    parts.extend(row.value for row in dossier.other_specifications)
    for row in dossier.performance:
        parts.append(row.claimed_value)
        if row.tested_or_operational_value:
            parts.append(row.tested_or_operational_value)
    return " \n ".join(p for p in parts if p)


def find_missing_spec_facts(
    corpus: CorpusResult, plan_result: PlanResult, dossier: ProductDossierOut
) -> list[dict[str, Any]]:
    """Every spec-like snippet sitting in a CITED (real registry number) source's own text whose
    number is not grounded (:func:`_digits_grounded`, the same digit-boundary-safe check the row
    post-checks already use) anywhere in the dossier's own kept values -- one entry per missing
    snippet, ``{"n", "topic", "snippet"}``, in registry order, capped at
    :data:`_FACT_RETENTION_MAX_SNIPPETS` (a re-ask prompt scoped to everything would just be a second
    full extraction pass, exactly what this is meant not to be)."""
    registry_text = _registry_text_by_n(corpus, plan_result)
    represented = _all_kept_value_texts(dossier)
    topic_by_n: dict[int, str] = {}
    for finding in plan_result.findings:
        for s in finding.source_ns:
            if s.get("n") is not None:
                topic_by_n[s["n"]] = finding.key
    missing: list[dict[str, Any]] = []
    for n in sorted(registry_text):
        text = registry_text[n]
        for fact, snippet in _spec_like_snippets(text):
            if _digits_grounded(fact, represented):
                continue
            missing.append({"n": n, "topic": topic_by_n.get(n, ""), "snippet": snippet[:200]})
            if len(missing) >= _FACT_RETENTION_MAX_SNIPPETS:
                return missing
    return missing


class _SupplementalSpecsOut(BaseModel):
    """The re-ask call's own tiny schema -- ``other_specifications`` rows only, nothing else in the
    dossier is ever touched by this second call."""

    other_specifications: list[SpecRow] = Field(default_factory=list)


def reask_missing_facts(
    missing: list[dict[str, Any]],
    corpus: CorpusResult,
    *,
    role: str = "resident",
    interactive: bool = False,
    llm_leg: str | None = None,
) -> list[SpecRow]:
    """Exactly one follow-up structured-extraction call, scoped ONLY to ``missing``'s own snippets --
    asks the model to route each real fact into a proper ``other_specifications`` row (a snippet
    that turns out to be a false-positive unit match is simply omitted from the reply). Returns
    ``[]`` (never raises) on any failure -- a failed re-ask still leaves every entry logged as
    ``dossier.fact_missing`` by the caller, it just doesn't recover this run's own data for it."""
    if not missing:
        return []
    lines = [f"[{m['n']}] {m['snippet']}" for m in missing]
    prompt = (
        "להלן קטעי טקסט ממקורות שכבר צוטטו בסקירה (מספר המקור בסוגריים מרובעים בתחילת כל שורה), "
        "שכל אחד מהם מכיל מספר/יחידה שלא נכלל בשום שורת מפרט/ביצועים קיימת בסקירה:\n\n"
        + wrap_data("\n".join(lines), "dossier_missing_facts", corpus.product_key)
        + f"\n\nעבור כל קטע שמכיל עובדה טכנית אמיתית עבור {corpus.product_name}"
        + (f" ({corpus.vendor})" if corpus.vendor else "")
        + ": כתוב שורת other_specifications אחת (parameter_he מתאים לעובדה, value כפי שפורסם "
        "במקור כולל יחידות, cites=[מספר המקור מהסוגריים המרובעים בתחילת אותה שורה]). קטע שאינו "
        "עובדה טכנית אמיתית עבור מוצר זה (למשל התאמה מקרית של מספר לא קשור) -- פשוט השמט אותו, "
        "אל תמציא. החזר JSON בלבד לפי הסכמה."
    )
    chain_override = None
    if llm_leg:
        from eoa.llm.chain import build_chain_with_leg_override

        chain_override = build_chain_with_leg_override(role, llm_leg)
    try:
        out = chat_structured(
            role,
            _SupplementalSpecsOut,
            [
                {"role": "system", "content": render("system_analyst", data_guard=DATA_GUARD_SYSTEM)},
                {"role": "user", "content": prompt},
            ],
            task="report",
            interactive=interactive,
            options={"temperature": 0.1, "num_predict": 2000},
            chain_override=chain_override,
        )
    except Exception as exc:
        log.warning("dossier.fact_missing_reask_failed", error=str(exc)[:200])
        return []
    return out.other_specifications


def apply_fact_retention(
    grounding: GroundingResult,
    corpus: CorpusResult,
    plan_result: PlanResult,
    *,
    role: str = "resident",
    interactive: bool = False,
    llm_leg: str | None = None,
) -> GroundingResult:
    """The single entry point ``build_dossier`` calls for item B.1: scans, logs every miss, re-asks
    once, grounds whatever comes back (same :func:`_ground_spec_row` post-check every other
    ``other_specifications`` row goes through -- a re-ask reply is not trusted any harder than the
    original extraction), and appends only the rows that survive grounding."""
    missing = find_missing_spec_facts(corpus, plan_result, grounding.dossier)
    if not missing:
        return grounding
    for m in missing:
        log.info("dossier.fact_missing", n=m["n"], topic=m["topic"], snippet=m["snippet"])
    supplemental = reask_missing_facts(missing, corpus, role=role, interactive=interactive, llm_leg=llm_leg)
    if not supplemental:
        return grounding
    valid_ns = _valid_ns(corpus)
    registry_text = _registry_text_by_n(corpus, plan_result)
    grounded_supplemental = [
        r
        for r in (
            _ground_spec_row(row.model_copy(update={"key": ""}), valid_ns, registry_text, grounding.dropped)
            for row in supplemental
        )
        if r.value
    ]
    if not grounded_supplemental:
        return grounding
    updated = grounding.dossier.model_copy(
        update={"other_specifications": [*grounding.dossier.other_specifications, *grounded_supplemental]}
    )
    return GroundingResult(dossier=updated, dropped=grounding.dropped)


# --------------------------------------------------------------------------
# PD-fix-4 (2026-09-09, item B.3): fact retention across runs. A rerun must never know LESS than the
# previous run of the same ``product_key`` -- when this run's own keyed row is null but a previous
# run already had a grounded value for that same key, and that value's own cited source is still
# registered (reachable) in THIS run's own registry, carry it forward rather than silently losing
# it. The previous row's citation numbers are never reused verbatim (they numbered a DIFFERENT
# run's registry) -- each is resolved back to its own URL and re-numbered against THIS run's
# registry; a citation whose URL isn't registered this run at all is simply not carried (never
# "reachable/registered" per the rule), and a value with no citation left after that isn't carried
# either (an unsourced carried fact would be worse than an honest null).
# --------------------------------------------------------------------------

CARRIED_FROM_RUN_TAG_HE = "מהסקירה הקודמת"


def _previous_source_url_by_n(previous_row: dict[str, Any] | None) -> dict[int, str]:
    if not previous_row:
        return {}
    sources = previous_row.get("sources") or []
    return {s.get("n"): s.get("url") for s in sources if s.get("n") is not None and s.get("url")}


def _current_n_by_normalized_url(corpus: CorpusResult) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in corpus.registry:
        url, n = r.get("url"), r.get("n")
        if url and n is not None:
            out.setdefault(normalize_url(url), n)
    return out


def _remap_prev_cites(
    prev_cites: list[int] | None, prev_url_by_n: dict[int, str], current_n_by_norm: dict[str, int]
) -> list[int]:
    """Every previous-run citation number that still resolves to a URL registered in THIS run's own
    registry, re-numbered to this run's own ``n`` -- ``[]`` when none do (nothing "reachable/
    registered" to carry the value under)."""
    out: list[int] = []
    for n in prev_cites or []:
        url = prev_url_by_n.get(n)
        if not url:
            continue
        cur_n = current_n_by_norm.get(normalize_url(url))
        if cur_n is not None and cur_n not in out:
            out.append(cur_n)
    return out


def _append_carried_tag(existing: str) -> str:
    tag = f"({CARRIED_FROM_RUN_TAG_HE})"
    if not existing:
        return tag
    if CARRIED_FROM_RUN_TAG_HE in existing:
        return existing
    return f"{existing}; {tag}"


def carry_forward_missing_specs(
    dossier: ProductDossierOut, corpus: CorpusResult
) -> tuple[ProductDossierOut, int]:
    """Fills a null-value keyed specifications/performance row from the previous dossier of the same
    ``product_key`` (``corpus.previous``, the raw ``product_dossiers`` row) whenever that previous
    run had a real, non-vague (:func:`_is_vague_value_he` -- a previous run predating item B.2 can
    itself carry a hand-wavy "לא צוין"/"מתקדם"-with-no-digit value; never resurrect that through the
    back door) value for the same ``key`` AND at least one of its own citations still resolves into
    this run's own registry (:func:`_remap_prev_cites`). The carried value's ``variant``/
    ``conditions_he`` gets a ``"(מהסקירה הקודמת)"`` tag -- ``eoa.dossier.spec_render`` already joins
    that field into the rendered cell, so no renderer change is needed; ``eoa.dossier.diff`` compares
    only ``value``/``claimed_value``/``tested_or_operational_value`` (never ``variant``/
    ``conditions_he``), and the carried value is verbatim-identical to the previous run's own value,
    so a carried row is never reported as a change either -- both "for free", by construction.
    Returns ``(dossier, 0)`` unchanged when there is no previous dossier, or it carries no `sources`
    at all to re-resolve citations against."""
    previous_row = corpus.previous
    if not previous_row:
        return dossier, 0
    prev_url_by_n = _previous_source_url_by_n(previous_row)
    if not prev_url_by_n:
        return dossier, 0
    previous_data = previous_row.get("data") or {}
    current_n_by_norm = _current_n_by_normalized_url(corpus)
    prev_specs_by_key = {r.get("key"): r for r in (previous_data.get("specifications") or []) if r.get("key")}
    prev_perf_by_key = {r.get("key"): r for r in (previous_data.get("performance") or []) if r.get("key")}

    carried = 0
    new_specs: list[SpecRow] = []
    for row in dossier.specifications:
        prev_row = prev_specs_by_key.get(row.key) if row.key and not row.value else None
        prev_value = prev_row.get("value") if prev_row else None
        # A previous run that predates item B.2 (vague-value nulling) can itself carry a vague
        # value ("לא צוין"/"מתקדם" with no digit) -- never resurrect that through the back door;
        # the point of carrying forward is real facts, not stale hand-waving.
        if not prev_value or _is_vague_value_he(prev_value):
            new_specs.append(row)
            continue
        new_cites = _remap_prev_cites(prev_row.get("cites"), prev_url_by_n, current_n_by_norm)
        if not new_cites:
            new_specs.append(row)
            continue
        carried += 1
        new_specs.append(
            row.model_copy(
                update={
                    "value": prev_value,
                    "unit": prev_row.get("unit") or row.unit,
                    "source_kind": prev_row.get("source_kind") or row.source_kind,
                    "cites": new_cites,
                    "variant": _append_carried_tag(row.variant),
                }
            )
        )

    new_perf: list[PerformanceRow] = []
    for row in dossier.performance:
        prev_row = prev_perf_by_key.get(row.key) if row.key and not row.claimed_value else None
        prev_value = prev_row.get("claimed_value") if prev_row else None
        if not prev_value or _is_vague_value_he(prev_value):
            new_perf.append(row)
            continue
        new_cites = _remap_prev_cites(prev_row.get("cites"), prev_url_by_n, current_n_by_norm)
        if not new_cites:
            new_perf.append(row)
            continue
        carried += 1
        new_perf.append(
            row.model_copy(
                update={
                    "claimed_value": prev_value,
                    "tested_or_operational_value": row.tested_or_operational_value
                    or prev_row.get("tested_or_operational_value"),
                    "cites": new_cites,
                    "conditions_he": _append_carried_tag(row.conditions_he),
                }
            )
        )

    if carried == 0:
        return dossier, 0
    return dossier.model_copy(update={"specifications": new_specs, "performance": new_perf}), carried


def build_dossier(
    corpus: CorpusResult,
    plan_result: PlanResult,
    *,
    role: str = "resident",
    interactive: bool = False,
    llm_leg: str | None = None,
) -> GroundingResult:
    """``extract`` + ``ground`` in one call -- the shape ``eoa.dossier.report`` uses. A
    :class:`~eoa.errors.LLMOutputError` propagates (the caller decides the fallback -- an empty,
    honest "not_found" dossier -- rather than this module inventing one). ``llm_leg`` (PD-cloud-
    tools, 2026-09-09) is forwarded verbatim to :func:`extract_dossier`.

    PD-fix-4 (2026-09-09, items B.1/B.3): after the deterministic grounding pass, the fact-retention
    scan + one bounded re-ask (:func:`apply_fact_retention`) recovers a real cited fact the model
    silently dropped instead of routing to ``other_specifications``; then :func:`carry_forward_
    missing_specs` fills any keyed row still null from the previous dossier of the same
    ``product_key``, when that previous value's own citation still resolves in this run's registry.
    Both run unconditionally (a no-op, zero extra calls, when there is nothing to do) -- every
    existing caller's behavior is unchanged for a first-run dossier with no previous data and no
    missing facts."""
    try:
        draft = extract_dossier(corpus, plan_result, role=role, interactive=interactive, llm_leg=llm_leg)
    except LLMOutputError:
        raise
    grounding = ground_dossier(draft, corpus, plan_result)
    grounding = apply_fact_retention(
        grounding, corpus, plan_result, role=role, interactive=interactive, llm_leg=llm_leg
    )
    carried_dossier, _carried_count = carry_forward_missing_specs(grounding.dossier, corpus)
    if carried_dossier is not grounding.dossier:
        grounding = GroundingResult(dossier=carried_dossier, dropped=grounding.dropped)
    return grounding


__all__ = [
    "CARRIED_FROM_RUN_TAG_HE",
    "DroppedField",
    "GroundingResult",
    "apply_fact_retention",
    "apply_vocabulary",
    "build_data_block",
    "build_dossier",
    "build_platforms",
    "build_timeline",
    "carry_forward_missing_specs",
    "extract_dossier",
    "find_missing_spec_facts",
    "ground_dossier",
    "parse_amount_he",
    "reask_missing_facts",
    "split_country_region",
]
