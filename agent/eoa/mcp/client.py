"""Thin async MCP client wrapper (A8, docs/adr/006-mcp-sources.md).

Uses the official ``mcp`` Python SDK (stdio + streamable-HTTP transports). Every call here opens a
fresh session, does the one thing asked, and tears the session down -- no persistent background
connection is kept. This is the simplest thing that works for a low-frequency, read-only,
interactive-analyst tool layer (a handful of calls per investigation, not a hot loop); a
long-lived connection pool would need a background event loop thread and was judged unnecessary
complexity here (see docs/adr/006-mcp-sources.md "Consequences").

Nothing in this module applies allow/deny filtering, truncation, DATA-framing, or guard
screening -- that is ``eoa.mcp.registry``'s job. This module only knows how to reach one server.
"""

from __future__ import annotations

import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import structlog
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import TextContent

from eoa.config import REPO_ROOT, McpServerCfg
from eoa.errors import EOAError
from eoa.security.redact import redact_secrets

log = structlog.get_logger(__name__)


class McpConnectionError(EOAError):
    """Could not connect to, initialize, or communicate with an MCP server."""


@dataclass
class McpToolInfo:
    name: str  # bare tool name as advertised by the server
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class McpCallResult:
    text: str  # concatenated text content (the only content type this project consumes)
    is_error: bool = False


def _resolve_command(command: str) -> str:
    """``"{python}"`` in ``config/mcp.yaml`` resolves to the interpreter running this process
    (the project's own venv) -- so our stdio servers always run with the same environment/
    dependencies as the rest of the app, on any machine, without hardcoding a path."""
    if command == "{python}":
        return sys.executable
    return command


def _server_params(server: McpServerCfg) -> StdioServerParameters:
    import os

    env = {name: os.environ[name] for name in server.env if name in os.environ}
    return StdioServerParameters(
        command=_resolve_command(server.command or ""),
        args=list(server.args),
        env=env or None,
        cwd=str(REPO_ROOT / "agent"),
    )


async def list_tools(server: McpServerCfg) -> list[McpToolInfo]:
    """Connect, initialize, list tools, disconnect. Raises ``McpConnectionError`` on any failure."""
    try:
        async with AsyncExitStack() as stack:
            session = await _open_session(stack, server)
            result = await session.list_tools()
            return [
                McpToolInfo(name=t.name, description=t.description or "", input_schema=t.inputSchema or {})
                for t in result.tools
            ]
    except McpConnectionError:
        raise
    except Exception as exc:
        # Q2-15 (2026-09-06): `exc`'s own text can echo a request URL (or other upstream detail)
        # carrying an API key -- redact before it becomes an exception message that gets logged,
        # persisted to `mcp_calls.error`, or returned to the model.
        raise McpConnectionError(
            f"mcp server '{server.id}': list_tools failed: {redact_secrets(str(exc))}"
        ) from exc


async def call_tool(server: McpServerCfg, tool_name: str, arguments: dict[str, Any]) -> McpCallResult:
    """Connect, initialize, call one tool, disconnect. Raises ``McpConnectionError`` on failure."""
    try:
        async with AsyncExitStack() as stack:
            session = await _open_session(stack, server)
            result = await session.call_tool(
                tool_name, arguments, read_timeout_seconds=timedelta(seconds=server.timeout_s)
            )
            text = "\n".join(block.text for block in result.content if isinstance(block, TextContent))
            return McpCallResult(text=text, is_error=bool(result.isError))
    except McpConnectionError:
        raise
    except Exception as exc:
        # Q2-15: same redaction as list_tools above.
        raise McpConnectionError(
            f"mcp server '{server.id}' tool '{tool_name}' failed: {redact_secrets(str(exc))}"
        ) from exc


async def _open_session(stack: AsyncExitStack, server: McpServerCfg) -> ClientSession:
    if server.transport == "stdio":
        params = _server_params(server)
        read, write = await stack.enter_async_context(stdio_client(params))
    elif server.transport == "http":
        if not server.url:
            raise McpConnectionError(f"mcp server '{server.id}': transport=http but no url configured")
        read, write, _get_session_id = await stack.enter_async_context(streamablehttp_client(server.url))
    else:
        raise McpConnectionError(f"mcp server '{server.id}': unknown transport '{server.transport}'")
    session = await stack.enter_async_context(ClientSession(read, write))
    await session.initialize()
    return session
