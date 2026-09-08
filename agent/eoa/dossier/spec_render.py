"""Grouped specifications/performance table rendering (PD-vocab-extract, docs/
PLAN_SPEC_VOCABULARY.md, section 5.1's grouping rule applied to the single-dossier report path):
produces the same ``{"title_he", "headers", "rows"}`` / ``{"title_he", "body_he"}`` table-entry
shape ``eoa.dossier.report._ordered_report_entries`` already builds by hand for every other
section, so ``build_docx``/``render_markdown``/``render_html`` (``eoa.report.docx_builder``) render
it exactly like every other section -- no renderer change needed. ``eoa.dossier.report`` integrates
this module through exactly ONE call (:func:`spec_and_performance_entries`), per this round's file
ownership (another lane is concurrently editing that module for an unrelated LLM-leg override).

Grouping/order: every ``SpecRow``/``PerformanceRow`` with a non-empty ``key`` is looked up in
``eoa.dossier.vocabulary.all_params_by_key()`` -- the flat map across ALL six product-line blocks,
safe with no ``product_line`` context needed here at all, because a key is globally unique: a given
dossier's own rows only ever carry keys from ``common`` + its own line, an unrelated line's keys
simply never appear among them. Rows are grouped by ``group_he`` in ``eoa.dossier.vocabulary.
GROUP_ORDER_HE``'s fixed order, and ordered within a group by vocabulary DECLARATION order (not
alphabetical, not extraction order) -- section 5.1's own rule, reused here. A row with no key at all
(should not normally happen post ``eoa.dossier.extract``'s own post-check, which demotes any
unkeyed specifications/performance row into ``other_specifications`` -- kept here only as a
defensive fallback for old, pre-vocabulary-rollout persisted data) sorts to the end.
"""

from __future__ import annotations

from typing import Any

from eoa.dossier.vocabulary import GROUP_ORDER_HE, SpecParam, all_params_by_key
from eoa.llm.schemas.product_dossier import ProductDossierOut

PLACEHOLDER_HE = "לא נמצא במקורות"
_OTHER_GROUP_HE = "אחר"

_GROUP_INDEX = {g: i for i, g in enumerate(GROUP_ORDER_HE)}
_UNKNOWN_SORT_KEY = (len(GROUP_ORDER_HE), 10_000)


def _cell(value: Any) -> Any:
    if value in (None, ""):
        return PLACEHOLDER_HE
    return value


def _cite_cell(cites: list[int]) -> str:
    return ", ".join(f"[{n}]" for n in cites) if cites else "—"


def _sort_index(params_by_key: dict[str, SpecParam]) -> dict[str, tuple[int, int]]:
    """``key -> (group_order_index, declaration_order_index)`` -- built from the global vocabulary
    map (itself ``lru_cache``'d, see ``eoa.dossier.vocabulary``, so this is cheap per call)."""
    return {
        key: (_GROUP_INDEX.get(param.group_he, len(GROUP_ORDER_HE)), i)
        for i, (key, param) in enumerate(params_by_key.items())
    }


def _group_label(key: str, params_by_key: dict[str, SpecParam]) -> str:
    param = params_by_key.get(key)
    return param.group_he if param is not None else _OTHER_GROUP_HE


def specifications_entry(dossier: ProductDossierOut) -> dict[str, Any]:
    if not dossier.specifications:
        return {"title_he": "מפרט", "body_he": PLACEHOLDER_HE}
    params_by_key = all_params_by_key()
    sort_index = _sort_index(params_by_key)
    rows_sorted = sorted(
        dossier.specifications, key=lambda r: sort_index.get(r.key, _UNKNOWN_SORT_KEY)
    )
    headers = ["קבוצה", "פרמטר", "ערך", "יחידה/וריאנט", "סוג מקור", "מקור"]
    rows = [
        [
            _group_label(r.key, params_by_key),
            r.parameter_he,
            _cell(r.value),
            " / ".join(x for x in (r.unit, r.variant) if x) or "—",
            r.source_kind,
            _cite_cell(r.cites),
        ]
        for r in rows_sorted
    ]
    return {"title_he": "מפרט", "headers": headers, "rows": rows}


def performance_entry(dossier: ProductDossierOut) -> dict[str, Any]:
    if not dossier.performance:
        return {"title_he": "ביצועים (מוצהר מול נמדד)", "body_he": PLACEHOLDER_HE}
    params_by_key = all_params_by_key()
    sort_index = _sort_index(params_by_key)
    rows_sorted = sorted(dossier.performance, key=lambda r: sort_index.get(r.key, _UNKNOWN_SORT_KEY))
    headers = ["קבוצה", "מדד", "ערך מוצהר", "ערך נמדד/מבצעי", "תנאים", "מקור"]
    rows = [
        [
            _group_label(r.key, params_by_key),
            r.metric_he,
            _cell(r.claimed_value),
            _cell(r.tested_or_operational_value),
            _cell(r.conditions_he)[:150],
            _cite_cell(r.cites),
        ]
        for r in rows_sorted
    ]
    return {"title_he": "ביצועים (מוצהר מול נמדד)", "headers": headers, "rows": rows}


def other_specifications_entry(dossier: ProductDossierOut) -> dict[str, Any]:
    """Section 5.1's "פרמטרים נוספים" appendix -- the ``other_specifications`` overflow bucket
    (section 3.3 item 3), unsorted/unstyled, exactly the free-text row rendering ``specifications``
    used before this vocabulary rollout."""
    if not dossier.other_specifications:
        return {"title_he": "פרמטרים נוספים", "body_he": PLACEHOLDER_HE}
    headers = ["פרמטר", "ערך", "יחידה/וריאנט", "סוג מקור", "מקור"]
    rows = [
        [
            r.parameter_he,
            _cell(r.value),
            " / ".join(x for x in (r.unit, r.variant) if x) or "—",
            r.source_kind,
            _cite_cell(r.cites),
        ]
        for r in dossier.other_specifications
    ]
    return {"title_he": "פרמטרים נוספים", "headers": headers, "rows": rows}


def spec_and_performance_entries(
    dossier: ProductDossierOut,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The single entry point ``eoa.dossier.report`` calls: ``(specifications_entry,
    performance_entry, other_specifications_entry)`` -- report-table-entry dicts ready to splice
    into ``_ordered_report_entries``'s own list, replacing that module's former (now removed)
    ``_specifications_table``/``_performance_table`` helpers."""
    return specifications_entry(dossier), performance_entry(dossier), other_specifications_entry(dossier)


__all__ = [
    "other_specifications_entry",
    "performance_entry",
    "spec_and_performance_entries",
    "specifications_entry",
]
