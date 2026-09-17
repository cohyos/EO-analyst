"""Unit tests for ``eoa.report.tech_daily``'s pure-logic helpers (no DB, no Ollama): the lookback
window, the citation-registry/layer-grouping builder, the "no news" rendering
(``no_news_extra_sections``), the <=6-column layer status table, the "מה השתנה מאתמול" delta, and
the deterministic QA-fallback draft.

Every DB-touching function (``collect_candidate_items``/``_persist_report``/
``_recent_tech_daily_report``/``_previous_tech_daily_layers_with_news``) is monkeypatched at the
module's own ``_fetchall``/``connection`` reference -- no Postgres, no Ollama, no network. Mirrors
the existing convention in ``tests/unit/test_report_daily.py``/``test_product_lines.py``.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_report_tech_daily.py -q``
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from eoa.report import tech_daily as td
from eoa.report import tech_supply_chain as tsc

_SAMPLE_LAYERS_YAML = {
    "layers": [
        {
            "key": "detectors_fpa",
            "label_he": "גלאים ומישורי מוקד (FPA)",
            "description_he": "FPA, InSb, MCT, SWIR",
            "keywords_he": ["מישור מוקד"],
            "keywords_en": ["FPA"],
            "domains_hint": ["tech_dev"],
            "patent_cpc_hint": ["H01L27/146"],
        },
        {
            "key": "optics",
            "label_he": "עדשות וערוצים אלקטרואופטיים (Optics)",
            "description_he": "lenses, EO channels",
            "keywords_he": ["עדשה"],
            "keywords_en": ["lens"],
            "domains_hint": ["tech_dev"],
            "patent_cpc_hint": [],
        },
        {
            "key": "operational_concepts",
            "label_he": "תפיסות מבצעיות (CONOPS)",
            "description_he": "MUM-T, CCA, kill chains",
            "keywords_he": [],
            "keywords_en": ["MUM-T"],
            "domains_hint": ["c_uas"],
            "patent_cpc_hint": [],
        },
    ]
}


@pytest.fixture(autouse=True)
def _fake_layers_settings(monkeypatch):
    """Same small, deterministic three-layer fixture catalog every test in this file sees --
    keeps assertions independent of future edits to the real ``config/tech_supply_chain.yaml``."""
    fake_settings = SimpleNamespace(tech_supply_chain=_SAMPLE_LAYERS_YAML)
    monkeypatch.setattr(tsc, "settings", lambda: fake_settings)
    yield


def _entry(n: int, *, title: str = "כותרת", tech_maturity: str | None = None) -> dict:
    return {
        "n": n,
        "id": n,
        "kind": "item",
        "title": title,
        "source_name": "מקור",
        "url": f"https://example.test/{n}",
        "published_at": dt.datetime(2026, 9, 16, tzinfo=dt.UTC),
        "summary_he": "תקציר",
        "so_what_he": "מה זה אומר",
        "tech_maturity": tech_maturity,
    }


# ---------------------------------------------------------------------------------------------
# lookback_range
# ---------------------------------------------------------------------------------------------


class TestLookbackRange:
    def test_lookback_days_1_is_a_single_whole_day(self):
        label = dt.date(2026, 9, 17)
        start, end, out_label = td.lookback_range(label, 1)
        assert out_label == label
        assert start.date() == label
        assert end.date() == label
        assert start.time() == dt.time.min
        assert end.hour == 23

    def test_lookback_days_30_spans_30_whole_days(self):
        label = dt.date(2026, 9, 17)
        start, end, _ = td.lookback_range(label, 30)
        assert (end.date() - start.date()).days == 29  # 30 whole days inclusive of both ends

    def test_period_end_none_defaults_to_today(self):
        _, _, label = td.lookback_range(None, 1)
        assert label == td._today_jerusalem()

    def test_lookback_days_zero_is_clamped_to_one_day(self):
        label = dt.date(2026, 9, 17)
        start, end, _ = td.lookback_range(label, 0)
        assert start.date() == end.date() == label


# ---------------------------------------------------------------------------------------------
# build_layer_content: citation registry + layer grouping
# ---------------------------------------------------------------------------------------------


class TestBuildLayerContent:
    def test_items_without_layers_are_excluded_from_registry(self):
        items = [{"id": 1, "title": "x", "source_name": "s", "url": "u", "published_at": None,
                  "summary_he": None, "so_what_he": None, "tech_maturity": None}]
        citation_items, layer_entries = td.build_layer_content(items, [], {}, {})
        assert citation_items == []
        assert all(v == [] for v in layer_entries.values())

    def test_item_with_one_layer_gets_registered_and_grouped(self):
        items = [{"id": 5, "title": "FPA breakthrough", "source_name": "s", "url": "u",
                  "published_at": None, "summary_he": None, "so_what_he": None, "tech_maturity": None}]
        citation_items, layer_entries = td.build_layer_content(items, [], {5: [("detectors_fpa", "core")]}, {})
        assert len(citation_items) == 1
        assert citation_items[0]["n"] == 1
        assert citation_items[0]["kind"] == "item"
        assert layer_entries["detectors_fpa"][0]["relevance"] == "core"
        assert layer_entries["detectors_fpa"][0]["id"] == citation_items[0]["id"]
        assert layer_entries["optics"] == []

    def test_item_matching_two_layers_with_different_relevance_per_layer(self):
        items = [{"id": 5, "title": "x", "source_name": "s", "url": "u", "published_at": None,
                  "summary_he": None, "so_what_he": None, "tech_maturity": None}]
        citation_items, layer_entries = td.build_layer_content(
            items, [], {5: [("detectors_fpa", "core"), ("optics", "tangential")]}, {}
        )
        assert len(citation_items) == 1  # one canonical registry entry regardless of layer count
        assert layer_entries["detectors_fpa"][0]["relevance"] == "core"
        assert layer_entries["optics"][0]["relevance"] == "tangential"
        # per-layer copies, not the same shared dict (relevance differs)
        assert layer_entries["detectors_fpa"][0] is not layer_entries["optics"][0]

    def test_items_and_patents_share_one_sequential_n_space_without_id_collision(self):
        items = [{"id": 1, "title": "item one", "source_name": "s", "url": "u", "published_at": None,
                  "summary_he": None, "so_what_he": None, "tech_maturity": None}]
        patents = [{"id": 1, "pub_number": "US123", "title": "patent one", "abstract": "a",
                    "assignees": ["Acme"], "publication_date": None, "url": "u2"}]
        citation_items, _layer_entries = td.build_layer_content(
            items, patents, {1: [("detectors_fpa", "core")]}, {1: [("optics", "core")]}
        )
        ns = [e["n"] for e in citation_items]
        assert ns == [1, 2]  # sequential, no collision despite both source ids == 1
        assert citation_items[1]["kind"] == "patent"
        assert citation_items[1]["id"] is None  # patents never populate items_included


class TestCoreVsTangential:
    def test_layer_status_table_reports_no_news_when_only_tangential(self):
        tangential = _entry(1)
        tangential["relevance"] = "tangential"
        layer_entries = {"detectors_fpa": [tangential], "optics": [], "operational_concepts": []}
        table = td.layer_status_table(layer_entries)
        fpa_row = next(r for r in table["rows"] if r[0] == "גלאים ומישורי מוקד (FPA)")
        assert fpa_row[2] == "אין חדש"
        assert fpa_row[1] == "1"  # still counted in "פריטים"
        assert fpa_row[4] == "[1]"  # tangential citation still surfaced in sources

    def test_no_news_extra_sections_notes_tangential_count_with_citations(self):
        tangential = _entry(7)
        tangential["relevance"] = "tangential"
        layer_entries = {"detectors_fpa": [tangential], "optics": [], "operational_concepts": []}
        sections = td.no_news_extra_sections(layer_entries)
        fpa_section = next(s for s in sections if s["title_he"] == "גלאים ומישורי מוקד (FPA)")
        assert fpa_section["body_he"].startswith("אין חדש בתחום זה בתקופה.")
        assert "1 פריטים משיקים" in fpa_section["body_he"]
        assert "[7]" in fpa_section["body_he"]

    def test_no_news_extra_sections_plain_line_when_zero_entries_at_all(self):
        layer_entries = {"detectors_fpa": [], "optics": [], "operational_concepts": []}
        sections = td.no_news_extra_sections(layer_entries)
        fpa_section = next(s for s in sections if s["title_he"] == "גלאים ומישורי מוקד (FPA)")
        assert fpa_section["body_he"] == "אין חדש בתחום זה בתקופה."

    def test_layer_with_core_entry_is_excluded_from_no_news(self):
        core = _entry(1)
        core["relevance"] = "core"
        layer_entries = {"detectors_fpa": [core], "optics": [], "operational_concepts": []}
        sections = td.no_news_extra_sections(layer_entries)
        assert "גלאים ומישורי מוקד (FPA)" not in {s["title_he"] for s in sections}

    def test_layers_items_block_excludes_tangential_entries(self):
        core = _entry(1, title="core item")
        core["relevance"] = "core"
        tangential = _entry(2, title="tangential item")
        tangential["relevance"] = "tangential"
        layer_entries = {"detectors_fpa": [core, tangential], "optics": [], "operational_concepts": []}
        block = td._layers_items_block(layer_entries)
        assert "core item" in block
        assert "tangential item" not in block

    def test_draft_tech_daily_skips_llm_when_only_tangential(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("chat_structured must not be called for tangential-only layers")

        monkeypatch.setattr(td, "chat_structured", _boom)
        tangential = _entry(1)
        tangential["relevance"] = "tangential"
        layer_entries = {"detectors_fpa": [tangential], "optics": [], "operational_concepts": []}
        draft = td.draft_tech_daily(layer_entries)
        assert draft.sections == []

    def test_drop_non_core_sections_removes_section_with_no_core_entries(self):
        from eoa.llm.schemas.analysis import DailyReportDraft, Sentence, StructuredSection

        tangential = _entry(1)
        tangential["relevance"] = "tangential"
        layer_entries = {"detectors_fpa": [tangential], "optics": [], "operational_concepts": []}
        draft = DailyReportDraft(
            sections=[
                StructuredSection(
                    title_he="גלאים", domain="detectors_fpa",
                    sentences=[Sentence(text_he="hallucinated prose", cites=[1])],
                )
            ]
        )
        cleaned = td._drop_non_core_sections(draft, layer_entries)
        assert cleaned.sections == []

    def test_drop_non_core_sections_keeps_section_with_core_entries(self):
        from eoa.llm.schemas.analysis import DailyReportDraft, Sentence, StructuredSection

        core = _entry(1)
        core["relevance"] = "core"
        layer_entries = {"detectors_fpa": [core], "optics": [], "operational_concepts": []}
        draft = DailyReportDraft(
            sections=[
                StructuredSection(
                    title_he="גלאים", domain="detectors_fpa",
                    sentences=[Sentence(text_he="real prose", cites=[1])],
                )
            ]
        )
        cleaned = td._drop_non_core_sections(draft, layer_entries)
        assert len(cleaned.sections) == 1


class TestPeriodLine:
    def test_one_day_lookback_is_the_plain_24h_line(self):
        start, end, _ = td.lookback_range(dt.date(2026, 9, 17), 1)
        assert td._period_line_he(1, start, end) == "תקופת הסקירה: 24 השעות האחרונות"

    def test_wide_lookback_shows_date_range_and_day_count(self):
        start, end, _ = td.lookback_range(dt.date(2026, 9, 17), 30)
        line = td._period_line_he(30, start, end)
        assert line == "תקופת הסקירה: 19.08.2026–17.09.2026 (סקירה פותחת -- 30 יום)"


# ---------------------------------------------------------------------------------------------
# no_news_extra_sections -- the required literal "no news" line
# ---------------------------------------------------------------------------------------------


class TestNoNewsExtraSections:
    def test_layer_with_zero_entries_gets_the_literal_no_news_line(self):
        layer_entries = {"detectors_fpa": [], "optics": [], "operational_concepts": []}
        sections = td.no_news_extra_sections(layer_entries)
        assert len(sections) == 3
        assert all(s["body_he"] == "אין חדש בתחום זה בתקופה." for s in sections)
        titles = {s["title_he"] for s in sections}
        assert titles == {"גלאים ומישורי מוקד (FPA)", "עדשות וערוצים אלקטרואופטיים (Optics)",
                           "תפיסות מבצעיות (CONOPS)"}

    def test_layer_with_entries_is_excluded_from_no_news_sections(self):
        layer_entries = {"detectors_fpa": [_entry(1)], "optics": [], "operational_concepts": []}
        sections = td.no_news_extra_sections(layer_entries)
        titles = {s["title_he"] for s in sections}
        assert "גלאים ומישורי מוקד (FPA)" not in titles
        assert len(sections) == 2

    def test_no_padding_when_every_layer_has_news(self):
        layer_entries = {"detectors_fpa": [_entry(1)], "optics": [_entry(2)], "operational_concepts": [_entry(3)]}
        assert td.no_news_extra_sections(layer_entries) == []

    def test_every_extra_section_uses_after_summary_position(self):
        layer_entries = {"detectors_fpa": [], "optics": [], "operational_concepts": []}
        assert all(s["position"] == "after_summary" for s in td.no_news_extra_sections(layer_entries))


# ---------------------------------------------------------------------------------------------
# layer_status_table -- <=6 columns, required fields
# ---------------------------------------------------------------------------------------------


class TestLayerStatusTable:
    def test_table_has_at_most_six_columns(self):
        layer_entries = {"detectors_fpa": [_entry(1)], "optics": [], "operational_concepts": []}
        table = td.layer_status_table(layer_entries)
        assert table is not None
        assert len(table["headers"]) <= 6
        assert all(len(row) <= 6 for row in table["rows"])
        assert len(table["headers"]) == len(table["rows"][0])

    def test_every_configured_layer_gets_exactly_one_row(self):
        layer_entries = {"detectors_fpa": [_entry(1)], "optics": [], "operational_concepts": []}
        table = td.layer_status_table(layer_entries)
        assert len(table["rows"]) == 3

    def test_status_column_reflects_news_vs_no_news(self):
        layer_entries = {"detectors_fpa": [_entry(1)], "optics": [], "operational_concepts": []}
        table = td.layer_status_table(layer_entries)
        by_layer = dict(zip([r[0] for r in table["rows"]], table["rows"], strict=True))
        fpa_row = by_layer["גלאים ומישורי מוקד (FPA)"]
        optics_row = by_layer["עדשות וערוצים אלקטרואופטיים (Optics)"]
        assert fpa_row[2] == "חדש"
        assert optics_row[2] == "אין חדש"

    def test_maturity_column_picks_the_highest_ranked_maturity(self):
        entries = [_entry(1, tech_maturity="lab"), _entry(2, tech_maturity="fielded")]
        layer_entries = {"detectors_fpa": entries, "optics": [], "operational_concepts": []}
        table = td.layer_status_table(layer_entries)
        fpa_row = next(r for r in table["rows"] if r[0] == "גלאים ומישורי מוקד (FPA)")
        assert fpa_row[3] == "מבצעי"  # fielded -> "מבצעי", the higher-ranked of the two

    def test_sources_cell_lists_citation_markers(self):
        layer_entries = {"detectors_fpa": [_entry(1), _entry(2)], "optics": [], "operational_concepts": []}
        table = td.layer_status_table(layer_entries)
        fpa_row = next(r for r in table["rows"] if r[0] == "גלאים ומישורי מוקד (FPA)")
        assert fpa_row[4] == "[1][2]"

    def test_empty_layer_config_returns_none(self, monkeypatch):
        monkeypatch.setattr(tsc, "settings", lambda: SimpleNamespace(tech_supply_chain={}))
        assert td.layer_status_table({}) is None


# ---------------------------------------------------------------------------------------------
# changed_since_yesterday_extra_section
# ---------------------------------------------------------------------------------------------


class TestChangedSinceYesterday:
    def test_no_previous_report_returns_none(self, monkeypatch):
        monkeypatch.setattr(td, "_fetchall", lambda *a, **k: [])
        layer_entries = {"detectors_fpa": [_entry(1)], "optics": [], "operational_concepts": []}
        assert td.changed_since_yesterday_extra_section(layer_entries, dt.date(2026, 9, 17)) is None

    def test_no_change_since_previous_report(self, monkeypatch):
        monkeypatch.setattr(td, "_fetchall", lambda *a, **k: [{"qa_report": {"layers_with_news": ["detectors_fpa"]}}])
        layer_entries = {"detectors_fpa": [_entry(1)], "optics": [], "operational_concepts": []}
        section = td.changed_since_yesterday_extra_section(layer_entries, dt.date(2026, 9, 17))
        assert section is not None
        assert section["body_he"] == "אין שינוי בשכבות עם חדש לעומת הדוח הקודם."
        assert section["position"] == "before_summary"

    def test_newly_active_layer_is_reported(self, monkeypatch):
        monkeypatch.setattr(td, "_fetchall", lambda *a, **k: [{"qa_report": {"layers_with_news": []}}])
        layer_entries = {"detectors_fpa": [_entry(1)], "optics": [], "operational_concepts": []}
        section = td.changed_since_yesterday_extra_section(layer_entries, dt.date(2026, 9, 17))
        assert "גלאים ומישורי מוקד (FPA)" in section["body_he"]

    def test_newly_quiet_layer_is_reported(self, monkeypatch):
        monkeypatch.setattr(td, "_fetchall", lambda *a, **k: [{"qa_report": {"layers_with_news": ["optics"]}}])
        layer_entries = {"detectors_fpa": [], "optics": [], "operational_concepts": []}
        section = td.changed_since_yesterday_extra_section(layer_entries, dt.date(2026, 9, 17))
        assert "עדשות וערוצים אלקטרואופטיים (Optics)" in section["body_he"]


# ---------------------------------------------------------------------------------------------
# _deterministic_fallback_draft -- every sentence is cited, schema-valid
# ---------------------------------------------------------------------------------------------


class TestDeterministicFallbackDraft:
    def test_fallback_draft_cites_only_registry_numbers(self):
        layer_entries = {"detectors_fpa": [_entry(1), _entry(2)], "optics": [], "operational_concepts": []}
        draft = td._deterministic_fallback_draft(layer_entries)
        assert len(draft.sections) == 1
        assert draft.sections[0].domain == "detectors_fpa"
        for sentence in draft.sections[0].sentences:
            assert sentence.cites  # non-empty, schema requires it anyway
            assert set(sentence.cites) <= {1, 2}

    def test_fallback_draft_has_no_sections_when_nothing_has_news(self):
        layer_entries = {"detectors_fpa": [], "optics": [], "operational_concepts": []}
        draft = td._deterministic_fallback_draft(layer_entries)
        assert draft.sections == []
        assert draft.system_note_he  # still carries an explanatory note


# ---------------------------------------------------------------------------------------------
# draft_tech_daily -- zero-content short circuit (no LLM call)
# ---------------------------------------------------------------------------------------------


class TestDraftTechDailyNoContent:
    def test_no_layers_with_items_skips_llm_and_returns_no_items_draft(self, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("chat_structured must not be called when nothing has news")

        monkeypatch.setattr(td, "chat_structured", _boom)
        layer_entries = {"detectors_fpa": [], "optics": [], "operational_concepts": []}
        draft = td.draft_tech_daily(layer_entries)
        assert draft.sections == []
        assert "אין חדש" in draft.system_note_he
