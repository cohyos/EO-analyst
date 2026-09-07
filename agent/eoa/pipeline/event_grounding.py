"""Post-extraction grounding guard for the ``events`` structured-extraction stage
(``eoa.pipeline.analyze``'s ``persist_analysis``/``insert_event`` path and
``eoa.report.bd_territory.collect_platform_events``'s downstream platform-row derivation).

Root cause (docs/qa/content_review/CR-factcheck.md, independent fact-check pass 2026-09-07):
``eoa.pipeline.analysis_grounding`` already grounds ``summary_he``/``so_what_he``/``key_facts``/
``entities_mentioned`` before persistence, but the *structured* ``events`` extraction (``EventOut``,
``eoa.llm.schemas.analysis``) had no equivalent check at all. Two examples, both from item 81 (a
Globes article about Anduril appointing Amikam Norkin to head its Israel operation, which *also*,
in a later paragraph, mentions a $10B financing round in progress and, in an earlier paragraph, a
~$100B *valuation* Anduril was reportedly seeking):

* events.id 22 -- extracted as ``kind='m_and_a'``, ``amount_usd=10_000_000_000``,
  ``customer='Israel Ministry of Defense and IDF'``, from a source that describes an executive
  appointment, no transaction, and no IMOD/IDF counterparty of any kind. This fabricated row was
  then promoted into monthly's own "top 10 events by value" table and into bd_il's procurement
  pipeline table as a fictitious "$10B fighter-jet / targeting-pod" opportunity signal (bd_il's own
  ``collect_platform_events``, see below, compounded the error a second time by attaching an
  unrelated platform-payload template to it).
* events.id 23 -- extracted as ``kind='m_and_a'`` for the *same* $10B figure, this time correctly
  describing the still-open financing round ("the size of the round has reached about $10
  billion") -- a real, source-grounded number, but the wrong ``kind`` (a financing round is
  ``investment``, not ``m_and_a`` -- there is no acquisition/merger in this source at all) and
  conflated by the ranking/table code with event 22's fabricated row into "two $10B M&A events".

This module's job is narrower and more mechanical than ``analysis_grounding``'s prose-sentence
surgery: an ``events`` row is a handful of *discrete, independently checkable* fields (an amount, a
currency, a customer, a handful of party names, a ``kind`` enum value), so :func:`ground_event`
validates each field against the item's own source text and either clears an ungrounded field or
reclassifies ``kind`` to the value the source vocabulary actually supports -- never rewrites prose.

Five rules (task brief numbering):

  (a) **Amount/currency grounding** (:func:`_amount_grounded`) -- ``amount_usd`` must be traceable
      to a number in the source at the *same magnitude* (accepts "10 billion"/"$10bn"/
      "€3.1 billion" digit-plus-magnitude-word forms, and a bare grouped-digit literal as a
      fallback). A number immediately preceded (within a short window) by a valuation/market-size
      cue ("valuation of", "valued at", "market cap", "שווי של", ...) is a company valuation, not a
      deal amount, and is dropped even when the digits themselves are grounded -- this is what
      event 22 needs (its $10B is real digits in the source, but they describe event 23's
      financing-round size, not event 22's non-existent transaction) and, symmetrically, what
      keeps event 23's *own* $10B (immediately preceded by "the size of the round has reached
      about", not any valuation cue) intact. The before-only window is deliberate: a valuation
      figure is conventionally introduced ("a valuation of $X", "שווי של X") with the cue
      *preceding* the number; checking only before, not after, is what tells apart "$10 billion,
      based on a company valuation of about $100 billion" (only the $100B is a valuation) from a
      naive whole-sentence scan that would flag both.
  (b) **Party/customer grounding** (:func:`_name_grounded`) -- every non-empty ``customer`` and
      ``parties`` entry must appear literally in the source (case-insensitive), or resolve
      (``eoa.pipeline.entity_normalize.resolve_canonical``) to a watchlist/curated-org record at
      least one of whose *other* aliases the source mentions (the "IAI" abbreviation vs. "Israel
      Aerospace Industries" spelled out case). A handful of literal placeholder strings a model
      sometimes writes instead of leaving the field empty ("לא צוין", "not specified", "n/a", ...)
      are normalised to ``None`` up front and never count as a grounding failure.
  (c) **Kind reclassification** (:func:`_reclassify_kind`) -- checked against the *event's own*
      extracted text (``title``/``summary_he``/``program``), not the item's full source corpus.
      This is a deliberate, narrower scope than "the source" the task brief names: checking the
      full corpus produces exactly the same false-positive shape as the ``collect_platform_events``
      bug this same round fixes (see :mod:`eoa.report.bd_territory`) -- item 81's corpus contains
      the word "acquisitions" in an unrelated sentence ("the company... examined possible
      collaborations, investments, and acquisitions") that has nothing to do with event 22, and a
      corpus-wide check would have let event 22 keep its fabricated 'm_and_a' kind for exactly that
      reason. The event's own narrative is the right granularity: a 'm_and_a' event without
      acquisition/merger/takeover vocabulary in its own text is downgraded to 'partnership'
      (co-marketing/teaming vocabulary present), 'investment' (financing-round vocabulary present),
      'appointment' (personnel-appointment vocabulary present), or 'other' (none of the above); a
      'regulation'/'other' event whose own text is an earnings/backlog/financial-results disclosure
      is reclassified to 'financial_results'. 'appointment' and 'financial_results' are new
      ``events.kind`` values (``db/migrations/versions/0030_events_grounding.py`` -- neither
      existing 9-value enum member has an honest slot for either).
  (d) **Title-only/empty-body items** (:func:`_is_thin_source`) -- an item whose own text (title +
      clean_text + raw_text) carries under :data:`_THIN_SOURCE_MIN_CHARS` characters of real
      content may not produce an event with an ``amount_usd`` or ``customer`` at all (regardless of
      whether that field would otherwise ground): there is no real body for a number/name to be
      meaningfully traceable *to*, only a headline a model can free-associate numbers onto.
  (e) **Confidence cap** -- ``confidence`` is capped at 0.5 whenever *any* field above was actually
      dropped (kind reclassification alone, with nothing else lost, does not trigger the cap --
      event 23's $10B stays at its original 0.7 confidence once only its ``kind`` changes).

:func:`ground_event` is the single per-event entry point (item dict + event dict/``EventOut`` in,
:class:`GroundedEvent` or ``None`` out -- ``None`` is never returned by the rules above; every field
that fails is dropped from an otherwise-kept event, matching the task brief's "return the cleaned
event or None" contract literally: the *caller* (``eoa.pipeline.analyze.persist_analysis``, once
wired -- see the module's own docstring for the one-line change; ``scripts/repair_round14_events.py``
today) decides whether a maximally-stripped event -- e.g. an 'other'-kind row with no amount, no
customer and a title -- is still worth inserting, this module only ever strips fields it cannot
justify). :func:`reconcile_events` is the separate, list-level cross-source merge (task rule 2): a
function, given every currently-grounded event, groups and merges rows describing the same
underlying deal reported by more than one source item (companies overlap + same kind + an
*actually-confirmed* date match within :data:`_DATE_MERGE_WINDOW_DAYS` days OR amount match within
:data:`_AMOUNT_MERGE_TOLERANCE` of each other -- an OR, not an AND, of the two signals, but each
signal itself requires both sides to genuinely carry a date/amount, never "either side is unknown so
call it a match": this is what reconciles item 90's "€3.1bn ($3.6bn), signed" event with items
150/155's "€3.5bn ($4bn), approved" events into one row even though their extracted *dates* are 39
days apart (their *amounts* are within tolerance, which is what actually merges them), while still
refusing to merge two same-kind events that share nothing but one common party name and carry no
date/amount evidence at all on either side -- see :func:`_mergeable`'s own docstring for the exact
false-merge case that requirement exists to rule out, found live while testing this module).

Wiring instruction for ``eoa.pipeline.analyze.persist_analysis`` (owned by another agent this
round; not editable here -- see the repo-level task brief). In the ``for ev in _dedup_events(...)``
loop, immediately after the existing::

    amount_usd, magnitude = normalize_amount_from_source(ev.amount_usd, _source_text_for_amounts(item))

insert::

    from eoa.pipeline.event_grounding import ground_event
    grounded = ground_event(
        item,
        {
            "kind": ev.kind, "title": ev.title, "date": ev.date, "amount_usd": amount_usd,
            "currency": ev.currency, "parties": event_parties, "customer": ev.customer,
            "program": ev.program, "summary_he": ev.summary_he, "confidence": ev.confidence,
        },
    )
    if grounded is None:
        log.info("event.dropped", item_id=item["id"], title=(ev.title or "")[:160])
        continue
    if grounded.dropped_fields:
        log.info(
            "event.ungrounded_field_dropped",
            item_id=item["id"], title=(ev.title or "")[:160], fields=grounded.dropped_fields,
        )

then replace the ``insert_event(...)`` call's ``kind=ev.kind, ..., amount_usd=amount_usd,
currency=ev.currency, parties=event_parties, customer=ev.customer, program=ev.program,
summary_he=ev.summary_he, confidence=ev.confidence`` keyword arguments with ``kind=grounded.kind,
title=grounded.title, date=_parse_date(grounded.date), amount_usd=grounded.amount_usd,
currency=grounded.currency, parties=grounded.parties, customer=grounded.customer,
program=grounded.program, summary_he=grounded.summary_he, confidence=grounded.confidence``.
``reconcile_events`` is a repair-script/batch-report concern (``scripts/repair_round14_events.py``
already calls it over the full ``events`` table) -- it is not meant to run per-item at extraction
time, since the whole point is comparing an event against *other items'* already-persisted events.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------------------------

_ACQUISITION_RE = re.compile(
    r"\bacqui(?:re|res|red|ring|sition)\w*\b|\bmerger\b|\bmerge[sd]?\b|\btakeover\b|\bbuyout\b|"
    r"\bto\s+buy\b|רכיש[הת]|רכש[הו]|נרכש[הו]?|מיזוג|השתלטות",
    re.IGNORECASE,
)
_PARTNERSHIP_RE = re.compile(
    r"\bpartner(?:ship)?s?\b|\bteaming\b|\bjoint\s+(?:venture|offer|bid)\b|\bcollaborat\w*\b|"
    r"\bco-market\w*\b|\bworking\s+together\b|שיתוף\s+פעולה|שותפ(?:ות|ה)",
    re.IGNORECASE,
)
_INVESTMENT_RE = re.compile(
    r"\bfunding\s+round\b|\bfinancing\s+round\b|\braised\b|\binvestment\s+round\b|"
    r"\bequity\s+round\b|\bfundrais\w*\b|גיוס\s+הון|סבב\s+גיוס",
    re.IGNORECASE,
)
_FINANCIAL_RESULTS_RE = re.compile(
    r"\bbacklog\b|\bearnings\b|\bquarterly\s+results\b|\bnet\s+profit\b|\brevenue\b|"
    r"\bbeats\s+analysts\b|\border\s+book\b|מלאי\s+הזמנות|רבעון|רווח\s+נקי|תוצאות\s+כספיות|"
    r"דיווח(?:ה)?\s+על\s+שיא",
    re.IGNORECASE,
)
_APPOINTMENT_RE = re.compile(
    r"\bappoint(?:s|ed|ment)?\b|\bnamed\s+as\b|\bchosen\s+to\s+(?:lead|head)\b|\bto\s+head\b|"
    r"\bnew\s+(?:CEO|president|chairman)\b|מינוי|מונ[התה]|נבחר\s+ל|ראש\s+הפעילות|ראש\s+פעילות|יכהן\s+כ",
    re.IGNORECASE,
)
_VALUATION_CONTEXT_RE = re.compile(
    r"valuation|valued\s+at|market\s+cap|market\s+value|worth\s+an\s+estimated|שווי",
    re.IGNORECASE,
)

_PLACEHOLDER_NONE = frozenset(
    {"לא צוין", "לא ידוע", "not specified", "unspecified", "n/a", "na", "unknown", "—", "-", "none"}
)

# ---------------------------------------------------------------------------------------------
# amount grounding (rule a)
# ---------------------------------------------------------------------------------------------

_AMOUNT_MAGNITUDE_WORDS: dict[str, float] = {
    "billion": 1e9, "bn": 1e9, "b": 1e9, "מיליארד": 1e9, "מיליארדי": 1e9,
    "million": 1e6, "mn": 1e6, "mm": 1e6, "m": 1e6, "מיליון": 1e6, "מיליוני": 1e6,
    "thousand": 1e3, "k": 1e3, "אלף": 1e3,
}  # fmt: skip
_MAGNITUDE_ALT = "|".join(sorted(_AMOUNT_MAGNITUDE_WORDS, key=len, reverse=True))
_VALUATION_WINDOW_CHARS = 60
_VALUATION_AFTER_WINDOW_CHARS = 14


def _fmt_reduced(value: float) -> list[str]:
    base = str(int(value)) if float(value).is_integer() else f"{value:.4f}".rstrip("0").rstrip(".")
    return list(dict.fromkeys([base, base.replace(".", ",")]))


def _grouped_digit_pattern(digits: str) -> str:
    groups: list[str] = []
    i = len(digits)
    while i > 0:
        groups.append(digits[max(0, i - 3) : i])
        i -= 3
    groups.reverse()
    return r"[,.]?".join(re.escape(g) for g in groups)


def _amount_grounded(amount: float | None, text: str) -> tuple[bool, bool, str]:
    """Rule (a): ``(grounded, is_valuation, evidence)``. ``text`` is the item's own source
    corpus. ``evidence`` is the matched snippet when grounded (whether kept or dropped as a
    valuation), or ``""`` when nothing was found at all."""
    if amount is None or amount <= 0 or not text:
        return True, False, ""
    match: re.Match[str] | None = None
    for magnitude_val in sorted(set(_AMOUNT_MAGNITUDE_WORDS.values()), reverse=True):
        reduced = amount / magnitude_val
        if reduced < 0.01 or reduced > 100_000:
            continue
        for v in _fmt_reduced(round(reduced, 4)):
            pat = (
                r"(?<![\d.,])(?:US\$|USD|\$|€|£|₪)?\s*"
                + re.escape(v)
                + r"\s*(?:-|to|–)?\s*(?:"
                + _MAGNITUDE_ALT
                + r")\b"
            )
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                match = m
                break
        if match:
            break
    if match is None and float(amount).is_integer():
        digits = str(int(amount))
        if len(digits) >= 4:
            pat = r"(?<!\d)(?<!\d\.)" + _grouped_digit_pattern(digits) + r"(?!\d)(?!\.\d)"
            match = re.search(pat, text)
    if match is None:
        return False, False, ""
    # Valuation cue before the figure ("a valuation of about $100 billion") or immediately after
    # it ("trading at $1.5b valuation" -- live event 19's item title). The after-window is tight
    # (14 chars) on purpose: event 23's "$10 billion, based on a company valuation of about
    # $100 billion" must keep its real round size, so a cue ~30 chars later must NOT count.
    window_before = text[max(0, match.start() - _VALUATION_WINDOW_CHARS) : match.start()]
    window_after = text[match.end() : match.end() + _VALUATION_AFTER_WINDOW_CHARS]
    is_valuation = bool(_VALUATION_CONTEXT_RE.search(window_before)) or bool(
        _VALUATION_CONTEXT_RE.search(window_after)
    )
    return True, is_valuation, _snippet(match, text, radius=40)


# ---------------------------------------------------------------------------------------------
# party/customer grounding (rule b)
# ---------------------------------------------------------------------------------------------


def _normalize_placeholder(name: str | None) -> str | None:
    if name is None:
        return None
    if name.strip().casefold() in _PLACEHOLDER_NONE:
        return None
    return name


def _name_grounded(name: str | None, corpus_cf: str) -> bool:
    if not name or not name.strip():
        return True
    if name.casefold() in corpus_cf:
        return True
    try:
        from eoa.pipeline.entity_normalize import resolve_canonical

        record = resolve_canonical(name)
    except Exception:  # pragma: no cover -- entity_normalize/watchlist unavailable
        record = None
    if record:
        return any((s or "").casefold() in corpus_cf for s in [record.get("name", ""), *record.get("aliases", [])])
    return False


# ---------------------------------------------------------------------------------------------
# kind reclassification (rule c)
# ---------------------------------------------------------------------------------------------


def _snippet(m: re.Match[str], text: str, radius: int = 30) -> str:
    start, end = max(0, m.start() - radius), min(len(text), m.end() + radius)
    return text[start:end].strip()


def _reclassify_kind(kind: str, own_text: str) -> tuple[str, bool, str]:
    """``own_text`` is the event's *own* extracted text (title + summary_he + program) -- see the
    module docstring's rule (c) for why this, not the item's full source corpus, is the right
    scope. Returns ``(new_kind, changed, evidence)``."""
    if kind == "m_and_a" and not _ACQUISITION_RE.search(own_text):
        if m := _PARTNERSHIP_RE.search(own_text):
            return "partnership", True, _snippet(m, own_text)
        if m := _INVESTMENT_RE.search(own_text):
            return "investment", True, _snippet(m, own_text)
        if m := _APPOINTMENT_RE.search(own_text):
            return "appointment", True, _snippet(m, own_text)
        return "other", True, "no acquisition/partnership/investment/appointment vocabulary in the event's own text"
    if kind in ("regulation", "other") and (m := _FINANCIAL_RESULTS_RE.search(own_text)):
        return "financial_results", True, _snippet(m, own_text)
    return kind, False, ""


# ---------------------------------------------------------------------------------------------
# thin-source guard (rule d)
# ---------------------------------------------------------------------------------------------

_THIN_SOURCE_MIN_CHARS = 80


def _is_thin_source(item: dict[str, Any]) -> bool:
    body = " ".join(filter(None, [item.get("clean_text"), item.get("raw_text")])).strip()
    return len(body) < _THIN_SOURCE_MIN_CHARS


# ---------------------------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------------------------


@dataclass
class FieldChange:
    """One field-level change :func:`ground_event` made, with the short piece of source text (or
    reasoning) that justifies it -- what ``scripts/repair_round14_events.py``'s printed table's
    "evidence quote" column comes from."""

    field: str
    before: Any
    after: Any
    evidence: str


@dataclass
class GroundedEvent:
    kind: str
    title: str | None
    date: Any
    amount_usd: float | None
    currency: str | None
    parties: list[str]
    customer: str | None
    program: str | None
    summary_he: str | None
    confidence: float | None
    dropped_fields: list[str] = field(default_factory=list)
    kind_changed_from: str | None = None
    changes: list[FieldChange] = field(default_factory=list)


def _source_corpus(item: dict[str, Any]) -> str:
    return " ".join(filter(None, [item.get("title"), item.get("clean_text"), item.get("raw_text")]))


def ground_event(item: dict[str, Any], event: Any) -> GroundedEvent | None:
    """The single per-event entry point. ``item`` is an ``items`` row (dict-like: at least
    ``title``/``clean_text``/``raw_text``). ``event`` is either an ``EventOut`` (or any object with
    matching attributes) or a plain dict with the same keys (``kind``, ``title``, ``date``,
    ``amount_usd``, ``currency``, ``parties``, ``customer``, ``program``, ``summary_he``,
    ``confidence``) -- the latter is what both the analyze.py wiring (after its own
    ``normalize_amount_from_source``/``_recall_event_parties`` passes) and
    ``scripts/repair_round14_events.py`` (reading straight from the ``events`` table) pass in.

    Never returns ``None`` itself (see the module docstring) -- kept for callers that want to treat
    "reclassified all the way down to an empty husk" as droppable; today no rule produces that."""
    if not isinstance(event, dict):
        event = {
            "kind": event.kind,
            "title": event.title,
            "date": event.date,
            "amount_usd": event.amount_usd,
            "currency": event.currency,
            "parties": list(event.parties or []),
            "customer": event.customer,
            "program": event.program,
            "summary_he": event.summary_he,
            "confidence": event.confidence,
        }

    dropped: list[str] = []
    changes: list[FieldChange] = []
    corpus = _source_corpus(item)
    corpus_cf = corpus.casefold()
    thin = _is_thin_source(item)

    kind = event.get("kind") or "other"
    own_text = " ".join(filter(None, [event.get("title"), event.get("summary_he"), event.get("program")]))
    new_kind, kind_changed, kind_evidence = _reclassify_kind(kind, own_text)
    if kind_changed:
        changes.append(FieldChange("kind", kind, new_kind, kind_evidence))

    amount = event.get("amount_usd")
    currency = event.get("currency")
    if amount is not None and thin:
        dropped.append("amount_usd")
        changes.append(
            FieldChange("amount_usd", amount, None, "item source text under 80 chars -- title-only/empty-body")
        )
        amount, currency = None, None
    elif amount is not None:
        grounded_ok, is_valuation, amt_evidence = _amount_grounded(float(amount), corpus)
        if is_valuation or not grounded_ok:
            reason = (
                f'valuation/market-size context, not a deal amount: "...{amt_evidence}..."'
                if is_valuation
                else "amount not found (at this magnitude) anywhere in the source text"
            )
            dropped.append("amount_usd")
            changes.append(FieldChange("amount_usd", amount, None, reason))
            amount, currency = None, None

    customer = _normalize_placeholder(event.get("customer"))
    if customer is not None and thin:
        dropped.append("customer")
        changes.append(
            FieldChange("customer", customer, None, "item source text under 80 chars -- title-only/empty-body")
        )
        customer = None
    elif customer is not None and not _name_grounded(customer, corpus_cf):
        dropped.append("customer")
        changes.append(FieldChange("customer", customer, None, "not found in source text (literally or via alias)"))
        customer = None

    parties_kept: list[str] = []
    for p in event.get("parties") or []:
        p_norm = _normalize_placeholder(p)
        if not p_norm:
            continue
        if _name_grounded(p_norm, corpus_cf):
            parties_kept.append(p_norm)
        else:
            dropped.append(f"parties:{p_norm}")
            changes.append(
                FieldChange("parties", p_norm, None, "not found in source text (literally or via alias)")
            )

    # 'appointment' events are structurally not monetary transactions -- even a grounded-looking
    # amount/customer on an appointment-kind row (e.g. event 22's real $10B digits, which describe
    # a *different* fact in the same article -- see rule (a)'s docstring) is dropped categorically.
    if new_kind == "appointment":
        if amount is not None:
            dropped.append("amount_usd")
            changes.append(
                FieldChange("amount_usd", amount, None, "kind='appointment' -- personnel moves carry no deal amount")
            )
            amount, currency = None, None
        if customer is not None:
            dropped.append("customer")
            changes.append(
                FieldChange("customer", customer, None, "kind='appointment' -- personnel moves carry no customer")
            )
            customer = None

    confidence = event.get("confidence")
    if dropped and confidence is not None:
        confidence = min(float(confidence), 0.5)

    return GroundedEvent(
        kind=new_kind,
        title=event.get("title"),
        date=event.get("date"),
        amount_usd=amount,
        currency=currency,
        parties=parties_kept,
        customer=customer,
        program=event.get("program"),
        summary_he=event.get("summary_he"),
        confidence=confidence,
        dropped_fields=dropped,
        kind_changed_from=kind if kind_changed else None,
        changes=changes,
    )


# ---------------------------------------------------------------------------------------------
# cross-source reconciliation (task rule 2)
# ---------------------------------------------------------------------------------------------

_DATE_MERGE_WINDOW_DAYS = 14
_AMOUNT_MERGE_TOLERANCE = 0.12
_EUR_TO_USD = 1.08  # fixed approximation, documented here -- see reconcile_events docstring


def _to_usd(amount: float | None, currency: str | None) -> float | None:
    if amount is None:
        return None
    amount = float(amount)  # DB rows hand back events.amount_usd as decimal.Decimal
    cur = (currency or "USD").upper()
    if cur == "EUR":
        return amount * _EUR_TO_USD
    if cur in ("ILS", "NIS"):
        return amount * 0.27
    return amount


def _identity_keys(ev: dict[str, Any]) -> set[str]:
    names = [ev.get("customer"), *(ev.get("parties") or [])]
    return {n.strip().casefold() for n in names if n and n.strip()}


def _distinct_customers(e1: dict[str, Any], e2: dict[str, Any]) -> bool:
    """True when *both* events name a specific ``customer`` and those customers are different (not
    the same string, and not resolved -- ``eoa.pipeline.entity_normalize.resolve_canonical`` -- to
    the same watchlist/curated-org record). A from-testing false-merge case (this round) had event
    61 (Leonardo DRS wins a US Space Force prototype contract, item 303) cluster with event 124
    (Leonardo wins a Centauro II contract for the *Brazilian Army*, item 815) -- two unrelated
    contracts that happen to share the single common vendor name "Leonardo" and land two days apart
    by coincidence, with no ``amount_usd`` on either side to check. A shared vendor plus date
    proximity is not evidence of the same deal when the two events *also* each name a different,
    specific buyer -- this check blocks that pairing outright, mirroring
    ``eoa.memory.relational.same_kind_duplicate``'s own "distinct numbers/proper nouns block the
    merge outright" philosophy for the same-item case just above."""
    c1, c2 = e1.get("customer"), e2.get("customer")
    if not c1 or not c2:
        return False
    if c1.strip().casefold() == c2.strip().casefold():
        return False
    try:
        from eoa.pipeline.entity_normalize import resolve_canonical

        r1, r2 = resolve_canonical(c1), resolve_canonical(c2)
    except Exception:  # pragma: no cover
        r1 = r2 = None
    return not (r1 is not None and r2 is not None and r1.get("name") == r2.get("name"))


def _dates_confirmed_close(d1: Any, d2: Any) -> bool:
    """True only when *both* dates are actually present and within the window -- an unknown date
    on either side is *not* treated as a pass here (see :func:`_mergeable`'s own docstring for why:
    two same-kind events sharing one common party but with nothing else in common must not merge on
    identity alone)."""
    if d1 is None or d2 is None:
        return False
    if isinstance(d1, str):
        try:
            d1 = dt.date.fromisoformat(d1[:10])
        except ValueError:
            return False
    if isinstance(d2, str):
        try:
            d2 = dt.date.fromisoformat(d2[:10])
        except ValueError:
            return False
    return abs((d1 - d2).days) <= _DATE_MERGE_WINDOW_DAYS


def _amounts_confirmed_close(a1: float | None, c1: str | None, a2: float | None, c2: str | None) -> bool:
    """True only when *both* amounts are actually present and within tolerance -- see
    :func:`_dates_confirmed_close`'s docstring for why an unknown side is not a free pass here."""
    if a1 is None or a2 is None:
        return False
    u1, u2 = _to_usd(a1, c1), _to_usd(a2, c2)
    if u1 is None or u2 is None or u1 <= 0 or u2 <= 0:
        return False
    return abs(u1 - u2) / max(u1, u2) <= _AMOUNT_MERGE_TOLERANCE


def _mergeable(e1: dict[str, Any], e2: dict[str, Any]) -> bool:
    """Same-item pairs require the same ``kind`` *and* :func:`eoa.memory.relational.
    same_kind_duplicate` on their titles -- the exact rule ``insert_event``'s own near-duplicate
    upsert already uses for a fresh insert, reused here rather than re-implemented so a
    already-persisted pair is judged identically to a new one. "Same item + same kind" alone is
    *not* enough on its own: testing this module live against the full ``events`` table found item
    101's four 'launch' rows are two genuinely distinct satellites ("שיגור לוויין דור 1" / Dror 1
    and "שיגור לוויין אופק 19" / Ofek 19) the model split across separate rows, and item 153's four
    'partnership' rows are four distinct partner companies (WeatherNext, GraphCast, Pangu-Weather,
    IFS HRES) -- a blanket same-item-same-kind merge would have silently discarded three of every
    four in each group. ``same_kind_duplicate`` is exactly the guard built for this ("אופק 19" vs
    "דור 1" is its own worked example, blocked outright by its distinct-numbers check).

    Cross-item requires same ``kind`` + overlapping company identity *and* an actually-confirmed
    corroborating date or amount match (:func:`_dates_confirmed_close`/
    :func:`_amounts_confirmed_close`) -- identity overlap alone is deliberately not enough either: a
    second false-merge case found the same way had event 24 (Anduril x Elbit Sigma-155 partnership,
    item 81, no date/amount) cluster with events 35/225/246 (Elbit x Serbia UAV-factory
    partnership, item 126, no date/amount) purely because both share the single, extremely common
    party name "Elbit" and neither side had a date or amount to check against -- two entirely
    unrelated partnerships that happen to name the same company. Requiring at least one signal to
    be genuinely present *and* matching on both sides (not simply absent on both) is what tells that
    case apart from the real cross-source merges this function exists for (e.g. the Greece
    air-defense deal or the Elbit $270M SPECTRO/ISR contract, both of which have a real,
    corroborating date or amount match on every merged pair)."""
    if e1.get("item_id") == e2.get("item_id"):
        if e1.get("kind") != e2.get("kind"):
            return False
        from eoa.memory.relational import same_kind_duplicate

        return same_kind_duplicate(e1.get("title"), e2.get("title"))
    if e1.get("kind") != e2.get("kind"):
        return False
    if _distinct_customers(e1, e2):
        return False
    if not (_identity_keys(e1) & _identity_keys(e2)):
        return False
    return _dates_confirmed_close(e1.get("date"), e2.get("date")) or _amounts_confirmed_close(
        e1.get("amount_usd"), e1.get("currency"), e2.get("amount_usd"), e2.get("currency")
    )


def _fmt_amount_note(ev: dict[str, Any]) -> str | None:
    amount, currency = ev.get("amount_usd"), ev.get("currency") or "USD"
    if amount is None:
        return None
    return f"{float(amount):,.0f} {currency}"


@dataclass
class ReconciledEvent:
    keep: dict[str, Any]
    merged_item_ids: list[int]
    #: every ``events.id`` in this cluster *other than* ``keep["id"]`` -- includes same-item
    #: siblings (two near-duplicate rows on the very same item, both pulled into one cluster by
    #: :func:`_mergeable`'s same-item branch), not just cross-item ones. The caller should delete
    #: exactly these ids and nothing else -- do not try to reconstruct this set from
    #: ``merged_item_ids`` (which is cross-item only and will silently miss a same-item duplicate).
    member_ids: list[int] = field(default_factory=list)
    amounts_reconciled: bool = False


def reconcile_events(events: list[dict[str, Any]]) -> list[ReconciledEvent]:
    """Groups ``events`` (each a dict with at least ``id``/``item_id``/``kind``/``date``/
    ``amount_usd``/``currency``/``customer``/``parties``/``summary_he``/``confidence`` -- the shape
    ``scripts/repair_round14_events.py`` reads straight from the ``events`` table) into clusters
    describing the same underlying real-world event, via a union-find over :func:`_mergeable`
    (same ``kind`` + overlapping company identity + (dates within 14 days OR amounts within ~12%
    of each other, comparing in USD via a fixed EUR/ILS approximation -- not a live FX rate; good
    enough for "is this roughly the same number", not for anything precision-sensitive) -- see the
    module docstring for why this is an OR, not an AND, of the date/amount signals).

    Returns one :class:`ReconciledEvent` per cluster (singletons included, ``merged_item_ids``
    empty for those): ``keep`` is the richest event in the cluster (most non-null fields, ties
    broken by lowest ``id`` -- the earliest-inserted row), with ``customer``/``parties`` widened by
    union across the cluster and, when the cluster's own amounts disagree beyond
    :data:`_AMOUNT_MERGE_TOLERANCE`, ``summary_he`` appended with a reconciliation note listing
    every amount seen ("סכומים שונים בין המקורות: ...") instead of silently picking one -- this is
    the task brief's own worked example (Greece $4B/€3.1B/€3.5B, CR-factcheck.md weekly lines
    502-504) and ``amounts_reconciled`` is set so the caller can log/report it. The caller is
    responsible for actually persisting the merge (updating ``keep``'s row, setting
    ``source_item_ids``, deleting the merged siblings) -- this function only computes it."""
    n = len(events)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            if _mergeable(events[i], events[j]):
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    def richness(ev: dict[str, Any]) -> int:
        return sum(1 for k in ("amount_usd", "customer", "date", "program", "parties") if ev.get(k))

    out: list[ReconciledEvent] = []
    for indices in clusters.values():
        members = [events[i] for i in indices]
        best = sorted(members, key=lambda e: (-richness(e), e.get("id") or 0))[0]
        keep = dict(best)

        all_customers = {e.get("customer") for e in members if e.get("customer")}
        all_parties: list[str] = []
        for e in members:
            for p in e.get("parties") or []:
                if p not in all_parties:
                    all_parties.append(p)
        if all_parties:
            keep["parties"] = all_parties
        if not keep.get("customer") and all_customers:
            keep["customer"] = next(iter(all_customers))

        item_ids = sorted({e.get("item_id") for e in members if e.get("item_id") is not None})
        merged_item_ids = [i for i in item_ids if i != keep.get("item_id")]
        member_ids = [e.get("id") for e in members if e.get("id") is not None and e.get("id") != keep.get("id")]

        amounts_reconciled = False
        if len(members) > 1:
            distinct_amounts = {_fmt_amount_note(e) for e in members if e.get("amount_usd") is not None}
            distinct_amounts.discard(None)
            if len(distinct_amounts) > 1:
                amounts_reconciled = True
                note = "סכומים שונים בין המקורות: " + ", ".join(sorted(distinct_amounts))
                summary = keep.get("summary_he") or ""
                if note not in summary:
                    keep["summary_he"] = (summary + " " + note).strip()
                # Ambiguous amount across sources -- keep the richest single figure as amount_usd
                # (already ``keep``'s own, via ``richness``) but confidence reflects the ambiguity.
                if keep.get("confidence") is not None:
                    keep["confidence"] = min(float(keep["confidence"]), 0.6)

        out.append(
            ReconciledEvent(
                keep=keep,
                merged_item_ids=merged_item_ids,
                member_ids=member_ids,
                amounts_reconciled=amounts_reconciled,
            )
        )

    return out
