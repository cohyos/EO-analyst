# STATUS — EO-Analyst autonomous build

> If a session is resumed, read this file first. Then `git log --oneline -30`, `docs/NIGHT_CHECKLIST.md`,
> `docs/RUNBOOK.md`, `docs/MODULES.md`, `docs/USER_GUIDE_HE.md`.

## Goal (started 2026-09-04 14:00 Israel time)
Working end-to-end analyst with a first real nightly run at 01:00 on 2026-09-05 (docx ~06:00, ntfy), full web UI,
and phase B/C features built while waiting (per the user's instruction "אל תעצור בהמתנה ללילה").

## Decisions (docs/adr/): 001 models · 002 Ollama native + isolation · 003 fetcher bridge + ntfy relay

## Live system
- Containers: postgres (healthy), searxng (8088 dev), ntfy (8090), fetcher (job server + relay), agent
  (orchestrator; daily 01:00, pre-flight 23:30, wake guard 00:55, daytime poll, weekly Sat 01:00, conference scan
  monthly 02:30, monthly report 03:30 once the reports agent lands), web (UI+API 8765).
- Verified end-to-end: ingest (251 items), embeddings + dedup, first classifications visible in the UI feed,
  deep search on a real question → `found` 0.95 with sources, isolation checks PASS, ntfy relay to the public topic,
  docx report builder (sample), API + UI pages (Morning / Feed / Investigations screenshots OK).
- Tests: 402 unit + security tests green; web vitest 25 green.

## Host constraints observed
- The user's own data jobs leave 0.5–5 GB RAM free by day → GPU stages queue (by design). Host-side catch-up
  (`scripts/catchup.sh`) is paused; `scripts/ram_watch_resume.sh` restarts it when ≥ 10 GB stays free for 2 minutes.
  Golden-set evals (`evals/run_evals.py --set-name classify_triage`) still need a run when RAM allows.
- Ollama runs via manual `ollama serve` (tray updater hung). `.wslconfig` pending `scripts/host/apply_wslconfig.ps1`.

## Built this session (see docs/MODULES.md)
core config/gate/llm client · fetch (40 sources) + sanitizer · security heuristics (83 fixtures) + guard L1/L2 ·
pipeline dedup/xlang-dedup/classify/triage/analyze · deep search ReAct (persistence protocol, read-before-finish) ·
report daily docx RTL + QA citations · orchestrator/jobs/scheduler/backup · ntfy + clarification gate + relay ·
FastAPI (all API.md routes) · React RTL UI (9 screens, error boundary) · Obsidian export · evals harness + 40 golden ·
conferences tracker FR-12 (horizon, verify/discover, reminders, iCal, API) · feedback loop (calibration, surveys,
weekly meta) · graph edge provenance · install scripts, README, runbook, user guide (HE), night checklist.

## In flight
- reports-weekly-monthly agent (trends, weekly/monthly docx, jobs + monthly cron).
- guard-l1-onnx agent (bake the Protect AI classifier into the agent image, offline).
- conferences agent: dedupe seed vs generated rows ("AUSA" vs "AUSA 2026").

## Before 01:00 tonight
- Rebuild agent + web images with everything above (`docker compose build agent web && docker compose up -d`).
- Apply any new migration (`alembic upgrade head`), run `docs/NIGHT_CHECKLIST.md`.

## Known gaps / next
- Weekly meta summary needs a scheduler slot (Sat 06:30) once main.py is free of concurrent edits.
- Firewall script (admin) and Codex sandbox setup remain user actions.
