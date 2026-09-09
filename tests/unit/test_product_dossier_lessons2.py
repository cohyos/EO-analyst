"""LESSONS-2 (2026-09-09, docs/qa/content_review/LESSONS-fable-dossier.md): tests for the seven
new dossier sections -- timeline, pricing_estimate, claims_review, per-row confidence, gaps_tracking,
grouped sources / methodology appendices, and the variants/platforms restructure.

Run with: ``PYTHONUTF8=1 PYTHONPATH=agent .venv/Scripts/python.exe -m pytest
tests/unit/test_product_dossier_lessons2.py -q``
"""

from __future__ import annotations

from eoa.dossier import extract as dossier_extract
from eoa.dossier import report as dossier_report
from eoa.dossier.corpus import CorpusResult
from eoa.llm.schemas.product_dossier import (
    AssumptionRow,
    ClaimReviewRow,
    CompetitorRow,
    DealRow,
    IdentityBlock,
    MarketAnchorRow,
    PartnerRow,
    PlatformRow,
    PriceRow,
    PricingEstimateBlock,
    ProductDossierOut,
    SpecRow,
    TimelineRow,
    VersionRow,
)


def _corpus(**kwargs) -> CorpusResult:
    base = dict(product_key="x", product_name="X", vendor="V", aliases=[], terms=[])
    base.update(kwargs)
    return CorpusResult(**base)


# --------------------------------------------------------------------------
# item 4: per-row confidence
# --------------------------------------------------------------------------


def test_row_confidence_high_for_two_independent_citations() -> None:
    assert dossier_extract._row_confidence_he([1, 2]) == "high"


def test_row_confidence_high_for_qualifying_source_kind_even_with_one_cite() -> None:
    assert dossier_extract._row_confidence_he([1], "datasheet") == "high"


def test_row_confidence_medium_for_single_unqualified_citation() -> None:
    assert dossier_extract._row_confidence_he([1], "article") == "medium"


def test_row_confidence_low_for_no_citations() -> None:
    assert dossier_extract._row_confidence_he([]) == "low"


def test_ground_spec_row_sets_confidence_from_cites_and_source_kind() -> None:
    row = SpecRow(parameter_he="p", value="10 ק\"מ", source_kind="datasheet", cites=[1])
    grounded = dossier_extract._ground_spec_row(row, {1}, {1: "ערך 10 ק\"מ"}, [])
    assert grounded.confidence == "high"


def test_ground_deal_row_sets_confidence_level() -> None:
    row = DealRow(customer="AF", amount="", cites=[1, 2])
    grounded = dossier_extract._ground_deal_row(row, {1, 2}, {1: "", 2: ""}, [], published_by_n={})
    assert grounded.confidence_level == "high"


def test_dossier_level_confidence_is_weighted_share_of_high_rows() -> None:
    from eoa.dossier.plan import PlanResult

    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        specifications=[
            SpecRow(parameter_he="a", value="1", cites=[1, 2], confidence="high"),
            SpecRow(parameter_he="b", value="2", cites=[1], confidence="medium"),
        ],
    )
    _outcome, confidence = dossier_report._compute_outcome_confidence(dossier, PlanResult(findings=[]))
    assert confidence == 0.5


# --------------------------------------------------------------------------
# item 1: timeline
# --------------------------------------------------------------------------


def test_build_timeline_includes_launch_contract_and_variant_rows() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X", first_announced="2016-01-01", cites=[1]),
        deals=[DealRow(customer="AF", date="2020-05-01", amount="$1M", cites=[2])],
        variants_and_versions=[VersionRow(name="Block II", year="2019", cites=[3])],
    )
    timeline = dossier_extract.build_timeline(dossier)
    kinds = [(r.kind, r.date) for r in timeline]
    assert ("launch", "2016-01-01") in kinds
    assert ("contract", "2020-05-01") in kinds
    assert ("variant", "2019") in kinds
    # chronological order
    dates = [r.date for r in timeline]
    assert dates == sorted(dates)


def test_build_timeline_skips_undated_or_uncited_deals() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        deals=[DealRow(customer="AF", date=None, amount="$1M", cites=[1]), DealRow(customer="AF2", date="2021", cites=[])],
    )
    assert dossier_extract.build_timeline(dossier) == []


def test_build_timeline_folds_in_programme_deals_when_corpus_has_them() -> None:
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"))
    corpus = _corpus(
        programme_deals=[
            {
                "platform": "Watchkeeper X",
                "customer": "Romania",
                "date": "2026-02-01",
                "amount_text": "כ-410 מיליון דולר",
                "cites": [5],
                "component_of_package": True,
            }
        ]
    )
    timeline = dossier_extract.build_timeline(dossier, corpus)
    assert len(timeline) == 1
    assert timeline[0].kind == "contract"
    assert "Watchkeeper X" in timeline[0].event_he
    assert timeline[0].cites == [5]


def test_ground_timeline_drops_undated_and_uncited_llm_rows() -> None:
    dropped: list = []
    rows = [
        TimelineRow(date="2021-01-01", event_he="שילוב", kind="integration", cites=[1]),
        TimelineRow(date=None, event_he="בלי תאריך", kind="milestone", cites=[1]),
        TimelineRow(date="2021", event_he="בלי ציטוט", kind="milestone", cites=[]),
    ]
    kept = dossier_extract._ground_timeline(rows, {1}, dropped)
    assert len(kept) == 1
    assert kept[0].event_he == "שילוב"


def test_timeline_table_renders_placeholder_when_empty() -> None:
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"))
    tbl = dossier_report._timeline_table(dossier)
    assert tbl["body_he"] == dossier_report.PLACEHOLDER_HE


# --------------------------------------------------------------------------
# item 2: pricing_estimate
# --------------------------------------------------------------------------


def _est(**kwargs) -> PricingEstimateBlock:
    base = dict(
        method_he="שיטה",
        assumptions=[AssumptionRow(text_he="הנחה", cites=[1])],
        market_anchors=[MarketAnchorRow(product_he="MX-15", price_range_he="1-2M$", cites=[1])],
        range_low=1_000_000.0,
        range_high=2_000_000.0,
        currency="USD",
    )
    base.update(kwargs)
    return PricingEstimateBlock(**base)


def test_pricing_estimate_kept_when_gate_conditions_met() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        deals=[DealRow(customer="AF", amount="$1M", quantity="10", cites=[1])],
        pricing_estimate=_est(),
    )
    dropped: list = []
    kept = dossier_extract._ground_pricing_estimate(dossier.pricing_estimate, {1}, dossier, dropped)
    assert kept is not None
    assert kept.confidence == "low"


def test_pricing_estimate_dropped_without_market_anchor() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        deals=[DealRow(customer="AF", amount="$1M", quantity="10", cites=[1])],
        pricing_estimate=_est(market_anchors=[]),
    )
    dropped: list = []
    kept = dossier_extract._ground_pricing_estimate(dossier.pricing_estimate, {1}, dossier, dropped)
    assert kept is None
    assert any(d.field == "pricing_estimate" for d in dropped)


def test_pricing_estimate_dropped_without_contract_total_with_scope() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        deals=[DealRow(customer="AF", amount="$1M", cites=[1])],  # no quantity/platform
        pricing_estimate=_est(),
    )
    dropped: list = []
    kept = dossier_extract._ground_pricing_estimate(dossier.pricing_estimate, {1}, dossier, dropped)
    assert kept is None


def test_pricing_estimate_kept_via_totalled_price_row() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        pricing=[
            PriceRow(
                figure="$2M", basis_he=dossier_extract.BASIS_TOTAL_HE, source_kind="contract", cites=[1]
            )
        ],
        pricing_estimate=_est(),
    )
    dropped: list = []
    kept = dossier_extract._ground_pricing_estimate(dossier.pricing_estimate, {1}, dossier, dropped)
    assert kept is not None


def test_pricing_estimate_entries_always_two_and_render_disclaimer() -> None:
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"), pricing_estimate=None)
    entries = dossier_report._pricing_estimate_entries(dossier)
    assert len(entries) == 2
    assert entries[0]["body_he"] == dossier_report.PLACEHOLDER_HE

    dossier2 = ProductDossierOut(identity=IdentityBlock(product_name="X"), pricing_estimate=_est())
    entries2 = dossier_report._pricing_estimate_entries(dossier2)
    assert len(entries2) == 2
    assert dossier_report.PRICING_ESTIMATE_DISCLAIMER_HE in entries2[0]["body_he"]
    assert "headers" in entries2[1]


# --------------------------------------------------------------------------
# item 3: claims_review
# --------------------------------------------------------------------------


def test_claims_review_capped_at_five_grounded_rows() -> None:
    rows = [ClaimReviewRow(claim_he=f"claim {i}", cites=[1]) for i in range(7)]
    dropped: list = []
    kept = dossier_extract._ground_claims_review(rows, {1}, dropped)
    assert len(kept) == 5


def test_claims_review_row_dropped_without_citation() -> None:
    rows = [ClaimReviewRow(claim_he="x", cites=[])]
    dropped: list = []
    kept = dossier_extract._ground_claims_review(rows, {1}, dropped)
    assert kept == []
    assert any(d.field == "claims_review" for d in dropped)


def test_claims_review_table_placeholder_when_empty() -> None:
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"))
    tbl = dossier_report._claims_review_table(dossier)
    assert tbl["body_he"] == dossier_report.PLACEHOLDER_HE


# --------------------------------------------------------------------------
# item 5: gaps_tracking
# --------------------------------------------------------------------------


def test_build_gaps_tracking_raw_merges_gap_status_and_new_gaps() -> None:
    from eoa.llm.schemas.analysis import Sentence

    corpus = _corpus(gap_status=[{"gap": "מחיר יחידה", "status": "closed", "cites": [1]}])
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        risks_and_gaps_he=[Sentence(text_he="פער חדש שלא היה קודם.", cites=[2])],
    )
    raw = dossier_report._build_gaps_tracking_raw(corpus, dossier)
    statuses = {r["gap"]: r["status"] for r in raw}
    assert statuses["מחיר יחידה"] == "closed"
    assert statuses["פער חדש שלא היה קודם."] == "new"


def test_build_gaps_tracking_raw_empty_without_gap_status_or_new_gaps() -> None:
    corpus = _corpus()
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"))
    assert dossier_report._build_gaps_tracking_raw(corpus, dossier) == []


def test_gaps_tracking_table_placeholder_when_empty() -> None:
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"))
    tbl = dossier_report._gaps_tracking_table(dossier)
    assert tbl["body_he"] == dossier_report.PLACEHOLDER_HE


# --------------------------------------------------------------------------
# item 6: grouped sources + methodology
# --------------------------------------------------------------------------


def test_source_group_classification() -> None:
    assert dossier_report._source_group_he({"kind": "patent"}) == "פטנטים"
    assert dossier_report._source_group_he({"kind": "tender"}) == "מכרזים"
    assert dossier_report._source_group_he({"kind": "web", "topic": "competitors"}) == "מתחרים"
    assert dossier_report._source_group_he({"kind": "web", "source_kind": "vendor_official"}) == "יצרן ועלונים"
    assert dossier_report._source_group_he({"kind": "web", "source_kind": "trade_press"}) == "עיתונות ביטחונית וכלכלית"
    assert dossier_report._source_group_he({"kind": "web", "source_kind": "reference"}) == "מחקר ודוחות שוק"
    assert dossier_report._source_group_he({"kind": "web", "source_kind": "forum"}) == "אחר"


def test_grouped_sources_table_placeholder_without_corpus() -> None:
    tbl = dossier_report._grouped_sources_table(None)
    assert tbl["body_he"] == dossier_report.PLACEHOLDER_HE


def test_grouped_sources_table_groups_and_sorts() -> None:
    corpus = _corpus(
        registry=[
            {"n": 2, "kind": "web", "source_kind": "press", "title": "News"},
            {"n": 1, "kind": "web", "source_kind": "vendor_official", "title": "Vendor"},
            {"n": 3, "kind": "patent", "title": "Patent"},
        ]
    )
    tbl = dossier_report._grouped_sources_table(corpus)
    assert tbl["headers"] == ["קבוצה", "#", "כותרת", "סוג/קישור"]
    groups_in_order = [r[0] for r in tbl["rows"]]
    assert groups_in_order.index("יצרן ועלונים") < groups_in_order.index("פטנטים")


def test_methodology_entry_placeholder_without_corpus() -> None:
    assert dossier_report._methodology_entry(None, None, None)["body_he"] == dossier_report.PLACEHOLDER_HE


def test_methodology_entry_summarizes_progress_and_llm_leg() -> None:
    corpus = _corpus(registry=[{"n": 1, "source_kind": "vendor_official"}])
    progress = [
        {"topic": "specifications", "title_he": "מפרט", "status": "done", "seconds": 120.0, "pages_read": [{"n": 1}]}
    ]
    entry = dossier_report._methodology_entry(corpus, progress, "codex:gpt-6-astra")
    assert "codex:gpt-6-astra" in entry["body_he"]
    assert "מפרט" in entry["body_he"]


# --------------------------------------------------------------------------
# item 7: variants (evidence + confidence) and platforms table
# --------------------------------------------------------------------------


def test_variants_table_has_evidence_and_confidence_columns() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        variants_and_versions=[
            VersionRow(name="Block II", evidence_he="מוזכר בעלון 2023", confidence="high", cites=[1])
        ],
    )
    tbl = dossier_report._variants_table(dossier)
    assert tbl["headers"] == ["גרסה/דגם", "שנה", "שינויים", "עדות", "ביטחון", "מקור"]
    assert "מוזכר בעלון 2023" in tbl["rows"][0]
    assert "גבוה" in tbl["rows"][0]


def test_build_platforms_merges_maturity_and_deal_platforms() -> None:
    from eoa.llm.schemas.product_dossier import MaturityBlock

    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        maturity=MaturityBlock(platforms_integrated=["Hermes 900"], cites=[1]),
        deals=[DealRow(customer="AF", platform="Hermes 900", cites=[2]), DealRow(customer="RO", platform="Watchkeeper X", cites=[3])],
    )
    platforms = dossier_extract.build_platforms(dossier)
    names = {p.platform for p in platforms}
    assert names == {"Hermes 900", "Watchkeeper X"}
    hermes = next(p for p in platforms if p.platform == "Hermes 900")
    assert hermes.integration_evidence_he  # deal-derived evidence wins
    assert 2 in hermes.cites


def test_platforms_table_placeholder_when_empty() -> None:
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="X"))
    tbl = dossier_report._platforms_table(dossier)
    assert tbl["body_he"] == dossier_report.PLACEHOLDER_HE


def test_platforms_table_renders_rows() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        platforms=[PlatformRow(platform="Hermes 900", domain="אווירי", integration_evidence_he="e", cites=[1])],
    )
    tbl = dossier_report._platforms_table(dossier)
    assert tbl["rows"][0][0] == "Hermes 900"


# --------------------------------------------------------------------------
# every new/changed table stays within the report's <= 6-column limit
# --------------------------------------------------------------------------


def test_new_tables_stay_within_six_columns() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="X"),
        variants_and_versions=[VersionRow(name="v", cites=[1])],
        deals=[DealRow(customer="AF", platform="Hermes 900", amount="$1M", quantity="1", cites=[1])],
        partnerships=[PartnerRow(partner="p", cites=[1])],
        competitors=[CompetitorRow(product="c", cites=[1])],
        platforms=[PlatformRow(platform="Hermes 900", cites=[1])],
        timeline=[TimelineRow(date="2020", event_he="e", cites=[1])],
        claims_review=[ClaimReviewRow(claim_he="c", cites=[1])],
        pricing_estimate=_est(),
    )
    builders = [
        dossier_report._variants_table,
        dossier_report._deals_table,
        dossier_report._partnerships_table,
        dossier_report._competitors_table,
        dossier_report._platforms_table,
        dossier_report._timeline_table,
        dossier_report._claims_review_table,
    ]
    for builder in builders:
        tbl = builder(dossier)
        if "headers" not in tbl:
            continue
        assert len(tbl["headers"]) <= 6, tbl["title_he"]
        for row in tbl["rows"]:
            assert len(row) == len(tbl["headers"])
    for entry in dossier_report._pricing_estimate_entries(dossier):
        if "headers" in entry:
            assert len(entry["headers"]) <= 6
