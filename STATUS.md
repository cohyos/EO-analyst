# STATUS — EO-Analyst autonomous build

> If a session is resumed, read this file first. Then `git log --oneline -20`. Then `docs/MODULES.md`, `docs/RUNBOOK.md`.

## Goal for this run (started 2026-09-04 14:00 Israel time)
Working end-to-end analyst: first real nightly run at 01:00 on 2026-09-05 producing a docx report at ~06:00 and ntfy
notifications; full web UI (hybrid ops console + analyst chat); second night with deep search.

## Decisions (docs/adr/)
- ADR-001 (bake-off 2026-09-04): `resident` = DictaLM-3.0-Nemotron-12B (Hebrew 0.90, JSON 100%, 1,741 tok/s prompt
  eval), `investigator` = gemma4:12b (only model that drove tools to `finish`), `light` = gemma4:e4b,
  `embed` = snowflake-arctic-embed2 (1024-d). gemma4:26b rejected.
- ADR-002: Ollama native on Windows (0.0.0.0:11434, NO_CLOUD, MAX_LOADED=1, KV q8_0, flash-attn, GPU_OVERHEAD 1.2 GB);
  containers reach it via host.docker.internal. `agent` container verified: DB/Ollama/SearXNG reachable, GPU telemetry
  works, internet blocked (ConnectError).
- Agent↔fetcher bridge: the isolated agent never fetches; it enqueues `ingest` / `fetch_url` jobs that the fetcher
  container executes (agent/eoa/fetch/remote.py). Host runs (EOA_ROLE unset) fetch in-process.
- ntfy self-hosted (127.0.0.1:8090) + public fallback topic `dissertation_editor_ysf` (user-consult, 5-min timeout).
- Deep search: max 4 investigations per night.

## Host state
- Ollama: auto-updater got stuck after restart (installer killed); server started manually with `ollama serve`
  + hardened env (log: output/logs/ollama_serve.log). NOTE: when the user next reboots, the Ollama app will start
  with the user-scope env vars (set) — verify `OLLAMA_HOST` then. Version now reports 0.33.3.
- Scheduled task "EO-Analyst Wake" daily 00:55 (WakeToRun). `.wslconfig` written (applies after `wsl --shutdown`).
- Docker: postgres, searxng (dev port 127.0.0.1:8088), ntfy (8090), fetcher, web (UI+API at 127.0.0.1:8765) running.
  Images: eo-analyst/{postgres-age,agent,fetcher,web}:local. `agent` service not started yet (start before 01:00).
- DB: 220 items ingested (host run), embeddings + dedup done (18 duplicates). First real classify+triage pass running
  on host (output/logs/first_pass.log, ~8 s/item).

## Progress
- [x] Plan, approvals, host survey, disk cleanup, repo skeleton, conventions, API contract
- [x] Core (config/errors/db/gate/gpu/llm client/prompts/schemas) + orchestrator + CLI + notify
- [x] Docker infra, DB schema (0001, 0002), AGE graph, seed, memory layer
- [x] Fetch layer (40 sources), security heuristics + fixtures + guard L1/L2
- [x] Pipeline dedup/classify/triage/analyze; deep search ReAct + SearXNG (google/bing news/google news work)
- [x] Report layer (docx RTL + QA citations), Web API (FastAPI), React UI (9 screens), evals harness (40 golden)
- [x] Bake-off + ADR-001; embeddings test; unit tests 108+54+21+8+... all green
- [x] install.ps1/.sh, README, RUNBOOK, MODULES.md
- [ ] First pass: classify → triage → analyze → report on the 220 items (running / next)
- [ ] Deep-search end-to-end test from host (`eo investigate "..."`) once GPU is free
- [ ] Rebuild images with latest code (running), `wsl --shutdown` to apply .wslconfig, `docker compose up -d` all,
      verify agent container boots + scheduler registered, isolation script
- [ ] First nightly run 2026-09-05 01:00 → docx at ~06:00

## Known gaps / next
- Codex CLI writes blocked by the Claude Code permission classifier until the user runs
  scripts/host/codex_sandbox_setup.ps1; agy writes only inside its scratch workspace (use `agy -p > file`).
- Cross-language dedup: arctic-embed2 he↔en pair similarity ≈ 0.53 < threshold 0.92, so only same-language
  duplicates merge. Phase B: entity+date matching for cross-lingual merge.
- Windows Firewall rules (scripts/host/firewall_ollama.ps1) need the user (admin).
- Sources blocked by robots/Cloudflare are logged in sources.yaml (`verified: false`).
