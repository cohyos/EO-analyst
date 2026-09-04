"""Unit tests for `WS /ws/status` -- FastAPI TestClient's `websocket_connect`, all DB/gate/services
calls monkeypatched (no real Postgres, no real GPU/Ollama telemetry).

Regression coverage for the "socket closes immediately after accept" bug: `pipeline_status()`'s
`current_job` (and `run_log_since()`'s rows) come from raw DB rows carrying native
`datetime.datetime` values, which `WebSocket.send_json`'s plain `json.dumps` cannot serialise --
uncaught, that `TypeError` used to be fatal to the whole handler and killed the connection on the
very first tick that hit it (in practice: as soon as any `jobs` row was ever in state 'running',
including a stale one left over from a crashed run). These tests assert the socket stays open and
keeps pushing valid JSON frames even when a tick's payload still carries such a value, and even when
a whole tick raises outright.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_ws_status.py -q``
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    # No real Postgres in this test: the app's lifespan opens/closes the
    # pool, so replace both with no-ops (same pattern as test_api_smoke.py).
    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


class _FakeGateStatus:
    """Stands in for `eoa.resources.gate.gate().status()` -- no real nvidia-smi/Ollama telemetry."""

    def status(self) -> dict:
        return {
            "at": "2026-01-01T00:00:00+00:00",
            "gpu": {
                "available": False,
                "vram_total_mb": 0,
                "vram_used_mb": 0,
                "vram_free_mb": 0,
                "util_pct": 0,
                "temp_c": 0,
            },
            "ram": {"free_mb": 1000, "total_mb": 2000},
            "disk_free_gb": 100.0,
            "loaded_models": [],
            "batch_window": False,
            "recent_decisions": [],
        }


class _FakeApiCfg:
    status_push_seconds = 0.01  # keep the test fast


class _FakeSettings:
    api = _FakeApiCfg()


def _patch_common(monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services
    from eoa.api.routes import status as status_route

    monkeypatch.setattr(
        services,
        "services_status",
        lambda: {"postgres": True, "ollama": False, "searxng": True, "ntfy": True},
    )
    monkeypatch.setattr(status_route, "gate", lambda: _FakeGateStatus())
    monkeypatch.setattr(status_route, "settings", lambda: _FakeSettings())
    monkeypatch.setattr(services, "latest_run_log_id", lambda: 0)
    monkeypatch.setattr(services, "run_log_since", lambda last_id: ([], last_id))


def _empty_pipeline() -> dict:
    return {
        "current_job": None,
        "queue_depth": 0,
        "stage": None,
        "night_window": False,
        "next_run_at": None,
        "last_run": None,
    }


def test_ws_status_streams_at_least_two_frames(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    _patch_common(monkeypatch)
    monkeypatch.setattr(services, "pipeline_status", _empty_pipeline)

    with client.websocket_connect("/ws/status") as ws:
        frames = [ws.receive_json() for _ in range(2)]

    assert len(frames) >= 2
    for frame in frames:
        assert isinstance(frame, dict)
        assert set(frame) >= {"at", "services", "gate", "pipeline"}
        assert frame["services"]["postgres"] is True
        assert frame["gate"]["ram"]["free_mb"] == 1000
        assert frame["pipeline"]["queue_depth"] == 0


def test_ws_status_survives_non_json_serializable_current_job(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: a raw `datetime` in `current_job` (as an un-normalised DB row would carry)
    must not kill the socket -- it must still arrive as *some* JSON string."""
    from eoa.api import services

    _patch_common(monkeypatch)
    started_at = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    monkeypatch.setattr(
        services,
        "pipeline_status",
        lambda: {
            "current_job": {"id": 1, "kind": "daily_run", "state": "running", "started_at": started_at},
            "queue_depth": 1,
            "stage": "fetch",
            "night_window": True,
            "next_run_at": None,
            "last_run": None,
        },
    )

    with client.websocket_connect("/ws/status") as ws:
        frames = [ws.receive_json() for _ in range(2)]

    assert len(frames) >= 2
    for frame in frames:
        current_job = frame["pipeline"]["current_job"]
        assert current_job["state"] == "running"
        assert isinstance(current_job["started_at"], str)


def test_ws_status_survives_a_bad_tick_and_keeps_pushing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single tick raising (DB hiccup, telemetry error, ...) must be logged and skipped, not fatal."""
    from eoa.api import services

    _patch_common(monkeypatch)

    calls = {"n": 0}

    def flaky_pipeline_status() -> dict:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated DB hiccup")
        return _empty_pipeline()

    monkeypatch.setattr(services, "pipeline_status", flaky_pipeline_status)

    with client.websocket_connect("/ws/status") as ws:
        first = ws.receive_json()
        # Tick 2 raises inside `_status_payload()` and is swallowed per-tick
        # (no frame sent that tick); the loop must still be alive to
        # deliver tick 3's frame rather than the socket having closed.
        third = ws.receive_json()

    assert first["pipeline"]["queue_depth"] == 0
    assert third["pipeline"]["queue_depth"] == 0
    assert calls["n"] >= 3
