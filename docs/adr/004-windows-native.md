# ADR-004: EO-Analyst runs natively on Windows (Docker retired)

**Status:** accepted (2026-09-05)

## Context
The Docker-based stack (ADR-002, ADR-003) gave the `agent` container real, if imperfect, network
isolation: DNS pinned to `0.0.0.0` plus static `extra_hosts`, so hostname-based egress from the
isolated process was impossible (`docker-compose.yml`'s top-of-file note; `scripts/verify_isolation.*`).
That isolation came at a fixed cost: three-plus containers, a jobs-table bridge between `agent` and
`fetcher` purely so the isolated process could still read pages (ADR-003), an `ntfy` relay thread to
mirror messages to the public fallback topic, and Docker Desktop/WSL2 overhead on a 12 GB-VRAM laptop
where every hundred MB matters (ADR-001, ADR-002).

On 2026-09-05 the user decided to drop Docker entirely and run everything as native Windows processes
under one project directory (`docs/PLAN_WINDOWS_NATIVE.md`), accepting the loss of that network-layer
isolation in exchange for one process topology instead of three-plus containers, no jobs-table bridge,
and full GPU/VRAM parity with the host. This ADR records that decision, the resulting process topology,
and — since the isolation boundary this project's threat model leaned on is gone — what stays in place
to compensate, and what is still owed as follow-up.

## Decision

### 1. One process topology, no containers
| Process | Was (Docker) | Now (native) |
|---|---|---|
| PostgreSQL 17 | `postgres` container, `internal` network, port 5433 published to loopback | `runtime\pgsql` (EDB "binaries without installer" zip, 17.9) + `runtime\pgdata`, `pg_ctl`-managed, **port 5432** -- 5433 was only ever the Docker Compose host-mapping; the native cluster uses postgres's actual default port instead. `DATABASE_URL` in `.env`/`runtime\eoa.env` reflects this (`postgresql://eoa:<pw>@127.0.0.1:5432/eoanalyst`) |
| ntfy | `ntfy` container, port 8091 published to loopback | `runtime\ntfy\ntfy.exe serve`, port 8091, **listening on `0.0.0.0`** (not `127.0.0.1`) because the phone already subscribes over Tailscale at `http://100.70.157.25:8091/eo-analyst` (ADR-003) and that path only exists if the process actually binds the non-loopback interface |
| agent (orchestrator) | `agent` container, `internal`+`hostlink` networks, DNS pinned to `0.0.0.0`, static `extra_hosts` | `.venv\Scripts\python.exe -m eoa.orchestrator.main`, `EOA_ROLE=host` (already the code's default role -- `agent/eoa/fetch/remote.py`'s `_role()` -- so the in-process fetch path activates automatically; the jobs-table bridge to a separate `fetcher` container is no longer exercised) |
| web/API | `web` container | `.venv\Scripts\python.exe -m uvicorn eoa.api.app:app --host 127.0.0.1 --port 8765`, same port, now also serving `web\dist` built by `npm run build` directly on the host |
| SearXNG | `searxng` container | retired; replaced by `eoa.search.provider` (`ddgs`, multi-engine, per `docs/PLAN_WINDOWS_NATIVE.md` §1 item 4 -- a separate work item, not part of this ADR) |
| Ollama | native Windows service (already, per ADR-002) | unchanged |

`scripts\native\install_native.ps1` provisions all of the above (a `.venv` built from the host's
own Python -- no managed download; `pyproject.toml` requires `>=3.12` and this machine runs
Python 3.14 -- PostgreSQL, ntfy, the guard model, the frontend build) with no admin rights,
entirely under `<repo>\runtime\` (gitignored) plus a project-local `.venv`. The guard model step
prefers `docker cp` out of the (retiring) `eoa-agent` container's filesystem when that container
still exists, falling back to a Hugging Face download otherwise.
`scripts\native\eoa-supervisor.ps1` starts and supervises
postgres/ntfy/orchestrator/api with restart-on-exit (exponential backoff) and logs to
`runtime\logs\`; `scripts\native\register_autostart.ps1` registers it as a **user-level** (no admin)
Task Scheduler task, "EO-Analyst Supervisor", at logon. `scripts\native\migrate_from_docker.ps1`
moves data from the (retiring) Docker Postgres volume into the native cluster one time. `eo native
start|stop|status|logs` (`agent/eoa/cli.py`) wraps the supervisor for day-to-day use.

### 2. Existing "EO-Analyst Wake" task is left alone
A separate, pre-existing user-level Task Scheduler task, **"EO-Analyst Wake"**, runs daily at 00:55
with `WakeToRun=true` and a no-op action (`cmd.exe /c exit 0`). Its only purpose is to force the
laptop out of sleep before the 01:00-06:00 night window (`config/config.yaml`'s `schedule.night_window`)
so the *already-running* supervisor's orchestrator can execute its scheduled night run --
it starts no process of its own and is independent of "EO-Analyst Supervisor" (this ADR's task,
which starts the stack at logon and keeps it running). The two are complementary: Wake gets the
machine out of sleep; Supervisor is what was already running (or, if the machine was fully powered
off rather than asleep, needs to have been started manually / via a logon that follows Wake).
`scripts\native\register_autostart.ps1` does not touch "EO-Analyst Wake" and documents this in its
own header comment.

### 3. Security compensations for the lost network-layer isolation
Docker's DNS-pinning + static-hosts trick (ADR-002 consequence: "IP-literal egress from `agent`
is the documented residual gap") is gone entirely on native Windows -- there is no isolation
boundary left between the orchestrator process and the internet at the OS/network level. What
was already defense-in-depth around that boundary, and stays in force, becomes the *primary*
defense:

- **L1 (`agent/eoa/security/guard.py`, ONNX classifier) + L2 (LLM judge, no tools)** prompt-injection
  screening on every fetched item before any tool-enabled model sees it (`docs/CONVENTIONS.md` rule 3).
  `EOA_GUARD_L1_DIR` now defaults to `<repo>\runtime\models\prompt-guard` when the env var is unset
  and that directory exists (the native install's own model location), so the guard still loads
  without requiring `runtime\eoa.env` to be sourced by every invocation.
- **DATA framing**: fetched content is wrapped and the model instructed to treat it as data, never
  instructions (`docs/CONVENTIONS.md` rule 3; `eoa.llm.ollama_client.wrap_data`).
- **SSRF guard** (`agent/eoa/fetch/remote.py::assert_public_http_url`): scheme/credential/port
  checks and a public-IP-after-DNS-resolution check on every URL the agent fetches, regardless of
  which network it's running on.
- **URL allow-list during deep-search investigations** and **source deny/blocklisting**
  (`agent/eoa/security/guard.py::_maybe_blocklist`, `sources.blocklisted`) continue to gate what
  gets fetched at all.
- **Outbound HTTP audit log** -- logging host + byte count for every outbound HTTP request the
  agent process makes -- is **not yet implemented**. It is called out explicitly in
  `docs/PLAN_WINDOWS_NATIVE.md` §1 item 5 as a follow-up and is tracked here as an open item for a
  later pass; until it lands, the only real-time signal of unexpected egress is Windows Firewall
  logging (if configured) or a packet capture, neither of which is part of this repo.

### 4. What is explicitly NOT compensated
Native Windows gives no equivalent of Docker's per-container DNS/network segmentation. A process
running as the same OS user as everything else (browser, other tools) can, in principle, reach
anything that user's Windows Firewall profile allows. This is an accepted tradeoff (see Context) --
not a gap this ADR closes -- and is why the guard/SSRF/allow-list layers above are now load-bearing
rather than "second line of defense."

## Consequences
- **Positive**: one process topology instead of three-plus containers; no jobs-table fetch bridge;
  full host GPU/VRAM available to Ollama with no WSL2/Docker Desktop overhead; simpler local dev
  loop (`eo native start/stop/status/logs` instead of `docker compose ...`).
- **Negative**: the network-isolation boundary that ADR-002/ADR-003 built around is gone; the
  outbound-HTTP audit log that would partially compensate is not yet built (tracked above).
  `ntfy` now listens on `0.0.0.0:8091` rather than loopback-only, so Windows Firewall's private-network
  profile is the only thing standing between an untrusted LAN peer and the ntfy API (no auth) --
  same posture ADR-002 already accepted for Ollama on `0.0.0.0:11434`.
- **Neutral**: `NTFY_URL`'s port (8091) and the API's port (8765) are unchanged, so application
  code and the web frontend needed no changes for those. `DATABASE_URL`'s port did change, from
  5433 (the Docker Compose host-mapping) to 5432 (postgres's actual default, now that nothing
  else is competing for it) -- `.env` and `runtime\eoa.env` both carry the new value.
- **Follow-up**: outbound HTTP audit log (§3); retire the Docker Compose files and images once
  `scripts\native\migrate_from_docker.ps1` has verified row counts and the native stack has run
  unattended through at least one full night window; SearXNG retirement is tracked separately
  (`docs/PLAN_WINDOWS_NATIVE.md` §1 item 4, not part of this ADR).
