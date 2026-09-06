"""Tests for the U8 provider-dispatch hook in eoa.llm.ollama_client (docs/adr/005-cloud-llm-cli.md).

These never touch the network or a real subprocess: `chat()`'s cloud branch is exercised by
monkeypatching `eoa.llm.providers.cli.CliProvider.chat` directly.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from eoa.config import ChainEntryCfg
from eoa.errors import ProviderUnavailable
from eoa.llm import ollama_client as oc
from eoa.llm.providers.base import ProviderResult


def _fake_settings(*, allow_cloud=True, interactive_default="ollama", mode="local", chains=None):
    """A minimal stand-in for ``Settings`` -- ``effective_chain`` mirrors the real
    ``LlmProvidersCfg.effective_chain`` logic (mode=local -> just ollama; mode=cloud -> the
    role's configured chain, ollama-terminated) so `chat()`'s new pipeline branch can be tested
    without touching real config.yaml."""
    chains = chains or {}

    def _effective_chain(role: str) -> list[ChainEntryCfg]:
        if mode != "cloud":
            return [ChainEntryCfg(provider="ollama")]
        chain = list(chains.get(role) or [])
        if not chain:
            return [ChainEntryCfg(provider="ollama")]
        if chain[-1].provider != "ollama":
            chain.append(ChainEntryCfg(provider="ollama"))
        return chain

    llm_providers = SimpleNamespace(
        allow_cloud=allow_cloud,
        interactive_default=interactive_default,
        cli={},
        timeout_s=120,
        mode=mode,
        chains=chains,
        effective_chain=_effective_chain,
    )
    return SimpleNamespace(
        llm_providers=llm_providers,
        models={"resident": "dictalm3_12b"},
        ollama=SimpleNamespace(keep_alive="30m", num_ctx={}, num_predict={}, options={}),
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


class TestDispatchExplicitProviderPowerSuffix:
    """Regression test for the "<provider>:<model>@<power>" suffix parse in
    ``_dispatch_explicit_provider``: it must split into (model, power) in that order, not swapped.
    Before the fix, ``power, _, model = model.partition("@")`` assigned the model name to
    ``power`` and the power/effort level to ``model``, so a CLI provider was constructed with a
    bogus model id and its power/effort argument silently dropped.
    """

    def test_cli_provider_gets_model_and_power_in_correct_order(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings())
        captured: dict[str, Any] = {}

        class _FakeCli:
            def __init__(self, kind, model=None, power=None):
                captured["kind"] = kind
                captured["model"] = model
                captured["power"] = power

            def chat(self, messages, **kw):
                return ProviderResult(content="ok", model=captured["model"] or "m", provider=captured["kind"])

        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider", _FakeCli)
        monkeypatch.setattr(oc, "_log_cloud_call", lambda **kw: None)

        oc._dispatch_explicit_provider(
            "agy:gemini-3.8-flash-medium@high", [{"role": "user", "content": "hi"}], format_schema=None
        )

        assert captured["kind"] == "agy"
        assert captured["model"] == "gemini-3.8-flash-medium"
        assert captured["power"] == "high"

    def test_api_provider_gets_model_and_power_in_correct_order(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings())
        captured: dict[str, Any] = {}

        def fake_get_api_provider(kind, model=None, power=None):
            captured["kind"] = kind
            captured["model"] = model
            captured["power"] = power

            class _Fake:
                def chat(self, messages, **kw):
                    return ProviderResult(content="ok", model=model or "m", provider=kind)

            return _Fake()

        monkeypatch.setattr("eoa.llm.providers.api.get_api_provider", fake_get_api_provider)
        monkeypatch.setattr(oc, "_log_cloud_call", lambda **kw: None)

        oc._dispatch_explicit_provider(
            "anthropic:claude-sonnet-5@low", [{"role": "user", "content": "hi"}], format_schema=None
        )

        assert captured["kind"] == "anthropic"
        assert captured["model"] == "claude-sonnet-5"
        assert captured["power"] == "low"

    def test_no_power_suffix_leaves_power_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings())
        captured: dict[str, Any] = {}

        class _FakeCli:
            def __init__(self, kind, model=None, power=None):
                self.kind = kind
                self.model = model
                captured["model"] = model
                captured["power"] = power

            def chat(self, messages, **kw):
                return ProviderResult(content="ok", model=self.model or "m", provider=self.kind)

        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider", _FakeCli)
        monkeypatch.setattr(oc, "_log_cloud_call", lambda **kw: None)

        oc._dispatch_explicit_provider(
            "codex:default", [{"role": "user", "content": "hi"}], format_schema=None
        )

        assert captured["model"] == "default"
        assert captured["power"] is None


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


class TestPipelineChainDispatch:
    """U8-א/ה (Revision 2026-09-06): inside the pipeline process, `mode: cloud` routes a
    role-based call through its configured chain, falling back to ollama; `mode: local` (default)
    is byte-for-byte the old behaviour."""

    def test_local_mode_never_touches_chain_dispatch(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings(mode="local"))
        monkeypatch.setenv("EOA_PIPELINE", "1")

        def boom(*a, **k):
            raise AssertionError("chain dispatch must not run in local mode")

        monkeypatch.setattr(oc, "_dispatch_chain", boom)
        monkeypatch.setattr(
            oc, "gate", lambda: SimpleNamespace(acquire=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ollama-path")))
        )
        with pytest.raises(RuntimeError, match="ollama-path"):
            oc.chat("resident", [{"role": "user", "content": "hi"}])

    def test_cloud_mode_first_entry_success_never_touches_ollama(self, monkeypatch: pytest.MonkeyPatch):
        chains = {"resident": [ChainEntryCfg(provider="agy", model="gemini-3.8-flash-medium")]}
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings(mode="cloud", chains=chains))
        monkeypatch.setenv("EOA_PIPELINE", "1")
        monkeypatch.setattr(
            "eoa.llm.providers.cli.CliProvider.is_available", lambda self: True
        )
        monkeypatch.setattr(
            "eoa.llm.providers.cli.CliProvider.chat",
            lambda self, messages, **kw: ProviderResult(
                content="PONG", model="gemini-3.8-flash-medium", provider="agy", duration_ms=5, usage={}
            ),
        )
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: None)

        def boom(*a, **k):
            raise AssertionError("gate().acquire() must not be called when the first chain entry succeeds")

        monkeypatch.setattr(oc, "gate", lambda: SimpleNamespace(acquire=boom))
        res = oc.chat("resident", [{"role": "user", "content": "hi"}])
        assert res.content == "PONG"
        assert res.model == "agy:gemini-3.8-flash-medium"

    def test_cloud_mode_falls_back_to_ollama_on_failure(self, monkeypatch: pytest.MonkeyPatch):
        chains = {"resident": [ChainEntryCfg(provider="agy", model="bogus-model")]}
        monkeypatch.setattr(oc, "settings", lambda: _fake_settings(mode="cloud", chains=chains))
        monkeypatch.setenv("EOA_PIPELINE", "1")
        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider.is_available", lambda self: True)

        from eoa.errors import CliProviderError

        def fail_chat(self, messages, **kw):
            raise CliProviderError("bogus model id rejected")

        monkeypatch.setattr("eoa.llm.providers.cli.CliProvider.chat", fail_chat)
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: None)
        monkeypatch.setattr(
            oc,
            "gate",
            lambda: SimpleNamespace(acquire=lambda *a, **k: SimpleNamespace(ollama="dictalm3_12b", key="resident", ctx_max=8192)),
        )

        class _FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": "local answer"}, "prompt_eval_count": 3, "eval_count": 4}

        class _FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, *a, **k):
                return _FakeResp()

        monkeypatch.setattr(oc, "_client", lambda: _FakeClient())
        res = oc.chat("resident", [{"role": "user", "content": "hi"}])
        assert res.content == "local answer"
        assert res.model == "dictalm3_12b"  # the chain's local terminal entry answered


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
