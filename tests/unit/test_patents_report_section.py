"""Tests for eoa.patents.report_section (A14) -- pure rendering + collection, DB mocked."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from eoa.patents.report_section import (
    BD_SECTION_TITLE_HE,
    MONTHLY_SECTION_TITLE_HE,
    WEEKLY_SECTION_TITLE_HE,
    collect_patents_bd,
    collect_patents_landscape,
    collect_patents_window,
    patents_bd_extra_section,
    patents_bd_table,
    patents_extra_section,
    patents_landscape_extra_section,
    patents_landscape_table,
    patents_table,
)


def _patent_row(**overrides):
    base = dict(
        id=1,
        pub_number="US11234567B2",
        title="Digital pixel readout circuit",
        assignees=["Elbit"],
        subdomain="droic_digital_pixel",
        value_score=62,
    )
    base.update(overrides)
    return base


class TestCollectPatentsWindow:
    def test_returns_rows_from_fetchall(self):
        with patch("eoa.patents.report_section._fetchall", return_value=[_patent_row()]):
            data = collect_patents_window(dt.date(2026, 9, 1), dt.date(2026, 9, 6))
        assert len(data["new_patents"]) == 1

    def test_db_failure_returns_empty_list_not_raise(self):
        with patch("eoa.patents.report_section._fetchall", side_effect=RuntimeError("db down")):
            data = collect_patents_window(dt.date(2026, 9, 1), dt.date(2026, 9, 6))
        assert data["new_patents"] == []


class TestPatentsExtraSection:
    def test_title_and_position(self):
        section = patents_extra_section({"new_patents": [_patent_row()]})
        assert section["title_he"] == WEEKLY_SECTION_TITLE_HE
        assert section["position"] == "after_outlook"

    def test_body_lists_new_patents(self):
        section = patents_extra_section({"new_patents": [_patent_row()]})
        assert "Digital pixel readout circuit" in section["body_he"]
        assert "Elbit" in section["body_he"]

    def test_empty_data_still_returns_section(self):
        section = patents_extra_section({"new_patents": []})
        assert "לא זוהו" in section["body_he"]


class TestPatentsTable:
    def test_none_when_empty(self):
        assert patents_table({"new_patents": []}) is None

    def test_table_shape_uses_sequential_number_not_citation(self):
        table = patents_table({"new_patents": [_patent_row()]})
        assert table is not None
        assert table["headers"][0] == "#"
        assert table["rows"][0][0] == 1
        assert table["rows"][0][1] == "US11234567B2"


class TestCollectPatentsLandscape:
    def test_returns_aggregates(self):
        with patch(
            "eoa.patents.report_section._fetchall",
            side_effect=[[{"subdomain": "droic_digital_pixel", "c": 5}], [{"assignee": "Elbit", "c": 3}]],
        ):
            data = collect_patents_landscape()
        assert data["by_subdomain"][0]["c"] == 5
        assert data["by_assignee"][0]["assignee"] == "Elbit"

    def test_db_failure_returns_empty_lists(self):
        with patch("eoa.patents.report_section._fetchall", side_effect=RuntimeError("boom")):
            data = collect_patents_landscape()
        assert data == {"by_subdomain": [], "by_assignee": []}


class TestPatentsLandscapeSection:
    def test_title(self):
        section = patents_landscape_extra_section({"by_subdomain": [], "by_assignee": []})
        assert section["title_he"] == MONTHLY_SECTION_TITLE_HE

    def test_table_none_when_no_assignees(self):
        assert patents_landscape_table({"by_assignee": []}) is None

    def test_table_shape(self):
        table = patents_landscape_table({"by_assignee": [{"assignee": "Elbit", "c": 3}]})
        assert table is not None
        assert table["rows"][0] == ["Elbit", 3]


class TestCollectPatentsBd:
    def test_matches_by_assignee_country(self):
        with (
            patch("eoa.patents.report_section._fetchall", return_value=[_patent_row(assignees=["Elbit"])]),
            patch(
                "eoa.patents.report_section.resolve_canonical",
                return_value={"name": "Elbit", "kind": "company", "country": "IL"},
            ),
        ):
            data = collect_patents_bd("IL")
        assert len(data["competitor_patents"]) == 1
        assert data["territory"] == "IL"

    def test_no_match_returns_empty(self):
        with (
            patch("eoa.patents.report_section._fetchall", return_value=[_patent_row(assignees=["RandomCo"])]),
            patch("eoa.patents.report_section.resolve_canonical", return_value=None),
        ):
            data = collect_patents_bd("IL")
        assert data["competitor_patents"] == []

    def test_db_failure_returns_empty(self):
        with patch("eoa.patents.report_section._fetchall", side_effect=RuntimeError("boom")):
            data = collect_patents_bd("IL")
        assert data["competitor_patents"] == []


class TestPatentsBdSection:
    def test_title(self):
        section = patents_bd_extra_section({"territory": "IL", "competitor_patents": []})
        assert section["title_he"] == BD_SECTION_TITLE_HE

    def test_table_none_when_empty(self):
        assert patents_bd_table({"competitor_patents": []}) is None

    def test_table_shape(self):
        table = patents_bd_table({"competitor_patents": [_patent_row()]})
        assert table is not None
        assert table["rows"][0][0] == 1
