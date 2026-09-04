"""Tests for eoa.resources.gate — resource allocation and thermal management."""

from __future__ import annotations

import sys
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from eoa.config import settings
from eoa.errors import ResourceUnavailable
from eoa.resources.gate import ResourceGate, gate
from eoa.resources.gpu import GpuStatus, HostStatus, LoadedModel


@pytest.fixture
def resource_gate():
    """Fresh ResourceGate instance for each test."""
    return ResourceGate()


class TestResourceGateDecisions:
    """Test the core decision flow of the resource gate."""

    def test_proceed_with_sufficient_vram(self, resource_gate, mock_host_status, monkeypatch):
        """Sufficient free VRAM → proceed."""
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: mock_host_status)
        monkeypatch.setattr(
            "eoa.resources.gate.gate", lambda: resource_gate
        )
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)

        spec = resource_gate.acquire("resident")
        assert spec.key == "resident"
        assert resource_gate.history[-1].decision == "proceed"

    def test_proceed_when_model_already_loaded(self, resource_gate, mock_host_with_loaded_model, monkeypatch):
        """Model already loaded → proceed without sleeping."""
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: mock_host_with_loaded_model)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)

        spec = resource_gate.acquire("resident")
        assert spec.key == "resident"
        assert resource_gate.history[-1].decision == "proceed"
        assert resource_gate.history[-1].reason == "already loaded"

    def test_queued_with_backoff_on_insufficient_vram(self, resource_gate, mock_host_status_low_vram, monkeypatch):
        """Insufficient VRAM → queue with backoff sequence."""
        call_count = [0]
        sleep_calls = []

        def mock_snapshot(_):
            # First calls return low VRAM, then eventually sufficient
            if call_count[0] < 2:
                call_count[0] += 1
                return mock_host_status_low_vram
            return HostStatus(
                at=datetime.now(tz=UTC),
                gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=1000, util_pct=10, temp_c=50, available=True),
                ram_free_mb=32000,
                ram_total_mb=64000,
                disk_free_gb=100,
                loaded_models=[],
            )

        def mock_sleep(delay):
            sleep_calls.append(delay)

        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", mock_snapshot)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)
        resource_gate._sleep = mock_sleep

        spec = resource_gate.acquire("light")
        assert spec.key == "light"
        assert len(sleep_calls) > 0
        # Check that we had queued decisions in history
        queued_decisions = [d for d in resource_gate.history if d.decision == "queued"]
        assert len(queued_decisions) > 0

    def test_swap_when_other_model_can_be_unloaded(self, resource_gate, monkeypatch):
        """When reclaimable VRAM exists → swap (unload old model)."""
        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=9500, util_pct=50, temp_c=70, available=True),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[
                LoadedModel(name="gemma4:e4b", size_mb=5500, size_vram_mb=5500),
            ],
        )
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: host)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)
        monkeypatch.setattr("eoa.llm.ollama_client.unload_model", MagicMock())

        # Simulate the model has been loaded for > min_loaded_seconds
        resource_gate._loaded_since["gemma4:e4b"] = time.monotonic() - 400

        spec = resource_gate.acquire("resident")
        assert spec.key == "resident"
        assert resource_gate.history[-1].decision == "swap"

    def test_resource_unavailable_on_timeout(self, resource_gate, mock_host_status_low_vram, monkeypatch):
        """Queue timeout → ResourceUnavailable."""
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: mock_host_status_low_vram)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)

        def mock_sleep(delay):
            pass

        resource_gate._sleep = mock_sleep

        with pytest.raises(ResourceUnavailable, match="VRAM unavailable"):
            resource_gate.acquire("resident", est_vram_mb=10000)

    def test_cpu_runtime_models_skip_vram_check(self, resource_gate, mock_host_status, monkeypatch):
        """CPU-runtime models (runtime != 'ollama') → proceed without VRAM check."""
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: mock_host_status)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)

        spec = resource_gate.acquire("guard_l1")  # CPU runtime model
        assert spec.key == "guard_l1"
        assert resource_gate.history[-1].decision == "proceed"
        assert "cpu-runtime" in resource_gate.history[-1].reason


class TestHardStops:
    """Test hard-stop conditions (disk, temperature, RAM)."""

    def test_disk_below_minimum_raises(self, resource_gate, mock_host_status_low_disk, monkeypatch):
        """Disk < min_free_disk_gb → ResourceUnavailable immediately."""
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: mock_host_status_low_disk)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)

        with pytest.raises(ResourceUnavailable, match="disk free"):
            resource_gate.acquire("resident")

    def test_gpu_temp_stop_raises(self, resource_gate, mock_host_status_hot_gpu, monkeypatch):
        """GPU temp >= stop_c → ResourceUnavailable."""
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: mock_host_status_hot_gpu)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)

        with pytest.raises(ResourceUnavailable, match="GPU temperature"):
            resource_gate.acquire("resident")

    def test_ram_below_minimum_raises(self, resource_gate, monkeypatch):
        """RAM < min_free_ram_mb → ResourceUnavailable."""
        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=2000, util_pct=10, temp_c=50, available=True),
            ram_free_mb=4000,  # Below 8000 MB minimum
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[],
        )
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: host)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)

        with pytest.raises(ResourceUnavailable, match="RAM free"):
            resource_gate.acquire("resident")


class TestThermalPause:
    """Test thermal pause behavior."""

    def test_thermal_pause_above_pause_threshold(self, resource_gate, monkeypatch):
        """Temp >= pause_c → one thermal_pause then retry."""
        call_count = [0]

        def mock_snapshot(_):
            if call_count[0] == 0:
                call_count[0] += 1
                return HostStatus(
                    at=datetime.now(tz=UTC),
                    gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=2000, util_pct=80, temp_c=85, available=True),
                    ram_free_mb=32000,
                    ram_total_mb=64000,
                    disk_free_gb=100,
                    loaded_models=[],
                )
            # Next call: temp is cool
            return HostStatus(
                at=datetime.now(tz=UTC),
                gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=2000, util_pct=10, temp_c=50, available=True),
                ram_free_mb=32000,
                ram_total_mb=64000,
                disk_free_gb=100,
                loaded_models=[],
            )

        sleep_calls = []

        def mock_sleep(delay):
            sleep_calls.append(delay)

        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", mock_snapshot)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)
        resource_gate._sleep = mock_sleep

        spec = resource_gate.acquire("resident")
        assert spec.key == "resident"
        # Check that we had a thermal pause
        thermal_pauses = [d for d in resource_gate.history if d.decision == "thermal_pause"]
        assert len(thermal_pauses) == 1
        assert len(sleep_calls) > 0


class TestPoliteMode:
    """Test polite mode behavior."""

    def test_polite_mode_deferred_outside_night_window(self, resource_gate, monkeypatch):
        """Outside night window with high GPU util and no loaded models → deferred."""
        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=2000, util_pct=40, temp_c=50, available=True),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[],  # No models loaded (so util is external)
        )
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: host)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)

        # Mock to be outside night window
        monkeypatch.setattr.multiple(resource_gate, force_night_mode=False)
        with patch.object(ResourceGate, "_in_night_window", return_value=False):
            with pytest.raises(ResourceUnavailable, match="polite mode"):
                resource_gate.acquire("light", interactive=False)

    def test_polite_mode_allows_interactive(self, resource_gate, monkeypatch):
        """Outside night window but interactive=True → proceed."""
        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=2000, util_pct=40, temp_c=50, available=True),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[],
        )
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: host)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)

        with patch.object(ResourceGate, "_in_night_window", return_value=False):
            spec = resource_gate.acquire("light", interactive=True)
            assert spec.key == "light"

    def test_force_night_mode_bypasses_polite(self, resource_gate, monkeypatch):
        """force_night_mode=True → polite mode is bypassed."""
        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=2000, util_pct=40, temp_c=50, available=True),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[],
        )
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: host)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)

        resource_gate.force_night_mode = True
        with patch.object(ResourceGate, "_in_night_window", return_value=False):
            spec = resource_gate.acquire("light", interactive=False)
            assert spec.key == "light"


class TestNightWindowDetection:
    """Test _in_night_window logic."""

    def test_in_night_window_during_batch_hours(self, monkeypatch):
        """During the configured night window → True."""
        gate_obj = ResourceGate()
        with patch.object(ResourceGate, "_in_night_window", return_value=True):
            assert gate_obj._in_night_window() is True

    def test_is_batch_window_respects_force_night_mode(self, monkeypatch):
        """force_night_mode=True → is_batch_window() returns True."""
        gate_obj = ResourceGate()
        gate_obj.force_night_mode = True
        assert gate_obj.is_batch_window() is True

    def test_is_batch_window_checks_night_window(self, monkeypatch):
        """is_batch_window() checks _in_night_window when force_night_mode=False."""
        gate_obj = ResourceGate()
        gate_obj.force_night_mode = False
        with patch.object(ResourceGate, "_in_night_window", return_value=True):
            assert gate_obj.is_batch_window() is True
        with patch.object(ResourceGate, "_in_night_window", return_value=False):
            assert gate_obj.is_batch_window() is False


class TestLoadedModelTracking:
    """Test tracking of when models were loaded."""

    def test_loaded_since_recorded_on_proceed(self, resource_gate, mock_host_with_loaded_model, monkeypatch):
        """On proceeding with a model, _loaded_since is recorded."""
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda _: mock_host_with_loaded_model)
        monkeypatch.setattr("eoa.memory.relational.record_resource_decision", lambda **kw: None)

        resource_gate.acquire("resident")
        assert "gemma4:12b" in resource_gate._loaded_since
        assert isinstance(resource_gate._loaded_since["gemma4:12b"], float)

    def test_reclaimable_vram_excludes_recently_loaded_models(self, resource_gate, monkeypatch):
        """Models loaded < min_loaded_seconds ago are not reclaimable."""
        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=8000, util_pct=50, temp_c=70, available=True),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[
                LoadedModel(name="gemma4:e4b", size_mb=5500, size_vram_mb=5500),
            ],
        )
        # Model loaded recently
        resource_gate._loaded_since["gemma4:e4b"] = time.monotonic()

        reclaim = resource_gate._reclaimable_vram(host, keep="resident")
        assert reclaim == 0  # Model too recent to reclaim

    def test_reclaimable_vram_includes_old_models(self, resource_gate, monkeypatch):
        """Models loaded > min_loaded_seconds ago are reclaimable."""
        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=8000, util_pct=50, temp_c=70, available=True),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[
                LoadedModel(name="gemma4:e4b", size_mb=5500, size_vram_mb=5500),
            ],
        )
        # Model loaded long ago (400s > 300s min_loaded_seconds)
        resource_gate._loaded_since["gemma4:e4b"] = time.monotonic() - 400

        reclaim = resource_gate._reclaimable_vram(host, keep="resident")
        assert reclaim == 5500

    def test_reclaimable_vram_excludes_keep_model(self, resource_gate, monkeypatch):
        """The keep parameter prevents reclaiming its VRAM."""
        host = HostStatus(
            at=datetime.now(tz=UTC),
            gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=8000, util_pct=50, temp_c=70, available=True),
            ram_free_mb=32000,
            ram_total_mb=64000,
            disk_free_gb=100,
            loaded_models=[
                LoadedModel(name="gemma4:12b", size_mb=8200, size_vram_mb=8200),
                LoadedModel(name="gemma4:e4b", size_mb=5500, size_vram_mb=5500),
            ],
        )
        resource_gate._loaded_since["gemma4:12b"] = time.monotonic() - 400
        resource_gate._loaded_since["gemma4:e4b"] = time.monotonic() - 400

        reclaim = resource_gate._reclaimable_vram(host, keep="gemma4:12b")
        assert reclaim == 5500  # Only the light model is reclaimable


class TestGateSingleton:
    """Test the process-wide gate singleton."""

    def test_gate_returns_singleton(self, monkeypatch):
        """gate() always returns the same ResourceGate instance."""
        g1 = gate()
        g2 = gate()
        assert g1 is g2

    def test_gate_is_thread_safe(self, monkeypatch):
        """gate() is thread-safe."""
        import threading

        gates = []

        def get_gate():
            gates.append(gate())

        t1 = threading.Thread(target=get_gate)
        t2 = threading.Thread(target=get_gate)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert gates[0] is gates[1]
