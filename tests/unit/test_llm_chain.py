"""Tests for eoa.llm.chain -- the fallback-chain executor (U8-ה, Revision 2026-09-06).

All providers are fakes; nothing here touches a real subprocess or the network. DB logging
(`_record`) is monkeypatched out in most tests to keep them pure-unit, with one test asserting the
exact fields passed to `log_llm_call` for the accounting contract itself.
"""

from __future__ import annotations

from typing import Any

import pytest

from eoa.config import ChainEntryCfg
from eoa.errors import CliProviderError, LLMOutputError, ProviderUnavailable
from eoa.llm.chain import ChainExhausted, run_chain
from eoa.llm.providers.base import ProviderResult


def _ok_provider(content: str = "ok", model: str = "m", usage: dict[str, Any] | None = None):
    class _P:
        def is_available(self):
            return True

        def chat(self, messages, *, model=None, json_schema=None, timeout_s=None):
            return ProviderResult(content=content, model=model or "m", provider="fake", usage=usage or {})

    return _P()


def _failing_provider(exc: Exception):
    class _P:
        def is_available(self):
            return True

        def chat(self, messages, *, model=None, json_schema=None, timeout_s=None):
            raise exc

    return _P()


def _unavailable_provider():
    class _P:
        def is_available(self):
            return False

        def chat(self, messages, *, model=None, json_schema=None, timeout_s=None):
            raise AssertionError("chat() must not be called when is_available() is False")

    return _P()


def _local_ollama_leg(content: str = "local", usage: dict[str, Any] | None = None):
    calls = {"n": 0}

    def _call() -> ProviderResult:
        calls["n"] += 1
        return ProviderResult(content=content, model="dictalm3_12b", provider="ollama", usage=usage or {})

    _call.calls = calls
    return _call


class TestRunChainHappyPath:
    def test_first_entry_success_never_touches_ollama(self, monkeypatch):
        monkeypatch.setattr("eoa.llm.chain._build_provider", lambda entry: _ok_provider(content="PONG"))
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: None)
        chain = [ChainEntryCfg(provider="agy", model="x"), ChainEntryCfg(provider="ollama")]
        local = _local_ollama_leg()
        result, attempts = run_chain("resident", chain, local, messages=[])
        assert result.content == "PONG"
        assert local.calls["n"] == 0
        assert len(attempts) == 1
        assert attempts[0].ok is True
        assert attempts[0].provider == "agy"


class TestRunChainFallback:
    def test_unavailable_provider_falls_back(self, monkeypatch):
        monkeypatch.setattr("eoa.llm.chain._build_provider", lambda entry: _unavailable_provider())
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: None)
        chain = [ChainEntryCfg(provider="claude"), ChainEntryCfg(provider="ollama")]
        local = _local_ollama_leg(content="local answer")
        result, attempts = run_chain("investigator", chain, local, messages=[])
        assert result.content == "local answer"
        assert local.calls["n"] == 1
        assert len(attempts) == 2
        assert attempts[0].ok is False
        assert attempts[1].ok is True
        assert attempts[1].fell_back_from == "claude"

    def test_provider_error_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            "eoa.llm.chain._build_provider", lambda entry: _failing_provider(CliProviderError("boom"))
        )
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: None)
        chain = [ChainEntryCfg(provider="agy"), ChainEntryCfg(provider="ollama")]
        result, attempts = run_chain("light", chain, _local_ollama_leg(), messages=[])
        assert result.provider == "ollama"
        assert attempts[0].error == "boom"

    def test_multi_hop_fallback_through_three_entries(self, monkeypatch):
        providers = iter(
            [
                _failing_provider(ProviderUnavailable("no key")),
                _failing_provider(CliProviderError("rate limited")),
            ]
        )
        monkeypatch.setattr("eoa.llm.chain._build_provider", lambda entry: next(providers))
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: None)
        chain = [
            ChainEntryCfg(provider="anthropic", model="claude-opus-5"),
            ChainEntryCfg(provider="agy", model="gemini-3.1-pro-high"),
            ChainEntryCfg(provider="ollama"),
        ]
        result, attempts = run_chain("investigator", chain, _local_ollama_leg(), messages=[])
        assert result.provider == "ollama"
        assert len(attempts) == 3
        assert [a.ok for a in attempts] == [False, False, True]
        assert attempts[2].fell_back_from == "agy"

    def test_local_terminal_failure_raises_chain_exhausted(self, monkeypatch):
        monkeypatch.setattr(
            "eoa.llm.chain._build_provider", lambda entry: _failing_provider(CliProviderError("x"))
        )
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: None)
        chain = [ChainEntryCfg(provider="agy"), ChainEntryCfg(provider="ollama")]

        def boom_local():
            raise RuntimeError("gpu on fire")

        with pytest.raises(ChainExhausted):
            run_chain("resident", chain, boom_local, messages=[])

    def test_llm_output_error_is_fallback_worthy(self, monkeypatch):
        """A schema-validation failure surviving chat_structured's own retry surfaces here as
        LLMOutputError -- also a fallback trigger, not a hard stop."""
        monkeypatch.setattr(
            "eoa.llm.chain._build_provider", lambda entry: _failing_provider(LLMOutputError("bad json"))
        )
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: None)
        chain = [ChainEntryCfg(provider="openai"), ChainEntryCfg(provider="ollama")]
        result, attempts = run_chain("light", chain, _local_ollama_leg(), messages=[])
        assert result.provider == "ollama"
        assert attempts[0].error == "bad json"


class TestRunChainRecording:
    def test_record_writes_expected_fields(self, monkeypatch):
        captured = {}

        def fake_log_llm_call(**kw):
            captured.update(kw)
            return 1

        monkeypatch.setattr("eoa.memory.relational.log_llm_call", fake_log_llm_call)
        monkeypatch.setattr(
            "eoa.llm.chain._build_provider",
            lambda entry: _ok_provider(content="x", usage={"input_tokens": 100, "output_tokens": 50}),
        )
        monkeypatch.setattr(
            "eoa.llm.chain.estimate_cost_usd",
            lambda provider, model, p, c: 0.0042,
        )
        chain = [
            ChainEntryCfg(provider="anthropic", model="claude-sonnet-5", power="high"),
            ChainEntryCfg(provider="ollama"),
        ]
        run_chain("resident", chain, _local_ollama_leg(), messages=[], batch_size=3)
        assert captured["provider"] == "anthropic"
        assert captured["prompt_tokens"] == 100
        assert captured["completion_tokens"] == 50
        assert captured["est_cost_usd"] == 0.0042
        assert captured["batch_size"] == 3
        assert captured["role"] == "resident"
        assert captured["attempt_no"] == 1
        assert captured["error"] is None

    def test_record_never_raises_on_db_failure(self, monkeypatch):
        def boom(**kw):
            raise RuntimeError("db is down")

        monkeypatch.setattr("eoa.memory.relational.log_llm_call", boom)
        monkeypatch.setattr("eoa.llm.chain._build_provider", lambda entry: _ok_provider())
        chain = [ChainEntryCfg(provider="agy"), ChainEntryCfg(provider="ollama")]
        result, _ = run_chain("light", chain, _local_ollama_leg(), messages=[])
        assert result.content == "ok"


class TestBuildProvider:
    def test_unknown_provider_raises(self):
        from eoa.llm.chain import _build_provider

        with pytest.raises(ValueError):
            _build_provider(ChainEntryCfg(provider="not-a-real-provider"))

    def test_cli_kinds_build_cli_provider(self):
        from eoa.llm.chain import _build_provider
        from eoa.llm.providers.cli import CliProvider

        for kind in ("agy", "claude", "codex"):
            p = _build_provider(ChainEntryCfg(provider=kind, model="x"))
            assert isinstance(p, CliProvider)
            assert p.kind == kind

    def test_api_kinds_build_api_provider(self):
        from eoa.llm.chain import _build_provider
        from eoa.llm.providers.api import AnthropicProvider, GeminiProvider, OpenAIProvider

        assert isinstance(_build_provider(ChainEntryCfg(provider="anthropic")), AnthropicProvider)
        assert isinstance(_build_provider(ChainEntryCfg(provider="gemini")), GeminiProvider)
        assert isinstance(_build_provider(ChainEntryCfg(provider="openai")), OpenAIProvider)


class TestParseLeg:
    """PD-cloud-tools (2026-09-09): a dossier run's ``llm_leg`` string -> one ``ChainEntryCfg``."""

    def test_provider_and_model(self):
        from eoa.llm.chain import parse_leg

        assert parse_leg("codex:gpt-6-astra") == ChainEntryCfg(
            provider="codex", model="gpt-6-astra", power=None
        )

    def test_provider_model_and_power(self):
        from eoa.llm.chain import parse_leg

        assert parse_leg("claude:claude-sonnet-5@high") == ChainEntryCfg(
            provider="claude", model="claude-sonnet-5", power="high"
        )

    def test_bare_provider_no_model(self):
        from eoa.llm.chain import parse_leg

        assert parse_leg("codex") == ChainEntryCfg(provider="codex", model=None, power=None)

    def test_local_returns_none(self):
        from eoa.llm.chain import parse_leg

        assert parse_leg("local") is None

    def test_none_and_empty_return_none(self):
        from eoa.llm.chain import parse_leg

        assert parse_leg(None) is None
        assert parse_leg("") is None


class TestBuildChainWithLegOverride:
    """PD-cloud-tools (2026-09-09): the override leg is prepended ahead of the role's normally-
    configured chain -- never replaces it -- so a temporarily-unavailable leg still degrades to
    the existing chain instead of leaving the caller with nothing."""

    def test_override_prepended_ahead_of_configured_chain(self, monkeypatch: pytest.MonkeyPatch):
        from eoa.llm import chain as chain_mod

        class _LP:
            def effective_chain(self, role: str) -> list[ChainEntryCfg]:
                return [ChainEntryCfg(provider="claude", model="claude-sonnet-5"), ChainEntryCfg(provider="ollama")]

        class _S:
            llm_providers = _LP()

        monkeypatch.setattr(chain_mod, "settings", lambda: _S())
        result = chain_mod.build_chain_with_leg_override("investigator", "codex:gpt-6-astra")
        assert [e.provider for e in result] == ["codex", "claude", "ollama"]
        assert result[0].model == "gpt-6-astra"

    def test_local_leg_returns_configured_chain_unchanged(self, monkeypatch: pytest.MonkeyPatch):
        from eoa.llm import chain as chain_mod

        configured = [ChainEntryCfg(provider="claude"), ChainEntryCfg(provider="ollama")]

        class _LP:
            def effective_chain(self, role: str) -> list[ChainEntryCfg]:
                return configured

        class _S:
            llm_providers = _LP()

        monkeypatch.setattr(chain_mod, "settings", lambda: _S())
        assert chain_mod.build_chain_with_leg_override("investigator", "local") == configured
        assert chain_mod.build_chain_with_leg_override("investigator", None) == configured
