"""Stage 4 (``docs/PLAN_PRODUCT_DOSSIER.md`` section 4.4): "מה השתנה" -- a deterministic diff
between the just-built (grounded) :class:`~eoa.llm.schemas.product_dossier.ProductDossierOut` and
the previous dossier of the same ``product_key`` (``product_dossiers.data``, a plain JSONB dict).

Deliberately code-only, not LLM-drafted: the schema already lets the extraction model attempt
``what_changed_he`` itself, but a diff is exactly the kind of claim a deterministic comparison can
state more reliably (and cheaply) than another model call -- :func:`compute_diff`'s output is what
``eoa.dossier.report`` actually persists into ``what_changed_he``, replacing whatever the model
wrote. Every emitted :class:`~eoa.llm.schemas.analysis.Sentence` cites the *current* row's own
``cites`` (already grounded by ``eoa.dossier.extract``) -- a diff sentence about the previous run's
data would need the previous run's own registry, which no longer means anything against the current
run's freshly-numbered sources.

PD-fix-2 (2026-09-08), findings from the second live SPECTRO XR dossier (``product_dossiers.id=2``
vs. ``id=1``, same ``product_key``):

1. A deal's identity for "did this deal already exist" purposes is (normalized customer, the
   amount's own numeric value, kind) -- **never** ``date``/``date_kind``/``region_he``. Those three
   fields routinely change between runs for the exact same deal (a date gets discovered, a region
   gets refined into region_he, a backfilled "published" date is later replaced by a "deal" date)
   without the deal itself being new -- see :func:`_deal_key`/:func:`_deal_amount_key`. A date that
   went from unknown to known for an already-known deal is its own honest, narrower note ("נוסף
   תאריך לעסקה ...") rather than a fabricated "new deal".
2. A specification/performance value that is mere rewording of the previous run's own value (same
   facts, different phrasing -- e.g. "...על הגימבל" -> "...המורכבים על הגימבל") must not read as a
   change. Values are compared by token (Jaccard) overlap; ``>= 0.6`` counts as the same value --
   see :func:`_values_effectively_same`.
"""

from __future__ import annotations

import re
from typing import Any

from eoa.dossier.extract import parse_amount_he
from eoa.llm.schemas.analysis import Sentence
from eoa.llm.schemas.product_dossier import ProductDossierOut
from eoa.pipeline import entity_normalize


def _rows(data: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not data:
        return []
    value = data.get(key)
    return value if isinstance(value, list) else []


# --------------------------------------------------------------------------
# PD-fix-2 item 1: deal identity -- (customer, amount, kind) only; date/date_kind/region never
# participate in "is this the same deal" -- they are the exact fields a rerun most often refines
# for an already-known deal.
# --------------------------------------------------------------------------


def _deal_amount_key(amount_value: float | None, amount_text: str) -> str:
    """Numeric identity for a deal's amount. Prefers the already-grounded ``amount_value``
    (every *current* deal row has it -- ``eoa.dossier.extract``'s post-check always derives it
    deterministically from ``amount``). A *previous* dossier persisted before that field existed
    (or a hand-built fixture/raw dict omitting it) falls back to parsing the same published
    ``amount`` text with the exact same rules (:func:`eoa.dossier.extract.parse_amount_he`), so
    both sides of the comparison always land on the same key for the same published figure."""
    if amount_value is not None:
        return f"{amount_value:.2f}"
    parsed, _currency = parse_amount_he(amount_text or "")
    return f"{parsed:.2f}" if parsed is not None else ""


def _deal_key(customer: str, amount_value: float | None, amount_text: str, kind: str) -> tuple[str, str, str]:
    return (
        entity_normalize.normalize_name_key(customer),
        _deal_amount_key(amount_value, amount_text),
        str(kind or ""),
    )


def _diff_deals(previous: dict[str, Any] | None, current: ProductDossierOut) -> list[Sentence]:
    prev_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in _rows(previous, "deals"):
        key = _deal_key(r.get("customer") or "", r.get("amount_value"), r.get("amount") or "", r.get("kind") or "")
        # First-seen wins; a duplicate identity already present in the previous run's own data is
        # not this stage's problem to resolve.
        prev_by_key.setdefault(key, r)

    out: list[Sentence] = []
    for d in current.deals:
        if not d.cites:
            continue
        key = _deal_key(d.customer, d.amount_value, d.amount, d.kind)
        prev_row = prev_by_key.get(key)
        if prev_row is None:
            amount_part = f" בהיקף {d.amount}" if d.amount else ""
            out.append(
                Sentence(
                    text_he=f"עסקה חדשה מאז הסקירה הקודמת: {d.customer or '—'}{amount_part} ({d.kind}).",
                    cites=d.cites,
                )
            )
            continue
        # Same deal identity: date/date_kind/region churn alone is never "a new deal" -- only a
        # date genuinely discovered where none existed before earns its own honest note.
        if d.date and not prev_row.get("date"):
            out.append(
                Sentence(text_he=f"נוסף תאריך לעסקה עם {d.customer or '—'}: {d.date}.", cites=d.cites)
            )
    return out


# --------------------------------------------------------------------------
# PD-fix-2 item 2: token-overlap "same value" comparison, shared by specifications and performance.
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").casefold()))


def _values_effectively_same(previous_value: str, current_value: str) -> bool:
    """An exact (case-insensitive) match is always "same". Otherwise, compared by token (Jaccard)
    overlap -- ``|intersection| / |union| >= 0.6`` counts as the same value (mere rewording of the
    same fact), below that counts as a genuine change (a new number, unit, or parameter)."""
    a, b = (previous_value or "").strip(), (current_value or "").strip()
    if a.casefold() == b.casefold():
        return True
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.6


def _spec_key(row: dict[str, Any]) -> str:
    return str(row.get("parameter_he") or "").strip().casefold()


def _diff_specifications(previous: dict[str, Any] | None, current: ProductDossierOut) -> list[Sentence]:
    prev_by_param = {_spec_key(r): (r.get("value") or "") for r in _rows(previous, "specifications")}
    out = []
    for spec in current.specifications:
        key = spec.parameter_he.strip().casefold()
        if key not in prev_by_param:
            if spec.value and spec.cites:
                out.append(
                    Sentence(
                        text_he=f"פרמטר מפרט חדש שפורסם: {spec.parameter_he} = {spec.value}.",
                        cites=spec.cites,
                    )
                )
            continue
        prev_value = prev_by_param[key]
        if spec.value and spec.cites and not _values_effectively_same(prev_value, spec.value):
            out.append(
                Sentence(
                    text_he=f"שינוי בערך המפרט '{spec.parameter_he}': {prev_value or '—'} -> {spec.value}.",
                    cites=spec.cites,
                )
            )
    return out


def _perf_key(row: dict[str, Any]) -> str:
    return str(row.get("metric_he") or "").strip().casefold()


def _diff_performance(previous: dict[str, Any] | None, current: ProductDossierOut) -> list[Sentence]:
    """PD-fix-2 item 2's "same rule for performance rows" -- claimed and demonstrated/operational
    values compared the same token-overlap way as specifications, kept as two independent checks
    per metric (a rewording of one must not mask a genuine change in the other)."""
    prev_by_metric = {_perf_key(r): r for r in _rows(previous, "performance")}
    out: list[Sentence] = []
    for perf in current.performance:
        key = perf.metric_he.strip().casefold()
        prev_row = prev_by_metric.get(key)
        if prev_row is None:
            if perf.claimed_value and perf.cites:
                out.append(
                    Sentence(
                        text_he=f"מדד ביצועים חדש שפורסם: {perf.metric_he} = {perf.claimed_value}.",
                        cites=perf.cites,
                    )
                )
            continue
        prev_claimed = prev_row.get("claimed_value") or ""
        if perf.claimed_value and perf.cites and not _values_effectively_same(prev_claimed, perf.claimed_value):
            out.append(
                Sentence(
                    text_he=(
                        f"שינוי בערך הביצועים המוצהר '{perf.metric_he}': "
                        f"{prev_claimed or '—'} -> {perf.claimed_value}."
                    ),
                    cites=perf.cites,
                )
            )
        prev_tested = prev_row.get("tested_or_operational_value") or ""
        cur_tested = perf.tested_or_operational_value or ""
        if cur_tested and perf.cites and not _values_effectively_same(prev_tested, cur_tested):
            out.append(
                Sentence(
                    text_he=(
                        f"שינוי בערך הביצועים הנמדד/מבצעי '{perf.metric_he}': "
                        f"{prev_tested or '—'} -> {cur_tested}."
                    ),
                    cites=perf.cites,
                )
            )
    return out


def _version_key(row: dict[str, Any]) -> str:
    return str(row.get("name") or "").strip().casefold()


def _diff_versions(previous: dict[str, Any] | None, current: ProductDossierOut) -> list[Sentence]:
    prev_names = {_version_key(r) for r in _rows(previous, "variants_and_versions")}
    out = []
    for v in current.variants_and_versions:
        if v.name.strip().casefold() in prev_names:
            continue
        if v.cites:
            out.append(Sentence(text_he=f"גרסה/דגם חדש שזוהה: {v.name}.", cites=v.cites))
    return out


def _price_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("figure") or ""), str(row.get("date") or ""))


def _diff_pricing(previous: dict[str, Any] | None, current: ProductDossierOut) -> list[Sentence]:
    prev_keys = {_price_key(r) for r in _rows(previous, "pricing")}
    out = []
    for p in current.pricing:
        key = (p.figure, p.date or "")
        if key in prev_keys:
            continue
        if p.cites:
            out.append(
                Sentence(text_he=f"נתון מחיר חדש שפורסם: {p.figure} ({p.basis_he or '—'}).", cites=p.cites)
            )
    return out


def compute_diff(previous_data: dict[str, Any] | None, current: ProductDossierOut) -> list[Sentence] | None:
    """``None`` when there is no previous dossier for this ``product_key`` at all (first run --
    "מה השתנה" has no meaning yet); otherwise every detected change, deals first (highest BD
    relevance), then pricing, specifications, performance, versions -- an empty list is a real,
    honest answer ("a previous dossier exists, but nothing detectably changed"), never omitted."""
    if previous_data is None:
        return None
    out: list[Sentence] = []
    out.extend(_diff_deals(previous_data, current))
    out.extend(_diff_pricing(previous_data, current))
    out.extend(_diff_specifications(previous_data, current))
    out.extend(_diff_performance(previous_data, current))
    out.extend(_diff_versions(previous_data, current))
    return out


__all__ = ["compute_diff"]
