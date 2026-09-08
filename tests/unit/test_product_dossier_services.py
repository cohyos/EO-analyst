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
    "sources": [{"n": 1, "url": "https://x", "title": "t", "kind": "item"}],
}

#: PD-fix (2026-09-08, item 2): what `_dossier_source_view` maps `_ROW["sources"]` into --
#: `reliability`/`accessed_at` default null (this fixture row carries neither), `kind` falls back to
#: the internal record-type `kind` ("item") since there is no `source_kind` override on it.
_ROW_SOURCES_VIEW = [
    {"n": 1, "url": "https://x", "title": "t", "kind": "item", "reliability": None, "accessed_at": None}
]


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
    assert out["sources"] == _ROW_SOURCES_VIEW


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
        return {"id": 99, "state": "running", "payload": None}

    monkeypatch.setattr(services, "_fetchall", fake_fetchall)
    monkeypatch.setattr(services, "_fetchone", fake_fetchone)
    detail = services.dossier_detail("elbit-systems-spectro-xr")
    assert detail is not None
    assert detail["pending_job"] == {"job_id": 99, "state": "running", "progress": []}
    assert detail["latest"]["sources"] == _ROW_SOURCES_VIEW


def test_dossier_detail_pending_job_surfaces_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """PD-fix-3 item 2: `eoa.dossier.plan.run_plan`'s `on_progress` writes into `jobs.payload->
    'progress'` while the job is still running -- `_pending_dossier_job` must surface it verbatim."""
    progress = [{"topic": "specifications", "title_he": "מפרט ודף נתונים", "status": "done", "seconds": 12.3, "sources_found": 2}]

    def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
        return [dict(_ROW)]

    def fake_fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
        assert "jobs" in query
        return {"id": 99, "state": "running", "payload": {"progress": progress}}

    monkeypatch.setattr(services, "_fetchall", fake_fetchall)
    monkeypatch.setattr(services, "_fetchone", fake_fetchone)
    detail = services.dossier_detail("elbit-systems-spectro-xr")
    assert detail is not None
    assert detail["pending_job"]["progress"] == progress


def test_dossier_source_view_prefers_source_kind_over_internal_kind() -> None:
    row = {
        "n": 9,
        "url": "https://elbitsystems.com/press",
        "title": "Elbit press release",
        "kind": "web",
        "source_kind": "vendor_official",
        "reliability": "primary",
        "accessed_at": "2026-09-08T10:00:00+00:00",
    }
    view = services._dossier_source_view(row)
    assert view["kind"] == "vendor_official"
    assert view["reliability"] == "primary"
    assert view["accessed_at"] == "2026-09-08T10:00:00+00:00"


def test_dossier_source_view_falls_back_to_internal_kind_for_db_rows() -> None:
    row = {"n": 1, "url": None, "title": "item title", "kind": "item"}
    view = services._dossier_source_view(row)
    assert view["kind"] == "item"
    assert view["reliability"] is None


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


# --------------------------------------------------------------------------
# llm_leg plumbing (PD-cloud-tools, 2026-09-09)
# --------------------------------------------------------------------------


def test_enqueue_product_dossier_carries_llm_leg_in_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_enqueue_job(kind: str, payload: dict[str, Any], priority: int = 2) -> int:
        captured["payload"] = payload
        return 123

    monkeypatch.setattr(services.relational, "enqueue_job", fake_enqueue_job)
    services.enqueue_product_dossier("SPECTRO XR", "Elbit Systems", llm_leg="codex:gpt-6-astra")
    assert captured["payload"]["llm_leg"] == "codex:gpt-6-astra"


def test_enqueue_product_dossier_llm_leg_none_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_enqueue_job(kind: str, payload: dict[str, Any], priority: int = 2) -> int:
        captured["payload"] = payload
        return 123

    monkeypatch.setattr(services.relational, "enqueue_job", fake_enqueue_job)
    services.enqueue_product_dossier("SPECTRO XR")
    assert captured["payload"]["llm_leg"] is None


def test_rerun_product_dossier_carries_llm_leg_in_payload(monkeypatch: pytest.MonkeyPatch) -> None:
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
    services.rerun_product_dossier("elbit-systems-spectro-xr", llm_leg="claude:claude-sonnet-5")
    assert captured["payload"]["llm_leg"] == "claude:claude-sonnet-5"


def test_dossier_run_card_reads_llm_leg_from_data_meta() -> None:
    row = {**_ROW, "data": {**_ROW["data"], "meta": {"llm_leg": "codex:gpt-6-astra"}}}
    card = services._dossier_run_card(row)
    assert card["llm_leg"] == "codex:gpt-6-astra"


def test_dossier_run_card_defaults_llm_leg_to_local_when_absent() -> None:
    card = services._dossier_run_card(_ROW)
    assert card["llm_leg"] == "local"


# --------------------------------------------------------------------------
# PD-vocab-reports (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md section 5): the vocabulary endpoint
# (`GET /api/dossiers/vocabulary/{product_line}`) and product-line-detail's own "which products on
# this line have a dossier" exposure (`services._product_line_dossiers`,
# `services.product_line_detail`'s new `dossiers` key).
# --------------------------------------------------------------------------


def test_dossier_vocabulary_targeting_pods_is_common_plus_line_block() -> None:
    out = services.dossier_vocabulary("targeting_pods")
    assert out is not None
    assert out["product_line"] == "targeting_pods"
    keys = [p["key"] for p in out["parameters"]]
    assert len(keys) == len(set(keys))  # globally unique, per eoa.dossier.vocabulary's own guarantee
    assert len(keys) > 24  # common (24) + at least one targeting_pods-only key
    # every parameter carries the full field contract the UI needs
    sample = out["parameters"][0]
    for field in ("key", "label_he", "label_en", "unit", "value_type", "enum_values", "synonyms", "group_he", "required", "notes_he", "table"):
        assert field in sample


def test_dossier_vocabulary_common_alone_has_no_line_block() -> None:
    common_only = services.dossier_vocabulary("common")
    assert common_only is not None
    assert common_only["product_line"] is None
    full = services.dossier_vocabulary("targeting_pods")
    assert full is not None
    assert len(common_only["parameters"]) < len(full["parameters"])
    common_keys = {p["key"] for p in common_only["parameters"]}
    full_keys = {p["key"] for p in full["parameters"]}
    assert common_keys < full_keys  # strict subset


def test_dossier_vocabulary_unknown_line_returns_none() -> None:
    assert services.dossier_vocabulary("not_a_real_product_line") is None


def test_product_line_dossiers_returns_latest_run_per_product_sorted_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {
            "product_key": "beta-co-widget",
            "product_name": "Beta Widget",
            "vendor": "Beta Co",
            "id": 2,
            "created_at": dt.datetime(2026, 9, 5, tzinfo=dt.UTC),
            "outcome": "found",
            "confidence": 0.7,
            "report_id": 10,
            "data": {},
        },
        {
            "product_key": "elbit-systems-spectro-xr",
            "product_name": "SPECTRO XR",
            "vendor": "Elbit Systems",
            "id": 1,
            "created_at": dt.datetime(2026, 9, 8, tzinfo=dt.UTC),
            "outcome": "found",
            "confidence": 0.68,
            "report_id": 42,
            "data": {},
        },
    ]

    def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
        assert "product_dossiers" in query
        assert params["line"] == "targeting_pods"
        return list(rows)

    monkeypatch.setattr(services, "_fetchall", fake_fetchall)
    out = services._product_line_dossiers("targeting_pods")
    assert [d["product_name"] for d in out] == ["Beta Widget", "SPECTRO XR"]  # alphabetical
    assert out[1]["product_key"] == "elbit-systems-spectro-xr"
    assert out[1]["latest"]["id"] == 1
    assert out[1]["latest"]["confidence"] == 0.68


def test_product_line_dossiers_empty_when_no_products_have_a_dossier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(services, "_fetchall", lambda query, params=None: [])
    assert services._product_line_dossiers("targeting_pods") == []


def test_product_line_detail_exposes_which_products_have_dossiers(monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.product_lines import stats as pl_stats

    canned_dossiers = [{"product_key": "elbit-systems-spectro-xr", "product_name": "SPECTRO XR", "vendor": "Elbit Systems", "latest": {"id": 3}}]

    monkeypatch.setattr(pl_stats, "product_line_stats", lambda line_id: {"items_7d": 0})
    monkeypatch.setattr(services, "_fetchall", lambda query, params=None: [])
    monkeypatch.setattr(services, "_fetchone", lambda query, params=None: None)
    monkeypatch.setattr(services, "_attach_corroboration", lambda items: None)
    monkeypatch.setattr(services, "list_product_line_reports", lambda line_id: [])
    monkeypatch.setattr(services, "_product_line_dossiers", lambda line_id: canned_dossiers)

    detail = services.product_line_detail("targeting_pods")
    assert detail is not None
    assert detail["dossiers"] == canned_dossiers


def test_product_line_detail_unknown_line_returns_none() -> None:
    assert services.product_line_detail("not_a_real_product_line") is None
