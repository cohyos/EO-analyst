"""Unit tests for the ``tech_daily`` report kind's read-side wiring: ``GET /api/reports?kind=
tech_daily`` passes the filter through unchanged (the backend never hard-codes an enum of report
kinds -- ``eoa.api.services.list_reports``'s ``kind`` filter is free-form SQL, gated only by the
DB's own ``reports_kind_check`` CHECK constraint, widened for 'tech_daily' by migration 0034), and
``eoa.api.services.morning()``'s new "טכנולוגיה היום" card (``_tech_daily_summary``).

Same ``TestClient`` + monkeypatch convention as ``tests/unit/test_api_round4_gate_and_reports.py``
(no live DB/Ollama/network).

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_reports_api_tech_daily.py -q``
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from eoa.api import services


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


class TestReportsKindFilter:
    def test_kind_tech_daily_is_forwarded_unchanged_to_the_service_layer(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def _fake_list_reports(*, kind=None, limit=30):
            captured["kind"] = kind
            captured["limit"] = limit
            return []

        monkeypatch.setattr(services, "list_reports", _fake_list_reports)

        r = client.get("/api/reports", params={"kind": "tech_daily"})
        assert r.status_code == 200
        assert r.json() == []
        assert captured["kind"] == "tech_daily"

    def test_list_reports_kind_tech_daily_builds_the_expected_where_clause(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def _fake_fetchall(sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return []

        monkeypatch.setattr(services, "_fetchall", _fake_fetchall)

        assert services.list_reports(kind="tech_daily") == []
        assert "kind = %(kind)s" in captured["sql"]
        assert captured["params"]["kind"] == "tech_daily"


class TestTechDailyMorningSummary:
    def test_no_tech_daily_report_yet_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "_fetchone", lambda *a, **k: None)
        assert services._tech_daily_summary() is None

    def test_layers_with_news_count_read_from_qa_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = {
            "id": 42,
            "created_at": "2026-09-17T04:00:00+03:00",
            "qa_passed": True,
            "qa_report": {"layers_with_news": ["detectors_fpa", "optics"]},
        }
        monkeypatch.setattr(services, "_fetchone", lambda *a, **k: row)

        summary = services._tech_daily_summary()
        assert summary == {
            "report_id": 42,
            "created_at": "2026-09-17T04:00:00+03:00",
            "qa_passed": True,
            "layers_with_news_count": 2,
        }

    def test_missing_layers_with_news_key_defaults_to_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = {"id": 7, "created_at": "x", "qa_passed": False, "qa_report": {}}
        monkeypatch.setattr(services, "_fetchone", lambda *a, **k: row)

        assert services._tech_daily_summary()["layers_with_news_count"] == 0

    def test_morning_includes_tech_daily_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(services, "list_reports", lambda **kwargs: [])
        monkeypatch.setattr(services, "_fetchall", lambda *a, **k: [])
        monkeypatch.setattr(services, "_tech_daily_summary", lambda: {"report_id": 1, "created_at": "x",
                                                                        "qa_passed": True,
                                                                        "layers_with_news_count": 1})
        monkeypatch.setattr(services, "_night_summary", lambda: {})
        monkeypatch.setattr(services, "recent_errors", lambda: [])

        result = services.morning()
        assert result["tech_daily"] == {
            "report_id": 1, "created_at": "x", "qa_passed": True, "layers_with_news_count": 1
        }
