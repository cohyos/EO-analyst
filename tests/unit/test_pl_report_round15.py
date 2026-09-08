"""Unit tests for PL-REPORT-FIX (round-15, user screenshot 2026-09-08 20:30 of pl_targeting_pods,
reports.id=184, output/reports/pl_targeting_pods_2026-09-08.md).

Four defects, four test classes below:

1. ``TestDedupePipelineRows`` -- the opportunities table ("מפת קונים / צינור הזדמנויות") carried
   two near-identical forecast rows for the same platform+payload, target dates one day apart.
   Render-side dedupe only (:func:`eoa.report.product_line.dedupe_pipeline_rows`); the data-level
   dedupe of the underlying ``tender_forecasts`` rows belongs to ``eoa.tenders.forecast``, out of
   scope here.
2. ``TestTenderAggregatorReliability`` -- every row/action rested on one tender-aggregator source
   (usarfp.com) rendered with reliability "—"/date "—" in the sources appendix; a recommended
   action citing only an aggregator; an opportunity's tier capped at "C" when aggregator-only.
3. ``TestTableCaptionRtlFix`` -- covered directly against ``eoa.report.docx_builder`` (this
   module's caption logic is centralized there, not duplicated in product_line/bd_territory).
4. ``TestBuyerNeverEmDash`` -- the opportunities table's "גורם רוכש" column must read "לא צוין",
   never "—", and a forecast row's real ``buyer_country`` must actually be used.

No DB, no LLM/Ollama calls -- every function under test is pure, mirroring the conventions already
used by tests/unit/test_reports_round13.py.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_pl_report_round15.py -q``
"""

from __future__ import annotations

import datetime as dt

from eoa.llm.schemas.analysis import Sentence
from eoa.llm.schemas.product_line import ProductLineRecommendedAction
from eoa.report import docx_builder
from eoa.report import product_line as pl
from eoa.report import qa_citations as qc

TODAY = dt.date(2026, 9, 8)

AGGREGATOR_URL = "https://www.usarfp.com/tender/litening-advanced-targeting-pod-3526dc.php"
AGGREGATOR_ITEM = {"id": 1, "n": 1, "url": AGGREGATOR_URL, "source_name": "usarfp.com"}
NON_AGGREGATOR_ITEM = {"id": 2, "n": 2, "url": "https://www.rafael.co.il/news/x", "source_name": "Rafael"}


# =================================================================================================
# Defect #1 -- render-side opportunities-table dedupe
# =================================================================================================


def _forecast_row(n: int, likelihood: float, window_from: dt.date, window_to: dt.date) -> dict:
    return {
        "n": n,
        "platform": "מטוס קרב",
        "payload_need": "פוד כיוון (Targeting Pod)",
        "likelihood": likelihood,
        "window_from": window_from,
        "window_to": window_to,
        "buyer_country": None,
        "candidate_vendors": [],
        "trigger_item_id": None,
    }


class TestDedupePipelineRows:
    def test_merges_near_duplicate_forecast_rows_one_day_apart(self) -> None:
        # The exact reproduction case: 2027-03-05..2028-09-05 (grade-driving likelihood 60%) vs
        # 2027-03-06..2028-09-06 (40%), one day apart on both edges.
        rows = pl._pipeline_rows_from_forecasts(
            [
                _forecast_row(2, 0.6, dt.date(2027, 3, 5), dt.date(2028, 9, 5)),
                _forecast_row(3, 0.4, dt.date(2027, 3, 6), dt.date(2028, 9, 6)),
            ],
            set(),
            today=TODAY,
        )
        assert len(rows) == 2
        merged = pl.dedupe_pipeline_rows(rows, today=TODAY)
        assert len(merged) == 1
        row = merged[0]
        assert row.n == 2
        assert row.extra_ns == [3]
        assert "תחזיות מאוחדות" in row.target_date_he
        assert "2 תחזיות מאוחדות" in row.target_date_he
        # widened to cover both windows
        assert "2027-03-05" in row.target_date_he
        assert "2028-09-06" in row.target_date_he

    def test_keeps_rows_outside_the_14_day_window_distinct(self) -> None:
        rows = pl._pipeline_rows_from_forecasts(
            [
                _forecast_row(2, 0.6, dt.date(2027, 3, 5), dt.date(2028, 9, 5)),
                _forecast_row(3, 0.4, dt.date(2027, 4, 5), dt.date(2028, 10, 5)),  # >14 days later
            ],
            set(),
            today=TODAY,
        )
        merged = pl.dedupe_pipeline_rows(rows, today=TODAY)
        assert len(merged) == 2

    def test_different_buyer_or_stage_never_merges(self) -> None:
        rows = pl._pipeline_rows_from_forecasts(
            [
                _forecast_row(2, 0.6, dt.date(2027, 3, 5), dt.date(2028, 9, 5)),
                {**_forecast_row(3, 0.4, dt.date(2027, 3, 6), dt.date(2028, 9, 6)), "buyer_country": "Poland"},
            ],
            set(),
            today=TODAY,
        )
        merged = pl.dedupe_pipeline_rows(rows, today=TODAY)
        assert len(merged) == 2

    def test_single_row_list_passthrough(self) -> None:
        rows = pl._pipeline_rows_from_forecasts(
            [_forecast_row(2, 0.6, dt.date(2027, 3, 5), dt.date(2028, 9, 5))], set(), today=TODAY
        )
        assert pl.dedupe_pipeline_rows(rows, today=TODAY) == rows

    def test_merge_keeps_better_grade_row_as_the_base(self) -> None:
        # Neither row here is aggregator-only, so tiers differ by likelihood-driven score --
        # confirms "keep the better grade" without the aggregator cap forcing both to C.
        rows = [
            pl.PipelineRow(
                opportunity_he="X: Y", stage="RFI", buyer_he="", target_date_he="a",
                n=10, reference_date=TODAY, likelihood=0.9, window_from=TODAY - dt.timedelta(days=1),
            ),
            pl.PipelineRow(
                opportunity_he="X: Y", stage="RFI", buyer_he="", target_date_he="b",
                n=11, reference_date=TODAY, likelihood=0.1, window_from=TODAY - dt.timedelta(days=2),
            ),
        ]
        merged = pl.dedupe_pipeline_rows(rows, today=TODAY)
        assert len(merged) == 1
        assert merged[0].n == 10  # the higher-likelihood (better-graded) row survives as the base
        assert merged[0].extra_ns == [11]


# =================================================================================================
# Defect #2 -- tender-aggregator domain reliability, action downgrade, tier cap
# =================================================================================================


class TestTenderAggregatorReliability:
    def test_is_tender_aggregator_domain_matches_known_aggregators(self) -> None:
        assert qc.is_tender_aggregator_domain(url=AGGREGATOR_URL) is True
        assert qc.is_tender_aggregator_domain(url="https://tendersinfo.com/x") is True
        assert qc.is_tender_aggregator_domain(source_name="www.globaltenders.com") is True

    def test_is_tender_aggregator_domain_false_for_primary_sources(self) -> None:
        assert qc.is_tender_aggregator_domain(url="https://www.rafael.co.il/news") is False
        assert qc.is_tender_aggregator_domain(url=None, source_name=None) is False

    def test_reliability_never_renders_em_dash_for_an_aggregator(self) -> None:
        label = docx_builder.reliability_label(qc.tender_aggregator_reliability())
        assert label != "—"
        assert "מצבור מכרזים" in label

    def test_attach_metadata_sets_reliability_for_aggregator_item(self, monkeypatch) -> None:
        # No DB access in a unit test -- the published_at DB fallback (see
        # _any_status_tender_date_for_item) is exercised on its own further below.
        monkeypatch.setattr(pl, "_fetchone", lambda *a, **k: None)
        items = [{"id": 1, "url": AGGREGATOR_URL, "source_name": "usarfp.com", "published_at": None}]
        pl._attach_tender_aggregator_metadata(items, [])
        assert items[0]["reliability"] is not None
        assert docx_builder.reliability_label(items[0]["reliability"]) != "—"

    def test_attach_metadata_backfills_date_from_linked_tender_row(self) -> None:
        published = dt.datetime(2026, 8, 20, tzinfo=dt.UTC)
        items = [{"id": 1, "url": AGGREGATOR_URL, "source_name": "usarfp.com", "published_at": None}]
        tenders = [{"item_id": 1, "published_at": published, "deadline": None}]
        pl._attach_tender_aggregator_metadata(items, tenders)
        assert items[0]["published_at"] == published

    def test_attach_metadata_falls_back_to_tender_deadline_when_no_published_at(self) -> None:
        deadline = dt.date(2026, 10, 1)
        items = [{"id": 1, "url": AGGREGATOR_URL, "source_name": "usarfp.com", "published_at": None}]
        tenders = [{"item_id": 1, "published_at": None, "deadline": deadline}]
        pl._attach_tender_aggregator_metadata(items, tenders)
        assert items[0]["published_at"] == deadline

    def test_attach_metadata_never_overwrites_an_existing_published_at(self) -> None:
        real_date = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
        items = [{"id": 1, "url": AGGREGATOR_URL, "source_name": "usarfp.com", "published_at": real_date}]
        tenders = [{"item_id": 1, "published_at": dt.datetime(2026, 8, 20, tzinfo=dt.UTC), "deadline": None}]
        pl._attach_tender_aggregator_metadata(items, tenders)
        assert items[0]["published_at"] == real_date

    def test_attach_metadata_falls_back_to_db_lookup_for_archived_linked_tender(self, monkeypatch) -> None:
        # Live-verification finding (rebuilding pl_targeting_pods 2026-09-08): the Litening item's
        # own linked `tenders` row is status='archived' with an expired deadline, so it's excluded
        # from collect_tenders_and_forecasts's own open/unknown-only list -- the appendix date
        # backfill must still find it via a direct, status-unrestricted DB lookup.
        archived_published_at = dt.datetime(2015, 8, 11, tzinfo=dt.UTC)

        def fake_fetchone(query, params=None):
            assert params["id"] == 1
            return {"published_at": archived_published_at, "deadline": dt.date(2015, 9, 11)}

        monkeypatch.setattr(pl, "_fetchone", fake_fetchone)
        items = [{"id": 1, "url": AGGREGATOR_URL, "source_name": "usarfp.com", "published_at": None}]
        pl._attach_tender_aggregator_metadata(items, [])  # tenders=[] -- not in the open/unknown list
        assert items[0]["published_at"] == archived_published_at

    def test_attach_metadata_db_fallback_returns_none_when_no_tender_links_to_the_item(self, monkeypatch) -> None:
        monkeypatch.setattr(pl, "_fetchone", lambda query, params=None: None)
        items = [{"id": 1, "url": AGGREGATOR_URL, "source_name": "usarfp.com", "published_at": None}]
        pl._attach_tender_aggregator_metadata(items, [])
        assert items[0]["published_at"] is None

    def test_attach_metadata_skips_db_fallback_when_open_tender_already_matched(self, monkeypatch) -> None:
        def boom(query, params=None):
            raise AssertionError("should not query the DB when the open-tenders list already matched")

        monkeypatch.setattr(pl, "_fetchone", boom)
        items = [{"id": 1, "url": AGGREGATOR_URL, "source_name": "usarfp.com", "published_at": None}]
        tenders = [{"item_id": 1, "published_at": dt.datetime(2026, 8, 20, tzinfo=dt.UTC), "deadline": None}]
        pl._attach_tender_aggregator_metadata(items, tenders)  # must not raise
        assert items[0]["published_at"] is not None

    def test_attach_metadata_never_overwrites_an_existing_reliability_value(self, monkeypatch) -> None:
        monkeypatch.setattr(pl, "_fetchone", lambda *a, **k: None)
        items = [
            {"id": 1, "url": AGGREGATOR_URL, "source_name": "usarfp.com", "published_at": None, "reliability": "custom"}
        ]
        pl._attach_tender_aggregator_metadata(items, [])
        assert items[0]["reliability"] == "custom"

    def test_non_aggregator_item_untouched(self, monkeypatch) -> None:
        monkeypatch.setattr(pl, "_fetchone", lambda *a, **k: None)
        items = [{"id": 2, "url": "https://www.rafael.co.il/news", "source_name": "Rafael", "published_at": None}]
        pl._attach_tender_aggregator_metadata(items, [])
        assert items[0].get("reliability") is None
        assert items[0]["published_at"] is None

    def _action(self, cites: list[int], *, priority: str = "H") -> ProductLineRecommendedAction:
        return ProductLineRecommendedAction(
            action_he="להגיש הצעה תחרותית למכרז.",
            priority=priority,
            rationale=[Sentence(text_he="נימוק כלשהו.", cites=cites)],
            owner_role_he="מכירות",
            timing_he="מיידי",
        )

    def test_downgrades_action_supported_only_by_an_aggregator(self) -> None:
        items = [AGGREGATOR_ITEM]
        out = pl._downgrade_aggregator_only_actions([self._action([1])], items)
        assert len(out) == 1
        assert out[0].priority == "L"
        assert out[0].action_he.startswith("לאימות: ")
        # the appended rationale sentence cites the same (already-valid) aggregator n, no new n
        assert all(n in {1} for s in out[0].rationale for n in s.cites)
        assert len(out[0].rationale) == 2  # original + the appended aggregator-disclosure sentence

    def test_does_not_downgrade_a_double_prefix(self) -> None:
        items = [AGGREGATOR_ITEM]
        once = pl._downgrade_aggregator_only_actions([self._action([1])], items)
        twice = pl._downgrade_aggregator_only_actions(once, items)
        assert twice[0].action_he == once[0].action_he
        assert twice[0].action_he.count("לאימות: ") == 1

    def test_leaves_action_untouched_when_a_second_non_aggregator_source_supports_it(self) -> None:
        items = [AGGREGATOR_ITEM, NON_AGGREGATOR_ITEM]
        out = pl._downgrade_aggregator_only_actions([self._action([1, 2], priority="H")], items)
        assert out[0].priority == "H"
        assert not out[0].action_he.startswith("לאימות: ")
        assert len(out[0].rationale) == 1  # nothing appended

    def test_leaves_action_untouched_when_no_citation_resolves_to_any_source(self) -> None:
        out = pl._downgrade_aggregator_only_actions([self._action([99])], [])
        assert out[0].priority == "H"
        assert not out[0].action_he.startswith("לאימות: ")

    def test_pipeline_row_tier_capped_at_c_when_aggregator_only(self) -> None:
        row = pl.PipelineRow(
            opportunity_he="x", stage="RFP", buyer_he="Poland", target_date_he="d",
            n=1, amount_usd=100_000_000, level="red", reference_date=TODAY, is_aggregator_only=True,
        )
        # Without the cap this row's own inputs (amount >= 50M + recent reference date) would
        # score an "A".
        assert row.tier(today=TODAY) == "C"

    def test_pipeline_row_tier_uncapped_when_not_aggregator_only(self) -> None:
        row = pl.PipelineRow(
            opportunity_he="x", stage="RFP", buyer_he="Poland", target_date_he="d",
            n=1, amount_usd=100_000_000, level="red", reference_date=TODAY, is_aggregator_only=False,
        )
        assert row.tier(today=TODAY) == "A"

    def test_tender_row_from_aggregator_url_is_flagged_aggregator_only(self) -> None:
        rows = pl._pipeline_rows_from_tenders(
            [{"n": 1, "title": "RFI", "agency": None, "status": "unknown", "url": AGGREGATOR_URL, "entities": []}],
            set(),
        )
        assert rows[0].is_aggregator_only is True
        assert rows[0].tier(today=TODAY) == "C"

    def test_forecast_row_resolves_aggregator_status_via_trigger_item(self) -> None:
        rows = pl._pipeline_rows_from_forecasts(
            [{**_forecast_row(2, 0.6, dt.date(2027, 1, 1), dt.date(2028, 1, 1)), "trigger_item_id": 1}],
            set(),
            today=TODAY,
            items_by_id={1: AGGREGATOR_ITEM},
        )
        assert rows[0].is_aggregator_only is True


# =================================================================================================
# Defect #3 -- row-count caption RTL/parenthesis fix (centralized in eoa.report.docx_builder;
# product_line/bd_territory both call the shared _table_caption_text via the three renderers, no
# duplicate caption logic of their own to fix separately).
# =================================================================================================


class TestTableCaptionRtlFix:
    def test_no_wrapping_parentheses_in_synthesized_caption(self) -> None:
        caption = docx_builder._table_caption_text({"rows": [[1], [2], [3], [4]]})
        assert caption == "4 שורות"
        assert "(" not in caption and ")" not in caption

    def test_caption_dropped_entirely_for_three_or_fewer_rows(self) -> None:
        assert docx_builder._table_caption_text({"rows": [[1], [2], [3]]}) is None
        assert docx_builder._table_caption_text({"rows": [[1]]}) is None
        assert docx_builder._table_caption_text({"rows": []}) is None

    def test_note_he_still_wins_regardless_of_row_count(self) -> None:
        assert docx_builder._table_caption_text({"note_he": "הערה.", "rows": [[1]]}) == "הערה."


# =================================================================================================
# Defect #4 -- buyer column: "לא צוין" not "—", and a forecast's real buyer_country is used
# =================================================================================================


class TestBuyerNeverEmDash:
    def test_pipeline_table_renders_missing_buyer_as_lo_tzuyan_not_em_dash(self) -> None:
        rows = pl._pipeline_rows_from_tenders(
            [{"n": 1, "title": "RFI", "agency": None, "status": "unknown", "url": None, "entities": []}], set()
        )
        table = pl.pipeline_table(rows, today=TODAY)
        buyer_cell = table["rows"][0][2]
        assert buyer_cell == "לא צוין"
        assert buyer_cell != "—"

    def test_pipeline_table_renders_a_real_buyer_when_present(self) -> None:
        rows = pl._pipeline_rows_from_tenders(
            [{"n": 1, "title": "RFI", "agency": "US Air Force", "status": "unknown", "url": None, "entities": []}],
            set(),
        )
        table = pl.pipeline_table(rows, today=TODAY)
        assert table["rows"][0][2] == "US Air Force"

    def test_forecast_pipeline_row_uses_buyer_country_field(self) -> None:
        # Previously hardcoded to "—" even though tender_forecasts.buyer_country is selected and
        # available -- a render-side bug, not a data gap.
        row = pl._pipeline_rows_from_forecasts(
            [{**_forecast_row(2, 0.5, dt.date(2027, 1, 1), dt.date(2028, 1, 1)), "buyer_country": "Poland"}],
            set(),
            today=TODAY,
        )[0]
        assert row.buyer_he == "Poland"
        table = pl.pipeline_table([row], today=TODAY)
        assert table["rows"][0][2] == "Poland"

    def test_forecast_pipeline_row_without_buyer_country_renders_lo_tzuyan(self) -> None:
        row = pl._pipeline_rows_from_forecasts(
            [_forecast_row(2, 0.5, dt.date(2027, 1, 1), dt.date(2028, 1, 1))], set(), today=TODAY
        )[0]
        table = pl.pipeline_table([row], today=TODAY)
        assert table["rows"][0][2] == "לא צוין"

    def test_event_pipeline_row_missing_customer_renders_lo_tzuyan(self) -> None:
        rows = pl._pipeline_rows_from_events(
            [{"kind": "contract_award", "title": "t", "program": None, "customer": None, "date": TODAY, "n": 1}],
            set(),
        )
        table = pl.pipeline_table(rows, today=TODAY)
        assert table["rows"][0][2] == "לא צוין"
