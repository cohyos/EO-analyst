# STATUS — EO-Analyst autonomous build

> If a session is resumed, read this file first. Then `git log --oneline -20`. Then `docs/MODULES.md`.

## Goal for this run (started 2026-09-04 14:00 Israel time)
Working end-to-end analyst: first real nightly run at 01:00 on 2026-09-05 producing a docx report at ~06:00 and ntfy
notifications; then the full web UI (hybrid ops console + analyst chat); then the second night with deep search.

## Decisions taken (see docs/adr/)
- 12 GB VRAM reality → single resident model architecture; bake-off decides the model (ADR-001, pending).
- Western-origin models only; Chinese models removed from Ollama 2026-09-04.
- Ollama: native Windows service, containers reach it via host.docker.internal (measure in P0.4; ADR-002).
- ntfy self-hosted in compose (port 127.0.0.1:8090), reachable from phone via Tailscale; public ntfy.sh topic
  `dissertation_editor_ysf` remains the fallback and the user-consult channel (5-minute timeout).
- Deep search: max 4 investigations per night (config `deep_search.max_per_night`).

## Progress
- [x] Plan v2 written, approved (תוכנית_פיתוח_מפורטת_v2.md)
- [x] Host survey; Chinese models removed; disk 227 GB free; .wslconfig written
- [x] Repo skeleton: pyproject, config/*.yaml, docs/CONVENTIONS.md
- [x] Core: eoa.config, eoa.errors, eoa.db, eoa.resources.gpu, eoa.resources.gate, eoa.llm.ollama_client, eoa.llm.prompts
- [ ] P0.2 models: pulling gemma4:26b, e5, embeddinggemma, granite3-guardian, ministral-3 (background)
- [ ] P0.3 bake-off (scripts/bakeoff.py via Codex → run → ADR-001)
- [ ] P1.1 docker infra (agent infra-docker)
- [ ] P1.2/1.3 schema + memory layer (agent db-schema)
- [ ] P2.1 fetch + sources.yaml (agent fetch-module)
- [ ] P2.2 security: heuristics + fixtures (agent security-fixtures); L1 classifier + L2 LLM (lead)
- [ ] P2.3 embed/dedup; P2.4 classify/triage; P3.4 analyze; P3.5 report docx; P3.6 notify; P1.6 scheduler
- [ ] P3.1/3.2/3.3 SearXNG + deep search ReAct + multilingual
- [ ] P4 web UI (FastAPI + React)
- [ ] First nightly run

## Open issues / notes
- No admin rights in the session: Windows Firewall rule for ollama.exe is prepared as scripts/host/firewall_ollama.ps1
  (user runs it elevated once). OLLAMA_HOST must be 0.0.0.0 for containers to reach it; see ADR-002 for the tradeoff.
- RAM free was only ~25 GB of 64 during the day (user workload); night runs assume ≥ 40 GB free.
