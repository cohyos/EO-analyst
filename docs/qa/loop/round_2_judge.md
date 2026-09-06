# Round 2 — Independent Judge Report

**Judge:** independent read-only agent (no pipeline runs, no fixes, no OMC tools, no DB writes). DB accessed via `runtime/eoa.env` → `postgresql://eoa:***@127.0.0.1:5432/eoanalyst`, `SELECT version_num FROM alembic_version` = **`0019`** (matches repo HEAD). Live chat tested against the running server on `127.0.0.1:8765` (`POST /api/ask`), all 8 golden questions run **sequentially, last**, 300s wall-clock cap, one retry on timeout (never needed — all 8 completed first try). Verdict written to `docs/qa/loop/round_2_judge.json`.

## Scores (judge component; merge with `round_2_auto.json`'s deterministic component per the 0.5/0.5 rubric)

| Domain | Score /100 | n | Round 1 | Delta | One-line verdict |
|---|---|---|---|---|---|
| D1 Classification/triage | 58 | 40 | 40 | +18 | Null-triage repair (30 items) *actually landed this time* on the DB this judge reads — verified 0 remaining violations. But items 52/56/57 (worse variant) and item 39 (truncation) remain untouched. |
| D2 Summary/so-what | 53 | 40 | 55 | -2 | Untouched by round 2; templated phrase count flat (32 vs 34). |
| D3 Events/entities | 58 | 40 | 38 | +20 | Three genuine, verified entity-level fixes (BlueHalo, Palantir, Norkin merge). Event-row dedup and the amount-unit bug explicitly deferred — unit bug is now visibly wrong in a live report. |
| D4 Deep investigations | 52 | 6 | 48 | +4 | Anchoring flag now durably applied. But an unscoped repair script silently regressed job 91's honest "not_found" into a misleading "partial/0.5" — undisclosed, found only by this judge. |
| D5 Chat ("ask the analyst") | 32 | 8 | 28 | +4 | Repetition loop genuinely, durably fixed. But an independent re-run surfaced two NEW severe fabrications (Q2, Q4) that round 2's own single-sample verification missed entirely. |
| D6 Daily/weekly reports | 40 | 2 | 35 | +5 | Weekly's runaway-JSON bug fixed at the schema level (verified, 292/292 tests green) — but the live rebuild still delivers zero synthesis to the reader, same as before. |
| D7 BD focus reports | 35 | 6 | 62 | -27 | **Regression.** bd_us — round 1's one clear win — failed QA 8 times today and the live file is now worse than what round 1 saw. bd_territory.py was never touched this round. |
| D8 Patent survey | 45 | 2 | 45 | 0 | Entirely untouched; every round-1 defect (exclusivity overclaim, untraceable relationship) confirmed unchanged despite 9 same-day regenerations. |
| D9 Tenders/forecasts/conferences | 45 | 3 | 42 | +3 | Tenders table still 0 open rows, unchanged across all 3 rounds. Source freshness keeps improving organically (53/60 now vs. round-2-auto's own 30/60 snapshot). |

## Cross-cutting finding: the DB-environment-mismatch bug from round 1 is fixed

Round 1's repairs ran against the wrong Postgres instance (port 5433) and several "fixed" claims never reached the production DB. Round 2's fixes explicitly ran against `127.0.0.1:5432`, and **every round-2 data-repair claim this judge checked was independently confirmed present** on that DB — a genuine process fix worth noting even though it's not itself a QA-domain score.

## New finding this judge surfaced (not in either fixes doc): round-2's own live verification under-samples model variance

Round 2's chat-fixes report ran each golden question **once** and reported that sample as the round's result. This judge's independent second sample of the same 8 questions reproduced the loop fix cleanly, but on **Q2 and Q4** produced materially different, more severe hallucinations than round 2's own reported run:

- **Q2 (Iron Beam):** round 2 reported a clean answer correctly separating Iron Beam from Iron Dome. This judge's re-run instead fabricated a Rafael "Iron Beam" contract narrative that is actually AeroVironment's own, unrelated $465M laser program (item 96) — none of the 7 retrieved sources supports the connection.
- **Q4 (DROIC):** round 2 reported a hedged, correctly-labelled answer. This judge's re-run **invented a named professor, university, and research project** ("Kunat Pipatanakul", a Thai university, "Wayu-Paxa-OCR-Zero") by conflating two unrelated retrieved arXiv papers (SAR super-resolution + Thai OCR) into one narrative.

Both are worse failure modes than anything round 1 documented for these questions, and both slipped past round 2's guards (anchor check, citation check, entity-canonicalization) because the fabricated text still contains citation markers and the nominal topic word.

## Evidence log (selected)

### D1
- Items 1,4,7,14: `level='archive', score=1, triage_reason='gate:stub_content_cleared_non_defensible'` — confirmed, durable.
- Items 52,56,57: still `domain=NULL`, `processed_stages=['embed_dedup','classify']` — unchanged.
- Item 24: `subdomain='detectors_fpa'` — confirmed landed (round 1's claim now true).
- Item 39: `summary_he='עדשה חדשה למטע'` — still truncated, unchanged.

### D3
- Item 47: `entities_mentioned=['Northrop Grumman','AeroVironment','Anduril','US Army','US Navy']` — BlueHalo gone.
- Item 50: `entities_mentioned=['US Army','Anduril','Palantir']` — Palantir recalled.
- Entity id 1025 ("Amiram Norkin") gone; id 258 now `kind='person'`.
- Item 81: still 7 event rows (22,23,24,221,222,223,224) — unchanged.
- Events 55/84/89/121: `amount_usd` still `540.7`/`464.8`/`464.8`/`464.8` (bare millions) — now visibly wrong ("465 USD") in the freshly-rebuilt `weekly_2026-09-06.md`'s own events table.

### D4
- Jobs 46/47/86/91 all carry `legacy_unanchored=true` — confirmed.
- Job 91: `outcome='partial'`, `confidence=0.5`, `answer_he='לא נמצא מידע מספק במסגרת התקציב'` (still says nothing was found), `updated_at` identical across all 4 jobs (2026-09-06 11:02:06) — traced to `scripts/mark_legacy_investigations.py`'s blanket "not_found+sources→partial" pass sweeping up a job it wasn't meant to touch, undoing round 1's verified fix.

### D5 (live retest, sequential, first attempt only)

| Q | Subject | Seconds | Chars | Verdict |
|---|---|---|---|---|
| 1 | XM30 | 82.3 | 3299 | Clean, no loop, well-cited. |
| 2 | Iron Beam | 53.9 | 2191 | **New severe fabrication** — Rafael/AeroVironment conflation, not supported by any of 7 sources. |
| 3 | Greece/LORA | 151.1 | 2218 | Substance still wrong (never mentions LORA) but now visibly double-flagged; a literal `[n=5]` template token leaks into the heading. |
| 4 | DROIC | 101.8 | 4483 | **New severe fabrication** — invented professor/university/project name from two conflated unrelated papers. |
| 5 | Skyranger | 53.6 | 2200 | Clean, reasonable comparison. |
| 6 | AUSA 2026 | 40.7 | 3253 | Clean, appropriately hedged. |
| 7 | EO/IR RFI | 31.3 | 2391 | item 127 correctly labelled academic, not RFI — 3c fix confirmed. |
| 8 | SPECTRO ISR | 59.5 | 2677 | Clean, no loop. |

### D6
- `weekly_2026-09-06.md` (report id 38): both LLM calls completed cleanly within budget (104.6s/105.8s, no truncation) — schema fix confirmed. But cross-domain sentence duplication in the trend narrative tripped the QA gate twice, so the reader still gets tables-only, zero synthesis — identical outcome to before.
- `daily_2026-09-06.md`: unchanged — still tables-only, duplicate-title collision confirmed at rows 41/54, both off-topic items (IDF-scapegoat opinion piece, Lebanon-ridge piece) still in the industry table.

### D7
- `reports` table shows `bd_territory/US` rebuilt **8 times today** (02:48–06:10), every attempt `qa_passed=false`. The live `bd_us_2026-09-06.md` now has a warning-banner exec summary with one surviving sentence — worse than round 1's finding — because `bd_territory.py` was never migrated to the structured schema.
- bd_us conference dates: **0/4 matched** the DB (worse than round 1's 2/4) — e.g., AUSA cited "2026-10-01 to 2026-10-18" vs. DB's 2026-10-12 to 2026-10-14.
- bd_kr (genuinely rebuilt, report id 37): conference dates **match the DB exactly** — confirms the date-source fix works on freshly-rebuilt reports; bd_us just wasn't rebuilt with it despite 8 attempts.
- Doubled-ASCII-quote defect (`ארה""ב`) persists, 7 occurrences, unique to bd_us.

### D8
- Anduril patent survey (report id 36, latest of 3 regenerations today): exclusivity overclaim and untraceable Anduril-Elbit "Sigma 155 howitzer" relationship both persist verbatim. Timeline/CPC tables still empty in both surveys inspected.

### D9
- Tenders: still 5 rows, all closed, every deadline already past — unchanged across all 3 rounds.
- Source freshness: 53/60 now (vs. round_2_auto.json's own same-day 30/60) — organic continued improvement from round 1's fix.

## Worst 10 items (this round)

1. **D5** Q4 fabricated professor/institution/project — a more severe, more specific hallucination category than round 1 found, missed by round 2's own single-sample verification.
2. **D5** Q2 Iron Beam/AeroVironment conflation — directly contradicts round 2's own reported live result for this exact question.
3. **D7** bd_us regression — 8 same-day rebuild failures, live file now worse than round 1's finding, conference dates 0/4 matched.
4. **D1** Items 52,56,57 still pre-triage (domain=NULL) — unchanged, out of scope again.
5. **D3** Item 81 duplicate events + amount_usd unit bug — both deferred again, unit bug now visibly wrong in the live weekly report.
6. **D4** Job 91 outcome regression — an undisclosed side effect of an unscoped repair script, found only by this judge.
7. **D8** Anduril patent-survey overclaims, unchanged, despite 9 same-day regenerations.
8. **D9** Tenders table — unchanged across all 3 rounds now.
9. **D6** Weekly/daily — schema-level fix real, but zero user-visible synthesis improvement.
10. **D5** Q3 LORA substance still wrong, plus a new `[n=5]` template-leak rendering bug.

## What round 3 should do, ranked by expected score gain per unit of effort

1. **Scope the `mark_legacy_investigations.py` "not_found→partial" pass to specific job ids**, not a blanket sweep — cheap, prevents silently undoing verified fixes on unrelated jobs (D4).
2. **Investigate the Q2/Q4 fabrication class** (cross-item entity/narrative conflation under citation-guard cover) — high value, since it undermines trust in every answer that "looks" well-cited; likely needs a semantic entity-consistency check, not just anchor/citation presence.
3. **Migrate `bd_territory.py` to the structured schema** that already fixed daily/weekly — bd_us has now failed QA 8+ times in a row; this is the single most reproducible, highest-value D7 fix available and the mechanism is already proven.
4. **Fix items 52/56/57's stuck pre-triage state** — small, well-understood scope (same root cause as items 1/4/7/14, already fixed this round), likely cheap to extend.
5. **Fix item 39's truncation and the item-81 event-row dedup** — both already scoped and understood (background task spawned for the latter); low-hanging.
6. **Address the amount_usd unit bug (events 55/84/89/121)** — small, well-defined (×1000 scaling error), now directly user-visible in reports.
7. **D8 (patent survey) and D2 (templated phrasing)** — lowest urgency: no regression, but zero progress across 2 rounds; worth a dedicated pass once D5/D7 stabilize.

Full evidence is in `docs/qa/loop/round_2_judge.json`.
