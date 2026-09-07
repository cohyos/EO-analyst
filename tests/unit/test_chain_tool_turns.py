"""Round 7 (2026-09-07): tool-calling turns through the cloud chain.

`_dispatch_chain` used to drop ``tools`` and hardcode ``tool_calls=[]``, so the deep-search ReAct
loop lost every search/read/finish call the moment a cloud chain was configured for its role and
looped until its budget expired. Contract now: a tool-calling turn runs only on a tool-capable leg
(the local Ollama leg today), cloud legs are skipped with one warning per role, tool calls flow back,
and a chain with no tool-capable leg fails loudly."""

from __future__ import annotations

from typing import Any

import pytest

from eoa.config import ChainEntryCfg
from eoa.errors import ProviderUnavailable
from eoa.llm import chain as chain_mod
from eoa.llm import ollama_client
from eoa.llm.providers.base import ProviderResult

TOOLS = [{"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}]
TOOL_CALL = {"function": {"name": "search", "arguments": {"q": "XM30 EO/IR"}}}


class _FakeProvider:
    supports_tools = False

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def is_available(self) -> bool:
        return True

    def chat(self, messages: Any, **kw: Any) -> ProviderResult:
        self.calls.append(kw)
        return ProviderResult(content="prose answer, no tools", model="cloud-model", provider="claude")


class _ToolProvider(_FakeProvider):
    supports_tools = True

    def chat(self, messages: Any, **kw: Any) -> ProviderResult:
        self.calls.append(kw)
        return ProviderResult(content="", model="api-model", provider="anthropic", tool_calls=[TOOL_CALL])


def _entries(*providers: str) -> list[ChainEntryCfg]:
    return [ChainEntryCfg(provider=p, model=None if p == "ollama" else "m") for p in providers]


@pytest.fixture
def no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chain_mod, "_record", lambda *a, **k: None)


class TestRunChainTools:
    def test_cloud_leg_skipped_for_tool_turn_and_local_tool_calls_flow_back(
        self, monkeypatch: pytest.MonkeyPatch, no_record: None
    ) -> None:
        fake = _FakeProvider()
        monkeypatch.setattr(chain_mod, "_build_provider", lambda entry: fake)
        local = ProviderResult(content="", model="dictalm", provider="ollama", tool_calls=[TOOL_CALL])
        result, attempts = chain_mod.run_chain(
            "investigator", _entries("claude", "agy", "ollama"), lambda: local, messages=[], tools=TOOLS
        )
        assert fake.calls == []  # never asked a CLI leg to do tool-calling
        assert result.provider == "ollama" and result.tool_calls == [TOOL_CALL]
        assert [a.provider for a in attempts] == ["claude", "agy", "ollama"]
        assert all(not a.ok for a in attempts[:2]) and "tool-calling" in (attempts[0].error or "")

    def test_provider_that_supports_tools_receives_them(
        self, monkeypatch: pytest.MonkeyPatch, no_record: None
    ) -> None:
        prov = _ToolProvider()
        monkeypatch.setattr(chain_mod, "_build_provider", lambda entry: prov)
        result, attempts = chain_mod.run_chain(
            "investigator",
            _entries("anthropic", "ollama"),
            lambda: pytest.fail("local leg must not run"),
            messages=[],
            tools=TOOLS,
        )
        assert prov.calls[0]["tools"] == TOOLS
        assert result.tool_calls == [TOOL_CALL] and attempts[0].ok

    def test_no_tool_capable_leg_fails_loudly(self, no_record: None) -> None:
        with pytest.raises(ProviderUnavailable, match="no tool-capable leg"):
            chain_mod.run_chain(
                "investigator", _entries("claude", "agy"), lambda: pytest.fail("x"), messages=[], tools=TOOLS
            )

    def test_without_tools_cloud_leg_is_used_as_before(
        self, monkeypatch: pytest.MonkeyPatch, no_record: None
    ) -> None:
        fake = _FakeProvider()
        monkeypatch.setattr(chain_mod, "_build_provider", lambda entry: fake)
        result, attempts = chain_mod.run_chain(
            "resident", _entries("claude", "ollama"), lambda: pytest.fail("local must not run"), messages=[]
        )
        assert result.provider == "claude" and "tools" not in fake.calls[0]
        assert result.tool_calls == []


class TestDispatchChain:
    def test_dispatch_threads_tools_to_local_leg_and_returns_tool_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def _fake_ollama_chat(role: str, messages: Any, **kw: Any) -> ollama_client.ChatResult:
            seen.update(kw)
            return ollama_client.ChatResult(
                content="",
                tool_calls=[TOOL_CALL],
                thinking=None,
                prompt_tokens=10,
                eval_tokens=5,
                duration_ms=1,
                model="dictalm",
                raw={},
            )

        monkeypatch.setattr(ollama_client, "_ollama_chat", _fake_ollama_chat)
        monkeypatch.setattr(chain_mod, "_record", lambda *a, **k: None)
        monkeypatch.setattr(chain_mod, "_build_provider", lambda entry: _FakeProvider())

        class _LP:
            def effective_chain(self, role: str) -> list[ChainEntryCfg]:
                return _entries("claude", "ollama")

        class _S:
            llm_providers = _LP()

        monkeypatch.setattr(ollama_client, "settings", lambda: _S())
        ollama_client._TOOL_TURN_WARNED_ROLES.discard("investigator")
        res = ollama_client._dispatch_chain(
            "investigator",
            [{"role": "user", "content": "q"}],
            task="react",
            format_schema=None,
            options=None,
            think=None,
            interactive=False,
            keep_alive=None,
            tools=TOOLS,
        )
        assert seen["tools"] == TOOLS
        assert res.tool_calls == [TOOL_CALL]
        assert res.model == "dictalm"
        assert "investigator" in ollama_client._TOOL_TURN_WARNED_ROLES


class TestInteractiveChainDefault:
    def test_interactive_default_chain_routes_chat_through_dispatch_chain(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Round-7 judge (D5): `interactive_default: "chain"` sends an interactive turn through the
        role's cloud chain instead of queueing behind the local model."""
        monkeypatch.delenv("EOA_PIPELINE", raising=False)

        class _LP:
            interactive_default = "chain"

            def effective_chain(self, role: str) -> list[ChainEntryCfg]:
                return _entries("claude", "ollama")

        class _S:
            llm_providers = _LP()
            models: dict[str, str] = {"resident": "dictalm"}

        monkeypatch.setattr(ollama_client, "settings", lambda: _S())
        called: dict[str, Any] = {}

        def _fake_dispatch(role: str, messages: Any, **kw: Any) -> ollama_client.ChatResult:
            called.update(role=role, **kw)
            return ollama_client.ChatResult(
                content="ok",
                tool_calls=[],
                thinking=None,
                prompt_tokens=1,
                eval_tokens=1,
                duration_ms=1,
                model="claude:claude-sonnet-5",
                raw={},
            )

        monkeypatch.setattr(ollama_client, "_dispatch_chain", _fake_dispatch)
        monkeypatch.setattr(ollama_client, "_ollama_chat", lambda *a, **k: pytest.fail("must not go local"))
        res = ollama_client.chat("resident", [{"role": "user", "content": "q"}], task="chat")
        assert res.content == "ok" and called["role"] == "resident"
        assert ollama_client.resolve_provider_info(None) == ("claude", "m")
