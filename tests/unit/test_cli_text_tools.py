"""Cloud-tools (2026-09-09): text-protocol tool calling for CLI legs.

`eoa.llm.chain.run_chain` used to skip every CLI leg for a tool-calling turn (no CLI provider
accepted a caller-supplied ``tools`` schema) -- so a tool-calling turn always ran on the local
Ollama leg even in ``mode: cloud``. `CliProvider.chat(..., tools=...)` now renders the tool list
into the prompt with a strict output contract, parses the reply, and adapts it back into the
same ``tool_calls`` shape Ollama's native tool-calling API produces
(``[{"function": {"name": ..., "arguments": {...}}}]``). All subprocess calls are mocked --
these tests never invoke a real agy/claude/codex binary.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from eoa.errors import CliProviderError
from eoa.llm.providers.cli import (
    CliProvider,
    _find_tool_spec,
    _parse_tool_reply,
    _render_tool_list,
    _validate_tool_args,
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Web metasearch.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}, "lang": {"type": "string"}},
                "required": ["query", "lang"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Stop and report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "outcome": {"type": "string"},
                    "answer_he": {"type": "string"},
                    "confidence": {"type": "number"},
                    "sources": {"type": "array"},
                },
                "required": ["outcome", "answer_he", "confidence", "sources"],
            },
        },
    },
]


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


# --------------------------------------------------------------------------------------------
# pure parsing/validation helpers
# --------------------------------------------------------------------------------------------


class TestRenderToolList:
    def test_includes_name_description_and_schema(self):
        rendered = _render_tool_list(TOOLS)
        assert "search" in rendered and "finish" in rendered
        assert "Web metasearch." in rendered
        assert '"query"' in rendered


class TestFindToolSpec:
    def test_finds_by_name(self):
        spec = _find_tool_spec(TOOLS, "search")
        assert spec is not None and spec["name"] == "search"

    def test_unknown_name_returns_none(self):
        assert _find_tool_spec(TOOLS, "bogus") is None


class TestValidateToolArgs:
    def test_missing_required_arg_reported(self):
        spec = _find_tool_spec(TOOLS, "search")
        err = _validate_tool_args(spec, {"query": "XM30"})
        assert err is not None and "lang" in err

    def test_all_required_present_ok(self):
        spec = _find_tool_spec(TOOLS, "search")
        assert _validate_tool_args(spec, {"query": "XM30", "lang": "en"}) is None


class TestParseToolReply:
    def test_plain_tool_call(self):
        raw = json.dumps({"tool": "search", "args": {"query": "SPECTRO XR", "lang": "en"}})
        parsed, err = _parse_tool_reply(raw, TOOLS)
        assert err is None
        assert parsed == ("search", {"query": "SPECTRO XR", "lang": "en"})

    def test_final_maps_to_finish(self):
        raw = json.dumps(
            {"final": {"outcome": "found", "answer_he": "תשובה", "confidence": 0.9, "sources": []}}
        )
        parsed, err = _parse_tool_reply(raw, TOOLS)
        assert err is None
        name, args = parsed
        assert name == "finish"
        assert args["outcome"] == "found"

    def test_tolerates_fenced_json_block(self):
        raw = "```json\n" + json.dumps({"tool": "search", "args": {"query": "q", "lang": "en"}}) + "\n```"
        parsed, err = _parse_tool_reply(raw, TOOLS)
        assert err is None and parsed[0] == "search"

    def test_tolerates_stray_prose_around_json_object(self):
        raw = 'Sure, here is my answer:\n{"tool": "search", "args": {"query": "q", "lang": "en"}}\nDone.'
        parsed, err = _parse_tool_reply(raw, TOOLS)
        assert err is None and parsed[0] == "search"

    def test_no_json_object_is_malformed(self):
        parsed, err = _parse_tool_reply("I don't know what to do.", TOOLS)
        assert parsed is None and err is not None

    def test_missing_tool_and_final_key_is_malformed(self):
        parsed, err = _parse_tool_reply(json.dumps({"foo": "bar"}), TOOLS)
        assert parsed is None and 'tool" or "final"' in (err or "")

    def test_unknown_tool_name_is_malformed(self):
        raw = json.dumps({"tool": "delete_everything", "args": {}})
        parsed, err = _parse_tool_reply(raw, TOOLS)
        assert parsed is None and "unknown tool" in (err or "")

    def test_missing_required_arg_is_malformed(self):
        raw = json.dumps({"tool": "search", "args": {"query": "q"}})
        parsed, err = _parse_tool_reply(raw, TOOLS)
        assert parsed is None and "lang" in (err or "")


# --------------------------------------------------------------------------------------------
# CliProvider.chat(..., tools=...) end to end (subprocess mocked)
# --------------------------------------------------------------------------------------------


class TestCliProviderSupportsTools:
    def test_defaults_true(self, monkeypatch: pytest.MonkeyPatch):
        fake_settings = SimpleNamespace(llm_providers=SimpleNamespace(cli_text_tools=True))
        monkeypatch.setattr("eoa.llm.providers.cli.settings", lambda: fake_settings)
        assert CliProvider("codex").supports_tools is True

    def test_config_flag_false_disables(self, monkeypatch: pytest.MonkeyPatch):
        fake_settings = SimpleNamespace(llm_providers=SimpleNamespace(cli_text_tools=False))
        monkeypatch.setattr("eoa.llm.providers.cli.settings", lambda: fake_settings)
        assert CliProvider("claude").supports_tools is False

    def test_missing_attr_defaults_true(self, monkeypatch: pytest.MonkeyPatch):
        # A minimal settings fixture with no `cli_text_tools` attribute at all (older/partial
        # config fixture) must not crash -- defaults to the feature being on.
        fake_settings = SimpleNamespace(llm_providers=SimpleNamespace())
        monkeypatch.setattr("eoa.llm.providers.cli.settings", lambda: fake_settings)
        assert CliProvider("agy").supports_tools is True


class TestCliProviderChatWithTools:
    def test_single_call_tool_reply_becomes_ollama_shaped_tool_calls(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["input"] = kwargs.get("input")
            reply = json.dumps({"tool": "search", "args": {"query": "SPECTRO XR", "lang": "en"}})
            return _completed(stdout=json.dumps({"is_error": False, "result": reply}))

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        result = CliProvider("claude", "claude-sonnet-5").chat(
            [{"role": "user", "content": "investigate SPECTRO XR"}], tools=TOOLS
        )
        assert result.tool_calls == [
            {"function": {"name": "search", "arguments": {"query": "SPECTRO XR", "lang": "en"}}}
        ]
        assert result.content == ""
        # the tool list + output contract must have reached the model.
        assert "TOOL CALLING PROTOCOL" in captured["input"]
        assert "search" in captured["input"]

    def test_final_reply_maps_to_finish_tool_call(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")

        def fake_run(args, **kwargs):
            reply = json.dumps(
                {"final": {"outcome": "found", "answer_he": "תשובה", "confidence": 0.8, "sources": []}}
            )
            return _completed(stdout=json.dumps({"status": "SUCCESS", "response": reply}))

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        result = CliProvider("agy").chat([{"role": "user", "content": "q"}], tools=TOOLS)
        assert result.tool_calls[0]["function"]["name"] == "finish"
        assert result.tool_calls[0]["function"]["arguments"]["outcome"] == "found"

    def test_malformed_reply_gets_one_repair_prompt_then_succeeds(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        calls: list[str] = []

        def fake_run(args, **kwargs):
            prompt = kwargs.get("input") or ""
            calls.append(prompt)
            if len(calls) == 1:
                # first attempt: prose, no JSON at all.
                return _completed(stdout=json.dumps({"is_error": False, "result": "I will search now."}))
            reply = json.dumps({"tool": "search", "args": {"query": "q", "lang": "en"}})
            return _completed(stdout=json.dumps({"is_error": False, "result": reply}))

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        result = CliProvider("claude").chat([{"role": "user", "content": "q"}], tools=TOOLS)
        assert len(calls) == 2  # exactly one repair attempt
        assert "אינה תואמת לפרוטוקול" in calls[1]
        assert result.tool_calls[0]["function"]["name"] == "search"

    def test_malformed_reply_after_repair_raises_cli_provider_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        calls: list[str] = []

        def fake_run(args, **kwargs):
            calls.append(kwargs.get("input") or "")
            return _completed(stdout=json.dumps({"is_error": False, "result": "still no JSON here."}))

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        with pytest.raises(CliProviderError, match="malformed"):
            CliProvider("claude").chat([{"role": "user", "content": "q"}], tools=TOOLS)
        assert len(calls) == 2  # the original attempt + exactly one repair, then it gives up

    def test_codex_tool_reply_reads_output_file(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")

        def fake_run(args, **kwargs):
            out_path = args[args.index("-o") + 1]
            from pathlib import Path

            reply = json.dumps({"tool": "read", "args": {"url": "https://x.test"}})
            # "read" isn't in TOOLS below on purpose to exercise the unknown-tool path elsewhere;
            # here we use a tools list that actually includes it.
            Path(out_path).write_text(reply, encoding="utf-8")
            return _completed(stdout=json.dumps({"type": "turn.completed", "usage": {}}))

        read_tools = [
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Fetch a URL.",
                    "parameters": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                },
            }
        ]
        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        result = CliProvider("codex").chat([{"role": "user", "content": "q"}], tools=read_tools)
        assert result.tool_calls == [{"function": {"name": "read", "arguments": {"url": "https://x.test"}}}]

    def test_json_schema_ignored_when_tools_given(self, monkeypatch: pytest.MonkeyPatch):
        """``tools`` and ``json_schema`` are never both meaningful for one call in this codebase's
        actual call sites (a ReAct turn passes only ``tools``); the tools path takes priority and
        the schema instruction is not appended, matching ``chat()``'s own dispatch."""
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        captured = {}

        def fake_run(args, **kwargs):
            captured["input"] = kwargs.get("input")
            reply = json.dumps({"tool": "search", "args": {"query": "q", "lang": "en"}})
            return _completed(stdout=json.dumps({"is_error": False, "result": reply}))

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        CliProvider("claude").chat(
            [{"role": "user", "content": "q"}],
            json_schema={"type": "object"},
            tools=TOOLS,
        )
        assert "STRUCTURED OUTPUT REQUIRED" not in captured["input"]
