"""Unit tests for Round 5 P4 report-rendering features (docs/REPORT_TEMPLATE_BENCHMARK.md sec
3.1#1/#9, 3.4#10, 3.6, sec 4 items 4/5/7/12; docs/QA_CONTINUOUS_LOOP.md sec 6 D6/D4/D9 checks):

1. BLUF ("שורה תחתונה") before the executive summary, from ``draft.bluf`` or synthesized from the
   daily report's own deterministic zero-narrative fallback; ``extra_sections`` position
   ``before_summary``.
2. Outlook indicator likelihood/confidence rendered as two separate clauses.
3. ``draft.assumptions`` ("הנחות והפרכות") rendered after the outlook.
4. Deep-search ``outcome="blocked"`` rendered distinctly from ``not_found``, with
   ``rerun_note_he`` underneath.
5. A source-reliability ("אמינות") column in the sources appendix.
6. A row/table-level ``related_trend_he`` note on generic tables.

P3 (``eoa.llm.schemas.analysis``: ``DailyReportDraft.bluf``, ``OutlookIndicator.likelihood``/
``confidence_level``/``confidence_basis_he``, a draft's ``assumptions``) has not landed in the
schema yet as of this package -- every test that needs one of those fields uses a minimal
``types.SimpleNamespace`` duck-typed stand-in, exactly the tolerance
``eoa.report.docx_builder`` itself is built for (``getattr``/``_field``), never a real pydantic
instance with fabricated extra kwargs (pydantic quietly drops unknown fields, so that wouldn't
exercise anything real). The BLUF-fallback-synthesis tests are the one exception: they use the
real ``DailyReportDraft`` shape, because ``eoa.report.daily._deterministic_fallback_draft``'s own
shape (empty ``sections``, non-empty ``system_note_he``, an ``exec_summary`` built from top items)
is already exactly what the schema supports today.
"""

from __future__ import annotations

import datetime as dt
import re
from types import SimpleNamespace

import pytest

from eoa.llm.schemas.analysis import DailyReportDraft, Sentence
from eoa.report import docx_builder as db

# Mirrors eoa.qa.d6_daily_report._CLAUSE_SPLIT_RE -- the deterministic QA gate this module's
# likelihood/confidence rendering must satisfy (docs/QA_CONTINUOUS_LOOP.md sec 6 D6 item 2).
_CLAUSE_SPLIT_RE = re.compile(r"[.,;]")
_LIKELIHOOD_WORD_HE = "סבירות"
_CONFIDENCE_WORD_HE = "ביטחון"


def _fake_sentence(text_he: str, cites: list[int] | None = None) -> SimpleNamespace:
    return SimpleNamespace(text_he=text_he, cites=cites or [])


def _structured_draft(**overrides) -> SimpleNamespace:
    """A minimal duck-typed stand-in for the goal-1 structured draft shape (no
    ``exec_summary_he``, so :func:`eoa.report.docx_builder._is_legacy_prose_draft` reports
    ``False``) carrying every field ``docx_builder`` might look at, all defaulting to empty."""
    base: dict = {
        "bluf": [],
        "exec_summary": [_fake_sentence("משפט תקציר.", [1])],
        "sections": [],
        "system_note_he": "",
        "analyst_note_he": None,
        "outlook": [],
        "assumptions": [],
        "open_points_he": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


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
            "reliability": {"kind": "primary", "score": 0.87},
        },
    ]


def _docx_heading1_texts(doc) -> list[str]:
    return [p.text for p in doc.paragraphs if p.style is not None and p.style.name == "Heading 1"]


# ---------------------------------------------------------------------------
# 1. BLUF
# ---------------------------------------------------------------------------


class TestBluf:
    def test_draft_bluf_field_renders_before_summary_in_markdown(self, items):
        draft = _structured_draft(bluf=[_fake_sentence("יש להיערך מיידית לאירוע.", [1])])
        md = db.render_markdown(draft, items, [])
        assert "## שורה תחתונה" in md
        assert md.index("שורה תחתונה") < md.index("תקציר מנהלים")
        assert "יש להיערך מיידית לאירוע. [1]" in md

    def test_draft_bluf_field_renders_before_summary_in_html(self, items):
        draft = _structured_draft(bluf=[_fake_sentence("יש להיערך מיידית לאירוע.", [1])])
        html_out = db.render_html(draft, items, [])
        assert html_out.index("שורה תחתונה") < html_out.index("תקציר מנהלים")
        assert "<strong>" in html_out.split("שורה תחתונה")[1].split("</h2>")[1][:200]

    def test_draft_bluf_field_renders_bold_heading_first_in_docx(self, items):
        draft = _structured_draft(bluf=[_fake_sentence("יש להיערך מיידית לאירוע.", [1])])
        doc = db.build_docx(draft, items, [], period_end=dt.date(2026, 9, 6))
        heading_texts = _docx_heading1_texts(doc)
        assert heading_texts[0] == "שורה תחתונה"
        assert heading_texts[1] == "תקציר מנהלים"
        # the paragraph immediately after the BLUF heading is the bold BLUF text
        bluf_heading_idx = next(
            i
            for i, p in enumerate(doc.paragraphs)
            if p.style is not None and p.style.name == "Heading 1" and p.text == "שורה תחתונה"
        )
        bluf_p = doc.paragraphs[bluf_heading_idx + 1]
        assert bluf_p.runs
        assert all(run.font.bold for run in bluf_p.runs if run.text.strip())

    def test_no_bluf_heading_when_draft_has_no_bluf_and_is_not_the_fallback(self, items):
        draft = _structured_draft(sections=[])
        # exec_summary present but system_note_he empty -> not the deterministic-fallback shape.
        md = db.render_markdown(draft, items, [])
        assert "שורה תחתונה" not in md

    def test_extra_sections_before_summary_position_renders_ahead_of_summary(self, items):
        draft = _structured_draft()
        extra = [{"title_he": "הקשר טריטוריאלי", "body_he": "פרטים.", "position": "before_summary"}]
        md = db.render_markdown(draft, items, [], extra_sections=extra)
        assert md.index("הקשר טריטוריאלי") < md.index("תקציר מנהלים")

    def test_deterministic_fallback_draft_synthesizes_a_bluf(self, items):
        """eoa.report.daily._deterministic_fallback_draft's real shape: empty sections, a
        non-empty system_note_he, exec_summary built from top items -- the renderer must still
        show a BLUF (labelled as system-built) even though the model never wrote one."""
        draft = DailyReportDraft(
            exec_summary=[Sentence(text_he="אלביט זכתה בחוזה משמעותי.", cites=[1])],
            sections=[],
            system_note_he="תקציר מובנה אוטומטית (ללא ניסוח מודל): הטיוטה הטקסטואלית נדחתה.",
            outlook=[],
            open_points_he=[],
        )
        md = db.render_markdown(draft, items, [])
        assert "## שורה תחתונה" in md
        assert "אלביט זכתה בחוזה משמעותי. [1]" in md
        assert "ללא ניסוח מודל" in md.split("## שורה תחתונה")[1].split("## תקציר מנהלים")[0]

    def test_normal_structured_draft_with_sections_gets_no_synthesized_bluf(self, items):
        """The synthesized-BLUF fallback must never fire for an ordinary successful draft that
        merely happens to have no `bluf` field yet -- only for the true zero-narrative fallback
        shape (empty sections + system_note_he set)."""
        from eoa.llm.schemas.analysis import StructuredSection

        draft = DailyReportDraft(
            exec_summary=[Sentence(text_he="תקציר רגיל.", cites=[1])],
            sections=[
                StructuredSection(
                    title_he="תחום",
                    domain="airborne_pods",
                    sentences=[Sentence(text_he="ניתוח.", cites=[1])],
                )
            ],
            system_note_he="",
            outlook=[],
            open_points_he=[],
        )
        md = db.render_markdown(draft, items, [])
        assert "שורה תחתונה" not in md


# ---------------------------------------------------------------------------
# 2. Outlook likelihood / confidence
# ---------------------------------------------------------------------------


class TestOutlookLikelihoodConfidence:
    def test_legacy_indicator_without_new_fields_renders_unchanged(self):
        indicator = _fake_sentence("להערכתנו המגמה תימשך.", [])
        assert db._render_outlook_indicator(indicator) == db._render_sentence(indicator)

    def test_likelihood_and_confidence_render_as_two_clauses(self):
        indicator = SimpleNamespace(
            text_he="להערכתנו הפעילות תגבר",
            cites=[],
            likelihood=0.65,
            confidence_level="high",
            confidence_basis_he="שלושה מקורות עצמאיים",
        )
        rendered = db._render_outlook_indicator(indicator)
        assert "סבירות: 65%" in rendered
        assert "ביטחון: גבוה (שלושה מקורות עצמאיים)" in rendered
        # the deterministic QA clause-separation gate: never both keywords in one clause.
        clauses = _CLAUSE_SPLIT_RE.split(rendered)
        assert not any(_LIKELIHOOD_WORD_HE in c and _CONFIDENCE_WORD_HE in c for c in clauses)
        assert any(_LIKELIHOOD_WORD_HE in c for c in clauses)
        assert any(_CONFIDENCE_WORD_HE in c for c in clauses)

    def test_multiple_indicators_stay_clause_separated_when_joined(self):
        """Two indicators joined into one outlook paragraph must not let indicator A's
        "ביטחון" clause fuse with indicator B's "סבירות" clause (no punctuation between them)."""
        ind1 = SimpleNamespace(text_he="אירוע א", cites=[], likelihood=0.2, confidence_level="low")
        ind2 = SimpleNamespace(text_he="אירוע ב", cites=[], likelihood=0.9, confidence_level="high")
        draft = _structured_draft(outlook=[ind1, ind2])
        outlook_text = db._draft_outlook_text(draft)
        clauses = _CLAUSE_SPLIT_RE.split(outlook_text)
        assert not any(_LIKELIHOOD_WORD_HE in c and _CONFIDENCE_WORD_HE in c for c in clauses)

    def test_format_likelihood_ratio_and_already_percent(self):
        assert db._format_likelihood(0.4) == "40%"
        assert db._format_likelihood(40) == "40%"

    def test_format_confidence_level_maps_known_values(self):
        assert db._format_confidence_level("high") == "גבוה"
        assert db._format_confidence_level("medium") == "בינוני"
        assert db._format_confidence_level("low") == "נמוך"
        assert db._format_confidence_level("weird") == "weird"


# ---------------------------------------------------------------------------
# 3. Assumptions / falsifiers
# ---------------------------------------------------------------------------


class TestAssumptions:
    def test_render_assumption_includes_falsifier_and_cites(self):
        assumption = SimpleNamespace(
            assumption_he="השוק ימשיך לצמוח", falsifier_he="ירידה בתקציבי הביטחון", cites=[3]
        )
        rendered = db._render_assumption(assumption)
        assert rendered == "השוק ימשיך לצמוח — הפרכה: ירידה בתקציבי הביטחון [3]"
        # eoa.qa.d7_bd_report._FALSIFIER_KEYWORDS_HE = ("פריך", "הפרכ", "falsif") -- the rendered
        # line must actually contain one of these (docs/MODULES.md "Round 5 P6" cross-team finding).
        assert "הפרכ" in rendered

    def test_assumptions_section_renders_after_outlook_heading(self, items):
        draft = _structured_draft(
            outlook=[_fake_sentence("להערכתנו המגמה תימשך.", [])],
            assumptions=[SimpleNamespace(assumption_he="הנחה א", falsifier_he="הפרכה א", cites=[1])],
        )
        md = db.render_markdown(draft, items, [])
        assert md.index("מבט קדימה") < md.index("הנחות והפרכות")
        assert "הנחה א — הפרכה: הפרכה א [1]" in md

    def test_no_assumptions_section_when_absent(self, items):
        draft = _structured_draft()
        md = db.render_markdown(draft, items, [])
        assert "הנחות והפרכות" not in md

    def test_assumptions_render_in_docx_as_bullets(self, items):
        draft = _structured_draft(
            outlook=[_fake_sentence("להערכתנו המגמה תימשך.", [])],
            assumptions=[SimpleNamespace(assumption_he="הנחה א", falsifier_he="הפרכה א", cites=[1])],
        )
        doc = db.build_docx(draft, items, [], period_end=dt.date(2026, 9, 6))
        assert "הנחות והפרכות" in _docx_heading1_texts(doc)
        bullets = [p.text for p in doc.paragraphs if p.style is not None and p.style.name == "List Bullet"]
        assert any("הנחה א" in b and "הפרכה א" in b for b in bullets)


# ---------------------------------------------------------------------------
# 4. Deep-search blocked outcome
# ---------------------------------------------------------------------------


class TestDeepSearchBlocked:
    @pytest.fixture
    def blocked_entry(self) -> dict:
        return {
            "question": "האם המפעל בגרמניה מייצר עבור רפאל?",
            "outcome": "blocked",
            "answer_he": "",
            "confidence": 0.1,
            "blocked_reason_he": "נחסם בבדיקת אבטחה",
            "rerun_note_he": "השאלה נחקרה 2 פעמים; מוצגת הריצה הטובה ביותר.",
        }

    def test_blocked_renders_distinct_label_not_not_found_in_markdown(self, items, blocked_entry):
        draft = _structured_draft()
        md = db.render_markdown(draft, items, [], deep_search=[blocked_entry])
        assert "נחסם (לא נחקר בפועל): נחסם בבדיקת אבטחה" in md
        assert "— לא נמצא:" not in md
        assert "השאלה נחקרה 2 פעמים" in md

    def test_blocked_rerun_note_not_mistaken_for_new_entry(self, items, blocked_entry):
        """The rerun-note line must stay indented so eoa.qa.d4_investigations's `^-` bullet regex
        (docs/QA_CONTINUOUS_LOOP.md sec 6 D4) never parses it as a second investigation entry."""
        draft = _structured_draft()
        md = db.render_markdown(draft, items, [], deep_search=[blocked_entry])
        section = md.split("## חקירות עומק")[1]
        entry_lines = [ln for ln in section.splitlines() if ln.startswith("- ")]
        assert len(entry_lines) == 1

    def test_blocked_amber_class_in_html(self, items, blocked_entry):
        draft = _structured_draft()
        html_out = db.render_html(draft, items, [], deep_search=[blocked_entry])
        assert 'class="ds-blocked"' in html_out
        assert "נחסם (לא נחקר בפועל)" in html_out
        # digits render inside a <bdi> span (Hebrew/Latin bidi splitting), so check the
        # surrounding Hebrew text and the rerun-note class rather than one exact substring.
        assert 'class="ds-rerun-note"' in html_out
        assert "השאלה נחקרה" in html_out and "פעמים" in html_out

    def test_blocked_bold_and_rerun_note_italic_in_docx(self, items, blocked_entry):
        draft = _structured_draft()
        doc = db.build_docx(draft, items, [], period_end=dt.date(2026, 9, 6), deep_search=[blocked_entry])
        texts = [p.text for p in doc.paragraphs]
        assert any("נחסם (לא נחקר בפועל): נחסם בבדיקת אבטחה" in t for t in texts)
        assert any("השאלה נחקרה 2 פעמים" in t for t in texts)

    def test_found_outcome_still_renders_answer_unaffected(self, items):
        entry = {"question": "שאלה", "outcome": "found", "answer_he": "תשובה מלאה.", "confidence": 0.9}
        draft = _structured_draft()
        md = db.render_markdown(draft, items, [], deep_search=[entry])
        assert "— נמצא: תשובה מלאה." in md


# ---------------------------------------------------------------------------
# 5. Source reliability appendix column
# ---------------------------------------------------------------------------


class TestSourceReliability:
    def test_reliability_label_none_is_dash(self):
        assert db.reliability_label(None) == "—"

    def test_reliability_label_plain_string_passthrough(self):
        assert db.reliability_label("מקור ראשי מאומת") == "מקור ראשי מאומת"

    def test_reliability_label_dict_composes_kind_label_score(self):
        value = {"kind": "primary", "label": "מאומת ידנית", "score": 0.873}
        assert db.reliability_label(value) == "מקור ראשוני · מאומת ידנית · 0.87"

    def test_reliability_label_dict_secondary_score_only(self):
        assert db.reliability_label({"kind": "secondary", "score": 0.5}) == "מקור משני · 0.50"

    def test_reliability_label_empty_dict_is_dash(self):
        assert db.reliability_label({}) == "—"

    def test_appendix_header_has_reliability_column_markdown(self, items):
        draft = _structured_draft()
        md = db.render_markdown(draft, items, [])
        assert "| # | כותרת | מקור | אמינות | תאריך | קישור |" in md

    def test_appendix_row_shows_reliability_value_markdown(self, items):
        draft = _structured_draft()
        md = db.render_markdown(draft, items, [])
        appendix = md.split("## נספח מקורות")[1]
        assert "מקור ראשוני · 0.87" in appendix

    def test_appendix_row_shows_dash_when_reliability_absent(self, items):
        draft = _structured_draft()
        md = db.render_markdown(draft, items, [])
        appendix = md.split("## נספח מקורות")[1]
        first_row = [ln for ln in appendix.splitlines() if ln.strip().startswith("|")][2]
        assert "—" in first_row

    def test_appendix_header_has_reliability_column_html(self, items):
        draft = _structured_draft()
        html_out = db.render_html(draft, items, [])
        assert "<th>אמינות</th>" in html_out


# ---------------------------------------------------------------------------
# 6. Table trend cross-reference (W5)
# ---------------------------------------------------------------------------


class TestTableTrendNote:
    def test_row_cells_plain_list_unaffected(self):
        assert db._row_cells(["a", "b"]) == ["a", "b"]
        assert db._row_related_trend(["a", "b"]) is None

    def test_row_cells_dict_shape(self):
        row = {"cells": ["a", "b"], "related_trend_he": "עלייה בביקוש"}
        assert db._row_cells(row) == ["a", "b"]
        assert db._row_related_trend(row) == "עלייה בביקוש"

    def test_apply_row_trend_note_no_op_when_absent(self):
        assert db._apply_row_trend_note(["a", "b"], None) == ["a", "b"]

    def test_apply_row_trend_note_appends_to_last_cell(self):
        assert db._apply_row_trend_note(["a", "b"], "עלייה בביקוש") == ["a", "b (מגמה: עלייה בביקוש)"]

    def test_table_with_dict_rows_renders_trend_note_in_markdown(self, items):
        draft = _structured_draft()
        tables = [
            {
                "title_he": "רדאר טכנולוגי",
                "headers": ["טכנולוגיה", "סטטוס"],
                "rows": [
                    {"cells": ["LiDAR", "בשל"], "related_trend_he": "חיישנים אוטונומיים"},
                    ["Thermal", "מתפתח"],
                ],
            }
        ]
        md = db.render_markdown(draft, items, [], tables=tables)
        assert "בשל (מגמה: חיישנים אוטונומיים)" in md
        assert "| Thermal | מתפתח |" in md

    def test_table_level_related_trend_renders_as_note_in_html(self, items):
        draft = _structured_draft()
        tables = [
            {
                "title_he": "פטנטים",
                "headers": ["שם"],
                "rows": [["Patent A"]],
                "related_trend_he": "אנרגיה מכוונת",
            }
        ]
        html_out = db.render_html(draft, items, [], tables=tables)
        assert "מגמה: אנרגיה מכוונת" in html_out

    def test_dedupe_rows_across_tables_treats_dict_rows_like_list_rows(self):
        t1 = {
            "title_he": "א",
            "headers": ["x", "y"],
            "rows": [{"cells": ["Elbit [3]", "2026"], "related_trend_he": "מגמה"}],
        }
        t2 = {"title_he": "ב", "headers": ["x", "y"], "rows": [["Elbit [3]", "2026"]]}
        out = db.dedupe_rows_across_tables([t1, t2])
        assert out[0]["rows"] == t1["rows"]
        assert out[1]["rows"] == []
        # Round-14 (CR-editing.md): singular Hebrew phrasing for exactly one dropped row ("1
        # שורות כבר הופיעו" was a number/gender-agreement error -- שורה is feminine singular).
        assert "שורה אחת כבר הופיעה" in out[1]["note_he"]

    def test_plain_list_rows_dedupe_exactly_as_before(self):
        """Regression: existing plain-list-row dedupe behaviour must be unaffected by the
        dict-row support added for W5."""
        t1 = {"headers": ["a", "b"], "rows": [["x [1]", "y"]]}
        t2 = {"headers": ["a", "b"], "rows": [["x [1]", "y"], ["z [2]", "y"]]}
        out = db.dedupe_rows_across_tables([t1, t2])
        assert out[1]["rows"] == [["z [2]", "y"]]


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
