# STATUS — EO-Analyst autonomous build

> If a session is resumed, read this file first. Then `git log --oneline -30`, `docs/NIGHT_CHECKLIST.md`,
> `docs/RUNBOOK.md`, `docs/MODULES.md`, `docs/USER_GUIDE_HE.md`.

## Goal (started 2026-09-04 14:00 Israel time)
Working end-to-end analyst with a first real nightly run at 01:00 on 2026-09-05 (docx ~06:00, ntfy), full web UI,
and phase B/C features (user: "אל תעצור בהמתנה ללילה").

## Decisions (docs/adr/): 001 models · 002 Ollama native + isolation · 003 fetcher bridge + ntfy relay

## Live system (2026-09-04 ~17:00)
- Containers: postgres (healthy), searxng (8088 dev), ntfy (8090), fetcher (job server + relay), agent (orchestrator
  with 8 scheduler jobs: daily 01:00, pre_flight 23:30, daytime_poll, weekly Sat 01:00, conference_scan monthly 02:30,
  monthly 03:30, weekly_meta Sat 06:30, conference_reminders 07:00), web (UI+API 8765).
- Guard L1 (Protect AI ONNX) loads offline inside the agent image (score 0.99999 on a canned injection).
- Verified end-to-end: ingest 251 items; embeddings + dedup; classification visible in the UI; deep search → `found`
  0.95 with sources; isolation PASS; ntfy relay; docx sample; API + UI pages Morning/Feed/Investigations/Conferences/
  Inbox rendered. Conferences: 15 rows, one per conference per year, iCal export OK.
- Tests: 457 unit + security green; web vitest 25 green; lint clean.

## Host constraints
- The user's data jobs leave 0.5–5 GB RAM free by day → GPU stages queue. `scripts/ram_watch_resume.sh` started
  `scripts/catchup.sh` (classify → triage → analyze loop, then evals, then a first `eo run report`) — progress in
  output/logs/catchup2.log / ram_watch.log; it advances only when RAM frees.
- Ollama runs via manual `ollama serve` (tray updater hung). `.wslconfig` pending scripts/host/apply_wslconfig.ps1.

## Built this session (docs/MODULES.md has per-module API)
core config/gate/llm client · fetch (40 sources) + sanitizer · security heuristics (83 fixtures) + guard L1/L2 ·
pipeline dedup / xlang-dedup / classify / triage / analyze · deep search ReAct (persistence protocol,
read-before-finish, timeout-tolerant) · report daily/weekly/monthly docx RTL + trends + QA citations ·
orchestrator/jobs/scheduler/backup (COPY-based in container) · ntfy + clarification gate + relay · FastAPI (all
API.md routes + conferences + feedback) · React RTL UI (9 screens, error boundary, null-safe) · Obsidian export ·
evals harness + 40 golden · conferences tracker FR-12 (horizon, verify/discover, reminders, iCal) · feedback loop
(calibration, surveys, weekly meta) · graph edge provenance · install scripts, README, runbook, user guide (HE),
night checklist, host helper scripts.

## Before 01:00 tonight (automatic unless noted)
- Final image rebuild running (agent + web) — verify `docker compose ps` afterwards.
- Run docs/NIGHT_CHECKLIST.md at ~23:00 (user or next session).

## Known gaps / next
- Golden-set evals not yet run (RAM); ADR-001 numbers come from the bake-off only.
- L1 classifier catches 19% of the fixture corpus (classic overrides/prompt-leak); heuristics 85%+ and L2 cover the rest.
- Firewall script (admin) and Codex sandbox setup remain user actions; `.wslconfig` not applied.
