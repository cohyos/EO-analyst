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
#
# PD-fix-3 (2026-09-08, item 2): customer identity falls back to (amount, kind) alone whenever the
# customer is missing/a placeholder on EITHER side. The live SPECTRO XR regression (id=2 -> id=3):
# run 2's deal carried a real (if unidentified) customer description, "מדינה באזור אסיה-פסיפיק (לא
# מזוהה)"; run 3's re-extraction of the very same deal lost that text and left customer empty/None
# -- two genuinely different normalized-name keys for the same deal, so it read as a brand-new
# "עסקה חדשה" (with the fabricated-looking "לקוח לא צוין: כ-80 מיליון דולר" line to go with it).
# :func:`_is_placeholder_customer` recognizes both an actually-empty/``None`` customer AND the
# common placeholder TEXT a model (or a hand-built previous-run dict) sometimes writes literally
# ("—", "-", "לא ידוע", "לא צוין", "unknown", "n/a", ...) -- a placeholder customer on either side
# collapses the customer half of the key to the same empty string, so identity then rests on
# amount+kind alone, exactly matching a *real* customer's own empty-string key (which is exactly
# what should happen: "no customer known" is one identity, not a new one every run).
# --------------------------------------------------------------------------

_PLACEHOLDER_CUSTOMER_RE = re.compile(
    r"^[\s\-—–_.]*$|^(?:n/?a|unknown|לא\s*ידוע|לא\s*צוין|אין\s*מידע|לא\s*מזוהה)[\s.]*$", re.IGNORECASE
)


def _is_placeholder_customer(customer: str | None) -> bool:
    """``True`` for ``None``/empty AND for common literal placeholder text (never for a real,
    if partial, customer description like "מדינה באזור אסיה-פסיפיק (לא מזוהה)" -- that string
    names an actual region/qualifier, it just isn't a specific company/country name)."""
    if not customer:
        return True
    return bool(_PLACEHOLDER_CUSTOMER_RE.match(customer.strip()))


def _customer_display_he(customer: str | None) -> str:
    """Never renders the raw ``"—"``/``None`` placeholder into change text -- an honest Hebrew
    label instead (PD-fix-3 item 2's own "never print '—' as a customer" rule)."""
    if customer and not _is_placeholder_customer(customer):
        return customer.strip()
    return "לקוח לא צוין"


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


def _deal_key(
    customer: str | None, amount_value: float | None, amount_text: str, kind: str
) -> tuple[str, str, str]:
    customer_key = "" if _is_placeholder_customer(customer) else entity_normalize.normalize_name_key(customer)
    return (
        customer_key,
        _deal_amount_key(amount_value, amount_text),
        str(kind or ""),
    )


def _diff_deals(previous: dict[str, Any] | None, current: ProductDossierOut) -> list[Sentence]:
    prev_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    #: PD-fix-3 item 2: the (amount, kind)-only fallback -- populated from EVERY previous deal
    #: regardless of its own customer, so a current row whose customer is a placeholder can still
    #: find its previous match (and vice versa: a previous row whose OWN customer was a placeholder
    #: is still findable from a current row that has since identified a real customer).
    prev_by_amount_kind: dict[tuple[str, str], dict[str, Any]] = {}
    for r in _rows(previous, "deals"):
        key = _deal_key(r.get("customer"), r.get("amount_value"), r.get("amount") or "", r.get("kind") or "")
        # First-seen wins; a duplicate identity already present in the previous run's own data is
        # not this stage's problem to resolve.
        prev_by_key.setdefault(key, r)
        prev_by_amount_kind.setdefault((key[1], key[2]), r)

    out: list[Sentence] = []
    for d in current.deals:
        if not d.cites:
            continue
        key = _deal_key(d.customer, d.amount_value, d.amount, d.kind)
        prev_row = prev_by_key.get(key)
        if prev_row is None:
            # PD-fix-3 item 2: an exact-key miss doesn't necessarily mean "new deal" -- when the
            # customer is a placeholder on EITHER side (this run's or the matched-by-amount+kind
            # previous row's), customer text is never a reliable identity signal for that pair, so
            # identity falls back to (amount, kind) alone. Two rows that both name REAL, different
            # customers at the same amount+kind are deliberately left unmatched here (still "new") --
            # the fallback is never applied just because two customer strings differ.
            candidate = prev_by_amount_kind.get((key[1], key[2]))
            if candidate is not None and (
                _is_placeholder_customer(d.customer) or _is_placeholder_customer(candidate.get("customer"))
            ):
                prev_row = candidate
        if prev_row is None:
            amount_part = f" בהיקף {d.amount}" if d.amount else ""
            out.append(
                Sentence(
                    text_he=(
                        f"עסקה חדשה מאז הסקירה הקודמת: {_customer_display_he(d.customer)}"
                        f"{amount_part} ({d.kind})."
                    ),
                    cites=d.cites,
                )
            )
            continue
        # Same deal identity: date/date_kind/region churn alone is never "a new deal" -- only a
        # date genuinely discovered where none existed before earns its own honest note.
        if d.date and not prev_row.get("date"):
            out.append(
                Sentence(
                    text_he=f"נוסף תאריך לעסקה עם {_customer_display_he(d.customer)}: {d.date}.",
                    cites=d.cites,
                )
            )
    return out


# --------------------------------------------------------------------------
# PD-fix-2 item 2: token-overlap "same value" comparison, shared by specifications and performance.
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").casefold()))


def _token_jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _values_effectively_same(previous_value: str, current_value: str) -> bool:
    """An exact (case-insensitive) match is always "same". Otherwise, compared by token (Jaccard)
    overlap -- ``|intersection| / |union| >= 0.6`` counts as the same value (mere rewording of the
    same fact), below that counts as a genuine change (a new number, unit, or parameter)."""
    a, b = (previous_value or "").strip(), (current_value or "").strip()
    if a.casefold() == b.casefold():
        return True
    if not a or not b:
        return False
    return _token_jaccard(a, b) >= 0.6


#: PD-fix-3 (2026-09-08, item 3): a parameter/metric NAME match this loose ("ביצועי אופטיקה" vs.
#: "ביצועי עומס אופטי במארז קומפקטי" -- the live SPECTRO XR case, 5 false "new parameter" entries
#: from the extraction model simply re-wording its own parameter names between runs, not a genuine
#: new spec). Deliberately lower than the 0.6 VALUE-sameness threshold above: a name is a short,
#: low-entropy label where even a real rewording often shares under 60% of its tokens, while two
#: genuinely different parameters sharing half their words is rare enough that 0.5 stays safe.
_PARAM_NAME_JACCARD_MIN = 0.5


def _find_matching_prev_row(
    prev_rows: list[dict[str, Any]],
    *,
    name_key: str,
    value_key: str,
    current_name: str,
    current_value: str,
) -> dict[str, Any] | None:
    """The previous row this current spec/performance row is "the same parameter" as -- by a fuzzy
    (token-Jaccard >= :data:`_PARAM_NAME_JACCARD_MIN`) match on the NAME field, OR (independently)
    by the VALUE already being effectively the same (:func:`_values_effectively_same`, >= 0.6) --
    either signal alone is enough, covering both "renamed parameter, same value" and "same-ish name,
    refined/changed value". Name match is tried first (cheaper, and the more semantically direct
    signal); returns the first row satisfying either, or ``None`` when nothing matches at all (a
    genuinely new parameter)."""
    current_name = (current_name or "").strip()
    for row in prev_rows:
        prev_name = str(row.get(name_key) or "").strip()
        if prev_name and _token_jaccard(current_name, prev_name) >= _PARAM_NAME_JACCARD_MIN:
            return row
    current_value = (current_value or "").strip()
    if current_value:
        for row in prev_rows:
            prev_value = str(row.get(value_key) or "").strip()
            if prev_value and _values_effectively_same(prev_value, current_value):
                return row
    return None


def _diff_specifications(previous: dict[str, Any] | None, current: ProductDossierOut) -> list[Sentence]:
    prev_specs = _rows(previous, "specifications")
    out = []
    for spec in current.specifications:
        prev_row = _find_matching_prev_row(
            prev_specs,
            name_key="parameter_he",
            value_key="value",
            current_name=spec.parameter_he,
            current_value=spec.value,
        )
        if prev_row is None:
            if spec.value and spec.cites:
                out.append(
                    Sentence(
                        text_he=f"פרמטר מפרט חדש שפורסם: {spec.parameter_he} = {spec.value}.",
                        cites=spec.cites,
                    )
                )
            continue
        prev_value = prev_row.get("value") or ""
        if spec.value and spec.cites and not _values_effectively_same(prev_value, spec.value):
            out.append(
                Sentence(
                    text_he=f"שינוי בערך המפרט '{spec.parameter_he}': {prev_value or '—'} -> {spec.value}.",
                    cites=spec.cites,
                )
            )
    return out


def _diff_performance(previous: dict[str, Any] | None, current: ProductDossierOut) -> list[Sentence]:
    """PD-fix-2 item 2's "same rule for performance rows" -- claimed and demonstrated/operational
    values compared the same token-overlap way as specifications, kept as two independent checks
    per metric (a rewording of one must not mask a genuine change in the other). PD-fix-3 item 3:
    matched against the previous run's rows the same fuzzy name-or-value way specifications are,
    not by an exact ``metric_he`` string -- the same LLM re-wording drift applies to metric names
    as much as spec parameter names."""
    prev_perf = _rows(previous, "performance")
    out: list[Sentence] = []
    for perf in current.performance:
        prev_row = _find_matching_prev_row(
            prev_perf,
            name_key="metric_he",
            value_key="claimed_value",
            current_name=perf.metric_he,
            current_value=perf.claimed_value,
        )
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
