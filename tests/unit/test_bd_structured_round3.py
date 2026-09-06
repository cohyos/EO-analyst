"""Round 3 (2026-09-06, D7 judge finding, docs/qa/loop/round_2_judge.md -- score 35): the BD
territory report ("דוח מיקוד לפיתוח עסקי") migrated from free Hebrew prose (post-hoc regex
citation stripping) to the structured, citations-by-construction schema
(``eoa.llm.schemas.bd_territory.BdTerritoryReportDraft``: ``Sentence{text_he, cites[]}``
everywhere) that ``eoa.report.daily``/``eoa.report.weekly`` already use -- mirrors those modules'
own round-1/round-2 migrations.

Every scenario here is fully offline: ``chat_structured`` is monkeypatched with fakes (no Ollama),
and every DB-touching collector is monkeypatched too (no Postgres).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_bd_structured_round3.py -q``
"""

from __future__ import annotations

import datetime as dt

import pytest

from eoa.llm.schemas.analysis import Sentence
from eoa.llm.schemas.bd_territory import BdRecommendedAction, BdTerritoryReportDraft
from eoa.report import bd_territory as bdt

MARKET_ITEMS = [
    {
        "id": 501,
        "n": 1,
        "title": "US Army awards new targeting pod contract",
        "domain": "airborne_pods",
        "source_name": "Defense News",
        "url": "https://example.com/501",
        "published_at": dt.date(2026, 8, 20),
        "level": "red",
        "geography": "US",
        "entities_mentioned": ["Elbit"],
        "summary_he": 'צבא ארה"ב הכריז על מכרז חדש לפוד כיוון.',
        "so_what_he": "הזדמנות לספקי EO/IR.",
    },
]

PLATFORM_EVENTS = [
    {
        "item_id": 999,
        "item_url": "https://example.com/999",
        "item_title": "Vendor X wins gimbal deal",
        "source_name": "Defense News",
        "published_at": dt.date(2026, 8, 22),
        "date": dt.date(2026, 8, 22),
        "platform_he": 'כטב"ם MALE',
        "payload_need_he": "מטע\"ד ג'ימבלי EO/IR",
        "buyer": "US Army",
        "vendor": "Vendor X",
        "amount_usd": 50_000_000,
        "currency": "USD",
    }
]

TENDERS_DATA = {
    "tenders": [
        {
            "title": "RFI: naval EO director",
            "agency": "US Navy",
            "country": "US",
            "deadline": dt.date(2026, 12, 1),
            "status": "open",
            "url": "https://example.gov/rfi/1",
            "published_at": dt.date(2026, 8, 1),
        }
    ],
    "forecasts": [],
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
                "date": dt.date(2026, 8, 20),
                "amount_usd": 10_000_000,
                "currency": "USD",
                "customer": "US Army",
                "program": None,
            }
        ],
    }
]

CONFERENCES_DATA = {
    "territory": [
        {
            "name": "AUSA",
            "start_date": dt.date(2026, 10, 10),
            "end_date": dt.date(2026, 10, 12),
            "city": "Washington",
            "relevance": 5,
            "status": "confirmed",
            "organizer": "Association of the United States Army",
        }
    ],
    "international": [],
}


def _valid_draft() -> BdTerritoryReportDraft:
    """A fully cited draft -- every ``cites`` entry resolves inside the registry
    ``build_bd_territory`` assembles from the fixtures above: market items get n=1, the platform
    event's source item gets n=2, the tender gets n=3, the conference gets n=4."""
    return BdTerritoryReportDraft(
        exec_summary=[
            Sentence(text_he='צבא ארה"ב הכריז על מכרז חדש לפוד כיוון EO/IR.', cites=[1]),
        ],
        market_bullets=[
            Sentence(text_he='צבא ארה"ב מקדם מכרז לפוד כיוון חדש.', cites=[1]),
            Sentence(text_he="Vendor X זכתה בעסקת ג'ימבל.", cites=[2]),
        ],
        competitor_moves=[
            Sentence(text_he='Elbit זכתה בעסקת פוד כיוון בארה"ב.', cites=[1]),
        ],
        sections=[],
        recommended_actions=[
            BdRecommendedAction(
                action_he="ליזום פגישת היכרות עם US Navy לקראת ה-RFI הימי",
                priority="H",
                rationale=[Sentence(text_he='נפתח RFI לכיוון ימי בארה"ב.', cites=[3])],
                owner_role_he="פיתוח עסקי",
                timing_he="מיידי",
                target="US Navy",
                confidence=0.7,
            ),
            BdRecommendedAction(
                action_he="להיערך לכנס AUSA הקרוב",
                priority="M",
                rationale=[Sentence(text_he="AUSA מתקיים בטריטוריה זו.", cites=[4])],
                owner_role_he="שיווק",
                timing_he="רבעון הקרוב",
                target="AUSA",
                confidence=0.6,
            ),
        ],
        analyst_note_he=None,
        risks_assumptions_he="הדוח מבוסס על כיסוי מקורות חלקי בחלון הזמן שנבדק בלבד.",
        open_points_he=[],
    )


def _bad_ref_draft() -> BdTerritoryReportDraft:
    """Schema-valid (every Sentence carries a non-empty ``cites``) but cites a registry number
    (999) that does not exist -- the failure mode ``_run_qa`` must catch at the registry level,
    since the schema itself cannot know the valid range for a given report run."""
    return BdTerritoryReportDraft(
        exec_summary=[Sentence(text_he='צבא ארה"ב הכריז על מכרז חדש.', cites=[999])],
        market_bullets=[],
        competitor_moves=[],
        sections=[],
        recommended_actions=[],
        analyst_note_he=None,
        risks_assumptions_he="",
        open_points_he=[],
    )


@pytest.fixture
def patch_bd_collectors(monkeypatch, tmp_path):
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
        lambda t, months=12, international_limit=5: {
            "territory": [dict(c) for c in CONFERENCES_DATA["territory"]],
            "international": [],
        },
    )
    monkeypatch.setattr(bdt, "collect_dormant_watchlist_competitors", lambda t, active, limit=20: [])
    monkeypatch.setattr(bdt, "_persist_report", lambda *a, **k: 900)
    monkeypatch.setattr(bdt, "_report_path", lambda territory, period_end, ext: tmp_path / f"bd.{ext}")
    return tmp_path


# --------------------------------------------------------------------------
# 1. happy path: every [n] renders
# --------------------------------------------------------------------------


def test_happy_path_renders_every_citation_marker(patch_bd_collectors, monkeypatch):
    monkeypatch.setattr(bdt, "chat_structured", lambda role, schema, messages, **kw: _valid_draft())

    paths = bdt.build_bd_territory("US", 90, period_end=dt.date(2026, 9, 6))

    assert paths.qa.passed, paths.qa.errors
    # Markdown (unlike the HTML renderer's bidi-run splitting, which can separate "[" / digit / "]"
    # into different <bdi> spans for a table cell) keeps every "[n]" marker as a literal, contiguous
    # substring -- both in body prose (via _md_citations) and in table cells (_md_cell does not
    # touch non-URL text) -- so it is the reliable place to assert every citation actually rendered.
    md_text = paths.md.read_text(encoding="utf-8")
    for n in (1, 2, 3, 4):
        assert f"[{n}]" in md_text, f"missing citation marker [{n}] in rendered output"


# --------------------------------------------------------------------------
# 2. an unknown [n] is rejected, then repaired by the corrective retry
# --------------------------------------------------------------------------


def test_unknown_citation_rejected_then_repaired_by_corrective_retry(patch_bd_collectors, monkeypatch):
    calls: list[int] = []

    def fake_chat_structured(role, schema, messages, **kw):
        calls.append(1)
        if len(calls) == 1:
            return _bad_ref_draft()
        return _valid_draft()

    monkeypatch.setattr(bdt, "chat_structured", fake_chat_structured)

    paths = bdt.build_bd_territory("US", 90, period_end=dt.date(2026, 9, 6))

    assert len(calls) == 2, "expected exactly one corrective retry after the first bad-ref draft"
    assert paths.qa.passed, paths.qa.errors


def test_run_qa_flags_out_of_range_citation():
    citation_items = [{"id": 501, "n": 1}]
    draft = _bad_ref_draft()
    qa = bdt._run_qa(draft, citation_items, has_items=True)
    assert not qa.passed
    assert 999 in qa.bad_refs


# --------------------------------------------------------------------------
# 3. two failures produce the deterministic substitute summary, not a banner
# --------------------------------------------------------------------------


def test_two_failures_produce_deterministic_substitute_not_a_banner(patch_bd_collectors, monkeypatch):
    monkeypatch.setattr(bdt, "chat_structured", lambda role, schema, messages, **kw: _bad_ref_draft())

    paths = bdt.build_bd_territory("US", 90, period_end=dt.date(2026, 9, 6))

    # The report still built (never crashed) but is honestly marked as not QA-passed.
    assert not paths.qa.passed

    md_text = paths.md.read_text(encoding="utf-8")
    html_text = paths.html.read_text(encoding="utf-8")
    # The deterministic substitute's own explanatory note is present...
    assert "תקציר מובנה אוטומטית" in md_text
    # ...and the top market item still shows up, cited, in the substitute exec summary.
    assert "US Army awards new targeting pod contract" in md_text
    assert "[1]" in md_text
    # ...but the old free-prose warning banner text must never appear (goal-1/round-3 convention:
    # structured drafts never render docx_builder's "אזהרה" banner -- the deterministic fallback
    # replaces it instead).
    assert "אזהרה: הדוח לא עבר" not in md_text
    assert "אזהרה: הדוח לא עבר" not in html_text


def test_deterministic_fallback_draft_always_passes_its_own_qa():
    citation_items = [
        {"id": 501, "n": 1},
        {"id": 999, "n": 2},
        {"id": -1001, "n": 3},
    ]
    items = [{**MARKET_ITEMS[0], "n": 1}]
    events = [{**PLATFORM_EVENTS[0], "n": 2}]
    draft = bdt._deterministic_fallback_draft(
        "US", items, events, COMPETITORS, TENDERS_DATA, CONFERENCES_DATA
    )
    qa = bdt._run_qa(draft, citation_items, has_items=True)
    assert qa.passed, qa.errors
    assert draft.exec_summary  # a real, cited substitute summary, not an empty one
    assert "תקציר מובנה אוטומטית" in draft.system_note_he


# --------------------------------------------------------------------------
# 4. a no-activity territory still emits the machine-detectable marker
# --------------------------------------------------------------------------


def test_no_activity_territory_still_emits_marker(monkeypatch, tmp_path):
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
    monkeypatch.setattr(
        bdt, "collect_dormant_watchlist_competitors", lambda t, active, limit=20: ["Shield AI", "Anduril"]
    )
    monkeypatch.setattr(bdt, "_persist_report", lambda *a, **k: 1)
    monkeypatch.setattr(bdt, "_report_path", lambda territory, period_end, ext: tmp_path / f"bd_kr.{ext}")

    def _fail_if_called(*a, **k):
        raise AssertionError("chat_structured must not be called when there is no activity at all")

    monkeypatch.setattr(bdt, "chat_structured", _fail_if_called)

    paths = bdt.build_bd_territory("KR", 90, period_end=dt.date(2026, 9, 6))

    assert paths.qa.passed
    md_text = paths.md.read_text(encoding="utf-8")
    assert bdt.NO_ACTIVITY_MARKER_HE in md_text
    assert "Shield AI" in md_text
    assert "Anduril" in md_text


# --------------------------------------------------------------------------
# 5. textnorm (D7 finding 2) is applied to the structured fields
# --------------------------------------------------------------------------


def test_textnorm_applied_to_structured_sentence_fields():
    draft = BdTerritoryReportDraft(
        exec_summary=[Sentence(text_he='פעילות בארה""ב גוברת.', cites=[1])],
        market_bullets=[Sentence(text_he='מכרז חדש בארה""ב.', cites=[1])],
        competitor_moves=[Sentence(text_he='מתחרה פועל בארה""ב.', cites=[1])],
        sections=[],
        recommended_actions=[
            BdRecommendedAction(
                action_he='לפנות לגורם בארה""ב',
                priority="H",
                rationale=[Sentence(text_he='הזדמנות בארה""ב.', cites=[1])],
                owner_role_he="מכירות",
                timing_he="מיידי",
                target='גורם בארה""ב',
            )
        ],
        risks_assumptions_he='כיסוי חלקי בארה""ב.',
        open_points_he=['האם יש עוד מכרזים בארה""ב?'],
    )
    normalized = bdt._normalize_draft_text(draft)

    from eoa.report.textnorm import GERSHAYIM

    for text in (
        normalized.exec_summary[0].text_he,
        normalized.market_bullets[0].text_he,
        normalized.competitor_moves[0].text_he,
        normalized.recommended_actions[0].action_he,
        normalized.recommended_actions[0].rationale[0].text_he,
        normalized.recommended_actions[0].target,
        normalized.risks_assumptions_he,
        normalized.open_points_he[0],
    ):
        assert '""' not in text
    assert f"ארה{GERSHAYIM}ב" in normalized.exec_summary[0].text_he


def test_textnorm_applied_end_to_end_through_build(patch_bd_collectors, monkeypatch):
    doubled_quote_draft = BdTerritoryReportDraft(
        exec_summary=[Sentence(text_he='פעילות בארה""ב גוברת.', cites=[1])],
        market_bullets=[],
        competitor_moves=[],
        sections=[],
        recommended_actions=[],
        analyst_note_he=None,
        risks_assumptions_he="",
        open_points_he=[],
    )
    monkeypatch.setattr(bdt, "chat_structured", lambda role, schema, messages, **kw: doubled_quote_draft)

    paths = bdt.build_bd_territory("US", 90, period_end=dt.date(2026, 9, 6))

    html_text = paths.html.read_text(encoding="utf-8")
    assert '""' not in html_text


# --------------------------------------------------------------------------
# 6. the our_company perspective rule survives into the rendered prompt
# --------------------------------------------------------------------------


def test_our_company_perspective_rule_present_in_prompt(monkeypatch):
    captured_messages: list = []

    def fake_chat_structured(role, schema, messages, **kw):
        captured_messages.append(messages)
        return _valid_draft()

    monkeypatch.setattr(bdt, "chat_structured", fake_chat_structured)

    bdt.draft_bd_territory(
        "US",
        90,
        bdt.format_market_items_block([dict(it) for it in MARKET_ITEMS]),
        "אין אירועים.",
        "אין מכרזים.",
        "אין מתחרים.",
        "אין כנסים.",
        has_items=True,
    )

    assert len(captured_messages) == 1
    prompt = captured_messages[0][1]["content"]
    company = bdt.settings().bd_report.our_company
    assert company.name in prompt
    assert "נקודת המבט של החברה שלנו" in prompt
    assert "לעולם לא" in prompt and "מתחרה" in prompt
    # the structured-schema rule itself is also present -- the model is told cites/Sentence
    # carries the [n], never inline text.
    assert "cites" in prompt
    assert "Sentence" in prompt
