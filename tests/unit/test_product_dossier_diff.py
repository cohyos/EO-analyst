"""Diff-stage tests for the product dossier (PD-backend, user request 2026-09-08):
``eoa.dossier.diff.compute_diff`` -- "מה השתנה" vs. the previous dossier's raw JSONB ``data``.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_dossier_diff.py -q``
"""

from __future__ import annotations

from eoa.dossier.diff import compute_diff
from eoa.llm.schemas.product_dossier import (
    DealRow,
    IdentityBlock,
    PerformanceRow,
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


# --------------------------------------------------------------------------
# PD-fix-2 item 1: deal identity is (customer, amount, kind) only -- never date/date_kind/region.
# Reproduces the live SPECTRO XR false positive: product_dossiers.id=1 -> id=2, the $270M and $80M
# deals gained a date (backfilled from a source's published_at, or genuinely discovered) between
# runs and were wrongly reported as "new deals" under the old (customer, date, kind) key.
# --------------------------------------------------------------------------


def test_same_deal_with_newly_backfilled_date_not_reported_as_new_deal() -> None:
    previous = {
        "deals": [
            {
                "date": None,
                "kind": "contract_award",
                "amount": "כ-270 מיליון דולר, לביצוע על פני תקופה של עד שש שנים",
                "customer": "לקוח בינלאומי (לא מזוהה)",
            }
        ]
    }
    current = _current(
        deals=[
            DealRow(
                date="2026-09-02",
                date_kind="published",
                kind="contract_award",
                amount="כ-270 מיליון דולר, לביצוע על פני תקופה של עד שש שנים",
                amount_value=270_000_000.0,
                customer="לקוח בינלאומי (לא מזוהה)",
                cites=[1],
            )
        ]
    )
    changes = compute_diff(previous, current)
    assert not any("עסקה חדשה" in c.text_he for c in changes)
    assert any("נוסף תאריך" in c.text_he for c in changes)


def test_same_deal_with_only_region_change_not_reported_at_all() -> None:
    previous = {
        "deals": [
            {
                "date": "2021-06",
                "kind": "contract_award",
                "amount": "כ-80 מיליון דולר",
                "customer": "מדינה באזור אסיה-פסיפיק (לא מזוהה)",
            }
        ]
    }
    current = _current(
        deals=[
            DealRow(
                date="2021-06",
                date_kind="deal",
                region_he="אסיה-פסיפיק",
                kind="contract_award",
                amount="כ-80 מיליון דולר",
                amount_value=80_000_000.0,
                customer="מדינה באזור אסיה-פסיפיק (לא מזוהה)",
                cites=[1],
            )
        ]
    )
    assert compute_diff(previous, current) == []


def test_deal_customer_key_is_case_and_whitespace_insensitive() -> None:
    previous = {
        "deals": [{"date": None, "kind": "contract_award", "amount": "$1M", "customer": "US Air Force"}]
    }
    current = _current(
        deals=[DealRow(customer="  us   air force  ", kind="contract_award", amount="$1M", cites=[1])]
    )
    assert compute_diff(previous, current) == []


def test_deal_key_ignores_previous_run_missing_amount_value_field() -> None:
    """A previous dossier persisted before ``amount_value`` existed (a raw dict with no such key)
    must still key-match the current, already-grounded row for the same published amount text."""
    previous = {
        "deals": [
            {"date": None, "kind": "contract_award", "amount": "מעל 90 מיליון דולר", "customer": "X"}
        ]
    }
    current = _current(
        deals=[
            DealRow(
                customer="X", kind="contract_award", amount="מעל 90 מיליון דולר", amount_value=90_000_000.0,
                cites=[1],
            )
        ]
    )
    assert compute_diff(previous, current) == []


def test_genuinely_different_amount_is_still_a_new_deal() -> None:
    previous = {"deals": [{"date": None, "kind": "contract_award", "amount": "$1M", "customer": "X"}]}
    current = _current(deals=[DealRow(customer="X", kind="contract_award", amount="$5M", cites=[1])])
    changes = compute_diff(previous, current)
    assert any("עסקה חדשה" in c.text_he for c in changes)


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 2): deal identity falls back to (amount, kind) alone when the customer
# is missing/a placeholder on EITHER side. Reproduces the live SPECTRO XR regression (id=2 -> id=3):
# run 2's deal named a real (if unidentified) customer description; run 3's re-extraction of the
# SAME deal lost that text (customer became None/"—") -- it must not read as a new deal, and the
# text must never print the raw "—" placeholder for a customer.
# --------------------------------------------------------------------------


def test_customer_became_placeholder_not_reported_as_new_deal() -> None:
    previous = {
        "deals": [
            {
                "date": "2021-06",
                "kind": "contract_award",
                "amount": "כ-80 מיליון דולר",
                "customer": "מדינה באזור אסיה-פסיפיק (לא מזוהה)",
            }
        ]
    }
    current = _current(
        deals=[
            DealRow(
                date="2021-06",
                kind="contract_award",
                amount="כ-80 מיליון דולר",
                amount_value=80_000_000.0,
                customer=None,
                cites=[1],
            )
        ]
    )
    assert compute_diff(previous, current) == []


def test_customer_was_placeholder_now_identified_not_reported_as_new_deal() -> None:
    """Symmetric case: the previous run had no customer at all, the current run identifies one --
    same deal (amount+kind match), not a new one."""
    previous = {
        "deals": [
            {
                "date": "2021-06",
                "kind": "contract_award",
                "amount": "$1 million",
                "amount_value": 1_000_000.0,
                "customer": "—",
            }
        ]
    }
    current = _current(
        deals=[
            DealRow(
                date="2021-06",
                kind="contract_award",
                amount="$1 million",
                amount_value=1_000_000.0,
                customer="US Air Force",
                cites=[1],
            )
        ]
    )
    assert compute_diff(previous, current) == []


def test_new_deal_with_no_customer_never_prints_raw_dash() -> None:
    previous = {"deals": []}
    current = _current(
        deals=[DealRow(customer=None, kind="contract_award", amount="$5M", cites=[1])]
    )
    changes = compute_diff(previous, current)
    assert len(changes) == 1
    assert "—" not in changes[0].text_he
    assert "לקוח לא צוין" in changes[0].text_he


def test_two_different_real_customers_with_different_amounts_still_distinct() -> None:
    """Sanity check the fallback doesn't erase identity when both sides DO name real, different
    customers with genuinely different amounts."""
    previous = {"deals": [{"date": None, "kind": "contract_award", "amount": "$1M", "customer": "US Air Force"}]}
    current = _current(
        deals=[DealRow(customer="Royal Air Force", kind="contract_award", amount="$5M", cites=[1])]
    )
    changes = compute_diff(previous, current)
    assert any("עסקה חדשה" in c.text_he for c in changes)


# --------------------------------------------------------------------------
# PD-fix-2 item 2: token-overlap "same value" -- mere rewording must not read as a change.
# --------------------------------------------------------------------------


def test_spec_reworded_value_not_reported_as_change() -> None:
    previous = {
        "specifications": [
            {"parameter_he": "חיישנים", "value": "חיישני IMU בסיבים אופטיים על הגימבל"}
        ]
    }
    current = _current(
        specifications=[
            SpecRow(
                parameter_he="חיישנים",
                value="חיישני IMU בסיבים אופטיים המורכבים על הגימבל",
                cites=[2],
            )
        ]
    )
    assert compute_diff(previous, current) == []


def test_spec_genuinely_different_value_still_reported() -> None:
    previous = {"specifications": [{"parameter_he": "משקל", "value": '20 ק"ג'}]}
    current = _current(specifications=[SpecRow(parameter_he="משקל", value='95 ק"ג', cites=[2])])
    changes = compute_diff(previous, current)
    assert len(changes) == 1
    assert "20" in changes[0].text_he and "95" in changes[0].text_he


def test_performance_reworded_claimed_value_not_reported() -> None:
    previous = {
        "performance": [
            {"metric_he": "טווח זיהוי", "claimed_value": "עד 20 ק\"מ ביום בהיר"}
        ]
    }
    current = _current(
        performance=[
            PerformanceRow(metric_he="טווח זיהוי", claimed_value="עד 20 ק\"מ ביום בהיר וללא ערפל", cites=[3])
        ]
    )
    assert compute_diff(previous, current) == []


def test_performance_genuinely_different_claimed_value_reported() -> None:
    previous = {"performance": [{"metric_he": "טווח זיהוי", "claimed_value": '20 ק"מ'}]}
    current = _current(
        performance=[PerformanceRow(metric_he="טווח זיהוי", claimed_value='36 ק"מ', cites=[3])]
    )
    changes = compute_diff(previous, current)
    assert any("טווח זיהוי" in c.text_he and "20" in c.text_he and "36" in c.text_he for c in changes)


def test_performance_new_metric_detected() -> None:
    previous = {"performance": []}
    current = _current(
        performance=[PerformanceRow(metric_he="קצב זיהוי", claimed_value="95%", cites=[3])]
    )
    changes = compute_diff(previous, current)
    assert any("קצב זיהוי" in c.text_he for c in changes)


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 3): fuzzy spec/performance row matching -- the extraction model
# re-words parameter/metric names between runs (live SPECTRO XR: "ביצועי אופטיקה" vs. "ביצועי עומס
# אופטי במארז קומפקטי", 5 false "new parameter" entries). A previous row counts as the same
# parameter when its NAME is a fuzzy (token-Jaccard >= 0.5) match, OR when its VALUE is already
# effectively the same (>= 0.6) -- either alone is enough.
# --------------------------------------------------------------------------


def test_spec_renamed_parameter_same_value_not_reported_as_new() -> None:
    """The live case verbatim: the parameter name is reworded almost beyond recognition (name
    Jaccard well under 0.5), but the published value itself didn't change -- caught by the VALUE
    side of the OR, not the name side."""
    previous = {"specifications": [{"parameter_he": "ביצועי אופטיקה", "value": '10 ק"מ'}]}
    current = _current(
        specifications=[
            SpecRow(parameter_he="ביצועי עומס אופטי במארז קומפקטי", value='10 ק"מ', cites=[2])
        ]
    )
    assert compute_diff(previous, current) == []


def test_spec_fuzzy_renamed_parameter_name_with_changed_value_reports_value_change() -> None:
    """A moderate rewording (name Jaccard >= 0.5) whose value DID genuinely change -- still matched
    as the same parameter (by name), and the value change is reported (not a fabricated "new
    parameter")."""
    previous = {"specifications": [{"parameter_he": "טווח זיהוי MWIR", "value": '10 ק"מ'}]}
    current = _current(
        specifications=[SpecRow(parameter_he="טווח זיהוי", value='25 ק"מ', cites=[2])]
    )
    changes = compute_diff(previous, current)
    assert len(changes) == 1
    assert "פרמטר מפרט חדש" not in changes[0].text_he
    assert "10" in changes[0].text_he and "25" in changes[0].text_he


def test_spec_genuinely_new_parameter_neither_name_nor_value_match() -> None:
    previous = {"specifications": [{"parameter_he": "משקל", "value": '20 ק"ג'}]}
    current = _current(
        specifications=[SpecRow(parameter_he="צריכת הספק", value="50W", cites=[2])]
    )
    changes = compute_diff(previous, current)
    assert any("פרמטר מפרט חדש" in c.text_he and "צריכת הספק" in c.text_he for c in changes)


def test_performance_renamed_metric_same_claimed_value_not_reported_as_new() -> None:
    previous = {"performance": [{"metric_he": "ביצועי אופטיקה", "claimed_value": '20 ק"מ'}]}
    current = _current(
        performance=[
            PerformanceRow(
                metric_he="ביצועי עומס אופטי במארז קומפקטי", claimed_value='20 ק"מ', cites=[3]
            )
        ]
    )
    assert compute_diff(previous, current) == []


def test_performance_genuinely_new_metric_neither_name_nor_value_match() -> None:
    previous = {"performance": [{"metric_he": "טווח זיהוי", "claimed_value": '20 ק"מ'}]}
    current = _current(
        performance=[PerformanceRow(metric_he="קצב זיהוי שגוי", claimed_value="12%", cites=[3])]
    )
    changes = compute_diff(previous, current)
    assert any("מדד ביצועים חדש" in c.text_he and "קצב זיהוי שגוי" in c.text_he for c in changes)


def test_deals_reported_before_specifications() -> None:
    previous: dict = {"deals": [], "specifications": []}
    current = _current(
        deals=[DealRow(customer="X", date="2026-01-01", kind="contract_award", cites=[1])],
        specifications=[SpecRow(parameter_he="משקל", value="20", cites=[2])],
    )
    changes = compute_diff(previous, current)
    assert "עסקה" in changes[0].text_he
