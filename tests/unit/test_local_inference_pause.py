"""Resource pause must block local inference without touching GPU models or cloud work."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from eoa.cli import app
from eoa.config import ChainEntryCfg, ModelSpec
from eoa.errors import ResourceUnavailable
from eoa.llm import chain, ollama_client
from eoa.llm.providers.base import ProviderResult
from eoa.resources import gate as gate_module
from eoa.resources.gate import ResourceGate
from eoa.resources.gpu import GpuStatus, HostStatus


@pytest.fixture
def paused():
    gate_module.LOCAL_INFERENCE_PAUSE_FILE.touch()


@pytest.mark.parametrize("interactive", [False, True])
@pytest.mark.parametrize("night", [False, True])
def test_pause_precedes_night_interactive_and_telemetry(paused, monkeypatch, interactive, night):
    resource_gate = ResourceGate()
    resource_gate.force_night_mode = night
    snapshot = Mock(side_effect=AssertionError("paused calls must not reach telemetry"))
    monkeypatch.setattr(gate_module.telemetry, "snapshot", snapshot)
    with pytest.raises(ResourceUnavailable, match="Local inference is paused"):
        resource_gate.acquire("resident", interactive=interactive)
    snapshot.assert_not_called()


@pytest.mark.parametrize("operation", ["chat", "stream", "embed", "warm"])
def test_every_ollama_entrypoint_is_blocked_before_http(paused, monkeypatch, operation):
    # Worker-module imports set this process-wide; test the explicit local API entrypoint.
    monkeypatch.delenv("EOA_PIPELINE", raising=False)
    client = Mock(side_effect=AssertionError("no Ollama HTTP while paused"))
    monkeypatch.setattr(ollama_client, "_client", client)
    with pytest.raises(ResourceUnavailable, match="Local inference is paused"):
        if operation == "chat":
            ollama_client.chat("resident", [{"role": "user", "content": "test"}], provider="ollama")
        elif operation == "stream":
            list(ollama_client.chat_stream("resident", [], provider="ollama", interactive=True))
        elif operation == "embed":
            ollama_client.embed(["test"])
        else:
            ollama_client.warm_up("resident")
    client.assert_not_called()


def test_pause_is_rechecked_while_waiting_for_resources(monkeypatch):
    resource_gate = ResourceGate()
    resource_gate.force_night_mode = True
    monkeypatch.setattr(resource_gate, "_record", lambda decision: None)
    host = HostStatus(datetime.now(UTC), GpuStatus(12227, 12000, 0, 50), 32000, 64000, 100)
    monkeypatch.setattr(gate_module.telemetry, "snapshot", lambda *args: host)
    monkeypatch.setattr(resource_gate, "_sleep", lambda seconds: gate_module.LOCAL_INFERENCE_PAUSE_FILE.touch())
    with pytest.raises(ResourceUnavailable, match="Local inference is paused"):
        resource_gate.acquire("resident")


def test_cpu_security_guard_remains_available(paused, monkeypatch):
    resource_gate = ResourceGate()
    spec = ModelSpec(key="guard", runtime="onnx", vendor="test", origin="US", license="MIT")
    config = SimpleNamespace(
        model=lambda role: spec,
        resources=SimpleNamespace(
            queue_backoff_seconds=[1], queue_timeout_min=1, min_free_ram_mb=8000,
            min_free_disk_gb=20, gpu_temp_stop_c=88,
        ),
        ollama_url="http://unused",
    )
    monkeypatch.setattr(gate_module, "settings", lambda: config)
    host = HostStatus(datetime.now(UTC), GpuStatus(12227, 1000, 0, 50), 32000, 64000, 100)
    monkeypatch.setattr(gate_module.telemetry, "snapshot", lambda *args: host)
    monkeypatch.setattr(resource_gate, "_record", lambda decision: None)
    assert resource_gate.acquire("guard_l1") is spec


def test_cloud_success_does_not_reach_local_gate(paused, monkeypatch):
    result = ProviderResult(content="cloud result", model="test", provider="fake", usage={})
    provider = SimpleNamespace(is_available=lambda: True, chat=lambda *args, **kwargs: result)
    monkeypatch.setattr(chain, "_build_provider", lambda entry: provider)
    monkeypatch.setattr(chain, "_record", lambda *args: None)
    local = Mock(side_effect=AssertionError("cloud success must not fall back"))
    actual, _ = chain.run_chain(
        "resident", [ChainEntryCfg(provider="claude", model="test"), ChainEntryCfg(provider="ollama")],
        local, messages=[],
    )
    assert actual is result
    local.assert_not_called()


def test_local_fallback_preserves_resource_deferral(paused, monkeypatch):
    monkeypatch.setattr(chain, "_record", lambda *args: None)
    with pytest.raises(ResourceUnavailable, match="Local inference is paused"):
        chain.run_chain(
            "resident", [ChainEntryCfg(provider="ollama")],
            lambda: ResourceGate().acquire("resident"), messages=[],
        )


def test_cli_pause_resume_are_shared_idempotent_and_do_not_unload(monkeypatch):
    unload = Mock(side_effect=AssertionError("never unload another application's model"))
    monkeypatch.setattr(ollama_client, "unload_model", unload)
    runner = CliRunner()
    for _ in range(2):
        assert runner.invoke(app, ["models", "pause-local"]).exit_code == 0
    assert gate_module.local_inference_paused()
    assert "paused" in runner.invoke(app, ["models", "local-status"]).stdout
    for _ in range(2):
        assert runner.invoke(app, ["models", "resume-local"]).exit_code == 0
    assert not gate_module.local_inference_paused()
    assert "enabled" in runner.invoke(app, ["models", "local-status"]).stdout
    unload.assert_not_called()


def test_unreadable_pause_state_fails_closed(monkeypatch):
    monkeypatch.setattr(
        gate_module, "LOCAL_INFERENCE_PAUSE_FILE", SimpleNamespace(stat=Mock(side_effect=PermissionError))
    )
    assert gate_module.local_inference_paused()


@pytest.mark.parametrize("busy", [False, True])
def test_embedding_exception_still_checks_gpu_and_blocks_chat(monkeypatch, busy):
    gate_module.LOCAL_INFERENCE_PAUSE_FILE.write_text("allow-embed", encoding="utf-8")
    resource_gate = ResourceGate()
    monkeypatch.setattr(resource_gate, "_record", lambda decision: None)
    host = HostStatus(datetime.now(UTC), GpuStatus(24000, 2000, 99 if busy else 0, 50), 32000, 64000, 100)
    monkeypatch.setattr(gate_module.telemetry, "snapshot", lambda *args: host)
    from eoa.resources.inference import local_inference_lock
    with local_inference_lock(role="embed"):
        if busy:
            with pytest.raises(ResourceUnavailable, match="GPU busy"):
                resource_gate.acquire("embed")
        else:
            assert resource_gate.acquire("embed").ollama
    with pytest.raises(ResourceUnavailable, match="paused"):
        with local_inference_lock():
            pass
    with pytest.raises(ResourceUnavailable, match="paused"):
        resource_gate.acquire("resident")


def test_pause_command_revokes_embedding_exception():
    runner = CliRunner()
    assert runner.invoke(app, ["models", "pause-local", "--allow-embed"]).exit_code == 0
    gate_module._check_local_inference_pause("embed")
    assert runner.invoke(app, ["models", "pause-local"]).exit_code == 0
    with pytest.raises(ResourceUnavailable):
        gate_module._check_local_inference_pause("embed")
