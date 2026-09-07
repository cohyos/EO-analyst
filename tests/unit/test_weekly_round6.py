"""Unit tests for R6-weekly (docs/qa/loop/round_6_fixes.md): grouping the weekly/monthly report's
per-item top-level headings (trends, domain sections, Israel/tech/patents/BD tables) under a
handful of parent headings so the D6 ``heading_count_within_budget`` check (weekly/monthly budget
16) passes again -- ``docs/REPORT_TEMPLATE_BENCHMARK.md`` sec 3.2 targets ~14.

Covers:
  1. ``eoa.report.docx_builder._group_entries``/``_group_title`` (pure grouping logic).
  2. ``group_he`` on ``extra_sections`` entries -- one ``##``/Heading-1 parent, each member as
     ``###``/Heading-2, in markdown/html/docx.
  3. ``group_he`` on ``tables`` entries, including a *prose* member (``body_he``, no
     ``headers``/``rows``) sharing a group with a real table.
  4. A group member whose own ``title_he`` equals the group's ``group_he`` renders directly under
     the parent with no child heading of its own (the Israel-section "merged table" case).
  5. ``domain_group_he`` wrapping ``draft.sections`` under one parent.
  6. ``open_points_in_outlook`` nesting "נקודות פתוחות" under "מבט קדימה".
  7. Every new hook defaults to off, so the unchanged daily-report call sites render exactly as
     before (regression).
  8. A synthetic weekly/monthly draft assembled the way ``eoa.report.weekly``/``monthly`` actually
     wire ``group_he``/``domain_group_he``/``open_points_in_outlook`` stays within the D6 budget
     (<= 16 ``##`` headings).

Every test builds its own minimal, duck-typed ``SimpleNamespace`` draft (same convention as
``tests/unit/test_renderer_round5.py``) and calls ``eoa.report.docx_builder`` directly -- no DB, no
LLM, no ``eoa.report.weekly``/``monthly`` collector monkeypatching needed.
"""

from __future__ import annotations

import datetime as dt
import re
from types import SimpleNamespace

from eoa.report import docx_builder as db

_H2_ONLY_RE = re.compile(r"(?m)^##(?!#)\s+\S")
_H3_ONLY_RE = re.compile(r"(?m)^###(?!#)\s+\S")


def _fake_sentence(text_he: str, cites: list[int] | None = None) -> SimpleNamespace:
    return SimpleNamespace(text_he=text_he, cites=cites or [])


def _draft(**overrides) -> SimpleNamespace:
    """A minimal duck-typed structured-draft stand-in carrying every field docx_builder might
    look at, all defaulting to empty (same convention as test_renderer_round5.py's own)."""
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


def _items() -> list[dict]:
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


def _docx_heading_texts(doc, style_name: str) -> list[str]:
    return [p.text for p in doc.paragraphs if p.style is not None and p.style.name == style_name]


def _has_h2(md: str, title: str) -> bool:
    """A markdown line that is exactly an H2 heading with this title -- a plain ``"## " + title in
    md`` substring check is unsafe here since e.g. ``"### מגמה א"`` also contains ``"## מגמה א"`` as
    a substring (one ``#`` short)."""
    return re.search(rf"(?m)^## {re.escape(title)}\s*$", md) is not None


# ---------------------------------------------------------------------------
# 1. _group_entries / _group_title -- pure grouping logic
# ---------------------------------------------------------------------------


def test_group_entries_groups_same_group_he_preserving_order():
    entries = [
        {"title_he": "A", "group_he": "G"},
        {"title_he": "B"},
        {"title_he": "C", "group_he": "G"},
    ]
    out = db._group_entries(entries)
    assert len(out) == 2
    assert isinstance(out[0], list) and [e["title_he"] for e in out[0]] == ["A", "C"]
    assert out[1] == {"title_he": "B"}


def test_group_entries_ungrouped_entries_pass_through_unchanged():
    entries = [{"title_he": "A"}, {"title_he": "B"}]
    assert db._group_entries(entries) == entries


def test_group_entries_position_filter_applies_before_grouping():
    entries = [
        {"title_he": "A", "position": "after_summary", "group_he": "G"},
        {"title_he": "B", "position": "after_outlook", "group_he": "G"},
    ]
    out = db._group_entries(entries, "after_summary")
    assert len(out) == 1
    assert isinstance(out[0], list) and len(out[0]) == 1 and out[0][0]["title_he"] == "A"


def test_group_title_for_group_uses_group_he_for_ungrouped_uses_title_he():
    grouped = [{"title_he": "A", "group_he": "G"}, {"title_he": "B", "group_he": "G"}]
    assert db._group_title(grouped) == "G"
    assert db._group_title({"title_he": "solo"}) == "solo"


# ---------------------------------------------------------------------------
# 2/4. extra_sections group_he -- markdown/html/docx
# ---------------------------------------------------------------------------


def test_extra_sections_group_renders_one_h2_with_h3_children_markdown():
    draft = _draft()
    extra = [
        {"title_he": "מגמה א", "body_he": "פרטי מגמה א.", "group_he": "מגמות השבוע"},
        {"title_he": "מגמה ב", "body_he": "פרטי מגמה ב.", "group_he": "מגמות השבוע"},
    ]
    md = db.render_markdown(draft, _items(), [], extra_sections=extra)
    assert "## מגמות השבוע" in md
    assert "### מגמה א" in md
    assert "### מגמה ב" in md
    assert not _has_h2(md, "מגמה א")  # never its own top-level heading
    assert md.count("## מגמות השבוע") == 1  # exactly one parent heading, not one per trend


def test_extra_sections_group_renders_one_h2_with_h3_children_html():
    draft = _draft()
    extra = [
        {"title_he": "מגמה א", "body_he": "פרטי מגמה א.", "group_he": "מגמות השבוע"},
        {"title_he": "מגמה ב", "body_he": "פרטי מגמה ב.", "group_he": "מגמות השבוע"},
    ]
    html_out = db.render_html(draft, _items(), [], extra_sections=extra)
    assert "מגמות השבוע</h2>" in html_out
    assert "<h3>" in html_out
    assert html_out.index("מגמות השבוע") < html_out.index("מגמה א") < html_out.index("מגמה ב")


def test_extra_sections_group_renders_heading1_then_heading2_children_docx():
    draft = _draft()
    extra = [
        {"title_he": "מגמה א", "body_he": "פרטי מגמה א.", "group_he": "מגמות השבוע"},
        {"title_he": "מגמה ב", "body_he": "פרטי מגמה ב.", "group_he": "מגמות השבוע"},
    ]
    doc = db.build_docx(draft, _items(), [], period_end=dt.date(2026, 9, 6), extra_sections=extra)
    h1 = _docx_heading_texts(doc, "Heading 1")
    h2 = _docx_heading_texts(doc, "Heading 2")
    assert "מגמות השבוע" in h1
    assert "מגמה א" not in h1  # only the group parent is a top-level heading
    assert h2 == ["מגמה א", "מגמה ב"]


def test_extra_sections_without_group_he_render_unchanged_regression():
    """No `group_he` key at all (every existing daily/weekly/monthly call site as of round 5) must
    keep rendering exactly as before -- one top-level heading per entry."""
    draft = _draft()
    extra = [{"title_he": "הקשר טריטוריאלי", "body_he": "פרטים.", "position": "after_summary"}]
    md = db.render_markdown(draft, _items(), [], extra_sections=extra)
    assert "## הקשר טריטוריאלי" in md
    assert "###" not in md


# ---------------------------------------------------------------------------
# 3/4. tables group_he, including a prose member -- markdown/html/docx
# ---------------------------------------------------------------------------


def _israel_like_tables() -> list[dict]:
    """Mirrors eoa.report.weekly's own wiring: the merged table's title_he equals the group_he (so
    it renders directly under the parent, no "###" of its own) while the per-company summary table
    gets one."""
    return [
        {
            "title_he": "תעשייה ישראלית",
            "headers": ["כותרת", "סוג"],
            "rows": [["פריט א", "זכייה"]],
            "group_he": "תעשייה ישראלית",
        },
        {
            "title_he": "סיכום שבועי לפי חברה",
            "headers": ["חברה", "אזכורים"],
            "rows": [["חברה א", 3]],
            "group_he": "תעשייה ישראלית",
        },
    ]


def test_tables_group_primary_member_has_no_h3_others_do_markdown():
    draft = _draft()
    md = db.render_markdown(draft, _items(), [], tables=_israel_like_tables())
    assert md.count("## תעשייה ישראלית") == 1
    assert "### תעשייה ישראלית" not in md
    assert "### סיכום שבועי לפי חברה" in md


def test_tables_group_primary_member_has_no_h3_others_do_html():
    draft = _draft()
    html_out = db.render_html(draft, _items(), [], tables=_israel_like_tables())
    assert html_out.count(">תעשייה ישראלית<") == 1  # only the h2, no duplicate h3
    assert "<h3>" in html_out
    assert "סיכום שבועי לפי חברה" in html_out


def test_tables_group_primary_member_has_no_h3_others_do_docx():
    draft = _draft()
    doc = db.build_docx(draft, _items(), [], period_end=dt.date(2026, 9, 6), tables=_israel_like_tables())
    h1 = _docx_heading_texts(doc, "Heading 1")
    h2 = _docx_heading_texts(doc, "Heading 2")
    assert h1.count("תעשייה ישראלית") == 1
    assert h2 == ["סיכום שבועי לפי חברה"]


def test_tables_group_prose_member_renders_as_prose_not_table_markdown():
    """A `tables`-list entry with `body_he` (no headers/rows) -- the mechanism
    eoa.report.weekly/monthly use to fold a formerly-extra_sections prose block (patents/IP,
    acquisition watch) into a table group -- renders as plain text, not an empty table."""
    draft = _draft()
    tables = [
        {"title_he": "פטנטים ו-IP", "body_he": "נמצאו 3 פטנטים חדשים.", "group_he": "טכנולוגיה ו-IP"},
        {
            "title_he": "פטנטים חדשים",
            "headers": ["שם"],
            "rows": [["פטנט א"]],
            "group_he": "טכנולוגיה ו-IP",
        },
    ]
    md = db.render_markdown(draft, _items(), [], tables=tables)
    assert "## טכנולוגיה ו-IP" in md
    assert "### פטנטים ו-IP" in md
    assert "נמצאו 3 פטנטים חדשים." in md
    assert "| שם |" in md  # the real table still renders as a table


def test_tables_group_prose_member_renders_as_prose_not_table_docx():
    draft = _draft()
    tables = [
        {"title_he": "פטנטים ו-IP", "body_he": "נמצאו 3 פטנטים חדשים.", "group_he": "טכנולוגיה ו-IP"},
    ]
    doc = db.build_docx(draft, _items(), [], period_end=dt.date(2026, 9, 6), tables=tables)
    body_texts = [p.text for p in doc.paragraphs]
    assert any("נמצאו 3 פטנטים חדשים" in t for t in body_texts)
    # the only real docx Table is the always-present sources appendix -- no empty table object is
    # created for a prose-only tables-list member.
    assert len(doc.tables) == 1


def test_tables_without_group_he_render_unchanged_regression():
    draft = _draft()
    tables = [{"title_he": "לוח 90 הימים הקרובים", "headers": ["שם"], "rows": [["כנס א"]]}]
    md = db.render_markdown(draft, _items(), [], tables=tables)
    assert "## לוח 90 הימים הקרובים" in md
    assert "###" not in md


def test_build_docx_toc_lists_one_entry_per_group_not_per_member():
    draft = _draft()
    tables = _israel_like_tables()
    doc = db.build_docx(draft, _items(), [], period_end=dt.date(2026, 9, 6), tables=tables, include_toc=True)
    toc_texts = [
        p.text
        for p in doc.paragraphs
        if p.text and "תעשייה ישראלית" in p.text and p.style.name != "Heading 1"
    ]
    # the TOC's own bookmark-link paragraph contains the group title once; the per-company table's
    # own heading is a different, unrelated string, so the group title appears at most in the TOC
    # line and the single Heading-1 (already checked in the h1 test above).
    assert len(toc_texts) <= 1


# ---------------------------------------------------------------------------
# 5. domain_group_he -- wraps draft.sections under one parent
# ---------------------------------------------------------------------------


def _sectioned_draft() -> SimpleNamespace:
    sections = [
        SimpleNamespace(title_he="אלקטרו-אופטיקה אווירית", sentences=[_fake_sentence("משפט אחד.", [1])]),
        SimpleNamespace(title_he="מעקב ימי", sentences=[_fake_sentence("משפט שני.", [2])]),
    ]
    return _draft(sections=sections)


def test_domain_group_he_wraps_sections_under_one_h2_markdown():
    md = db.render_markdown(_sectioned_draft(), _items(), [], domain_group_he="סקירה לפי תחום")
    assert "## סקירה לפי תחום" in md
    assert "### אלקטרו-אופטיקה אווירית" in md
    assert "### מעקב ימי" in md
    assert not _has_h2(md, "אלקטרו-אופטיקה אווירית")


def test_domain_group_he_wraps_sections_under_one_h2_html():
    html_out = db.render_html(_sectioned_draft(), _items(), [], domain_group_he="סקירה לפי תחום")
    assert "סקירה לפי תחום</h2>" in html_out
    assert "<h3>" in html_out


def test_domain_group_he_wraps_sections_under_heading1_then_heading2_docx():
    doc = db.build_docx(
        _sectioned_draft(),
        _items(),
        [],
        period_end=dt.date(2026, 9, 6),
        domain_group_he="סקירה לפי תחום",
    )
    h1 = _docx_heading_texts(doc, "Heading 1")
    h2 = _docx_heading_texts(doc, "Heading 2")
    assert "סקירה לפי תחום" in h1
    assert h2 == ["אלקטרו-אופטיקה אווירית", "מעקב ימי"]


def test_domain_group_he_absent_keeps_legacy_per_section_headings_regression():
    """`domain_group_he=None` (the default, every daily-report call site) must render exactly as
    before -- one top-level heading per domain section."""
    md = db.render_markdown(_sectioned_draft(), _items(), [])
    assert "## אלקטרו-אופטיקה אווירית" in md
    assert "## מעקב ימי" in md
    assert "###" not in md


# ---------------------------------------------------------------------------
# 6. open_points_in_outlook -- "נקודות פתוחות" nested under "מבט קדימה"
# ---------------------------------------------------------------------------


def _outlook_draft() -> SimpleNamespace:
    return _draft(
        outlook=[_fake_sentence("צפויה עלייה בפעילות.", [1])],
        open_points_he=["האם החוזה יאושר?"],
    )


def test_open_points_in_outlook_nests_under_outlook_heading_markdown():
    md = db.render_markdown(_outlook_draft(), _items(), [], open_points_in_outlook=True)
    assert "## מבט קדימה" in md
    assert not _has_h2(md, "נקודות פתוחות")
    assert "### נקודות פתוחות" in md
    assert md.index("מבט קדימה") < md.index("נקודות פתוחות")


def test_open_points_in_outlook_nests_under_outlook_heading_html():
    html_out = db.render_html(_outlook_draft(), _items(), [], open_points_in_outlook=True)
    assert "מבט קדימה</h2>" in html_out
    assert "<h3>" in html_out
    assert html_out.index("מבט קדימה") < html_out.index("נקודות פתוחות")


def test_open_points_in_outlook_nests_under_outlook_heading_docx():
    doc = db.build_docx(
        _outlook_draft(), _items(), [], period_end=dt.date(2026, 9, 6), open_points_in_outlook=True
    )
    h1 = _docx_heading_texts(doc, "Heading 1")
    h2 = _docx_heading_texts(doc, "Heading 2")
    assert "מבט קדימה" in h1
    assert "נקודות פתוחות" not in h1
    assert "נקודות פתוחות" in h2


def test_open_points_in_outlook_true_but_no_outlook_text_still_shows_heading():
    """Open points alone (no outlook prose) must still surface under a "מבט קדימה" heading rather
    than silently vanishing."""
    draft = _draft(open_points_he=["שאלה פתוחה."])
    md = db.render_markdown(draft, _items(), [], open_points_in_outlook=True)
    assert "## מבט קדימה" in md
    assert "### נקודות פתוחות" in md


def test_open_points_in_outlook_false_keeps_legacy_position_regression():
    """The default (`False`, every daily-report call site) keeps "נקודות פתוחות" as its own
    top-level heading, before "מבט קדימה", exactly as before."""
    md = db.render_markdown(_outlook_draft(), _items(), [])
    assert "## נקודות פתוחות" in md
    assert "### נקודות פתוחות" not in md
    assert md.index("נקודות פתוחות") < md.index("מבט קדימה")


# ---------------------------------------------------------------------------
# 7. Synthetic weekly/monthly draft -- heading budget (D6 heading_count_within_budget: <= 16)
# ---------------------------------------------------------------------------


def _synthetic_report_kwargs() -> dict:
    """Mirrors exactly how eoa.report.weekly/monthly wire group_he/domain_group_he/
    open_points_in_outlook (see those modules' own `_TRENDS_GROUP_HE`/etc. constants) -- a synthetic
    stand-in for the full weekly/monthly draft with every optional section populated (the worst
    case for heading count)."""
    draft = _draft(
        bluf=[_fake_sentence("שורה תחתונה.", [1])],
        sections=[
            SimpleNamespace(title_he=f"תחום {i}", sentences=[_fake_sentence("משפט.", [1])]) for i in range(6)
        ],
        outlook=[_fake_sentence("תחזית.", [1])],
        assumptions=[SimpleNamespace(text_he="הנחה.", cites=[1], falsifier_he="הפרכה.")],
        open_points_he=["שאלה פתוחה."],
    )
    extra_sections = [
        {"title_he": "מה השתנה מאז הדוח הקודם", "body_he": "עודכן.", "position": "after_summary"},
        *[
            {
                "title_he": f"מגמה {i}",
                "body_he": "פרטים.",
                "position": "after_summary",
                "group_he": "מגמות השבוע",
            }
            for i in range(5)
        ],
        {
            "title_he": "מעקב אינדיקטורים",
            "body_he": "חדש: פריט אחד.",
            "position": "after_outlook",
        },
        {
            "title_he": "סיכום מטא שבועי",
            "body_he": "אין תובנות.",
            "position": "after_outlook",
        },
    ]
    tables = [
        {
            "title_he": "תעשייה ישראלית",
            "headers": ["כותרת"],
            "rows": [["א"]],
            "group_he": "תעשייה ישראלית",
        },
        {
            "title_he": "סיכום שבועי לפי חברה",
            "headers": ["חברה"],
            "rows": [["א"]],
            "group_he": "תעשייה ישראלית",
        },
        {
            "title_he": "רדאר טכנולוגי",
            "headers": ["תת-תחום"],
            "rows": [["א"]],
            "group_he": "טכנולוגיה ו-IP",
        },
        {
            "title_he": "התפתחויות שכדאי לעקוב",
            "headers": ["פריט"],
            "rows": [["א"]],
            "group_he": "טכנולוגיה ו-IP",
        },
        {"title_he": "פטנטים ו-IP", "body_he": "נמצאו פטנטים.", "group_he": "טכנולוגיה ו-IP"},
        {
            "title_he": "פטנטים חדשים",
            "headers": ["שם"],
            "rows": [["א"]],
            "group_he": "טכנולוגיה ו-IP",
        },
        {"title_he": "מעקב רכישות ושותפויות", "body_he": "אין חדש.", "group_he": "פיתוח עסקי"},
        {
            "title_he": "לוח 90 הימים הקרובים",
            "headers": ["שם"],
            "rows": [["כנס א"]],
            "group_he": "פיתוח עסקי",
        },
    ]
    events = [
        {
            "date": dt.date(2026, 9, 1),
            "kind": "contract_award",
            "parties": ["A"],
            "customer": "B",
            "amount_usd": 1,
            "n": 1,
        }
    ]
    deep_search = [{"question": "שאלה?", "outcome": "found", "answer_he": "תשובה [1]."}]
    return dict(
        draft=draft,
        items=_items(),
        events=events,
        extra_sections=extra_sections,
        tables=tables,
        deep_search=deep_search,
        domain_group_he="סקירה לפי תחום",
        open_points_in_outlook=True,
    )


def test_synthetic_weekly_heading_count_within_d6_budget():
    kw = _synthetic_report_kwargs()
    md = db.render_markdown(
        kw["draft"],
        kw["items"],
        kw["events"],
        deep_search=kw["deep_search"],
        extra_sections=kw["extra_sections"],
        tables=kw["tables"],
        domain_group_he=kw["domain_group_he"],
        open_points_in_outlook=kw["open_points_in_outlook"],
    )
    count = len(_H2_ONLY_RE.findall(md))
    assert count <= 16, f"expected <= 16 H2 headings, got {count}"
    # sanity: grouping actually happened (H3 children exist), this isn't just an empty draft
    assert len(_H3_ONLY_RE.findall(md)) >= 5


def test_synthetic_monthly_heading_count_within_d6_budget():
    """Same synthetic shape, exercised through render_html/build_docx too -- all three renderers
    must agree on the same (grouped) heading structure."""
    kw = _synthetic_report_kwargs()
    html_out = db.render_html(
        kw["draft"],
        kw["items"],
        kw["events"],
        deep_search=kw["deep_search"],
        extra_sections=kw["extra_sections"],
        tables=kw["tables"],
        domain_group_he=kw["domain_group_he"],
        open_points_in_outlook=kw["open_points_in_outlook"],
    )
    h2_count = html_out.count("<h2")
    assert h2_count <= 16, f"expected <= 16 <h2> headings, got {h2_count}"

    doc = db.build_docx(
        kw["draft"],
        kw["items"],
        kw["events"],
        period_end=dt.date(2026, 9, 30),
        deep_search=kw["deep_search"],
        extra_sections=kw["extra_sections"],
        tables=kw["tables"],
        domain_group_he=kw["domain_group_he"],
        open_points_in_outlook=kw["open_points_in_outlook"],
        include_toc=True,
    )
    h1_count = len(_docx_heading_texts(doc, "Heading 1"))
    assert h1_count <= 16, f"expected <= 16 Heading-1 paragraphs, got {h1_count}"
