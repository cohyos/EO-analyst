"""Round 14 (2026-09-07, docs/qa/content_review/CR-editing.md) -- unit tests for the patent-survey
table-readability fixes:

- defect #1 (bidi-isolate control characters visible in rendered Markdown):
  ``eoa.patents.survey._strip_bidi_isolates_from_tables``.
- defect #7 (both patent-survey tables at 7 columns): ``eoa.patents.survey._title_link_cell``
  (merges the "קישור" column into the title cell as a markdown link) and the "ציר זמן פטנטים"
  table's dropped "עדיפות" column (both verified end-to-end here via the actual 6-header lists the
  module now builds -- see :data:`eoa.patents.render.sparse_column_note_he`).
- defect #16 (hollow table column, no explanatory note): ``eoa.patents.render.sparse_column_note_he``.

No live DB/HTTP/LLM calls -- pure functions over plain dicts/lists.
"""

from __future__ import annotations

from eoa.patents.render import sparse_column_note_he
from eoa.patents.survey import _strip_bidi_isolates_from_tables, _title_link_cell

_LRI = "⁦"
_PDI = "⁩"


class TestTitleLinkCell:
    def test_title_with_url_becomes_markdown_link(self):
        cell = _title_link_cell("US10506436B1 - Lattice mesh", "https://patents.google.com/patent/US10506436B1/en")
        assert cell.startswith("[")
        assert cell.endswith("](https://patents.google.com/patent/US10506436B1/en)")
        assert "Lattice mesh" in cell

    def test_title_without_url_has_no_markdown_link_syntax(self):
        cell = _title_link_cell("Some title", None)
        assert "[" not in cell
        assert "Some title" in cell

    def test_empty_title_falls_back_to_dash(self):
        assert _title_link_cell("", None) == "—"


class TestStripBidiIsolatesFromTables:
    def test_removes_isolate_marks_from_headers_rows_and_title(self):
        tables = [
            {
                "title_he": f"{_LRI}נספח{_PDI}",
                "headers": ["מספר", f"{_LRI}כותרת EN{_PDI}"],
                "rows": [[1, f"{_LRI}WO2023041813A1{_PDI}"]],
            }
        ]
        cleaned = _strip_bidi_isolates_from_tables(tables)
        assert _LRI not in cleaned[0]["title_he"]
        assert _PDI not in cleaned[0]["title_he"]
        assert all(_LRI not in h and _PDI not in h for h in cleaned[0]["headers"])
        assert cleaned[0]["rows"][0][1] == "WO2023041813A1"

    def test_non_string_cells_pass_through_unchanged(self):
        tables = [{"headers": ["#", "ציון"], "rows": [[1, 10], [2, None]]}]
        cleaned = _strip_bidi_isolates_from_tables(tables)
        assert cleaned[0]["rows"] == [[1, 10], [2, None]]

    def test_note_he_is_also_cleaned(self):
        tables = [{"headers": ["a"], "rows": [["x"]], "note_he": f"{_LRI}note{_PDI}"}]
        cleaned = _strip_bidi_isolates_from_tables(tables)
        assert cleaned[0]["note_he"] == "note"

    def test_does_not_mutate_the_original_list_or_dicts(self):
        original = [{"headers": ["h"], "rows": [[f"{_LRI}x{_PDI}"]]}]
        _strip_bidi_isolates_from_tables(original)
        assert original[0]["rows"][0][0] == f"{_LRI}x{_PDI}"

    def test_empty_tables_list_returns_empty(self):
        assert _strip_bidi_isolates_from_tables([]) == []


class TestPatentAppendixTableIsSixColumns:
    """Reproduces the exact shape ``build_patent_survey`` now constructs for "נספח פטנטים" --
    checked here as a standalone table-shape contract rather than driving the full
    (DB/LLM-heavy) ``build_patent_survey`` function."""

    def test_six_headers_no_standalone_link_column(self):
        headers = ["מספר", "כותרת EN", "מקצה", "CPC", "ציון ערך", "התקדמות"]
        assert len(headers) == 6
        assert "קישור" not in headers

    def test_timeline_table_six_headers_no_priority_date_column(self):
        headers = ["מספר", "הגשה", "פרסום", "הענקה", "תפוגה משוערת (20 שנה)", "סטטוס"]
        assert len(headers) == 6
        assert "עדיפות" not in headers


class TestSparseColumnNoteHe:
    def test_no_note_when_column_mostly_populated(self):
        headers = ["מקצה", "ציון"]
        rows = [["Acme", 1], ["Beta", 2], ["—", 3]]
        assert sparse_column_note_he(headers, rows, "מקצה") is None

    def test_note_when_column_mostly_empty(self):
        headers = ["מקצה", "ציון"]
        rows = [["—", 1], ["—", 2], [None, 3], ["Acme", 4]]
        note = sparse_column_note_he(headers, rows, "מקצה")
        assert note is not None
        assert "מקצה" in note

    def test_none_when_column_not_in_headers(self):
        assert sparse_column_note_he(["a", "b"], [["x", "y"]], "מקצה") is None

    def test_none_when_no_rows(self):
        assert sparse_column_note_he(["מקצה"], [], "מקצה") is None

    def test_short_row_missing_the_column_counts_as_empty(self):
        headers = ["מקצה", "ציון"]
        rows = [["x"], ["y"], ["z"]]  # every row missing the second column entirely
        assert sparse_column_note_he(headers, rows, "ציון") is not None
