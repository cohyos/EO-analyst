# STATUS — EO-Analyst autonomous build

> If a session is resumed, read this file first. Then `git log --oneline -20`. Then `docs/MODULES.md`.

## Goal for this run (started 2026-09-04 14:00 Israel time)
Working end-to-end analyst: first real nightly run at 01:00 on 2026-09-05 producing a docx report at ~06:00 and ntfy
notifications; then the full web UI (hybrid ops console + analyst chat); then the second night with deep search.

## Decisions taken (see docs/adr/)
- 12 GB VRAM reality → single resident model architecture; bake-off decides the model (ADR-001, running).
- Western-origin models only; Chinese models removed from Ollama 2026-09-04.
- Ollama: native Windows service; user-scope env vars set (OLLAMA_HOST=0.0.0.0:11434, NO_CLOUD, MAX_LOADED=1,
  KV q8_0, flash attention, GPU_OVERHEAD 1.2 GB, KEEP_ALIVE 30m) — take effect after Ollama restart.
- ntfy self-hosted in compose (127.0.0.1:8090), Tailscale for phone; public topic `dissertation_editor_ysf` = fallback
  and user-consult channel (5-minute timeout).
- Deep search: max 4 investigations per night (config `deep_search.max_per_night`).
- Embeddings: `multilingual-e5-large-instruct` does NOT exist in the Ollama library. Candidates pulled:
  embeddinggemma (768-d), snowflake-arctic-embed2 (1024-d), nomic-embed-text-v2-moe. Decide by a Hebrew↔English
  similarity test; schema currently vector(1024) (change with a migration if a 768-d model wins).
- Codex CLI: installed + logged in, but the Claude Code permission classifier blocks its Windows sandbox setup and the
  bypass flag; user must run scripts/host/codex_sandbox_setup.ps1 once. Until then Codex = read-only reviews.
  agy (Gemini) writes only inside its own scratch workspace; use `agy -p ... > file` generation pattern.

## Host state
- Windows scheduled task "EO-Analyst Wake" daily 00:55 (WakeToRun) registered (user scope).
- `.wslconfig` written (36 GB / 16 CPU / 8 GB swap) — applies after `wsl --shutdown` (not done yet: Docker running).
- Docker: postgres (PG17 + AGE 1.7 + pgvector 0.8.6), searxng, ntfy up. Ports 127.0.0.1:5433 / :8090.
- DB: migrations 0001+0002 applied; graph init OK; 40 entities + 11 conferences seeded; 40 Entity vertices.
- First ingest (host-run): 220 items from 40 sources (210 en / 10 he). Sources blocked by robots/Cloudflare are logged.

## Progress
- [x] Plan v2, approvals, host survey, disk cleanup
- [x] Repo skeleton, config, conventions, API contract (docs/API.md)
- [x] Core: config, errors, db, resources (gpu, gate), llm client + prompts + schemas
- [x] Docker infra + DB schema + memory layer (relational/graph/vector) + seed
- [x] Fetch layer + 40 sources; security heuristics (83 injection fixtures, 95.6% detect, 0% FP) + guard (L1/L2)
- [x] Pipeline: dedup, classify, triage, analyze; deep search ReAct + SearXNG client; orchestrator + CLI; ntfy
- [ ] Bake-off running (evals/results/bakeoff_run.log) → ADR-001 → set config.models
- [ ] Embedding model decision → run dedup → classify → triage → analyze on the 220 items (first real pass)
- [ ] Report layer (agent report-docx, running) → first docx
- [ ] Web API (agent api-backend, running) + React UI (agent web-ui, running)
- [ ] Unit tests for core (agent), integration smoke, isolation verification
- [ ] Containerize agent/fetcher/web; start orchestrator for tonight's 01:00 run
- [ ] First nightly run 2026-09-05 01:00

## Open issues / notes
- No admin rights: Windows Firewall rule for ollama.exe not created (scripts/host/firewall_ollama.ps1 to be written).
- RAM free ~25 GB of 64 during the day (user workload).
- Python 3.14 on host: psycopg_pool prints a harmless finalization warning at exit (fix: atexit close_pool).
