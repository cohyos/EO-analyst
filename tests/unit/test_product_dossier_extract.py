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
    IdentityBlock,
    PerformanceRow,
    PriceRow,
    ProductDossierOut,
    SpecRow,
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
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה כלשהי.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="—", kind="contract_award", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals[0].customer is None


def test_deal_customer_placeholder_hebrew_text_normalized_to_none() -> None:
    items = [{"id": 1, "title": "contract", "summary_he": "עסקה כלשהי.", "so_what_he": ""}]
    corpus = _corpus_with_registry([_reg(1)], items=items)
    draft = ProductDossierOut(
        identity=IdentityBlock(product_name="SPECTRO XR"),
        deals=[DealRow(customer="לא ידוע", kind="contract_award", cites=[1])],
    )
    result = dossier_extract.ground_dossier(draft, corpus, _EMPTY_PLAN)
    assert result.dossier.deals[0].customer is None


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


def test_other_specification_matching_known_synonym_is_flagged_but_not_reclassified() -> None:
    """The promoted matcher (section 3.2) used as a non-destructive QA signal only -- the row stays
    in other_specifications (the model's own placement decision is never silently overridden), but
    the match is logged for the field-dropped audit trail."""
    other = [SpecRow(parameter_he="עומס אופטי במארז קומפקטי", value="v", cites=[1])]
    dropped: list[dossier_extract.DroppedField] = []
    _new_specs, _new_perf, new_other = dossier_extract.apply_vocabulary(
        [], [], other, product_line="targeting_pods", dropped=dropped
    )
    assert len(new_other) == 1
    assert new_other[0].parameter_he == "עומס אופטי במארז קומפקטי"
    assert any(
        d.field == "other_specifications" and "size_to_performance_ratio" in d.reason for d in dropped
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
