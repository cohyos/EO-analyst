"""Unit tests for CR-platform-opportunity (2026-09-16): ``eoa.report.platform_opportunities`` --
the deterministic "הזדמנויות אינטגרציה בפלטפורמות" table wired into the daily report and each
product-line report. No live DB required: ``_fetchall`` is monkeypatched (mirrors
``eoa.report.tech_watch``/``eoa.report.acquisition_watch``'s own DB-free unit tests).

Run with:
``PYTHONPATH=agent python -m pytest tests/unit/test_report_platform_opportunities.py -q``
"""

from __future__ import annotations

import datetime as dt

import pytest

from eoa.report import platform_opportunities as po


def _row(**overrides) -> dict:
    base = dict(
        id=22760,
        url="https://www.twz.com/air/yfq-44a-fury-has-been-fit-checked-with-air-to-ground-munitions",
        title="YFQ-44A Fury Has Been Fit Checked With Air-To-Ground Munitions",
        published_at=dt.datetime(2026, 9, 15),
        score=5,
        level="yellow",
        product_lines=["targeting_pods"],
        so_what_he="להערכתנו זו הזדמנות אינטגרציה לפוד ציון מטרות ישראלי.",
        summary_he="אנדוריל ביצעה בדיקות התאמה ל-YFQ-44A Fury.",
        entities_mentioned=["Anduril"],
        source_name="TWZ",
    )
    base.update(overrides)
    return base


class TestProductLineNamesHe:
    def test_known_line_id_with_real_keyword_evidence_resolves_to_hebrew_name(self) -> None:
        # so_what_he contains the literal targeting_pods keywords_he entry "פוד ציון מטרות".
        text = po._product_line_names_he(_row(product_lines=["targeting_pods"]))
        assert "פוד" in text

    def test_unknown_line_id_falls_back_to_raw_id(self) -> None:
        it = _row(product_lines=["not_a_real_line"], so_what_he="", summary_he="", title="")
        assert po._product_line_names_he(it) == "not_a_real_line"

    def test_empty_or_none_returns_placeholder(self) -> None:
        assert po._product_line_names_he({"product_lines": None}) == po.NO_PRODUCT_LINE_TEXT_HE
        assert po._product_line_names_he({"product_lines": []}) == po.NO_PRODUCT_LINE_TEXT_HE

    def test_multiple_lines_with_evidence_are_joined(self) -> None:
        it = _row(
            product_lines=["targeting_pods", "lorop_pods"],
            so_what_he="הזדמנות לפוד ציון מטרות וגם לפוד LOROP.",
            summary_he="",
            title="",
        )
        text = po._product_line_names_he(it)
        assert "," in text

    def test_platform_class_only_mention_is_not_evidence_for_any_tagged_line(self) -> None:
        """R-platform-mapping item 1 (daily_2026-09-28.md): an item that only names the platform
        CLASS/programme (e.g. "NGAD") -- with zero real pod/sensor/gimbal/missile-warning content
        -- must NOT resolve to any of the airborne product lines it happens to be DB-tagged with,
        since "NGAD" is a shared, non-distinctive floor-level opportunity_signals entry duplicated
        across all of them. Reproduces the live bug: an "NGAD engine prototypes" item (about an
        ENGINE, no pod content) was shown as compatible with all four lines."""
        it = _row(
            product_lines=["targeting_pods", "mws_eo", "lorop_pods", "ball_gimbals_16in"],
            title="NGAD engine prototypes take shape in two $3.5bn contracts",
            so_what_he="הפנטגון הגדיל את תקרת התקציב לפיתוח שני אבות טיפוס מנוע עבור תוכנית NGAD.",
            summary_he="שני חוזי אבות טיפוס מקבילים לפיתוח מנוע לתוכנית NGAD.",
        )
        assert po._product_line_names_he(it) == po.NO_PRODUCT_LINE_TEXT_HE

    def test_genuine_line_specific_evidence_survives_alongside_dropped_lines(self) -> None:
        """A line whose OWN distinctive (non-shared) opportunity_signals term is genuinely present
        (here: mws_eo's "missile warning") is kept, while a co-tagged line with no evidence at all
        in the same text is dropped -- the column reflects only what the text actually supports."""
        it = _row(
            product_lines=["targeting_pods", "mws_eo"],
            title="New aircraft self-protection suite unveiled",
            so_what_he="המערכת החדשה מספקת missile warning לכלי הטיס.",
            summary_he="",
        )
        text = po._product_line_names_he(it)
        assert "מערכות התראה" in text  # mws_eo's name_he
        assert "פודי ציון מטרות" not in text  # targeting_pods dropped -- no evidence


class TestExtendRegistry:
    def test_new_row_gets_next_n(self) -> None:
        citation_items: list[dict] = [{"id": 1, "n": 1}]
        rows = [_row(id=22760)]
        po._extend_registry(citation_items, rows)
        assert rows[0]["n"] == 2
        assert any(it["id"] == 22760 and it["n"] == 2 for it in citation_items)

    def test_existing_row_reuses_its_n(self) -> None:
        citation_items: list[dict] = [{"id": 22760, "n": 5}]
        rows = [_row(id=22760)]
        po._extend_registry(citation_items, rows)
        assert rows[0]["n"] == 5
        assert len(citation_items) == 1  # not duplicated

    def test_empty_registry_starts_at_one(self) -> None:
        citation_items: list[dict] = []
        rows = [_row(id=22760)]
        po._extend_registry(citation_items, rows)
        assert rows[0]["n"] == 1


class TestPlatformOpportunityTable:
    def test_returns_none_when_no_items(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(po, "_fetchall", lambda *a, **k: [])
        result = po.platform_opportunity_table([], dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 16))
        assert result is None

    def test_builds_table_with_expected_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(po, "_fetchall", lambda *a, **k: [_row()])
        citation_items: list[dict] = []
        result = po.platform_opportunity_table(citation_items, dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 16))
        assert result is not None
        assert result["title_he"] == po.SECTION_TITLE_HE
        assert len(result["headers"]) <= 6
        assert len(result["rows"]) == 1
        row = result["rows"][0]
        assert row[0] == "YFQ-44A Fury Has Been Fit Checked With Air-To-Ground Munitions"
        assert "[1]" in row[-1]
        assert citation_items and citation_items[0]["id"] == 22760

    def test_line_id_filter_is_forwarded_into_query_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_fetchall(sql: str, params: dict | None = None) -> list[dict]:
            captured["sql"] = sql
            captured["params"] = params
            return []

        monkeypatch.setattr(po, "_fetchall", fake_fetchall)
        po.collect_platform_opportunity_items(
            dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 16), line_id="targeting_pods"
        )
        assert captured["params"]["line"] == "targeting_pods"
        assert "product_lines @> ARRAY" in captured["sql"]

    def test_no_line_id_omits_the_product_line_filter_clause(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict = {}

        def fake_fetchall(sql: str, params: dict | None = None) -> list[dict]:
            captured["sql"] = sql
            return []

        monkeypatch.setattr(po, "_fetchall", fake_fetchall)
        po.collect_platform_opportunity_items(dt.datetime(2026, 9, 1), dt.datetime(2026, 9, 16))
        assert "product_lines @>" not in captured["sql"]
