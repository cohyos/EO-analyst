# STATUS — EO-Analyst (native Windows stack)

> Current operating state: [CURRENT_STATE_HE.md](docs/CURRENT_STATE_HE.md), updated 2026-09-11.
> The sections below retain the earlier implementation history.

> **Rewritten 2026-09-06 (Q7-3)** — the previous version of this file described the retired
> Docker Compose topology (containers, port 8091→ntfy inside compose, `eo status`-only). This
> rewrite reflects the native stack since ADR-004. If a session is resumed, read this file first.
> Then: `git log --oneline -40`, `docs/NIGHT_CHECKLIST.md`, `docs/RUNBOOK.md`, `docs/MODULES.md`,
> `docs/QA_PROGRAM.md` + `docs/qa/` (active QA loop), `docs/USER_GUIDE_HE.md`.

## Mode

Manager mode: Fable orchestrates; cheap agents (haiku/sonnet), Gemini (`agy`, inline prompts) and
Codex do the work in parallel, each with an explicit file-ownership slice to avoid collisions.
Development continues between QA rounds; the active discipline is `docs/QA_PROGRAM.md`'s layered
loop (below), not the old container-rebuild/e2e cycle.

## Decisions

ADR-001 model selection · ADR-002 Ollama native + (retired) network isolation · ADR-003 fetcher
bridge + ntfy relay (superseded by ADR-004's single-process topology) · **ADR-004 Windows-native
(Docker retired)** · ADR-005 cloud LLM via CLI · ADR-006 MCP sources.

## Live topology (native, ADR-004 — no Docker for the app itself)

One user, four processes, no containers, everything under `runtime/` (gitignored) + the repo's own
`.venv`:

| Process | What | Port |
|---|---|---|
| PostgreSQL 17 | `runtime\pgsql` (EDB zip) + `runtime\pgdata`, `pg_ctl`-managed | **5432** |
| ntfy | `runtime\ntfy\ntfy.exe serve`, binds `0.0.0.0` (phone reaches it over Tailscale) | **8091** |
| orchestrator | `.venv\Scripts\python.exe -m eoa.orchestrator.main`, `EOA_ROLE=host` (in-process fetch, no fetcher bridge) | — |
| web/API | `.venv\Scripts\python.exe -m uvicorn eoa.api.app:app --port 8765`, also serves `web/dist` | **8765** |

All four are started/monitored/restarted by `scripts\native\eoa-supervisor.ps1` (logs to
`runtime\logs\`), registered as the user-level Task Scheduler task **"EO-Analyst Supervisor"**
(`scripts\native\register_autostart.ps1`, runs at logon, no admin rights). A separate pre-existing
task, **"EO-Analyst Wake"**, wakes the machine at 00:55 for the 01:00-06:00 night window but starts
no process of its own — see ADR-004 §2 for why the two are independent.

Day-to-day control is the `eo` CLI (`agent/eoa/cli.py`, `native_app` sub-command group):
`eo native start|stop|status|logs`. `eo native status` reads `runtime\pids\*.pid` plus live health
checks; it is read-only and safe to run any time.

Ollama stays native/local (ADR-002, unchanged) on `127.0.0.1:11434`, GPU-backed. SearXNG is
retired — search goes through `eoa.search.provider` (`ddgs`, multi-engine) with SearXNG as an
optional fallback only if a URL is configured.

**Security note (ADR-004 §3-4):** Docker's DNS-pinned network isolation is gone; there is no
OS-level boundary between the orchestrator process and the internet anymore. The L1 (ONNX)/L2
(LLM judge) prompt-injection guard, DATA-framing of fetched content, the SSRF guard, and the
investigation URL allow-list are now the *primary* defense, not defense-in-depth. An outbound-HTTP
audit log to partially compensate is still an open follow-up (tracked in ADR-004).

**A Docker Postgres container (`eoa-postgres`) from the pre-migration stack is still running
alongside the native one** — kept until the user approves deleting it (Q6-12, see "Pending user
actions" below); it is not part of the live topology the app actually uses.

## What was built 2026-09-05 → 2026-09-06

- **The Windows-native migration itself** (ADR-004, `docs/PLAN_WINDOWS_NATIVE.md`): plain-SQL
  `graph_edges` replacing Apache AGE, `items.embedding real[]` + numpy cosine replacing pgvector
  (migration 0006), `ddgs`-backed search provider, native runtime scripts (install/supervisor/
  autostart/migrate-from-docker), ntfy JSON publish fix, DB migrated off Docker Postgres.
- **LLM cloud mode (U8, full)**: a global local/cloud switch with a per-role fallback chain ending
  at local; CLI providers (`agy`/`claude`/`codex` as subprocesses) and direct-API providers
  (Anthropic/Gemini/OpenAI via `.env` keys); model + power/effort selection; cost tracking
  (migration 0009, `llm_calls`); batch mode for classify/triage/analyze in cloud mode; file-based
  cloud-delegated deep search; a tool-permission matrix; Settings UI (chains editor, model picker).
- **MCP layer (A8)**: read-only, allow-listed MCP client (DATA-framed output, guard-screened,
  audited — migration 0010, `mcp_calls`) with our own stdio servers for procurement (SAM.gov /
  USAspending / DSCA / Federal Register / Congress.gov), Janes (the user's own subscription, API
  wrapper), and patents (EPO OPS / PatentsView); wired into deep-search tool registration and the
  Settings MCP allow-list card. ADR-006.
- **BD territory report (A11)**: business-development-by-territory report (market picture, open
  tenders/forecasts, active competitors/wins, upcoming conferences, entry points, prioritized
  actions), weekly + on-demand, docx/md/html, `/bd` screen with a territory + lookback selector.
- **Technology watch (A12)**: new `tech_dev` taxonomy domain (DROIC/digital-pixel FPA, SWIR/eSWIR,
  HOT MCT/T2SL, event-based sensors, meta-surface optics, on-sensor/edge AI, lasers/LiDAR,
  target-recognition CV) fed from academic/trade sources (arXiv eess.IV/cs.CV, SPIE/JEI, Optica,
  IEEE, DTIC/OSTI, trade press, patents via MCP), TRL/maturity classification, daily/weekly report
  sections, and a `/tech-radar` screen (subdomain × maturity matrix).
- **Night-run fixes (F19-F22)**: accurate ingest counts, tender/undated items excluded from news
  sections, theme-aware report HTML, a post-tenders catch-up mini-stage so same-run tender-derived
  items get embedded/classified/triaged instead of waiting for the next backlog sweep.
- **QA program stood up** (`docs/QA_PROGRAM.md`, user directive 2026-09-06): 7 independent layers
  (Q1 code, Q2 security, Q3 content/analytic quality, Q4 link validity, Q5 UI/UX, Q6 ops, Q7
  requirements traceability), round r1 complete — see "QA loop status" below.

## QA loop status

Round **r1** findings are in `docs/qa/findings_Q{1..7}_r1.md`, triaged into fix packages in
`docs/qa/TRIAGE_r1.md`: **12 P1 / 32 P2 / 33 P3**, 0 P1 remaining untriaged. Fix packages are
in flight in parallel, each with explicit file ownership to avoid collisions (see each package's
findings file for scope). `docs/qa/traceability_r1.md` is the FR-level matrix (103 requirements:
83 implemented, 11 partial, 3 missing, 5 decided-not-to-implement). Stabilization criterion (per
`docs/QA_PROGRAM.md` §1): a round with 0 new P1/P2 and non-increasing P3, followed by one
confirming verification round. **Not yet reached** — this is round r1's fix pass, not yet
re-verified. Next: re-run the 7 layers as round r2 once the in-flight fix packages land.

## Pending user actions

- **Phone ntfy subscription**: subscribe to `http://100.70.157.25:8091/eo-analyst` (Tailscale).
  Once confirmed, per Q2-7 the public ntfy mirror (`notify.mirror_to_public`) should be turned off.
- **API keys in `.env`** (see `.env.example` for the full annotated list) — all optional, each
  degrades gracefully to "not configured" rather than failing: `ANTHROPIC_API_KEY`,
  `GEMINI_API_KEY`, `OPENAI_API_KEY` (cloud LLM providers); `SAM_GOV_API_KEY`,
  `CONGRESS_GOV_API_KEY` (procurement MCP; USAspending/Federal Register/DSCA need no key);
  `EPO_OPS_KEY`/`EPO_OPS_SECRET`, `USPTO_ODP_API_KEY` (patents MCP; PatentsView is retired).
- **Janes API key** (`JANES_API_KEY`, `JANES_API_BASE`) — the user has a Janes Data Services
  subscription (confirmed 2026-09-05); without the key the Janes MCP server has no automation
  against the portal at all.
- **Docker Postgres container/volume/image deletion approval (Q6-12)**: `eoa-postgres` is still
  running post-migration (see "Live topology" above); needs an explicit user go-ahead before
  `docker rm`/`docker volume rm`/`docker image rm`, per `docs/PLAN_WINDOWS_NATIVE.md` §5's
  "kept until deletion is approved" plan.
- **"EO-Analyst Wake" task decision (Q6-11)**: whether to keep or remove the separate wake-only
  Task Scheduler task now that the supervisor task also runs at logon — open user decision.

## Known open findings

Full detail in `docs/qa/findings_Q{1..7}_r1.md` and the merged `docs/qa/TRIAGE_r1.md`. Headline:
0 P1 open (all triaged into an owned fix package or a documented user decision — see TRIAGE_r1.md's
"תוקן ישירות" / user-decision rows), 32 P2 and 33 P3 distributed across the fix packages listed in
TRIAGE_r1.md (S1 security, FX1 sources/content, FX2 hygiene/tests/docs — this pass, C1/C2 pipeline
correctness, UX1 interface, A10 tenders, A8 MCP). Re-check `docs/qa/TRIAGE_r1.md`'s status column
for what has since closed.
