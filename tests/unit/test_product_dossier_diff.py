"""Diff-stage tests for the product dossier (PD-backend, user request 2026-09-08):
``eoa.dossier.diff.compute_diff`` -- "מה השתנה" vs. the previous dossier's raw JSONB ``data``.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_dossier_diff.py -q``
"""

from __future__ import annotations

from eoa.dossier.diff import compute_diff
from eoa.llm.schemas.product_dossier import (
    DealRow,
    IdentityBlock,
    PriceRow,
    ProductDossierOut,
    SpecRow,
    VersionRow,
)


def _current(**kwargs) -> ProductDossierOut:
    return ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"), **kwargs)


def test_no_previous_dossier_returns_none() -> None:
    assert compute_diff(None, _current()) is None


def test_first_run_after_previous_exists_but_empty_lists_returns_empty_list() -> None:
    assert compute_diff({}, _current()) == []


def test_new_deal_detected() -> None:
    previous = {"deals": []}
    current = _current(
        deals=[DealRow(customer="US Air Force", date="2026-02-01", kind="contract_award", cites=[1])]
    )
    changes = compute_diff(previous, current)
    assert len(changes) == 1
    assert "US Air Force" in changes[0].text_he
    assert changes[0].cites == [1]


def test_existing_deal_not_reported_as_new() -> None:
    previous = {"deals": [{"customer": "US Air Force", "date": "2026-02-01", "kind": "contract_award"}]}
    current = _current(
        deals=[DealRow(customer="US Air Force", date="2026-02-01", kind="contract_award", cites=[1])]
    )
    assert compute_diff(previous, current) == []


def test_deal_with_no_cites_never_reported() -> None:
    previous = {"deals": []}
    current = _current(
        deals=[DealRow(customer="US Air Force", date="2026-02-01", kind="contract_award", cites=[])]
    )
    assert compute_diff(previous, current) == []


def test_new_spec_value_change_detected() -> None:
    previous = {"specifications": [{"parameter_he": "משקל", "value": '20 ק"ג'}]}
    current = _current(specifications=[SpecRow(parameter_he="משקל", value='25 ק"ג', cites=[2])])
    changes = compute_diff(previous, current)
    assert len(changes) == 1
    assert "20" in changes[0].text_he and "25" in changes[0].text_he


def test_unchanged_spec_value_not_reported() -> None:
    previous = {"specifications": [{"parameter_he": "משקל", "value": '20 ק"ג'}]}
    current = _current(specifications=[SpecRow(parameter_he="משקל", value='20 ק"ג', cites=[2])])
    assert compute_diff(previous, current) == []


def test_new_version_detected() -> None:
    previous = {"variants_and_versions": []}
    current = _current(variants_and_versions=[VersionRow(name="SPECTRO XR Block II", cites=[3])])
    changes = compute_diff(previous, current)
    assert any("Block II" in c.text_he for c in changes)


def test_new_price_point_detected() -> None:
    previous = {"pricing": []}
    current = _current(pricing=[PriceRow(figure="$2M", basis_he="ליחידה", source_kind="contract", cites=[4])])
    changes = compute_diff(previous, current)
    assert any("$2M" in c.text_he for c in changes)


def test_deals_reported_before_specifications() -> None:
    previous: dict = {"deals": [], "specifications": []}
    current = _current(
        deals=[DealRow(customer="X", date="2026-01-01", kind="contract_award", cites=[1])],
        specifications=[SpecRow(parameter_he="משקל", value="20", cites=[2])],
    )
    changes = compute_diff(previous, current)
    assert "עסקה" in changes[0].text_he
