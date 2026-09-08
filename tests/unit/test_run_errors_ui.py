"""Tests for the Morning "שגיאות בריצה האחרונה" panel's backing data (UI-ERRORS,
docs/qa/content_review/UI-ERRORS.md).

Two layers:

1. Recording (`eoa.orchestrator.jobs._run_stage`): a stage exception must persist a real
   `error_type`, a non-degenerate `message` (never a bare `str(exc)` like "0" for
   `KeyError(0)`), and a `traceback_tail` of file:line:function frames.
2. Reading (`eoa.api.services.recent_errors`/`_classify_error`): every entry must carry a
   deterministic Hebrew cause/action/impact classification, and legacy `run_log` rows written
   before `error_type`/`traceback_tail` existed (e.g. error 279, 2026-09-08 01:31:51, tenders
   stage -- see the module docstring reference in services.py) must be reconstructed from the
   stored raw traceback text, not show a useless message.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_run_errors_ui.py -q``
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from eoa.api import services
from eoa.orchestrator import jobs

# --------------------------------------------------------------------------------------------
# Recording: eoa.orchestrator.jobs._run_stage
# --------------------------------------------------------------------------------------------


class TestRunStageErrorRecording:
    def _run_and_capture(self, monkeypatch: pytest.MonkeyPatch, fn) -> dict[str, Any]:
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            jobs,
            "heartbeat",
            lambda job_id, stage, event, detail=None, **kw: calls.append(
                {"job_id": job_id, "stage": stage, "event": event, "detail": detail}
            ),
        )
        rs = jobs.RunState(job_id=1)
        jobs._run_stage(rs, "tenders", fn)
        error_calls = [c for c in calls if c["event"] == "error"]
        assert error_calls, "expected exactly one 'error' heartbeat"
        return error_calls[-1]["detail"]

    def test_keyerror_with_int_arg_gets_a_real_message_and_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reproduces error 279: `KeyError(0)` -- `str(exc)` is just "0"."""

        def boom() -> None:
            raise KeyError(0)

        detail = self._run_and_capture(monkeypatch, boom)
        assert detail["error_type"] == "KeyError"
        assert detail["error"] != "0"
        assert "KeyError" in detail["error"]

    def test_ordinary_exception_message_is_kept_verbatim(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom() -> None:
            raise ValueError("bad payload shape")

        detail = self._run_and_capture(monkeypatch, boom)
        assert detail["error_type"] == "ValueError"
        assert detail["error"] == "bad payload shape"

    def test_traceback_tail_is_file_line_function_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def inner() -> None:
            raise RuntimeError("nested failure")

        def outer() -> None:
            inner()

        detail = self._run_and_capture(monkeypatch, outer)
        tail = detail["traceback_tail"]
        assert isinstance(tail, list) and tail
        assert all(":" in frame for frame in tail)
        # file:line:function -- exactly two colons, no local-variable payload.
        assert all(frame.count(":") == 2 for frame in tail)
        assert any("inner" in frame for frame in tail)

    def test_failures_counter_increments_and_run_continues(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A non-mandatory stage's exception is swallowed -- the pipeline keeps going, which is
        exactly what `impact_he` reports back to the reader."""
        monkeypatch.setattr(jobs, "heartbeat", lambda *a, **kw: None)
        rs = jobs.RunState(job_id=1)
        result = jobs._run_stage(rs, "tenders", lambda: (_ for _ in ()).throw(KeyError(0)))
        assert result is None
        assert rs.failures["tenders"] == 1


# --------------------------------------------------------------------------------------------
# Reading: eoa.api.services._classify_error / recent_errors
# --------------------------------------------------------------------------------------------


class TestClassifyError:
    def test_db_connection_error(self) -> None:
        c = services._classify_error("OperationalError", "connection to server at ... failed", "ingest")
        assert c["cause_he"] == "בסיס הנתונים לא זמין"
        assert "eo native status" in c["action_he"]

    def test_timeout(self) -> None:
        c = services._classify_error("TimeoutError", "timed out after 30s", "deep_search")
        assert "פסק זמן" in c["cause_he"]

    def test_http_error_from_source(self) -> None:
        c = services._classify_error("FetchError", "GET https://x failed: 503", "ingest")
        assert c["cause_he"] == "המקור השיב בשגיאה"

    def test_code_bug_family(self) -> None:
        c = services._classify_error("KeyError", "KeyError: 0", "tenders")
        assert "שגיאת קוד" in c["cause_he"]
        assert "מכרזים" in c["cause_he"]

    def test_unknown_falls_back_to_generic(self) -> None:
        c = services._classify_error(None, "something odd", "ingest")
        assert c["cause_he"] == "שגיאה לא מסווגת"

    def test_mandatory_stage_impact_differs_from_regular_stage(self) -> None:
        mandatory = services._classify_error("KeyError", "x", "report")
        regular = services._classify_error("KeyError", "x", "tenders")
        assert mandatory["impact_he"] != regular["impact_he"]
        assert "קריטי" in mandatory["impact_he"]


class TestRecentErrorsShape:
    def test_new_style_row_passes_stored_fields_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {
                "id": 500,
                "job_id": 9,
                "stage": "ingest",
                "event": "error",
                "detail": {
                    "error": "bad payload shape",
                    "error_type": "ValueError",
                    "traceback_tail": ["a.py:1:f"],
                    "trace": "Traceback...\nValueError: bad payload shape\n",
                },
                "at": dt.datetime.now(tz=dt.UTC),
            }
        ]
        monkeypatch.setattr(services, "_fetchall", lambda *_a, **_kw: rows)

        (entry,) = services.recent_errors()
        assert entry["message"] == "bad payload shape"
        assert entry["error_type"] == "ValueError"
        assert entry["traceback_tail"] == ["a.py:1:f"]
        assert entry["cause_he"]
        assert entry["action_he"]
        assert entry["impact_he"]
        assert entry["link"]

    def test_legacy_row_error_279_shape_is_reconstructed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A `run_log` row written before `error_type`/`traceback_tail` existed -- only `error`
        (the useless "0") and the raw `trace` string are present."""
        trace = (
            'Traceback (most recent call last):\n'
            '  File "agent/eoa/orchestrator/jobs.py", line 130, in _run_stage\n'
            '    out = fn()\n'
            '  File "agent/eoa/tenders/scan.py", line 992, in _candidate_duplicate_exists\n'
            '    return any(_notice_portal(r[0]) == portal for r in rows if r[0])\n'
            "KeyError: 0\n"
        )
        rows = [
            {
                "id": 279,
                "job_id": 180,
                "stage": "tenders",
                "event": "error",
                "detail": {"error": "0", "stage": "tenders", "trace": trace, "minutes": 1.8},
                "at": dt.datetime.now(tz=dt.UTC),
            }
        ]
        monkeypatch.setattr(services, "_fetchall", lambda *_a, **_kw: rows)

        (entry,) = services.recent_errors()
        assert entry["message"] != "0"
        assert "KeyError" in entry["message"]
        assert entry["error_type"] == "KeyError"
        assert entry["traceback_tail"], "expected frames recovered from the raw trace"
        assert entry["cause_he"] == "שגיאת קוד בשלב 'מכרזים'"

    def test_no_errors_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "_fetchall", lambda *_a, **_kw: [])
        assert services.recent_errors() == []

    def test_item_id_drives_the_link_when_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rows = [
            {
                "id": 501,
                "job_id": 9,
                "stage": "classify",
                "event": "error",
                "detail": {"error": "x", "error_type": "ValueError", "item_id": 42},
                "at": dt.datetime.now(tz=dt.UTC),
            }
        ]
        monkeypatch.setattr(services, "_fetchall", lambda *_a, **_kw: rows)
        (entry,) = services.recent_errors()
        assert entry["link"] == "/items/42"
