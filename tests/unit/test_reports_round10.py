"""Unit tests for the R10-reports package (round-10 QA loop, closing round-9 judge worst-list
items #2/#6/#7/#9 -- docs/qa/loop/round_9_judge.md).

Every DB-touching function is monkeypatched at the module level (no live DB, no Ollama), mirroring
the conventions already used by tests/unit/test_reports_round9.py/test_reports_round8.py.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_reports_round10.py -q``
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

from eoa.report import daily, indicators
from eoa.report import product_line as pl

UTC = dt.UTC

_REPAIR_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "repair_round10.py"
_spec = importlib.util.spec_from_file_location("repair_round10", _REPAIR_SCRIPT_PATH)
repair = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(repair)


# --------------------------------------------------------------------------
# Finding #1 -- deep-search rerun reconciliation ignores citation quality
# (round-9 judge worst #2: job 146 vs job 160).
# --------------------------------------------------------------------------


def _run(job, outcome, *, confidence=None, has_low_quality_source=False, rerun_of=None, q="q1"):
    return {
        "job_id": job,
        "trigger_item_id": 44,
        "question": q,
        "outcome": outcome,
        "answer_he": f"a{job}",
        "confidence": confidence,
        "has_low_quality_source": has_low_quality_source,
        "rerun_of_job_id": rerun_of,
    }


class TestTitleLowQualitySignature:
    def test_cloudflare_challenge_title_matches(self) -> None:
        assert daily._title_is_low_quality_signature("Just a moment...") is True

    def test_cloudflare_attention_required_title_matches(self) -> None:
        assert daily._title_is_low_quality_signature("Attention Required! | Cloudflare") is True

    def test_case_insensitive_match(self) -> None:
        assert daily._title_is_low_quality_signature("JUST A MOMENT...") is True

    def test_ordinary_article_title_does_not_match(self) -> None:
        assert daily._title_is_low_quality_signature("Japan FY2027 Defense Budget Request") is False

    def test_none_title_does_not_match(self) -> None:
        assert daily._title_is_low_quality_signature(None) is False

    def test_empty_title_does_not_match(self) -> None:
        assert daily._title_is_low_quality_signature("") is False


class TestEntryHasLowQualitySource:
    def test_zero_sources_is_low_quality(self) -> None:
        entry = {"job_id": 1, "sources": []}
        assert daily._entry_has_low_quality_source(entry, {}) is True

    def test_missing_sources_key_is_low_quality(self) -> None:
        entry = {"job_id": 1}
        assert daily._entry_has_low_quality_source(entry, {}) is True

    def test_source_with_matching_title_signature_is_low_quality(self) -> None:
        # Job 146's exact live shape (docs/qa/loop/round_9_judge.md worst #2): sole source is a
        # Cloudflare interstitial the pre-round-9 code silently counted as a real read.
        url = "https://easternherald.com/2026/09/05/japan-fy2027-defense-budget-aargm-er-mq9-drones-missiles/"
        entry = {"job_id": 146, "sources": [url]}
        titles = {(146, url): "Just a moment..."}
        assert daily._entry_has_low_quality_source(entry, titles) is True

    def test_source_with_clean_title_is_not_low_quality(self) -> None:
        # Job 160's exact live shape: the clean rerun's own sole source.
        url = "https://breakingdefense.com/2026/08/japan-seeks-new-extended-range-missiles-in-record-55b-defense-budget-request/"
        entry = {"job_id": 160, "sources": [url]}
        titles = {(160, url): ""}
        assert daily._entry_has_low_quality_source(entry, titles) is False

    def test_source_with_no_investigation_log_row_is_not_flagged_on_absence_alone(self) -> None:
        entry = {"job_id": 9, "sources": ["https://example.com/a"]}
        assert daily._entry_has_low_quality_source(entry, {}) is False

    def test_any_one_bad_source_among_several_flags_the_whole_entry(self) -> None:
        entry = {"job_id": 5, "sources": ["https://clean.example/a", "https://bad.example/b"]}
        titles = {
            (5, "https://clean.example/a"): "A real headline",
            (5, "https://bad.example/b"): "403 Forbidden",
        }
        assert daily._entry_has_low_quality_source(entry, titles) is True


class TestFetchSourceTitles:
    def test_empty_job_ids_returns_empty_without_a_db_call(self, monkeypatch) -> None:
        def _boom(timeout=5):
            raise AssertionError("connection() must not be called for an empty job_ids list")

        monkeypatch.setattr(daily, "connection", _boom)
        assert daily._fetch_source_titles([]) == {}

    def test_maps_job_url_pairs_to_titles(self, monkeypatch) -> None:
        class _Cur:
            def execute(self, sql, params=None):
                return None

            def fetchall(self):
                return [{"job_id": 146, "url": "https://x/a", "title": "Just a moment..."}]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Conn:
            def cursor(self, row_factory=None):
                return _Cur()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(daily, "connection", lambda timeout=5: _Conn())
        out = daily._fetch_source_titles([146])
        assert out == {(146, "https://x/a"): "Just a moment..."}

    def test_db_failure_degrades_to_empty_map(self, monkeypatch) -> None:
        def _boom(timeout=5):
            raise RuntimeError("db down")

        monkeypatch.setattr(daily, "connection", _boom)
        assert daily._fetch_source_titles([1, 2]) == {}


class TestReconcileCitationQualityOverride:
    def test_clean_partial_rerun_beats_unclean_found_original(self) -> None:
        # The exact 146-vs-160 shape: job 146 found/0.85 but low-quality-sourced; job 160 is its
        # clean rerun, partial/0.1.
        rows = [
            _run(160, "partial", confidence=0.1, has_low_quality_source=False, rerun_of=146),
            _run(146, "found", confidence=0.85, has_low_quality_source=True),
        ]
        out = daily.reconcile_deep_search_reruns(rows)
        assert len(out) == 1
        assert out[0]["job_id"] == 160

    def test_note_flags_the_citation_quality_override_when_it_fires(self) -> None:
        rows = [
            _run(160, "partial", confidence=0.1, has_low_quality_source=False, rerun_of=146),
            _run(146, "found", confidence=0.85, has_low_quality_source=True),
        ]
        out = daily.reconcile_deep_search_reruns(rows)
        assert "חסימה" in out[0]["rerun_note_he"] or "אימות אנושי" in out[0]["rerun_note_he"]

    def test_ordinary_rerun_note_has_no_override_clause_when_it_does_not_fire(self) -> None:
        rows = [
            _run(9, "partial", has_low_quality_source=False),
            _run(8, "not_found", has_low_quality_source=False),
        ]
        out = daily.reconcile_deep_search_reruns(rows)
        assert "חסימה" not in out[0]["rerun_note_he"]

    def test_all_unclean_falls_back_to_ordinary_tier_order(self) -> None:
        # No clean alternative exists in the group -- the found/unclean run must still win, same
        # as the pre-fix behaviour (never let the override starve a group with no clean option).
        rows = [
            _run(2, "found", confidence=0.9, has_low_quality_source=True),
            _run(1, "not_found", has_low_quality_source=True),
        ]
        out = daily.reconcile_deep_search_reruns(rows)
        assert out[0]["job_id"] == 2

    def test_clean_run_below_partial_does_not_earn_the_override(self) -> None:
        # A clean `blocked` (rank 2, below the "partial" bar) must NOT beat an unclean `found`
        # (rank 4) -- the finding's own rule is "loses to any clean run with outcome >= partial".
        rows = [
            _run(2, "found", confidence=0.9, has_low_quality_source=True),
            _run(1, "blocked", has_low_quality_source=False),
        ]
        out = daily.reconcile_deep_search_reruns(rows)
        assert out[0]["job_id"] == 2

    def test_two_clean_runs_same_tier_prefer_higher_confidence(self) -> None:
        rows = [
            _run(2, "found", confidence=0.4, has_low_quality_source=False),
            _run(1, "found", confidence=0.9, has_low_quality_source=False),
        ]
        out = daily.reconcile_deep_search_reruns(rows)
        assert out[0]["job_id"] == 1

    def test_two_clean_runs_same_tier_same_confidence_prefer_newest(self) -> None:
        rows = [
            _run(9, "partial", confidence=0.5, has_low_quality_source=False),
            _run(8, "partial", confidence=0.5, has_low_quality_source=False),
        ]
        out = daily.reconcile_deep_search_reruns(rows)
        assert out[0]["job_id"] == 9

    def test_missing_has_low_quality_source_key_defaults_clean(self) -> None:
        # Backward compatibility: an entry built without this round's new field (e.g. an older
        # caller/test fixture) must not be penalised -- see test_deep_search_reconcile_round4.py's
        # own bare fixtures.
        rows = [{"job_id": 3, "trigger_item_id": None, "question": "q", "outcome": "found"}]
        out = daily.reconcile_deep_search_reruns(rows)
        assert out[0]["job_id"] == 3


# --------------------------------------------------------------------------
# Finding #2 -- product_line.py event-kind labels: single funnel, never a raw literal
# (round-9 judge worst #7: pl_targeting_pods showed raw "test" beside "ניסוי").
# --------------------------------------------------------------------------


class TestProductLineEventKindSingleFunnel:
    _ALL_KNOWN_KINDS = (
        "contract_award",
        "m_and_a",
        "partnership",
        "investment",
        "launch",
        "test",
        "deployment",
        "regulation",
        "other",
    )

    def test_event_kind_label_never_returns_a_raw_english_kind_literal(self) -> None:
        for kind in (*self._ALL_KNOWN_KINDS, "some_future_unmapped_kind", None):
            label = pl._event_kind_label(kind)
            assert label not in self._ALL_KNOWN_KINDS  # never the raw key back out
            assert isinstance(label, str) and label

    def test_test_kind_renders_as_hebrew_word_not_raw_english(self) -> None:
        assert pl._event_kind_label("test") == "ניסוי"

    def test_format_events_block_never_leaks_raw_test_literal(self) -> None:
        events = [{"n": 1, "kind": "test", "title": "a live-fire drill", "customer": "IDF", "date": None}]
        block = pl.format_events_block(events)
        assert "ניסוי" in block
        # the kind cell (text before the first " | ") must be the Hebrew label, never the raw
        # DB literal -- title text containing the substring "test" is irrelevant to this check.
        kind_cell = block.split("|", 1)[0].removeprefix("[1] ").strip()
        assert kind_cell == "ניסוי"

    def test_events_table_never_leaks_raw_test_literal(self) -> None:
        events = [{"n": 1, "kind": "test", "title": "Live-fire test", "customer": "IDF", "date": None}]
        table = pl.events_table(events)
        assert table is not None
        kind_cell = table["rows"][0][0]
        assert kind_cell == "ניסוי"

    def test_unmapped_kind_falls_back_to_other_never_raw(self) -> None:
        events = [{"n": 1, "kind": "some_future_unmapped_kind", "title": "X", "customer": "Y", "date": None}]
        table = pl.events_table(events)
        assert table["rows"][0][0] == "אחר"

    def test_missing_kind_falls_back_to_other(self) -> None:
        assert pl._event_kind_label(None) == "אחר"


# --------------------------------------------------------------------------
# Finding #3 -- daily indicator evidence: widen the match window to 7 days + events
# (round-9 judge worst #9: daily 1/8 vs weekly 17/19).
# --------------------------------------------------------------------------


class _IndConnQueue:
    """Callable standing in for `eoa.report.indicators.connection` -- returns a fresh one-shot
    fake connection/cursor per call, in the order the test supplies (mirrors
    `_fetch_recent_evidence_items` then `_fetch_recent_evidence_events`, each its own
    `with connection() as conn:` block)."""

    def __init__(self, responses: list[list[dict]]):
        self._responses = list(responses)
        self._i = 0

    def __call__(self, timeout=5):
        rows = self._responses[self._i]
        self._i += 1
        return _IndConn(rows)


class _IndConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self, row_factory=None):
        return _IndCur(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _IndCur:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        return None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestWidenDailyEvidenceCandidates:
    def test_combines_items_and_events(self, monkeypatch) -> None:
        item_row = {
            "id": 501,
            "title": "מערכת חדשה",
            "summary_he": "תקציר",
            "so_what_he": None,
            "published_at": dt.datetime(2026, 9, 3, tzinfo=UTC),
            "source_name": "src",
            "url": "https://x/1",
        }
        event_row = {
            "id": 777,
            "ev_title": "חוזה חדש",
            "program": None,
            "ev_summary_he": "תקציר אירוע",
            "item_title": "כותרת הפריט",
            "published_at": dt.datetime(2026, 9, 4, tzinfo=UTC),
            "source_name": "src2",
            "url": "https://x/2",
        }
        monkeypatch.setattr(indicators, "connection", _IndConnQueue([[item_row], [event_row]]))
        now = dt.datetime(2026, 9, 7, tzinfo=UTC)
        candidates = indicators._widen_daily_evidence_candidates(now)
        assert len(candidates) == 2
        ids = {c["id"] for c in candidates}
        assert ids == {501, 777}
        event_candidate = next(c for c in candidates if c["id"] == 777)
        assert event_candidate["title"] == "חוזה חדש"  # event's own title, not the item's

    def test_db_failure_on_either_query_degrades_to_no_candidates_for_that_query(self, monkeypatch) -> None:
        def _items_boom(timeout=5):
            raise RuntimeError("db down")

        calls = {"n": 0}

        def _connection(timeout=5):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("db down")
            return _IndConn([])

        monkeypatch.setattr(indicators, "connection", _connection)
        candidates = indicators._widen_daily_evidence_candidates(dt.datetime.now(UTC))
        assert candidates == []


class TestExtendRegistryWithCandidates:
    def test_appends_new_candidate_with_fresh_n(self) -> None:
        citation_items = [{"id": 1, "n": 1, "title": "a"}]
        candidates = [{"id": 501, "title": "b", "source_name": "s", "url": "u", "published_at": None}]
        indicators._extend_registry_with_candidates(citation_items, candidates)
        assert len(citation_items) == 2
        assert citation_items[1]["id"] == 501 and citation_items[1]["n"] == 2

    def test_idempotent_does_not_duplicate_existing_id(self) -> None:
        citation_items = [{"id": 501, "n": 3, "title": "existing"}]
        candidates = [{"id": 501, "title": "b", "source_name": "s", "url": "u", "published_at": None}]
        indicators._extend_registry_with_candidates(citation_items, candidates)
        assert len(citation_items) == 1
        assert citation_items[0]["n"] == 3  # untouched, not renumbered

    def test_empty_registry_starts_numbering_at_one(self) -> None:
        citation_items: list[dict] = []
        candidates = [{"id": 9, "title": "x", "source_name": None, "url": None, "published_at": None}]
        indicators._extend_registry_with_candidates(citation_items, candidates)
        assert citation_items[0]["n"] == 1


class TestBuildIndicatorWatchlistSectionWidensOnlyDaily:
    def test_daily_widens_evidence_and_registers_new_citation(self, monkeypatch) -> None:
        # An indicator whose only match lives outside today's `items` (id 501) but inside the
        # widened trailing-week pool -- must resolve to a real, appendix-backed [n], not "—".
        monkeypatch.setattr(
            indicators,
            "_fetch_open_indicators",
            lambda kind: [
                {
                    "id": 1,
                    "text_he": "אספקת מערכת XYZ99 לצבא צפויה בקרוב",
                    "first_seen": dt.datetime(2026, 8, 20, tzinfo=UTC),
                    "last_seen": dt.datetime(2026, 8, 20, tzinfo=UTC),
                    "status": "open",
                    "kind": "daily",
                }
            ],
        )
        monkeypatch.setattr(indicators, "_apply_maturation", lambda matured, dropped: None)
        monkeypatch.setattr(
            indicators,
            "_upsert_open",
            lambda texts, still_open, *, kind, source_report_id, now: (set(), []),
        )
        wide_candidate = {
            "id": 501,
            "title": "עדכון XYZ99",
            "summary_he": "מערכת XYZ99 סופקה",
            "so_what_he": None,
            "source_name": "src",
            "url": "https://x/1",
            "published_at": dt.datetime(2026, 9, 3, tzinfo=UTC),
        }
        monkeypatch.setattr(indicators, "_widen_daily_evidence_candidates", lambda now: [wide_candidate])

        citation_items: list[dict] = []
        section, _rows = indicators.build_indicator_watchlist_section(
            "daily",
            outlook=[],
            items=[],
            citation_items=citation_items,
            now=dt.datetime(2026, 9, 7, tzinfo=UTC),
        )
        assert section is not None
        assert any(it.get("id") == 501 for it in citation_items)  # widened candidate registered
        assert "[1]" in section["body_he"]  # resolved evidence, not "—"

    def test_weekly_never_calls_the_widening_helper(self, monkeypatch) -> None:
        def _boom(now):
            raise AssertionError("widening must not run for kind != 'daily'")

        monkeypatch.setattr(indicators, "_fetch_open_indicators", lambda kind: [])
        monkeypatch.setattr(indicators, "_apply_maturation", lambda matured, dropped: None)
        monkeypatch.setattr(
            indicators,
            "_upsert_open",
            lambda texts, still_open, *, kind, source_report_id, now: (set(), []),
        )
        monkeypatch.setattr(indicators, "_widen_daily_evidence_candidates", _boom)
        section, _rows = indicators.build_indicator_watchlist_section(
            "weekly", outlook=[], items=[], citation_items=[]
        )
        assert section is None  # no rows either way -- the point is _boom never fired


# --------------------------------------------------------------------------
# Finding #4 -- entity orphan repair (round-9 judge worst #6: 25/334 vs round 8's 16/334).
# --------------------------------------------------------------------------


class _R10FakeCursor:
    """A cursor whose `fetchall()` returns the next canned response each time `execute()` is
    called -- supports a `with connection() as conn, conn.cursor() as cur:` block that issues more
    than one query against the same cursor (repair_round10's own apply block)."""

    def __init__(self, responses: list[list[dict]]):
        self._responses = list(responses)
        self._idx = -1

    def execute(self, sql, params=None):
        self._idx += 1
        return None

    def fetchall(self):
        return list(self._responses[self._idx]) if self._idx < len(self._responses) else []

    def fetchone(self):
        rows = self._responses[self._idx] if self._idx < len(self._responses) else []
        return rows[0] if rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _R10FakeConn:
    def __init__(self, cursor: _R10FakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _R10ConnectionQueue:
    def __init__(self, cursors: list[_R10FakeCursor]):
        self._cursors = list(cursors)
        self._i = 0

    def __call__(self, timeout=None):
        cur = self._cursors[self._i]
        self._i += 1
        return _R10FakeConn(cur)


class TestWatchlistProtection:
    def test_known_watchlist_company_is_protected(self) -> None:
        names = repair._watchlist_protected_names()
        from eoa.pipeline.entity_normalize import normalize_name_key

        assert normalize_name_key("BlueHalo") in names

    def test_protection_reason_none_for_unprotected_name(self) -> None:
        row = {"id": 1, "name": "Totally Fictional Widget Co", "kind": "company", "country": None}
        reason = repair._protection_reason(row, repair._watchlist_protected_names(), "")
        assert reason is None

    def test_protection_reason_watchlist_name(self) -> None:
        row = {"id": 9, "name": "BlueHalo", "kind": "company", "country": "US"}
        reason = repair._protection_reason(row, repair._watchlist_protected_names(), "")
        assert reason == "watchlist_or_payloads_vendor"

    def test_protection_reason_referenced_by_report_state(self) -> None:
        row = {"id": 2, "name": "Totally Fictional Widget Co", "kind": "company", "country": None}
        reports_blob = "some report_state text mentioning Totally Fictional Widget Co here"
        reason = repair._protection_reason(row, set(), reports_blob)
        assert reason == "referenced_by_report_state"


class TestRepairEntityOrphansDryRun:
    def test_dry_run_never_deletes_and_protects_watchlist_rows(self, monkeypatch) -> None:
        population2 = [
            {"id": 9, "name": "BlueHalo", "kind": "company", "country": "US", "created_at": None},
            {
                "id": 9999,
                "name": "Totally Fictional Widget Co",
                "kind": "company",
                "country": None,
                "created_at": None,
            },
        ]
        queue = _R10ConnectionQueue(
            [
                _R10FakeCursor([population2]),  # find_zero_mention_entities
                _R10FakeCursor([population2]),  # find_fully_orphaned_entities
                _R10FakeCursor([[]]),  # list_new_orphans_since_round6
                _R10FakeCursor([[]]),  # _reports_report_state_text
                _R10FakeCursor([[]]),  # _trace_creation_source for the unprotected candidate
            ]
        )
        monkeypatch.setattr(repair, "connection", queue)
        report = repair.repair_entity_orphans(apply=False)
        assert report["protected_count"] == 1
        assert report["to_delete_count"] == 1
        assert report["deleted_ids"] == []  # dry run never writes
        assert report["applied"] is False


class TestRepairEntityOrphansApply:
    def test_apply_deletes_only_the_unprotected_row_and_strips_patents_reference(self, monkeypatch) -> None:
        population2 = [
            {"id": 9, "name": "BlueHalo", "kind": "company", "country": "US", "created_at": None},
            {
                "id": 9999,
                "name": "Totally Fictional Widget Co",
                "kind": "company",
                "country": None,
                "created_at": None,
            },
        ]
        queue = _R10ConnectionQueue(
            [
                _R10FakeCursor([population2]),  # find_zero_mention_entities
                _R10FakeCursor([population2]),  # find_fully_orphaned_entities
                _R10FakeCursor([[]]),  # list_new_orphans_since_round6
                _R10FakeCursor([[]]),  # _reports_report_state_text
                _R10FakeCursor([[]]),  # _trace_creation_source for 9999
                _R10FakeCursor(
                    [
                        [{"id": 42, "entity_ids": [9999, 5]}],  # patents SELECT: one soft-ref hit
                        None,  # patents UPDATE (fetchall not called)
                        [{"id": 9999}],  # DELETE ... RETURNING id
                    ]
                ),
            ]
        )
        monkeypatch.setattr(repair, "connection", queue)
        report = repair.repair_entity_orphans(apply=True)
        assert report["deleted_ids"] == [9999]
        assert report["patents_touched"] == 1
        assert report["protected_count"] == 1  # BlueHalo untouched

    def test_no_unprotected_candidates_means_zero_deletions_and_no_apply_connection(
        self, monkeypatch
    ) -> None:
        # Live-verified round-10 shape: all 16 population-2 rows are watchlist names -- the apply
        # block's own connection() call must never fire when there is nothing to delete.
        population2 = [{"id": 9, "name": "BlueHalo", "kind": "company", "country": "US", "created_at": None}]
        queue = _R10ConnectionQueue(
            [
                _R10FakeCursor([population2]),
                _R10FakeCursor([population2]),
                _R10FakeCursor([[]]),
                _R10FakeCursor([[]]),
            ]
        )
        monkeypatch.setattr(repair, "connection", queue)
        report = repair.repair_entity_orphans(apply=True)
        assert report["deleted_ids"] == []
        assert report["to_delete_count"] == 0
