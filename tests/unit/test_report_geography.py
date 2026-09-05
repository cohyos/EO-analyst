"""Tests for eoa.report.geography (U7) -- pure logic + mocked DB, no live Postgres."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

from eoa.report.geography import (
    collect_by_country,
    format_country_section,
    items_by_country,
    normalize_country,
    raw_values_for_country,
)


class TestNormalizeCountry:
    def test_none_and_empty_are_other(self):
        assert normalize_country(None) == "other"
        assert normalize_country("") == "other"

    def test_common_aliases(self):
        assert normalize_country("US") == "US"
        assert normalize_country("USA") == "US"
        assert normalize_country("United States") == "US"
        assert normalize_country("united states of america") == "US"
        assert normalize_country("Israel") == "IL"
        assert normalize_country("ישראל") == "IL"
        assert normalize_country("UK") == "GB"
        assert normalize_country("United Kingdom") == "GB"
        assert normalize_country("EU") == "EU"
        assert normalize_country("NATO") == "NATO"

    def test_bare_two_letter_code_is_uppercased(self):
        assert normalize_country("il") == "IL"
        assert normalize_country("Us") == "US"

    def test_unrecognized_value_is_other(self):
        assert normalize_country("Wakanda") == "other"
        assert normalize_country("some free text") == "other"

    def test_trailing_period_and_case_are_ignored(self):
        assert normalize_country("U.S.A.") == "US"
        assert normalize_country("  Israel  ") == "IL"


class TestRawValuesForCountry:
    def test_includes_known_aliases_and_bare_code(self):
        raws = raw_values_for_country("US")
        assert "usa" in raws
        assert "united states" in raws
        assert "US" in raws
        assert "us" in raws

    def test_unknown_code_still_returns_bare_forms(self):
        raws = raw_values_for_country("ZZ")
        assert "ZZ" in raws
        assert "zz" in raws


def _item_row(**overrides):
    base = dict(geography="US", level="red")
    base.update(overrides)
    return base


class TestItemsByCountry:
    def test_groups_and_counts_by_normalized_country(self):
        rows = [
            _item_row(geography="US", level="red"),
            _item_row(geography="USA", level="orange"),
            _item_row(geography="Israel", level="yellow"),
            _item_row(geography=None, level="archive"),
        ]
        with patch("eoa.report.geography._fetchall", return_value=rows):
            groups = items_by_country()
        by_country = {g["country"]: g for g in groups}
        assert by_country["US"]["total"] == 2
        assert by_country["US"]["red"] == 1
        assert by_country["US"]["orange"] == 1
        assert by_country["IL"]["total"] == 1
        assert by_country["other"]["total"] == 1

    def test_sorted_descending_by_total(self):
        rows = [_item_row(geography="US") for _ in range(3)] + [_item_row(geography="IL")]
        with patch("eoa.report.geography._fetchall", return_value=rows):
            groups = items_by_country()
        assert groups[0]["country"] == "US"
        assert groups[0]["total"] == 3

    def test_passes_filters_through_to_query(self):
        with patch("eoa.report.geography._fetchall", return_value=[]) as mock_fetchall:
            items_by_country(level=["red", "orange"], domain="c_uas", since="2026-09-01")
        args, _kwargs = mock_fetchall.call_args
        query = args[0]
        params = args[1]
        assert "i.level = ANY(%(levels)s)" in query
        assert "i.domain = %(domain)s" in query
        assert params["levels"] == ["red", "orange"]
        assert params["domain"] == "c_uas"


class TestCollectByCountry:
    def test_groups_items_and_caps_top_items(self):
        rows = [
            {"id": i, "title": f"item {i}", "geography": "US", "level": "red", "score": 10 - i}
            for i in range(5)
        ]
        with patch("eoa.report.geography._fetchall", return_value=rows):
            data = collect_by_country(dt.date(2026, 9, 1), dt.date(2026, 9, 4), top_items_per_country=3)
        assert data["total_items"] == 5
        us = next(c for c in data["countries"] if c["country"] == "US")
        assert us["count"] == 5
        assert len(us["top_items"]) == 3

    def test_no_rows_returns_empty_countries(self):
        with patch("eoa.report.geography._fetchall", return_value=[]):
            data = collect_by_country()
        assert data["countries"] == []
        assert data["total_items"] == 0


class TestFormatCountrySection:
    def test_title_and_position(self):
        section = format_country_section({"countries": [{"country": "US", "count": 2, "top_items": []}]})
        assert section["title_he"] == "לפי מדינה"
        assert section["position"] == "after_outlook"

    def test_empty_data_still_returns_section(self):
        section = format_country_section({"countries": []})
        assert "לא זוהו" in section["body_he"]

    def test_body_lists_countries_and_top_items(self):
        data = {
            "countries": [
                {
                    "country": "US",
                    "count": 2,
                    "top_items": [{"id": 7, "title": "EO/IR sensor deal", "level": "red", "score": 9}],
                }
            ]
        }
        section = format_country_section(data)
        assert "US" in section["body_he"]
        assert "EO/IR sensor deal" in section["body_he"]
        assert "[7]" in section["body_he"]
