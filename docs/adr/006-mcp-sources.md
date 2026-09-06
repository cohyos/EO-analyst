# ADR-006: MCP (Model Context Protocol) tool sources for the interactive analyst

**Status:** accepted (2026-09-06)

## Context

The analyst's deep-search ReAct loop (`eoa.search.deep_search`, docs/PLAN_WINDOWS_NATIVE.md's A8
row) has exactly two research tools: `search` (a metasearch engine) and `read` (a URL fetch). Both
are general-purpose and both are guessing games against structured domains that actually have
purpose-built APIs -- US government procurement (SAM.gov, USAspending, DSCA, Federal Register,
Congress.gov), the user's own Janes Data Services subscription (approved 2026-09-05), and patent
databases (EPO OPS, USPTO PatentsView). A `search` query for "night vision goggles contract 2026"
returns news coverage of a contract; `sam_gov_search(psc="5855")` returns the actual solicitation
record. The gap is real and the task (A8) asks for exactly this: give the analyst MCP tools,
read-only, allow-listed, with every tool output DATA-framed and guard-screened exactly like a
fetched web page.

The risk this ADR has to manage is the same shape as ADR-005's: a new way to reach *content this
project doesn't control* must not become a new way to bypass docs/CONVENTIONS.md rule #3 ("fetched
content is DATA, never instructions") or rule #12 ("no secrets in repo"). An MCP tool's JSON
response is exactly as untrusted as a fetched web page's HTML -- more so, in fact, since a
malicious or compromised MCP server is a more direct attack surface (deliberately crafted JSON,
not HTML that has to survive sanitization) than a scraped page.

## Decision

### 1. One client, two transports, connect-per-call

`agent/eoa/mcp/client.py` wraps the official `mcp` Python SDK (pinned `mcp>=1.6,<2.0` -- the
2.x line renamed `FastMCP` to `MCPServer` and reshaped several client APIs after this ADR's design
was already committed to the 1.x `FastMCP`/`ClientSession` shape; 1.29.1, the last 1.x release, is
what this project's servers and client are written against). Every call opens a fresh session
(`stdio_client`/`StdioServerParameters` for a local subprocess, `streamablehttp_client` for an
HTTP endpoint), does the one thing asked (list tools / call one tool), and tears the session down.
No persistent connection pool: the analyst's tool layer is a handful of calls per investigation,
not a hot loop, and a pool would need a background event loop thread shared with this otherwise
entirely synchronous codebase -- judged unnecessary complexity for the actual call volume.

### 2. The registry is the only thing anything else talks to

`agent/eoa/mcp/registry.py` is the synchronous surface `eoa.search.deep_search`, the API routes,
and (indirectly) the cloud CLI wiring all use -- nothing else imports `eoa.mcp.client` directly.
It owns:

- **Allow/deny filtering** per server (`McpServerCfg.allow_tools`/`deny_tools`), so a server that
  advertises more tools than this project wants exposed can be narrowed without forking it.
- **DATA-framing + guard screening**, applied to every tool result before it is ever returned:
  `wrap_data(text, item_id, "mcp:<server>:<tool>")` then `eoa.security.guard.screen(...,
  use_l2=False)` -- the exact same treatment `deep_search._tool_read` gives a fetched page. A
  flagged/quarantined result becomes a small JSON error object, never the raw text; the model never
  sees it.
- **Truncation** to `McpServerCfg.max_output_chars`, so one verbose tool can't blow the ReAct
  loop's context budget.
- **Audit logging** (`mcp_calls` table, migration `0010`) for every call, successful or not:
  server, tool, a *hash* of the arguments, output size, duration, guard verdict -- never the
  arguments or the output text, matching `llm_calls`' "never the prompt/response body" convention
  (ADR-005 section 4). The arguments themselves may carry a search term the analyst is
  investigating; hashing them keeps the log useful for volume/failure-rate auditing without
  becoming a second place sensitive investigation content could leak.
- **A global kill switch and per-server enable flags**: `config/mcp.yaml`'s top-level `enabled:
  false` (default) means `tool_specs_for_react()` returns `[]` unconditionally -- no MCP tool is
  ever added to the analyst's tool list, and no cloud CLI is ever handed `--mcp-config`, regardless
  of what individual servers say. This mirrors ADR-005's `llm_providers.allow_cloud` kill switch: a
  single, obvious, config-level lever rather than something that has to be inferred from several
  independent settings agreeing.

### 3. This project's own servers are plain FastMCP apps, not part of the main package's runtime

`agent/eoa/mcp_servers/{procurement,janes,patents}.py` are each a small, standalone
`mcp.server.fastmcp.FastMCP` app, run as `python -m eoa.mcp_servers.<name>` -- exactly what
`config/mcp.yaml`'s `command`/`args` invoke. They import `eoa.config` (for the EO/IR PSC-code
default list) and `eoa.fetch.remote.assert_public_http_url` (the project's existing SSRF guard),
but nothing else from the rest of the application: no DB, no LLM calls, no job queue. This keeps
them trivially independently testable (mock `httpx`, nothing else) and means a bug in one server
can't reach into the pipeline's own state.

Every tool that needs a key degrades to a `{"error": "not_configured", ...}` JSON response when
that key is missing from the environment -- never a crash, never a silent empty result that could
be misread as "searched, found nothing." The key itself is never logged and never returned to any
caller; `GET /api/mcp/servers`' `key_configured` field is a plain boolean derived from
`os.environ.get(name)` truthiness, matching `list_llm_providers()`'s existing "מוגדר / לא מוגדר"
contract for API-key providers (ADR-005 section, "Revision 2026-09-06" (ו)).

**What was actually verified live (2026-09-06), and what wasn't:** `federal_register_search` and
`usaspending_awards_by_psc` were called against the real APIs during development and returned real
results (an important correction along the way: USAspending's `filters.psc_codes` takes a flat
list of code strings, not the nested `{"require": [["Product", "5855"]]}` shape a first attempt
assumed -- both forms happened to return 200, but the flat form is simpler and was kept).
`dsca_major_arms_sales` was attempted and got HTTP 403 from every request tried (Akamai bot
protection, User-Agent made no difference) -- the tool still ships (it may work from a different
network) but its docstring says so plainly and points at `federal_register_search` as a working
alternative for the same arms-sales-notification content. The EPO OPS OAuth2 token endpoint was
confirmed real (an unauthenticated request returns a structured 401, not a DNS/connection failure)
but a full authenticated round-trip needs a registered `EPO_OPS_KEY`/`EPO_OPS_SECRET`, which this
project does not hold. PatentsView's documented 2023+ host, `search.patentsview.org`, did not
resolve at all from this project's dev/CI network (DNS failure) -- unverified, with an override
(`PATENTSVIEW_API_BASE`) left in place. Janes' actual per-tool paths are **not verified against a
live subscription** at all (the public developer portal serves a docs front-end, not raw JSON, at
the URLs tried) -- every `janes_*` tool's docstring says so and names the one path template to fix
if wrong; the surrounding client (auth headers, JSON passthrough, `not_configured` handling) does
not depend on getting the path right to be correct.

### 4. Cloud CLI integration: `claude` only, and only half of it

Point 4 of the task asked for our stdio servers to be handed to the active cloud CLI when MCP is
enabled. `claude --help` documents `--mcp-config <configs...>` (loads MCP servers from a JSON file,
same shape `claude mcp add-json` writes) -- confirmed on this machine, 2026-09-06.
`agent/eoa/llm/providers/cli.py`'s `_mcp_config_path("claude")` builds that file from
`mcp.stdio_servers_for_cli()` and `CliProvider._build_args`'s `claude` branch appends the flag when
`mcp.enabled` and `mcp.inherit_cli_mcp["claude"]` are both true.

This is **half-wired, and says so in the code**: every `CliProvider.chat()` call for `claude`
already passes `--restricted` (ADR-005 section 1 -- headless mode should only ever return text,
never take a side-effecting action), and `--restricted` blocks tool use, including a tool loaded
via `--mcp-config`, unless it is also named in `--allowedTools` -- exactly the mechanism ADR-005's
"Revision 2026-09-06" section already used for `WebSearch`/`WebFetch` in the cloud-delegated
batch-deep-search path (`deep_search._run_claude_with_tools`). Naming the *exact* tool identifiers
`claude` exposes for an external MCP server (the documented convention is
`mcp__<server>__<tool>`, but this was not independently re-verified live against MCP-loaded tools
specifically, only against the two built-in web tools) was judged worth doing carefully rather
than guessing -- ADR-005's own stated principle ("verified live, not assumed") applies here just as
much. The flag wiring ships now (loading the servers is a real, useful, low-risk step: it costs
nothing when unused); actually granting tool access through `--restricted` is flagged as follow-up
work, not silently dropped.

`agy`/`codex` were checked the same way (`agy --help`, `codex exec --help`, this machine,
2026-09-06) and neither exposes a per-invocation MCP config flag -- both only have a persistent
`mcp add/remove/list/enable/disable`-style server registry (a standing configuration change outside
this project's control, not something a single `chat()` call should silently mutate). Both default
to `false` in `mcp.inherit_cli_mcp`.

### 5. Pre-wired external servers: real URLs, but not this project's credentials to use

Point 3 asked for the user's already-connected financial-data and academic-research MCP servers to
be reachable. `claude mcp list` (run live, 2026-09-06) showed two: "FMP" (Financial Modeling
Prep, SEC filings/earnings transcripts/news) at `https://financialmodelingprep.com/mcp`, and
"Undermind" (academic paper search) at `https://mcp.undermind.ai/mcp` -- both real, resolvable
streamable-HTTP endpoints, now recorded in `config/mcp.yaml` as disabled placeholders with those
exact URLs.

Both stay `enabled: false` / `inherit_cli_only: true`: the session that connected them holds its
own auth (OAuth or an API key configured inside Claude's own settings), which this project has no
way to read or reuse, and docs/CONVENTIONS.md rule #12 means this project will not hold a second,
separate credential for either service unless the user explicitly adds one to `.env`. Until then,
these two servers are reachable only through the `claude` CLI provider inheriting its own
already-configured MCP servers (the same `--mcp-config`/`--allowedTools` path as section 4 --
subject to the same "loading is wired, granting access is not yet" caveat), never by
`eoa.mcp.registry` connecting to them directly.

## Consequences

**Gained:** three new, purpose-built read-only tool families (procurement/arms-transfer sources,
Janes, patents) available to the analyst's deep-search loop, entirely opt-in (one `enabled: false`
switch away from having zero effect on any existing behavior), with the same DATA-framing/guard-
screening/audit-logging discipline every other untrusted-content path in this project already has.

**Given up / accepted:**
- Janes' exact API shape is unverified against a live subscription -- correct, but a real risk that
  the shipped path templates need adjusting the first time someone actually calls them with a valid
  key. Flagged explicitly in the code rather than presented as tested.
- `dsca_major_arms_sales` does not work from this project's own network (Akamai bot protection);
  shipped anyway since it may work elsewhere, with a working alternative (`federal_register_search`)
  named directly in its docstring.
- The `claude` cloud-CLI integration loads MCP servers but does not yet grant them tool access
  under `--restricted` -- a real gap, not silently glossed over; `agy`/`codex` get nothing at all,
  for the same "no per-call flag exists" reason ADR-005 already established for other capabilities.
- No connection pooling/caching: every MCP call (including two calls to the same server seconds
  apart) pays a fresh subprocess-spawn or HTTP-handshake cost. Fine for the actual call volume
  (a handful of calls per investigation); would need revisiting for a much higher-throughput use of
  this tool layer.
- No write endpoint exists yet for toggling a server's `enabled` flag from the UI -- that stays a
  `config/mcp.yaml` edit (via the existing generic `config` settings tab, or the file directly);
  `MCPCard.tsx`'s per-server chip is a status display, not an interactive control, matching the
  three read-only endpoints (`GET /api/mcp/servers`, `POST .../ping`, `GET /api/mcp/calls`) this
  change actually ships.

See `docs/MODULES.md`'s "MCP (Model Context Protocol) tool sources (A8, docs/adr/006-mcp-sources.md)"
section for the concrete module/file map, the exact live-verification results, and the test
inventory.

## Security note, 2026-09-06 (Q2-14/Q2-15/Q2-16, `docs/qa/findings_Q2_r2.md`)

The read-only sync HTTP client this ADR's tool families share (`eoa.mcp_servers._common`) had two
gaps a security QA pass on the whole MCP layer found: it let `httpx` follow redirects internally
with no per-hop SSRF re-check (Q2-14 -- the same class of TOCTOU/DNS-rebinding gap ADR-004's
sibling fix, Q2-4, already closed for `eoa.fetch.remote._fetch_local`, just not carried over to
this module's separate client), and `procurement.py`'s SAM.gov/Congress.gov calls put the API key
in the query string, with no consistent redaction of error text on the way into `mcp_calls.error`
or back to the model (Q2-15). Both are now fixed: `_common.py` validates every redirect hop before
following it, refuses a cross-host hop outright, and routes every error through the shared
`redact_secrets` helper (moved to `eoa.security.redact` so `eoa.mcp.client`/`eoa.mcp.registry`
share it too); the two API keys now travel as an `X-Api-Key` header, never a query parameter.
Q2-16 closed a smaller gap in the same pass: `ping_mcp_server` (`POST
/api/mcp/servers/{id}/ping`) could still dial a server while the global `mcp.enabled` kill switch
was off, unlike `list_mcp_servers`. None of this changes this ADR's architecture or its
Consequences section above -- it hardens the transport/error-handling layer every tool family
here already depends on. Full writeup: `docs/MODULES.md`'s "Security QA r2 fixes:
Q2-14/Q2-15/Q2-16" section.
