"""Tests for eoa.llm.providers -- U8 cloud CLI provider routing (docs/adr/005-cloud-llm-cli.md).

All subprocess calls are mocked (`subprocess.run` is monkeypatched on the ``cli`` module) --
these tests never invoke a real agy/claude/codex binary. See
``scripts/`` or the ADR for the live smoke-test commands that do.
"""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from eoa.errors import CliProviderError, ProviderUnavailable
from eoa.llm.providers import parse_provider
from eoa.llm.providers.base import ProviderResult, strip_code_fences
from eoa.llm.providers.cli import CliProvider


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["x"], returncode=returncode, stdout=stdout, stderr=stderr)


class TestParseProvider:
    def test_bare_kind(self):
        assert parse_provider("ollama") == ("ollama", None)
        assert parse_provider("codex") == ("codex", None)

    def test_kind_with_model(self):
        assert parse_provider("agy:gemini-3.8-flash-medium") == ("agy", "gemini-3.8-flash-medium")
        assert parse_provider("claude:claude-sonnet-5") == ("claude", "claude-sonnet-5")


class TestStripCodeFences:
    def test_removes_fence(self):
        assert strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_no_fence_unchanged(self):
        assert strip_code_fences('{"a": 1}') == '{"a": 1}'


class TestCliProviderConstruction:
    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError):
            CliProvider("bogus")

    def test_list_models_static_fallback(self, monkeypatch: pytest.MonkeyPatch):
        # No config override -> falls back to the hard-coded static list.
        fake_settings = SimpleNamespace(llm_providers=SimpleNamespace(cli={}, timeout_s=120))
        monkeypatch.setattr("eoa.llm.providers.cli.settings", lambda: fake_settings)
        assert "gemini-3.8-flash-medium" in CliProvider("agy").list_models()
        assert "claude-sonnet-5" in CliProvider("claude").list_models()
        assert CliProvider("codex").list_models() == ["default"]


class TestCliProviderAvailability:
    def test_unavailable_when_binary_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda _: None)
        cp = CliProvider("agy")
        assert cp.is_available() is False
        with pytest.raises(ProviderUnavailable):
            cp.chat([{"role": "user", "content": "hi"}])

    def test_available_when_binary_found(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/usr/bin/{name}")
        assert CliProvider("claude").is_available() is True


class TestCliProviderChatAgy:
    def test_success(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["input"] = kwargs.get("input")
            return _completed(
                stdout=json.dumps({"status": "SUCCESS", "response": "PONG\n", "usage": {"output_tokens": 3}})
            )

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        result = CliProvider("agy").chat([{"role": "user", "content": "ping"}])
        assert isinstance(result, ProviderResult)
        assert result.content == "PONG"
        assert result.provider == "agy"
        assert result.usage["output_tokens"] == 3
        # agy has no stdin support -> prompt travels as an argv element, not stdin.
        assert captured["input"] is None
        assert "-p" in captured["args"]

    def test_status_failure_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        monkeypatch.setattr(
            "eoa.llm.providers.cli.subprocess.run",
            lambda *a, **k: _completed(stdout=json.dumps({"status": "ERROR", "response": ""})),
        )
        with pytest.raises(CliProviderError):
            CliProvider("agy").chat([{"role": "user", "content": "ping"}])

    def test_nonzero_exit_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        monkeypatch.setattr(
            "eoa.llm.providers.cli.subprocess.run",
            lambda *a, **k: _completed(stderr="boom", returncode=1),
        )
        with pytest.raises(CliProviderError, match="boom"):
            CliProvider("agy").chat([{"role": "user", "content": "ping"}])

    def test_non_json_output_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        monkeypatch.setattr(
            "eoa.llm.providers.cli.subprocess.run", lambda *a, **k: _completed(stdout="not json")
        )
        with pytest.raises(CliProviderError):
            CliProvider("agy").chat([{"role": "user", "content": "ping"}])


class TestCliProviderChatClaude:
    def test_success_uses_stdin_and_restricted_flag(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["input"] = kwargs.get("input")
            return _completed(
                stdout=json.dumps({"is_error": False, "result": "PONG", "usage": {"output_tokens": 5}})
            )

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        result = CliProvider("claude", "claude-haiku-4-5-20251001").chat(
            [{"role": "system", "content": "be terse"}, {"role": "user", "content": "ping"}]
        )
        assert result.content == "PONG"
        assert "--restricted" in captured["args"]
        assert "--model" in captured["args"]
        assert captured["input"] is not None and "ping" in captured["input"]

    def test_is_error_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        monkeypatch.setattr(
            "eoa.llm.providers.cli.subprocess.run",
            lambda *a, **k: _completed(stdout=json.dumps({"is_error": True, "result": "boom"})),
        )
        with pytest.raises(CliProviderError):
            CliProvider("claude").chat([{"role": "user", "content": "ping"}])


class TestCliProviderChatCodex:
    def test_success_reads_output_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")

        def fake_run(args, **kwargs):
            # Find the -o <path> pair and write the "agent's final answer" there,
            # mirroring what `codex exec -o` actually does.
            out_path = args[args.index("-o") + 1]
            from pathlib import Path

            Path(out_path).write_text("PONG", encoding="utf-8")
            stdout = "\n".join(
                [
                    json.dumps({"type": "thread.started"}),
                    json.dumps({"type": "turn.completed", "usage": {"output_tokens": 2}}),
                ]
            )
            return _completed(stdout=stdout)

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        result = CliProvider("codex").chat([{"role": "user", "content": "ping"}])
        assert result.content == "PONG"
        assert result.usage == {"output_tokens": 2}

    def test_missing_output_file_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        # Never writes to -o's path -- the CliProvider still cleans it up and must raise.
        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", lambda *a, **k: _completed(stdout=""))

        def fake_parse_codex(proc, tmp_out):
            from eoa.llm.providers.cli import _parse_codex as real

            return real(proc, None)  # simulate the temp file having vanished

        monkeypatch.setattr(
            "eoa.llm.providers.cli.CliProvider._parse_output",
            lambda self, proc, tmp_out: fake_parse_codex(proc, tmp_out),
        )
        with pytest.raises(CliProviderError):
            CliProvider("codex").chat([{"role": "user", "content": "ping"}])


class TestCliProviderTimeout:
    def test_timeout_raises_cli_provider_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")

        def fake_run(*a, **k):
            raise subprocess.TimeoutExpired(cmd="agy", timeout=1)

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        with pytest.raises(CliProviderError, match="timed out"):
            CliProvider("agy").chat([{"role": "user", "content": "ping"}], timeout_s=1)


class TestPowerEffortFlag:
    """U8-ג (Revision 2026-09-06): power/effort level -> the flag each CLI actually accepts,
    verified live against `agy --help`/`claude --help`/`codex exec --help` (see
    docs/adr/005-cloud-llm-cli.md's Revision 2026-09-06 permission matrix)."""

    def test_agy_gets_bare_effort_flag(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return _completed(stdout=json.dumps({"status": "SUCCESS", "response": "ok"}))

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        CliProvider("agy", power="high").chat([{"role": "user", "content": "ping"}])
        assert "--effort" in captured["args"]
        assert captured["args"][captured["args"].index("--effort") + 1] == "high"

    def test_claude_gets_bare_effort_flag(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return _completed(stdout=json.dumps({"is_error": False, "result": "ok"}))

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        CliProvider("claude", power="low").chat([{"role": "user", "content": "ping"}])
        assert "--effort" in captured["args"]
        assert captured["args"][captured["args"].index("--effort") + 1] == "low"

    def test_codex_gets_config_override_not_a_dedicated_flag(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            out_path = args[args.index("-o") + 1]
            from pathlib import Path

            Path(out_path).write_text("ok", encoding="utf-8")
            return _completed(stdout="")

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        CliProvider("codex", power="medium").chat([{"role": "user", "content": "ping"}])
        assert "--effort" not in captured["args"]
        assert "-c" in captured["args"]
        assert captured["args"][captured["args"].index("-c") + 1] == "model_reasoning_effort=medium"

    def test_no_power_omits_the_flag_entirely(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return _completed(stdout=json.dumps({"status": "SUCCESS", "response": "ok"}))

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        CliProvider("agy").chat([{"role": "user", "content": "ping"}])
        assert "--effort" not in captured["args"]

    def test_call_time_power_overrides_constructor_power(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return _completed(stdout=json.dumps({"status": "SUCCESS", "response": "ok"}))

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        CliProvider("agy", power="low").chat([{"role": "user", "content": "ping"}], power="high")
        assert captured["args"][captured["args"].index("--effort") + 1] == "high"


class TestJsonSchemaInstruction:
    def test_schema_appended_to_prompt(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.shutil.which", lambda name: f"/bin/{name}")
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            return _completed(stdout=json.dumps({"status": "SUCCESS", "response": "{}"}))

        monkeypatch.setattr("eoa.llm.providers.cli.subprocess.run", fake_run)
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        CliProvider("agy").chat([{"role": "user", "content": "ping"}], json_schema=schema)
        prompt_arg = captured["args"][captured["args"].index("-p") + 1]
        assert "STRUCTURED OUTPUT REQUIRED" in prompt_arg
        assert '"x"' in prompt_arg
