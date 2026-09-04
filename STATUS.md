# STATUS — EO-Analyst autonomous build

> If a session is resumed, read this file first. Then `git log --oneline -40`, `docs/NIGHT_CHECKLIST.md`,
> `docs/RUNBOOK.md`, `docs/MODULES.md`, `docs/USER_GUIDE_HE.md`, `e2e/QA_FINDINGS.md`.

## Mode
Manager mode: Fable orchestrates; cheap agents (haiku/sonnet), Gemini (`agy`, inline prompts ≤ 32 KB) and Codex
(stdin prompts; read-only) do the work. Keep developing between night runs; rigorous UI QA (Playwright, e2e/) → fix →
re-run until green; then rebuild images.

## Decisions: ADR-001 models · ADR-002 Ollama native + isolation · ADR-003 fetcher bridge + ntfy relay

## Live system (2026-09-04 ~19:00)
- Containers: postgres, searxng (8088), ntfy (8090), fetcher (job server + relay + raw fetch), agent (orchestrator,
  8 scheduler jobs, tenders stage in the daily run), web (UI+API 8765). Last image rebuild: 18:45 (rebuild5).
- DB migrations at head (0005). Data: 279 items — 277 classified (120 out_of_scope), 118 triaged so far with the
  tightened rubric (1 red / 21 orange / 11 yellow), 14 events; 12 tenders + 2 forecasts (quality pass in progress);
  15 conferences (URLs being seeded). Titles repaired (0 empty).
- Verified: WS /ws/status streams; settings PUT works (config mount rw); tenders API; deep search e2e `found` 0.95.
- Tests: 679 unit/security green; 18 live integration green; web vitest 98 green; Playwright suite: 19 findings on
  the 18:45 build → ui-qa-fix agent iterating.

## In flight
- ui-qa-fix: fix the 19 Playwright findings (feed level filter, conference links/labels, a11y roles/names, mobile nav)
  and iterate to green locally → then rebuild web.
- tenders (quality): two-signal gate (procurement + EO/IR term), procurement-domain search, LLM relevance gating,
  purge script for junk rows.
- conference-urls: official URLs/organizers seeded into conferences.
- Host catch-up loop (RAM-gated): triage → analyze → evals → first report.

## After the agents land
1. `docker compose build web agent fetcher && docker compose up -d` ; re-run `npm --prefix e2e test` against
   http://127.0.0.1:8765 (must be green) ; commit.
2. Night checklist (docs/NIGHT_CHECKLIST.md). Night run 01:00 → docx by 06:00.

## Pending user actions
firewall script (admin) · apply_wslconfig (quiet moment/reboot) · codex sandbox setup · phone ntfy subscription.
