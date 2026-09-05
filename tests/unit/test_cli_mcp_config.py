"""Tests for eoa.llm.providers.cli._mcp_config_path (A8/point 4, docs/adr/006-mcp-sources.md) --
building a temporary `--mcp-config` JSON file for the `claude` CLI provider from this project's
own enabled stdio MCP servers. No real subprocess is ever spawned here.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from eoa.config import McpCfg, McpServerCfg
from eoa.llm.providers.cli import CliProvider, _mcp_config_path


def _settings_with(mcp_cfg: McpCfg):
    return lambda: SimpleNamespace(mcp=mcp_cfg)


class TestMcpConfigPath:
    def test_returns_none_when_mcp_disabled(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "eoa.llm.providers.cli.settings",
            _settings_with(McpCfg(enabled=False, servers=[McpServerCfg(id="x", enabled=True, command="python")])),
        )
        assert _mcp_config_path("claude") is None

    def test_returns_none_when_cli_kind_not_opted_in(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "eoa.llm.providers.cli.settings",
            _settings_with(
                McpCfg(
                    enabled=True,
                    servers=[McpServerCfg(id="x", enabled=True, command="python")],
                    inherit_cli_mcp={"claude": False},
                )
            ),
        )
        assert _mcp_config_path("claude") is None

    def test_returns_none_with_no_stdio_servers(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "eoa.llm.providers.cli.settings",
            _settings_with(McpCfg(enabled=True, servers=[], inherit_cli_mcp={"claude": True})),
        )
        assert _mcp_config_path("claude") is None

    def test_builds_valid_config_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setenv("SAM_GOV_API_KEY", "test-key-value")
        server = McpServerCfg(
            id="procurement",
            transport="stdio",
            enabled=True,
            command="{python}",
            args=["-m", "eoa.mcp_servers.procurement"],
            env=["SAM_GOV_API_KEY"],
        )
        monkeypatch.setattr(
            "eoa.llm.providers.cli.settings",
            _settings_with(McpCfg(enabled=True, servers=[server], inherit_cli_mcp={"claude": True})),
        )
        path = _mcp_config_path("claude")
        assert path is not None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["mcpServers"]["procurement"]["command"] == sys.executable
            assert data["mcpServers"]["procurement"]["args"] == ["-m", "eoa.mcp_servers.procurement"]
            assert data["mcpServers"]["procurement"]["env"] == {"SAM_GOV_API_KEY": "test-key-value"}
        finally:
            path.unlink(missing_ok=True)

    def test_config_error_treated_as_no_mcp(self, monkeypatch: pytest.MonkeyPatch):
        def boom():
            raise RuntimeError("config not loadable")

        monkeypatch.setattr("eoa.llm.providers.cli.settings", boom)
        assert _mcp_config_path("claude") is None


class TestBuildArgsWiresMcpConfig:
    def test_claude_args_include_mcp_config_when_enabled(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        server = McpServerCfg(id="procurement", transport="stdio", enabled=True, command="{python}", args=["-m", "x"])
        monkeypatch.setattr(
            "eoa.llm.providers.cli.settings",
            _settings_with(McpCfg(enabled=True, servers=[server], inherit_cli_mcp={"claude": True})),
        )
        cp = CliProvider("claude")
        args, _stdin_data, tmp_out = cp._build_args("claude.exe", None, "hi", None)
        assert "--mcp-config" in args
        idx = args.index("--mcp-config")
        assert tmp_out is not None
        assert args[idx + 1] == str(tmp_out)
        tmp_out.unlink(missing_ok=True)

    def test_claude_args_omit_mcp_config_when_disabled(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("eoa.llm.providers.cli.settings", _settings_with(McpCfg(enabled=False)))
        cp = CliProvider("claude")
        args, _stdin_data, tmp_out = cp._build_args("claude.exe", None, "hi", None)
        assert "--mcp-config" not in args
        assert tmp_out is None

    def test_agy_never_gets_mcp_config_flag(self, monkeypatch: pytest.MonkeyPatch):
        server = McpServerCfg(id="procurement", transport="stdio", enabled=True, command="{python}", args=["-m", "x"])
        monkeypatch.setattr(
            "eoa.llm.providers.cli.settings",
            _settings_with(McpCfg(enabled=True, servers=[server], inherit_cli_mcp={"agy": True, "claude": True})),
        )
        cp = CliProvider("agy")
        args, _stdin_data, _tmp_out = cp._build_args("agy.exe", None, "hi", None)
        assert "--mcp-config" not in args
