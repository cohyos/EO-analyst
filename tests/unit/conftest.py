"""Keep unit tests independent of the live application's resource pause switch."""

import pytest


@pytest.fixture(autouse=True)
def isolated_local_inference_switch(monkeypatch, tmp_path):
    monkeypatch.setattr("eoa.resources.gate.LOCAL_INFERENCE_PAUSE_FILE", tmp_path / "local-inference.pause")


@pytest.fixture(autouse=True)
def isolated_status_cache_and_inference_lock(monkeypatch, tmp_path):
    monkeypatch.setattr("eoa.resources.inference.LOCK_PATH", tmp_path / "inference.lock")
    monkeypatch.setattr("eoa.api.services._services_cache", (0, {}))
