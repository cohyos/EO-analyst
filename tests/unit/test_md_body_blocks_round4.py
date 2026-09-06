"""User finding 2026-09-06 evening: Markdown tables in section bodies rendered as raw pipes."""

from __future__ import annotations

from eoa.report.docx_builder import _md_blocks

BODY = """| תאריך | חברה | סוג אירוע | מקור |
| --- | --- | --- | --- |
| 2026-09-01 | Elbit | שותפות | [1] |
| — | Hensoldt | שותפות | [3] |

- Teledyne: לא זוהתה פעילות.
- PVP Advanced EO Systems: לא זוהתה פעילות.

ציון-ערך פרוקסי מפטנטים (מדד פרוקסי, לא הערכת שווי כספית):
- Elbit: 12
"""


def test_md_blocks_split_table_bullets_and_paragraphs() -> None:
    blocks = _md_blocks(BODY)
    kinds = [k for k, _ in blocks]
    assert kinds == ["table", "bullets", "para", "bullets"]
    headers, rows = blocks[0][1]
    assert headers == ["תאריך", "חברה", "סוג אירוע", "מקור"]
    assert rows[0] == ["2026-09-01", "Elbit", "שותפות", "[1]"]
    assert blocks[1][1] == ["Teledyne: לא זוהתה פעילות.", "PVP Advanced EO Systems: לא זוהתה פעילות."]
    assert blocks[2][1].startswith("ציון-ערך פרוקסי")


def test_plain_prose_is_one_paragraph_block() -> None:
    assert _md_blocks("משפט ראשון.\nמשפט שני.") == [("para", "משפט ראשון. משפט שני.")]
    assert _md_blocks("") == [("para", "")]


def test_html_extra_section_renders_a_real_table() -> None:
    from eoa.report.docx_builder import _extra_sections_html

    parts: list[str] = []
    _extra_sections_html(
        parts,
        [{"title_he": "מעקב", "body_he": BODY, "position": "after_summary"}],
        "after_summary",
        lambda t: f"<h2>{t}</h2>",
    )
    out = "".join(parts)
    assert "<table>" in out and "<th>חברה</th>" in out and "<ul>" in out
    assert "| ---" not in out
