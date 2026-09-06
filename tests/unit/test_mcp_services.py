"""Tests for `eoa.api.services.ping_mcp_server` -- Q2-16 (2026-09-06): the global `mcp.enabled`
kill switch must be respected here exactly like `list_mcp_servers` already does, without ever
dialing the server when MCP is globally disabled.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from eoa.api import services
from eoa.config import McpCfg, McpServerCfg


def _server(**kw) -> McpServerCfg:
    base = dict(id="procurement", label="Procurement", transport="stdio", enabled=True)
    base.update(kw)
    return McpServerCfg(**base)


def _settings_with(cfg: McpCfg):
    return lambda: SimpleNamespace(mcp=cfg)


class TestPingMcpServer:
    def test_unknown_server_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(services.eoa_config, "settings", _settings_with(McpCfg(enabled=True, servers=[])))
        with pytest.raises(services.McpServerNotFound):
            services.ping_mcp_server("bogus")

    def test_globally_disabled_returns_not_enabled_without_connecting(self, monkeypatch: pytest.MonkeyPatch):
        server = _server()
        monkeypatch.setattr(
            services.eoa_config, "settings", _settings_with(McpCfg(enabled=False, servers=[server]))
        )

        def boom(_server):
            raise AssertionError("ping_server must not be called while mcp.enabled is False")

        monkeypatch.setattr("eoa.mcp.registry.ping_server", boom)

        out = services.ping_mcp_server("procurement")
        assert out["ok"] is False
        assert out["error"] == "not_enabled"
        assert out["tool_count"] == 0

    def test_inherit_cli_only_short_circuits(self, monkeypatch: pytest.MonkeyPatch):
        server = _server(inherit_cli_only=True)
        monkeypatch.setattr(
            services.eoa_config, "settings", _settings_with(McpCfg(enabled=True, servers=[server]))
        )

        def boom(_server):
            raise AssertionError("ping_server must not be called for an inherit_cli_only server")

        monkeypatch.setattr("eoa.mcp.registry.ping_server", boom)

        out = services.ping_mcp_server("procurement")
        assert out["ok"] is False
        assert "inherit_cli_only" in out["error"]

    def test_enabled_dials_the_server(self, monkeypatch: pytest.MonkeyPatch):
        server = _server()
        monkeypatch.setattr(
            services.eoa_config, "settings", _settings_with(McpCfg(enabled=True, servers=[server]))
        )

        result = SimpleNamespace(
            id="procurement", ok=True, error=None, tool_count=4, tools=["ping"], latency_ms=12
        )
        monkeypatch.setattr("eoa.mcp.registry.ping_server", lambda _server: result)

        out = services.ping_mcp_server("procurement")
        assert out["ok"] is True
        assert out["tool_count"] == 4
