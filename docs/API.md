# EO-Analyst Web API contract (FastAPI, `agent/eoa/api/app.py`)

Base URL: `http://127.0.0.1:8765`. JSON everywhere; Hebrew strings are UTF-8. Timestamps ISO-8601 with offset.
Auth: none (bound to localhost / Tailscale only). CORS: allow `http://localhost:5173` (Vite dev) and same-origin.

## Status & resources
- `GET /api/status` → `{ "at", "services": {"postgres": bool, "ollama": bool, "searxng": bool, "ntfy": bool},
  "gate": <ResourceGate.status()>, "pipeline": {"current_job": {...}|null, "queue_depth": int, "stage": str|null,
  "night_window": bool, "next_run_at": iso|null, "last_run": {"started_at","finished_at","state","stages": {...}}|null } }`
- `WS /ws/status` → pushes the same object every `api.status_push_seconds` seconds, plus `{"type":"log", ...}` lines
  from `run_log` as they arrive.

## Morning / reports
- `GET /api/reports?kind=daily&limit=30` → `[{"id","kind","period_start","period_end","path_docx","path_md","path_html","qa_passed","created_at","headline_count"}]`
- `GET /api/reports/{id}` → report row + `html` (rendered) + `open_points` (list) + `items_included` (ids)
- `GET /api/reports/{id}/file?fmt=docx|md|html` → file download
- `GET /api/morning` → `{ "report": <latest daily or null>, "headlines": [{"item_id","title","level","summary_he","url"}],
  "open_points": [ {"id","question","options","answer","assumed"} ], "night_summary": {"items_ingested","classified","red","orange","deep_searches","duration_min","errors"} }`

## Items feed (triage)
- `GET /api/items?level=red,orange&domain=&since=&q=&page=1&page_size=50&sort=score|published_at` → `{ "total", "items": [ItemCard] }`
  `ItemCard = {"id","title","url","source_name","published_at","lang","domain","subdomain","report_kind","trl","geography",
  "score","level","triage_reason","summary_he","so_what_he","entities_mentioned","tags","security_status","dedup_of","key_facts"}`
- `GET /api/items/{id}` → ItemCard + `clean_text` + `events` + `edges` + `investigations`
- `POST /api/items/{id}/feedback` body `{"user_level": "red|orange|yellow|archive", "comment": str|null}` → writes `triage_feedback`, updates `items.level`, returns ItemCard
- `POST /api/items/{id}/investigate` body `{"question": str|null}` → enqueues a `deep_search` job (priority 0 = interactive) → `{"job_id"}`

## Entities & graph
- `GET /api/entities?q=&kind=&limit=50` → `[{"id","name","kind","country","aliases","focus","item_count","last_seen"}]`
- `GET /api/entities/{id}` → entity + `timeline` (events + items, newest first) + `neighbors` (edges with labels & item_id)
- `GET /api/graph?entity_id=&depth=1&labels=` → `{ "nodes": [{"id","name","kind","country"}], "edges": [{"src","dst","label","item_id","evidence"}] }`
- `GET /api/graph/query?name=partners_of_competitors&arg=Elbit` → rows (named, pre-built Cypher only; no free Cypher)

## Investigations (deep search)
- `GET /api/investigations?limit=20` → `[{"job_id","item_id","question","state","rounds","queries","pages_read","outcome","started_at","finished_at"}]`
- `GET /api/investigations/{job_id}` → detail + `log` (investigation_log rows in order) + `answer` (InvestigationOut)
- `POST /api/investigations/{job_id}/stop` → sets a stop flag
- `WS /ws/investigations/{job_id}` → live log lines

## Ask the analyst (RAG chat)
- `POST /api/ask` body `{"question": str, "context_item_ids": [int], "context_entity_ids": [int], "history": [{"role","content"}], "provider": str|null}`
  → streams SSE (`text/event-stream`) events: `{"type":"citations","items":[{"n","item_id","title","url"}]}`,
  `{"type":"meta","provider","model"}` (U8, sent once before the first token), `{"type":"token","text"}`, `{"type":"done"}`.
  Answer text uses `[n]` markers that map to `citations`. `provider` (U8, docs/adr/005-cloud-llm-cli.md):
  `"ollama"` | `"agy[:<model>]"` | `"claude[:<model>]"` | `"codex[:<model>]"`; omit/`null` for the server default
  (`llm_providers.interactive_default`). Always forced back to `"ollama"` for anything running in the
  orchestrator/job-queue process (night pipeline, "run now", "investigate") regardless of this field.

## LLM providers (U8, docs/adr/005-cloud-llm-cli.md)
- `GET /api/llm/providers` → `{"allow_cloud", "interactive_default", "providers": [{"id","label","kind":"local"|"cloud","available","models":[str]}]}`
  (`ollama` always first/present; the three cloud entries are omitted entirely when `allow_cloud` is `false`)
- `PUT /api/llm/settings` body `{"interactive_default"?: str, "allow_cloud"?: bool, "revision"?: str}` (or `If-Match` header)
  → `{"ok", "errors": [], "revision"}`; 409 with `{"current_revision"}` on a stale revision, same
  optimistic-concurrency contract as `PUT /api/settings/config` (this patches the same file's `llm_providers` keys).

## Conferences (phase C, stub returns [] for now)
- `GET /api/conferences?from=&to=` ; `GET /api/conferences/ical` (text/calendar)

## Tenders / RFI / RFP (section 5.2, `eoa.tenders`)
- `GET /api/tenders?status=open|closed|awarded|unknown&country=&q=&min_relevance=3&limit=100` → `[TenderCard]`
  (`min_relevance` defaults to `3` -- rows below that are hidden unless the caller explicitly passes `min_relevance=0`)
  `TenderCard = {"id","source","external_ref","title","agency","country","published_at","deadline","url","cpv_naics","summary_he","relevance","matched_terms","entities","status","item_id","created_at","updated_at"}`
- `GET /api/tenders/forecasts?limit=100` → `[ForecastCard]`
  `ForecastCard = {"id","platform","buyer_country","trigger_event_id","trigger_item_id","payload_need","candidate_vendors","likelihood","window_from","window_to","rationale_he","sources","created_at","updated_at"}`

## Clarifications & feedback
- `GET /api/clarifications?open=true` → `[{"id","kind","question","options","answer","asked_at","timeout_at","assumed"}]`
- `POST /api/clarifications/{id}/answer` body `{"answer": str}`
- `GET /api/surveys/latest` → `{"id","report_id","questions":[{"id","type":"choice|scale|text","text_he","options"}],"answers"}`
- `POST /api/surveys/{id}/answers` body `{"answers": {qid: value}}` → writes feedback_surveys + derived lessons
- `GET /api/lessons` → `[{"id","kind","text","active","created_at"}]`; `POST /api/lessons` `{"kind","text"}`; `DELETE /api/lessons/{id}`

## Jobs & control
- `GET /api/jobs?state=&limit=50`
- `POST /api/run` body `{"scope": "daily|ingest|report|weekly", "mode": "eco|full"}` → enqueues; `{"job_id"}`
- `POST /api/jobs/{id}/cancel`

## Settings
- `GET /api/settings/{name}` name ∈ config|sources|watchlist|taxonomy|models → `{"yaml": str}`
- `PUT /api/settings/{name}` body `{"yaml": str}` → validates (pydantic / schema), writes file, returns `{"ok", "errors": []}`

## Errors
Non-2xx → `{"error": {"code": str, "message_he": str, "detail": any}}`.
