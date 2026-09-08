"""Tests for ``eoa.orchestrator.jobs.run_product_dossier`` -- the ``product_dossier`` job kind
handler (PD-backend, user request 2026-09-08). ``eoa.dossier.report.build_product_dossier`` is
monkeypatched throughout (no DB, no network, no LLM); this file only locks in the payload ->
call-args mapping and the failure-never-blocks-the-orchestrator contract, including PD-cloud-tools
(2026-09-09)'s ``llm_leg`` passthrough.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_jobs_product_dossier.py -q``
"""

from __future__ import annotations

import os

# `eoa.orchestrator.jobs`'s own module docstring documents that importing it sets
# `os.environ.setdefault("EOA_PIPELINE", "1")` at import time (ADR-005's pipeline gate) -- pytest
# collects/imports every test file before running any test, so this import's side effect would
# otherwise leak into every other test in the same process (e.g. `test_ollama_client_provider_
# dispatch.py`'s `TestChatStructuredProviderThreading` assumes `EOA_PIPELINE` is unset). Record
# whether it was already set before this import, then undo the setdefault if it wasn't -- this
# file's own tests never rely on `EOA_PIPELINE` being set (they call `jobs.run_product_dossier`
# directly with `eoa.dossier.report.build_product_dossier` monkeypatched, never touching an LLM
# dispatch path that cares about it).
_had_eoa_pipeline = "EOA_PIPELINE" in os.environ

from dataclasses import dataclass  # noqa: E402
from typing import Any  # noqa: E402

import pytest  # noqa: E402

from eoa.orchestrator import jobs  # noqa: E402

if not _had_eoa_pipeline:
    os.environ.pop("EOA_PIPELINE", None)


@dataclass
class _FakePaths:
    docx: str = "/x.docx"
    md: str = "/x.md"
    html: str = "/x.html"
    report_id: int = 42
    dossier_id: int = 7
    product_key: str = "elbit-systems-spectro-xr"
    outcome: str = "found"
    confidence: float = 0.8


def test_missing_product_name_returns_error_without_calling_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("build_product_dossier must not be called")

    monkeypatch.setattr("eoa.dossier.report.build_product_dossier", boom)
    out = jobs.run_product_dossier({"id": 1, "payload": {}})
    assert out == {"product_dossier_error": "missing product_name"}


def test_forwards_payload_fields_to_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_build(product_name: str, vendor: Any, aliases: Any, **kw: Any) -> _FakePaths:
        captured["product_name"] = product_name
        captured["vendor"] = vendor
        captured["aliases"] = aliases
        captured.update(kw)
        return _FakePaths()

    monkeypatch.setattr("eoa.dossier.report.build_product_dossier", fake_build)
    job = {
        "id": 99,
        "payload": {
            "product_key": "elbit-systems-spectro-xr",
            "product_name": "SPECTRO XR",
            "vendor": "Elbit Systems",
            "aliases": ["Spectro"],
            "product_line": "targeting_pods",
            "budget_multiplier": 2.0,
            "llm_leg": "codex:gpt-6-astra",
        },
    }
    out = jobs.run_product_dossier(job)
    assert captured["product_name"] == "SPECTRO XR"
    assert captured["vendor"] == "Elbit Systems"
    assert captured["aliases"] == ["Spectro"]
    assert captured["product_line"] == "targeting_pods"
    assert captured["budget_multiplier"] == 2.0
    assert captured["llm_leg"] == "codex:gpt-6-astra"
    assert captured["job_id"] == 99
    assert out == {
        "product_dossier": {
            "report_id": 42,
            "dossier_id": 7,
            "product_key": "elbit-systems-spectro-xr",
            "outcome": "found",
            "confidence": 0.8,
        }
    }


def test_llm_leg_absent_from_payload_passes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_build(product_name: str, vendor: Any, aliases: Any, **kw: Any) -> _FakePaths:
        captured.update(kw)
        return _FakePaths()

    monkeypatch.setattr("eoa.dossier.report.build_product_dossier", fake_build)
    jobs.run_product_dossier({"id": 1, "payload": {"product_name": "SPECTRO XR"}})
    assert captured["llm_leg"] is None


def test_builder_exception_returns_error_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_build(*a: Any, **k: Any) -> Any:
        raise RuntimeError("boom, LLM unavailable")

    monkeypatch.setattr("eoa.dossier.report.build_product_dossier", fake_build)
    out = jobs.run_product_dossier({"id": 1, "payload": {"product_name": "SPECTRO XR"}})
    assert "product_dossier_error" in out
    assert "boom" in out["product_dossier_error"]
