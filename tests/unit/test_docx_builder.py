"""Unit tests for eoa.report.docx_builder: RTL correctness, structure, hyperlinks, run splitting."""

from __future__ import annotations

import datetime as dt

import docx
import pytest
from docx.oxml.ns import qn

from eoa.llm.schemas.analysis import DailyReportDraft, ReportSection
from eoa.report import docx_builder as db
from eoa.report.qa_citations import QAResult

# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def fixture_items() -> list[dict]:
    return [
        {
            "id": 101,
            "n": 1,
            "title": "Elbit Systems wins targeting pod contract",
            "source_name": "Defense News",
            "url": "https://example.com/news/elbit-pod-contract",
            "published_at": dt.date(2026, 9, 1),
            "level": "red",
            "domain": "airborne_pods",
            "summary_he": "אלביט מערכות (Elbit Systems) זכתה בחוזה לאספקת פודי כיוון (Targeting Pods) בהיקף 80 מיליון דולר.",
            "so_what_he": "להערכתנו החוזה מחזק את מעמדה התחרותי של החברה בשוק ה-EO/IR.",
            "key_facts": ["היקף החוזה: 80 מיליון דולר", "תאריך: ספטמבר 2026"],
        },
        {
            "id": 102,
            "n": 2,
            "title": "Rafael launches new C-UAS system",
            "source_name": "Janes",
            "url": "https://example.com/news/rafael-cuas",
            "published_at": dt.date(2026, 9, 2),
            "level": "orange",
            "domain": "c_uas",
            "summary_he": 'רפאל השיקה מערכת נגד כטב"מים (C-UAS) חדשה המבוססת על גילוי EO/IR.',
            "so_what_he": "המערכת צפויה להתחרות במוצרים קיימים בשוק.",
            "key_facts": ["השקה: ספטמבר 2026"],
        },
        {
            "id": 103,
            "n": 3,
            "title": "Naval EO director sea trial completed",
            "source_name": "Naval Today",
            "url": "https://example.com/news/naval-eo-director",
            "published_at": dt.date(2026, 9, 3),
            "level": "orange",
            "domain": "naval_surveillance",
            "summary_he": "בוצע ניסוי ימי (sea trial) מוצלח למערכת EO ימית חדשה.",
            "so_what_he": "מדובר בצעד משמעותי לקראת הבשלה מבצעית (operational maturity).",
            "key_facts": ["ניסוי הושלם בהצלחה"],
        },
    ]


@pytest.fixture
def fixture_events() -> list[dict]:
    return [
        {
            "id": 501,
            "item_id": 101,
            "kind": "contract_award",
            "title": "Targeting pod contract",
            "date": dt.date(2026, 9, 1),
            "amount_usd": 80_000_000,
            "currency": "USD",
            "parties": ["Elbit Systems", "USAF"],
            "customer": "USAF",
            "program": None,
            "source_name": "Defense News",
        },
    ]


@pytest.fixture
def fixture_draft() -> DailyReportDraft:
    return DailyReportDraft(
        exec_summary_he=(
            "אלביט מערכות (Elbit Systems) זכתה בחוזה של 80 מיליון דולר לאספקת פודי כיוון [1]. "
            "רפאל השיקה מערכת C-UAS חדשה [2]. בוצע ניסוי ימי מוצלח למערכת EO ימית [3]."
        ),
        sections=[
            ReportSection(
                title_he='פודים ומטע"דים אוויריים',
                domain="airborne_pods",
                prose_he="אלביט מערכות זכתה בחוזה בהיקף 80 מיליון דולר לאספקת Targeting Pods [1].",
            ),
            ReportSection(
                title_he='נגד כטב"מים',
                domain="c_uas",
                prose_he="רפאל השיקה מערכת C-UAS חדשה המבוססת על חיישני EO/IR [2].",
            ),
        ],
        outlook_he="להערכתנו מגמת ההשקות תימשך ברבעון הקרוב.",
        open_points_he=["האם ידוע מי היו המתחרות שהפסידו במכרז?"],
    )


# --------------------------------------------------------------------------
# split_runs / split_runs_with_citations
# --------------------------------------------------------------------------


def test_split_runs_pure_hebrew():
    runs = db.split_runs("שלום עולם")
    assert runs == [("he", "שלום עולם")]


def test_split_runs_pure_english():
    runs = db.split_runs("Hello World")
    assert runs == [("other", "Hello World")]


def test_split_runs_mixed_hebrew_english():
    runs = db.split_runs("שלום Hello עולם")
    classes = [c for c, _ in runs]
    assert "he" in classes
    assert "other" in classes
    # Reassembling all chunks must reproduce the original text exactly.
    assert "".join(chunk for _, chunk in runs) == "שלום Hello עולם"


def test_split_runs_trailing_space_stays_with_hebrew_word():
    runs = db.split_runs("שלום Hello")
    assert runs[0][0] == "he"
    assert runs[0][1] == "שלום "
    assert runs[1] == ("other", "Hello")


def test_split_runs_with_citations_isolates_citation_token():
    tokens = db.split_runs_with_citations("משפט עם הפניה [3] להמשך.")
    classes = [c for c, _ in tokens]
    assert "cite" in classes
    cite_chunks = [chunk for c, chunk in tokens if c == "cite"]
    assert cite_chunks == ["[3]"]


# --------------------------------------------------------------------------
# add_mixed_paragraph
# --------------------------------------------------------------------------


def test_add_mixed_paragraph_splits_runs_with_rtl_flags():
    doc = docx.Document()
    paragraph = db.add_mixed_paragraph(doc, "שלום Hello עולם")
    assert len(paragraph.runs) >= 2
    saw_hebrew_rtl = False
    saw_other_ltr = False
    for run in paragraph.runs:
        rpr = run._element.find(qn("w:rPr"))
        assert rpr is not None
        rfonts = rpr.find(qn("w:rFonts"))
        assert rfonts is not None
        cs_font = rfonts.get(qn("w:cs"))
        if any(db._char_class(ch) == "he" for ch in run.text):
            assert run.font.rtl is True
            assert cs_font == db.HEBREW_FONT
            saw_hebrew_rtl = True
        else:
            assert not run.font.rtl
            saw_other_ltr = True
    assert saw_hebrew_rtl
    assert saw_other_ltr


def test_add_mixed_paragraph_is_bidi_and_right_aligned():
    doc = docx.Document()
    paragraph = db.add_mixed_paragraph(doc, "שלום עולם")
    ppr = paragraph._p.find(qn("w:pPr"))
    assert ppr is not None
    assert ppr.find(qn("w:bidi")) is not None
    jc = ppr.find(qn("w:jc"))
    assert jc is not None
    assert jc.get(qn("w:val")) == "right"


def test_add_mixed_paragraph_citation_is_superscript():
    doc = docx.Document()
    paragraph = db.add_mixed_paragraph(doc, "עובדה חשובה [7].")
    cite_runs = [r for r in paragraph.runs if r.text == "[7]"]
    assert cite_runs
    assert cite_runs[0].font.superscript is True


# --------------------------------------------------------------------------
# add_hyperlink
# --------------------------------------------------------------------------


def test_add_hyperlink_creates_relationship_and_visible_text():
    doc = docx.Document()
    paragraph = doc.add_paragraph()
    db.add_hyperlink(paragraph, "https://example.com/x", "https://example.com/x")
    assert len(paragraph.hyperlinks) == 1
    assert paragraph.hyperlinks[0].address == "https://example.com/x"
    assert paragraph.text == "https://example.com/x"


# --------------------------------------------------------------------------
# build_docx: full structural assertions
# --------------------------------------------------------------------------


def _all_paragraphs(document):
    yield from document.paragraphs
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from cell.paragraphs


def _count_hyperlinks(document) -> int:
    return sum(len(p.hyperlinks) for p in _all_paragraphs(document))


@pytest.fixture
def built_doc(fixture_draft, fixture_items, fixture_events):
    return db.build_docx(
        fixture_draft,
        fixture_items,
        fixture_events,
        period_end=dt.date(2026, 9, 4),
        qa=QAResult(passed=True),
    )


def test_build_docx_document_defaults_have_bidi_and_hebrew_font(built_doc):
    styles_elm = built_doc.styles.element
    doc_defaults = styles_elm.find(qn("w:docDefaults"))
    assert doc_defaults is not None
    ppr_default = doc_defaults.find(qn("w:pPrDefault") + "/" + qn("w:pPr"))
    assert ppr_default is not None
    assert ppr_default.find(qn("w:bidi")) is not None
    rpr_default = doc_defaults.find(qn("w:rPrDefault") + "/" + qn("w:rPr"))
    assert rpr_default is not None
    assert rpr_default.find(qn("w:rtl")) is not None
    rfonts = rpr_default.find(qn("w:rFonts"))
    assert rfonts.get(qn("w:cs")) == db.HEBREW_FONT

    normal = built_doc.styles["Normal"]
    normal_ppr = normal.element.find(qn("w:pPr"))
    assert normal_ppr is not None
    assert normal_ppr.find(qn("w:bidi")) is not None


def test_build_docx_headings_exist(built_doc):
    heading_texts = {
        p.text for p in built_doc.paragraphs if p.style is not None and p.style.name == "Heading 1"
    }
    assert "תקציר מנהלים" in heading_texts
    assert "טבלת אירועים עסקיים" in heading_texts
    assert "נקודות פתוחות" in heading_texts
    assert "מבט קדימה" in heading_texts
    assert "נספח מקורות" in heading_texts
    assert any("פודים" in t for t in heading_texts)
    assert any("כטב" in t for t in heading_texts)


def test_build_docx_title_page_present(built_doc):
    all_text = "\n".join(p.text for p in built_doc.paragraphs)
    assert db.TITLE_TEXT in all_text
    assert "נוצר אוטומטית" in all_text


def test_build_docx_hyperlink_count_matches_items(built_doc, fixture_items):
    assert _count_hyperlinks(built_doc) == len(fixture_items)


def test_build_docx_has_tables(built_doc):
    # events table + sources appendix table
    assert len(built_doc.tables) == 2
    for table in built_doc.tables:
        tbl_pr = table._tbl.tblPr
        assert tbl_pr.find(qn("w:bidiVisual")) is not None


def test_build_docx_events_table_has_expected_columns(built_doc):
    events_table = built_doc.tables[0]
    header_cells = [c.text for c in events_table.rows[0].cells]
    assert header_cells == ["תאריך", "סוג", "צדדים", "לקוח/תוכנית", "סכום", "מקור"]
    assert len(events_table.rows) == 2  # header + 1 event


def test_build_docx_sources_appendix_has_all_items(built_doc, fixture_items):
    appendix = built_doc.tables[-1]
    assert len(appendix.rows) == len(fixture_items) + 1  # header + N items
    header_cells = [c.text for c in appendix.rows[0].cells]
    assert header_cells == ["#", "כותרת", "מקור", "תאריך", "קישור"]


def test_build_docx_toc_field_present(built_doc):
    xml = built_doc.element.xml
    assert "TOC" in xml
    assert "fldSimple" in xml


def test_build_docx_page_field_in_footer(built_doc):
    footer_xml = built_doc.sections[0].footer._element.xml
    assert "PAGE" in footer_xml


def test_build_docx_update_fields_flagged(built_doc):
    settings_elm = built_doc.settings.element
    el = settings_elm.find(qn("w:updateFields"))
    assert el is not None
    assert el.get(qn("w:val")) == "true"


def test_build_docx_qa_warning_when_failed(fixture_draft, fixture_items, fixture_events):
    qa = QAResult(passed=False, errors=["דוגמה לשגיאה"], uncited_sentences=["משפט"], bad_refs=[99])
    doc = db.build_docx(fixture_draft, fixture_items, fixture_events, period_end=dt.date(2026, 9, 4), qa=qa)
    all_text = "\n".join(p.text for p in doc.paragraphs)
    assert "אזהרה" in all_text


# --------------------------------------------------------------------------
# save / validate round-trip
# --------------------------------------------------------------------------


def test_save_and_validate_docx_round_trip(built_doc, tmp_path):
    out_path = tmp_path / "sample_daily.docx"
    saved = db.save_docx(built_doc, out_path)
    assert saved == out_path
    assert out_path.exists()
    db.validate_docx(out_path)  # must not raise

    reopened = docx.Document(str(out_path))
    assert len(reopened.tables) == 2


def test_validate_docx_detects_duplicate_zip_parts(built_doc, tmp_path):
    import zipfile

    out_path = tmp_path / "broken.docx"
    db.save_docx(built_doc, out_path)

    broken_path = tmp_path / "broken_dup.docx"
    with zipfile.ZipFile(out_path) as src, zipfile.ZipFile(broken_path, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            dst.writestr(item, data)
            if item.filename.endswith("document.xml"):
                dst.writestr(item, data)  # duplicate the same part on purpose

    with pytest.raises(ValueError, match="duplicate"):
        db.validate_docx(broken_path)


def test_validate_docx_detects_malformed_xml(built_doc, tmp_path):
    import zipfile

    out_path = tmp_path / "broken2.docx"
    db.save_docx(built_doc, out_path)

    broken_path = tmp_path / "broken2_malformed.docx"
    with zipfile.ZipFile(out_path) as src, zipfile.ZipFile(broken_path, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.endswith("document.xml"):
                data = data[:-20]  # truncate to corrupt the XML
            dst.writestr(item, data)

    with pytest.raises(ValueError, match="malformed"):
        db.validate_docx(broken_path)


# --------------------------------------------------------------------------
# render_markdown / render_html
# --------------------------------------------------------------------------


def test_render_markdown_contains_citations_and_appendix(fixture_draft, fixture_items, fixture_events):
    md = db.render_markdown(fixture_draft, fixture_items, fixture_events, period_end=dt.date(2026, 9, 4))
    assert "[1]" in md
    assert "נספח מקורות" in md
    assert "https://example.com/news/elbit-pod-contract" in md


def test_render_html_is_rtl_and_links_citations(fixture_draft, fixture_items, fixture_events):
    html_out = db.render_html(fixture_draft, fixture_items, fixture_events, period_end=dt.date(2026, 9, 4))
    assert 'dir="rtl"' in html_out
    assert 'id="src-1"' in html_out
    assert 'href="#src-1"' in html_out


def test_render_html_escapes_content():
    draft = DailyReportDraft(
        exec_summary_he="<script>alert(1)</script> [1].",
        sections=[],
        outlook_he="",
        open_points_he=[],
    )
    items = [{"n": 1, "title": "t", "source_name": "s", "url": "https://x", "published_at": None}]
    html_out = db.render_html(draft, items, [])
    assert "<script>alert(1)</script>" not in html_out
    assert "&lt;script&gt;" in html_out


# --------------------------------------------------------------------------
# hebrew_date_str / fmt_date / fmt_amount
# --------------------------------------------------------------------------


def test_hebrew_date_str_format():
    s = db.hebrew_date_str(dt.date(2026, 9, 4))
    assert "2026" in s
    assert "בספטמבר" in s


def test_fmt_date_variants():
    assert db.fmt_date(None) == "—"
    assert db.fmt_date(dt.date(2026, 1, 5)) == "2026-01-05"
    assert db.fmt_date(dt.datetime(2026, 1, 5, 10, 30)) == "2026-01-05"


def test_fmt_amount_variants():
    assert db.fmt_amount({"amount_usd": None}) == "—"
    assert db.fmt_amount({"amount_usd": 80_000_000, "currency": "USD"}) == "80,000,000 USD"
