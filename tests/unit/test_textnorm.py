"""Unit tests for eoa.report.textnorm: Hebrew punctuation normalisation plus (round-17,
2026-09-17) Hebrew company-name canonicalisation -- registry loading, prefix handling, bdi/URL
safety, idempotence, and a report-render integration test across md/html/docx."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from eoa.llm.schemas.analysis import DailyReportDraft, Sentence, StructuredSection
from eoa.report import docx_builder as db
from eoa.report.textnorm import (
    canonicalize_hebrew_names,
    canonicalize_hebrew_names_deep,
    normalize_draft,
    normalize_report_text,
)

# --------------------------------------------------------------------------
# registry loading
# --------------------------------------------------------------------------


def test_registry_loads_from_company_facts_yaml():
    """The live config/company_facts.yaml `hebrew_names` registry actually drives the
    canonicaliser -- not a hardcoded table in the module."""
    assert canonicalize_hebrew_names("ראפאל") == "רפאל"
    assert canonicalize_hebrew_names("ריינמטאל") == "ריינמטל"
    assert canonicalize_hebrew_names("לאונרדו") == "ליאונרדו"


def test_registry_leaves_unregistered_text_untouched():
    assert canonicalize_hebrew_names("חברת דוגמה בע\"מ") == "חברת דוגמה בע\"מ"


def test_none_and_empty_input_returned_unchanged():
    assert canonicalize_hebrew_names(None) is None
    assert canonicalize_hebrew_names("") == ""


# --------------------------------------------------------------------------
# prefix handling (ו/ב/ל/מ/ש/ה/כ)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ראפאל הודיעה היום", "רפאל הודיעה היום"),
        ("וראפאל הודיעה", "ורפאל הודיעה"),
        ("לראפאל יש חוזה", "לרפאל יש חוזה"),
        ("מראפאל התקבלה הודעה", "מרפאל התקבלה הודעה"),
        ("שראפאל תזכה בחוזה", "שרפאל תזכה בחוזה"),
        ("בראפאל עובדים רבים", "ברפאל עובדים רבים"),
        ("כראפאל אין חברה אחרת", "כרפאל אין חברה אחרת"),
        ("הראפאל הידועה", "הרפאל הידועה"),
    ],
)
def test_hebrew_single_letter_prefix_preserved(text: str, expected: str):
    assert canonicalize_hebrew_names(text) == expected


def test_variant_inside_longer_unrelated_word_not_touched():
    """'ראפאלי' is a different (longer) word -- must not be truncated into 'רפאלי'."""
    text = "ראפאלי הוא שם משפחה נפוץ"
    assert canonicalize_hebrew_names(text) == text


def test_variant_after_non_hebrew_boundary_is_still_matched():
    """The boundary check is specifically about an *adjacent Hebrew letter*, not any non-space
    character -- a hyphen (not a Hebrew letter) right before the variant is a valid boundary."""
    text = "משהו-ראפאל היא לא חברה אמיתית"
    assert "רפאל" in canonicalize_hebrew_names(text)
    assert "ראפאל" not in canonicalize_hebrew_names(text)


# --------------------------------------------------------------------------
# bdi / URL safety
# --------------------------------------------------------------------------


def test_bdi_wrapped_span_is_never_touched():
    text = "לפני <bdi>Rafael ראפאל</bdi> אחרי"
    assert canonicalize_hebrew_names(text) == text


def test_url_is_never_touched():
    text = "ראה https://example.com/ראפאל-news לפרטים"
    result = canonicalize_hebrew_names(text)
    assert "https://example.com/ראפאל-news" in result
    # but text outside the URL is still processed if it had a variant
    assert "ראפאל" not in result.replace("https://example.com/ראפאל-news", "")


def test_text_around_bdi_span_still_canonicalized():
    text = "ראפאל ו-<bdi>Rafael</bdi> וגם ראפאל שוב"
    result = canonicalize_hebrew_names(text)
    assert result == "רפאל ו-<bdi>Rafael</bdi> וגם רפאל שוב"


# --------------------------------------------------------------------------
# idempotence
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ראפאל הודיעה",
        "וראפאל וריינמטאל ולאונרדו",
        "רפאל כבר נכונה",
        "",
    ],
)
def test_idempotent(text: str):
    once = canonicalize_hebrew_names(text)
    twice = canonicalize_hebrew_names(once)
    assert once == twice


def test_normalize_report_text_is_idempotent_and_composes_both_passes():
    text = 'תע"א ו-ראפאל'  # ASCII-quote acronym + a company misspelling in one string
    once = normalize_report_text(text)
    assert once == "תע״א ו-רפאל"
    assert normalize_report_text(once) == once


# --------------------------------------------------------------------------
# canonicalize_hebrew_names_deep (tables payload walker)
# --------------------------------------------------------------------------


def test_deep_walks_nested_structures():
    payload = {
        "rows": [["ראפאל", 3], ["ריינמטאל", None]],
        "note_he": "ראפאל ולאונרדו",
        "n": 7,
    }
    result = canonicalize_hebrew_names_deep(payload)
    assert result == {
        "rows": [["רפאל", 3], ["ריינמטל", None]],
        "note_he": "רפאל וליאונרדו",
        "n": 7,
    }


# --------------------------------------------------------------------------
# report-render integration: a draft containing "ראפאל" renders "רפאל" in md/html/docx
# --------------------------------------------------------------------------


@pytest.fixture
def misspelled_draft() -> DailyReportDraft:
    return DailyReportDraft(
        exec_summary=[Sentence(text_he="ראפאל השיקה מערכת חדשה.", cites=[1])],
        sections=[
            StructuredSection(
                title_he="הגנה אווירית",
                domain="air_defense",
                sentences=[Sentence(text_he="וראפאל ממשיכה לפתח את המערכת.", cites=[1])],
            )
        ],
        open_points_he=[],
    )


@pytest.fixture
def misspelled_items() -> list[dict]:
    return [
        {
            "id": 1,
            "n": 1,
            "title": "Rafael unveils system",
            "source_name": "Defense News",
            "url": "https://example.com/rafael",
            "published_at": dt.date(2026, 9, 1),
            "level": "orange",
            "domain": "air_defense",
            "summary_he": "ראפאל השיקה מערכת חדשה.",
            "so_what_he": "",
            "key_facts": [],
        }
    ]


def test_normalize_draft_fixes_company_name_before_render(misspelled_draft):
    fixed = normalize_draft(misspelled_draft)
    assert "ראפאל" not in fixed.exec_summary[0].text_he
    assert fixed.exec_summary[0].text_he == "רפאל השיקה מערכת חדשה."
    assert fixed.sections[0].sentences[0].text_he == "ורפאל ממשיכה לפתח את המערכת."


def test_render_markdown_output_has_no_misspelling(misspelled_draft, misspelled_items):
    fixed = normalize_draft(misspelled_draft)
    md = db.render_markdown(fixed, misspelled_items, [], period_end=dt.date(2026, 9, 1))
    assert "ראפאל" not in md
    assert "רפאל" in md


def test_render_html_output_has_no_misspelling(misspelled_draft, misspelled_items):
    fixed = normalize_draft(misspelled_draft)
    html = db.render_html(fixed, misspelled_items, [], period_end=dt.date(2026, 9, 1))
    assert "ראפאל" not in html
    assert "רפאל" in html


def test_build_docx_output_has_no_misspelling(misspelled_draft, misspelled_items, tmp_path):
    fixed = normalize_draft(misspelled_draft)
    doc = db.build_docx(fixed, misspelled_items, [], period_end=dt.date(2026, 9, 1))
    out_path = tmp_path / "test_report.docx"
    db.save_docx(doc, out_path)
    reopened = __import__("docx").Document(str(out_path))
    full_text = "\n".join(p.text for p in reopened.paragraphs)
    assert "ראפאל" not in full_text
    assert "רפאל" in full_text


def test_tables_payload_canonicalized_in_render(misspelled_items):
    """A company-name misspelling sitting in a deterministic `tables` cell (not draft prose) is
    also fixed -- covers report kinds (product_line/dossier/patents-survey) whose real content
    lives in `tables`, not `draft.sections`."""
    empty_draft = DailyReportDraft(exec_summary=[], sections=[], open_points_he=[])
    tables = [{"title_he": "טבלה", "headers": ["חברה"], "rows": [["ראפאל"]]}]
    md = db.render_markdown(
        empty_draft, misspelled_items, [], period_end=dt.date(2026, 9, 1), tables=tables
    )
    assert "ראפאל" not in md
    assert "רפאל" in md


# --------------------------------------------------------------------------
# regression: the already-rebuilt report files under output/reports/versions/** must be clean
# --------------------------------------------------------------------------


def test_no_rafael_misspelling_in_rendered_report_files():
    versions_dir = Path(__file__).resolve().parents[2] / "output" / "reports" / "versions"
    if not versions_dir.exists():
        pytest.skip("output/reports/versions not present in this checkout")
    offenders = []
    for path in versions_dir.rglob("*"):
        if path.is_file() and path.suffix in (".md", ".html"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if "ראפאל" in text:
                offenders.append(str(path))
    assert offenders == []
