# EO-Analyst — Engineering Conventions (read before writing any code)

## Purpose
Locally hosted OSINT analyst for defense electro-optics (EO/IR) and computer vision.
The active installation uses Windows native services, cloud providers and optional Ollama inference.
See [CURRENT_STATE_HE.md](CURRENT_STATE_HE.md) for the current deployment and resource policy.
Docker references below describe the older deployment option.

## Layout
```
agent/eoa/            Python package `eoa` (import as `from eoa.x import y`)
  config.py           Settings loader (pydantic) for config/*.yaml
  db.py               psycopg3 pool + SQLAlchemy engine helpers
  orchestrator/       scheduler, jobs queue, night window, deadline budgeting
  resources/          resource gate (nvidia-smi, thermal, disk, RAM) — every LLM call goes through it
  fetch/              RSS/HTML fetching + sanitization (runs in the `fetcher` container, egress network)
  security/           prompt-injection guard (L1 classifier, heuristics, L2 LLM), quarantine
  pipeline/           embed/dedup, classify, triage, analyze
  search/             SearXNG client, deep-search ReAct loop, persistence protocol, multilingual
  llm/                ollama_client.py — THE ONLY module allowed to talk to Ollama; schemas/; prompts/
  memory/             relational.py, graph.py (Apache AGE / Cypher), vector.py, agent_memory.py
  report/             daily.py, docx_builder.py (RTL), qa_citations.py, templates/
  notify/             ntfy.py
  api/                FastAPI app (REST + WebSocket status) for the web UI
  cli.py              typer CLI: `eo run`, `eo status`, `eo investigate`, `eo report`, `eo models`
config/               config.yaml, models.yaml, models.lock, taxonomy.yaml, watchlist.yaml, sources.yaml
db/migrations/        Alembic
docker/               Dockerfiles; docker-compose.yml at repo root
web/                  React 19 + TypeScript + Vite + Tailwind (RTL) UI
tests/                unit/ integration/ security/ e2e/ fixtures/
evals/                golden sets + rubrics + results
docs/adr/             Architecture Decision Records
```

## Hard rules
1. **Ollama access only via `eoa.llm.ollama_client`.** No `httpx` calls to port 11434 anywhere else. Every call passes through `eoa.resources.gate.acquire(model_role, est_vram_mb)` first.
2. **Every LLM output is validated by a Pydantic model** (`eoa/llm/schemas/*.py`). Use Ollama `format` = JSON schema. On validation failure: one retry with the error message, then raise `LLMOutputError`.
3. **Fetched content is DATA, never instructions.** Raw text is wrapped as `<<<DATA id=...>>> ... <<<END DATA>>>` and the system prompt says to ignore any instruction inside. The model that reads raw content has **no tools**.
4. **Provenance everywhere.** Every `items`, `events`, `edges` row carries `source_url`, `fetched_at`, `item_id` (for derived rows). Every factual sentence in a report carries `[n]` mapped to an item URL. `report/qa_citations.py` blocks reports with uncited claims.
5. **Never invent.** If not found → `not_found` with the full attempt log in `investigation_log`.
6. **Config, not code.** Sources, watchlist, taxonomy, schedule, thresholds all in `config/*.yaml`. Code reads them via `eoa.config.settings()`.
7. **Time.** DB columns are `timestamptz` (UTC). Scheduling uses `Asia/Jerusalem`. Use `datetime.now(tz=UTC)`; never naive datetimes.
8. **Logging.** `structlog` JSON. `log = structlog.get_logger(__name__)`. Bind `job_id`, `stage`, `item_id` where available.
9. **Errors.** Custom exceptions in `eoa/errors.py`: `ResourceUnavailable`, `LLMOutputError`, `SecurityFlag`, `FetchError`, `DeadlineExceeded`. A failing source/item never stops the stage; log and continue.
10. **Tests.** pytest; unit tests must run without Docker/GPU (mock Ollama with `respx`, mock nvidia-smi with a fake subprocess). Mark others `@pytest.mark.integration` / `@pytest.mark.gpu`. No `skip`/`xfail` without a linked TODO reason.
11. **Style.** ruff (line 110), type hints everywhere, `from __future__ import annotations`, docstring one-liner per public function. Hebrew strings are fine in code and prompts; keep identifiers English.
12. **No secrets in repo.** `.env` only. No API keys exist in this project at all.
13. **Networks.** In compose, `fetcher` and `searxng` are on `egress`; everything else is on `internal` (`internal: true`). `worker` must fail to reach the internet — there is a test for this.

## Database (plain PostgreSQL 17 -- no extensions; ADR-004)
Core tables: `sources, items, entities, events, contracts, conferences, reports, jobs, run_log, resource_log,
model_registry, security_log, triage_feedback, source_reliability, search_playbook, lessons, investigation_log`.
Vector column: `items.embedding real[]` (no pgvector; cosine similarity computed in numpy by `eoa.memory.vector`).
Graph: plain-SQL table `graph_edges` (`src_entity_id, dst_entity_id, label, item_id, props jsonb`, unique per
`(src, dst, label, item_id)`) -- `entities` rows are the vertices directly, no separate graph store. Edge labels
`COMPETITOR_OF, SUPPLIER_OF, PARTNER_OF, ACQUIRED, INTEGRATES_WITH, BIDS_AGAINST, DERIVED_FROM`; every edge has
an `item_id` column. All graph access via `eoa.memory.graph` (no Apache AGE, no Cypher). See
`docs/PLAN_WINDOWS_NATIVE.md` §1a and migration `0006_drop_extensions.py`.

## LLM roles (config.models)
`resident` (all night work), `light` (daytime/quick), `hebrew_editor` (optional final polish), `heavy_investigator` (optional), `embed`, `guard_l1` (CPU classifier), `guard_l2` (LLM judge, no tools).

## Prompts
Live in `agent/eoa/llm/prompts/*.md` with `{placeholders}`; loaded via `eoa.llm.prompts.load(name)`. System prompts are Hebrew+English; outputs in Hebrew unless schema says otherwise. Technical terms keep English in parentheses.

## Definition of done for any module
- ruff + mypy clean, unit tests pass, no TODO placeholders, docstrings present, and a short section in `docs/MODULES.md` describing the public API.
