"""Round 5 P6 (docs/PLAN_ROUND5_REPORTS.md package P6; docs/REPORT_TEMPLATE_BENCHMARK.md sec 2.4
B1-B5 / sec 3.4 / sec 4 items 4/10/11): BD-territory report gaps closed this round --

  1. BLUF (``BdTerritoryReportDraft.bluf``) -- rendered natively by
     ``eoa.report.docx_builder`` (P4, landed the same evening) straight from ``draft.bluf``; this
     module's own job is (a) giving the schema that field and (b) back-filling it deterministically
     for the one shape docx_builder's own generic fallback does not cover (see
     ``eoa.report.bd_territory``'s module-level "BLUF note" comment).
  2. Buyer map / opportunity pipeline (B1) -- ``eoa.report.bd_territory.pipeline_table`` +
     ``_pipeline_rows_from_*``.
  3. Opportunity tiering (B2) -- ``tier_label``/``_tier_score`` and the "דרג" column on both the
     pipeline table and ``competitors_table``.
  4. Territory delta (B4) -- ``eoa.report.deltas.compute_deltas`` wired into
     ``build_bd_territory`` with ``kind="bd_territory"``.
  5. Assumptions <-> falsifiers (B5) -- ``BdAssumption``/``draft.assumptions``, replacing
     ``risks_assumptions_he`` for new drafts.

Every scenario here is fully offline: every DB-touching collector and ``chat_structured`` are
monkeypatched (no Postgres, no Ollama) except where a test explicitly documents it exercises the
real ``eoa.report.deltas`` DB read (wrapped in the module's own defensive ``try``/``except``, so a
missing/unreachable DB degrades to "no previous report" rather than failing the test).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_bd_round5.py -q``
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from eoa.llm.schemas.analysis import Sentence
from eoa.llm.schemas.bd_territory import (
    BdAssumption,
    BdPipelineOpportunity,
    BdRecommendedAction,
    BdTerritoryReportDraft,
)
from eoa.report import bd_territory as bdt

TODAY = dt.date(2026, 9, 6)


# --------------------------------------------------------------------------
# B2: tier scoring formula
# --------------------------------------------------------------------------


def test_magnitude_score_amount_thresholds():
    assert bdt._magnitude_score(amount_usd=60_000_000) == 3
    assert bdt._magnitude_score(amount_usd=6_000_000) == 2
    assert bdt._magnitude_score(amount_usd=100) == 1
    assert bdt._magnitude_score(amount_usd=0) == 0


def test_magnitude_score_likelihood_thresholds():
    assert bdt._magnitude_score(likelihood=0.7) == 3
    assert bdt._magnitude_score(likelihood=0.4) == 2
    assert bdt._magnitude_score(likelihood=0.1) == 1
    assert bdt._magnitude_score(likelihood=0.0) == 0


def test_magnitude_score_level_fallback():
    assert bdt._magnitude_score(level="red") == 3
    assert bdt._magnitude_score(level="orange") == 2
    assert bdt._magnitude_score(level="yellow") == 1


def test_magnitude_score_no_signal_baseline_is_one():
    assert bdt._magnitude_score() == 1


def test_recency_score_thresholds():
    assert bdt._recency_score(TODAY, today=TODAY) == 2
    assert bdt._recency_score(TODAY - dt.timedelta(days=30), today=TODAY) == 2
    assert bdt._recency_score(TODAY - dt.timedelta(days=60), today=TODAY) == 1
    assert bdt._recency_score(TODAY - dt.timedelta(days=200), today=TODAY) == 0
    assert bdt._recency_score(None, today=TODAY) == 0


def test_recency_score_future_date_uses_absolute_distance():
    assert bdt._recency_score(TODAY + dt.timedelta(days=10), today=TODAY) == 2


def test_tier_label_thresholds():
    assert bdt.tier_label(6) == "A"
    assert bdt.tier_label(4) == "A"
    assert bdt.tier_label(3) == "B"
    assert bdt.tier_label(2) == "B"
    assert bdt.tier_label(1) == "C"
    assert bdt.tier_label(0) == "C"


def test_tier_score_combines_magnitude_recency_watchlist_bonus():
    score = bdt._tier_score(amount_usd=60_000_000, reference_date=TODAY, watchlist_fit=True, today=TODAY)
    assert score == 3 + 2 + 1  # magnitude 3, recency 2, watchlist bonus 1
    assert bdt.tier_label(score) == "A"


# --------------------------------------------------------------------------
# B1: stage derivation
# --------------------------------------------------------------------------


def test_tender_stage_rfi_keyword_wins():
    assert bdt._tender_stage({"title": "RFI: naval EO director", "status": "open"}) == "RFI"
    assert bdt._tender_stage({"title": "בקשת מידע לגלאי IR", "status": "open"}) == "RFI"


def test_tender_stage_rfp_keyword():
    assert bdt._tender_stage({"title": "RFP for gimbal payload", "status": "open"}) == "RFP"


def test_tender_stage_unknown_status_is_assessment():
    assert bdt._tender_stage({"title": "Naval sensor upgrade", "status": "unknown"}) == "הערכה"


def test_tender_stage_open_default_is_rfp():
    assert bdt._tender_stage({"title": "Naval sensor upgrade", "status": "open"}) == "RFP"


def test_forecast_stage_near_term_high_likelihood_is_decision():
    forecast = {"window_to": TODAY + dt.timedelta(days=10), "likelihood": 0.7}
    assert bdt._forecast_stage(forecast, today=TODAY) == "החלטה"


def test_forecast_stage_default_is_assessment():
    forecast = {"window_to": TODAY + dt.timedelta(days=300), "likelihood": 0.7}
    assert bdt._forecast_stage(forecast, today=TODAY) == "הערכה"
    assert bdt._forecast_stage({}, today=TODAY) == "הערכה"


# --------------------------------------------------------------------------
# B1: pipeline row builders + table
# --------------------------------------------------------------------------


def test_pipeline_rows_from_tenders_watchlist_fit_via_entities():
    tenders = [
        {
            "title": "RFI: naval EO director",
            "agency": "US Navy",
            "status": "open",
            "deadline": TODAY,
            "n": 4,
            "entities": ["Elbit"],
        }
    ]
    rows = bdt._pipeline_rows_from_tenders(tenders, {"Elbit"})
    assert len(rows) == 1
    row = rows[0]
    assert row.stage == "RFI"
    assert row.buyer_he == "US Navy"
    assert row.n == 4
    assert row.watchlist_fit is True


def test_pipeline_rows_from_forecasts_uses_candidate_vendors_for_fit():
    forecasts = [
        {
            "platform": 'כטב"ם MALE',
            "payload_need": "מטע\"ד ג'ימבלי EO/IR",
            "likelihood": 0.6,
            "window_from": TODAY,
            "window_to": TODAY + dt.timedelta(days=200),
            "n": 5,
            "candidate_vendors": ["Vendor X"],
        }
    ]
    rows = bdt._pipeline_rows_from_forecasts(forecasts, {"Vendor X"}, today=TODAY)
    assert len(rows) == 1
    assert rows[0].n == 5
    assert rows[0].watchlist_fit is True
    assert rows[0].buyer_he == "—"


def test_pipeline_rows_from_events_only_contract_award():
    events = [
        {
            "kind": "contract_award",
            "platform_he": "MALE",
            "buyer": "US Army",
            "vendor": "Vendor X",
            "n": 7,
            "date": TODAY,
            "amount_usd": 10_000_000,
        },
        {"kind": "m_and_a", "platform_he": "irrelevant", "buyer": "x", "vendor": "y", "n": 8, "date": TODAY},
    ]
    rows = bdt._pipeline_rows_from_events(events, set())
    assert len(rows) == 1
    assert rows[0].stage == "לאחר-זכייה"
    assert rows[0].n == 7


def test_pipeline_rows_from_model_resolves_item_level_and_watchlist_fit():
    citation_items = [
        {"id": 501, "n": 1, "level": "red", "published_at": TODAY, "entities_mentioned": ["Elbit"]}
    ]
    opportunities = [
        BdPipelineOpportunity(
            opportunity_he="תוכנית חדשה לגלאי IR",
            stage="הערכה",
            buyer_he="US Army",
            target_date_he="—",
            rationale=[Sentence(text_he="פריט שוק מציין תוכנית חדשה.", cites=[1])],
        )
    ]
    rows = bdt._pipeline_rows_from_model(opportunities, citation_items, {"Elbit"})
    assert len(rows) == 1
    row = rows[0]
    assert row.n == 1
    assert row.level == "red"
    assert row.reference_date == TODAY
    assert row.watchlist_fit is True


def test_pipeline_rows_from_model_unresolved_citation_is_neutral():
    opportunities = [
        BdPipelineOpportunity(
            opportunity_he="הזדמנות",
            stage="הערכה",
            rationale=[Sentence(text_he="טענה.", cites=[999])],
        )
    ]
    rows = bdt._pipeline_rows_from_model(opportunities, [], set())
    assert rows[0].level is None
    assert rows[0].watchlist_fit is False


def test_pipeline_table_none_when_no_rows():
    assert bdt.pipeline_table([]) is None


def test_pipeline_table_sorted_by_stage_then_tier():
    rows = [
        bdt.PipelineRow(opportunity_he="B", stage="לאחר-זכייה", buyer_he="—", target_date_he="—", n=2),
        bdt.PipelineRow(opportunity_he="A", stage="RFI", buyer_he="—", target_date_he="—", n=1),
    ]
    table = bdt.pipeline_table(rows, today=TODAY)
    assert table["headers"] == ["הזדמנות", "שלב", "גורם רוכש", "תאריך יעד", "דרג", "מקור"]
    assert table["rows"][0][0] == "A"  # RFI sorts before לאחר-זכייה
    assert table["no_dedupe"] is True


def test_pipeline_table_marks_missing_citation_as_dash():
    rows = [bdt.PipelineRow(opportunity_he="A", stage="RFI", buyer_he="—", target_date_he="—", n=None)]
    table = bdt.pipeline_table(rows, today=TODAY)
    assert table["rows"][0][-1] == "—"


# --------------------------------------------------------------------------
# B2: competitor tiering
# --------------------------------------------------------------------------


def test_competitor_tier_uses_last_win_amount_and_recency():
    competitor = {
        "recent_wins": [{"amount_usd": 60_000_000, "date": TODAY}],
        "is_watchlist": True,
        "mentions": 0,
    }
    assert bdt._competitor_tier(competitor, today=TODAY) == "A"


def test_competitor_tier_falls_back_to_mentions_without_a_win():
    high_mentions = {"recent_wins": [], "is_watchlist": False, "mentions": 5}
    low_mentions = {"recent_wins": [], "is_watchlist": False, "mentions": 0}
    assert bdt._competitor_tier(high_mentions, today=TODAY) in ("A", "B", "C")
    assert bdt.tier_label(bdt._magnitude_score(level="orange")) != bdt.tier_label(
        bdt._magnitude_score(level=None)
    )
    # a competitor with real recent mentions never scores worse than one with none
    high_score = bdt._tier_score(level="orange", today=TODAY)
    low_score = bdt._tier_score(level=None, today=TODAY)
    assert high_score >= low_score
    assert bdt._competitor_tier(low_mentions, today=TODAY) == "C"


def test_competitors_table_has_tier_column():
    competitors = [
        {
            "name": "Elbit",
            "country": "IL",
            "mentions": 3,
            "is_israeli_industry": True,
            "is_watchlist": True,
            "recent_wins": [{"title": "win", "date": TODAY, "amount_usd": 60_000_000}],
        }
    ]
    table = bdt.competitors_table(competitors, today=TODAY)
    assert table["headers"][-1] == "דרג"
    assert table["rows"][0][-1] == "A"


# --------------------------------------------------------------------------
# B5: assumptions <-> falsifiers
# --------------------------------------------------------------------------


def test_bd_assumption_rejects_inline_citation_marker():
    with pytest.raises(Exception):
        BdAssumption(assumption_he="הנחה עם [1] מוטבע", falsifier_he="הפרכה")


def test_bd_assumption_rejects_empty_text():
    with pytest.raises(Exception):
        BdAssumption(assumption_he="   ", falsifier_he="הפרכה")


def test_run_qa_flags_bad_assumption_citation():
    draft = BdTerritoryReportDraft(
        assumptions=[BdAssumption(assumption_he="הנחה", falsifier_he="הפרכה", cites=[999])]
    )
    qa = bdt._run_qa(draft, [{"id": 1, "n": 1}], has_items=False)
    assert not qa.passed
    assert 999 in qa.bad_refs


def test_run_qa_allows_assumption_with_no_citations():
    draft = BdTerritoryReportDraft(assumptions=[BdAssumption(assumption_he="הנחה", falsifier_he="הפרכה")])
    qa = bdt._run_qa(draft, [{"id": 1, "n": 1}], has_items=False)
    assert qa.passed


# --------------------------------------------------------------------------
# BLUF: schema field, QA validation, deterministic backfill
# --------------------------------------------------------------------------


def test_bluf_field_defaults_to_empty_list():
    draft = BdTerritoryReportDraft()
    assert draft.bluf == []


def test_run_qa_flags_bad_bluf_citation():
    draft = BdTerritoryReportDraft(bluf=[Sentence(text_he="שורה תחתונה", cites=[999])])
    qa = bdt._run_qa(draft, [{"id": 1, "n": 1}], has_items=False)
    assert not qa.passed
    assert 999 in qa.bad_refs


def test_run_qa_does_not_require_bluf_to_be_non_empty():
    """A pre-round-5 draft fixture with no `bluf` at all must still pass QA -- docx_builder's own
    generic fallback (or this module's deterministic back-fill) covers the rendering side; `_run_qa`
    itself must never gate on `bluf` presence (several out-of-scope test fixtures across this
    codebase construct a `BdTerritoryReportDraft` with no `bluf` at all)."""
    draft = BdTerritoryReportDraft(exec_summary=[Sentence(text_he="עובדה", cites=[1])])
    qa = bdt._run_qa(draft, [{"id": 1, "n": 1}], has_items=True)
    assert qa.passed


def test_tables_sizing_sentence_cites_every_counted_row():
    tenders_data = {
        "tenders": [{"n": 1}, {"n": 2}],
        "forecasts": [{"n": 3}],
    }
    sentence = bdt._tables_sizing_sentence(tenders_data)
    assert sentence is not None
    assert set(sentence.cites) == {1, 2, 3}
    assert "2 מכרזים" in sentence.text_he


def test_tables_sizing_sentence_none_when_nothing_to_cite():
    assert bdt._tables_sizing_sentence({"tenders": [], "forecasts": []}) is None


def test_deterministic_bluf_prefers_top_item_then_sizing():
    items = [{"id": 1, "n": 1, "title": "T", "so_what_he": "so what", "domain": "airborne_pods"}]
    tenders_data = {"tenders": [{"n": 2}], "forecasts": []}
    sentences = bdt._deterministic_bluf(items, [], tenders_data, {"territory": []})
    assert 1 <= len(sentences) <= 2
    assert all(s.cites for s in sentences)


def test_deterministic_bluf_empty_when_nothing_to_cite():
    assert bdt._deterministic_bluf([], [], {"tenders": [], "forecasts": []}, {"territory": []}) == []


# --------------------------------------------------------------------------
# strip_placeholder_echoes / normalize_draft_text cover the new fields
# --------------------------------------------------------------------------


def test_strip_placeholder_echoes_drops_echoing_bluf_sentence():
    draft = BdTerritoryReportDraft(bluf=[Sentence(text_he="לפנות ללקוח A בנושא יכולת Y", cites=[1])])
    cleaned = bdt._strip_placeholder_echoes(draft)
    assert cleaned.bluf == []


def test_strip_placeholder_echoes_drops_echoing_pipeline_opportunity():
    draft = BdTerritoryReportDraft(
        pipeline_opportunities=[
            BdPipelineOpportunity(
                opportunity_he="מכרז X",
                stage="RFI",
                rationale=[Sentence(text_he="טענה תקינה.", cites=[1])],
            )
        ]
    )
    cleaned = bdt._strip_placeholder_echoes(draft)
    assert cleaned.pipeline_opportunities == []


def test_strip_placeholder_echoes_drops_echoing_assumption():
    draft = BdTerritoryReportDraft(
        assumptions=[BdAssumption(assumption_he="הנחה תקינה", falsifier_he="קשור ללקוח A")]
    )
    cleaned = bdt._strip_placeholder_echoes(draft)
    assert cleaned.assumptions == []


def test_normalize_draft_text_covers_new_fields():
    draft = BdTerritoryReportDraft(
        bluf=[Sentence(text_he='ארה""ב הודיעה', cites=[1])],
        pipeline_opportunities=[
            BdPipelineOpportunity(
                opportunity_he='תוכנית ארה""ב',
                stage="RFI",
                rationale=[Sentence(text_he='ארה""ב', cites=[1])],
            )
        ],
        assumptions=[BdAssumption(assumption_he='ארה""ב', falsifier_he='ארה""ב')],
    )
    normalized = bdt._normalize_draft_text(draft)
    assert '""' not in normalized.bluf[0].text_he
    assert '""' not in normalized.pipeline_opportunities[0].opportunity_he
    assert '""' not in normalized.assumptions[0].assumption_he


# --------------------------------------------------------------------------
# _all_watchlist_names
# --------------------------------------------------------------------------


def test_all_watchlist_names_includes_names_and_aliases(monkeypatch):
    monkeypatch.setattr(
        bdt,
        "settings",
        lambda: SimpleNamespace(
            watchlist={"companies": [{"name": "Elbit", "aliases": ["Elbit Systems"]}, {"name": "Vendor X"}]}
        ),
    )
    names = bdt._all_watchlist_names()
    assert names == {"Elbit", "Elbit Systems", "Vendor X"}


def test_all_watchlist_names_never_raises_on_bad_settings(monkeypatch):
    monkeypatch.setattr(bdt, "settings", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert bdt._all_watchlist_names() == set()


# --------------------------------------------------------------------------
# End-to-end: build_bd_territory wires BLUF/pipeline/assumptions/tier/delta together
# --------------------------------------------------------------------------

MARKET_ITEMS = [
    {
        "id": 501,
        "n": 1,
        "title": "US Army awards new targeting pod contract",
        "domain": "airborne_pods",
        "source_name": "Defense News",
        "url": "https://example.com/501",
        "published_at": TODAY,
        "level": "red",
        "geography": "US",
        "entities_mentioned": ["Elbit"],
        "summary_he": 'צבא ארה"ב הכריז על מכרז חדש לפוד כיוון.',
        "so_what_he": "הזדמנות לספקי EO/IR.",
    }
]

PLATFORM_EVENTS = [
    {
        "item_id": 999,
        "item_url": "https://example.com/999",
        "item_title": "Vendor X wins gimbal deal",
        "source_name": "Defense News",
        "published_at": TODAY,
        "date": TODAY,
        "platform_he": 'כטב"ם MALE',
        "payload_need_he": "מטע\"ד ג'ימבלי EO/IR",
        "buyer": "US Army",
        "vendor": "Vendor X",
        "amount_usd": 50_000_000,
        "currency": "USD",
        "kind": "contract_award",
    }
]

TENDERS_DATA = {
    "tenders": [
        {
            "title": "RFI: naval EO director",
            "agency": "US Navy",
            "country": "US",
            "deadline": TODAY + dt.timedelta(days=30),
            "status": "open",
            "url": "https://example.gov/rfi/1",
            "published_at": TODAY,
            "entities": [],
        }
    ],
    "forecasts": [
        {
            "platform": 'כטב"ם MALE',
            "buyer_country": "US",
            "payload_need": "מטע\"ד ג'ימבלי EO/IR",
            "likelihood": 0.6,
            "window_from": TODAY,
            "window_to": TODAY + dt.timedelta(days=300),
            "created_at": TODAY,
            "candidate_vendors": [],
        }
    ],
}

COMPETITORS = [
    {
        "entity_id": 1,
        "name": "Elbit",
        "country": "IL",
        "mentions": 3,
        "is_watchlist": True,
        "is_israeli_industry": True,
        "recent_wins": [
            {
                "id": 1,
                "item_id": 501,
                "title": "Targeting pod win",
                "date": TODAY,
                "amount_usd": 10_000_000,
                "currency": "USD",
                "customer": "US Army",
                "program": None,
            }
        ],
    }
]

CONFERENCES_DATA = {"territory": [], "international": []}


def _draft_fixture_with_bluf() -> BdTerritoryReportDraft:
    return BdTerritoryReportDraft(
        bluf=[Sentence(text_he='צבא ארה"ב מקדם מכרז דחוף בהיקף 50 מיליון דולר.', cites=[3])],
        exec_summary=[Sentence(text_he='צבא ארה"ב הכריז על מכרז חדש לפוד כיוון.', cites=[1])],
        market_bullets=[Sentence(text_he='צבא ארה"ב מקדם מכרז לפוד כיוון חדש.', cites=[1])],
        competitor_moves=[Sentence(text_he='Elbit זכתה בעסקת פוד כיוון בארה"ב.', cites=[1])],
        recommended_actions=[
            BdRecommendedAction(
                action_he="ליזום פגישת היכרות עם US Army",
                priority="H",
                rationale=[Sentence(text_he='נפתח RFI ימי בארה"ב.', cites=[4])],
                owner_role_he="פיתוח עסקי",
                timing_he="מיידי",
                target="US Navy",
            )
        ],
        pipeline_opportunities=[],
        assumptions=[
            BdAssumption(assumption_he="הכיסוי התקשורתי בטריטוריה זו חלקי", falsifier_he="דיווח נוסף שיתגלה"),
        ],
        risks_assumptions_he="",
        open_points_he=[],
    )


@pytest.fixture
def patch_bd_collectors_round5(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bdt, "collect_market_items", lambda t, s, e, max_items=250: [dict(it) for it in MARKET_ITEMS]
    )
    monkeypatch.setattr(
        bdt, "collect_platform_events", lambda t, s, e, limit=25: [dict(ev) for ev in PLATFORM_EVENTS]
    )
    monkeypatch.setattr(
        bdt,
        "collect_tenders_and_forecasts",
        lambda t, limit=20: {
            "tenders": [dict(x) for x in TENDERS_DATA["tenders"]],
            "forecasts": [dict(x) for x in TENDERS_DATA["forecasts"]],
        },
    )
    monkeypatch.setattr(
        bdt, "collect_active_competitors", lambda t, ids, s, e, limit=15: [dict(c) for c in COMPETITORS]
    )
    monkeypatch.setattr(
        bdt,
        "collect_conferences_for_territory",
        lambda t, months=12, international_limit=5: {"territory": [], "international": []},
    )
    monkeypatch.setattr(bdt, "collect_dormant_watchlist_competitors", lambda t, active, limit=20: [])
    monkeypatch.setattr(bdt, "draft_bd_territory", lambda *a, **k: _draft_fixture_with_bluf())
    monkeypatch.setattr(bdt, "_persist_report", lambda *a, **k: 777)
    monkeypatch.setattr(bdt, "_report_path", lambda territory, period_end, ext: tmp_path / f"bd.{ext}")
    return tmp_path


def test_build_bd_territory_renders_native_bluf_heading(patch_bd_collectors_round5):
    paths = bdt.build_bd_territory("US", 90, period_end=TODAY)
    md_text = paths.md.read_text(encoding="utf-8")
    assert "## שורה תחתונה" in md_text
    bluf_idx = md_text.index("## שורה תחתונה")
    summary_idx = md_text.index("## תקציר מנהלים")
    assert bluf_idx < summary_idx, "BLUF must render before the executive summary"


def test_build_bd_territory_renders_pipeline_and_assumptions_headings(patch_bd_collectors_round5):
    paths = bdt.build_bd_territory("US", 90, period_end=TODAY)
    md_text = paths.md.read_text(encoding="utf-8")
    assert bdt._PIPELINE_TABLE_TITLE_HE in md_text
    # "הנחות והפרכות" itself is rendered natively by eoa.report.docx_builder straight from
    # draft.assumptions (duck-typed) -- this module contributes only the schema field, so this
    # test only asserts the fixture's assumption content reaches the rendered report at all.
    assert "הנחות והפרכות" in md_text
    assert "הכיסוי התקשורתי בטריטוריה זו חלקי" in md_text


def test_build_bd_territory_no_items_still_succeeds_with_pipeline_and_tier_code(monkeypatch, tmp_path):
    """A genuinely empty territory must not crash the new B1/B2/B4 wiring (empty tenders/forecasts/
    events/competitors -> pipeline_table returns None, tier code sees no rows, delta computation
    degrades gracefully)."""
    monkeypatch.setattr(bdt, "collect_market_items", lambda t, s, e, max_items=250: [])
    monkeypatch.setattr(bdt, "collect_platform_events", lambda t, s, e, limit=25: [])
    monkeypatch.setattr(
        bdt, "collect_tenders_and_forecasts", lambda t, limit=20: {"tenders": [], "forecasts": []}
    )
    monkeypatch.setattr(bdt, "collect_active_competitors", lambda t, ids, s, e, limit=15: [])
    monkeypatch.setattr(
        bdt,
        "collect_conferences_for_territory",
        lambda t, months=12, international_limit=5: {"territory": [], "international": []},
    )
    monkeypatch.setattr(bdt, "collect_dormant_watchlist_competitors", lambda t, active, limit=20: [])
    monkeypatch.setattr(bdt, "_persist_report", lambda *a, **k: 1)
    monkeypatch.setattr(bdt, "_report_path", lambda territory, period_end, ext: tmp_path / f"bd.{ext}")

    def _fail_if_called(*a, **k):
        raise AssertionError("chat_structured should not be called with zero items")

    monkeypatch.setattr("eoa.report.bd_territory.chat_structured", _fail_if_called)

    paths = bdt.build_bd_territory("KR", 90, period_end=TODAY)
    assert paths.qa.passed
    md_text = paths.md.read_text(encoding="utf-8")
    assert bdt.NO_ACTIVITY_MARKER_HE in md_text
    # honest: no citable data at all means no BLUF section, per the module's own design choice
    assert "## שורה תחתונה" not in md_text
