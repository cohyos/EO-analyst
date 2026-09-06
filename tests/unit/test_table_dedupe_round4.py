"""W28: a row already shown in an earlier table is not repeated in a later one."""

from eoa.report.docx_builder import dedupe_rows_across_tables


def test_repeated_rows_dropped_with_note() -> None:
    t1 = {
        "title_he": "זכיות",
        "headers": ["א", "ב"],
        "rows": [["Elbit חוזה [3]", "2026"], ["IAI [4]", "2026"]],
    }
    t2 = {
        "title_he": "תחרות",
        "headers": ["א", "ב"],
        "rows": [["Elbit חוזה [3]", "2026"], ["Rafael [5]", "2026"]],
    }
    out = dedupe_rows_across_tables([t1, t2])
    assert out[0]["rows"] == t1["rows"]
    assert out[1]["rows"] == [["Rafael [5]", "2026"]]
    assert "1 שורות כבר הופיעו" in out[1]["note_he"]


def test_text_identity_when_no_citation_and_single_column_untouched() -> None:
    t1 = {"headers": ["x", "y"], "rows": [["Hanwha  ", "KR"]]}
    t2 = {"headers": ["x", "y"], "rows": [["hanwha", "KR"], ["LIG", "KR"]]}
    t3 = {"headers": ["x"], "rows": [["LIG"], ["LIG"]]}
    out = dedupe_rows_across_tables([t1, t2, t3])
    assert out[1]["rows"] == [["LIG", "KR"]]
    assert out[2]["rows"] == [["LIG"], ["LIG"]]
    assert dedupe_rows_across_tables(None) == []
