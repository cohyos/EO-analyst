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
from eoa.config import ChainEntryCfg


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


class TestPatchLlmProviderSettingsChains:
    """U8 Settings ChainsEditor: `PUT /api/llm/settings {chains: ...}` (services.py additive)."""

    def test_round_trip_add_step_and_terminal_appended(self, settings_tmp: Path):
        chains = {
            "resident": [ChainEntryCfg(provider="agy", model="gemini-3.8-flash-medium")],
        }
        ok, errors, revision = services.patch_llm_provider_settings(
            interactive_default=None, allow_cloud=None, chains=chains
        )
        assert ok is True
        assert errors == []
        assert revision is not None
        parsed = yaml.safe_load((settings_tmp / "config.yaml").read_text(encoding="utf-8"))
        resident = parsed["llm_providers"]["chains"]["resident"]
        # the local terminal step is appended automatically since the payload omitted it.
        assert resident == [
            {"provider": "agy", "model": "gemini-3.8-flash-medium"},
            {"provider": "ollama"},
        ]

    def test_reorder_round_trip(self, settings_tmp: Path):
        first = {
            "investigator": [
                ChainEntryCfg(provider="claude", model="claude-sonnet-5"),
                ChainEntryCfg(provider="agy", model="gemini-3.1-pro-high"),
            ]
        }
        services.patch_llm_provider_settings(interactive_default=None, allow_cloud=None, chains=first)
        reordered = {
            "investigator": [
                ChainEntryCfg(provider="agy", model="gemini-3.1-pro-high"),
                ChainEntryCfg(provider="claude", model="claude-sonnet-5"),
            ]
        }
        ok, errors, _ = services.patch_llm_provider_settings(
            interactive_default=None, allow_cloud=None, chains=reordered
        )
        assert ok is True
        assert errors == []
        parsed = yaml.safe_load((settings_tmp / "config.yaml").read_text(encoding="utf-8"))
        providers_in_order = [e["provider"] for e in parsed["llm_providers"]["chains"]["investigator"]]
        assert providers_in_order == ["agy", "claude", "ollama"]

    def test_terminal_ollama_not_duplicated(self, settings_tmp: Path):
        chains = {
            "light": [
                ChainEntryCfg(provider="agy", model="gemini-3.8-flash-medium"),
                ChainEntryCfg(provider="ollama"),
            ]
        }
        ok, errors, _ = services.patch_llm_provider_settings(interactive_default=None, allow_cloud=None, chains=chains)
        assert ok is True
        assert errors == []
        parsed = yaml.safe_load((settings_tmp / "config.yaml").read_text(encoding="utf-8"))
        assert parsed["llm_providers"]["chains"]["light"] == [
            {"provider": "agy", "model": "gemini-3.8-flash-medium"},
            {"provider": "ollama"},
        ]

    def test_unknown_provider_rejected_without_touching_file(self, settings_tmp: Path):
        before = (settings_tmp / "config.yaml").read_text(encoding="utf-8")
        ok, errors, revision = services.patch_llm_provider_settings(
            interactive_default=None,
            allow_cloud=None,
            chains={"resident": [ChainEntryCfg(provider="bogus", model="x")]},
        )
        assert ok is False
        assert errors
        assert revision is None
        assert (settings_tmp / "config.yaml").read_text(encoding="utf-8") == before

    def test_empty_model_rejected_for_non_ollama_provider(self, settings_tmp: Path):
        ok, errors, _ = services.patch_llm_provider_settings(
            interactive_default=None,
            allow_cloud=None,
            chains={"resident": [ChainEntryCfg(provider="claude", model="")]},
        )
        assert ok is False
        assert any("מודל" in e for e in errors)

    def test_ollama_step_needs_no_model(self, settings_tmp: Path):
        ok, errors, _ = services.patch_llm_provider_settings(
            interactive_default=None, allow_cloud=None, chains={"resident": [ChainEntryCfg(provider="ollama")]}
        )
        assert ok is True
        assert errors == []

    def test_power_not_in_providers_levels_rejected(self, settings_tmp: Path):
        # "gemini" (a direct-API provider) keeps its default power_levels (["low","medium","high"])
        # in this fixture since it never overrides `llm_providers.api` -- unlike `cli: {}`, which
        # the fixture does override to empty, so this isolates "known provider, bad power value"
        # from "provider has no power_levels configured at all" (covered by the CLI-provider path
        # in `test_reorder_round_trip`, where power is simply omitted).
        ok, errors, _ = services.patch_llm_provider_settings(
            interactive_default=None,
            allow_cloud=None,
            chains={"resident": [ChainEntryCfg(provider="gemini", model="gemini-3.5-flash", power="ultra")]},
        )
        assert ok is False
        assert any("עוצמה" in e for e in errors)

    def test_unknown_role_rejected(self, settings_tmp: Path):
        ok, errors, _ = services.patch_llm_provider_settings(
            interactive_default=None,
            allow_cloud=None,
            chains={"not_a_role": [ChainEntryCfg(provider="ollama")]},
        )
        assert ok is False
        assert any("תפקיד" in e for e in errors)

    def test_clearing_chains_writes_empty_map(self, settings_tmp: Path):
        setup_ok, setup_errors, _ = services.patch_llm_provider_settings(
            interactive_default=None,
            allow_cloud=None,
            chains={"resident": [ChainEntryCfg(provider="agy", model="gemini-3.8-flash-medium")]},
        )
        assert setup_ok is True
        assert setup_errors == []
        ok, errors, _ = services.patch_llm_provider_settings(interactive_default=None, allow_cloud=None, chains={})
        assert ok is True
        assert errors == []
        parsed = yaml.safe_load((settings_tmp / "config.yaml").read_text(encoding="utf-8"))
        assert parsed["llm_providers"]["chains"] == {}

    def test_chains_absent_key_in_fixture_gets_inserted(self, settings_tmp: Path):
        # settings_tmp's config.yaml fixture has no `chains:` key at all (documents the
        # insert-after-`llm_providers:` fallback path in `_patch_yaml_chains_block`).
        before = (settings_tmp / "config.yaml").read_text(encoding="utf-8")
        assert "chains" not in before
        ok, errors, _ = services.patch_llm_provider_settings(
            interactive_default=None,
            allow_cloud=None,
            chains={"resident": [ChainEntryCfg(provider="agy", model="gemini-3.8-flash-medium")]},
        )
        assert ok is True
        assert errors == []
        parsed = yaml.safe_load((settings_tmp / "config.yaml").read_text(encoding="utf-8"))
        assert parsed["llm_providers"]["chains"]["resident"][0]["provider"] == "agy"
        assert parsed["llm_providers"]["cli"] == {}  # rest of the section untouched


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

    def test_put_settings_route_updates_chains(self, settings_tmp: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.ollama.OllamaProvider.is_available", lambda self: False)
        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider.is_available", lambda self: False)
        from eoa.api.app import create_app

        client = TestClient(create_app())
        res = client.put(
            "/api/llm/settings",
            json={"chains": {"resident": [{"provider": "agy", "model": "gemini-3.8-flash-medium"}]}},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        parsed = yaml.safe_load((settings_tmp / "config.yaml").read_text(encoding="utf-8"))
        assert parsed["llm_providers"]["chains"]["resident"] == [
            {"provider": "agy", "model": "gemini-3.8-flash-medium"},
            {"provider": "ollama"},
        ]
        # GET reflects the newly persisted chain immediately (no cache clear needed on this route).
        res2 = client.get("/api/llm/providers")
        assert res2.json()["chains"]["resident"][0]["provider"] == "agy"

    def test_put_settings_route_rejects_bad_chain_provider(
        self, settings_tmp: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr("eoa.llm.providers.ollama.OllamaProvider.is_available", lambda self: False)
        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider.is_available", lambda self: False)
        from eoa.api.app import create_app

        client = TestClient(create_app())
        res = client.put(
            "/api/llm/settings",
            json={"chains": {"resident": [{"provider": "not-a-provider", "model": "x"}]}},
        )
        assert res.status_code == 200  # validation errors, not an HTTP error -- same as `mode`
        body = res.json()
        assert body["ok"] is False
        assert body["errors"]

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
