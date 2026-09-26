"""Tests for eoa.resources.gate — resource allocation and thermal management."""

from __future__ import annotations

import threading
import time
import types
from datetime import UTC, datetime

import pytest

from eoa.config import ModelSpec
from eoa.errors import ResourceUnavailable
from eoa.resources.gate import ResourceGate, gate
from eoa.resources.gpu import GpuStatus, HostStatus, LoadedModel


def _fake_resources(**overrides):
    """A duck-typed stand-in for ``Settings.resources`` (a plain namespace, not the real
    pydantic ``ResourcesCfg``) so these tests don't depend on ``conftest.minimal_settings`` — that
    fixture's ``ModelsRegistry`` construction has an unrelated pre-existing bug (it passes already
    -built ``ModelSpec`` instances through a validator that expects raw dicts) and is not otherwise
    exercised by any test, so it is left alone here."""
    defaults = dict(
        vram_total_mb=12227,
        vram_safety_margin_mb=1200,
        min_free_ram_mb=8000,
        min_free_disk_gb=20,
        warn_free_disk_gb=40,
        gpu_temp_pause_c=83,
        gpu_temp_stop_c=88,
        queue_backoff_seconds=[5, 10, 30, 60],
        queue_timeout_min=20,
        min_loaded_seconds=300,
        polite_mode=types.SimpleNamespace(external_gpu_util_threshold=25, enabled_outside_night_window=True),
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


class _FakeSettings:
    """Minimal duck-typed ``settings()`` stand-in exposing only what ``ResourceGate`` reads."""

    def __init__(self, models: dict[str, ModelSpec], resources: types.SimpleNamespace | None = None) -> None:
        self._models = models
        self.resources = resources or _fake_resources()
        self.ollama_url = "http://127.0.0.1:11434"

    def model(self, role: str) -> ModelSpec:
        return self._models[role]


RESIDENT_SPEC = ModelSpec(
    key="resident", ollama="gemma4:12b", vendor="Google", origin="US", license="Apache-2.0", est_vram_mb=8200
)
GUARD_L1_SPEC = ModelSpec(
    key="guard_l1",
    ollama="prompt_injection_deberta",
    vendor="Microsoft",
    origin="US",
    license="MIT",
    runtime="onnx",
    est_vram_mb=0,
)


@pytest.fixture
def resource_gate(monkeypatch):
    """Fresh ResourceGate instance for each test."""
    monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kwargs: None)
    return ResourceGate()


class TestResourceGateBasics:
    """Test basic gate functionality and initialization."""

    def test_gate_singleton_returns_same_instance(self):
        """gate() always returns the same ResourceGate instance."""
        g1 = gate()
        g2 = gate()
        assert g1 is g2

    def test_resource_gate_initializes_with_empty_history(self, resource_gate):
        """ResourceGate starts with empty history."""
        assert len(resource_gate.history) == 0
        assert resource_gate._loaded_since == {}

    def test_resource_gate_tracking_loaded_models(self, resource_gate):
        """ResourceGate tracks when models were loaded."""
        import time

        now = time.monotonic()
        resource_gate._loaded_since["test_model"] = now
        assert "test_model" in resource_gate._loaded_since

    def test_resource_gate_force_night_mode_flag(self, resource_gate):
        """force_night_mode flag can be set."""
        assert resource_gate.force_night_mode is False
        resource_gate.force_night_mode = True
        assert resource_gate.force_night_mode is True

    def test_is_batch_window_with_forced_night_mode(self, resource_gate):
        """is_batch_window() returns True when force_night_mode is True."""
        resource_gate.force_night_mode = True
        assert resource_gate.is_batch_window() is True

    def test_reclaimable_vram_no_models(self, resource_gate):
        """_reclaimable_vram returns 0 when no models loaded."""
        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12000, vram_used_mb=2000, util_pct=10, temp_c=50),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[],
        )
        reclaim = resource_gate._reclaimable_vram(host, keep="test")
        assert reclaim == 0

    def test_reclaimable_vram_excludes_keep_model(self, resource_gate):
        """_reclaimable_vram excludes the 'keep' model."""
        import time

        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12000, vram_used_mb=10000, util_pct=50, temp_c=70),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[
                LoadedModel(name="model_a", size_mb=5000, size_vram_mb=5000),
                LoadedModel(name="model_b", size_mb=5000, size_vram_mb=5000),
            ],
        )
        # Mark models as old enough to reclaim
        min_loaded = 300
        now = time.monotonic()
        resource_gate._loaded_since["model_a"] = now - min_loaded - 100
        resource_gate._loaded_since["model_b"] = now - min_loaded - 100

        # Both models are old enough, but keep model_a
        reclaim = resource_gate._reclaimable_vram(host, keep="model_a")
        assert reclaim == 0  # Loaded age does not establish exclusive ownership


class TestGateDecision:
    """Test the decision tracking system."""

    def test_decision_recorded_with_timestamp(self, resource_gate):
        """Decisions recorded in history include timestamp."""

        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12000, vram_used_mb=2000, util_pct=10, temp_c=50),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
        )
        decision = ResourceGate._decision("proceed", "test_model", host, 0, "test reason")
        assert decision.decision == "proceed"
        assert decision.model == "test_model"
        assert decision.reason == "test reason"
        assert isinstance(decision.at, datetime)

    def test_history_limited_to_maxlen(self, resource_gate):
        """History deque is limited to 200 entries."""

        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12000, vram_used_mb=2000, util_pct=10, temp_c=50),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
        )

        # Add more than 200 decisions
        for i in range(250):
            decision = ResourceGate._decision("proceed", f"model_{i}", host, 0, "test")
            resource_gate.history.append(decision)

        # Should only keep last 200
        assert len(resource_gate.history) == 200


class TestStatusSnapshot:
    """Test the status() method for UI reporting."""

    def test_status_returns_dict_with_expected_keys(self, resource_gate, monkeypatch):
        """status() returns a dict with GPU, RAM, disk, and recent decisions."""
        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12000, vram_used_mb=2000, util_pct=25, temp_c=60),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=75.5,
            loaded_models=[
                LoadedModel(name="model_x", size_mb=2000, size_vram_mb=2000),
            ],
        )
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: host)

        status = resource_gate.status()
        assert "at" in status
        assert "gpu" in status
        assert "ram" in status
        assert "disk_free_gb" in status
        assert "loaded_models" in status
        assert "batch_window" in status
        assert "recent_decisions" in status

    def test_status_gpu_fields(self, resource_gate, monkeypatch):
        """status() includes all GPU fields."""
        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12000, vram_used_mb=2000, util_pct=25, temp_c=60),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=75.5,
        )
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: host)

        status = resource_gate.status()
        assert status["gpu"]["available"] is True
        assert status["gpu"]["vram_total_mb"] == 12000
        assert status["gpu"]["vram_used_mb"] == 2000
        assert status["gpu"]["vram_free_mb"] == 10000
        assert status["gpu"]["util_pct"] == 25
        assert status["gpu"]["temp_c"] == 60

    def test_status_loaded_models(self, resource_gate, monkeypatch):
        """status() lists loaded models with metadata."""
        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12000, vram_used_mb=2000, util_pct=25, temp_c=60),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=75.5,
            loaded_models=[
                LoadedModel(name="model_a", size_mb=5000, size_vram_mb=5000),
                LoadedModel(name="model_b", size_mb=3000, size_vram_mb=2000),  # Partially on CPU
            ],
        )
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: host)

        status = resource_gate.status()
        assert len(status["loaded_models"]) == 2
        assert status["loaded_models"][0]["name"] == "model_a"
        assert status["loaded_models"][1]["cpu_offload"] is True  # Partially on CPU


class TestNightWindowDetection:
    """Test _in_night_window logic."""

    def test_in_night_window_static_method(self):
        """_in_night_window is a static method."""
        # Should not raise an error even though it's called on None context
        # (testing that it's callable as static)
        result = ResourceGate._in_night_window()
        assert isinstance(result, bool)


class TestThreadSafety:
    """Test thread-safety of gate singleton."""

    def test_gate_singleton_thread_safe(self):
        """gate() singleton is thread-safe."""
        import threading

        gates = []

        def get_gate():
            gates.append(gate())

        threads = [threading.Thread(target=get_gate) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should be the same instance
        assert all(g is gates[0] for g in gates)


class TestAcquireLocking:
    """#11: acquire() is serialised with self._lock via _acquire_locked."""

    def test_acquire_serializes_concurrent_calls(self, resource_gate, monkeypatch):
        """Two threads calling acquire() concurrently never execute the locked body overlappingly."""
        monkeypatch.setattr("eoa.resources.gate.settings", lambda: _FakeSettings({"resident": RESIDENT_SPEC}))
        # avoid _in_night_window() touching real config's schedule (force the batch-window path)
        resource_gate.force_night_mode = True

        # "resident" role -> ollama name "gemma4:12b"; mark it already-loaded so each call takes
        # the fast "already loaded" path (no queue sleeping).
        loaded_host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=8200, util_pct=10, temp_c=50, available=True),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[LoadedModel(name="gemma4:12b", size_mb=8200, size_vram_mb=8200)],
        )

        concurrent = 0
        max_concurrent = 0
        lock = threading.Lock()

        def fake_snapshot(*_a, **_kw):
            nonlocal concurrent, max_concurrent
            with lock:
                concurrent += 1
                max_concurrent = max(max_concurrent, concurrent)
            time.sleep(0.03)  # hold the "critical section" long enough for a race to show up
            with lock:
                concurrent -= 1
            return loaded_host

        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", fake_snapshot)

        errors: list[BaseException] = []

        def call_acquire():
            try:
                resource_gate.acquire("resident")
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=call_acquire) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors
        assert max_concurrent == 1, "acquire() calls overlapped — the lock did not serialise them"


class TestEligibleForUnload:
    """#12: only models past min_loaded_seconds (or never seen by the gate) are unload-eligible."""

    def _host(self, **loaded) -> HostStatus:
        return HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=10000, util_pct=50, temp_c=70),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=list(loaded.values()),
        )

    def test_unseen_model_is_not_eligible(self, resource_gate):
        """A model loaded by someone else must not be evicted."""
        host = self._host(a=LoadedModel(name="model_a", size_mb=5000, size_vram_mb=5000))
        eligible = resource_gate._eligible_for_unload(host, keep="keep_me")
        assert eligible == []

    def test_freshly_loaded_model_is_not_eligible(self, resource_gate):
        """A model loaded moments ago (by this gate) must not be unloaded yet."""
        host = self._host(a=LoadedModel(name="model_a", size_mb=5000, size_vram_mb=5000))
        resource_gate._loaded_since["model_a"] = time.monotonic()  # just loaded
        eligible = resource_gate._eligible_for_unload(host, keep="keep_me")
        assert eligible == []

    def test_aged_shared_model_is_not_eligible(self, resource_gate):
        """Age alone does not prove a shared model is unused."""
        host = self._host(a=LoadedModel(name="model_a", size_mb=5000, size_vram_mb=5000))
        min_loaded = 300  # matches config/config.yaml resources.min_loaded_seconds
        resource_gate._loaded_since["model_a"] = time.monotonic() - min_loaded - 100
        eligible = resource_gate._eligible_for_unload(host, keep="unrelated")
        assert eligible == []

    def test_keep_model_is_always_excluded(self, resource_gate):
        host = self._host(a=LoadedModel(name="keep_me", size_mb=5000, size_vram_mb=5000))
        eligible = resource_gate._eligible_for_unload(host, keep="keep_me")
        assert eligible == []


class TestThermalPauseIncrements:
    """#28: thermal pause naps in bounded (<= 30s) increments and re-checks, instead of a fixed 120s."""

    def test_thermal_pause_uses_bounded_increments(self, resource_gate, monkeypatch):
        monkeypatch.setattr("eoa.resources.gate.settings", lambda: _FakeSettings({"resident": RESIDENT_SPEC}))

        hot_host = HostStatus(
            at=datetime.now(tz=UTC),
            # >= gpu_temp_pause_c (83) but < gpu_temp_stop_c (88): stays in the pause branch forever
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=2000, util_pct=10, temp_c=85, available=True),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[],
        )
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda *_a, **_kw: hot_host)

        # deterministic fake clock: real time never actually passes, only what _sleep "spends" does
        clock = {"t": 0.0}
        monkeypatch.setattr("eoa.resources.gate.time.monotonic", lambda: clock["t"])

        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            clock["t"] += seconds

        resource_gate._sleep = fake_sleep

        with pytest.raises(ResourceUnavailable, match="thermal pause"):
            resource_gate.acquire("resident")

        assert sleeps, "expected at least one thermal-pause nap"
        assert all(0 < s <= 30 for s in sleeps), f"pause exceeded the 30s bound: {sleeps}"
        assert len(sleeps) > 1, "a single 120s nap would not re-check the temperature promptly"


class TestCpuRuntimeRamQueue:
    """#27: the CPU-runtime path (guard_l1 etc.) also applies the RAM floor, but queues instead of
    failing immediately."""

    def _host(self, ram_free_mb: int) -> HostStatus:
        return HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=2000, util_pct=10, temp_c=50, available=True),
            ram_free_mb=ram_free_mb,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[],
        )

    def test_queues_on_low_ram_then_proceeds(self, resource_gate, monkeypatch):
        monkeypatch.setattr("eoa.resources.gate.settings", lambda: _FakeSettings({"guard_l1": GUARD_L1_SPEC}))

        hosts = [self._host(4000), self._host(4000), self._host(32000)]

        def fake_snapshot(*_a, **_kw):
            return hosts.pop(0) if len(hosts) > 1 else hosts[0]

        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", fake_snapshot)
        sleeps: list[float] = []
        resource_gate._sleep = lambda s: sleeps.append(s)

        spec = resource_gate.acquire("guard_l1")

        assert spec.key == "guard_l1"
        assert len(sleeps) >= 1, "expected the CPU-runtime path to queue (sleep) on low RAM"

    def test_raises_resource_unavailable_after_deadline_when_ram_stays_low(self, resource_gate, monkeypatch):
        monkeypatch.setattr("eoa.resources.gate.settings", lambda: _FakeSettings({"guard_l1": GUARD_L1_SPEC}))
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda *_a, **_kw: self._host(4000))

        clock = {"t": 0.0}
        monkeypatch.setattr("eoa.resources.gate.time.monotonic", lambda: clock["t"])

        def fake_sleep(seconds: float) -> None:
            clock["t"] += seconds

        resource_gate._sleep = fake_sleep

        with pytest.raises(ResourceUnavailable, match="RAM"):
            resource_gate.acquire("guard_l1")


EMBED_SPEC = ModelSpec(
    key="arctic_embed2",
    ollama="snowflake-arctic-embed2",
    vendor="Snowflake",
    origin="US",
    license="Apache-2.0",
    est_vram_mb=1300,
)


class TestEmbedCpuFallback:
    """2026-09-26: when the GPU cannot take an embedding call right now, `acquire_embed` admits
    the SAME embedding model on the CPU instead of deferring the whole embed_dedup stage (26.9:
    polite mode deferred it and new items went unembedded)."""

    def _host(self, *, util: int = 5, temp: int = 50, vram_used: int = 2000, ram_free: int = 32000) -> HostStatus:
        return HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=vram_used, util_pct=util, temp_c=temp, available=True),
            ram_free_mb=ram_free,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[],
        )

    def _settings(self, monkeypatch, **res):
        resources = _fake_resources(embed_cpu_fallback=True, embed_cpu_threads=4, interactive_wait_s=20, **res)
        monkeypatch.setattr(
            "eoa.resources.gate.settings", lambda: _FakeSettings({"embed": EMBED_SPEC}, resources)
        )
        monkeypatch.setattr("eoa.resources.gate._check_local_inference_pause", lambda role: None)

    @pytest.mark.parametrize(
        "host_kw,why",
        [({"util": 90}, "external gpu util"), ({"temp": 85}, "gpu 85C"), ({"vram_used": 11500}, "vram free")],
    )
    def test_gpu_unusable_falls_back_to_cpu(self, resource_gate, monkeypatch, host_kw, why):
        self._settings(monkeypatch)
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda *_a, **_kw: self._host(**host_kw))
        decisions: list = []
        monkeypatch.setattr(resource_gate, "_record", lambda d: decisions.append(d))

        spec, on_cpu = resource_gate.acquire_embed("embed")

        assert spec.key == "arctic_embed2" and on_cpu is True
        assert any(why in str(getattr(d, "reason", d)) for d in decisions)

    def test_idle_gpu_stays_on_gpu(self, resource_gate, monkeypatch):
        self._settings(monkeypatch)
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda *_a, **_kw: self._host())
        _spec, on_cpu = resource_gate.acquire_embed("embed")
        assert on_cpu is False

    def test_full_pause_is_still_respected(self, resource_gate, monkeypatch):
        self._settings(monkeypatch)

        def _paused(role):
            raise ResourceUnavailable("Local inference is paused")

        monkeypatch.setattr("eoa.resources.gate._check_local_inference_pause", _paused)
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda *_a, **_kw: self._host(util=90))
        with pytest.raises(ResourceUnavailable, match="paused"):
            resource_gate.acquire_embed("embed")

    def test_fallback_disabled_keeps_old_polite_deferral(self, resource_gate, monkeypatch):
        self._settings(monkeypatch)
        resources = _fake_resources(embed_cpu_fallback=False, embed_cpu_threads=4, interactive_wait_s=20)
        monkeypatch.setattr("eoa.resources.gate.settings", lambda: _FakeSettings({"embed": EMBED_SPEC}, resources))
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda *_a, **_kw: self._host(util=90))
        with pytest.raises(ResourceUnavailable, match="polite"):
            resource_gate.acquire_embed("embed")

    def test_cpu_fallback_still_applies_the_ram_floor(self, resource_gate, monkeypatch):
        self._settings(monkeypatch)
        monkeypatch.setattr(
            "eoa.resources.gate.telemetry.snapshot", lambda *_a, **_kw: self._host(util=90, ram_free=1000)
        )
        clock = {"t": 0.0}
        monkeypatch.setattr("eoa.resources.gate.time.monotonic", lambda: clock["t"])
        resource_gate._sleep = lambda s: clock.__setitem__("t", clock["t"] + s)
        with pytest.raises(ResourceUnavailable, match="RAM"):
            resource_gate.acquire_embed("embed")
