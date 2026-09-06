"""Round 6 (R6-forecast package) tests -- pure logic, no DB/LLM/network.

Covers docs/qa/loop/round_5_judge.md D6/D9 findings 1-3:
  1. tender-forecast near-duplicates caused by Hebrew gershayim/geresh vs. ASCII quote drift in
     ``platform``/``payload_need`` text splitting one real forecast across two DB rows
     (``eoa.tenders.forecast.normalize_hebrew_punctuation`` / ``dedupe_existing_forecasts`` /
     ``eoa.tenders.report_section.dedupe_forecasts_by_topic``'s window-proximity clustering).
  2. a business-events near-duplicate where one copy has every descriptive field empty
     (``eoa.report.daily._dedup_events``'s second, item/date/amount/kind-keyed pass).
  3. an exercise/deployment event mislabeled 'ניסוי' (test) -- reclassified to the existing
     'deployment' DB literal at ingestion time (``eoa.pipeline.analyze._reclassify_exercise_kind``)
     and, for rows already persisted with the stale kind, relabeled to a display-only Hebrew string
     at report time (``eoa.report.daily._sanitize_event_kind``).

Run with:
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest \
        tests/unit/test_round6_forecast.py tests/unit/test_report_daily.py -q -p no:cacheprovider
"""

from __future__ import annotations

import datetime as dt

import pytest

from eoa.llm.schemas.analysis import EventOut
from eoa.pipeline import analyze
from eoa.report import daily
from eoa.tenders import report_section
from eoa.tenders.forecast import (
    ForecastDuplicateGroup,
    PlatformSpec,
    _forecast_stable_key,
    dedupe_existing_forecasts,
    find_duplicate_forecast_groups,
    normalize_hebrew_punctuation,
)

# --------------------------------------------------------------------------
# Finding 1a: normalize_hebrew_punctuation
# --------------------------------------------------------------------------


class TestNormalizeHebrewPunctuation:
    def test_gershayim_maps_to_ascii_quote(self):
        assert normalize_hebrew_punctuation("כטב״ם MALE") == 'כטב"ם MALE'

    def test_geresh_maps_to_ascii_apostrophe(self):
        assert normalize_hebrew_punctuation("ג׳ימבלי") == "ג'ימבלי"

    def test_already_ascii_text_unchanged(self):
        assert normalize_hebrew_punctuation('כטב"ם MALE') == 'כטב"ם MALE'

    def test_none_input_returns_empty_string(self):
        assert normalize_hebrew_punctuation(None) == ""

    def test_empty_string_returns_empty_string(self):
        assert normalize_hebrew_punctuation("") == ""


class TestPlatformSpecNormalizesAtLoad:
    def test_gershayim_in_yaml_entry_normalized_to_ascii(self):
        spec = PlatformSpec(
            {
                "key": "male_uav",
                "match": ["Reaper"],
                "category_he": "כטב״ם MALE",
                "payload_need_he": "מטע״ד ג׳ימבלי",
            }
        )
        assert spec.category_he == 'כטב"ם MALE'
        assert spec.payload_need_he == "מטע\"ד ג'ימבלי"

    def test_ascii_yaml_entry_unaffected(self):
        spec = PlatformSpec(
            {
                "key": "fighter_jet",
                "match": ["F-35"],
                "category_he": "מטוס קרב",
                "payload_need_he": "פוד כיוון",
            }
        )
        assert spec.category_he == "מטוס קרב"
        assert spec.payload_need_he == "פוד כיוון"


# --------------------------------------------------------------------------
# Finding 1b: _forecast_stable_key / find_duplicate_forecast_groups
# --------------------------------------------------------------------------


def _forecast_row(**overrides):
    base = dict(
        id=1,
        platform='כטב"ם MALE',
        buyer_country="US",
        payload_need="מטע\"ד ג'ימבלי EO/IR",
        likelihood=0.6,
        window_from=dt.date(2026, 12, 6),
        window_to=dt.date(2028, 3, 6),
        rationale_he="נימוק",
        sources=["item:1"],
        created_at=dt.datetime(2026, 9, 6, 16, 48, 49),
        updated_at=dt.datetime(2026, 9, 7, 1, 10, 18),
    )
    base.update(overrides)
    return base


class TestForecastStableKey:
    def test_gershayim_and_ascii_rows_share_a_key(self):
        ascii_row = _forecast_row(platform='כטב"ם MALE', payload_need="מטע\"ד ג'ימבלי EO/IR")
        gershayim_row = _forecast_row(platform="כטב״ם MALE", payload_need="מטע״ד ג׳ימבלי EO/IR")
        assert _forecast_stable_key(ascii_row) == _forecast_stable_key(gershayim_row)

    def test_different_buyer_country_yields_different_key(self):
        a = _forecast_row(buyer_country="US")
        b = _forecast_row(buyer_country="GR")
        assert _forecast_stable_key(a) != _forecast_stable_key(b)

    def test_case_and_whitespace_insensitive(self):
        a = _forecast_row(platform="Fighter Jet", payload_need="Targeting Pod")
        b = _forecast_row(platform=" fighter jet ", payload_need=" targeting pod ")
        assert _forecast_stable_key(a) == _forecast_stable_key(b)

    def test_window_dates_are_not_part_of_the_key(self):
        a = _forecast_row(window_from=dt.date(2026, 1, 1), window_to=dt.date(2026, 6, 1))
        b = _forecast_row(window_from=dt.date(2026, 1, 2), window_to=dt.date(2026, 6, 2))
        assert _forecast_stable_key(a) == _forecast_stable_key(b)


class TestFindDuplicateForecastGroups:
    def test_no_duplicates_returns_empty(self):
        rows = [_forecast_row(id=1, platform="A"), _forecast_row(id=2, platform="B")]
        assert find_duplicate_forecast_groups(rows) == []

    def test_gershayim_ascii_pair_grouped(self):
        rows = [
            _forecast_row(id=5, platform="כטב״ם MALE", updated_at=dt.datetime(2026, 9, 6)),
            _forecast_row(id=53, platform='כטב"ם MALE', updated_at=dt.datetime(2026, 9, 7)),
        ]
        groups = find_duplicate_forecast_groups(rows)
        assert len(groups) == 1
        group = groups[0]
        assert isinstance(group, ForecastDuplicateGroup)
        assert set(group.ids) == {5, 53}
        assert group.kept_id == 53  # most recently updated
        assert group.dropped_ids == [5]

    def test_three_platforms_one_true_duplicate_pair(self):
        rows = [
            _forecast_row(id=1, platform="A", updated_at=dt.datetime(2026, 9, 5)),
            _forecast_row(id=2, platform="A", updated_at=dt.datetime(2026, 9, 6)),
            _forecast_row(id=3, platform="B", updated_at=dt.datetime(2026, 9, 6)),
        ]
        groups = find_duplicate_forecast_groups(rows)
        assert len(groups) == 1
        assert set(groups[0].ids) == {1, 2}

    def test_ties_broken_by_highest_id(self):
        same_ts = dt.datetime(2026, 9, 6, 12, 0, 0)
        rows = [
            _forecast_row(id=10, updated_at=same_ts),
            _forecast_row(id=20, updated_at=same_ts),
        ]
        groups = find_duplicate_forecast_groups(rows)
        assert groups[0].kept_id == 20


# --------------------------------------------------------------------------
# Finding 1c: dedupe_existing_forecasts (fake conn/cursor -- no real DB)
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, rows, executed):
        self._rows = rows
        self._executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self._executed.append((query, params))

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows):
        self._rows = rows
        self.executed: list = []

    def cursor(self, row_factory=None):
        return _FakeCursor(self._rows, self.executed)


class TestDedupeExistingForecasts:
    def test_dry_run_never_writes(self):
        rows = [
            _forecast_row(id=5, platform="כטב״ם MALE", sources=["item:1"]),
            _forecast_row(id=53, platform='כטב"ם MALE', sources=["item:2"]),
        ]
        conn = _FakeConnection(rows)
        groups = dedupe_existing_forecasts(conn, apply=False)
        assert len(groups) == 1
        # only the initial SELECT was executed -- no UPDATE/DELETE in dry-run mode
        assert len(conn.executed) == 1
        assert conn.executed[0][0].strip().upper().startswith("SELECT")

    def test_no_duplicates_returns_empty_and_no_writes(self):
        rows = [_forecast_row(id=1, platform="A"), _forecast_row(id=2, platform="B")]
        conn = _FakeConnection(rows)
        groups = dedupe_existing_forecasts(conn, apply=True)
        assert groups == []
        assert len(conn.executed) == 1  # just the SELECT

    def test_apply_merges_sources_and_deletes_dropped_rows(self):
        rows = [
            _forecast_row(
                id=5, platform="כטב״ם MALE", sources=["item:1"], updated_at=dt.datetime(2026, 9, 6)
            ),
            _forecast_row(
                id=53, platform='כטב"ם MALE', sources=["item:2", "item:1"], updated_at=dt.datetime(2026, 9, 7)
            ),
        ]
        conn = _FakeConnection(rows)
        groups = dedupe_existing_forecasts(conn, apply=True)
        assert len(groups) == 1
        update_calls = [(q, p) for q, p in conn.executed if q.strip().upper().startswith("UPDATE")]
        delete_calls = [(q, p) for q, p in conn.executed if q.strip().upper().startswith("DELETE")]
        assert len(update_calls) == 1
        assert len(delete_calls) == 1
        # kept row (53) sources merged with dropped row (5)'s, order-preserving + deduped
        updated_sources, updated_id = update_calls[0][1]
        assert updated_sources == ["item:2", "item:1"]
        assert updated_id == 53
        assert delete_calls[0][1] == ([5],)


# --------------------------------------------------------------------------
# Finding 1d: report_section topic-key normalization + window-proximity clustering
# --------------------------------------------------------------------------


def _report_forecast_row(**overrides):
    base = dict(
        platform='כטב"ם MALE',
        payload_need="מטע\"ד ג'ימבלי EO/IR",
        buyer_country="US",
        likelihood=0.6,
        window_from=dt.date(2026, 12, 6),
        window_to=dt.date(2028, 3, 6),
        rationale_he="נימוק",
        sources=["item:1"],
    )
    base.update(overrides)
    return base


class TestReportSectionTopicKeyNormalization:
    def test_gershayim_and_ascii_rows_share_topic_key(self):
        a = _report_forecast_row(platform='כטב"ם MALE')
        b = _report_forecast_row(platform="כטב״ם MALE")
        assert report_section._forecast_topic_key(a) == report_section._forecast_topic_key(b)

    def test_dedupe_forecasts_by_topic_collapses_punctuation_drift_pair(self):
        rows = [
            _report_forecast_row(
                platform="כטב״ם MALE",
                likelihood=0.6,
                sources=["item:1"],
                window_from=dt.date(2026, 12, 5),
                window_to=dt.date(2028, 3, 5),
            ),
            _report_forecast_row(
                platform='כטב"ם MALE',
                likelihood=0.6,
                sources=["item:2"],
                window_from=dt.date(2026, 12, 6),
                window_to=dt.date(2028, 3, 6),
            ),
        ]
        merged = report_section.dedupe_forecasts_by_topic(rows)
        assert len(merged) == 1
        assert set(merged[0]["sources"]) == {"item:1", "item:2"}

    def test_windows_within_gap_merge(self):
        rows = [
            _report_forecast_row(window_from=dt.date(2026, 1, 1), window_to=dt.date(2026, 6, 1)),
            _report_forecast_row(window_from=dt.date(2026, 1, 3), window_to=dt.date(2026, 6, 3)),
        ]
        merged = report_section.dedupe_forecasts_by_topic(rows)
        assert len(merged) == 1

    def test_windows_far_apart_kept_separate(self):
        rows = [
            _report_forecast_row(window_from=dt.date(2026, 1, 1), window_to=dt.date(2026, 6, 1)),
            _report_forecast_row(window_from=dt.date(2026, 8, 1), window_to=dt.date(2027, 1, 1)),
        ]
        merged = report_section.dedupe_forecasts_by_topic(rows)
        assert len(merged) == 2

    def test_overlapping_windows_merge_even_if_gap_check_would_not_apply(self):
        rows = [
            _report_forecast_row(window_from=dt.date(2026, 1, 1), window_to=dt.date(2026, 12, 31)),
            _report_forecast_row(window_from=dt.date(2026, 6, 1), window_to=dt.date(2027, 6, 1)),
        ]
        merged = report_section.dedupe_forecasts_by_topic(rows)
        assert len(merged) == 1

    def test_missing_window_dates_never_block_merge(self):
        rows = [
            _report_forecast_row(window_from=None, window_to=None),
            _report_forecast_row(window_from=dt.date(2026, 1, 1), window_to=dt.date(2026, 6, 1)),
        ]
        merged = report_section.dedupe_forecasts_by_topic(rows)
        assert len(merged) == 1


# --------------------------------------------------------------------------
# Finding 2: eoa.report.daily._dedup_events second pass (item/date/amount/kind)
# --------------------------------------------------------------------------


class TestDedupEventsItemAmountKindPass:
    def test_empty_fields_vs_populated_fields_same_item_date_amount_kind_merged(self):
        rows = [
            {
                "id": 1,
                "item_id": 42,
                "kind": "other",
                "date": dt.date(2026, 9, 5),
                "amount_usd": 10_000_000,
                "parties": [],
                "customer": None,
                "program": None,
            },
            {
                "id": 2,
                "item_id": 42,
                "kind": "other",
                "date": dt.date(2026, 9, 5),
                "amount_usd": 10_000_000,
                "parties": [],
                "customer": "US Air Force",
                "program": "Massed Modular Aircraft",
            },
        ]
        out = daily._dedup_events(rows)
        assert len(out) == 1
        assert out[0]["id"] == 2  # the richer row (customer+program populated) is kept

    def test_different_amount_not_merged(self):
        rows = [
            {
                "id": 1,
                "item_id": 42,
                "kind": "other",
                "date": dt.date(2026, 9, 5),
                "amount_usd": 10_000_000,
                "parties": [],
                "customer": None,
                "program": None,
            },
            {
                "id": 2,
                "item_id": 42,
                "kind": "other",
                "date": dt.date(2026, 9, 5),
                "amount_usd": 20_000_000,
                "parties": [],
                "customer": "US Air Force",
                "program": None,
            },
        ]
        out = daily._dedup_events(rows)
        assert {r["id"] for r in out} == {1, 2}

    def test_different_item_id_not_merged(self):
        rows = [
            {
                "id": 1,
                "item_id": 42,
                "kind": "other",
                "date": dt.date(2026, 9, 5),
                "amount_usd": 10_000_000,
                "parties": [],
                "customer": None,
                "program": None,
            },
            {
                "id": 2,
                "item_id": 99,
                "kind": "other",
                "date": dt.date(2026, 9, 5),
                "amount_usd": 10_000_000,
                "parties": [],
                "customer": "US Air Force",
                "program": None,
            },
        ]
        out = daily._dedup_events(rows)
        assert {r["id"] for r in out} == {1, 2}

    def test_rows_with_no_amount_pass_through_unaffected(self):
        rows = [
            {
                "id": 1,
                "item_id": 42,
                "kind": "contract_award",
                "date": dt.date(2026, 9, 5),
                "amount_usd": None,
                "parties": ["Elbit"],
                "customer": None,
                "program": None,
            },
            {
                "id": 2,
                "item_id": 42,
                "kind": "launch",
                "date": dt.date(2026, 9, 5),
                "amount_usd": None,
                "parties": ["Elbit"],
                "customer": None,
                "program": None,
            },
        ]
        out = daily._dedup_events(rows)
        assert {r["id"] for r in out} == {1, 2}

    def test_rows_with_no_item_id_pass_through_unaffected(self):
        rows = [
            {"id": 1, "kind": "other", "date": dt.date(2026, 9, 5), "amount_usd": 5, "parties": []},
            {"id": 2, "kind": "other", "date": dt.date(2026, 9, 5), "amount_usd": 5, "parties": ["A"]},
        ]
        out = daily._dedup_events(rows)
        assert {r["id"] for r in out} == {1, 2}

    def test_existing_content_key_dedup_still_applies_first(self):
        """A pair the *original* (kind+parties+customer+program) key already catches must still
        collapse to one row -- the new second pass is additive, not a regression on F9/F16."""
        rows = [
            {"id": 1, "kind": "contract_award", "parties": ["Elbit"], "customer": "USAF", "program": None},
            {
                "id": 2,
                "kind": "contract_award",
                "parties": ["elbit"],
                "customer": "usaf",
                "program": None,
                "amount_usd": 80_000_000,
            },
        ]
        out = daily._dedup_events(rows)
        assert len(out) == 1
        assert out[0]["id"] == 2


# --------------------------------------------------------------------------
# Finding 3a: eoa.pipeline.analyze._reclassify_exercise_kind (ingestion-time fix)
# --------------------------------------------------------------------------


class TestReclassifyExerciseKind:
    def test_test_kind_with_hebrew_exercise_word_reclassified_to_deployment(self):
        ev = EventOut(
            kind="test",
            title="ניסוי מערכת ה-StrikeMaster בתנאים ארקטיים",
            program="Operation Atlantic City",
            summary_he="",
            confidence=0.7,
        )
        out = analyze._reclassify_exercise_kind(ev)
        assert out.kind == "deployment"

    def test_test_kind_with_english_exercise_word_reclassified(self):
        ev = EventOut(kind="test", title="System tested during NATO exercise", summary_he="", confidence=0.7)
        out = analyze._reclassify_exercise_kind(ev)
        assert out.kind == "deployment"

    def test_test_kind_with_named_operation_reclassified(self):
        ev = EventOut(
            kind="test",
            title="Weapon fired",
            program="Operation Atlantic City",
            summary_he="",
            confidence=0.7,
        )
        out = analyze._reclassify_exercise_kind(ev)
        assert out.kind == "deployment"

    def test_genuine_test_with_no_exercise_vocabulary_unchanged(self):
        ev = EventOut(kind="test", title="ירי ניסיוני של הטיל בוצע בהצלחה", summary_he="", confidence=0.7)
        out = analyze._reclassify_exercise_kind(ev)
        assert out.kind == "test"

    def test_non_test_kind_never_touched(self):
        ev = EventOut(
            kind="deployment",
            title="תרגיל נאט״ו ארקטי",
            program="Operation Atlantic City",
            summary_he="",
            confidence=0.7,
        )
        out = analyze._reclassify_exercise_kind(ev)
        assert out is ev  # untouched, not even copied
        assert out.kind == "deployment"

    def test_reclassification_wired_into_persist_analysis_events_loop(self, monkeypatch: pytest.MonkeyPatch):
        """The loop in persist_analysis must call the reclassifier before deciding whether an event
        is a narrative title and before insert_event -- verified by capturing insert_event's kind
        kwarg without touching the DB."""
        captured: dict = {}

        def fake_insert_event(**kwargs):
            captured.update(kwargs)
            return 1

        monkeypatch.setattr(analyze, "insert_event", fake_insert_event)
        monkeypatch.setattr(analyze, "update_item_fields", lambda *a, **k: None)
        monkeypatch.setattr(analyze, "_recall_event_parties", lambda ev: list(ev.parties or []))
        monkeypatch.setattr(analyze, "normalize_amount_from_source", lambda amount, text: (amount, None))

        from eoa.llm.schemas.analysis import AnalyzeOut

        ev = EventOut(
            kind="test",
            title="ניסוי מערכת ה-StrikeMaster בתנאים ארקטיים",
            program="Operation Atlantic City",
            date="2026-09-05",  # concrete anchor -- keeps _is_narrative_event_title from rejecting it
            summary_he="",
            confidence=0.7,
        )
        out = AnalyzeOut(
            summary_he="s",
            so_what_he="sw",
            key_facts=[],
            uncertainty_he="",
            events=[ev],
            edges=[],
        )
        item = {"id": 3702, "content_status": "full"}
        analyze.persist_analysis(item, out)
        assert captured.get("kind") == "deployment"


# --------------------------------------------------------------------------
# Finding 3b: eoa.report.daily._sanitize_event_kind (render-time safety net for already-persisted
# stale rows)
# --------------------------------------------------------------------------


class TestSanitizeEventKindExercise:
    def test_test_kind_with_exercise_word_relabeled(self):
        ev = {
            "id": 174,
            "item_id": 3702,
            "kind": "test",
            "title": "ניסוי מערכת ה-StrikeMaster בתנאים ארקטיים",
            "program": "Operation Atlantic City",
        }
        out = daily._sanitize_event_kind(ev)
        assert out["kind"] == daily._EXERCISE_KIND_LABEL_HE
        assert out["kind"] == "פעילות מבצעית"

    def test_test_kind_with_named_operation_in_program_relabeled(self):
        ev = {"id": 1, "kind": "test", "title": "Weapon fired", "program": "Operation Atlantic City"}
        out = daily._sanitize_event_kind(ev)
        assert out["kind"] == "פעילות מבצעית"

    def test_test_kind_with_neither_exercise_nor_test_vocab_downgrades_to_other(self):
        """Pre-existing W5 behaviour must survive unchanged."""
        ev = {"id": 1, "kind": "test", "title": "Company announces quarterly earnings", "program": None}
        out = daily._sanitize_event_kind(ev)
        assert out["kind"] == "other"

    def test_test_kind_with_only_test_vocab_stays_test(self):
        """Pre-existing W5 behaviour must survive unchanged: genuine test vocabulary with no
        exercise framing keeps kind='test'."""
        ev = {"id": 1, "kind": "test", "title": "ניסוי טיסה בוצע בהצלחה", "program": None}
        out = daily._sanitize_event_kind(ev)
        assert out["kind"] == "test"

    def test_non_test_kind_never_touched(self):
        ev = {
            "id": 1,
            "kind": "deployment",
            "title": "תרגיל נאט״ו ארקטי",
            "program": "Operation Atlantic City",
        }
        out = daily._sanitize_event_kind(ev)
        assert out is ev

    def test_relabeled_row_survives_event_has_signal(self):
        ev = {
            "id": 174,
            "item_id": 3702,
            "kind": "test",
            "title": "ניסוי מערכת ה-StrikeMaster בתנאים ארקטיים",
            "program": "Operation Atlantic City",
            "parties": [],
            "customer": None,
            "amount_usd": None,
        }
        sanitized = daily._sanitize_event_kind(ev)
        assert daily._event_has_signal(sanitized) is True
