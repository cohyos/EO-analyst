# Round 3 — Independent Judge Report

**Judge:** independent read-only agent (no pipeline runs, no fixes, no OMC tools/skills, no DB writes, no git changes). DB: `postgresql://eoa:***@127.0.0.1:5432/eoanalyst`, `SELECT version_num FROM alembic_version` = **0021** (matches repo HEAD). Live chat tested against `POST /api/ask` on `127.0.0.1:8765`, all 8 golden questions run **sequentially, last**, only after confirming (via `Get-CimInstance`/`tasklist`) no playwright process remained and `GET /api/status` answered in <2s. 300s cap, one retry configured (never needed — all 8 completed first try, 28–84s each). Verdict written to `docs/qa/loop/round_3_judge.json`.

### Scores

| Domain | Score | n | R2 | Δ | One-line verdict |
|---|---|---|---|---|---|
| D1 Classification/triage | 76 | 41 | 58 | +18 | Flagship bake-off defect ($464.8M/$192M contracts scored "archive") is fixed live — items 47/50/10 now orange/6-7. Item 22 still empty entities, 3rd round running. |
| D2 Summary/so-what | 58 | 42 | 53 | +5 | Templated phrase down 32→16 DB-wide, but recurs independently in the *cloud-drafted* weekly report's own prose — the fix's scope was too narrow. |
| D3 Events/entities | 88 | 174 | 58 | +30 | Both round-2 flagship defects (amount-unit bug, item-81 duplicates) genuinely and durably fixed. Cleanest win this round. |
| D4 Deep investigations | 55 | 8 | 52 | +3 | Job 91 regression fixed and disclosed. But the live weekly report exposes a new, undisclosed problem: the same investigation question re-run with contradictory outcomes, unreconciled. |
| D5 Chat | 42 | 8 | 32 | +10 | Loop fix holds; round 2's specific Q2 fabrication doesn't reproduce. But new hallucination variants (fabricated jargon, wrong entity-equivalences, self-contradiction) appear in Q4/Q5/Q7 that no guard catches — whack-a-mole, not convergence. |
| D6 Daily/weekly reports | 55 | 2 | 40 | +15 | Weekly's exec-summary/synthesis is a genuine, major leap in quality. But two severe new defects: a 164-row feedback section that's 88% spam from an old UI bug, and duplicate/contradictory investigation answers in the same document. |
| D7 BD focus reports | 68 | 4 | 35 | +33 | bd_us's structured-schema fix landed for real — populated exec summary, 4/4 conference dates now match DB. New defect: the acquisition-watch table is identical across all 4 territories, not scoped to any of them. |
| D8 Patent survey | 58 | 2 | 45 | +13 | Exclusivity overclaim genuinely scrubbed with a self-documenting log. But round_3_fixes.md's claim that the untraceable Anduril-Elbit relationship edge was "dropped" is false — it's still there, live, now duplicated. |
| D9 Tenders/forecasts/conferences | 55 | 3 | 45 | +10 | Conferences nearly doubled (15→28) with real dates. Tenders table substantively unchanged for the 3rd round — real new intake infrastructure (41 sources) produces zero visible tenders. |

**Overall judge-component weighted average: ~61.5/100 (was ~47.3 in round 2, +14.2)** — a real, substantial improvement, driven mainly by the 16:30 cloud-model switch (D1/D6/D7) plus genuine D3/D8/D9 engineering, offset by new content-quality defects in D4/D6 and D5's persistent hallucination whack-a-mole.

### Worst 10 this round

1. **D6/D4** — Weekly report renders the same investigation question multiple times with directly contradictory outcomes (Iron Beam MoD contract: "found" with a specific answer vs. "not_found" three times), unreconciled, undisclosed.
2. **D6** — Weekly's new "user feedback" section renders 164 raw rows; 144 are the echo of one 2026-09-04 UI double-submit bug on 3 items, not real activity — misrepresents the week to the reader.
3. **D5** — Q4 (DROIC) fabricates plausible ML jargon ("MAEC", "RSPEOT") attributed to Leonardo DRS — same fabrication class as round 2's fake professor, in a form the new grounded-entity regex doesn't catch.
4. **D8** — round_3_fixes.md's claim that the Anduril-Elbit "Sigma 155" relationship edge was dropped is false; it's live, duplicated (Elbit + Elbit Systems).
5. **D7** — The new acquisition-watch section is not territory-scoped: identical Elbit/Hensoldt rows appear verbatim in bd_us, bd_kr, and bd_gr.
6. **D9** — Tenders table unchanged in substance for the 3rd round (still 5 expired rows, 0 open/candidates) despite 41 newly-catalogued intake sources.
7. **D4** — Job 113's security-guard false-block is now directly visible, unexplained, in the live weekly report.
8. **D5** — Q7 self-contradicts (claims a Finnish MoD RFI was "published by the US government"); Q5 wrongly equates Israel's David's Sling with Germany's unrelated Skynex — both slip past every guard because the individual entities are real.
9. **D2** — The templated so_what phrase survives independently in cloud-drafted report prose, showing the round-3 fix (analyze.md only) was too narrowly scoped.
10. **D1** — Item 22's empty `entities_mentioned` is unaddressed across all 3 rounds now.

### Cloud vs. local (D1/D2 specifically)

Direct, live evidence that the 16:30 cloud-model switch is the dominant driver of this round's D1 gain: items 47, 50, and 10 — the exact three items `BAKEOFF.md` built its case on (a $464.8M AeroVironment production contract and a $192M Palantir/Anduril TITAN award, both scored `archive` by every local candidate including the resident DictaLM-3) — now score `orange`/6–7 on the live production DB, not `archive`. This is not a bake-off sandbox result; it's the actual pipeline output after the switch. Item 39, analysed after the switch, produced a complete, non-truncated, 8-key-fact summary, versus its pre-switch truncated fragment. However, D2's templated so_what phrase — the thing the bake-off implied was purely a local-model artifact — independently recurred in the cloud-drafted weekly report's own narrative prose (e.g., "לחזק את מעמדה" on the Hensoldt and Serbia/Elbit items), showing the crutch phrase is a Hebrew-analytical-writing habit that even Claude/Gemini fall into, not solely a DictaLM defect. Net: the cloud switch delivered the single largest, most concrete win of this round, but is not a panacea for every content-quality issue.

### What round 4 should do, ranked by expected gain per effort

1. **De-duplicate/reconcile investigation re-runs before rendering them in reports** (D4/D6) — cheap, high-value: a reader currently sees contradictory "found"/"not_found" answers to the identical question in the same document.
2. **Filter or de-duplicate the meta-feedback section** (D6) — trivial fix (group by item_id, show latest value or a count, not every raw row); currently actively misleading.
3. **Scope the acquisition-watch (A16) table per-territory** (D7) — the section exists and works mechanically; it just needs a territory filter, likely a one-line query change.
4. **Extend the so_what-phrase ban to the report-generation prompts**, not just analyze.md (D2) — the same fix, applied to a second prompt.
5. **Fix the Anduril-Elbit relationship-edge verification** — round_3_fixes.md believed this was already done; it demonstrably isn't, so the source-verification logic needs re-checking against this specific case.
6. **Extend D5's grounded-entity check to single-word technical acronyms**, not just multi-word proper nouns — directly closes the Q4-class fabrication.
7. **Item 22's entities backfill** — small, well-understood, three rounds overdue.
8. **Tenders**: the intake infrastructure (41 sources) is built; next step is loosening or auditing the LLM/gate rejection filters that are converting 21 scanned candidates into 0 visible tenders per run.

Supplementary note: D10/e2e — the playwright suite that was running throughout my review finished with `status: "failed"`, 6 failed tests (`e2e/test-results/.last-run.json`), including iPhone-Safari cases under "Ask the analyst" and "Conferences" — consistent with round_3_fixes.md's own disclosed "iPhone nav rail overflow" item still in flight. `round_3_auto.json`'s D10 entry notes "playwright run failed to produce a JSON report" for the automated scorer, so this wasn't captured in the deterministic weighted_total (86.0) — worth reconciling the reporter config in round 4.

Full evidence, quotes, and DB/file citations are in `docs/qa/loop/round_3_judge.json`.
