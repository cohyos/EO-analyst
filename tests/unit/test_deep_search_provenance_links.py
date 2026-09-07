"""R10-links: the deep-search section's new investigation/trigger-item provenance links, across
all three renderers (`eoa.report.docx_builder.render_markdown`/`render_html`/`build_docx`).

Mirrors the fixture shapes in tests/unit/test_renderer_round5.py's `TestDeepSearchBlocked` class
(a new, dedicated file rather than extending that shared one, since this round scopes
docx_builder.py edits to the deep-search section only and other engineers own the rest of that
file's coverage this round).

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_deep_search_provenance_links.py -q``
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from eoa.report import docx_builder as db


def _draft() -> SimpleNamespace:
    return SimpleNamespace(
        exec_summary_he="",
        exec_summary=[],
        sections=[],
        outlook_he="",
        outlook=[],
        open_points_he=[],
        bluf=[],
        assumptions=[],
    )


@pytest.fixture
def items() -> list[dict]:
    return [
        {
            "id": 1,
            "n": 1,
            "title": "Item one",
            "source_name": "Defense News",
            "url": "https://example.com/1",
            "published_at": dt.date(2026, 9, 1),
        },
        {
            "id": 2,
            "n": 2,
            "title": "Item two",
            "source_name": "Janes",
            "url": "https://example.com/2",
            "published_at": dt.date(2026, 9, 2),
        },
    ]


@pytest.fixture
def entry_with_trigger_item() -> dict:
    return {
        "job_id": 55,
        "item_id": 1,
        "question": "מה סטטוס ההזמנה?",
        "outcome": "found",
        "answer_he": "נמצאה תשובה.",
        "confidence": 0.8,
    }


@pytest.fixture
def freestanding_entry() -> dict:
    # No item_id/trigger_item_id at all -- a free-standing question (U12), never orphaned by the
    # `_filter_deep_search_to_items_included` gate either.
    return {"job_id": 77, "question": "שאלה כללית", "outcome": "not_found", "answer_he": ""}


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def test_markdown_links_investigation_detail_page(items, entry_with_trigger_item):
    md = db.render_markdown(_draft(), items, [], deep_search=[entry_with_trigger_item])
    assert "[חקירה #55](/investigations/55)" in md


def test_markdown_cites_trigger_item_via_existing_n_convention(items, entry_with_trigger_item):
    md = db.render_markdown(_draft(), items, [], deep_search=[entry_with_trigger_item])
    assert "פריט מקור [1](#src-1)" in md


def test_markdown_provenance_line_is_indented_not_a_new_entry(items, entry_with_trigger_item):
    """Must stay indented so eoa.qa.d4_investigations's `^-\\s+\\*\\*...` entry regex (which only
    matches un-indented lines) never mistakes it for a second investigation entry."""
    md = db.render_markdown(_draft(), items, [], deep_search=[entry_with_trigger_item])
    section = md.split("## חקירות עומק")[1]
    entry_lines = [ln for ln in section.splitlines() if ln.startswith("- ")]
    assert len(entry_lines) == 1
    assert any(ln.startswith("  - ") and "חקירה #55" in ln for ln in section.splitlines())


def test_markdown_freestanding_question_links_investigation_without_item_citation(items, freestanding_entry):
    md = db.render_markdown(_draft(), items, [], deep_search=[freestanding_entry])
    assert "[חקירה #77](/investigations/77)" in md
    assert "פריט מקור" not in md


def test_markdown_no_provenance_line_when_job_id_missing(items):
    entry = {"question": "שאלה בלי job_id", "outcome": "not_found", "answer_he": ""}
    md = db.render_markdown(_draft(), items, [], deep_search=[entry])
    assert "חקירה #" not in md


def test_markdown_unknown_trigger_item_omits_citation(items):
    entry = {"job_id": 88, "item_id": 999, "question": "q", "outcome": "not_found", "answer_he": ""}
    md = db.render_markdown(_draft(), items, [], deep_search=[entry])
    assert "[חקירה #88](/investigations/88)" in md
    assert "פריט מקור" not in md


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def test_html_links_investigation_detail_page(items, entry_with_trigger_item):
    html_out = db.render_html(_draft(), items, [], deep_search=[entry_with_trigger_item])
    assert '<a href="/investigations/55">' in html_out


def test_html_cites_trigger_item_via_existing_appendix_anchor(items, entry_with_trigger_item):
    html_out = db.render_html(_draft(), items, [], deep_search=[entry_with_trigger_item])
    assert '<a href="#src-1" class="cite">[1]</a>' in html_out


def test_html_provenance_wrapped_in_dedicated_class(items, entry_with_trigger_item):
    html_out = db.render_html(_draft(), items, [], deep_search=[entry_with_trigger_item])
    assert 'class="ds-provenance"' in html_out


# --------------------------------------------------------------------------
# docx
# --------------------------------------------------------------------------


def test_docx_shows_investigation_id_as_plain_text(items, entry_with_trigger_item):
    doc = db.build_docx(
        _draft(), items, [], period_end=dt.date(2026, 9, 6), deep_search=[entry_with_trigger_item]
    )
    texts = [p.text for p in doc.paragraphs]
    assert any("חקירה #55" in t for t in texts)


def test_docx_no_real_hyperlink_relationship_for_investigation_route(items, entry_with_trigger_item):
    """(docx: plain text with the id) -- unlike md/html, the docx must not create an external
    relationship pointing at the app's `/investigations/<id>` route, which doesn't resolve from a
    static file the way the md/html renderers' link does."""
    doc = db.build_docx(
        _draft(), items, [], period_end=dt.date(2026, 9, 6), deep_search=[entry_with_trigger_item]
    )
    rels = doc.part.rels
    assert not any("/investigations/55" in r.target_ref for r in rels.values() if r.is_external)


def test_docx_trigger_item_citation_still_uses_existing_n_mechanism(items, entry_with_trigger_item):
    doc = db.build_docx(
        _draft(), items, [], period_end=dt.date(2026, 9, 6), deep_search=[entry_with_trigger_item]
    )
    texts = [p.text for p in doc.paragraphs]
    assert any("פריט מקור" in t and "[1]" in t for t in texts)


def test_docx_freestanding_question_has_no_citation_text(items, freestanding_entry):
    doc = db.build_docx(_draft(), items, [], period_end=dt.date(2026, 9, 6), deep_search=[freestanding_entry])
    texts = [p.text for p in doc.paragraphs]
    assert any("חקירה #77" in t for t in texts)
    assert not any("פריט מקור" in t for t in texts)
