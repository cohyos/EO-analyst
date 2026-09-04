# STATUS — EO-Analyst autonomous build

> If a session is resumed, read this file first. Then `git log --oneline -20`, `docs/NIGHT_CHECKLIST.md`,
> `docs/RUNBOOK.md`, `docs/MODULES.md`.

## Goal for this run (started 2026-09-04 14:00 Israel time)
Working end-to-end analyst: first real nightly run at 01:00 on 2026-09-05 producing a docx report at ~06:00 and ntfy
notifications; full web UI (hybrid ops console + analyst chat); second night with deep search.

## Decisions (docs/adr/)
- ADR-001 (bake-off): `resident` = DictaLM-3.0-Nemotron-12B, `investigator` = gemma4:12b, `light` = gemma4:e4b,
  `embed` = snowflake-arctic-embed2 (1024-d). gemma4:26b rejected.
- ADR-002: Ollama native on Windows (0.0.0.0:11434, hardened env); containers reach it via host.docker.internal;
  `agent` has no internet (verified by scripts/verify_isolation.{sh,ps1}).
- ADR-003: agent↔fetcher jobs-table bridge (`ingest`, `fetch_url`); ntfy relay in the fetcher mirrors self-hosted
  messages to the public topic while `notify.mirror_to_public` is true.
- SearXNG: only google / bing / "bing news" / "google news" (+ baidu for zh, yandex for ru) return results;
  duckduckgo/brave/startpage get CAPTCHA-suspended. Configured per language in config.yaml.

## Live system (2026-09-04 ~16:00)
- Containers: postgres (healthy), searxng (dev port 8088), ntfy (8090), fetcher (job server + relay), agent
  (orchestrator: daily 01:00, pre-flight 23:30, wake guard 00:55, daytime poll, weekly Sat), web (UI+API 8765).
- Ollama server started manually with hardened env (`output/logs/ollama_serve.log`); the tray app's auto-updater
  hung and was killed. After a reboot the tray app starts with the user-scope env vars.
- Host: scheduled task "EO-Analyst Wake" 00:55; `.wslconfig` written but pending `wsl --shutdown` (apply at reboot).
- DB: ~251 items ingested, embeddings/dedup done; classify/triage/analyze catch-up loop running on host
  (`scripts/catchup.sh`, log output/logs/catchup.log) — gated by free RAM (the user's data jobs left 2–5 GB by day).
- Deep search e2e: first run reached `not_found` honestly but SearXNG returned 0 hits (engine config); fixed,
  second run in progress (output/logs/e2e_investigate2.log).
- Tests: 292 unit+security tests green (pytest), web 25 vitest green, lint clean.

## Remaining before tonight
- [ ] Rebuild web image with the API 404 fix; screenshot the real UI once (Morning page must render with zero data).
- [ ] Confirm e2e deep search reads pages and finishes with sources.
- [ ] Let the catch-up finish (or at least classify/triage) so the first report has content; optionally run
      `eo run report` once on host to produce a first docx before the night.
- [ ] Night checklist (docs/NIGHT_CHECKLIST.md) at ~23:00.

## Known gaps / next (phase B/C)
- Cross-language dedup (he↔en similarity ≈ 0.53 < 0.92) — entity+date matching later.
- Graph edge provenance not exposed by `memory.graph.neighbors()` (API/Obsidian show unresolved source).
- Conferences tracker (FR-12), weekly/monthly reports, surveys UI wiring end-to-end — phase C.
- Codex CLI writes blocked until the user runs scripts/host/codex_sandbox_setup.ps1; firewall script needs admin.
