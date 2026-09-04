"""Tests for eoa.resources.gate — resource allocation and thermal management."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from eoa.resources.gate import ResourceGate, gate
from eoa.resources.gpu import GpuStatus, HostStatus, LoadedModel


@pytest.fixture
def resource_gate():
    """Fresh ResourceGate instance for each test."""
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
        assert reclaim == 5000  # Only model_b


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
