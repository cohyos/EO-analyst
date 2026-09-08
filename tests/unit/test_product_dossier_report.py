"""Renderer + plan-topic tests for the product dossier (PD-backend, user request 2026-09-08):
``eoa.dossier.report``'s table builders (<= 6 columns, "לא נמצא במקורות" placeholders) and
``eoa.dossier.plan.build_topics`` (deterministic, no-network query construction).

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_dossier_report.py -q``
"""

from __future__ import annotations

from typing import Any

import pytest

from eoa.api import services
from eoa.dossier import plan as dossier_plan
from eoa.dossier import report as dossier_report
from eoa.llm.schemas.product_dossier import (
    DealRow,
    IdentityBlock,
    PartnerRow,
    PerformanceRow,
    PriceRow,
    ProductDossierOut,
    SpecRow,
    VersionRow,
)

_ALL_TABLE_BUILDERS = [
    dossier_report._specifications_table,
    dossier_report._variants_table,
    dossier_report._performance_table,
    dossier_report._deals_table,
    dossier_report._pricing_table,
    dossier_report._partnerships_table,
    dossier_report._competitors_table,
    dossier_report._patents_table,
    dossier_report._tenders_table,
]


def test_empty_dossier_every_table_has_placeholder_body() -> None:
    """PD-fix (2026-09-08, item 4): a table builder never returns ``None`` any more -- an empty
    section always renders its own single placeholder line (``body_he``, no ``headers``/``rows``)
    instead of being silently omitted from the report."""
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"))
    for builder in _ALL_TABLE_BUILDERS:
        entry = builder(dossier)
        assert entry is not None
        assert "headers" not in entry
        assert entry["body_he"] == dossier_report.PLACEHOLDER_HE


def _sample_dossier() -> ProductDossierOut:
    return ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR", vendor="Elbit Systems"),
        specifications=[SpecRow(parameter_he="משקל", value="", unit="", variant="", cites=[1])],
        variants_and_versions=[VersionRow(name="Block II", cites=[1])],
        performance=[PerformanceRow(metric_he="טווח", claimed_value='10 ק"מ', cites=[1])],
        deals=[DealRow(customer="US Air Force", cites=[1])],
        pricing=[PriceRow(figure="$2M", basis_he="ליחידה", source_kind="contract", cites=[1])],
        partnerships=[PartnerRow(partner="Partner Co", cites=[1])],
    )


def test_tables_stay_within_six_columns() -> None:
    dossier = _sample_dossier()
    for builder in _ALL_TABLE_BUILDERS:
        tbl = builder(dossier)
        if "headers" not in tbl:
            # An empty section for this dossier fixture (e.g. competitors/patents/tenders) --
            # placeholder body_he, not a real table; nothing to column-count here.
            continue
        assert len(tbl["headers"]) <= 6, tbl["title_he"]
        for row in tbl["rows"]:
            assert len(row) == len(tbl["headers"])


def test_missing_value_renders_placeholder() -> None:
    dossier = _sample_dossier()
    tbl = dossier_report._specifications_table(dossier)
    assert tbl is not None
    assert dossier_report.PLACEHOLDER_HE in tbl["rows"][0]


def test_cite_cell_formats_numbers() -> None:
    assert dossier_report._cite_cell([1, 2]) == "[1], [2]"
    assert dossier_report._cite_cell([]) == "—"


def test_signal_count_and_outcome_not_found_for_empty_dossier() -> None:
    from eoa.dossier.plan import PlanResult

    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"))
    outcome, confidence = dossier_report._compute_outcome_confidence(dossier, PlanResult(findings=[]))
    assert outcome == "not_found"
    assert confidence <= 0.3


def test_outcome_found_when_many_signals_present() -> None:
    from eoa.dossier.plan import PlanResult
    from eoa.llm.schemas.analysis import Sentence

    dossier = _sample_dossier()
    dossier = dossier.model_copy(update={"summary_he": [Sentence(text_he="עובדה.", cites=[1])]})
    outcome, _confidence = dossier_report._compute_outcome_confidence(dossier, PlanResult(findings=[]))
    assert outcome in ("found", "partial")


# --------------------------------------------------------------------------
# plan.build_topics -- deterministic, no network
# --------------------------------------------------------------------------


def test_build_topics_names_product_in_every_question() -> None:
    topics = dossier_plan.build_topics("SPECTRO XR", "Elbit Systems", ["Spectro"])
    assert len(topics) == len(dossier_plan.TOPICS)
    for t in topics:
        assert "SPECTRO XR" in t["question_he"]
        assert "Elbit Systems" in t["question_he"]


def test_build_topics_respects_max_topics_cap() -> None:
    topics = dossier_plan.build_topics("SPECTRO XR", "Elbit Systems", [], max_topics=3)
    assert len(topics) == 3


def test_build_topics_no_vendor_still_valid() -> None:
    topics = dossier_plan.build_topics("MOSP 5000", None, [])
    assert all(t["question_he"] for t in topics)


# --------------------------------------------------------------------------
# PD-fix (2026-09-08, item 4): the full section order -- always 15 entries, in the plan's exact
# order, every one present even for a fully empty dossier.
# --------------------------------------------------------------------------

_EXPECTED_ORDER_HE = [
    "זיהוי המוצר",
    "מפרט",
    "גרסאות",
    "ביצועים (מוצהר מול נמדד)",
    "בשלות ופריסה",
    "עסקאות",
    "מחירים",
    "שותפויות",
    "מתחרים",
    "פטנטים",
    "מכרזים ותחזיות",
    "רגולציה וייצוא",
    "פערים ואי-ודאויות",
    "משמעות עסקית",
    "מה השתנה",
]


def test_ordered_report_entries_follow_plan_order_for_empty_dossier() -> None:
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"))
    entries = dossier_report._ordered_report_entries(dossier)
    assert [e["title_he"] for e in entries] == _EXPECTED_ORDER_HE
    # Every section renders SOMETHING -- never omitted.
    for entry in entries:
        assert entry.get("body_he") or entry.get("rows") is not None


def test_ordered_report_entries_follow_plan_order_for_full_dossier() -> None:
    dossier = _sample_dossier()
    entries = dossier_report._ordered_report_entries(dossier)
    assert [e["title_he"] for e in entries] == _EXPECTED_ORDER_HE


def test_what_changed_entry_distinguishes_first_run_from_no_change() -> None:
    from eoa.llm.schemas.analysis import Sentence

    first_run = ProductDossierOut(identity=IdentityBlock(product_name="X"), what_changed_he=None)
    entry = dossier_report._what_changed_entry(first_run)
    assert "ראשונה" in entry["body_he"]

    no_change = ProductDossierOut(identity=IdentityBlock(product_name="X"), what_changed_he=[])
    entry = dossier_report._what_changed_entry(no_change)
    assert "לא זוהו שינויים" in entry["body_he"]

    changed = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        what_changed_he=[Sentence(text_he="עסקה חדשה זוהתה.", cites=[1])],
    )
    entry = dossier_report._what_changed_entry(changed)
    assert "עסקה חדשה זוהתה." in entry["body_he"]
    assert "[1]" in entry["body_he"]


# --------------------------------------------------------------------------
# PD-fix item 3: deal-cell renderer helpers
# --------------------------------------------------------------------------


def test_deal_amount_cell_appends_parsed_value() -> None:
    deal = DealRow(customer="x", amount="כ-80 מיליון דולר", amount_value=80_000_000.0, currency="USD")
    cell = dossier_report._deal_amount_cell(deal)
    assert "כ-80 מיליון דולר" in cell
    assert "80,000,000" in cell
    assert "USD" in cell


def test_deal_amount_cell_placeholder_when_empty() -> None:
    deal = DealRow(customer="x")
    assert dossier_report._deal_amount_cell(deal) == dossier_report.PLACEHOLDER_HE


def test_deal_date_cell_flags_published_fallback() -> None:
    deal = DealRow(customer="x", date="2026-03-01", date_kind="published")
    cell = dossier_report._deal_date_cell(deal)
    assert "2026-03-01" in cell
    assert "תאריך פרסום" in cell


def test_deal_date_cell_plain_for_actual_deal_date() -> None:
    deal = DealRow(customer="x", date="2026-03-01", date_kind="deal")
    assert dossier_report._deal_date_cell(deal) == "2026-03-01"


def test_deals_table_uses_region_when_country_empty() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        deals=[DealRow(customer="Some AF", country="", region_he="מדינה באסיה-פסיפיק", cites=[1])],
    )
    tbl = dossier_report._deals_table(dossier)
    assert "מדינה באסיה-פסיפיק" in tbl["rows"][0]


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 4): the deals table's customer cell renders "לא צוין", never the raw
# "—"/None placeholder -- and never the generic "לא נמצא במקורות" placeholder either (the deal row
# itself IS grounded; only the customer's identity is unknown).
# --------------------------------------------------------------------------


def test_deal_customer_cell_placeholder_for_none() -> None:
    deal = DealRow(customer=None)
    assert dossier_report._deal_customer_cell(deal) == dossier_report.CUSTOMER_PLACEHOLDER_HE


def test_deal_customer_cell_real_name_kept_verbatim() -> None:
    deal = DealRow(customer="US Air Force")
    assert dossier_report._deal_customer_cell(deal) == "US Air Force"


def test_deals_table_customer_column_never_shows_raw_dash() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"), deals=[DealRow(customer=None, cites=[1])]
    )
    tbl = dossier_report._deals_table(dossier)
    customer_cell = tbl["rows"][0][1]
    assert customer_cell == "לא צוין"
    assert customer_cell != "—"


# --------------------------------------------------------------------------
# PD-fix-2 item 3: date-only rendering + Hebrew deal-kind labels
# --------------------------------------------------------------------------


def test_date_only_strips_time_and_timezone() -> None:
    assert dossier_report._date_only("2026-09-02 09:04:00+03:00") == "2026-09-02"
    assert dossier_report._date_only("2026-09-02T09:04:00+03:00") == "2026-09-02"


def test_date_only_preserves_partial_precision_dates() -> None:
    assert dossier_report._date_only("2021-06") == "2021-06"
    assert dossier_report._date_only("2021") == "2021"


def test_date_only_passes_through_none_and_non_date_text() -> None:
    assert dossier_report._date_only(None) is None
    assert dossier_report._date_only("לא ידוע") == "לא ידוע"


def test_deal_date_cell_strips_time_from_published_fallback() -> None:
    deal = DealRow(customer="x", date="2026-09-02 09:04:00+03:00", date_kind="published")
    cell = dossier_report._deal_date_cell(deal)
    assert "2026-09-02" in cell
    assert "09:04" not in cell
    assert "תאריך פרסום" in cell


def test_deal_kind_label_reuses_shared_contract_award_label() -> None:
    from eoa.report.docx_builder import _EVENT_KIND_LABELS_HE

    assert dossier_report._deal_kind_label("contract_award") == _EVENT_KIND_LABELS_HE["contract_award"]


def test_deal_kind_label_covers_deal_only_kinds() -> None:
    for kind in ("FMS", "framework", "option", "export_license"):
        label = dossier_report._deal_kind_label(kind)
        assert label and label != kind


def test_deal_kind_label_falls_back_to_raw_kind_when_unrecognised() -> None:
    assert dossier_report._deal_kind_label("mystery_kind") == "mystery_kind"


def test_deals_table_renders_hebrew_kind_label_not_raw_kind() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        deals=[DealRow(customer="Some AF", kind="contract_award", cites=[1])],
    )
    tbl = dossier_report._deals_table(dossier)
    assert tbl["rows"][0][3] == dossier_report._deal_kind_label("contract_award")
    assert tbl["rows"][0][3] != "contract_award"


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 2): progress persists to jobs.payload (not jobs.result, which
# eoa.orchestrator.jobs's finish_job replaces wholesale on completion -- confirmed empty on the
# live job 194 row). Round-trip: two simulated on_progress topic callbacks through
# dossier_report._write_job_progress, read back through eoa.api.services (the same path
# GET /api/dossiers/{key} uses for pending_job.progress).
# --------------------------------------------------------------------------


class _FakeJobsCursor:
    """Applies the same jsonb ``||`` merge semantics Postgres would for
    ``UPDATE jobs SET payload = COALESCE(payload, '{}'::jsonb) || %(patch)s::jsonb WHERE id = ...
    AND state = 'running'`` against an in-memory ``{job_id: row}`` table -- no live Postgres
    needed, same "fake DB" convention as ``tests/unit/test_backfill_analysis_gaps.py``."""

    def __init__(self, table: dict[int, dict[str, Any]]) -> None:
        self._table = table

    def execute(self, query: str, params: dict[str, Any] | None = None) -> None:
        params = params or {}
        assert "UPDATE jobs SET payload" in query
        assert "state = 'running'" in query
        row = self._table.get(params["id"])
        if row is None or row.get("state") != "running":
            return
        patch = params["patch"]
        patch_dict = patch.obj if hasattr(patch, "obj") else patch
        row["payload"] = {**(row.get("payload") or {}), **patch_dict}

    def __enter__(self) -> _FakeJobsCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeJobsConnection:
    def __init__(self, table: dict[int, dict[str, Any]]) -> None:
        self._table = table

    def cursor(self, row_factory: Any = None) -> _FakeJobsCursor:
        return _FakeJobsCursor(self._table)

    def __enter__(self) -> _FakeJobsConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_write_job_progress_round_trips_through_service(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs_table: dict[int, dict[str, Any]] = {
        99: {"id": 99, "state": "running", "payload": {"product_key": "elbit-spectro-xr"}}
    }
    monkeypatch.setattr(dossier_report, "connection", lambda: _FakeJobsConnection(jobs_table))

    # Two topic callbacks, as eoa.dossier.plan.run_plan's on_progress would emit while working
    # through TOPICS in order: first "specifications" starts running, then it finishes and
    # "performance" starts.
    dossier_report._write_job_progress(
        99,
        [
            {"topic": "specifications", "title_he": "מפרט", "status": "running", "seconds": None, "sources_found": None},
            {"topic": "performance", "title_he": "ביצועים", "status": "pending", "seconds": None, "sources_found": None},
        ],
    )
    dossier_report._write_job_progress(
        99,
        [
            {"topic": "specifications", "title_he": "מפרט", "status": "done", "seconds": 8.5, "sources_found": 3},
            {"topic": "performance", "title_he": "ביצועים", "status": "running", "seconds": None, "sources_found": None},
        ],
    )

    # The enqueue-time payload keys (product_key) must survive the merge, not just progress.
    assert jobs_table[99]["payload"]["product_key"] == "elbit-spectro-xr"

    def fake_fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
        assert "jobs" in query
        row = jobs_table[99]
        return {"id": row["id"], "state": row["state"], "payload": row["payload"]}

    monkeypatch.setattr(services, "_fetchone", fake_fetchone)
    pending = services._pending_dossier_job("elbit-spectro-xr")
    assert pending is not None
    assert pending["job_id"] == 99
    assert pending["progress"] == [
        {"topic": "specifications", "title_he": "מפרט", "status": "done", "seconds": 8.5, "sources_found": 3},
        {"topic": "performance", "title_he": "ביצועים", "status": "running", "seconds": None, "sources_found": None},
    ]
