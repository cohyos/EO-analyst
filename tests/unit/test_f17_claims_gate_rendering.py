"""F17 (SOL-AUDIT-2026-09-24 / SOL-REVIEW-2026-09-24 review): a fully rejected, unsupported
claims-gate answer must stay dropped through rendering -- never crash, and never render the
literal Python string "None" where the text used to be.

`eoa.report.claims_gate.soften_text` returns `None` for a deep-search answer whose every sentence
gets dropped by `gate_text` (F17's own fix: the old `soften_text(...) or it[key]` fallback
silently restored the very unsupported text the gate had just rejected -- see this module's
docstring). This test proves the THREE renderers (`eoa.report.docx_builder.render_markdown`/
`render_html`/`build_docx`) all handle that `None` cleanly, across a non-blocked deep-search entry
(the normal case an answer is ever softened at all).

Mirrors the fixture shapes in `tests/unit/test_deep_search_provenance_links.py`.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_f17_claims_gate_rendering.py -q``
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from eoa.report import claims_gate
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
        }
    ]


@pytest.fixture
def fully_rejected_entry(monkeypatch: pytest.MonkeyPatch) -> dict:
    """A deep-search entry whose `answer_he` is fully rejected by the claims gate -- exercises the
    REAL `claims_gate.gate_deep_search_entries` path (not a hand-built `None`), so this fails
    against the old `... or it[key]` fallback bug too (it would silently restore the text below)."""
    monkeypatch.setattr(claims_gate, "gate_text", lambda text: None)
    entry = {
        "job_id": 55,
        "item_id": 1,
        "question": "מה סטטוס ההזמנה?",
        "outcome": "found",  # NOT "blocked" -- the branch that actually renders answer_he.
        "answer_he": "עלייה משמעותית בפעילות התחרותית.",
        "confidence": 0.8,
    }
    gated = claims_gate.gate_deep_search_entries([entry])
    assert gated[0]["answer_he"] is None, "the gate fixture itself must fully reject the answer"
    return gated[0]


class TestClaimsGateItselfNeverRestoresRejectedText:
    def test_gate_deep_search_entries_drops_not_restores(self, fully_rejected_entry: dict) -> None:
        assert fully_rejected_entry["answer_he"] is None


class TestMarkdownRendersRejectedAnswerCleanly:
    def test_no_crash(self, items, fully_rejected_entry) -> None:
        db.render_markdown(_draft(), items, [], deep_search=[fully_rejected_entry])

    def test_no_literal_none_string(self, items, fully_rejected_entry) -> None:
        md = db.render_markdown(_draft(), items, [], deep_search=[fully_rejected_entry])
        assert "None" not in md
        # the investigation entry itself must still render (heading/outcome/link), just with an
        # empty answer body -- this isn't supposed to make the whole entry vanish.
        assert "מה סטטוס ההזמנה" in md


class TestHtmlRendersRejectedAnswerCleanly:
    def test_no_crash(self, items, fully_rejected_entry) -> None:
        db.render_html(_draft(), items, [], deep_search=[fully_rejected_entry])

    def test_no_literal_none_string(self, items, fully_rejected_entry) -> None:
        html_out = db.render_html(_draft(), items, [], deep_search=[fully_rejected_entry])
        assert "None" not in html_out
        assert "מה סטטוס ההזמנה" in html_out


class TestDocxRendersRejectedAnswerCleanly:
    def test_no_crash(self, items, fully_rejected_entry) -> None:
        db.build_docx(_draft(), items, [], period_end=dt.date(2026, 9, 6), deep_search=[fully_rejected_entry])

    def test_no_literal_none_string(self, items, fully_rejected_entry) -> None:
        doc = db.build_docx(
            _draft(), items, [], period_end=dt.date(2026, 9, 6), deep_search=[fully_rejected_entry]
        )
        texts = [p.text for p in doc.paragraphs]
        assert not any(t.strip() == "None" or "None" in t for t in texts)
        assert any("מה סטטוס ההזמנה" in t for t in texts)
