"""Tests for the U8 provider-dispatch hook in eoa.llm.ollama_client (docs/adr/005-cloud-llm-cli.md).

These never touch the network or a real subprocess: `chat()`'s cloud branch is exercised by
monkeypatching `eoa.llm.providers.cli.CliProvider.chat` directly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from eoa.errors import ProviderUnavailable
from eoa.llm import ollama_client as oc
from eoa.llm.providers.base import ProviderResult


def _fake_settings(*, allow_cloud=True, interactive_default="ollama"):
    return SimpleNamespace(
        llm_providers=SimpleNamespace(allow_cloud=allow_cloud, interactive_default=interactive_default, cli={}, timeout_s=120),
        models={"resident": "dictalm3_12b"},
    )


class TestResolveProvider:
    def test_explicit_provider_wins(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings(interactive_default="ollama"))
        monkeypatch.delenv("EOA_PIPELINE", raising=False)
        assert oc._resolve_provider("agy:gemini-3.8-flash-medium") == "agy:gemini-3.8-flash-medium"

    def test_none_falls_back_to_configured_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings(interactive_default="claude:claude-sonnet-5"))
        monkeypatch.delenv("EOA_PIPELINE", raising=False)
        assert oc._resolve_provider(None) == "claude:claude-sonnet-5"

    def test_pipeline_env_forces_ollama_even_with_explicit_provider(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings(interactive_default="codex"))
        monkeypatch.setenv("EOA_PIPELINE", "1")
        assert oc._resolve_provider("agy:gemini-3.8-flash-medium") == "ollama"
        assert oc._resolve_provider(None) == "ollama"

    def test_empty_default_falls_back_to_ollama(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings(interactive_default=""))
        monkeypatch.delenv("EOA_PIPELINE", raising=False)
        assert oc._resolve_provider(None) == "ollama"


class TestResolveProviderInfo:
    def test_ollama_returns_resident_role(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings())
        monkeypatch.delenv("EOA_PIPELINE", raising=False)
        kind, model = oc.resolve_provider_info(None)
        assert kind == "ollama"
        assert model == "dictalm3_12b"

    def test_cloud_with_explicit_model(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings())
        monkeypatch.delenv("EOA_PIPELINE", raising=False)
        kind, model = oc.resolve_provider_info("agy:gemini-3.8-flash-medium")
        assert (kind, model) == ("agy", "gemini-3.8-flash-medium")

    def test_cloud_without_model_uses_first_listed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings())
        monkeypatch.delenv("EOA_PIPELINE", raising=False)
        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider.list_models", lambda self: ["default"])
        kind, model = oc.resolve_provider_info("codex")
        assert (kind, model) == ("codex", "default")

    def test_pipeline_gate_overrides_cloud_request(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings())
        monkeypatch.setenv("EOA_PIPELINE", "1")
        kind, _ = oc.resolve_provider_info("agy:gemini-3.8-flash-medium")
        assert kind == "ollama"


class TestChatDispatch:
    def test_chat_dispatches_to_cli_provider(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings())
        monkeypatch.delenv("EOA_PIPELINE", raising=False)
        monkeypatch.setattr(
            "eoa.llm.providers.cli.CliProvider.chat",
            lambda self, messages, **kw: ProviderResult(
                content="PONG", model="gemini-3.8-flash-medium", provider="agy", duration_ms=10, prompt_chars=5, usage={}
            ),
        )
        # log_llm_call touches the DB -- swallow it so this stays a pure unit test.
        monkeypatch.setattr(oc, "_log_cloud_call", lambda **kw: None)

        res = oc.chat("light", [{"role": "user", "content": "ping"}], provider="agy:gemini-3.8-flash-medium")
        assert res.content == "PONG"
        assert res.model == "agy:gemini-3.8-flash-medium"
        assert res.tool_calls == []

    def test_chat_never_calls_gate_for_cloud_provider(self, monkeypatch: pytest.MonkeyPatch):
        """The resource gate (VRAM/GPU) is Ollama-only; a cloud call must never touch it."""
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings())
        monkeypatch.delenv("EOA_PIPELINE", raising=False)
        monkeypatch.setattr(
            "eoa.llm.providers.cli.CliProvider.chat",
            lambda self, messages, **kw: ProviderResult(content="x", model="m", provider="agy"),
        )
        monkeypatch.setattr(oc, "_log_cloud_call", lambda **kw: None)

        def boom(*a, **k):
            raise AssertionError("gate().acquire() must not be called for a cloud provider")

        monkeypatch.setattr(oc, "gate", lambda: SimpleNamespace(acquire=boom))
        oc.chat("light", [{"role": "user", "content": "ping"}], provider="agy")

    def test_allow_cloud_false_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings(allow_cloud=False))
        monkeypatch.delenv("EOA_PIPELINE", raising=False)
        with pytest.raises(ProviderUnavailable):
            oc.chat("light", [{"role": "user", "content": "ping"}], provider="agy")

    def test_pipeline_env_forces_ollama_path(self, monkeypatch: pytest.MonkeyPatch):
        """Under EOA_PIPELINE=1, a `provider=` argument is silently ignored -- the call falls
        through to the ordinary Ollama path (and therefore *would* hit the gate/HTTP client,
        which this test doesn't stub -- so it asserts dispatch never reaches the CLI branch)."""
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings())
        monkeypatch.setenv("EOA_PIPELINE", "1")

        def boom(*a, **k):
            raise AssertionError("cloud CLI must never be reached when EOA_PIPELINE=1")

        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider.chat", boom)
        monkeypatch.setattr(oc, "gate", lambda: SimpleNamespace(acquire=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("stop-here-ollama-path-reached"))))
        with pytest.raises(RuntimeError, match="stop-here-ollama-path-reached"):
            oc.chat("light", [{"role": "user", "content": "ping"}], provider="agy")


class TestChatStreamDispatch:
    def test_cloud_provider_chunks_full_content(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings())
        monkeypatch.delenv("EOA_PIPELINE", raising=False)
        monkeypatch.setattr(
            "eoa.llm.providers.cli.CliProvider.chat",
            lambda self, messages, **kw: ProviderResult(content="x" * 50, model="m", provider="agy"),
        )
        monkeypatch.setattr(oc, "_log_cloud_call", lambda **kw: None)

        chunks = list(oc.chat_stream("resident", [{"role": "user", "content": "hi"}], provider="agy"))
        assert "".join(chunks) == "x" * 50
        assert len(chunks) > 1  # actually chunked, not one giant blob


class TestChatStructuredProviderThreading:
    def test_provider_passed_through_to_chat(self, monkeypatch: pytest.MonkeyPatch):
        from pydantic import BaseModel

        class Out(BaseModel):
            ok: bool

        seen_providers = []

        def fake_chat(role, msgs, **kw):
            seen_providers.append(kw.get("provider"))
            return oc.ChatResult(content='{"ok": true}')

        monkeypatch.setattr(oc, "chat", fake_chat)
        result = oc.chat_structured("light", Out, [{"role": "user", "content": "hi"}], provider="claude:claude-sonnet-5")
        assert result.ok is True
        assert seen_providers == ["claude:claude-sonnet-5"]
