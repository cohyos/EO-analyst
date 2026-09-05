"""MCP (Model Context Protocol) client layer for the interactive analyst (A8).

``eoa.mcp.client`` -- thin async wrapper around the official ``mcp`` SDK (stdio + streamable-HTTP).
``eoa.mcp.registry`` -- the synchronous, allow-listed, DATA-framed, guard-screened, audit-logged
surface everything else (deep_search's tool loop, the CLI providers, the API routes) actually uses.

See docs/adr/006-mcp-sources.md and docs/MODULES.md's MCP section.
"""

from __future__ import annotations
