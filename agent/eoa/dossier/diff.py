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
"""

from __future__ import annotations

from typing import Any

from eoa.llm.schemas.analysis import Sentence
from eoa.llm.schemas.product_dossier import ProductDossierOut


def _rows(data: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    if not data:
        return []
    value = data.get(key)
    return value if isinstance(value, list) else []


def _deal_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (str(row.get("customer") or ""), str(row.get("date") or ""), str(row.get("kind") or ""))


def _diff_deals(previous: dict[str, Any] | None, current: ProductDossierOut) -> list[Sentence]:
    prev_keys = {_deal_key(r) for r in _rows(previous, "deals")}
    out = []
    for d in current.deals:
        key = (d.customer, d.date or "", d.kind)
        if key in prev_keys or not d.cites:
            continue
        amount_part = f" בהיקף {d.amount}" if d.amount else ""
        out.append(
            Sentence(
                text_he=f"עסקה חדשה מאז הסקירה הקודמת: {d.customer or '—'}{amount_part} ({d.kind}).",
                cites=d.cites,
            )
        )
    return out


def _spec_key(row: dict[str, Any]) -> str:
    return str(row.get("parameter_he") or "").strip().casefold()


def _diff_specifications(previous: dict[str, Any] | None, current: ProductDossierOut) -> list[Sentence]:
    prev_by_param = {_spec_key(r): (r.get("value") or "") for r in _rows(previous, "specifications")}
    out = []
    for spec in current.specifications:
        key = spec.parameter_he.strip().casefold()
        prev_value = prev_by_param.get(key)
        if prev_value is None:
            if spec.value and spec.cites:
                out.append(
                    Sentence(
                        text_he=f"פרמטר מפרט חדש שפורסם: {spec.parameter_he} = {spec.value}.",
                        cites=spec.cites,
                    )
                )
        elif prev_value != spec.value and spec.value and spec.cites:
            out.append(
                Sentence(
                    text_he=f"שינוי בערך המפרט '{spec.parameter_he}': {prev_value or '—'} -> {spec.value}.",
                    cites=spec.cites,
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
    relevance), then pricing, specifications, versions -- an empty list is a real, honest answer
    ("a previous dossier exists, but nothing detectably changed"), never omitted."""
    if previous_data is None:
        return None
    out: list[Sentence] = []
    out.extend(_diff_deals(previous_data, current))
    out.extend(_diff_pricing(previous_data, current))
    out.extend(_diff_specifications(previous_data, current))
    out.extend(_diff_versions(previous_data, current))
    return out


__all__ = ["compute_diff"]
