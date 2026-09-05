"""Tests for the U8 LLM provider settings surface (agent/eoa/api/routes/llm.py +
`eoa.api.services.{list_llm_providers,patch_llm_provider_settings,_patch_yaml_scalar}`).

Follows the `settings_tmp` fixture pattern in tests/unit/test_settings_api.py: service-level
tests write into a throwaway CONFIG_DIR, never the repo's real config/*.yaml.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from eoa import config as eoa_config
from eoa.api import services


@pytest.fixture()
def settings_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / "watchlist.yaml").write_text("companies: []\n", encoding="utf-8")
    (tmp_path / "taxonomy.yaml").write_text("domains:\n  airborne_pods:\n    label: test\n", encoding="utf-8")
    (tmp_path / "models.yaml").write_text(
        "allowed_origins: [US]\nallowed_formats: [gguf]\nmodels: {}\n", encoding="utf-8"
    )
    (tmp_path / "sources.yaml").write_text("sources: []\n", encoding="utf-8")
    config_yaml = (
        "schedule: { night_window: { start: '01:00', end: '06:00' } }\n"
        "llm_providers:\n"
        "  mode: local\n"
        "  allow_cloud: true\n"
        '  interactive_default: "ollama"\n'
        "  timeout_s: 120\n"
        "  cli: {}\n"
    )
    (tmp_path / "config.yaml").write_text(config_yaml, encoding="utf-8")
    monkeypatch.setattr(services, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        services,
        "_SETTINGS_PATHS",
        {name: tmp_path / fname for name, fname in services.SETTINGS_FILES.items()},
    )
    # `list_llm_providers()` reads the live `eoa_config.settings()` singleton (so the Settings
    # "מודלים" card reflects what's actually on disk) -- redirect its loader at the source too,
    # not just the raw-YAML read/write paths `_settings_path` uses.
    monkeypatch.setattr(eoa_config, "CONFIG_DIR", tmp_path)
    eoa_config.settings.cache_clear()
    yield tmp_path
    eoa_config.settings.cache_clear()


class TestPatchYamlScalar:
    def test_replaces_value_preserving_rest(self):
        text = "a: 1\ninteractive_default: \"ollama\"\nb: 2\n"
        out = services._patch_yaml_scalar(text, "interactive_default", '"agy:gemini-3.8-flash-medium"')
        assert out == 'a: 1\ninteractive_default: "agy:gemini-3.8-flash-medium"\nb: 2\n'

    def test_unknown_key_raises(self):
        with pytest.raises(KeyError):
            services._patch_yaml_scalar("a: 1\n", "does_not_exist", "true")

    def test_only_first_occurrence_touched(self):
        # allow_cloud is unique in the real config.yaml; this documents the (count=1) behavior.
        text = "allow_cloud: true\nallow_cloud: true\n"
        out = services._patch_yaml_scalar(text, "allow_cloud", "false")
        assert out.splitlines()[0] == "allow_cloud: false"
        assert out.splitlines()[1] == "allow_cloud: true"


class TestPatchLlmProviderSettings:
    def test_updates_interactive_default(self, settings_tmp: Path):
        ok, errors, revision = services.patch_llm_provider_settings(
            interactive_default="claude:claude-sonnet-5", allow_cloud=None
        )
        assert ok is True
        assert errors == []
        assert revision is not None
        parsed = yaml.safe_load((settings_tmp / "config.yaml").read_text(encoding="utf-8"))
        assert parsed["llm_providers"]["interactive_default"] == "claude:claude-sonnet-5"
        assert parsed["llm_providers"]["allow_cloud"] is True  # untouched

    def test_updates_allow_cloud(self, settings_tmp: Path):
        ok, _errors, _ = services.patch_llm_provider_settings(interactive_default=None, allow_cloud=False)
        assert ok is True
        parsed = yaml.safe_load((settings_tmp / "config.yaml").read_text(encoding="utf-8"))
        assert parsed["llm_providers"]["allow_cloud"] is False

    def test_invalid_provider_value_rejected_by_full_validation(self, settings_tmp: Path):
        # interactive_default is a free-form string in the pydantic model (validated at call
        # time, not at write time) -- this just documents that a structurally-broken result
        # (e.g. quoting bug) would be caught by the same EOASettings(**parsed) validation the
        # generic settings editor uses, since patch goes through write_settings_yaml unchanged.
        ok, _errors, _ = services.patch_llm_provider_settings(interactive_default='has "quote', allow_cloud=None)
        assert ok is True  # a string value with an escaped quote is still valid YAML/pydantic
        parsed = yaml.safe_load((settings_tmp / "config.yaml").read_text(encoding="utf-8"))
        assert parsed["llm_providers"]["interactive_default"] == 'has "quote'

    def test_updates_mode(self, settings_tmp: Path):
        ok, errors, _ = services.patch_llm_provider_settings(interactive_default=None, allow_cloud=None, mode="cloud")
        assert ok is True
        assert errors == []
        parsed = yaml.safe_load((settings_tmp / "config.yaml").read_text(encoding="utf-8"))
        assert parsed["llm_providers"]["mode"] == "cloud"

    def test_invalid_mode_rejected_without_touching_file(self, settings_tmp: Path):
        before = (settings_tmp / "config.yaml").read_text(encoding="utf-8")
        ok, errors, revision = services.patch_llm_provider_settings(
            interactive_default=None, allow_cloud=None, mode="bogus"
        )
        assert ok is False
        assert errors
        assert revision is None
        assert (settings_tmp / "config.yaml").read_text(encoding="utf-8") == before

    def test_stale_revision_conflicts(self, settings_tmp: Path):
        with pytest.raises(services.SettingsConflict):
            services.patch_llm_provider_settings(
                interactive_default="agy", allow_cloud=None, expected_revision="stale-hash"
            )


class TestListLlmProviders:
    def test_shape_and_ollama_always_present(self, settings_tmp: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.ollama.OllamaProvider.is_available", lambda self: True)
        monkeypatch.setattr("eoa.llm.providers.ollama.OllamaProvider.list_models", lambda self: ["resident"])
        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider.is_available", lambda self: False)
        out = services.list_llm_providers()
        assert out["allow_cloud"] is True
        assert out["interactive_default"] == "ollama"
        assert out["mode"] == "local"
        assert out["chains"] == {}
        ids = [p["id"] for p in out["providers"]]
        assert ids[0] == "ollama"
        assert set(ids) == {"ollama", "agy", "claude", "codex", "anthropic", "gemini", "openai"}
        ollama_entry = next(p for p in out["providers"] if p["id"] == "ollama")
        assert ollama_entry["kind"] == "local"
        assert ollama_entry["available"] is True
        # U8-ו: API providers are always "לא מוגדר" (unavailable) without a real env key -- never
        # error out, and never surface anything beyond the availability boolean.
        api_entries = {p["id"]: p for p in out["providers"] if p["kind"] == "api"}
        assert set(api_entries) == {"anthropic", "gemini", "openai"}
        for entry in api_entries.values():
            assert entry["available"] is False
            assert "key_env" in entry
            assert entry["power_levels"]
        # U8-ג: CLI providers also carry power_levels (agy/claude --effort, codex -c override).
        cloud_entries = {p["id"]: p for p in out["providers"] if p["kind"] == "cloud"}
        assert set(cloud_entries) == {"agy", "claude", "codex"}
        for entry in cloud_entries.values():
            assert entry["power_levels"] == ["low", "medium", "high"]

    def test_cloud_hidden_when_allow_cloud_false(self, settings_tmp: Path):
        (settings_tmp / "config.yaml").write_text(
            (settings_tmp / "config.yaml").read_text(encoding="utf-8").replace("allow_cloud: true", "allow_cloud: false"),
            encoding="utf-8",
        )
        eoa_config.settings.cache_clear()
        out = services.list_llm_providers()
        assert out["allow_cloud"] is False
        assert [p["id"] for p in out["providers"]] == ["ollama"]


class TestLlmRoute:
    def test_get_providers_route(self, settings_tmp: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.ollama.OllamaProvider.is_available", lambda self: False)
        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider.is_available", lambda self: False)
        from eoa.api.app import create_app

        client = TestClient(create_app())
        res = client.get("/api/llm/providers")
        assert res.status_code == 200
        body = res.json()
        assert body["interactive_default"] == "ollama"
        assert any(p["id"] == "ollama" for p in body["providers"])

    def test_put_settings_route_updates_default(self, settings_tmp: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.ollama.OllamaProvider.is_available", lambda self: False)
        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider.is_available", lambda self: False)
        from eoa.api.app import create_app

        client = TestClient(create_app())
        res = client.put("/api/llm/settings", json={"interactive_default": "agy:gemini-3.8-flash-medium"})
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        parsed = yaml.safe_load((settings_tmp / "config.yaml").read_text(encoding="utf-8"))
        assert parsed["llm_providers"]["interactive_default"] == "agy:gemini-3.8-flash-medium"

    def test_put_settings_route_conflict(self, settings_tmp: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.ollama.OllamaProvider.is_available", lambda self: False)
        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider.is_available", lambda self: False)
        from eoa.api.app import create_app

        client = TestClient(create_app())
        res = client.put(
            "/api/llm/settings",
            json={"interactive_default": "agy"},
            headers={"If-Match": '"stale"'},
        )
        assert res.status_code == 409

    def test_put_settings_route_updates_mode(self, settings_tmp: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.ollama.OllamaProvider.is_available", lambda self: False)
        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider.is_available", lambda self: False)
        from eoa.api.app import create_app

        client = TestClient(create_app())
        res = client.put("/api/llm/settings", json={"mode": "cloud"})
        assert res.status_code == 200
        assert res.json()["ok"] is True
        parsed = yaml.safe_load((settings_tmp / "config.yaml").read_text(encoding="utf-8"))
        assert parsed["llm_providers"]["mode"] == "cloud"

    def test_get_calls_route(self, settings_tmp: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "eoa.memory.relational.summarize_llm_calls",
            lambda since_hours: {"since_hours": since_hours, "providers": [], "totals": {}},
        )
        from eoa.api.app import create_app

        client = TestClient(create_app())
        res = client.get("/api/llm/calls?since=48h")
        assert res.status_code == 200
        assert res.json()["since_hours"] == 48
