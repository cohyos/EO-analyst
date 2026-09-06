"""Unit tests for eoa.report.bd_territory (A11 "דוח מיקוד לפיתוח עסקי, מכירה ושיווק לפי
טריטוריה"), with every DB- and LLM-touching collector monkeypatched so these run with no
Postgres and no Ollama.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_report_bd_territory.py -q``
"""

from __future__ import annotations

import datetime as dt

import docx
import pytest

from eoa.llm.schemas.reports import BdAction, BdTerritoryReportDraft
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
        "summary_he": "צבא ארה\"ב הכריז על מכרז חדש לפוד כיוון [1].",
        "so_what_he": "הזדמנות לספקי EO/IR.",
    },
    {
        "id": 502,
        "n": 2,
        "title": "New C-UAS program announced",
        "domain": "c_uas",
        "source_name": "Janes",
        "url": "https://example.com/502",
        "published_at": dt.date(2026, 8, 25),
        "level": "orange",
        "geography": "US",
        "entities_mentioned": [],
        "summary_he": "הוכרזה תוכנית חדשה נגד כטב\"מים בארה\"ב [2].",
        "so_what_he": "שוק צומח.",
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
    "forecasts": [
        {
            "platform": 'כטב"ם MALE',
            "buyer_country": "US",
            "payload_need": "מטע\"ד ג'ימבלי EO/IR",
            "likelihood": 0.6,
            "window_from": dt.date(2027, 1, 1),
            "window_to": dt.date(2027, 12, 1),
            "created_at": dt.date(2026, 8, 15),
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


def _draft_fixture() -> BdTerritoryReportDraft:
    return BdTerritoryReportDraft(
        exec_summary_he=(
            'צבא ארה"ב הכריז על מכרז חדש לפוד כיוון [1]. שוק ה-C-UAS בארה"ב צומח [2].'
        ),
        market_bullets_he=[
            'צבא ארה"ב מקדם מכרז לפוד כיוון חדש [1].',
            'תוכנית C-UAS חדשה הוכרזה בארה"ב [2].',
            "Vendor X זכתה בעסקת ג'ימבל בהיקף 50 מיליון דולר [3].",
        ],
        sections=[],
        recommended_actions=[
            BdAction(
                action_he="ליזום פגישת היכרות עם US Army לקראת ה-RFI הימי",
                priority="H",
                rationale_he="נפתח RFI לכיוון ימי בארה\"ב [4], ותחזית הרכש תומכת בכך [5].",
                owner_role_he="פיתוח עסקי",
                timing_he="מיידי",
            ),
            BdAction(
                action_he="להציג יכולות ג'ימבל בכנס AUSA הקרוב",
                priority="M",
                rationale_he="Vendor X כבר פעילה בשוק הזה [3].",
                owner_role_he="שיווק",
                timing_he="רבעון הקרוב",
            ),
        ],
        risks_assumptions_he="הדוח מבוסס על כיסוי מקורות חלקי בחלון הזמן שנבדק בלבד.",
        outlook_he="",
        open_points_he=["האם ידוע על תקציב מאושר ל-RFI הימי?"],
    )


@pytest.fixture
def patch_bd_collectors(monkeypatch, tmp_path):
    monkeypatch.setattr(bdt, "collect_market_items", lambda t, s, e, max_items=250: [dict(it) for it in MARKET_ITEMS])
    monkeypatch.setattr(bdt, "collect_platform_events", lambda t, s, e, limit=25: [dict(ev) for ev in PLATFORM_EVENTS])
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
    monkeypatch.setattr(bdt, "draft_bd_territory", lambda *a, **k: _draft_fixture())
    monkeypatch.setattr(bdt, "_persist_report", lambda *a, **k: 777)
    monkeypatch.setattr(bdt, "_report_path", lambda territory, period_end, ext: tmp_path / f"bd.{ext}")
    return tmp_path


def test_lookback_range_defaults_to_90_days_window():
    start, end = bdt.lookback_range(90, dt.date(2026, 9, 6))
    assert end == dt.date(2026, 9, 6)
    assert (end - start).days == 89


def test_territory_label_normalizes():
    assert bdt.territory_label("us") == "US"
    assert bdt.territory_label("uk") == "GB"


def test_bullets_text_adds_trailing_period():
    text = bdt._bullets_text(["בולט בלי נקודה", "בולט עם נקודה."])
    lines = text.split("\n")
    assert lines[0].endswith(".")
    assert lines[1] == "בולט עם נקודה."


def test_format_market_items_block_empty():
    assert "לא זוהו" in bdt.format_market_items_block([])


def test_format_competitors_block_empty():
    assert "לא זוהו מתחרים" in bdt.format_competitors_block([])


def test_build_bd_territory_qa_passes_on_fixture_draft(patch_bd_collectors):
    paths = bdt.build_bd_territory("US", 90, period_end=dt.date(2026, 9, 6))
    assert paths.qa.passed, paths.qa.errors
    assert paths.report_id == 777
    assert paths.territory == "US"


def test_build_bd_territory_renders_expected_tables(patch_bd_collectors):
    paths = bdt.build_bd_territory("US", 90, period_end=dt.date(2026, 9, 6))
    doc = docx.Document(str(paths.docx))
    table_headers = [[c.text for c in t.rows[0].cells] for t in doc.tables]

    assert ["תאריך", "פלטפורמה/תוכנית", "רוכש", "ספק", "סכום", "צורך EO/IR נגזר", "מקור"] in table_headers
    assert ["כותרת", "גורם מזמין", "דדליין", "סטטוס", "קישור"] in table_headers
    assert ["מתחרה", "מדינה", "אזכורים בחלון", "תעשייה ישראלית", "זכייה אחרונה"] in table_headers
    assert ["שם", "תאריכים", "עיר", "סטטוס", "מארגן", "רלוונטיות"] in table_headers
    assert ["עדיפות", "פעולה", "נימוק", "אחראי", "תזמון"] in table_headers

    heading_texts = {p.text for p in doc.paragraphs if p.style is not None and p.style.name == "Heading 1"}
    assert "תמונת שוק בטריטוריה" in heading_texts
    assert "סיכונים והנחות" in heading_texts
    assert "דוח מיקוד לפיתוח עסקי — US" in {p.text for p in doc.paragraphs if p.style is not None and p.style.name == "Title"}


def test_build_bd_territory_actions_table_sorted_by_priority(patch_bd_collectors):
    paths = bdt.build_bd_territory("US", 90, period_end=dt.date(2026, 9, 6))
    doc = docx.Document(str(paths.docx))
    actions_table = next(
        t for t in doc.tables if [c.text for c in t.rows[0].cells] == ["עדיפות", "פעולה", "נימוק", "אחראי", "תזמון"]
    )
    priorities = [row.cells[0].text for row in actions_table.rows[1:]]
    assert priorities == ["גבוהה", "בינונית"]


def test_build_bd_territory_no_items_skips_llm_and_still_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(bdt, "collect_market_items", lambda t, s, e, max_items=250: [])
    monkeypatch.setattr(bdt, "collect_platform_events", lambda t, s, e, limit=25: [])
    monkeypatch.setattr(bdt, "collect_tenders_and_forecasts", lambda t, limit=20: {"tenders": [], "forecasts": []})
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

    paths = bdt.build_bd_territory("IL", 90, period_end=dt.date(2026, 9, 6))
    assert paths.qa.passed


def test_extend_registry_with_source_items_appends_new_entry():
    citation_items = [{"id": 1, "n": 1}]
    entries = [{"item_id": 999, "item_title": "t", "item_url": "u", "source_name": "s", "published_at": None}]
    out = bdt._extend_registry_with_source_items(citation_items, entries)
    assert len(out) == 2
    assert entries[0]["n"] == 2


def test_extend_registry_with_source_items_reuses_existing_entry():
    citation_items = [{"id": 999, "n": 1}]
    entries = [{"item_id": 999}]
    out = bdt._extend_registry_with_source_items(citation_items, entries)
    assert len(out) == 1
    assert entries[0]["n"] == 1


def test_extend_registry_with_tenders_assigns_sequential_n():
    citation_items = [{"id": 1, "n": 1}]
    data = {"tenders": [{"title": "t1"}], "forecasts": [{"platform": "p1"}]}
    out = bdt._extend_registry_with_tenders(citation_items, data)
    assert len(out) == 3
    assert data["tenders"][0]["n"] == 2
    assert data["forecasts"][0]["n"] == 3


def test_extend_registry_with_conferences_covers_both_lists():
    citation_items = [{"id": 1, "n": 1}]
    data = {"territory": [{"name": "AUSA"}], "international": [{"name": "DSEI"}]}
    out = bdt._extend_registry_with_conferences(citation_items, data)
    assert len(out) == 3
    assert data["territory"][0]["n"] == 2
    assert data["international"][0]["n"] == 3


def test_attach_win_citations_reuses_market_item_n():
    citation_items = [{"id": 501, "n": 1}, {"id": 502, "n": 2}]
    competitors = [{"recent_wins": [{"item_id": 501}, {"item_id": 999}]}]
    bdt._attach_win_citations(citation_items, competitors)
    assert competitors[0]["recent_wins"][0]["n"] == 1
    assert competitors[0]["recent_wins"][1]["n"] is None


def test_format_tenders_block_includes_citation_numbers():
    data = {"tenders": [{"title": "RFI", "n": 4, "agency": "US Navy"}], "forecasts": []}
    block = bdt.format_tenders_block(data)
    assert "[4]" in block


def test_format_conferences_block_includes_citation_numbers():
    data = {"territory": [{"name": "AUSA", "n": 7}], "international": []}
    block = bdt.format_conferences_block(data)
    assert "[7]" in block


def test_format_competitors_block_includes_win_citation_numbers():
    competitors = [
        {
            "name": "Elbit",
            "is_israeli_industry": True,
            "country": "IL",
            "mentions": 1,
            "recent_wins": [{"title": "win", "n": 3, "date": None, "amount_usd": None}],
        }
    ]
    block = bdt.format_competitors_block(competitors)
    assert "[3]" in block


# --------------------------------------------------------------------------
# BD-1 (docs/qa/findings_Q3_r2.md) regression tests
# --------------------------------------------------------------------------


def test_conferences_table_includes_status_and_organizer():
    data = {
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
    table = bdt.conferences_table(data)
    assert table["headers"] == ["שם", "תאריכים", "עיר", "סטטוס", "מארגן", "רלוונטיות"]
    assert table["rows"][0] == [
        "AUSA",
        "2026-10-10 - 2026-10-12",
        "Washington",
        "מאושר",
        "Association of the United States Army",
        5,
    ]


def test_format_conferences_block_includes_status_and_organizer():
    data = {
        "territory": [
            {
                "name": "AUSA",
                "n": 1,
                "start_date": dt.date(2026, 10, 10),
                "end_date": dt.date(2026, 10, 12),
                "city": "Washington",
                "status": "estimated",
                "organizer": "AUSA Inc.",
            }
        ],
        "international": [],
    }
    block = bdt.format_conferences_block(data)
    assert "משוער" in block
    assert "AUSA Inc." in block
    assert "2026-10-10" in block and "2026-10-12" in block


def test_conference_status_he_falls_back_to_raw_value():
    assert bdt._conference_status_he({"status": "confirmed"}) == "מאושר"
    assert bdt._conference_status_he({"status": "estimated"}) == "משוער"
    assert bdt._conference_status_he({"status": None}) == "—"


def test_drop_empty_sections_removes_blank_prose():
    from eoa.llm.schemas.analysis import ReportSection

    draft = BdTerritoryReportDraft(
        exec_summary_he="תקציר [1].",
        sections=[
            ReportSection(title_he="בינה חזותית (Computer Vision / AI)", domain="computer_vision", prose_he="   "),
            ReportSection(title_he="עם תוכן", domain="tech_dev", prose_he="יש כאן תוכן אמיתי [1]."),
        ],
    )
    cleaned = bdt._drop_empty_sections(draft)
    assert [s.title_he for s in cleaned.sections] == ["עם תוכן"]


def test_drop_empty_sections_noop_when_nothing_blank():
    from eoa.llm.schemas.analysis import ReportSection

    draft = BdTerritoryReportDraft(
        exec_summary_he="תקציר [1].",
        sections=[ReportSection(title_he="X", domain="tech_dev", prose_he="תוכן [1].")],
    )
    cleaned = bdt._drop_empty_sections(draft)
    assert cleaned is draft


def test_strip_uncited_drops_dependent_fragment_starting_with_conjunction():
    draft = BdTerritoryReportDraft(
        exec_summary_he=(
            'צבא ארה"ב מתמודד עם איומי רחפנים קטנים ומשימות מורכבות [1]. '
            "ובפרט לאיומי רחפנים קטנים ומשימות."
        ),
        market_bullets_he=[],
        recommended_actions=[],
        risks_assumptions_he="",
    )
    qa = bdt.QAResult(
        passed=False,
        errors=["..."],
        uncited_sentences=['צבא ארה"ב מתמודד עם איומי רחפנים קטנים ומשימות מורכבות [1].'],
        bad_refs=[],
        duplicate_sentences=[],
    )
    cleaned = bdt._strip_uncited(draft, qa)
    assert "ובפרט" not in cleaned.exec_summary_he
    for sentence in bdt.split_sentences(cleaned.exec_summary_he):
        assert not bdt._starts_with_conjunction(sentence)


def test_strip_uncited_drops_short_verbless_fragment_after_stripped_sentence():
    draft = BdTerritoryReportDraft(
        exec_summary_he=("החברה זכתה בחוזה גדול בארה\"ב [1]. תוצאה ישירה של כך."),
        market_bullets_he=[],
        recommended_actions=[],
        risks_assumptions_he="",
    )
    qa = bdt.QAResult(
        passed=False,
        errors=["..."],
        uncited_sentences=['החברה זכתה בחוזה גדול בארה"ב [1].'],
        bad_refs=[],
        duplicate_sentences=[],
    )
    cleaned = bdt._strip_uncited(draft, qa)
    assert "תוצאה ישירה" not in cleaned.exec_summary_he


def test_strip_uncited_keeps_kept_sentence_followed_by_unrelated_fragment_start():
    # A conjunction-led sentence is dropped unconditionally (BD-1's absolute invariant),
    # regardless of whether the sentence before it survived.
    draft = BdTerritoryReportDraft(
        exec_summary_he='החברה זכתה בחוזה גדול בארה"ב [1]. או שמא לא.',
        market_bullets_he=[],
        recommended_actions=[],
        risks_assumptions_he="",
    )
    qa = bdt.QAResult(passed=True, errors=[], uncited_sentences=[], bad_refs=[], duplicate_sentences=[])
    cleaned = bdt._strip_uncited(draft, qa)
    assert 'החברה זכתה בחוזה גדול בארה"ב [1].' in cleaned.exec_summary_he
    assert "או שמא לא" not in cleaned.exec_summary_he


def test_watchlist_competitor_names_filters_non_watchlist():
    competitors = [
        {"name": "Elbit", "is_watchlist": True},
        {"name": "Vendor X", "is_watchlist": False},
    ]
    assert bdt._watchlist_competitor_names(competitors) == {"Elbit"}


def test_action_promoted_competitor_detects_promotion_verb_and_name():
    action = BdAction(
        action_he="להציג יכולת של Shield AI בכנס AUSA הקרוב",
        priority="M",
        rationale_he="Shield AI פעילה בשוק [1].",
        owner_role_he="שיווק",
        timing_he="רבעון הקרוב",
    )
    assert bdt._action_promoted_competitor(action, {"Shield AI"}) == "Shield AI"


def test_action_promoted_competitor_ignores_non_watchlist_mentions():
    action = BdAction(
        action_he="לפנות ללקוח בנוגע ל-Shield AI כמתחרה בשוק",
        priority="M",
        rationale_he="Shield AI מתחרה בשוק [1].",
        owner_role_he="פיתוח עסקי",
        timing_he="מיידי",
    )
    # No promotion verb present -- monitoring/approaching the customer about a competitor is fine.
    assert bdt._action_promoted_competitor(action, {"Shield AI"}) is None


def test_perspective_violations_flags_competitor_promoting_action():
    draft = BdTerritoryReportDraft(
        exec_summary_he="תקציר [1].",
        recommended_actions=[
            BdAction(
                action_he="להציג יכולת של Shield AI בכנס AUSA",
                priority="H",
                rationale_he="Shield AI פעילה בתחום [1].",
                owner_role_he="שיווק",
                timing_he="רבעון הקרוב",
            ),
            BdAction(
                action_he="ליזום פגישה עם הלקוח בנוגע למכרז",
                priority="M",
                rationale_he="נפתח מכרז רלוונטי [1].",
                owner_role_he="פיתוח עסקי",
                timing_he="מיידי",
            ),
        ],
    )
    competitors = [{"name": "Shield AI", "is_watchlist": True}]
    violations = bdt._perspective_violations(draft, competitors)
    assert len(violations) == 1
    assert violations[0][1] == "Shield AI"


def test_perspective_violations_empty_when_no_watchlist_competitors():
    draft = BdTerritoryReportDraft(
        exec_summary_he="תקציר [1].",
        recommended_actions=[
            BdAction(
                action_he="להציג יכולת של Shield AI בכנס AUSA",
                priority="H",
                rationale_he="Shield AI פעילה בתחום [1].",
                owner_role_he="שיווק",
                timing_he="רבעון הקרוב",
            )
        ],
    )
    assert bdt._perspective_violations(draft, []) == []


def test_drop_perspective_violations_removes_only_offending_action():
    keep = BdAction(
        action_he="ליזום פגישה עם הלקוח", priority="M", rationale_he="נפתח מכרז [1].",
        owner_role_he="פיתוח עסקי", timing_he="מיידי",
    )
    drop = BdAction(
        action_he="להציג יכולת של Shield AI בכנס AUSA", priority="H", rationale_he="Shield AI פעילה [1].",
        owner_role_he="שיווק", timing_he="רבעון הקרוב",
    )
    draft = BdTerritoryReportDraft(exec_summary_he="תקציר [1].", recommended_actions=[keep, drop])
    cleaned = bdt._drop_perspective_violations(draft, [(drop, "Shield AI")])
    assert cleaned.recommended_actions == [keep]


def test_strip_placeholder_echoes_removes_summary_sentence_with_fictional_tender():
    draft = BdTerritoryReportDraft(
        exec_summary_he=(
            'צבא ארה"ב מתמודד עם איומי רחפנים קטנים [1]. הפעולה הדחופה ביותר המומלצת היא ליזום '
            "פגישת היכרות עם גורם מזמין לקראת מכרז X."
        ),
    )
    cleaned = bdt._strip_placeholder_echoes(draft)
    assert "מכרז X" not in cleaned.exec_summary_he
    assert 'צבא ארה"ב מתמודד עם איומי רחפנים קטנים [1].' in cleaned.exec_summary_he


def test_strip_placeholder_echoes_removes_bullet_and_action_with_generic_names():
    draft = BdTerritoryReportDraft(
        exec_summary_he="תקציר [1].",
        market_bullets_he=["התפתחות אמיתית [1].", "התפתחות בכנס Z הקרוב [2]."],
        recommended_actions=[
            BdAction(
                action_he="להציג יכולת Y בכנס Z הקרוב", priority="M", rationale_he="נימוק [1].",
                owner_role_he="שיווק", timing_he="מיידי",
            ),
            BdAction(
                action_he="ליזום פגישה עם US Army", priority="H", rationale_he="נימוק אמיתי [1].",
                owner_role_he="פיתוח עסקי", timing_he="מיידי",
            ),
        ],
    )
    cleaned = bdt._strip_placeholder_echoes(draft)
    assert cleaned.market_bullets_he == ["התפתחות אמיתית [1]."]
    assert len(cleaned.recommended_actions) == 1
    assert cleaned.recommended_actions[0].action_he == "ליזום פגישה עם US Army"


def test_strip_placeholder_echoes_noop_when_clean():
    draft = BdTerritoryReportDraft(
        exec_summary_he="תקציר אמיתי [1].",
        market_bullets_he=["בולט אמיתי [1]."],
        recommended_actions=[
            BdAction(action_he="פעולה", priority="M", rationale_he="נימוק [1].", owner_role_he="מכירות", timing_he="מיידי")
        ],
    )
    cleaned = bdt._strip_placeholder_echoes(draft)
    assert cleaned is draft


def test_cap_draft_lengths_truncates_runaway_bullets_and_actions():
    bullets = [f"בולט מספר {i} [1]." for i in range(15)]
    actions = [
        BdAction(
            action_he=f"פעולה {i}", priority="M", rationale_he="נימוק [1].",
            owner_role_he="מכירות", timing_he="מיידי",
        )
        for i in range(20)
    ]
    draft = BdTerritoryReportDraft(exec_summary_he="תקציר [1].", market_bullets_he=bullets, recommended_actions=actions)
    capped = bdt._cap_draft_lengths(draft)
    assert len(capped.market_bullets_he) == 8
    assert len(capped.recommended_actions) == 8
    assert capped.market_bullets_he == bullets[:8]
    assert capped.recommended_actions == actions[:8]


def test_cap_draft_lengths_noop_when_within_limits():
    draft = BdTerritoryReportDraft(
        exec_summary_he="תקציר [1].",
        market_bullets_he=["בולט [1]."],
        recommended_actions=[
            BdAction(action_he="פעולה", priority="M", rationale_he="נימוק [1].", owner_role_he="מכירות", timing_he="מיידי")
        ],
    )
    assert bdt._cap_draft_lengths(draft) is draft


def test_our_company_block_he_reads_config_default():
    block = bdt._our_company_block_he()
    assert "החברה שלנו" in block or "our_company" in block.lower()


def test_bd_table_counts_context_he_lists_nonzero_counts():
    counts = bdt.BdTableCounts(events=2, tenders=1, forecasts=0, competitors=3, conferences=0)
    text = counts.context_he()
    assert "2 אירועי" in text
    assert "1 מכרזים" in text
    assert "3 מתחרים" in text
    assert "תחזיות" not in text
    assert "כנסים" not in text


def test_bd_table_counts_context_he_empty_when_all_zero():
    assert bdt.BdTableCounts().context_he() == "אין (כל הטבלאות ריקות בתקופה זו)."


def test_tables_only_draft_used_when_items_empty_but_tables_present():
    draft = bdt.draft_bd_territory(
        "US", 90, "", "", "", "", "",
        has_items=False,
        table_counts=bdt.BdTableCounts(competitors=3),
    )
    assert "לא זוהו פריטי שוק חדשים" in draft.exec_summary_he
    assert "3 מתחרים פעילים" in draft.exec_summary_he
    assert draft.recommended_actions == []


def test_no_items_draft_used_when_everything_empty():
    draft = bdt.draft_bd_territory(
        "US", 90, "", "", "", "", "", has_items=False, table_counts=bdt.BdTableCounts()
    )
    assert "אין ממצאים" in draft.exec_summary_he


def test_collect_active_competitors_excludes_zero_activity_company(monkeypatch):
    def fake_fetchall(query, params=None):
        if "FROM entities" in query and "unnest" not in query:
            return [
                {"id": 1, "name": "Active Co", "country": "US", "relevance": 0.9, "is_watchlist": True},
                {"id": 2, "name": "Dormant Co", "country": "US", "relevance": 0.9, "is_watchlist": True},
            ]
        if "unnest(entities_mentioned)" in query:
            return [{"name": "Active Co", "n": 2}]
        if "FROM events" in query:
            return []
        if "FROM graph_edges" in query:
            return [{"n": 0}]
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr(bdt, "_fetchall", fake_fetchall)
    out = bdt.collect_active_competitors("US", [10, 11], dt.date(2026, 1, 1), dt.date(2026, 3, 1))
    names = [c["name"] for c in out]
    assert names == ["Active Co"]


def test_collect_dormant_watchlist_competitors_lists_names_not_in_active_set(monkeypatch):
    def fake_fetchall(query, params=None):
        return [
            {"name": "Active Co", "country": "US"},
            {"name": "Dormant Co", "country": "US"},
            {"name": "Other Territory Co", "country": "IL"},
        ]

    monkeypatch.setattr(bdt, "_fetchall", fake_fetchall)
    dormant = bdt.collect_dormant_watchlist_competitors("US", {"Active Co"})
    assert dormant == ["Dormant Co"]


def test_build_bd_territory_adds_dormant_note_when_few_competitors(patch_bd_collectors, monkeypatch):
    monkeypatch.setattr(bdt, "collect_active_competitors", lambda t, ids, s, e, limit=15: [dict(c) for c in COMPETITORS])
    monkeypatch.setattr(
        bdt, "collect_dormant_watchlist_competitors", lambda t, active, limit=20: ["Dormant Co", "Sleepy Co"]
    )
    paths = bdt.build_bd_territory("US", 90, period_end=dt.date(2026, 9, 6))
    html_text = paths.html.read_text(encoding="utf-8")
    assert "Dormant Co" in html_text
    assert "Sleepy Co" in html_text


def test_build_bd_territory_no_dormant_note_when_three_or_more_competitors(patch_bd_collectors, monkeypatch):
    many_competitors = [dict(COMPETITORS[0]) for _ in range(3)]
    for idx, c in enumerate(many_competitors):
        c["name"] = f"Competitor {idx}"
        c["recent_wins"] = []
    monkeypatch.setattr(bdt, "collect_active_competitors", lambda t, ids, s, e, limit=15: many_competitors)
    monkeypatch.setattr(
        bdt, "collect_dormant_watchlist_competitors", lambda t, active, limit=20: ["Dormant Co"]
    )
    paths = bdt.build_bd_territory("US", 90, period_end=dt.date(2026, 9, 6))
    html_text = paths.html.read_text(encoding="utf-8")
    assert "Dormant Co" not in html_text


def test_build_bd_territory_drops_perspective_violation_after_failed_retry(patch_bd_collectors, monkeypatch):
    bad_action = BdAction(
        action_he="להציג יכולת של Shield AI בכנס AUSA",
        priority="H",
        rationale_he="Shield AI פעילה בתחום [1].",
        owner_role_he="שיווק",
        timing_he="רבעון הקרוב",
    )
    good_action = BdAction(
        action_he="ליזום פגישת היכרות עם US Army",
        priority="M",
        rationale_he='נפתח RFI לכיוון ימי בארה"ב [4].',
        owner_role_he="פיתוח עסקי",
        timing_he="מיידי",
    )
    violating_draft = _draft_fixture().model_copy(update={"recommended_actions": [bad_action, good_action]})

    monkeypatch.setattr(bdt, "draft_bd_territory", lambda *a, **k: violating_draft)
    monkeypatch.setattr(
        bdt, "collect_active_competitors",
        lambda t, ids, s, e, limit=15: [dict(c) for c in COMPETITORS] + [{"entity_id": 2, "name": "Shield AI", "country": "US", "mentions": 1, "is_watchlist": True, "is_israeli_industry": False, "recent_wins": []}],
    )
    # The regeneration retry itself still violates (simulating a stubborn model) -- the fallback
    # must drop exactly the offending action rather than persist a competitor-promoting one.
    monkeypatch.setattr(bdt, "_perspective_corrective_retry", lambda *a, **k: violating_draft)

    paths = bdt.build_bd_territory("US", 90, period_end=dt.date(2026, 9, 6))
    html_text = paths.html.read_text(encoding="utf-8")
    # Shield AI may legitimately appear in the competitors table (it *is* an active competitor) --
    # what must never survive is the action that recommends promoting it.
    assert "להציג יכולת של Shield AI" not in html_text
    assert "ליזום פגישת היכרות" in html_text
    assert "US Army" in html_text
