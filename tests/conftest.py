"""Shared pytest fixtures for all tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from eoa.config import ModelSpec, ModelsRegistry, Settings, settings
from eoa.resources.gpu import GpuStatus, HostStatus, LoadedModel


@pytest.fixture
def minimal_settings() -> Settings:
    """A minimal Settings object for tests that don't use the real config files."""
    return Settings(
        timezone="Asia/Jerusalem",
        schedule=MagicMock(),
        registry=ModelsRegistry(
            allowed_origins=["US", "EU", "IL"],
            allowed_formats=["gguf"],
            models={
                "resident": ModelSpec(
                    key="resident",
                    ollama="gemma4:12b",
                    vendor="Google",
                    origin="US",
                    license="Apache-2.0",
                    est_vram_mb=8200,
                ),
                "light": ModelSpec(
                    key="light",
                    ollama="gemma4:e4b",
                    vendor="Google",
                    origin="US",
                    license="Apache-2.0",
                    est_vram_mb=5500,
                ),
                "embed": ModelSpec(
                    key="embed",
                    ollama="multilingual-e5-large",
                    vendor="Microsoft",
                    origin="US",
                    license="MIT",
                    dim=1024,
                    est_vram_mb=1300,
                ),
                "guard_l1": ModelSpec(
                    key="guard_l1",
                    ollama="prompt_injection_deberta",
                    vendor="Microsoft",
                    origin="US",
                    license="MIT",
                    runtime="onnx",
                    est_vram_mb=0,
                ),
                "disallowed": ModelSpec(
                    key="disallowed",
                    ollama="qwen:7b",
                    vendor="Alibaba",
                    origin="CN",  # Not allowed
                    license="MIT",
                    est_vram_mb=5000,
                ),
            },
        ),
        models={
            "resident": "resident",
            "light": "light",
            "embed": "embed",
            "guard_l1": "guard_l1",
        },
        triage=MagicMock(levels={"red": 8, "orange": 6, "yellow": 4}),
        deep_search=MagicMock(
            max_queries=15,
            max_pages=30,
            per_investigation_timeout_min=25,
            langs_primary=["he", "en"],
            langs_secondary=["ru", "zh", "fr", "de"],
            confidence_stop=0.8,
        ),
        resources=MagicMock(
            vram_total_mb=12227,
            vram_safety_margin_mb=1200,
            min_free_vram_mb={"resident": 9500, "light": 6000, "embed": 1500},
            min_free_disk_gb=20,
            gpu_temp_pause_c=83,
            gpu_temp_stop_c=88,
            queue_backoff_seconds=[5, 10, 30, 60],
            queue_timeout_min=20,
            min_loaded_seconds=300,
            polite_mode=MagicMock(
                external_gpu_util_threshold=25,
                enabled_outside_night_window=True,
            ),
        ),
        watchlist={"companies": [], "programs": []},
    )


@pytest.fixture
def mock_host_status() -> HostStatus:
    """A default HostStatus with sufficient resources."""
    return HostStatus(
        at=datetime.now(tz=UTC),
        gpu=GpuStatus(
            vram_total_mb=12227,
            vram_used_mb=2000,
            util_pct=10,
            temp_c=50,
            available=True,
        ),
        ram_free_mb=32000,
        ram_total_mb=64000,
        disk_free_gb=100,
        loaded_models=[],
    )


@pytest.fixture
def mock_host_status_low_vram() -> HostStatus:
    """HostStatus with limited VRAM."""
    return HostStatus(
        at=datetime.now(tz=UTC),
        gpu=GpuStatus(
            vram_total_mb=12227,
            vram_used_mb=10000,  # Only 2GB free
            util_pct=50,
            temp_c=75,
            available=True,
        ),
        ram_free_mb=8500,
        ram_total_mb=64000,
        disk_free_gb=100,
        loaded_models=[],
    )


@pytest.fixture
def mock_host_status_hot_gpu() -> HostStatus:
    """HostStatus with high GPU temperature."""
    return HostStatus(
        at=datetime.now(tz=UTC),
        gpu=GpuStatus(
            vram_total_mb=12227,
            vram_used_mb=2000,
            util_pct=80,
            temp_c=90,  # Above stop threshold of 88
            available=True,
        ),
        ram_free_mb=32000,
        ram_total_mb=64000,
        disk_free_gb=100,
        loaded_models=[],
    )


@pytest.fixture
def mock_host_status_low_disk() -> HostStatus:
    """HostStatus with low disk space."""
    return HostStatus(
        at=datetime.now(tz=UTC),
        gpu=GpuStatus(
            vram_total_mb=12227,
            vram_used_mb=2000,
            util_pct=10,
            temp_c=50,
            available=True,
        ),
        ram_free_mb=32000,
        ram_total_mb=64000,
        disk_free_gb=15,  # Below 20 GB minimum
        loaded_models=[],
    )


@pytest.fixture
def mock_host_status_no_gpu() -> HostStatus:
    """HostStatus with no GPU available."""
    return HostStatus(
        at=datetime.now(tz=UTC),
        gpu=GpuStatus(
            vram_total_mb=0,
            vram_used_mb=0,
            util_pct=0,
            temp_c=0,
            available=False,
            error="No NVIDIA GPU detected",
        ),
        ram_free_mb=32000,
        ram_total_mb=64000,
        disk_free_gb=100,
        loaded_models=[],
    )


@pytest.fixture
def mock_host_with_loaded_model() -> HostStatus:
    """HostStatus with a model already loaded."""
    return HostStatus(
        at=datetime.now(tz=UTC),
        gpu=GpuStatus(
            vram_total_mb=12227,
            vram_used_mb=8500,  # Model is loaded
            util_pct=40,
            temp_c=65,
            available=True,
        ),
        ram_free_mb=32000,
        ram_total_mb=64000,
        disk_free_gb=100,
        loaded_models=[
            LoadedModel(
                name="gemma4:12b",
                size_mb=8200,
                size_vram_mb=8200,
            ),
        ],
    )


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """Clear the settings cache before and after each test."""
    settings.cache_clear()
    yield
    settings.cache_clear()


@pytest.fixture(autouse=True)
def _reports_to_tmp(tmp_path, monkeypatch, clear_settings_cache):
    """Never let a test write report files into output/reports (see ReportCfg.model_post_init).

    ``ReportCfg.model_post_init`` reads ``EOA_REPORT_OUTPUT_DIR`` once, when ``settings()`` builds
    the model, so the env var must be in place *before* anything fills the ``lru_cache``. Any
    autouse fixture that calls ``settings()`` (``_ask_entailment_off``) therefore has to depend on
    this fixture -- pytest does not order same-scope autouse fixtures by definition order -- and the
    cache is cleared here again in case something earlier in the setup chain already populated it.
    """
    monkeypatch.setenv("EOA_REPORT_OUTPUT_DIR", str(tmp_path / "reports"))
    settings.cache_clear()
    yield


@pytest.fixture
def mock_database(monkeypatch):
    """Mock the database module to prevent connection attempts."""

    class MockConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, *args, **kwargs):
            return MagicMock(fetchone=MagicMock(return_value=None))

    def mock_connection():
        return MockConnection()

    monkeypatch.setattr("eoa.db.connection", mock_connection)
    return mock_connection


@pytest.fixture(autouse=True)
def _search_isolation(monkeypatch):
    """Round 4: the search provider keeps process-wide circuit breakers and a file cache; without
    this every test file inherits whatever state an earlier file left (three order-dependent
    failures in the full suite on 2026-09-06). Fresh circuits + no cache reads for every test;
    the search tests that exercise the cache opt back in explicitly."""
    monkeypatch.setenv("EOA_SEARCH_NO_CACHE", "1")
    try:
        from eoa.search import circuit

        circuit.reset_all()
    except Exception:
        pass
    yield
    try:
        from eoa.search import circuit

        circuit.reset_all()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _source_reliability_isolation(monkeypatch):
    """Round 7: the appendix reliability column looks up ``sources.reliability`` once per process;
    unit tests must never reach the live DB for it, and a test fixture item with no
    ``reliability`` must render "—" regardless of what the developer's DB holds."""
    try:
        from eoa.report import docx_builder
    except Exception:
        yield
        return
    monkeypatch.setattr(docx_builder, "_SOURCE_RELIABILITY_CACHE", {}, raising=False)
    yield


@pytest.fixture(autouse=True)
def _ask_entailment_off(monkeypatch, clear_settings_cache, _reports_to_tmp):
    """R7-chat: `ask.entailment_check` is ON in production (user decision 2026-09-07) but the pass
    calls the light LLM role; unit tests must never make that call -- force it off here.

    Depends on ``_reports_to_tmp`` because the ``settings()`` call below is what populates the
    cache for the test: the report output-dir override must already be in the environment."""
    try:
        from eoa.config import settings

        cfg = settings()
        ask = getattr(cfg, "ask", None)
        if ask is not None:
            monkeypatch.setattr(ask, "entailment_check", False, raising=False)
    except Exception:
        pass
    yield
