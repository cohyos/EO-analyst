"""Our own read-only MCP servers (A8): ``procurement``, ``janes``, ``patents``.

Each is a standalone stdio server, run as ``python -m eoa.mcp_servers.<name>`` (exactly what
``config/mcp.yaml`` configures as that server's ``command``/``args``). They are plain, focused
FastMCP apps -- no DB access, no LLM calls, no dependency on the rest of the running application
beyond ``eoa.config`` (for defaults) and ``eoa.fetch.remote.assert_public_http_url`` (SSRF guard on
every outbound HTTP call, even though the hosts are fixed, well-known public APIs -- defense in
depth per docs/CONVENTIONS.md rule #3's spirit: nothing this project fetches is trusted by
construction).
"""

from __future__ import annotations
