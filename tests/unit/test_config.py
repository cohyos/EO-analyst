"""Tests for eoa.config — configuration loading and model resolution."""

from __future__ import annotations

import pytest

from eoa.config import settings
from eoa.errors import ConfigError, ModelNotAllowed


class TestSettingsLoading:
    """Test the settings() cache and YAML loading."""

    def test_settings_loads_from_config_dir(self, monkeypatch):
        """settings() loads config.yaml, models.yaml, etc. from CONFIG_DIR."""
        s = settings()
        assert s.timezone == "Asia/Jerusalem"
        assert s.schedule is not None
        assert s.schedule.night_window.start == "01:00"
        assert s.schedule.night_window.end == "06:00"

    def test_settings_has_models_registry(self, monkeypatch):
        """Registry is loaded and contains expected models."""
        s = settings()
        assert s.registry is not None
        assert "US" in s.registry.allowed_origins
        assert "gemma4_12b" in s.registry.models

    def test_settings_loads_triage_levels(self, monkeypatch):
        """Triage levels are loaded from config."""
        s = settings()
        assert s.triage.levels["red"] == 8
        assert s.triage.levels["orange"] == 6
        assert s.triage.levels["yellow"] == 4

    def test_settings_loads_watchlist(self, monkeypatch):
        """Watchlist is loaded from config."""
        s = settings()
        assert s.watchlist is not None
        assert isinstance(s.watchlist, dict)

    def test_settings_cache_clear_reloads(self, monkeypatch):
        """Calling settings.cache_clear() reloads the config."""
        s1 = settings()
        settings.cache_clear()
        s2 = settings()
        assert s1.timezone == s2.timezone

    def test_settings_lru_cache_returns_same_object(self, monkeypatch):
        """Multiple calls to settings() without cache_clear() return the same object."""
        s1 = settings()
        s2 = settings()
        assert s1 is s2


class TestModelResolution:
    """Test the model() method for resolving roles to ModelSpec."""

    def test_model_resident_exists(self, monkeypatch):
        """model('resident') returns a valid ModelSpec."""
        s = settings()
        spec = s.model("resident")
        assert spec.key in ["gemma4_12b", "dictalm3_12b"]  # One of the configured residents
        assert spec.origin in ["US", "IL"]  # Western origin
        assert spec.est_vram_mb > 0

    def test_model_light_exists(self, monkeypatch):
        """model('light') returns expected spec."""
        s = settings()
        spec = s.model("light")
        assert spec.key is not None
        assert spec.origin in ["US", "EU", "IL"]

    def test_model_embed_exists(self, monkeypatch):
        """model('embed') returns a model with dim set."""
        s = settings()
        spec = s.model("embed")
        assert spec.dim is not None
        assert isinstance(spec.dim, int)
        assert spec.dim > 0

    def test_model_nonexistent_raises_config_error(self, monkeypatch):
        """model('nonexistent') raises ConfigError."""
        s = settings()
        with pytest.raises(ConfigError, match="no model configured for role"):
            s.model("nonexistent")

    def test_model_enforce_origin_allowlist(self, monkeypatch):
        """If origin is not in allowed_origins, ModelNotAllowed is raised."""
        s = settings()
        # Inject a disallowed model into the registry
        s.registry.models["cn_model"] = s.registry.models["gemma4_12b"].__class__(
            key="cn_model",
            ollama="test:model",
            vendor="Test",
            origin="CN",
            license="MIT",
        )
        s.models["test_role"] = "cn_model"
        with pytest.raises(ModelNotAllowed, match="not allowed"):
            s.model("test_role")

    def test_embed_dim_property(self, monkeypatch):
        """embed_dim property returns the dimension of the embedding model."""
        s = settings()
        dim = s.embed_dim
        assert isinstance(dim, int)
        assert dim > 0

    def test_embed_dim_missing_raises_error(self, monkeypatch):
        """embed_dim raises ConfigError if the embed model has no dim."""
        from eoa.config import ModelsRegistry, ScheduleCfg, Settings, TimeWindow

        # Create a registry with an embed model without dim (dict format for validator)
        registry = ModelsRegistry(
            allowed_origins=["US"],
            allowed_formats=["gguf"],
            models={
                "no_dim_embed": {
                    "ollama": "test",
                    "vendor": "Test",
                    "origin": "US",
                    "license": "MIT",
                    "dim": None,  # Missing dim
                },
            },
        )

        s = Settings(
            timezone="UTC",
            schedule=ScheduleCfg(night_window=TimeWindow(start="01:00", end="06:00")),
            registry=registry,
            models={"embed": "no_dim_embed"},
        )

        with pytest.raises(ConfigError, match="has no dim"):
            _ = s.embed_dim

    def test_has_model_returns_true_when_configured(self, monkeypatch):
        """has_model('resident') returns True if configured."""
        s = settings()
        assert s.has_model("resident")
        assert s.has_model("embed")

    def test_has_model_returns_false_when_not_configured(self, monkeypatch):
        """has_model('nonexistent') returns False."""
        s = settings()
        assert not s.has_model("nonexistent")


class TestResourcesConfiguration:
    """Test resource limits and settings."""

    def test_resources_vram_configuration(self, monkeypatch):
        """Resource VRAM limits are loaded."""
        s = settings()
        assert s.resources.vram_total_mb == 12227
        assert s.resources.vram_safety_margin_mb == 1200
        assert s.resources.min_free_vram_mb["resident"] == 9500
        assert s.resources.min_free_vram_mb["light"] == 6000

    def test_resources_gpu_temperature_thresholds(self, monkeypatch):
        """GPU temperature pause/stop thresholds are set."""
        s = settings()
        assert s.resources.gpu_temp_pause_c == 83
        assert s.resources.gpu_temp_stop_c == 88

    def test_resources_queue_backoff(self, monkeypatch):
        """Queue backoff sequence is configured."""
        s = settings()
        assert s.resources.queue_backoff_seconds == [5, 10, 30, 60]
        assert s.resources.queue_timeout_min == 20

    def test_resources_polite_mode(self, monkeypatch):
        """Polite mode settings are present."""
        s = settings()
        assert s.resources.polite_mode.external_gpu_util_threshold == 25
        assert s.resources.polite_mode.enabled_outside_night_window is True


class TestDeepSearchConfiguration:
    """Test deep search configuration."""

    def test_deep_search_budgets(self, monkeypatch):
        """Deep search budgets are configured."""
        s = settings()
        assert s.deep_search.max_queries == 15
        assert s.deep_search.max_pages == 30
        assert s.deep_search.per_investigation_timeout_min == 25

    def test_deep_search_languages(self, monkeypatch):
        """Primary and secondary languages are configured."""
        s = settings()
        assert "he" in s.deep_search.langs_primary
        assert "en" in s.deep_search.langs_primary
        assert "ru" in s.deep_search.langs_secondary

    def test_deep_search_confidence_stop(self, monkeypatch):
        """Confidence threshold is set."""
        s = settings()
        assert s.deep_search.confidence_stop == 0.8


class TestConfigEnvironmentOverrides:
    """Test that environment variables can override config."""

    def test_database_url_from_env(self, monkeypatch):
        """database_url property respects DATABASE_URL env var."""
        s = settings()
        # Default value (if no env var)
        default_url = s.database_url
        assert "postgresql" in default_url or "change-me" in default_url

        # With env var
        test_url = "postgresql://user:pass@host:5432/db"
        monkeypatch.setenv("DATABASE_URL", test_url)
        settings.cache_clear()
        s2 = settings()
        assert s2.database_url == test_url

    def test_ollama_url_from_env(self, monkeypatch):
        """ollama_url property respects OLLAMA_URL env var."""
        s = settings()
        default = s.ollama_url
        assert "11434" in default or "127.0.0.1" in default

        test_url = "http://remote-ollama:11434"
        monkeypatch.setenv("OLLAMA_URL", test_url)
        settings.cache_clear()
        s2 = settings()
        assert s2.ollama_url == test_url
