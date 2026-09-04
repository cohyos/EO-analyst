# STATUS — EO-Analyst autonomous build

> If a session is resumed, read this file first. Then `git log --oneline -40`, `docs/NIGHT_CHECKLIST.md`,
> `docs/RUNBOOK.md`, `docs/MODULES.md`, `docs/USER_GUIDE_HE.md`, `e2e/QA_FINDINGS.md` (when present).

## Mode
Manager mode (user instruction 2026-09-04 ~17:30): Fable orchestrates; cheap agents (haiku/sonnet), Gemini (`agy`,
inline prompts ≤ 32 KB) and Codex (stdin prompts; read-only until the user runs scripts/host/codex_sandbox_setup.ps1)
do the work. Do not wait for the night window — keep developing; run a rigorous UI/QA pass and fix.

## Decisions: ADR-001 models · ADR-002 Ollama native + isolation · ADR-003 fetcher bridge + ntfy relay

## Live system (2026-09-04 ~18:30)
- Containers: postgres, searxng (8088), ntfy (8090), fetcher (job server + relay + raw fetch), agent (orchestrator,
  8 scheduler jobs incl. tenders stage in the daily run), web (UI+API 8765). Images rebuilt after each batch.
- DB migrations at head (0005): items/entities/graph/jobs(leases)/conferences/tenders/tender_forecasts.
- Data: 252 items; catch-up loop (host, RAM-gated) classifies/triages/analyzes; triage was RESET at ~18:00 after the
  prompt tightening (old rubric rated 17/20 red) — re-triage happens in the catch-up rounds.
- Tenders: 12 tenders (TED, UK Contracts Finder, SearXNG RFI/RFP queries) + 2 forecasts live.
- Tests: 664+ unit/security green; 18 live-stack integration tests green; web vitest 69 green.

## Work landed today (highlights)
core · fetch (40 sources) · security (heuristics noisy-OR, guard L1 ONNX offline + L2, detect_text) · pipeline
(dedup, xlang dedup, classify, triage, analyze; num_predict caps) · deep search (DATA-framed tool outputs, hit
screening, URL allow-list, read-before-finish, SSRF guard) · reports daily/weekly/monthly + trends + tenders section ·
orchestrator (leases, reaper, run status, restorable backup) · gate (lock, eligible unload, thermal increments) · ntfy +
relay · FastAPI (all routes incl. conferences, feedback, tenders; settings hardened) · React UI (feed links/pagination/
unclassified level, item page, conferences links+ICS+details, resource history drawer, explain-score, DnD, PWA) ·
Obsidian export · evals harness · conferences tracker · feedback loop · docs (README, RUNBOOK, USER_GUIDE_HE,
AS_BUILT_HE, NIGHT_CHECKLIST, ADRs) · reviews: Codex security review (30 findings → fixed), Gemini prompt review → applied.

## In flight
- ui-qa-playwright: e2e/ Playwright suite against the live UI → e2e/QA_FINDINGS.md (then a fix cycle).
- backend-fixes-c: WS /ws/status closes immediately (bug) + ingestion encoding/mojibake repair.
- ui-tenders: /tenders screen (open tenders + forecasts), Morning tile, night-run replay timeline, citation chips.
- Then: rebuild images, re-run QA suite, fix, repeat until clean; night run 01:00.

## Host constraints
- User data jobs leave 0.5–16 GB RAM free (varies); gate queues on low RAM. Ollama via manual `ollama serve`.
- Pending user actions: firewall script (admin), apply_wslconfig, codex sandbox setup, phone ntfy subscription.
