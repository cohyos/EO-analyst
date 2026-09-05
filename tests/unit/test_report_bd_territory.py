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
    assert ["שם", "תאריכים", "עיר", "רלוונטיות"] in table_headers
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
