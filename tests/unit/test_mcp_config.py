"""Tests for the A8 MCP config model (eoa.config.McpCfg/McpServerCfg) and config/mcp.yaml itself."""

from __future__ import annotations

from eoa.config import McpCfg, McpServerCfg, settings


class TestMcpServerCfgDefaults:
    def test_defaults_are_safe(self):
        s = McpServerCfg(id="x")
        assert s.enabled is False
        assert s.transport == "stdio"
        assert s.allow_tools == []
        assert s.deny_tools == []
        assert s.inherit_cli_only is False


class TestMcpCfgHelpers:
    def test_disabled_by_default(self):
        assert McpCfg().enabled is False

    def test_server_lookup(self):
        cfg = McpCfg(servers=[McpServerCfg(id="a"), McpServerCfg(id="b")])
        assert cfg.server("a").id == "a"
        assert cfg.server("missing") is None

    def test_enabled_servers_excludes_disabled_and_inherit_only(self):
        cfg = McpCfg(
            servers=[
                McpServerCfg(id="on", enabled=True),
                McpServerCfg(id="off", enabled=False),
                McpServerCfg(id="cli-only", enabled=True, inherit_cli_only=True),
            ]
        )
        assert [s.id for s in cfg.enabled_servers()] == ["on"]

    def test_stdio_servers_for_cli_requires_command(self):
        cfg = McpCfg(
            servers=[
                McpServerCfg(id="stdio-ok", enabled=True, transport="stdio", command="python"),
                McpServerCfg(id="stdio-no-command", enabled=True, transport="stdio", command=None),
                McpServerCfg(id="http", enabled=True, transport="http", url="https://example.invalid"),
                McpServerCfg(id="disabled", enabled=False, transport="stdio", command="python"),
            ]
        )
        assert [s.id for s in cfg.stdio_servers_for_cli()] == ["stdio-ok"]


class TestMcpYamlLoads:
    """config/mcp.yaml itself must load into Settings.mcp without error."""

    def test_repo_mcp_yaml_loads(self):
        settings.cache_clear()
        s = settings()
        assert isinstance(s.mcp, McpCfg)
        ids = {srv.id for srv in s.mcp.servers}
        assert {"procurement", "janes", "patents"}.issubset(ids)
        for srv in s.mcp.servers:
            if srv.id == "procurement":
                assert srv.enabled is True
        assert s.mcp.procurement.psc_codes_eo_ir  # non-empty default list
