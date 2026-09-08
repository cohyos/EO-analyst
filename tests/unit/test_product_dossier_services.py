"""Service-layer tests for the product dossier (PD-backend, user request 2026-09-08):
``eoa.api.services.list_dossiers``/``dossier_detail``/``get_dossier``/``enqueue_product_dossier``/
``rerun_product_dossier`` -- monkeypatched ``services._fetchall``/``_fetchone`` (no real Postgres),
mirrors ``tests/unit/test_product_lines.py``'s own convention.

Specifically locks in the PD-ui contract-alignment fix (``docs/qa/content_review/PD-backend.md``):
``list_dossiers``'s ``count`` field is the latest run's grounded deal count (not a run count), and
``get_dossier``'s response has ``path_docx``/``path_md``/``path_html`` as top-level fields (not
nested under a ``report_paths`` object) -- both read directly by the already-shipped
``web/src/api/real.ts`` normalizers.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_product_dossier_services.py -q``
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from eoa.api import services

_ROW = {
    "product_key": "elbit-systems-spectro-xr",
    "product_name": "SPECTRO XR",
    "vendor": "Elbit Systems",
    "aliases": ["Spectro"],
    "product_line": "targeting_pods",
    "id": 5,
    "created_at": dt.datetime(2026, 9, 8, tzinfo=dt.UTC),
    "outcome": "found",
    "confidence": 0.8,
    "report_id": 42,
    "job_id": 7,
    "data": {"identity": {"product_name": "SPECTRO XR"}, "deals": [{"customer": "A"}, {"customer": "B"}]},
    "sources": [{"n": 1, "url": "https://x", "title": "t"}],
}


def test_list_dossiers_count_is_latest_deal_count(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
        assert "product_dossiers" in query
        return [dict(_ROW)]

    monkeypatch.setattr(services, "_fetchall", fake_fetchall)
    out = services.list_dossiers()
    assert len(out) == 1
    assert out[0]["product_key"] == "elbit-systems-spectro-xr"
    assert out[0]["count"] == 2  # len(data.deals), NOT a run count
    assert out[0]["latest"]["id"] == 5


def test_list_dossiers_zero_deals_is_zero_count(monkeypatch: pytest.MonkeyPatch) -> None:
    row = {**_ROW, "data": {"identity": {}}}

    monkeypatch.setattr(services, "_fetchall", lambda query, params=None: [dict(row)])
    out = services.list_dossiers()
    assert out[0]["count"] == 0


def test_get_dossier_flattens_report_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
        calls.append(query)
        if "product_dossiers" in query:
            return dict(_ROW)
        return {"path_docx": "/x.docx", "path_md": "/x.md", "path_html": "/x.html"}

    monkeypatch.setattr(services, "_fetchone", fake_fetchone)
    out = services.get_dossier("elbit-systems-spectro-xr", 5)
    assert out is not None
    assert out["path_docx"] == "/x.docx"
    assert out["path_md"] == "/x.md"
    assert out["path_html"] == "/x.html"
    assert "report_paths" not in out
    assert out["data"]["identity"]["product_name"] == "SPECTRO XR"
    assert out["sources"] == _ROW["sources"]


def test_get_dossier_missing_report_row_defaults_to_none_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
        if "product_dossiers" in query:
            return dict(_ROW)
        return None

    monkeypatch.setattr(services, "_fetchone", fake_fetchone)
    out = services.get_dossier("elbit-systems-spectro-xr", 5)
    assert out is not None
    assert out["path_docx"] is None
    assert out["path_md"] is None


def test_get_dossier_not_found_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "_fetchone", lambda query, params=None: None)
    assert services.get_dossier("unknown", 1) is None


def test_dossier_detail_includes_pending_job(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
        return [dict(_ROW)]

    def fake_fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
        assert "jobs" in query
        return {"id": 99, "state": "running"}

    monkeypatch.setattr(services, "_fetchall", fake_fetchall)
    monkeypatch.setattr(services, "_fetchone", fake_fetchone)
    detail = services.dossier_detail("elbit-systems-spectro-xr")
    assert detail is not None
    assert detail["pending_job"] == {"job_id": 99, "state": "running"}
    assert detail["latest"]["sources"] == _ROW["sources"]
    assert detail["latest"]["identity"]["product_name"] == "SPECTRO XR"


def test_dossier_detail_no_runs_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "_fetchall", lambda query, params=None: [])
    assert services.dossier_detail("unknown") is None


def test_enqueue_product_dossier_computes_product_key(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_enqueue_job(kind: str, payload: dict[str, Any], priority: int = 2) -> int:
        captured["kind"] = kind
        captured["payload"] = payload
        return 123

    monkeypatch.setattr(services.relational, "enqueue_job", fake_enqueue_job)
    out = services.enqueue_product_dossier("SPECTRO XR", "Elbit Systems", ["Spectro"])
    assert out == {"job_id": 123, "product_key": "elbit-systems-spectro-xr"}
    assert captured["kind"] == "product_dossier"
    assert captured["payload"]["product_key"] == "elbit-systems-spectro-xr"


def test_enqueue_product_dossier_requires_name() -> None:
    with pytest.raises(ValueError):
        services.enqueue_product_dossier("")


def test_rerun_product_dossier_reuses_prior_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
        return {
            "product_name": "SPECTRO XR",
            "vendor": "Elbit Systems",
            "aliases": ["Spectro"],
            "product_line": "targeting_pods",
        }

    captured: dict[str, Any] = {}

    def fake_enqueue_job(kind: str, payload: dict[str, Any], priority: int = 2) -> int:
        captured["payload"] = payload
        return 55

    monkeypatch.setattr(services, "_fetchone", fake_fetchone)
    monkeypatch.setattr(services.relational, "enqueue_job", fake_enqueue_job)
    out = services.rerun_product_dossier("elbit-systems-spectro-xr", budget_multiplier=2.0)
    assert out == {"job_id": 55}
    assert captured["payload"]["budget_multiplier"] == 2.0
    assert captured["payload"]["vendor"] == "Elbit Systems"


def test_rerun_product_dossier_unknown_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "_fetchone", lambda query, params=None: None)
    assert services.rerun_product_dossier("unknown") is None
