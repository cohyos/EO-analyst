# STATUS — EO-Analyst autonomous build

> If a session is resumed, read this file first. Then `git log --oneline -40`, `docs/NIGHT_CHECKLIST.md`,
> `docs/RUNBOOK.md`, `docs/MODULES.md`, `docs/USER_GUIDE_HE.md`, `e2e/QA_FINDINGS.md`.

## Mode
Manager mode: Fable orchestrates; cheap agents (haiku/sonnet), Gemini (`agy`, inline prompts ≤ 32 KB) and Codex
(stdin prompts; read-only) do the work. Keep developing between night runs; QA loop = Playwright (e2e/) → fix →
re-run until green → rebuild images → re-run against the live app.

## Decisions: ADR-001 models · ADR-002 Ollama native + isolation · ADR-003 fetcher bridge + ntfy relay

## Live system (2026-09-04 20:10)
- Containers rebuilt 20:00 (rebuild6): postgres, searxng (8088), ntfy (8091), fetcher (jobs + relay + raw fetch +
  title chain), agent (orchestrator, 8 scheduler jobs, tenders stage), web (UI+API 8765).
- **Playwright QA suite against the LIVE app: 166 passed / 0 failed / 2 skipped** (desktop + mobile).
- Night checklist: all green (services, Ollama, isolation PASS, scheduler registered, no stale jobs, ntfy + relay,
  wake task Ready, disk 135 GB free — the drop from 227 GB is Docker's WSL VHDX growth after ~10 image builds).
- Data: 279 items; 277 classified (120 out_of_scope); 257 triaged (4 red / 37 orange); 210 analyzed; 127 events;
  1 real tender (TED RPAS optronics, relevance 8) + 2 forecasts; 15 conferences, all with official URLs.
- Tests: 709 unit/security green; 18 live integration green; web vitest 99 green; e2e 166 green.

## Built since the previous STATUS
tenders/RFI/RFP module with strict two-signal gate + forecasts + /tenders screen · WS status fix · encoding
detection + mojibake repair · title fallback chain (61 rows repaired) · graph edge writes fixed (scalar SET params)
+ entity kind resolution · queue leases/reaper/run status/restorable backup · gate lock/eligible unload/thermal
increments · deep-search hardening (DATA-framed tool outputs, hit screening, URL allow-list, read-before-finish,
SSRF guard, honest deferral) · prompt improvements (FX arithmetic removed, lookup-table triage, platform-vs-payload
rule, rare-red) → triage no longer inflated · settings API hardening · UI: feed links/pagination/unclassified level,
item page, conferences links/ICS/details/no-link chip, resource history drawer, explain-score, DnD, PWA, replay
timeline, citation chips, a11y roles/names, Space quick-preview · install scripts, runbook, user guide, as-built.

## Running in the background
- Host catch-up loop (`scripts/catchup.sh` via ram_watch): analyze remainder → golden-set evals → first `eo run report`.
- Night run 01:00 (agent container): ingest → dedup → classify → triage → deep search (≤4) → analyze → tenders →
  report (docx) → backup/Obsidian → notify (self-hosted ntfy + relay to the public topic).

## Pending user actions
firewall script (admin) · apply_wslconfig (quiet moment/reboot) · codex sandbox setup · phone ntfy subscription
(http://100.70.157.25:8091/eo-analyst).
