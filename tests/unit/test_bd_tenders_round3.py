"""Round-3 QA-loop fixes (docs/qa/loop/round_1_judge.md, D7/D9):

1. BD-report conference dates must come from the DB, deterministically (D7 finding 1).
2. Doubled ASCII quotes in Hebrew abbreviations must be normalized in rendered BD text
   (D7 finding 2, ``eoa.report.textnorm``).
3. A genuine "no activity this window" BD report must not fail ``actions_table_nonempty``
   (D7 finding 3).
4. Tenders: whole-table status re-derivation and open-first/unknown-recent ordering
   (D9 finding 4).
5. Sources: a config-disabled or renamed source's DB row must not stay ``active=true``
   forever (D9 finding 5).

Every test here is fully offline: DB access is monkeypatched to fake connection/cursor objects (no
Postgres), and no LLM/HTTP call is ever made (the no-activity-marker test relies on
``draft_bd_territory``'s own ``has_items=False`` short-circuit, which returns a deterministic draft
without ever calling ``chat_structured``).

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_bd_tenders_round3.py -q``
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

from eoa.qa.d7_bd_report import score_D7
from eoa.report import bd_territory as bdt
from eoa.report.bd_territory import NO_ACTIVITY_MARKER_HE
from eoa.report.textnorm import (
    GERESH,
    GERSHAYIM,
    ascii_apostrophe_to_geresh,
    ascii_quote_to_gershayim,
    collapse_doubled_quotes,
    normalize_hebrew_punctuation,
)

# --------------------------------------------------------------------------
# shared fake DB primitives (mirrors tests/unit/test_tenders_scan.py's _FakeCursor/_FakeConnection)
# --------------------------------------------------------------------------


class _FakeCursor:
    """A cursor whose ``fetchall()`` returns one queued result per ``execute()`` call, in order --
    needed for functions (like ``redrive_all_tender_statuses``) that run more than one statement on
    the same cursor within a single ``with`` block."""

    def __init__(self, fetchall_results=None):
        self.executed: list[tuple[str, dict | None]] = []
        self._results = list(fetchall_results) if fetchall_results is not None else [[]]

    def execute(self, query, params=None):
        self.executed.append((query, params))
        return self

    def fetchall(self):
        return self._results.pop(0) if self._results else []

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --------------------------------------------------------------------------
# 1. eoa.report.textnorm (D7 finding 2)
# --------------------------------------------------------------------------


class TestTextNorm:
    def test_collapse_doubled_quotes(self):
        assert collapse_doubled_quotes('ארה""ב') == 'ארה"ב'
        assert collapse_doubled_quotes('ארה"""ב') == 'ארה"ב'
        assert collapse_doubled_quotes('ארה"ב') == 'ארה"ב'  # single quote untouched

    def test_ascii_quote_to_gershayim_between_hebrew_letters(self):
        out = ascii_quote_to_gershayim('ארה"ב')
        assert out == f"ארה{GERSHAYIM}ב"

    def test_ascii_quote_to_gershayim_ignores_quote_not_between_hebrew(self):
        # A quote next to a Latin letter/digit or at a string edge is left alone -- only a quote
        # with Hebrew-script characters on BOTH sides is a Hebrew-abbreviation gershayim.
        assert ascii_quote_to_gershayim('say "hello"') == 'say "hello"'
        assert ascii_quote_to_gershayim('"ארהב') == '"ארהב'

    def test_ascii_apostrophe_to_geresh_after_hebrew_letter(self):
        out = ascii_apostrophe_to_geresh("בא' כוח")
        assert out == f"בא{GERESH} כוח"

    def test_ascii_apostrophe_to_geresh_ignores_apostrophe_not_after_hebrew(self):
        assert ascii_apostrophe_to_geresh("don't") == "don't"

    def test_normalize_hebrew_punctuation_full_pipeline(self):
        raw = 'שני כנסים גדולים בתחום הביטחון מתוכננים בארה""ב, מה שמצביע'
        out = normalize_hebrew_punctuation(raw)
        assert '""' not in out
        assert f"ארה{GERSHAYIM}ב" in out

    def test_normalize_hebrew_punctuation_is_idempotent(self):
        raw = 'ארה""ב וגם בא\' כוח'
        once = normalize_hebrew_punctuation(raw)
        twice = normalize_hebrew_punctuation(once)
        assert once == twice

    @pytest.mark.parametrize("value", [None, ""])
    def test_normalize_hebrew_punctuation_passthrough_falsy(self, value):
        assert normalize_hebrew_punctuation(value) == value


# --------------------------------------------------------------------------
# 2. bd_territory Hebrew-punctuation normalization applied to the whole draft/tables
#    (D7 finding 2)
# --------------------------------------------------------------------------


class TestNormalizeDraftText:
    def test_normalize_draft_text_covers_every_llm_authored_field(self):
        from eoa.llm.schemas.analysis import Sentence
        from eoa.llm.schemas.bd_territory import BdRecommendedAction, BdTerritoryReportDraft

        draft = BdTerritoryReportDraft(
            exec_summary=[Sentence(text_he='פעילות בארה""ב גוברת.', cites=[1])],
            market_bullets=[Sentence(text_he='מכרז חדש בארה""ב.', cites=[1])],
            competitor_moves=[],
            sections=[],
            recommended_actions=[
                BdRecommendedAction(
                    action_he='לפנות לגורם בארה""ב',
                    priority="H",
                    rationale=[Sentence(text_he='הזדמנות בארה""ב.', cites=[1])],
                    owner_role_he="מכירות",
                    timing_he="מיידי",
                )
            ],
            risks_assumptions_he='כיסוי חלקי בארה""ב.',
            open_points_he=['האם יש עוד מכרזים בארה""ב?'],
        )
        normalized = bdt._normalize_draft_text(draft)
        assert '""' not in normalized.exec_summary[0].text_he
        assert '""' not in normalized.market_bullets[0].text_he
        assert '""' not in normalized.recommended_actions[0].action_he
        assert '""' not in normalized.recommended_actions[0].rationale[0].text_he
        assert '""' not in normalized.risks_assumptions_he
        assert '""' not in normalized.open_points_he[0]
        assert f"ארה{GERSHAYIM}ב" in normalized.exec_summary[0].text_he

    def test_normalize_table_covers_row_cells_and_note(self):
        table = {
            "title_he": "מכרזים בטריטוריה",
            "headers": ["כותרת", "גורם מזמין"],
            "rows": [['RFI בארה""ב', "US Navy"], [123, "—"]],
            "note_he": 'הערה על ארה""ב.',
        }
        normalized = bdt._normalize_table(table)
        assert '""' not in normalized["rows"][0][0]
        assert normalized["rows"][1][0] == 123  # non-string cells pass through untouched
        assert '""' not in normalized["note_he"]

    def test_normalize_table_none_passthrough(self):
        assert bdt._normalize_table(None) is None


# --------------------------------------------------------------------------
# 3. conference-date correction (D7 finding 1)
# --------------------------------------------------------------------------


class TestConferenceDateCorrection:
    def test_conferences_date_lookup_covers_both_lists(self):
        data = {
            "territory": [{"name": "AUSA 2026", "start_date": dt.date(2026, 10, 12)}],
            "international": [{"name": "SOF Week 2027", "start_date": dt.date(2027, 5, 3)}],
        }
        lookup = bdt._conferences_date_lookup(data)
        assert lookup == {
            "AUSA 2026": dt.date(2026, 10, 12),
            "SOF Week 2027": dt.date(2027, 5, 3),
        }

    def test_correct_conference_date_mentions_fixes_mismatch(self):
        text = "AUSA 2026 יתקיים בין 2026-10-01 ל-2026-10-18 השנה."
        lookup = {"AUSA 2026": dt.date(2026, 10, 12)}
        corrected, corrections = bdt._correct_conference_date_mentions(text, lookup)
        assert "2026-10-12" in corrected
        assert "2026-10-01" not in corrected
        assert len(corrections) == 1
        assert "AUSA 2026" in corrections[0]

    def test_correct_conference_date_mentions_noop_when_matching(self):
        text = "AUSA 2026 יתקיים ב-2026-10-12."
        lookup = {"AUSA 2026": dt.date(2026, 10, 12)}
        corrected, corrections = bdt._correct_conference_date_mentions(text, lookup)
        assert corrected == text
        assert corrections == []

    def test_correct_conference_date_mentions_noop_without_lookup(self):
        text = "AUSA 2026 יתקיים ב-2026-10-01."
        corrected, corrections = bdt._correct_conference_date_mentions(text, {})
        assert corrected == text
        assert corrections == []

    def test_correct_draft_conference_dates_fixes_summary_and_bullets(self):
        from eoa.llm.schemas.analysis import Sentence
        from eoa.llm.schemas.bd_territory import BdTerritoryReportDraft

        draft = BdTerritoryReportDraft(
            exec_summary=[Sentence(text_he="AUSA 2026 (2026-10-01) הוא אירוע מרכזי.", cites=[1])],
            market_bullets=[Sentence(text_he="יש להיערך ל-AUSA 2026 שיחל ב-2026-10-01.", cites=[1])],
            competitor_moves=[],
            sections=[],
            recommended_actions=[],
            risks_assumptions_he="",
            open_points_he=[],
        )
        lookup = {"AUSA 2026": dt.date(2026, 10, 12)}
        fixed = bdt._correct_draft_conference_dates(draft, lookup, territory="US")
        assert "2026-10-12" in fixed.exec_summary[0].text_he
        assert "2026-10-12" in fixed.market_bullets[0].text_he
        assert "2026-10-01" not in fixed.exec_summary[0].text_he
        assert "2026-10-01" not in fixed.market_bullets[0].text_he


# --------------------------------------------------------------------------
# 4. no-activity marker (D7 finding 3), end-to-end through build_bd_territory with a real,
#    unmocked draft_bd_territory (has_items=False short-circuits before any LLM call)
# --------------------------------------------------------------------------


class TestNoActivityMarkerEndToEnd:
    @pytest.fixture
    def patch_all_empty(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bdt, "collect_market_items", lambda t, s, e, max_items=250: [])
        monkeypatch.setattr(bdt, "collect_platform_events", lambda t, s, e, limit=25: [])
        monkeypatch.setattr(
            bdt, "collect_tenders_and_forecasts", lambda t, limit=20: {"tenders": [], "forecasts": []}
        )
        monkeypatch.setattr(bdt, "collect_active_competitors", lambda t, ids, s, e, limit=15: [])
        monkeypatch.setattr(
            bdt,
            "collect_conferences_for_territory",
            lambda t, months=12, international_limit=5: {"territory": [], "international": []},
        )
        monkeypatch.setattr(
            bdt, "collect_dormant_watchlist_competitors", lambda t, active, limit=20: ["Shield AI", "Anduril"]
        )
        monkeypatch.setattr(bdt, "_persist_report", lambda *a, **k: 1)
        monkeypatch.setattr(bdt, "_report_path", lambda territory, period_end, ext: tmp_path / f"bd_kr.{ext}")

        def _fail_if_called(*a, **k):
            raise AssertionError("chat_structured must not be called when there are no market items")

        monkeypatch.setattr("eoa.report.bd_territory.chat_structured", _fail_if_called)
        return tmp_path

    def test_no_activity_renders_marker_with_watchlist_names(self, patch_all_empty):
        result = bdt.build_bd_territory("KR", role="resident")
        md_text = result.md.read_text(encoding="utf-8")
        assert NO_ACTIVITY_MARKER_HE in md_text
        assert "Shield AI" in md_text
        assert "Anduril" in md_text

    def test_no_activity_section_has_the_actions_heading(self, patch_all_empty):
        result = bdt.build_bd_territory("KR", role="resident")
        md_text = result.md.read_text(encoding="utf-8")
        assert "## נקודות כניסה ופעולות מומלצות" in md_text


# --------------------------------------------------------------------------
# 5. eoa.qa.d7_bd_report: the no-activity marker passes actions_table_nonempty; a truly empty
#    section, or a self-contradictory one, still fails (D7 finding 3)
# --------------------------------------------------------------------------


class TestD7ActionsTableNonempty:
    def test_no_activity_marker_counts_as_populated(self, tmp_path):
        md = tmp_path / "bd_kr_2026-09-06.md"
        md.write_text(
            "# דוח מיקוד לפיתוח עסקי\n\n"
            "## תקציר מנהלים\n\nאין ממצאים.\n\n"
            "## נקודות כניסה ופעולות מומלצות\n\n"
            f"{NO_ACTIVITY_MARKER_HE} מתחרי מעקב שנבדקו בטריטוריה זו: Shield AI; Anduril.\n",
            encoding="utf-8",
        )
        score = score_D7([md])
        check = next(c for c in score.checks if c.name == "actions_table_nonempty")
        assert check.passed, check.evidence

    def test_truly_empty_actions_section_fails(self, tmp_path):
        md = tmp_path / "bd_x_2026-09-06.md"
        md.write_text("# דוח\n\n## תקציר מנהלים\n\nתוכן כלשהו.\n", encoding="utf-8")
        score = score_D7([md])
        check = next(c for c in score.checks if c.name == "actions_table_nonempty")
        assert not check.passed

    def test_contradictory_no_activity_with_populated_table_fails(self, tmp_path):
        md = tmp_path / "bd_y_2026-09-06.md"
        md.write_text(
            "# דוח\n\n## נקודות כניסה ופעולות מומלצות\n\n"
            f"{NO_ACTIVITY_MARKER_HE} מתחרי מעקב שנבדקו בטריטוריה זו: Shield AI.\n\n"
            "| עדיפות | פעולה | נימוק | אחראי | תזמון |\n"
            "|---|---|---|---|---|\n"
            "| גבוהה | לפנות ל-X | סיבה | מכירות | מיידי |\n",
            encoding="utf-8",
        )
        score = score_D7([md])
        check = next(c for c in score.checks if c.name == "actions_table_nonempty")
        assert not check.passed

    def test_normal_populated_actions_section_still_passes(self, tmp_path):
        md = tmp_path / "bd_us_2026-09-06.md"
        md.write_text(
            "# דוח\n\n## נקודות כניסה ופעולות מומלצות\n\n"
            "| עדיפות | פעולה | נימוק | אחראי | תזמון |\n"
            "|---|---|---|---|---|\n"
            "| גבוהה | לפנות ל-X | סיבה [1] | מכירות | מיידי |\n",
            encoding="utf-8",
        )
        score = score_D7([md])
        check = next(c for c in score.checks if c.name == "actions_table_nonempty")
        assert check.passed


# --------------------------------------------------------------------------
# 6. tenders whole-table status re-derivation (D9 finding 4b)
# --------------------------------------------------------------------------


class TestRedriveAllTenderStatuses:
    def test_closes_expired_and_reopens_unknown_with_future_deadline(self, monkeypatch):
        from eoa.tenders import scan as scan_mod

        cur = _FakeCursor(fetchall_results=[[{"id": 1}], [{"id": 2}, {"id": 3}]])
        conn = _FakeConnection(cur)
        monkeypatch.setattr(scan_mod, "connection", lambda: conn)

        count = scan_mod.redrive_all_tender_statuses(dt.date(2026, 9, 6))

        assert count == 3
        close_query, close_params = cur.executed[0]
        assert "status = 'closed'" in close_query
        assert "status IN ('open', 'unknown')" in close_query
        assert "deadline < %(today)s" in close_query
        assert close_params["today"] == dt.date(2026, 9, 6)

        reopen_query, reopen_params = cur.executed[1]
        assert "status = 'open'" in reopen_query
        assert "status = 'unknown'" in reopen_query
        assert "deadline >= %(today)s" in reopen_query
        assert reopen_params["today"] == dt.date(2026, 9, 6)

    def test_defaults_today_when_not_given(self, monkeypatch):
        from eoa.tenders import scan as scan_mod

        cur = _FakeCursor(fetchall_results=[[], []])
        conn = _FakeConnection(cur)
        monkeypatch.setattr(scan_mod, "connection", lambda: conn)

        count = scan_mod.redrive_all_tender_statuses()

        assert count == 0
        _, params = cur.executed[0]
        assert params["today"] == dt.date.today()


# --------------------------------------------------------------------------
# 7. eoa.memory.relational.deactivate_orphaned_sources (D9 finding 5)
# --------------------------------------------------------------------------


class TestDeactivateOrphanedSources:
    def test_deactivates_rows_not_in_current_names(self, monkeypatch):
        from eoa.memory import relational

        cur = _FakeCursor(fetchall_results=[[{"id": 1562}, {"id": 1563}]])
        conn = _FakeConnection(cur)
        monkeypatch.setattr(relational, "connection", lambda: conn)

        count = relational.deactivate_orphaned_sources({"arXiv eess.IV (EO/IR keyword-filtered)"})

        assert count == 2
        query, params = cur.executed[0]
        assert "active = false" in query
        assert "active = true" in query
        assert params["names"] == ["arXiv eess.IV (EO/IR keyword-filtered)"]

    def test_empty_current_names_is_a_noop_and_never_touches_the_db(self, monkeypatch):
        from eoa.memory import relational

        def _boom():
            raise AssertionError("connection() must not be called with an empty name set")

        monkeypatch.setattr(relational, "connection", _boom)
        assert relational.deactivate_orphaned_sources(set()) == 0


# --------------------------------------------------------------------------
# 8. sources_loader.upsert_sources_to_db syncs active=enabled and calls the orphan sweep
#    (D9 finding 5)
# --------------------------------------------------------------------------


class TestUpsertSourcesToDbActiveSync:
    def test_active_flag_mirrors_enabled_and_orphans_are_swept(self, monkeypatch):
        from eoa.fetch import sources_loader

        src_enabled = sources_loader.Source(
            id="a", name="A", url="https://a.example", kind="rss", lang="en", reliability=3, enabled=True
        )
        src_disabled = sources_loader.Source(
            id="b", name="B", url="https://b.example", kind="rss", lang="en", reliability=3, enabled=False
        )

        upsert_calls: list[dict] = []

        def fake_upsert_source(*, name, url, kind, lang, reliability, active):
            upsert_calls.append({"name": name, "active": active})
            return {"A": 101, "B": 102}[name]

        deactivate_calls: list[set[str]] = []

        def fake_deactivate(names):
            deactivate_calls.append(set(names))
            return 0

        monkeypatch.setattr("eoa.memory.relational.upsert_source", fake_upsert_source)
        monkeypatch.setattr("eoa.memory.relational.deactivate_orphaned_sources", fake_deactivate)

        id_map = sources_loader.upsert_sources_to_db([src_enabled, src_disabled])

        assert id_map == {"a": 101, "b": 102}
        assert {c["name"]: c["active"] for c in upsert_calls} == {"A": True, "B": False}
        assert deactivate_calls == [{"A", "B"}]


# --------------------------------------------------------------------------
# 9. eoa.fetch.service.run_ingest upserts every configured source (enabled or not) but only
#    fetches the enabled ones (D9 finding 5)
# --------------------------------------------------------------------------


class TestRunIngestUpsertsAllConfiguredSources:
    async def test_disabled_source_upserted_but_not_fetched(self, monkeypatch):
        from eoa.fetch import service

        enabled_src = SimpleNamespace(id="a", name="A", enabled=True)
        disabled_src = SimpleNamespace(id="b", name="B", enabled=False)

        monkeypatch.setattr("eoa.fetch.sources_loader.load_sources", lambda: [enabled_src, disabled_src])

        captured: dict = {}

        def fake_upsert_sources_to_db(sources):
            captured["upserted_names"] = {s.name for s in sources}
            return {"a": 1, "b": 2}

        monkeypatch.setattr("eoa.fetch.sources_loader.upsert_sources_to_db", fake_upsert_sources_to_db)

        attempted: list[str] = []

        async def fake_ingest_one_source(source, *, source_db_id, since_days, throttle, stats):
            attempted.append(source.id)

        monkeypatch.setattr(service, "_ingest_one_source", fake_ingest_one_source)

        await service.run_ingest()

        # Both sources (enabled and disabled) were upserted -- this is what keeps a disabled
        # source's `active` flag in the DB in sync with config -- but only the enabled one was
        # ever actually fetched.
        assert captured["upserted_names"] == {"A", "B"}
        assert attempted == ["a"]
