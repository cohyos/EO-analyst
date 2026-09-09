"""Extraction post-check tests for the product dossier (PD-backend, user request 2026-09-08):
``eoa.dossier.extract.ground_dossier`` -- the deterministic, code-only checks that run AFTER the
LLM's structured-extraction call (never mocked here; ``ground_dossier`` takes an already-built
:class:`~eoa.llm.schemas.product_dossier.ProductDossierOut` draft, no network/DB/LLM involved).

Covers the three worked cases from the task brief: a number not in source -> field null; an
invented competitor -> dropped; a price without basis -> dropped.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_dossier_extract.py -q``
"""

from __future__ import annotations

from typing import Any

from eoa.dossier import extract as dossier_extract
from eoa.dossier.corpus import CorpusResult
from eoa.dossier.plan import PlanResult
from eoa.llm.schemas.analysis import Sentence
from eoa.llm.schemas.product_dossier import (
    CompetitorRow,
    DealRow,
    DossierPatentRow,
    GapTrackingRow,
    IdentityBlock,
    PerformanceRow,
    PriceRow,
    ProductDossierOut,
    SpecRow,
    VersionRow,
)


def _corpus_with_registry(registry: list[dict[str, Any]], **items_kwargs: Any) -> CorpusResult:
    return CorpusResult(
        product_key="elbit-spectro-xr",
        product_name="SPECTRO XR",
        vendor="Elbit Systems",
        aliases=["Spectro"],
        terms=["SPECTRO XR", "Spectro"],
        registry=registry,
        **items_kwargs,
    )


_EMPTY_PLAN = PlanResult(findings=[])


def _reg(n: int, kind: str = "item", **extra: Any) -> dict[str, Any]:
    return {"n": n, "kind": kind, "id": n, "title": f"title-{n}", "url": None, "source_name": "src", **extra}


# --------------------------------------------------------------------------
# cites validity
# --------------------------------------------------------------------------


def test_out_of_range_cites_are_stripped_from_summary_sentence() -> None:
    corpus = _corpus_with_registry([_reg(1)])
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        summary_he=[Sentence(text_he="עובדה תקינה.", cites=[1, 99])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.summary_he) == 1
    assert result.dossier.summary_he[0].cites == [1]
    assert any(d.field == "summary_he" for d in result.dropped)


def test_sentence_with_only_bad_cites_is_dropped() -> None:
    corpus = _corpus_with_registry([_reg(1)])
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        risks_and_gaps_he=[Sentence(text_he="פער כלשהו.", cites=[42])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.risks_and_gaps_he == []


# --------------------------------------------------------------------------
# number not in source -> field null
# --------------------------------------------------------------------------


def test_spec_value_number_not_in_source_is_blanked() -> None:
    items = [{"id": 1, "title": "datasheet", "summary_he": 'המשקל הוא 20 ק"ג.', "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="משקל", value='99 ק"ג', cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.specifications[0].value == ""
    assert any(d.field == "specifications.value" for d in result.dropped)


def test_spec_value_number_grounded_is_kept() -> None:
    #: PD-vocab-extract (2026-09-09): a specifications row now needs a real vocabulary `key` to
    #: stay in `specifications` at all (an unkeyed row is demoted to `other_specifications`, see
    #: `test_unkeyed_spec_row_demoted_to_other_specifications` below) -- `key="weight"` is the real
    #: config/spec_vocabulary.yaml key for "משקל".
    items = [{"id": 1, "title": "datasheet", "summary_he": 'המשקל הוא 20 ק"ג.', "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="משקל", key="weight", value='20 ק"ג', cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.specifications[0].value == '20 ק"ג'
    assert result.dossier.specifications[0].key == "weight"
    assert result.dropped == []


# --------------------------------------------------------------------------
# invented competitor -> dropped
# --------------------------------------------------------------------------


def test_invented_competitor_is_dropped() -> None:
    items = [{"id": 1, "title": "article about Litening pod", "summary_he": "", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        competitors=[CompetitorRow(product="TotallyMadeUpPod9000", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.competitors == []
    assert any(d.field == "competitors" for d in result.dropped)


def test_grounded_competitor_is_kept() -> None:
    items = [{"id": 1, "title": "article about Litening pod", "summary_he": "", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        competitors=[CompetitorRow(product="Litening", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.competitors) == 1
    assert result.dossier.competitors[0].product == "Litening"


# --------------------------------------------------------------------------
# price without basis -> dropped
# --------------------------------------------------------------------------


def test_price_row_missing_basis_is_dropped() -> None:
    items = [{"id": 1, "title": "contract award", "summary_he": "$2 million contract.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[PriceRow(figure="$2 million", source_kind="contract", basis_he="", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.pricing == []
    assert any(d.field == "pricing" and "basis" in d.reason for d in result.dropped)


def test_price_row_wrong_source_kind_is_dropped() -> None:
    items = [{"id": 1, "title": "blog rumor", "summary_he": "$2 million maybe.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[PriceRow(figure="$2 million", source_kind="article", basis_he="ליחידה", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.pricing == []


def test_price_row_fully_grounded_is_kept() -> None:
    items = [
        {
            "id": 1,
            "title": "contract award",
            "summary_he": "מחיר לפי חוזה: $2 million ליחידה.",
            "so_what_he": "",
        }
    ]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[PriceRow(figure="$2 million", source_kind="contract", basis_he="ליחידה", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.pricing) == 1
    assert result.dossier.pricing[0].basis_he == dossier_extract.BASIS_UNIT_HE


def test_price_row_number_not_grounded_is_dropped() -> None:
    items = [{"id": 1, "title": "contract award", "summary_he": "חוזה נחתם, ליחידה.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[PriceRow(figure="$99 million", source_kind="contract", basis_he="ליחידה", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.pricing == []


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 1): basis_he consistency -- a price row is kept only when its basis_he
# classifies deterministically into one of the three canonical forms; the SPECTRO XR case is the
# real live-dossier row that motivated this ("לחוזה כולל (לא צוין מחיר ליחידה)", not the prompt's
# own "לתוכנית כולה").
# --------------------------------------------------------------------------


def test_classify_price_basis_contract_total_spectro_xr_live_case() -> None:
    assert (
        dossier_extract._classify_price_basis("לחוזה כולל (לא צוין מחיר ליחידה)")
        == dossier_extract.BASIS_TOTAL_HE
    )


def test_classify_price_basis_per_unit() -> None:
    assert dossier_extract._classify_price_basis("ליחידה") == dossier_extract.BASIS_UNIT_HE
    assert dossier_extract._classify_price_basis("מחיר ליחידה") == dossier_extract.BASIS_UNIT_HE


def test_classify_price_basis_lot() -> None:
    assert dossier_extract._classify_price_basis("למנה של 5 יחידות") == "למנה של 5 יחידות"
    assert dossier_extract._classify_price_basis("lot of 12 units") == "למנה של 12 יחידות"


def test_classify_price_basis_unparseable_returns_none() -> None:
    assert dossier_extract._classify_price_basis("מחיר משוער") is None


def test_price_row_spectro_xr_contract_total_is_normalized_and_kept() -> None:
    items = [
        {
            "id": 1,
            "title": "contract award",
            "summary_he": "כ-80 מיליון דולר לחוזה כולל (לא צוין מחיר ליחידה).",
            "so_what_he": "",
        }
    ]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[
            PriceRow(
                figure="כ-80 מיליון דולר",
                source_kind="contract",
                basis_he="לחוזה כולל (לא צוין מחיר ליחידה)",
                cites=[1],
            )
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.pricing) == 1
    assert result.dossier.pricing[0].basis_he == dossier_extract.BASIS_TOTAL_HE
    assert result.dossier.pricing[0].figure == "כ-80 מיליון דולר"


def test_price_row_unparseable_basis_is_dropped() -> None:
    items = [{"id": 1, "title": "contract award", "summary_he": "$2 million contract.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        pricing=[PriceRow(figure="$2 million", source_kind="contract", basis_he="מחיר משוער", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.pricing == []
    assert any(d.field == "pricing" and "unparseable" in d.reason for d in result.dropped)


# --------------------------------------------------------------------------
# PD-fix (2026-09-08, item 3): deal amount/date/country deterministic post-processing
# --------------------------------------------------------------------------


def test_parse_amount_he_hebrew_scale_and_currency() -> None:
    assert dossier_extract.parse_amount_he("כ-80 מיליון דולר") == (80_000_000.0, "USD")


def test_parse_amount_he_english_scale_and_currency() -> None:
    assert dossier_extract.parse_amount_he("$2 million") == (2_000_000.0, "USD")


def test_parse_amount_he_no_digits_returns_none() -> None:
    assert dossier_extract.parse_amount_he("") == (None, None)
    assert dossier_extract.parse_amount_he("no numbers here") == (None, None)


def test_split_country_region_region_only() -> None:
    assert dossier_extract.split_country_region("Asia-Pacific country") == ("", "Asia-Pacific country")
    assert dossier_extract.split_country_region("מדינה באסיה-פסיפיק") == ("", "מדינה באסיה-פסיפיק")


def test_split_country_region_specific_country_is_kept() -> None:
    assert dossier_extract.split_country_region("India") == ("India", "")
    assert dossier_extract.split_country_region("") == ("", "")


def test_deal_row_amount_parsed_and_country_split_to_region() -> None:
    items = [
        {
            "id": 1,
            "title": "Elbit wins deal",
            "summary_he": "עסקה בהיקף כ-80 מיליון דולר, מדינה באסיה-פסיפיק.",
            "so_what_he": "",
        }
    ]
    corpus = _corpus_with_registry([_reg(1, published_at="2026-03-01")], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[
            DealRow(
                customer="Some AF",
                amount="כ-80 מיליון דולר",
                country="מדינה באסיה-פסיפיק",
                date=None,
                cites=[1],
            )
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    deal = result.dossier.deals[0]
    assert deal.amount_value == 80_000_000.0
    assert deal.currency == "USD"
    assert deal.country == ""
    assert deal.region_he == "מדינה באסיה-פסיפיק"
    # No date on the deal itself -> backfilled from the cited item's own published_at.
    assert deal.date == "2026-03-01"
    assert deal.date_kind == "published"


def test_deal_row_with_own_date_keeps_date_kind_deal() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "נחתם חוזה ב-1.3.2026.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1, published_at="2026-05-01")], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="Some AF", date="2026-03-01", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    deal = result.dossier.deals[0]
    assert deal.date == "2026-03-01"
    assert deal.date_kind == "deal"


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 4): a placeholder "customer" string the model wrote literally (e.g.
# the live SPECTRO XR "—") is normalized to None -- never persisted/rendered as-is.
# --------------------------------------------------------------------------


def test_deal_customer_placeholder_dash_normalized_to_none() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה כלשהי ב-2026-01-01.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    # PD-fix-6 item 4: a deal row with no amount/customer/date/country is dropped entirely -- this
    # row keeps its own explicit `date` so it survives that check, isolating the assertion below to
    # customer-placeholder normalization alone (see test_deal_row_with_no_content_at_all_is_dropped
    # for the item-4 drop itself).
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="—", kind="contract_award", date="2026-01-01", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals[0].customer is None


def test_deal_customer_placeholder_hebrew_text_normalized_to_none() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה כלשהי ב-2026-01-01.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="לא ידוע", kind="contract_award", date="2026-01-01", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals[0].customer is None


def test_deal_row_with_no_content_at_all_is_dropped() -> None:
    """PD-fix-6 item 4: a candidate carrying neither an amount, a customer, a date, a country/
    region, a platform nor a quantity is dropped entirely -- there is nothing left worth keeping."""
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה כלשהי.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="—", kind="contract_award", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals == []


def test_deal_real_customer_name_is_kept_verbatim() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה עם US Air Force.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="US Air Force", kind="contract_award", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals[0].customer == "US Air Force"


# --------------------------------------------------------------------------
# PD-fix-3 (2026-09-08, item 1): patents -- relevance_he is entirely code-derived (never trusted as
# the model wrote it) and a row with no grounded applicant/product-name link is dropped outright.
# --------------------------------------------------------------------------


def test_patent_row_with_matching_assignee_gets_deterministic_relevance_he() -> None:
    corpus = _corpus_with_registry([_reg(1, kind="patent")])
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        patents=[
            DossierPatentRow(
                pub_number="US1234567B2",
                title="EO/IR payload apparatus",
                assignee="Elbit Systems",
                relevance_he="Some model-written text that may or may not be true.",
                cites=[1],
            )
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.patents) == 1
    relevance = result.dossier.patents[0].relevance_he
    assert "Elbit Systems" in relevance
    assert "Some model-written text" not in relevance


def test_patent_row_admitting_no_confirmed_link_is_dropped() -> None:
    """Reproduces the live defect verbatim: the model's own relevance_he already says there is no
    confirmed link in the sources, and the row must not survive grounding regardless."""
    corpus = _corpus_with_registry([_reg(1, kind="patent")])
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        patents=[
            DossierPatentRow(
                pub_number="CN113804187A",
                title="target positioning pod apparatus",
                assignee="",
                relevance_he="אין אישור במקורות לקשר",
                cites=[1],
            )
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.patents == []
    assert any(d.field == "patents" for d in result.dropped)


def test_patent_row_product_name_in_title_is_kept_without_assignee_match() -> None:
    corpus = _corpus_with_registry([_reg(1, kind="patent")])
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        patents=[
            DossierPatentRow(
                pub_number="US9999999A1",
                title="SPECTRO XR compact payload housing",
                assignee="Unrelated Assignee Inc",
                relevance_he="",
                cites=[1],
            )
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.patents) == 1
    assert "SPECTRO XR" in result.dossier.patents[0].relevance_he


# --------------------------------------------------------------------------
# PD-vocab-extract (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md section 3.5): fixed-vocabulary
# post-checks -- key validation/demotion, table relocation, label normalization, duplicate-key
# collapse, required-row backfill. Exercised both via the narrow ``apply_vocabulary`` entry point
# (no grounding/DB involved) and end-to-end via ``ground_dossier`` for the exact id=1/2/3 drift case.
# --------------------------------------------------------------------------


def test_invalid_key_is_demoted_to_other_specifications() -> None:
    specs = [SpecRow(parameter_he="פרמטר מומצא", key="not_a_real_vocabulary_key", value="v", cites=[1])]
    new_specs, _new_perf, other = dossier_extract.apply_vocabulary(
        specs, [], [], product_line="targeting_pods", dropped=[]
    )
    assert all(r.key != "not_a_real_vocabulary_key" for r in new_specs)
    assert any(r.parameter_he == "פרמטר מומצא" and r.key == "" for r in other)


def test_missing_key_specification_row_is_demoted_to_other_specifications() -> None:
    """A row the model left in `specifications` without ever assigning a `key` at all -- demoted
    the same as a hallucinated key (section 3.5 item 1, generalized -- see extract.py's own
    `_split_invalid_keys` docstring)."""
    specs = [SpecRow(parameter_he="עובדה חופשית", value="v", cites=[1])]
    new_specs, _new_perf, other = dossier_extract.apply_vocabulary(
        specs, [], [], product_line="targeting_pods", dropped=[]
    )
    # required-key backfill still populates new_specs with placeholder rows -- the free-text row
    # itself must simply not be among them.
    assert all(r.parameter_he != "עובדה חופשית" for r in new_specs)
    assert len(other) == 1
    assert other[0].parameter_he == "עובדה חופשית"
    assert other[0].key == ""


def test_valid_key_in_wrong_table_is_relocated() -> None:
    """The exact id=1/2/3 drift fix: `size_to_performance_ratio` is a `performance`-routed key
    (config/spec_vocabulary.yaml's own `table` field) -- a row the model placed in `specifications`
    is moved to `performance`, not left split across both."""
    specs = [
        SpecRow(
            parameter_he="ביצועי אופטיקה",
            key="size_to_performance_ratio",
            value="ביצועי מטע\"ד 20 אינץ' בתוך מארז 15 אינץ'",
            cites=[1],
        )
    ]
    new_specs, new_perf, _other = dossier_extract.apply_vocabulary(
        specs, [], [], product_line="targeting_pods", dropped=[]
    )
    assert not any(r.key == "size_to_performance_ratio" for r in new_specs)
    relocated = [r for r in new_perf if r.key == "size_to_performance_ratio"]
    assert len(relocated) == 1
    assert relocated[0].claimed_value == "ביצועי מטע\"ד 20 אינץ' בתוך מארז 15 אינץ'"


def test_valid_key_performance_row_wrongly_typed_specifications_key_is_relocated() -> None:
    """The reverse relocation: a `specifications`-routed key the model wrote as a `PerformanceRow`
    moves back to `specifications`."""
    perf = [PerformanceRow(metric_he="קוטר", key="pod_class_diameter", claimed_value="20", cites=[1])]
    new_specs, new_perf, _other = dossier_extract.apply_vocabulary(
        [], perf, [], product_line="targeting_pods", dropped=[]
    )
    assert not any(r.key == "pod_class_diameter" for r in new_perf)
    relocated = [r for r in new_specs if r.key == "pod_class_diameter"]
    assert len(relocated) == 1
    assert relocated[0].value == "20"


def test_parameter_he_normalized_to_canonical_label_even_when_model_reworded_it() -> None:
    specs = [SpecRow(parameter_he="שם שרירותי לא נכון", key="weight", value="25 ק\"ג", cites=[1])]
    new_specs, _new_perf, _other = dossier_extract.apply_vocabulary(
        specs, [], [], product_line="targeting_pods", dropped=[]
    )
    row = next(r for r in new_specs if r.key == "weight")
    assert row.parameter_he == "משקל"


def test_duplicate_key_same_variant_collapses_keeping_the_one_with_a_value() -> None:
    specs = [
        SpecRow(parameter_he="משקל", key="weight", value="", cites=[]),
        SpecRow(parameter_he="משקל", key="weight", value="25 ק\"ג", cites=[1]),
    ]
    new_specs, _new_perf, _other = dossier_extract.apply_vocabulary(
        specs, [], [], product_line="targeting_pods", dropped=[]
    )
    weight_rows = [r for r in new_specs if r.key == "weight"]
    assert len(weight_rows) == 1
    assert weight_rows[0].value == "25 ק\"ג"


def test_duplicate_key_different_variant_both_kept() -> None:
    specs = [
        SpecRow(parameter_he="שדה ראייה", key="field_of_view", value="10°", variant="WFOV", cites=[1]),
        SpecRow(parameter_he="שדה ראייה", key="field_of_view", value="1°", variant="NFOV", cites=[1]),
    ]
    new_specs, _new_perf, _other = dossier_extract.apply_vocabulary(
        specs, [], [], product_line="targeting_pods", dropped=[]
    )
    fov_rows = [r for r in new_specs if r.key == "field_of_view"]
    assert len(fov_rows) == 2
    assert {r.variant for r in fov_rows} == {"WFOV", "NFOV"}


def test_required_key_missing_after_extraction_gets_placeholder_row() -> None:
    """common.weight is `required: true` -- with no row for it at all, a deterministic placeholder
    (value="", cites=[]) is appended, rendering as "לא נמצא במקורות" downstream."""
    new_specs, _new_perf, _other = dossier_extract.apply_vocabulary(
        [], [], [], product_line="targeting_pods", dropped=[]
    )
    weight_rows = [r for r in new_specs if r.key == "weight"]
    assert len(weight_rows) == 1
    assert weight_rows[0].value == ""
    assert weight_rows[0].cites == []


def test_other_specification_matching_known_synonym_is_promoted_out_of_overflow() -> None:
    """PD-fix-4 (2026-09-09, item 1): an other_specifications row whose own ``parameter_he``
    matches a known vocabulary label/synonym is now actually promoted out of overflow (not just
    logged as a non-destructive QA hint, the pre-fix behavior) -- appended to the correct table
    (``performance``, per ``size_to_performance_ratio``'s own ``table`` field) with that key set,
    its own ``value``/``cites`` carried over untouched."""
    other = [SpecRow(parameter_he="עומס אופטי במארז קומפקטי", value="v", cites=[1])]
    dropped: list[dossier_extract.DroppedField] = []
    _new_specs, new_perf, new_other = dossier_extract.apply_vocabulary(
        [], [], other, product_line="targeting_pods", dropped=dropped
    )
    assert new_other == []
    promoted = [r for r in new_perf if r.key == "size_to_performance_ratio"]
    assert len(promoted) == 1
    assert promoted[0].claimed_value == "v"
    assert promoted[0].cites == [1]
    assert any(
        d.field == "other_specifications" and "promoted_to_vocabulary_key:size_to_performance_ratio" in d.reason
        for d in dropped
    )


def test_end_to_end_id1_id2_id3_drift_reproduced_and_fixed() -> None:
    """The live SPECTRO XR drift this whole vocabulary exists to close (docs/
    PLAN_SPEC_VOCABULARY.md's own opening table): three different phrasings of the same fact, fed
    through the full ``ground_dossier`` pipeline, must all land on the SAME key
    (``size_to_performance_ratio``) and the SAME table (``performance``)."""
    items = [
        {
            "id": 1,
            "title": "datasheet",
            "summary_he": "ביצועי מטע\"ד בגודל 20 אינץ' בתוך מארז פיזי של 15 אינץ'.",
            "so_what_he": "",
        }
    ]
    corpus = _corpus_with_registry([_reg(1)], items=items, product_line="targeting_pods")
    phrasings = [
        ("performance", "metric_he", "עומס אופטי במארז קומפקטי"),
        ("specifications", "parameter_he", "ביצועי אופטיקה"),
        ("specifications", "parameter_he", "ביצועי עומס אופטי במארז קומפקטי"),
    ]
    for table_written, field_name, label_written in phrasings:
        if table_written == "specifications":
            draft = ProductDossierOut(
                identity=IdentityBlock(product_name="SPECTRO XR"),
                specifications=[
                    SpecRow(
                        **{field_name: label_written},
                        key="size_to_performance_ratio",
                        value="ביצועי מטע\"ד בגודל 20 אינץ' בתוך מארז פיזי של 15 אינץ'",
                        cites=[1],
                    )
                ],
            )
        else:
            draft = ProductDossierOut(
                identity=IdentityBlock(product_name="SPECTRO XR"),
                performance=[
                    PerformanceRow(
                        **{field_name: label_written},
                        key="size_to_performance_ratio",
                        claimed_value="ביצועי מטע\"ד בגודל 20 אינץ' בתוך מארז פיזי של 15 אינץ'",
                        cites=[1],
                    )
                ],
            )
        result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
        assert result.dossier.specifications == [] or all(
            r.key != "size_to_performance_ratio" for r in result.dossier.specifications
        )
        matches = [r for r in result.dossier.performance if r.key == "size_to_performance_ratio"]
        assert len(matches) == 1
        assert matches[0].metric_he == "יחס ביצועים-למעטפת"


# --------------------------------------------------------------------------
# PD-fix-4 (2026-09-09, item B.2): vague-value nulling
# --------------------------------------------------------------------------


def test_is_vague_value_true_for_marker_with_no_digit() -> None:
    assert dossier_extract._is_vague_value_he("לייזרים מתקדמים (סוג לא צוין)")
    assert dossier_extract._is_vague_value_he("ייצוב ברמה גבוהה (ערך מספרי לא צוין)")


def test_is_vague_value_false_when_a_real_number_is_present() -> None:
    # A marker word can co-occur with a real number -- that's still a concrete value.
    assert not dossier_extract._is_vague_value_he("ייצוב מתקדם, דיוק 0.1 מיליראד")


def test_is_vague_value_false_for_ordinary_concrete_text() -> None:
    assert not dossier_extract._is_vague_value_he("קוטר 7 אינץ'")
    assert not dossier_extract._is_vague_value_he("")


def test_null_vague_values_clears_spec_value_and_cites() -> None:
    rows = [SpecRow(parameter_he="לייזר", value="לייזרים מתקדמים (סוג לא צוין)", cites=[1])]
    new_specs, _perf, _other = dossier_extract._null_vague_values(rows, [], [], [])
    assert new_specs[0].value == ""
    assert new_specs[0].cites == []


def test_null_vague_values_leaves_concrete_spec_untouched() -> None:
    rows = [SpecRow(parameter_he="קוטר", value="7 אינץ'", cites=[1])]
    new_specs, _perf, _other = dossier_extract._null_vague_values(rows, [], [], [])
    assert new_specs[0].value == "7 אינץ'"
    assert new_specs[0].cites == [1]


def test_null_vague_values_handles_performance_independently() -> None:
    rows = [
        PerformanceRow(
            metric_he="ייצוב",
            claimed_value="ברמה גבוהה",
            tested_or_operational_value="0.2 מיליראד",
            cites=[1],
        )
    ]
    _specs, new_perf, _other = dossier_extract._null_vague_values([], rows, [], [])
    row = new_perf[0]
    assert row.claimed_value == ""
    assert row.tested_or_operational_value == "0.2 מיליראד"  # the concrete half survives
    assert row.cites == [1]  # cites untouched -- still supports the surviving tested value


def test_ground_dossier_nulls_vague_spec_value_end_to_end() -> None:
    corpus = _corpus_with_registry([_reg(1, kind="web", title="ייצוב ברמה גבוהה, ללא ערך מספרי.")])
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="ייצוב", value="ייצוב ברמה גבוהה (ערך מספרי לא צוין)", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.specifications[0].value == ""


# --------------------------------------------------------------------------
# item B.1: fact-retention scan + one bounded re-ask
# --------------------------------------------------------------------------


def test_find_missing_spec_facts_flags_uncaptured_numeric_fact() -> None:
    corpus = _corpus_with_registry(
        [_reg(1, kind="web", url="https://elbitsystems.com/x", title='מד טווח לייזר: 15 ק"מ. משקל: 45 ק"ג.')]
    )
    draft = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    missing = dossier_extract.find_missing_spec_facts(corpus, _EMPTY_PLAN, draft)
    assert any('15 ק"מ' in m["snippet"] for m in missing)
    assert any('45 ק"ג' in m["snippet"] for m in missing)
    assert all(m["n"] == 1 for m in missing)


def test_find_missing_spec_facts_skips_already_represented_fact() -> None:
    corpus = _corpus_with_registry(
        [_reg(1, kind="web", url="https://elbitsystems.com/x", title='משקל: 45 ק"ג.')]
    )
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        other_specifications=[SpecRow(parameter_he="משקל", value='45 ק"ג', cites=[1])],
    )
    missing = dossier_extract.find_missing_spec_facts(corpus, _EMPTY_PLAN, draft)
    assert missing == []


def test_find_missing_spec_facts_empty_when_no_spec_like_text() -> None:
    corpus = _corpus_with_registry([_reg(1, kind="web", url="https://x.com/a", title="מאמר כללי ללא מספרים.")])
    draft = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    assert dossier_extract.find_missing_spec_facts(corpus, _EMPTY_PLAN, draft) == []


def test_reask_missing_facts_returns_empty_for_no_missing() -> None:
    corpus = _corpus_with_registry([])
    assert dossier_extract.reask_missing_facts([], corpus) == []


def test_reask_missing_facts_calls_chat_structured_and_returns_rows(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_chat_structured(role, schema, messages, **kwargs):
        captured["role"] = role
        captured["schema"] = schema
        return schema(other_specifications=[SpecRow(parameter_he='משקל', value='45 ק"ג', cites=[1])])

    monkeypatch.setattr(dossier_extract, "chat_structured", fake_chat_structured)
    corpus = _corpus_with_registry([_reg(1, kind="web", url="https://elbitsystems.com/x")])
    missing = [{"n": 1, "topic": "specifications", "snippet": 'משקל: 45 ק"ג'}]
    rows = dossier_extract.reask_missing_facts(missing, corpus)
    assert len(rows) == 1
    assert rows[0].value == '45 ק"ג'
    assert captured["role"] == "resident"


def test_reask_missing_facts_swallows_failure(monkeypatch: Any) -> None:
    def failing_chat_structured(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("llm down")

    monkeypatch.setattr(dossier_extract, "chat_structured", failing_chat_structured)
    corpus = _corpus_with_registry([_reg(1, kind="web", url="https://elbitsystems.com/x")])
    missing = [{"n": 1, "topic": "specifications", "snippet": "x"}]
    assert dossier_extract.reask_missing_facts(missing, corpus) == []


def test_apply_fact_retention_appends_grounded_supplemental_row(monkeypatch: Any) -> None:
    corpus = _corpus_with_registry(
        [_reg(1, kind="web", url="https://elbitsystems.com/x", title='משקל המערכת: 45 ק"ג.')]
    )
    draft = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    grounding = dossier_extract.GroundingResult(dossier=draft, dropped=[])

    def fake_reask(missing, corpus_arg, **kwargs):
        return [SpecRow(parameter_he="משקל", value='45 ק"ג', cites=[1])]

    monkeypatch.setattr(dossier_extract, "reask_missing_facts", fake_reask)
    result = dossier_extract.apply_fact_retention(grounding, corpus, _EMPTY_PLAN)
    assert len(result.dossier.other_specifications) == 1
    assert result.dossier.other_specifications[0].value == '45 ק"ג'


def test_apply_fact_retention_no_op_when_nothing_missing() -> None:
    corpus = _corpus_with_registry([_reg(1, kind="web", url="https://x.com/a", title="אין מספרים כאן.")])
    draft = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    grounding = dossier_extract.GroundingResult(dossier=draft, dropped=[])
    result = dossier_extract.apply_fact_retention(grounding, corpus, _EMPTY_PLAN)
    assert result is grounding


# --------------------------------------------------------------------------
# item B.3: fact retention across runs (carry-forward)
# --------------------------------------------------------------------------


def _previous_row(*, sources: list[dict[str, Any]], data: dict[str, Any]) -> dict[str, Any]:
    return {"id": 3, "sources": sources, "data": data}


def test_carry_forward_fills_null_spec_when_source_still_registered() -> None:
    corpus = _corpus_with_registry([_reg(5, kind="web", url="https://elbitsystems.com/x")])
    corpus.previous = _previous_row(
        sources=[{"n": 3, "url": "https://elbitsystems.com/x"}],
        data={"specifications": [{"key": "detector_type", "value": "HD", "cites": [3]}]},
    )
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="סוג גלאי", key="detector_type", value="")],
    )
    new_dossier, carried = dossier_extract.carry_forward_missing_specs(dossier, corpus)
    assert carried == 1
    row = new_dossier.specifications[0]
    assert row.value == "HD"
    assert row.cites == [5]  # remapped to THIS run's own registry number for the same url
    assert dossier_extract.CARRIED_FROM_RUN_TAG_HE in row.variant


def test_carry_forward_skips_when_source_not_registered_this_run() -> None:
    corpus = _corpus_with_registry([])  # the previous source's url is nowhere in this run's registry
    corpus.previous = _previous_row(
        sources=[{"n": 3, "url": "https://elbitsystems.com/gone"}],
        data={"specifications": [{"key": "detector_type", "value": "HD", "cites": [3]}]},
    )
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="סוג גלאי", key="detector_type", value="")],
    )
    new_dossier, carried = dossier_extract.carry_forward_missing_specs(dossier, corpus)
    assert carried == 0
    assert new_dossier.specifications[0].value == ""


def test_carry_forward_does_not_overwrite_existing_value() -> None:
    corpus = _corpus_with_registry([_reg(5, kind="web", url="https://elbitsystems.com/x")])
    corpus.previous = _previous_row(
        sources=[{"n": 3, "url": "https://elbitsystems.com/x"}],
        data={"specifications": [{"key": "detector_type", "value": "OLD-VALUE", "cites": [3]}]},
    )
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="סוג גלאי", key="detector_type", value="NEW-VALUE", cites=[5])],
    )
    new_dossier, carried = dossier_extract.carry_forward_missing_specs(dossier, corpus)
    assert carried == 0
    assert new_dossier.specifications[0].value == "NEW-VALUE"


def test_carry_forward_no_op_with_no_previous_dossier() -> None:
    corpus = _corpus_with_registry([])
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="סוג גלאי", key="detector_type", value="")],
    )
    new_dossier, carried = dossier_extract.carry_forward_missing_specs(dossier, corpus)
    assert carried == 0
    assert new_dossier is dossier


def test_carry_forward_performance_tags_conditions_he() -> None:
    corpus = _corpus_with_registry([_reg(2, kind="web", url="https://elbitsystems.com/perf")])
    corpus.previous = _previous_row(
        sources=[{"n": 9, "url": "https://elbitsystems.com/perf"}],
        data={"performance": [{"key": "detection_range", "claimed_value": "20 ק\"מ", "cites": [9]}]},
    )
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        performance=[PerformanceRow(metric_he="טווח זיהוי", key="detection_range", claimed_value="")],
    )
    new_dossier, carried = dossier_extract.carry_forward_missing_specs(dossier, corpus)
    assert carried == 1
    row = new_dossier.performance[0]
    assert row.claimed_value == '20 ק"מ'
    assert row.cites == [2]
    assert dossier_extract.CARRIED_FROM_RUN_TAG_HE in row.conditions_he


def test_carry_forward_never_resurrects_a_vague_previous_value() -> None:
    """Live finding (dossier run 5, 2026-09-09): a previous run predating item B.2 (vague-value
    nulling) can itself carry a hand-wavy "לא צוין"-with-no-digit value -- carry-forward must never
    resurrect that through the back door just because the current run's own value was (correctly)
    nulled by B.2."""
    corpus = _corpus_with_registry([_reg(5, kind="web", url="https://elbitsystems.com/x")])
    corpus.previous = _previous_row(
        sources=[{"n": 3, "url": "https://elbitsystems.com/x"}],
        data={
            "specifications": [
                {"key": "laser_designator_illuminator", "value": "לייזרים מתקדמים (סוג לא צוין)", "cites": [3]}
            ],
            "performance": [
                {"key": "line_of_sight_stabilization", "claimed_value": "ברמה גבוהה (ערך מספרי לא צוין)", "cites": [3]}
            ],
        },
    )
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="מצביע לייזר", key="laser_designator_illuminator", value="")],
        performance=[
            PerformanceRow(metric_he="ייצוב", key="line_of_sight_stabilization", claimed_value="")
        ],
    )
    new_dossier, carried = dossier_extract.carry_forward_missing_specs(dossier, corpus)
    assert carried == 0
    assert new_dossier.specifications[0].value == ""
    assert new_dossier.performance[0].claimed_value == ""


def test_carry_forward_does_not_break_diff_no_change_reported() -> None:
    """The carried value is verbatim-identical to the previous run's own value -- eoa.dossier.diff
    must not report it as a change (compares only value/claimed_value, never variant/conditions_he,
    so this is true "for free" -- this test is the regression guard for that claim)."""
    from eoa.dossier import diff as dossier_diff

    corpus = _corpus_with_registry([_reg(5, kind="web", url="https://elbitsystems.com/x")])
    previous_data = {"specifications": [{"key": "detector_type", "value": "HD", "cites": [3]}]}
    corpus.previous = _previous_row(
        sources=[{"n": 3, "url": "https://elbitsystems.com/x"}], data=previous_data
    )
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="סוג גלאי", key="detector_type", value="")],
    )
    new_dossier, carried = dossier_extract.carry_forward_missing_specs(dossier, corpus)
    assert carried == 1
    sentences = dossier_diff.compute_diff(previous_data, new_dossier)
    assert sentences == []


# --------------------------------------------------------------------------
# build_dossier wiring: both new steps (B.1 fact retention, B.3 carry-forward) run in order after
# ground_dossier, end to end -- extract_dossier itself is the only mocked call (no network/LLM).
# --------------------------------------------------------------------------


def test_build_dossier_wires_fact_retention_and_carry_forward(monkeypatch: Any) -> None:
    corpus = _corpus_with_registry(
        [
            _reg(1, kind="web", url="https://elbitsystems.com/spec", title='טווח זיהוי: 12 ק"מ.'),
            _reg(2, kind="web", url="https://elbitsystems.com/carried"),
        ]
    )
    corpus.previous = _previous_row(
        sources=[{"n": 9, "url": "https://elbitsystems.com/carried"}],
        data={"specifications": [{"key": "detector_type", "value": "HD sensor", "cites": [9]}]},
    )
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="סוג גלאי", key="detector_type", value="")],
    )

    monkeypatch.setattr(dossier_extract, "extract_dossier", lambda *a, **k: draft)

    def fake_reask(missing, corpus_arg, **kwargs):
        assert missing  # the 12 ק"מ fact from registry row 1 must have been detected
        return [SpecRow(parameter_he="טווח זיהוי", value='12 ק"מ', cites=[1])]

    monkeypatch.setattr(dossier_extract, "reask_missing_facts", fake_reask)

    result = dossier_extract.build_dossier(corpus, _EMPTY_PLAN, llm_leg="codex:gpt-6-astra")

    # B.1: the re-asked fact landed in other_specifications.
    assert any(r.value == '12 ק"מ' for r in result.dossier.other_specifications)
    # B.3: the previous run's detector_type value was carried forward, remapped to n=2, tagged.
    carried_row = next(r for r in result.dossier.specifications if r.key == "detector_type")
    assert carried_row.value == "HD sensor"
    assert carried_row.cites == [2]
    assert dossier_extract.CARRIED_FROM_RUN_TAG_HE in carried_row.variant


def test_build_dossier_no_op_for_first_run_with_no_previous_and_no_missing(monkeypatch: Any) -> None:
    """Every existing caller's behavior is unchanged for a plain first-run dossier -- both new steps
    are zero-cost no-ops when there is nothing to retain or carry forward."""
    corpus = _corpus_with_registry([_reg(1, kind="web", url="https://x.com/a", title="אין מספרים.")])
    draft = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    monkeypatch.setattr(dossier_extract, "extract_dossier", lambda *a, **k: draft)
    result = dossier_extract.build_dossier(corpus, _EMPTY_PLAN)
    assert result.dossier.other_specifications == []
    # apply_vocabulary's own required-param backfill (pre-existing, unrelated behavior) may still
    # add empty placeholder rows -- what matters here is that neither new step invented a value.
    assert all(r.value == "" and r.cites == [] for r in result.dossier.specifications)


# --------------------------------------------------------------------------
# PD-fix-4 (2026-09-09, item 1): overflow-row promotion -- the live SPECTRO XR run-8 facts
# (weight/diameter/height/average power/MWIR band/FOV counts/laser lines) that stayed stuck in
# other_specifications with key="" even though a real vocabulary key existed for each of them.
# --------------------------------------------------------------------------


def test_overflow_weight_row_promoted_via_existing_label_synonym() -> None:
    """"משקל המערכת." already contains the "משקל" synonym on the ``weight`` key -- promoted via
    plain label matching, no heuristic needed."""
    other = [SpecRow(parameter_he="משקל המערכת.", value='51 ק"ג', cites=[24], confidence="medium")]
    new_specs, _new_perf, new_other = dossier_extract.apply_vocabulary(
        [], [], other, product_line=None, dropped=[]
    )
    assert new_other == []
    weight_rows = [r for r in new_specs if r.key == "weight"]
    assert len(weight_rows) == 1
    assert weight_rows[0].value == '51 ק"ג'
    assert weight_rows[0].cites == [24]
    assert weight_rows[0].confidence == "medium"


def test_overflow_diameter_and_height_promoted_as_distinct_envelope_variants() -> None:
    """Two genuinely different overflow facts ("קוטר המערכת" / "גובה המערכת") both key to
    ``envelope_dimensions`` -- promoted with distinct variant tags so neither is silently collapsed
    away by the same-key/same-variant dedup step."""
    other = [
        SpecRow(parameter_he="קוטר המערכת.", value='415 מ"מ', cites=[24]),
        SpecRow(parameter_he="גובה המערכת.", value='500 מ"מ', cites=[24]),
    ]
    new_specs, _new_perf, new_other = dossier_extract.apply_vocabulary(
        [], [], other, product_line=None, dropped=[]
    )
    assert new_other == []
    envelope_rows = [r for r in new_specs if r.key == "envelope_dimensions"]
    assert len(envelope_rows) == 2
    by_variant = {r.variant: r.value for r in envelope_rows}
    assert by_variant["קוטר"] == '415 מ"מ'
    assert by_variant["גובה"] == '500 מ"מ'


def test_overflow_average_power_promoted_via_label_synonym() -> None:
    other = [SpecRow(parameter_he="הספק ממוצע.", value="500W", cites=[24])]
    new_specs, _new_perf, _new_other = dossier_extract.apply_vocabulary(
        [], [], other, product_line=None, dropped=[]
    )
    power_rows = [r for r in new_specs if r.key == "power_consumption"]
    assert len(power_rows) == 1
    assert power_rows[0].value == "500W"


def test_overflow_mwir_band_promoted_via_existing_mwir_synonym() -> None:
    """``common.detector_type`` already carries "MWIR" as one of its own synonyms (declared before
    ``mws_eo.spectral_band_coverage``'s own, later-declared "MWIR" synonym, so it wins the match --
    an existing, pre-fix vocabulary ambiguity this fix doesn't change, only actually promotes on)."""
    other = [
        SpecRow(parameter_he="התחום הספקטרלי של הערוץ התרמי (MWIR).", value="3-5µm", cites=[22])
    ]
    new_specs, _new_perf, new_other = dossier_extract.apply_vocabulary(
        [], [], other, product_line=None, dropped=[]
    )
    assert new_other == []
    detector_rows = [r for r in new_specs if r.key == "detector_type"]
    assert len(detector_rows) == 1
    assert detector_rows[0].value == "3-5µm"


def test_overflow_fov_counts_promoted_and_kept_distinct_per_channel() -> None:
    """All three imaging-channel FOV-count facts key to ``field_of_view`` (one via the literal
    "(FOV)" label match, the other two via the new "שדות הראייה" synonym) -- distinct per-channel
    variants keep all three, none silently collapsed."""
    other = [
        SpecRow(parameter_he="מספר שדות הראייה (FOV) בערוץ התרמי.", value="2 FOV", cites=[22]),
        SpecRow(
            parameter_he="מספר שדות הראייה בערוץ הנראה והתת־אדום הקרוב (Visible/NIR).",
            value="3 FOV",
            cites=[22],
        ),
        SpecRow(parameter_he="מספר שדות הראייה בערוץ SWIR.", value="2 FOV", cites=[22]),
    ]
    new_specs, _new_perf, new_other = dossier_extract.apply_vocabulary(
        [], [], other, product_line=None, dropped=[]
    )
    assert new_other == []
    fov_rows = [r for r in new_specs if r.key == "field_of_view"]
    assert len(fov_rows) == 3


def test_overflow_laser_lines_promoted_to_designator_and_rangefinder_respectively() -> None:
    other = [
        SpecRow(
            parameter_he="סוג הלייזר, אורך הגל ותדר הפעולה של מציין הלייזר (Designator).",
            value="Nd:YAG, 1064nm, עד 22Hz",
            cites=[22],
        ),
        SpecRow(
            parameter_he="סוג הלייזר, אורך הגל ותדר הפעולה של מד הטווח (Rangefinder).",
            value="Nd:YAG/OPO 1570nm עד 3Hz",
            cites=[22],
        ),
    ]
    new_specs, _new_perf, new_other = dossier_extract.apply_vocabulary(
        [], [], other, product_line=None, dropped=[]
    )
    assert new_other == []
    designator = [r for r in new_specs if r.key == "laser_designator_illuminator"]
    rangefinder = [r for r in new_specs if r.key == "laser_rangefinder"]
    assert len(designator) == 1 and designator[0].value == "Nd:YAG, 1064nm, עד 22Hz"
    assert len(rangefinder) == 1 and rangefinder[0].value == "Nd:YAG/OPO 1570nm עד 3Hz"


def test_overflow_prf_and_wavelength_facts_for_same_laser_key_both_survive() -> None:
    """Live SPECTRO XR run-8 regression found during verification: a PRF/repetition-rate overflow
    row and a separate wavelength/type-description overflow row both key to ``laser_rangefinder`` --
    without a distinguishing variant tag, the same-key/same-variant dedup step would silently
    collapse the second one away, losing a real fact."""
    other = [
        SpecRow(parameter_he="תדר חזרת הפולסים (PRF) של LTDRF.", value="עד 22Hz", cites=[20]),
        SpecRow(
            parameter_he="סוג הלייזר, אורך הגל ותדר הפעולה של מד הטווח (Rangefinder).",
            value="Nd:YAG/OPO 1570nm עד 3Hz",
            cites=[22],
        ),
    ]
    new_specs, _new_perf, new_other = dossier_extract.apply_vocabulary(
        [], [], other, product_line=None, dropped=[]
    )
    assert new_other == []
    rangefinder_rows = [r for r in new_specs if r.key == "laser_rangefinder"]
    assert len(rangefinder_rows) == 2
    values = {r.value for r in rangefinder_rows}
    assert values == {"עד 22Hz", "Nd:YAG/OPO 1570nm עד 3Hz"}


def test_overflow_row_with_no_match_at_all_stays_in_other_specifications() -> None:
    other = [SpecRow(parameter_he="מספר ערוצי הספוטר (Spotter).", value="שלושה", cites=[24])]
    new_specs, new_perf, new_other = dossier_extract.apply_vocabulary(
        [], [], other, product_line=None, dropped=[]
    )
    assert len(new_other) == 1
    assert new_other[0].parameter_he == "מספר ערוצי הספוטר (Spotter)."
    # Nothing invented for this row -- it never lands in specifications/performance under any key.
    assert not any(r.value == "שלושה" for r in new_specs)
    assert not any(r.claimed_value == "שלושה" for r in new_perf)


# --------------------------------------------------------------------------
# PD-fix-4 (2026-09-09, item 2): deterministic deal candidates from registry press-release sources.
# --------------------------------------------------------------------------


def test_deal_candidate_built_from_registry_press_release_with_award_keyword_and_amount() -> None:
    items = [
        {
            "id": 1,
            "title": "Elbit Systems Awarded Contract Over $90 Million",
            "summary_he": "אלביט מערכות זכתה בחוזה בהיקף העולה על 90 מיליון דולר.",
            "so_what_he": "",
        }
    ]
    corpus = _corpus_with_registry([_reg(1, published_at="2026-06-01")], items=items)
    draft = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.deals) == 1
    deal = result.dossier.deals[0]
    assert deal.cites == [1]
    assert deal.amount_value == 90_000_000.0
    assert deal.date == "2026-06-01"
    assert deal.date_kind == "published"


def test_deal_candidate_not_duplicated_when_model_already_cited_same_source() -> None:
    items = [
        {
            "id": 1,
            "title": "Elbit Systems Awarded Contract Over $90 Million",
            "summary_he": "אלביט מערכות זכתה בחוזה בהיקף העולה על 90 מיליון דולר.",
            "so_what_he": "",
        }
    ]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="Asia-Pacific customer", amount="90 מיליון דולר", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert len(result.dossier.deals) == 1
    assert result.dossier.deals[0].customer == "Asia-Pacific customer"


def test_deal_candidate_not_built_without_award_keyword() -> None:
    items = [{"id": 1, "title": "SPECTRO XR spec sheet", "summary_he": "משקל 51 ק\"ג.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals == []


def test_deal_candidate_multiple_press_releases_each_yield_own_deal() -> None:
    items = [
        {"id": 1, "title": "elbitsystems.com", "summary_he": "אלביט זכתה בחוזה בהיקף של כ-80 מיליון דולר.", "so_what_he": ""},
        {
            "id": 2,
            "title": "elbitsystems.com",
            "summary_he": "אלביט מערכות זכתה בחוזים בהיקף של כ-270 מיליון דולר.",
            "so_what_he": "",
        },
    ]
    corpus = _corpus_with_registry([_reg(1), _reg(2)], items=items)
    draft = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    amounts = sorted(d.amount_value for d in result.dossier.deals if d.amount_value)
    assert amounts == [80_000_000.0, 270_000_000.0]


def test_deal_candidate_spurious_digit_in_list_marker_yields_no_candidate() -> None:
    """PD-fix-6 item 1/4 root cause: a source whose ONLY digit runs are markdown list markers
    ("(1)", "1.") inside unrelated investigation prose, not a real monetary figure, must yield no
    deal candidate at all -- even though the text also carries an award/contract keyword elsewhere.
    Reproduces the live SPECTRO XR rerun (product_dossiers id=12) text that previously produced
    ``amount="**(1) עובדות רלוונטיות:** ב-1 בספט"`` / ``amount_value=1.0``."""
    items = [
        {
            "id": 1,
            "title": "ניתוח הדף",
            "summary_he": (
                "חברת אלביט מערכות זכתה בחוזה. הביא 3 מקורות נוספים. להלן ניתוח הדף: "
                "**(1) עובדות רלוונטיות:** ב-1 בספטמבר 2026 פורסמה הודעה."
            ),
            "so_what_he": "",
        }
    ]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals == []


# --------------------------------------------------------------------------
# PD-fix-6 (2026-09-09): deal-row hygiene fixed against the live SPECTRO XR rerun's own garbage
# (product_dossiers id=12) -- customer values that are torn fragments of investigation prose,
# non-ISO/timestamped dates, and duplicate deal rows in different textual shapes.
# --------------------------------------------------------------------------

#: The exact garbage `customer` strings named in the PD-fix-6 task brief, verbatim.
_PD_FIX_6_GARBAGE_CUSTOMERS: tuple[str, ...] = (
    "- **(1) עובדות רלוונטיות:** ב-1 בספט",
    "- ews, 03/2023) **(1) עובדות רלוונטיות",
    "- nds $270M ISR Deal חברת אלביט מערכות ז",
    "- הביא 3 מקורות נוספים. להלן ניתוח הדף",
)


def test_looks_like_customer_name_rejects_every_pd_fix_6_garbage_string() -> None:
    for garbage in _PD_FIX_6_GARBAGE_CUSTOMERS:
        assert dossier_extract._looks_like_customer_name(garbage) is False, garbage


def test_normalize_customer_nulls_every_pd_fix_6_garbage_string() -> None:
    for garbage in _PD_FIX_6_GARBAGE_CUSTOMERS:
        assert dossier_extract._normalize_customer(garbage) is None, garbage


def test_deal_row_with_garbage_customer_is_grounded_to_null_customer_but_keeps_amount() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה בהיקף כ-80 מיליון דולר.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[
            DealRow(
                customer=_PD_FIX_6_GARBAGE_CUSTOMERS[2],
                amount="כ-80 מיליון דולר",
                cites=[1],
            )
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    deal = result.dossier.deals[0]
    assert deal.customer is None
    assert deal.amount_value == 80_000_000.0


def test_looks_like_customer_name_accepts_real_organisation_and_country_names() -> None:
    for good in ("US Air Force", "משרד ההגנה הלאומי של רומניה", "Romania", "הצי הרומני", "NATO"):
        assert dossier_extract._looks_like_customer_name(good) is True, good


def test_normalize_deal_date_hebrew_month_year_becomes_iso_year_month() -> None:
    assert dossier_extract._normalize_deal_date("ספטמבר 2026") == "2026-09"
    assert dossier_extract._normalize_deal_date("מרץ 2023") == "2023-03"


def test_normalize_deal_date_english_month_year_becomes_iso_year_month() -> None:
    assert dossier_extract._normalize_deal_date("September 2026") == "2026-09"
    assert dossier_extract._normalize_deal_date("Mar 2023") == "2023-03"


def test_normalize_deal_date_strips_time_component_from_timestamp() -> None:
    assert dossier_extract._normalize_deal_date("2026-09-02 09:04:00+03:00") == "2026-09-02"


def test_normalize_deal_date_dmy_slash_becomes_iso() -> None:
    assert dossier_extract._normalize_deal_date("20/3/2023") == "2023-03-20"


def test_normalize_deal_date_passes_through_already_iso() -> None:
    assert dossier_extract._normalize_deal_date("2023-06-21") == "2023-06-21"
    assert dossier_extract._normalize_deal_date("2022-12") == "2022-12"


def test_normalize_deal_date_unparseable_text_becomes_none() -> None:
    assert dossier_extract._normalize_deal_date("הביא 3 מקורות נוספים") is None
    assert dossier_extract._normalize_deal_date(None) is None
    assert dossier_extract._normalize_deal_date("") is None


def test_deal_row_hebrew_month_date_grounded_to_iso_year_month() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה כלשהי.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="Some AF", date="ספטמבר 2026", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals[0].date == "2026-09"


def test_deal_row_timestamp_date_grounded_to_date_only() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה כלשהי.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="Some AF", date="2026-09-02 09:04:00+03:00", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals[0].date == "2026-09-02"


def test_deal_rows_deduped_by_amount_value_kind_and_customer() -> None:
    """PD-fix-6 item 3: the same $270M Romanian contract, cited from two different sources in the
    registry (two different press-release URLs the model each turned into its own deal row --
    matching product_dossiers id=12's repeated Romanian framework/order and $270M contract), same
    amount/kind/customer -- collapses to one row, first occurrence kept."""
    items = [
        {"id": 1, "title": "contract-a", "summary_he": "עסקה עם רומניה בהיקף כ-270 מיליון דולר.", "so_what_he": ""},
        {"id": 2, "title": "contract-b", "summary_he": "עסקה נוספת עם רומניה בהיקף כ-270 מיליון דולר.", "so_what_he": ""},
    ]
    corpus = _corpus_with_registry([_reg(1), _reg(2)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[
            DealRow(customer="Romania", amount="כ-270 מיליון דולר", kind="contract_award", cites=[1]),
            DealRow(customer="Romania", amount="270 מיליון דולר", kind="contract_award", cites=[2]),
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    deals_270 = [d for d in result.dossier.deals if d.amount_value == 270_000_000.0]
    assert len(deals_270) == 1
    assert deals_270[0].customer == "Romania"
    assert deals_270[0].cites == [1]


def test_deal_candidate_over_same_amount_and_customer_year_dedupes_against_model_row() -> None:
    """A registry-derived candidate (no customer of its own) over the same amount/kind whose
    source's OWN publish date falls in the same year as the model row's `date` collapses into the
    model row -- `customer or date year` as the dedup identity's third component means an
    undated-but-same-year candidate is still recognized as the same underlying deal."""
    items = [
        {"id": 1, "title": "contract-a", "summary_he": "עסקה עם רומניה בהיקף כ-270 מיליון דולר.", "so_what_he": ""},
        {
            "id": 2,
            "title": "elbitsystems.com",
            "summary_he": "אלביט זכתה בחוזה עם רומניה בהיקף של כ-270 מיליון דולר.",
            "so_what_he": "",
        },
    ]
    corpus = _corpus_with_registry([_reg(1), _reg(2, published_at="2023-06-21")], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[
            DealRow(
                customer=None,
                amount="כ-270 מיליון דולר",
                kind="contract_award",
                date="2023-06-21",
                cites=[1],
            ),
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    deals_270 = [d for d in result.dossier.deals if d.amount_value == 270_000_000.0]
    assert len(deals_270) == 1
    assert deals_270[0].cites == [1]


def test_deal_rows_same_amount_different_kind_both_kept() -> None:
    """A framework agreement and its follow-on contract award over the same nominal figure/customer
    are two distinct rows -- ``kind`` is part of the dedup identity, so they are never collapsed."""
    dropped: list = []
    deals = [
        DealRow(customer="Romania", amount="180 מיליון דולר", amount_value=180_000_000.0, kind="framework", cites=[1]),
        DealRow(
            customer="Romania", amount="180 מיליון דולר", amount_value=180_000_000.0, kind="contract_award", cites=[1]
        ),
    ]
    kept = dossier_extract._finalize_deals(deals, dropped)
    assert len(kept) == 2


def test_deal_row_no_content_dropped_by_finalize_deals_directly() -> None:
    dropped: list = []
    deals = [DealRow(kind="contract_award", cites=[1])]
    assert dossier_extract._finalize_deals(deals, dropped) == []
    assert dropped[0].reason == "empty_after_grounding"


# --------------------------------------------------------------------------
# PD-fix-4 (2026-09-09, item 5): timeline rows from dated gap/risk findings, and variants named
# outside the dedicated "versions" topic.
# --------------------------------------------------------------------------


def test_timeline_includes_dated_risk_sentence_even_without_a_structured_deal_row() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        risks_and_gaps_he=[
            Sentence(
                text_he="הסיכומים מתארים גם הודעת חברה מ־2 ביוני 2021 על חוזה בכ-80 מיליון דולר, "
                "אולם ללא כתובת מלאה אין בסיס להפניה פרטנית.",
                cites=[15, 16, 17],
            )
        ],
    )
    timeline = dossier_extract.build_timeline(dossier)
    assert len(timeline) == 1
    assert timeline[0].kind == "milestone"
    assert "יוני 2021" in timeline[0].date
    assert timeline[0].cites == [15, 16, 17]


def test_timeline_ignores_uncited_or_undated_findings() -> None:
    """A ``Sentence`` (``risks_and_gaps_he``) always carries >=1 cite by schema construction -- the
    "no cites" case is only reachable via ``gaps_tracking`` (``GapTrackingRow.cites`` has no such
    minimum)."""
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        risks_and_gaps_he=[Sentence(text_he="פער כלשהו ללא תאריך.", cites=[1])],
        gaps_tracking=[
            GapTrackingRow(gap_he="פער נוסף בלי תאריך.", cites=[2]),
            GapTrackingRow(gap_he="פער עם תאריך 2021 אך ללא ציטוט.", cites=[]),
        ],
    )
    assert dossier_extract.build_timeline(dossier) == []


def test_variant_named_in_risk_sentence_is_captured_as_version_row() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        risks_and_gaps_he=[
            Sentence(
                text_he="חקירת הגרסאות לא ביססה גרסאות נפרדות, אך חקירת המפרט מזכירה את הכינוי "
                "SPECTRO XR CU ללא פירוט, ולכן אין בסיס לרצף דורות.",
                cites=[18, 20, 25],
            )
        ],
    )
    mentions = dossier_extract.build_variant_mentions(dossier)
    assert len(mentions) == 1
    assert mentions[0].name == "SPECTRO XR CU"
    assert mentions[0].cites == [18, 20, 25]


def test_variant_mention_not_duplicated_when_already_in_versions_table() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        variants_and_versions=[VersionRow(name="SPECTRO XR CU", cites=[1])],
        risks_and_gaps_he=[
            Sentence(text_he="הכינוי SPECTRO XR CU מוזכר שוב כאן.", cites=[18]),
        ],
    )
    assert dossier_extract.build_variant_mentions(dossier) == []


def test_variant_mention_requires_citation() -> None:
    """A ``Sentence`` always carries >=1 cite by schema construction -- the "no cites" case is only
    reachable via ``gaps_tracking``."""
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        gaps_tracking=[GapTrackingRow(gap_he="SPECTRO XR CU מוזכר כאן ללא ציטוט.", cites=[])],
    )
    assert dossier_extract.build_variant_mentions(dossier) == []


# ==========================================================================
# PD-fix-5 (2026-09-09, docs/qa/content_review/PD-fix-5.md): comparison of product_dossiers rows
# 8/11 (elbit-systems-spectro-xr) against the hand-made reference dossier found five further live
# bugs. Each item below is its own section.
# ==========================================================================


# --------------------------------------------------------------------------
# item 5: registry-kind-aware row confidence -- a citation to a vendor_official/datasheet REGISTRY
# source now yields "high" even when the row's own (model-authored) schema-level source_kind is left
# at its default "other" and it carries only one citation.
# --------------------------------------------------------------------------


def test_spec_row_confidence_high_for_single_vendor_official_citation() -> None:
    corpus = _corpus_with_registry(
        [
            _reg(
                1,
                kind="web",
                url="https://elbitsystems.com/x",
                source_kind="vendor_official",
                title='משקל 51 ק"ג.',
            )
        ]
    )
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="משקל", key="weight", value='51 ק"ג', cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    row = next(r for r in result.dossier.specifications if r.key == "weight")
    assert row.confidence == "high"


def test_spec_row_confidence_stays_medium_for_single_press_citation() -> None:
    """Regression guard: item 5 only ADDS a new "high" path (registry vendor_official/datasheet) --
    a plain press citation with a single cite and no qualifying schema-level source_kind still stays
    "medium", exactly as before."""
    corpus = _corpus_with_registry(
        [
            _reg(
                1, kind="web", url="https://news.example.com/x", source_kind="press", title='משקל 51 ק"ג.'
            )
        ]
    )
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="משקל", key="weight", value='51 ק"ג', cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    row = next(r for r in result.dossier.specifications if r.key == "weight")
    assert row.confidence == "medium"


def test_performance_row_confidence_high_for_single_datasheet_citation() -> None:
    corpus = _corpus_with_registry(
        [
            _reg(
                1,
                kind="web",
                url="https://x.com/ds.pdf",
                source_kind="datasheet",
                title='טווח זיהוי 20 ק"מ.',
            )
        ]
    )
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        performance=[
            PerformanceRow(
                metric_he="טווח זיהוי", key="detection_range_dri", claimed_value='20 ק"מ', cites=[1]
            )
        ],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    row = next(r for r in result.dossier.performance if r.key == "detection_range_dri")
    assert row.confidence == "high"


def test_deal_row_confidence_high_for_single_vendor_official_citation() -> None:
    corpus = _corpus_with_registry(
        [
            _reg(
                1,
                kind="web",
                url="https://elbitsystems.com/news/x",
                source_kind="vendor_official",
                title="חוזה בכ-80 מיליון דולר.",
            )
        ]
    )
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="Some AF", amount="כ-80 מיליון דולר", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals[0].confidence_level == "high"


def test_variant_row_confidence_high_for_single_vendor_official_citation() -> None:
    corpus = _corpus_with_registry(
        [
            _reg(
                1,
                kind="web",
                url="https://elbitsystems.com/x",
                source_kind="vendor_official",
                title="SPECTRO XR CU.",
            )
        ]
    )
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        variants_and_versions=[VersionRow(name="SPECTRO XR CU", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.variants_and_versions[0].confidence == "high"


# --------------------------------------------------------------------------
# item 1: carry-forward covers a previous run's UNKEYED overflow fact too, and a stronger previous
# (datasheet/vendor) value wins over a weaker current (press) value for the same key.
# --------------------------------------------------------------------------


def test_carry_forward_covers_previous_overflow_row_matched_by_label() -> None:
    """A fact that lived in the PREVIOUS run's own ``other_specifications`` (key="") -- e.g. a run
    predating PD-fix-4's overflow-promotion fix -- is still carried into today's matching keyed row,
    not only a previous run's own already-keyed rows."""
    corpus = _corpus_with_registry([_reg(5, kind="web", url="https://elbitsystems.com/x")])
    corpus.previous = _previous_row(
        sources=[{"n": 24, "url": "https://elbitsystems.com/x", "source_kind": "vendor_official"}],
        data={"other_specifications": [{"parameter_he": "משקל המערכת.", "value": '51 ק"ג', "cites": [24]}]},
    )
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="משקל", key="weight", value="")],
    )
    new_dossier, carried = dossier_extract.carry_forward_missing_specs(dossier, corpus)
    assert carried == 1
    row = next(r for r in new_dossier.specifications if r.key == "weight")
    assert row.value == '51 ק"ג'
    assert row.cites == [5]
    assert dossier_extract.CARRIED_FROM_RUN_TAG_HE in row.variant


def test_carry_forward_replaces_weaker_current_value_with_stronger_previous_datasheet_value() -> None:
    """A previous run's value cited to a vendor page must WIN over a weaker current-run value cited
    only to a plain press source -- even though the current row is already filled (not null)."""
    corpus = _corpus_with_registry(
        [
            _reg(5, kind="web", url="https://elbitsystems.com/vendor", source_kind="vendor_official"),
            _reg(6, kind="web", url="https://news.example.com/press", source_kind="press"),
        ]
    )
    corpus.previous = _previous_row(
        sources=[{"n": 24, "url": "https://elbitsystems.com/vendor", "source_kind": "vendor_official"}],
        data={"specifications": [{"key": "weight", "value": '51 ק"ג', "cites": [24]}]},
    )
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="משקל", key="weight", value='52 ק"ג בתצורה מרבית', cites=[6])],
    )
    new_dossier, carried = dossier_extract.carry_forward_missing_specs(dossier, corpus)
    assert carried == 1
    row = next(r for r in new_dossier.specifications if r.key == "weight")
    assert row.value == '51 ק"ג'
    assert row.cites == [5]


def test_carry_forward_does_not_downgrade_when_current_source_is_at_least_as_strong() -> None:
    corpus = _corpus_with_registry([_reg(5, kind="web", url="https://x.com/page", source_kind="vendor_official")])
    corpus.previous = _previous_row(
        sources=[{"n": 24, "url": "https://x.com/page", "source_kind": "press"}],
        data={"specifications": [{"key": "weight", "value": '52 ק"ג', "cites": [24]}]},
    )
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="משקל", key="weight", value='51 ק"ג', cites=[5])],
    )
    new_dossier, carried = dossier_extract.carry_forward_missing_specs(dossier, corpus)
    assert carried == 0
    assert new_dossier.specifications[0].value == '51 ק"ג'


# --------------------------------------------------------------------------
# item 4 (cross-run half): variants are carried forward across runs exactly like keyed specs.
# --------------------------------------------------------------------------


def test_carry_forward_missing_variants_fills_from_previous_run() -> None:
    corpus = _corpus_with_registry([_reg(5, kind="web", url="https://elbitsystems.com/x")])
    corpus.previous = _previous_row(
        sources=[{"n": 24, "url": "https://elbitsystems.com/x"}],
        data={
            "variants_and_versions": [
                {"name": "SPECTRO XR CU", "cites": [24], "evidence_he": "מוזכר בעמוד היצרן."}
            ]
        },
    )
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    new_dossier, carried = dossier_extract.carry_forward_missing_variants(dossier, corpus)
    assert carried == 1
    row = new_dossier.variants_and_versions[0]
    assert row.name == "SPECTRO XR CU"
    assert row.cites == [5]
    assert dossier_extract.CARRIED_FROM_RUN_TAG_HE in row.evidence_he


def test_carry_forward_missing_variants_skips_name_already_present() -> None:
    corpus = _corpus_with_registry([_reg(5, kind="web", url="https://elbitsystems.com/x")])
    corpus.previous = _previous_row(
        sources=[{"n": 24, "url": "https://elbitsystems.com/x"}],
        data={"variants_and_versions": [{"name": "SPECTRO XR CU", "cites": [24]}]},
    )
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        variants_and_versions=[VersionRow(name="SPECTRO XR CU", cites=[1])],
    )
    new_dossier, carried = dossier_extract.carry_forward_missing_variants(dossier, corpus)
    assert carried == 0
    assert new_dossier is dossier


def test_carry_forward_missing_variants_no_op_with_no_previous_dossier() -> None:
    corpus = _corpus_with_registry([])
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    new_dossier, carried = dossier_extract.carry_forward_missing_variants(dossier, corpus)
    assert carried == 0
    assert new_dossier is dossier


# --------------------------------------------------------------------------
# item 3: deals -- a date is recovered from the cited page's own text when the registry carries no
# published_at at all (a plain "web"-fetched press release, not a DB item/event row); customer=None
# never blanks country/region_he.
# --------------------------------------------------------------------------


def test_deal_date_backfilled_from_cited_text_when_no_registry_published_at() -> None:
    corpus = _corpus_with_registry(
        [
            _reg(
                1,
                kind="web",
                url="https://elbitsystems.com/news/x",
                title="הודעת חברה מ-2 ביוני 2021 על חוזה בכ-80 מיליון דולר.",
            )
        ]
    )
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="Some AF", amount="כ-80 מיליון דולר", date=None, cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    deal = result.dossier.deals[0]
    # PD-fix-6 item 2: a DealRow.date is always ISO (or null), never the raw Hebrew month-year
    # phrase the textual date-hint fallback originally lifted verbatim ("יוני 2021").
    assert deal.date == "2021-06"
    assert deal.date_kind == "published"


def test_deal_country_and_region_kept_when_customer_unknown() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה עם מדינה באסיה-פסיפיק.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="—", country="מדינה באסיה-פסיפיק", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    deal = result.dossier.deals[0]
    assert deal.customer is None
    assert deal.region_he == "מדינה באסיה-פסיפיק"


# --------------------------------------------------------------------------
# item 4 (scan half): the variant scan accepts a lowercase deployment-domain word ("maritime") and a
# variant named off just the product's own "family" word, and (when a corpus is given) also scans
# registered datasheet text, not only already-extracted risk/gap sentences.
# --------------------------------------------------------------------------


def test_variant_config_word_maritime_captured_as_version_row() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        risks_and_gaps_he=[
            Sentence(text_he="עמוד היצרן מתאר תצורה בשם SPECTRO XR maritime לשימוש ימי.", cites=[7]),
        ],
    )
    names = {m.name for m in dossier_extract.build_variant_mentions(dossier)}
    assert "SPECTRO XR maritime" in names


def test_variant_base_name_anchor_captures_family_suffix_variant() -> None:
    """"SPECTRO CU" doesn't contain the full product_name ("SPECTRO XR") as a substring at all --
    only reachable via the shorter "SPECTRO" family-word anchor (item 4b)."""
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        gaps_tracking=[GapTrackingRow(gap_he="הכינוי SPECTRO CU מוזכר ללא פירוט.", cites=[9])],
    )
    names = {m.name for m in dossier_extract.build_variant_mentions(dossier)}
    assert "SPECTRO XR CU" in names


def test_variant_family_anchor_does_not_re_match_full_product_name_as_fake_variant() -> None:
    """Regression guard for the family-anchor extension: scanning "...SPECTRO XR CU..." must never
    also mint a bogus "SPECTRO XR XR CU"/"SPECTRO XR XR" row from the "SPECTRO" anchor re-consuming
    the product's own "XR" word."""
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        gaps_tracking=[GapTrackingRow(gap_he="הכינוי SPECTRO XR CU מוזכר ללא פירוט.", cites=[9])],
    )
    names = {m.name for m in dossier_extract.build_variant_mentions(dossier)}
    assert names == {"SPECTRO XR CU"}


def test_variant_scan_covers_registered_datasheet_text_when_corpus_given() -> None:
    corpus = _corpus_with_registry([])
    corpus.datasheets = [{"n": 12, "text": "SPECTRO XR maritime configuration described here."}]
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    mentions = dossier_extract.build_variant_mentions(dossier, corpus)
    assert len(mentions) == 1
    assert mentions[0].name == "SPECTRO XR maritime"
    assert mentions[0].cites == [12]


def test_variant_scan_without_corpus_ignores_datasheets() -> None:
    """Backward compatibility: every pre-existing caller that passes only ``dossier`` (no corpus)
    keeps behaving exactly as before -- datasheet text is only scanned when explicitly given."""
    dossier = ProductDossierOut(identity=IdentityBlock(product_name="SPECTRO XR"))
    assert dossier_extract.build_variant_mentions(dossier) == []


# --------------------------------------------------------------------------
# item 2: datasheet-priority key retention -- a still-null REQUIRED vocabulary key whose synonyms
# show up in a registered datasheet's own text is found and (via one bounded re-ask) filled, without
# any new web/network call.
# --------------------------------------------------------------------------


def test_find_missing_datasheet_keys_finds_required_null_key_in_registered_datasheet_text() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="משקל", key="weight", value="")],
    )
    corpus = _corpus_with_registry([])
    corpus.datasheets = [{"n": 7, "text": 'לפי העלון, משקל המערכת הוא 51 ק"ג בתצורה הבסיסית.'}]
    missing = dossier_extract.find_missing_datasheet_keys(dossier, corpus)
    assert any(m["key"] == "weight" and m["n"] == 7 for m in missing)


def test_find_missing_datasheet_keys_skips_a_key_already_filled() -> None:
    dossier = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        specifications=[SpecRow(parameter_he="משקל", key="weight", value='51 ק"ג', cites=[1])],
    )
    corpus = _corpus_with_registry([])
    corpus.datasheets = [{"n": 7, "text": 'משקל המערכת הוא 51 ק"ג.'}]
    assert dossier_extract.find_missing_datasheet_keys(dossier, corpus) == []


def test_apply_datasheet_key_retention_fills_null_required_key(monkeypatch: Any) -> None:
    corpus = _corpus_with_registry([_reg(7, kind="web", url="https://x.com/ds", title='משקל 51 ק"ג.')])
    corpus.datasheets = [{"n": 7, "text": 'לפי העלון, משקל המערכת הוא 51 ק"ג בתצורה הבסיסית.'}]
    grounding = dossier_extract.GroundingResult(
        dossier=ProductDossierOut(
            identity=IdentityBlock(product_name="SPECTRO XR"),
            specifications=[SpecRow(parameter_he="משקל", key="weight", value="")],
        ),
        dropped=[],
    )

    def fake_reask(missing, corpus_arg, **kwargs):
        assert missing and missing[0]["key"] == "weight"  # no new web call -- built purely offline
        return dossier_extract._DatasheetKeyRetentionOut(
            specifications=[SpecRow(parameter_he="משקל", key="weight", value='51 ק"ג', cites=[7])]
        )

    monkeypatch.setattr(dossier_extract, "reask_datasheet_keys", fake_reask)
    result = dossier_extract.apply_datasheet_key_retention(grounding, corpus, _EMPTY_PLAN)
    row = next(r for r in result.dossier.specifications if r.key == "weight")
    assert row.value == '51 ק"ג'


def test_apply_datasheet_key_retention_no_op_when_no_datasheets_registered() -> None:
    corpus = _corpus_with_registry([])
    grounding = dossier_extract.GroundingResult(
        dossier=ProductDossierOut(
            identity=IdentityBlock(product_name="SPECTRO XR"),
            specifications=[SpecRow(parameter_he="משקל", key="weight", value="")],
        ),
        dropped=[],
    )
    result = dossier_extract.apply_datasheet_key_retention(grounding, corpus, _EMPTY_PLAN)
    assert result is grounding
