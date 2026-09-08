"""Unit tests for the content-review editing pass (Round-14, docs/qa/content_review/CR-editing.md).

Covers the deterministic renderer/builder fixes made in response to the user's line-by-line
Hebrew copy-edit of the daily/weekly/monthly/bd_il/bd_us reports: bidi-isolate and stray
backslash-quote leakage in investigation text (``eoa.report.textnorm``), the "[item N]"/raw
taxonomy-slug/mid-word-truncation table defects (``eoa.report.daily``/``docx_builder``), the
"N שורות כבר הופיעו" number-agreement bug, the >6-column BD-territory table, and the safe
exact-duplicate-sentence dedupe (``eoa.report.style``).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_report_editing_round14.py -q``
"""

from __future__ import annotations

import re
from typing import Any

from eoa.llm.schemas.analysis import DailyReportDraft, OutlookIndicator, Sentence, StructuredSection
from eoa.llm.schemas.reports import WeeklyReportDraft, WeeklyTrendSection
from eoa.report import bd_territory, daily, deltas, docx_builder, style, trends, weekly
from eoa.report.textnorm import (
    collapse_space_before_closing_punctuation,
    normalize_hebrew_punctuation,
    strip_bidi_isolates,
    trim_at_word_boundary,
    unescape_stray_backslash_quotes,
)

LRI = "⁦"
PDI = "⁩"

# --------------------------------------------------------------------------
# textnorm.py: bidi-isolate stripping, stray backslash-quote unescape, punctuation-space collapse
# --------------------------------------------------------------------------


def test_strip_bidi_isolates_removes_lri_pdi_pair():
    text = f"מ-{LRI}76%{PDI} ל-{LRI}90%{PDI}"
    assert strip_bidi_isolates(text) == "מ-76% ל-90%"


def test_strip_bidi_isolates_noop_when_absent():
    assert strip_bidi_isolates("טקסט רגיל בלי סימוני בידי") == "טקסט רגיל בלי סימוני בידי"


def test_unescape_stray_backslash_quotes_fixes_hebrew_acronym():
    # The exact live defect: כטב\"מים instead of כטב"מים (later gershayim-normalized).
    assert unescape_stray_backslash_quotes('לנטרול כטב\\"מים') == 'לנטרול כטב"מים'


def test_unescape_stray_backslash_quotes_handles_apostrophe_too():
    assert unescape_stray_backslash_quotes("מנכ\\'ל") == "מנכ'ל"


def test_collapse_space_before_closing_punctuation():
    assert collapse_space_before_closing_punctuation("כטב\"מים [1, 5, 6] .") == 'כטב"מים [1, 5, 6].'


def test_normalize_hebrew_punctuation_full_pipeline_matches_live_defect():
    """The exact shape seen live in daily_2026-09-07.md: an escaped Hebrew acronym quote, bidi
    isolates around every number, and a stray space before the sentence-final period."""
    raw = f'לנטרול כטב\\"מים [{LRI}1, 5, 6{PDI}] .'
    cleaned = normalize_hebrew_punctuation(raw)
    assert LRI not in cleaned and PDI not in cleaned
    assert "\\" not in cleaned
    assert cleaned == 'לנטרול כטב״מים [1, 5, 6].'


def test_normalize_hebrew_punctuation_none_and_empty_are_safe():
    assert normalize_hebrew_punctuation(None) is None
    assert normalize_hebrew_punctuation("") == ""


def test_trim_at_word_boundary_never_cuts_mid_word():
    text = " ".join(["מילה"] * 60)
    trimmed = trim_at_word_boundary(text, 200, suffix=" …")
    body = trimmed[: -len(" …")]
    assert not text.startswith(body + "מ")  # would only happen if the cut fell mid-word
    assert body.split(" ")[-1] == "מילה"  # last kept token is a whole word


def test_trim_at_word_boundary_short_text_untouched():
    assert trim_at_word_boundary("קצר", 200) == "קצר"


def test_trim_at_word_boundary_no_spaces_falls_back_to_hard_cut():
    text = "א" * 250
    trimmed = trim_at_word_boundary(text, 200, suffix="…")
    assert trimmed == "א" * 200 + "…"


# --------------------------------------------------------------------------
# eoa.report.daily: deep-search text normalization + tenders-forecast table cleanup
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def execute(self, sql, params=None):
        pass

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_collect_deep_search_normalizes_answer_text(monkeypatch):
    row = {
        "job_id": 1,
        "payload": {"question": "מה קרה?"},
        "result": {
            "outcome": "found",
            "answer_he": f'לנטרול כטב\\"מים [{LRI}1, 5, 6{PDI}] .',
            "confidence": 0.8,
            "sources": [],
            "key_facts": [f"ב-{LRI}1{PDI} ביוני 2026, DAPA הודיעה."],
            "contradictions_he": 'אין סתירות בין ⁦המקורות⁩.'.replace("⁦", LRI).replace("⁩", PDI),
        },
        "state": "done",
        "finished_at": None,
        "trigger_item_id": None,
        "trigger_title": None,
        "trigger_url": None,
        "trigger_item_dedup_of": None,
    }
    monkeypatch.setattr(daily, "connection", lambda *a, **k: _FakeConn(_FakeCursor([row])))
    monkeypatch.setattr(daily, "_fetch_source_titles", lambda job_ids: {})
    monkeypatch.setattr(daily, "reconcile_deep_search_reruns", lambda out: out)

    out = daily.collect_deep_search()
    assert len(out) == 1
    entry = out[0]
    assert LRI not in entry["answer_he"] and PDI not in entry["answer_he"]
    assert "\\" not in entry["answer_he"]
    assert entry["answer_he"] == 'לנטרול כטב״מים [1, 5, 6].'
    assert LRI not in entry["key_facts"][0]
    assert LRI not in entry["contradictions_he"] and PDI not in entry["contradictions_he"]


def test_tenders_forecast_table_strips_internal_item_markers():
    data = {
        "new_forecasts": [
            {
                "platform": "מטוס קרב",
                "payload_need": "פוד כיוון",
                "likelihood": 0.6,
                "window_from": None,
                "window_to": None,
                "rationale_he": "המאמר ([item 12]) מצביע על כך שהמטוסים [item 47] יזדקקו לפוד חדש.",
            }
        ]
    }
    tbl = daily._tenders_forecast_table(data, [])
    row = tbl["rows"][0]
    assert "[item" not in row[4]
    assert "( )" not in row[4]
    assert "  " not in row[4]


def test_tenders_forecast_table_word_boundary_trim_with_pointer():
    long_rationale = " ".join(["מילה"] * 60)
    data = {
        "new_forecasts": [
            {
                "platform": "MQ-9 Reaper",
                "payload_need": "EO/IR gimbal upgrade",
                "likelihood": 0.72,
                "window_from": None,
                "window_to": None,
                "rationale_he": long_rationale,
            }
        ]
    }
    tbl = daily._tenders_forecast_table(data, [])
    row = tbl["rows"][0]
    assert len(row[4]) < len(long_rationale)
    assert "פירוט במקורות" in row[4]
    assert row[4].split(" … ")[0].split(" ")[-1] == "מילה"  # cut lands on a whole word


# --------------------------------------------------------------------------
# _domain_label / _subdomain_label: never leak a raw English taxonomy slug into a Hebrew heading
# --------------------------------------------------------------------------


def test_weekly_domain_label_never_leaks_out_of_scope():
    label = weekly._domain_label("out_of_scope")
    assert label != "out_of_scope"
    assert label == weekly._UNKNOWN_DOMAIN_LABEL_HE


def test_trends_domain_label_never_leaks_out_of_scope():
    label = trends._domain_label("out_of_scope")
    assert label != "out_of_scope"


def test_trends_subdomain_label_never_leaks_raw_key():
    label = trends._subdomain_label("not_a_real_subdomain_xyz")
    assert label != "not_a_real_subdomain_xyz"


def test_bd_territory_domain_label_never_leaks_out_of_scope():
    label = bd_territory._domain_label("out_of_scope")
    assert label != "out_of_scope"


def test_label_raw_subdomain_keys_repairs_a_persisted_raw_domain_slug():
    """Round-14 (CR-editing.md): a "vanished"/"strengthened"/"weakened" trend delta re-displays a
    *previous* report's already-persisted title_he verbatim -- so even after `trends._domain_label`
    stopped leaking a raw slug into a *newly generated* title, an old title stored before the fix
    (e.g. "...בתחום out_of_scope") still needs repair at render time. Confirmed live: the rebuilt
    weekly_2026-09-07.md still read "בתחום out_of_scope" 23 times post-fix until this repair
    function was extended to also handle the unquoted domain-slug case (it already handled the
    quoted subdomain-slug case)."""
    title = "מגמה: פעילות מוגברת סביב Europe בתחום out_of_scope"
    repaired = deltas._label_raw_subdomain_keys(title)
    assert "out_of_scope" not in repaired
    assert "Europe" in repaired  # only the slug is replaced, not the rest of the title


def test_label_raw_subdomain_keys_leaves_an_already_translated_title_alone():
    title = "מגמה: פעילות מוגברת סביב Elbit בתחום פודים ומטע\"דים אוויריים (Airborne Pods & Payloads)"
    assert deltas._label_raw_subdomain_keys(title) == title


def test_domain_label_still_resolves_a_real_domain():
    label = weekly._domain_label("c_uas")
    assert label != "c_uas"
    assert "C-UAS" in label or "כטב" in label


# --------------------------------------------------------------------------
# bd_territory.platform_events_table: <=6 columns (was 7: separate רוכש/ספק columns)
# --------------------------------------------------------------------------


def test_platform_events_table_has_at_most_six_columns():
    events = [
        {
            "date": None,
            "published_at": None,
            "platform_he": "מטוס קרב",
            "buyer": "Greece",
            "vendor": "Rafael",
            "amount_usd": 1000,
            "currency": "USD",
            "payload_need_he": "פוד כיוון",
            "n": 1,
        }
    ]
    tbl = bd_territory.platform_events_table(events)
    assert len(tbl["headers"]) <= 6
    assert len(tbl["rows"][0]) == len(tbl["headers"])
    assert "Greece" in tbl["rows"][0][2] and "Rafael" in tbl["rows"][0][2]


def test_platform_events_table_collapses_identical_buyer_vendor():
    events = [
        {
            "date": None,
            "published_at": None,
            "platform_he": "—",
            "buyer": "Israel",
            "vendor": "Israel",
            "amount_usd": None,
            "currency": None,
            "payload_need_he": None,
            "n": None,
        }
    ]
    tbl = bd_territory.platform_events_table(events)
    assert tbl["rows"][0][2] == "Israel"  # not "Israel / Israel"


# --------------------------------------------------------------------------
# docx_builder: cell trimming, table captions, singular/plural dedupe note
# --------------------------------------------------------------------------


def _minimal_daily_draft() -> DailyReportDraft:
    return DailyReportDraft(
        exec_summary=[Sentence(text_he="תקציר.", cites=[1])],
        sections=[],
        outlook=[],
    )


def test_md_cell_escapes_literal_pipe_so_it_cannot_split_the_row():
    """Round-14 (CR-editing.md): a scraped page title of the common "Headline | Site Name" shape
    (confirmed live in bd_il_2026-09-07.md's sources appendix) must never be able to split one
    markdown table cell into two, corrupting the whole row's column alignment."""
    cell = docx_builder._md_cell("Article Headline | Site Name")
    assert cell == "Article Headline \\| Site Name"
    assert len(re.findall(r"(?<!\\)\|", cell)) == 0  # no *unescaped* pipe left


def test_escape_md_table_cell_handles_none_and_pipe_and_newline():
    assert docx_builder._escape_md_table_cell("") == ""
    assert docx_builder._escape_md_table_cell("a|b") == "a\\|b"
    assert "\n" not in docx_builder._escape_md_table_cell("a\nb")


def test_render_markdown_sources_appendix_survives_pipe_in_title(monkeypatch):
    """End-to-end: a title containing "|" must not add an extra "|"-delimited column to the
    rendered appendix row."""
    draft = _minimal_daily_draft()
    items = [
        {
            "id": 1,
            "n": 1,
            "title": "Article Headline | Site Name",
            "source_name": "Example Source",
            "url": "https://example.com/a",
            "published_at": None,
        }
    ]
    md = docx_builder.render_markdown(draft, items, [])
    appendix_rows = [ln for ln in md.splitlines() if ln.startswith("| <a id=\"src-1\"")]
    assert len(appendix_rows) == 1
    row = appendix_rows[0]
    assert "Article Headline \\| Site Name" in row  # the title's own "|" is escaped, not bare
    # Exactly 6 columns (7 *unescaped* pipes) -- the title's own "|" (preceded by "\\") must not
    # count as a column separator.
    unescaped_pipes = len(re.findall(r"(?<!\\)\|", row))
    assert unescaped_pipes == 7


def test_trim_cell_text_word_boundary_and_url_passthrough():
    long_text = " ".join(["מילה"] * 60)
    trimmed = docx_builder._trim_cell_text(long_text)
    assert len(trimmed) < len(long_text)
    assert "פירוט במקורות" in trimmed

    url = "https://example.com/" + "a" * 300
    assert docx_builder._trim_cell_text(url) == url  # URLs are never trimmed (rendered as links)

    assert docx_builder._trim_cell_text(42) == 42  # non-strings pass through untouched


def test_table_caption_uses_note_he_when_present():
    tbl = {"note_he": "הערה קיימת.", "rows": [[1], [2]]}
    assert docx_builder._table_caption_text(tbl) == "הערה קיימת."


def test_table_caption_synthesizes_row_count_when_missing():
    # Round-15 (PL-REPORT-FIX): >3 rows so the caption actually renders (see the "small table"
    # test below) -- and with no wrapping parentheses (they mirror in RTL, see docx_builder's own
    # updated docstring).
    tbl = {"rows": [[1], [2], [3], [4], [5]]}
    assert docx_builder._table_caption_text(tbl) == "5 שורות"


def test_table_caption_none_for_empty_table():
    assert docx_builder._table_caption_text({"rows": []}) is None


def test_table_caption_none_for_three_or_fewer_rows():
    # Round-15 (PL-REPORT-FIX, user screenshot 2026-09-08 20:30): a synthesized row-count caption
    # is dropped entirely for a table this small -- confirmed live: pl_targeting_pods_2026-09-08's
    # "מפת קונים / צינור הזדמנויות" table rendered a mangled "*(3 שורות)*" caption under exactly 3
    # rows.
    assert docx_builder._table_caption_text({"rows": [[1], [2], [3]]}) is None
    assert docx_builder._table_caption_text({"rows": [[1]]}) is None


def test_table_caption_note_he_still_renders_for_a_small_table():
    # An author-provided note_he is real content (not a bare row count), so it still renders even
    # under the <=3-row synthesis threshold.
    tbl = {"note_he": "הערה קיימת.", "rows": [[1], [2]]}
    assert docx_builder._table_caption_text(tbl) == "הערה קיימת."


def test_dedupe_rows_across_tables_singular_note_for_one_dropped_row():
    tables = [
        {"title_he": "א", "headers": ["x", "y"], "rows": [["a", "b"]]},
        {"title_he": "ב", "headers": ["x", "y"], "rows": [["a", "b"], ["c", "d"]]},
    ]
    out = docx_builder.dedupe_rows_across_tables(tables)
    second = out[1]
    assert second["rows"] == [["c", "d"]]
    assert "שורה אחת כבר הופיעה" in second["note_he"]
    assert "1 שורות" not in second["note_he"]


def test_dedupe_rows_across_tables_plural_note_for_multiple_dropped_rows():
    tables = [
        {"title_he": "א", "headers": ["x", "y"], "rows": [["a", "b"], ["c", "d"]]},
        {"title_he": "ב", "headers": ["x", "y"], "rows": [["a", "b"], ["c", "d"], ["e", "f"]]},
    ]
    out = docx_builder.dedupe_rows_across_tables(tables)
    second = out[1]
    assert second["rows"] == [["e", "f"]]
    assert "2 שורות כבר הופיעו" in second["note_he"]


# --------------------------------------------------------------------------
# eoa.report.style: safe exact-duplicate-sentence dedupe (never rewrites, never empties a section)
# --------------------------------------------------------------------------


def _weekly_draft_with_duplicate_sentence() -> WeeklyReportDraft:
    dup = Sentence(text_he="אלביט מערכות זכתה בחוזה של 50 מיליון דולר.", cites=[1])
    return WeeklyReportDraft(
        exec_summary=[dup, Sentence(text_he="רפאל השיקה מערכת נגד כטבמים חדשה.", cites=[2])],
        trends=[
            WeeklyTrendSection(
                title_he="מגמה: פעילות מוגברת סביב Elbit Systems",
                sentences=[Sentence(text_he="אלביט מערכות זכתה בחוזה של 50 מיליון דולר.", cites=[1])],
            ),
        ],
        sections=[
            StructuredSection(
                title_he="פודים ומטענים אוויריים",
                domain="airborne_pods",
                sentences=[Sentence(text_he="אלביט מערכות זכתה בחוזה של 50 מיליון דולר.", cites=[1])],
            ),
        ],
        outlook=[OutlookIndicator(text_he="להערכתנו מגמת ההשקות תימשך.", cites=[], is_assessment=True)],
        open_points_he=[],
    )


def test_dedupe_exact_sentences_protects_single_sentence_collections_from_emptying():
    """Round-14 design choice: a section/trend whose *only* sentence exactly duplicates one kept
    earlier is still kept in place (never dropped down to zero sentences) -- dropping it would
    trade a "repeated sentence" defect for an "empty section" defect, which this project's own
    style-guard docstring explicitly treats as the worse outcome (CONVENTIONS.md rule 4: never a
    silent rewrite that could leave a heading with nothing under it)."""
    draft = _weekly_draft_with_duplicate_sentence()
    updated, n_dropped = style.dedupe_exact_sentences_across_sections(draft)
    assert len(updated.exec_summary) == 2
    assert updated.exec_summary[0].text_he == "אלביט מערכות זכתה בחוזה של 50 מיליון דולר."
    assert n_dropped == 0
    assert len(updated.trends[0].sentences) == 1
    assert len(updated.sections[0].sentences) == 1


def test_dedupe_exact_sentences_drops_repeat_when_its_collection_has_other_content():
    """The case that *does* drop something: a collection with a real, non-duplicate sentence
    alongside the exact repeat -- dropping the repeat here never risks an empty section."""
    dup_text = "אלביט מערכות זכתה בחוזה של 50 מיליון דולר."
    draft = WeeklyReportDraft(
        exec_summary=[Sentence(text_he=dup_text, cites=[1])],
        trends=[
            WeeklyTrendSection(
                title_he="מגמה: פעילות מוגברת סביב Elbit Systems",
                sentences=[
                    Sentence(text_he=dup_text, cites=[1]),
                    Sentence(text_he="נרשמה עלייה בפעילות סביב אלביט בתחום הפודים האוויריים.", cites=[1]),
                ],
            ),
        ],
        sections=[],
        outlook=[],
        open_points_he=[],
    )
    updated, n_dropped = style.dedupe_exact_sentences_across_sections(draft)
    assert n_dropped == 1
    assert len(updated.trends[0].sentences) == 1
    assert updated.trends[0].sentences[0].text_he != dup_text


def test_dedupe_exact_sentences_never_empties_a_section_when_all_its_sentences_repeat():
    # Same fixture, but exec_summary is empty this time -- the section's own single (repeated)
    # sentence is the *first* occurrence encountered overall for that normalized text, so it must
    # be kept rather than dropped down to zero sentences.
    section = StructuredSection(
        title_he="פודים ומטענים אוויריים",
        domain="airborne_pods",
        sentences=[
            Sentence(text_he="אלביט מערכות זכתה בחוזה של 50 מיליון דולר.", cites=[1]),
            Sentence(text_he="אלביט מערכות זכתה בחוזה של 50 מיליון דולר.", cites=[1]),
        ],
    )
    draft = WeeklyReportDraft(exec_summary=[], trends=[], sections=[section], outlook=[], open_points_he=[])
    updated, n_dropped = style.dedupe_exact_sentences_across_sections(draft)
    assert len(updated.sections[0].sentences) == 1  # first kept, exact repeat within it dropped
    assert n_dropped == 1


def test_dedupe_exact_sentences_is_a_noop_when_nothing_repeats():
    draft = WeeklyReportDraft(
        exec_summary=[Sentence(text_he="משפט ראשון.", cites=[1])],
        trends=[],
        sections=[
            StructuredSection(
                title_he="x", domain="airborne_pods", sentences=[Sentence(text_he="משפט שני.", cites=[2])]
            )
        ],
        outlook=[],
        open_points_he=[],
    )
    updated, n_dropped = style.dedupe_exact_sentences_across_sections(draft)
    assert n_dropped == 0
    assert [s.text_he for s in updated.exec_summary] == [s.text_he for s in draft.exec_summary]
    assert [s.text_he for s in updated.sections[0].sentences] == [s.text_he for s in draft.sections[0].sentences]


# --------------------------------------------------------------------------
# product_line.py: fallback sentence trim is word-boundary safe (was a bare text[:200] slice)
# --------------------------------------------------------------------------


def test_product_line_fallback_top_item_sentences_word_boundary_trim():
    from eoa.report import product_line

    long_text = " ".join(["מילה"] * 60)
    items: list[dict[str, Any]] = [{"so_what_he": long_text, "n": 1}]
    sentences = product_line._fallback_top_item_sentences(items, limit=5)
    assert len(sentences) == 1
    text = sentences[0].text_he
    assert len(text) <= 200 or text.split(" ")[-1] not in ("מיל", "מי", "מ")  # never a partial word
