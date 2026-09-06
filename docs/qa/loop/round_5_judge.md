# Round 5 — Independent Judge Report (J5)

**Judge:** independent read-only agent. No pipeline runs, no fixes, no rebuilds, no DB writes, no
git commands of any kind, no OMC tools/skills/agents, no process kills. DB read via
`runtime/eoa.env` → `postgresql://eoa:***@127.0.0.1:5432/eoanalyst` (port 5432 confirmed from the
env file itself, never 5433); `SELECT version_num FROM alembic_version` = **0025**. Live chat
tested against `POST /api/ask` on `127.0.0.1:8765` (SSE), all 8 golden questions run sequentially
and last, 36–210s each, ~9 minutes of wall-clock against a 45-minute budget, zero timeouts, zero
retries needed. Every claim below was checked against a live `SELECT` and/or the literal bytes of
a report file — not against status sections or planning docs, which are treated as claims to
verify, not evidence.

**Process disclosure:** an early `grep DATABASE_URL runtime/eoa.env` (before switching to a
redacting `sed`) printed the plaintext DB password once into my own tool output at the very start
of the session. It was never repeated, never written to any file, and does not appear in this
report or the JSON. Flagging per the "never print secrets" rule; recommend rotating the `eoa` DB
password out of an abundance of caution.

## Method

1. Read `docs/QA_CONTINUOUS_LOOP.md`, `golden_items.json`, `golden_questions.json`,
   `round_3_judge.json/.md`, `round_5_auto.json`, `round_5_chat_fixes.md`,
   `docs/PLAN_ROUND5_REPORTS.md` (all 9 package status sections), and
   `docs/REVIEW_2026-09-06_evening.md` (W1–W28).
2. Verified DB connectivity and basic counts (473 items, 175 events, 31 deep-search jobs) before
   any content checks.
3. For every "fixed" claim in `PLAN_ROUND5_REPORTS.md` and `round_5_chat_fixes.md`, ran an
   independent `SELECT` or read the actual report bytes rather than trusting the doc's own
   narrative — this is exactly how round 3 caught two false "fixed" claims (D8 Sigma-155, D2
   scope), and it caught a new one this round (D7 acquisition-watch, D4 blocked/not_found).
4. Read the five brief-named fresh report files in full (`daily_2026-09-06.md`,
   `weekly_2026-09-06.md`, `monthly_2026-09-30.md`, `bd_us/il/de/gr/kr`, both patent surveys), plus
   the actual end-of-chain `daily_2026-09-07.md` and `bd_eu/in_2026-09-07.md` once file-mtime
   inspection revealed the brief's named daily file was stale (see D6 finding).
5. Ran all 8 golden chat questions once each, sequentially, last, and read every answer's full
   text plus its citation list against the underlying `items` rows.
6. Wrote `round_5_judge.json`/`.md`.

## Scores

| Domain | Score | n | R3 | Δ | One-line verdict |
|---|---|---|---|---|---|
| D1 Classification/triage | 70 | 64 | 76 | −6 | Flagship fixes (47/50/10, item 39) durable. Item 22's 3-round-old gap is now *masked* by a loosened check, not fixed, and a long-flagged miscategorisation reached a live report for the first time. |
| D2 Summary/so-what | 48 | 46 | 58 | −10 | Same 16 legacy items with the banned so_what phrase, unchanged 3rd round; newly confirmed leaking into a live chat answer too — a defect that keeps spreading rather than shrinking. |
| D3 Events/entities | 74 | 45 | 88 | −14 | DB-level dedup/unit fixes hold. Two new, real, table-level issues (a near-duplicate event row; the Owl item reaching a report table) not caught by any DB-level gate. |
| D4 Deep investigations | 42 | 15 | 55 | −13 | 'blocked' is real at the schema level (a genuine P7 win), but a cross-package rank-table gap means job 113 still displays as 'לא נמצא' in the live daily/weekly reports — a direct, undisclosed violation of this round's own explicit requirement. |
| D5 Chat | 35 | 8 | 42 | −7 | The retrieval-relevance caveat guard is verified live and works well (Q4/Q6). But 2 of 8 independently-sampled answers (Q1, Q2) contain severe, guard-uncaught, confidently-stated fabrications — one of them reproducing a class the project already claimed to have closed. |
| D6 Daily/weekly/monthly | 60 | 96 | 55 | +5 | BLUF/deltas/indicators/unified-Israel-table are real, substantial, verified wins on the freshest builds. Offset by the job-113 rendering bug, a new forecast-table duplication issue, and a stale-artifact naming gap for the file the brief actually pointed at. |
| D7 BD territory reports | 50 | 8 (5 fresh + methodology note) | 68 | −18 | Buyer-pipeline/tier/assumptions structure genuinely landed. But the flagship acquisition-watch territory-scoping bug — specifically claimed fixed on 2026-09-06 (W18) — is still live, now with an added internal duplicate row. ~40% of the automated D7 sample was stale, pre-round-5 content. |
| D8 Patent survey | 82 | 43 | 58 | +24 | The single biggest, most durable win this round: the Sigma-155 relationship edge (falsely claimed fixed in round 3) is genuinely gone, with disclosed removal counts. Unclassified-cluster fix is real but partial; one deterministic "failure" is actually a checker false negative over sound, honest engineering. |
| D9 Tenders/forecasts/conferences | 76 | 89 | 55 | +21 | Conferences and source-reliability wins hold. New forecast-table duplication issue shared with D6. Tenders substance unchanged; new low-quality "unknown"-status intake noise (not user-visible) worth a lightweight gate. |

**Weighted average (D1–D9, weights 15/15/8/12/10/15/8/7/5, same convention as round 3):
≈ 57.6/100** (was ≈ 61.5 in round 3). A real, substantial engineering effort landed this round (all
nine P1–P9 packages plus W1–W28 report "done" in the planning docs), and two domains show genuine,
durable, verifiable wins (D8 +24, D9 +21). But three domains show real regressions driven by
**cross-package integration gaps** rather than by any single team's incomplete work — the "blocked"
outcome exists correctly in the DB but is invisible in the report a reader actually sees (D4/D6);
the acquisition-watch scoping fix that one package (P6/BD) believed was already handled by another
package's territory filter is still leaking (D7); and the chat-answer surface, entirely separate
from the report-generation prompts P3 targeted, keeps producing fabrication shapes each round's
guards did not anticipate (D5). The pattern from round 3 — "every round's guard closes the shapes
it was built for; new shapes appear in the next live sample" — continues undiminished for D5, and
has now spread to D4/D6/D7 in the form of package-boundary gaps rather than model hallucination.

## Per-domain findings and evidence

### D1 — Classification/triage (70, n=64)

- **VERIFIED, durable (2nd round holding):** items 47, 50, 10 remain `score_level='orange'`,
  `score=6-7`; item 39 remains `red`/10 with a full, substantive `summary_he`/`so_what_he`. Direct
  query, no regression.
- **VERIFIED, unchanged convention:** items 52/56/57 `dedup_of` correctly populated.
- **NOT LANDED, masked:** item 22 still has `entities_mentioned=[]` on the live DB — 4th round
  running. `round_5_auto.json`'s own check was *widened* this round to also accept a populated
  `key_facts` array as satisfying `entities_mentioned_nonempty_in_scope`; item 22 passes only
  because it has 8 key_facts, not because its entities were ever backfilled. The scorer was
  relaxed, not the defect fixed.
- **NEW:** `round_5_auto.json` flags a fresh `reason_score_level_consistency` failure on item 5604
  (59/60, was clean) — not investigated at content level given time budget.
- **NEW, downstream-visible:** the "Western Burrowing Owl conservation" item (first flagged as a
  borderline miscategorisation risk in round 3's rotating sample) now appears in a live report
  table for the first time (see D3).

### D2 — Summary/so-what (48, n=46)

- Direct query: `so_what_he ILIKE '%מחזק את מעמד%'` returns **exactly 16 items**, and they are the
  **identical 16 ids** as round 3 (15, 26, 51, 70, 82, 89, 120, 126, 143, 149, 157, 313, 747, 1011,
  1353, 1493), all dated ≤2026-09-04. No new items reproduce it at the item-analysis level — a
  small positive — but round 3's "progressive nightly-backfill rewrite" claim has not touched a
  single legacy row across two more rounds.
- **New, more important than the raw count:** golden chat Q8 (SPECTRO ISR) independently produced
  "מחזק את מעמדה של ישראל **בשוק הגנת הסייבר הפיזי**" — the banned crutch phrase plus an incoherent,
  apparently invented market-category label. Chat runs through `ask_grounding.py`, entirely
  separate from `qa_citations.py`'s so_what stripper — this is a third surface for the same defect,
  untouched by any P3/P8 guard.
- `round_5_auto.json`'s D2 score of 100.0 does not test for this phrase at all (only
  length/Hebrew-dominance/no-chatter/parenthesised-terminology) — invisible to automation, as in
  every prior round.

### D3 — Events/entities (74, n=45)

- **VERIFIED, durable:** 0/45 exact-duplicate event groups; round-3's amount_usd and item-81 fixes
  hold on spot-check.
- **New:** the live daily report's business-events table renders the same underlying event twice —
  once with blank party fields, once with `US Air Force`/`Massed Modular Aircraft (MMA)` — both
  `2026-09-05`, both `$10,000,000`, both citing `[4]`. The DB-level exact-duplicate gate does not
  catch this because the two rows are not byte-identical.
- **Unchanged (W5, still open per the evening review):** the Kongsberg StrikeMaster NATO-exercise
  row is still typed `ניסוי` (experiment), a kind mismatch flagged in round 4.
- **New, downstream-visible:** the Western Burrowing Owl item is now rendered in this same table,
  typed `רגולציה` with party `US Department of Defense` — a wildlife-conservation story on a US
  training range presented to readers as a defense-industry regulatory event.

### D4 — Deep investigations (42, n=15)

- **VERIFIED at schema level:** `jobs.result->>'outcome'` and `investigation_log.outcome` (id 392)
  for job 113 both correctly read `'blocked'` with a substantive `blocked_reason_he` — a real value
  distinct from `not_found`, contrary to `PLAN_ROUND5_REPORTS.md`'s own claim that the backfill
  "was not run, read-only check only."
- **SEVERE regression, undisclosed, directly violates this round's brief:** the live
  `daily_2026-09-06.md` and `weekly_2026-09-06.md` reports both render the VW/Rafael investigation
  question's "חקירות עומק" entry as **`לא נמצא: לא נמצא מידע מספק במסגרת התקציב`** — the primary,
  displayed line — with `blocked` demoted to a small parenthetical: `(ריצות נוספות: not_found,
  blocked)`. Root cause, confirmed by reading `agent/eoa/report/daily.py` lines 338–371:
  `reconcile_deep_search_reruns`'s `_OUTCOME_RANK = {"found":4,"partial":3,"off_topic":1,
  "not_found":0}` was written before `blocked` existed and was never updated; `blocked` silently
  ranks 0, ties `not_found`, and the "newest wins" tie-break happened to surface the uninformative
  run. `round_5_auto.json`'s `blocked_distinct_from_not_found` check passes anyway — it only scans
  for `לא נמצא` co-occurring with a block phrase on the *same line*, never inspecting the
  rerun-selection logic that actually produced the visible defect.
- Golden job sample (91/86/70/48/47/46): all still `not_found`/`off_topic`, low-to-zero confidence,
  no new regression; job 91's round-2 fix still holds.

### D5 — Chat (35, n=8)

- **Verified new capability:** `retrieval_relevance_caveat` — which `round_5_chat_fixes.md`'s own
  "P10" section says was explicitly *not* wired into `routes/ask.py` "per this package's file
  scope" — is live and firing correctly. Q4 (DROIC) and Q6 (AUSA) both opened with the caveat and
  stayed honestly framed as inferred context throughout, a genuine fix of the AUSA-conflation
  pattern that same doc documented as still-broken in its own live sample. Someone landed this
  after the doc was written; worth reconciling for the record.
- **Severe, new, worst-list #2:** Q2 (Iron Beam) correctly fires the anchor warning, but its
  demoted "### הקשר קרוב (לא התשובה)" section — formatted with full `תשובה ישירה`/`עובדות מרכזיות`
  headers — asserts as fact that Rafael won a ~$270M Iron Beam contract including a fictitious
  "AMPS NG" system, built by conflating Elbit's real, unrelated $270M SPECTRO ISR contract (items
  93/321) with an invented Rafael narrative. Same fabrication class the round-2/3 money-conflation
  guard was built to close, now surviving inside the anchor guard's own fallback path.
- **Severe, new, worst-list #3:** Q1 (XM30) states two fully invented "key facts" — a HEL
  laser/missile-interception system and ATR/GPS-denied navigation — attributed to item 257, whose
  actual `clean_text` (read directly) is exclusively about vehicle delivery, program value, and
  supplier roster. Zero mention of lasers or computer vision anywhere in the source. No guard
  fired.
- Q3 (Greece/LORA): known, disclosed, unchanged 4th-round bug, correctly anchor-flagged.
- Q5 (Skyranger): substantively clean and correctly avoids round-3's David's-Sling/Skynex
  equivalence error, but has two malformed citation markers (`[9]]`, `[6]]`), one referencing a
  non-existent source 9.
- Q7 (RFI): clean, honest, correctly Finnish-attributed.
- Q8 (SPECTRO): substantively accurate but see D2 for the so_what-phrase leak.
- **Net:** every fabrication *shape* explicitly targeted by round 5's own worst-list (P8) is
  verifiably fixed on this sample. But 2 of 8 independently-drawn live answers contain fresh,
  confidently-stated, structurally-embedded fabrications at least as severe as any prior round's
  flagship finding. Per this round's own scoring guidance, a fabrication in a sample of 8 caps the
  domain at 60; two such fabrications keep it well below that.

### D6 — Daily/weekly/monthly reports (60, n=96)

- **Major verified win:** on the genuinely-freshest builds (`daily_2026-09-07.md`,
  `weekly_2026-09-06.md`, `monthly_2026-09-30.md`), BLUF, the unified single-table Israel-industry
  section, the indicator-watchlist table, likelihood/confidence separation, and
  assumptions/falsifiers all render correctly with real, cited content — not decorative headers
  over empty sections.
- **Verified, correctly working (not a bug):** `daily_2026-09-07.md`'s delta section correctly
  finds report id 56 as its predecessor and renders a genuine delta.
- **Process-hygiene finding:** the brief's named fresh daily artifact,
  `output/reports/daily_2026-09-06.md`, has an on-disk mtime of **2026-09-06 23:51** — before the
  claimed 00:00–01:00 rebuild window, and before the actual end-of-chain daily rebuild
  (`daily_2026-09-07.md`, 01:17). Its own "no previous report" message is technically accurate for
  its own position (it is genuinely the first daily report ever to get a populated
  `report_state`), but is easy to misread as "the pipeline just started" against 14 pre-existing
  daily reports. Recommend the round-6 rebuild chain re-date/overwrite the exact filename the round
  brief will name, to prevent a future judge from scoring a stale file under a "fresh" label.
- **Severe, shared with D4:** job 113 renders as `לא נמצא` in both reports — see D4.
- **New, shared with D9:** the tender-forecast table has 3 near-duplicate row pairs (6/10 rows) —
  see D9.
- Deterministic findings independently confirmed: 1 uncited exec-summary analyst sentence, 14 H2
  headings (budget 12), 2 cross-table duplicate rows.

### D7 — BD territory reports (50, n=8 fresh + methodology note)

- **Methodology note:** `round_5_auto.json`'s D7 sample of 8 files is ~40% stale — `bd_eu`,
  `bd_gb`, `bd_in` (2026-09-06 versions) were built 06:49–06:56 on 2026-09-06, hours before even the
  round-4b evening chain, and `bd_gb` was never rebuilt this round at all. This judge's score is
  based on the 5 files the brief actually names as fresh (`bd_us/il/de/gr/kr`).
- **Verified structural upgrade:** bd_us/de/gr/il all carry a genuinely populated buyer-pipeline
  table, tier/rank column, BLUF, and a real "הנחות והפרכות" section.
- **Not landed, against a specific "fixed" claim:** `docs/REVIEW_2026-09-06_evening.md`'s W18 claims
  the acquisition-watch territory-scoping bug was fixed 2026-09-06. Direct read shows
  `bd_us_2026-09-06.md`'s acquisition-watch table still lists Elbit (an Israeli company)
  acquiring/partnering with Anduril, with no "גלobalי" disclosure, in a **US**-territory report.
- **New:** `bd_il_2026-09-06.md`'s own acquisition-watch table has an exact-duplicate row (`Elbit |
  שותפות | — | — | [11]`, listed twice).
- bd_kr's honest empty-activity sections are, per round 2/3 precedent, not scored as a defect.

### D8 — Patent survey (82, n=43)

- **Major verified fix, reverses a round-3 false claim:** the Anduril-Elbit "Sigma 155"
  relationship edge is genuinely gone from the live survey, with a disclosed removal count ("4
  קשרים הוסרו כי המקורות לא תומכים בהם").
- **Verified:** methodology box + coverage tag present before the executive summary; no bogus
  generic assignees found.
- **Partial:** unclassified clusters now split into TF-IDF-lite sub-groups with real per-cluster
  narrative, but every heading and the cluster table still literally say `לא מסווג` — a genuine,
  honestly-disclosed improvement in granularity, not the full fix the plan doc's language implies.
- **Checker false negative:** `cpc_assignee_matrix_present` fails only because it looks for the
  literal string "CPC" — the survey correctly falls back to a cluster×assignee matrix when CPC data
  is absent (0% coverage), exactly as designed. Sound engineering mislabeled as a failure.
- Patent abstracts remain substantively good; patent numbers correctly LTR-isolated.

### D9 — Tenders/forecasts/conferences (76, n=89)

- **Verified, durable:** 28 conferences with real dates; source-reliability column now in the
  appendix.
- **New, shared with D6:** the daily tender-forecast table's 3 near-duplicate pairs are this
  domain's core deliverable, not just a rendering nit.
- **Unchanged:** the tenders table backing reports remains 5 real rows, all with past deadlines.
- **New, internal-only:** the raw tenders table has grown to 13 rows, several with junk
  `unknown`-status, generically-titled, repeated candidates — not user-visible (correctly filtered
  to `intake='accepted'`) but worth a lightweight dedup/title gate before it accumulates further.

## Worst 10 this round

1. **D4/D6** — job 113's `blocked` outcome renders as `לא נמצא` (not `נחסם`) in the live daily and
   weekly reports, directly violating this round's explicit brief requirement — a cross-package
   integration gap (P2's rerun-rank table never updated for P7's new outcome value).
2. **D5** — Q2 (Iron Beam): a confidently-formatted, fully fabricated Rafael/Iron-Beam/SPECTRO
   narrative survives inside the anchor guard's own demoted fallback section.
3. **D5** — Q1 (XM30): two invented "key facts" (HEL laser interception, ATR/GPS-denied nav) are
   presented as verified findings from a source that mentions neither.
4. **D2** — the so_what crutch phrase, unchanged for 16 items/3 rounds, is now confirmed leaking
   into a live chat answer too (Q8), plus an incoherent invented market label.
5. **D7** — acquisition-watch territory-scoping, specifically claimed fixed (W18), is still broken
   (Elbit/Anduril row in the US report with no global disclosure) plus a new internal duplicate row
   in bd_il.
6. **D6/D9** — the daily report's tender-forecast table has 3 near-duplicate row pairs (6/10 rows),
   a new, previously-undocumented dedup gap.
7. **D8** — the "unclassified cluster" fix is partial: every heading still literally says
   `לא מסווג` despite real sub-cluster granularity underneath.
8. **D1** — item 22's `entities_mentioned=[]`, unaddressed for a 4th round, is now masked by a
   loosened deterministic check rather than fixed.
9. **D3/D1** — the long-flagged Western Burrowing Owl item has reached a live report table for the
   first time, mislabeled as a US DoD regulatory event.
10. **D6** — the brief's named "fresh" daily artifact predates the actual round-5 rebuild chain by
    over an hour; the true fresh file has a different filename.

## Comparison with round 3 — what moved and why

Round 3's dominant story was the cloud-model switch delivering one enormous, concrete D1 win plus
genuine D3/D8/D9 engineering, offset by content-quality whack-a-mole in D2/D4/D5/D6. Round 5's
story is different in kind: nine full engineering packages (P1–P9) landed, addressing nearly every
gap `docs/REPORT_TEMPLATE_BENCHMARK.md` catalogued — and the *structural* wins are real and
verified (D8 +24, D9 +21, D6's BLUF/deltas/indicators genuinely working). But three of the
regressions this round (D4 −13, D7 −18, and D2's continued stagnation) are not new model
hallucinations — they are **integration gaps between packages that each did their own job
correctly in isolation**: P7 correctly added `blocked` to the schema; P2's rerun-ranking code,
owned by a different package built earlier the same evening, was never told about it. P6/BD
believed the acquisition-watch territory filter (attributed to W18/round-4b) already handled
cross-territory leakage; it doesn't, on the exact case checked. This is a different failure mode
than round 3's, and arguably a harder one to catch with per-package unit tests, since each
package's own tests pass — only an end-to-end read of the actually-rendered report surfaces it.
D5 continues its now-three-round pattern of guards closing exactly the shapes they were built for
while new shapes appear in the next live sample; this round the two new shapes (Q1, Q2) are
arguably *more* severe than round 3's (confidently-numbered fabricated technical facts, and a
fabrication reproducing a supposedly-closed conflation class) rather than less.

## Recommended round-6 packages, ranked by expected score gain

1. **Fix `_OUTCOME_RANK` to rank `blocked` above `not_found`/`off_topic`** in
   `agent/eoa/report/daily.py`'s `reconcile_deep_search_reruns` (one line, `{"found":4,"partial":3,
   "blocked":2,"off_topic":1,"not_found":0}` or similar) and add `blocked_reason_he` to
   `collect_deep_search`'s built dict, as P7's own status doc already specified. Highest
   expected gain per effort of anything on this list — directly closes this round's #1 worst
   finding (D4+D6) and is a true one-line fix once someone notices it.
2. **Extend the D4 deterministic `blocked_distinct_from_not_found` check to inspect
   `reconcile_deep_search_reruns`'s *selected* outcome, not just literal string co-occurrence** —
   this exact defect passed a check specifically designed to catch it; the check needs to follow
   the same reconciliation path the renderer does.
3. **Re-verify and actually fix the acquisition-watch territory filter** (D7) against the specific
   live case in this report (Elbit/Anduril in bd_us) — the W18 fix evidently doesn't cover an
   Israeli-company-acquires-foreign-company shape; needs an explicit "global" tag or a real
   bidirectional territory test, plus a same-table dedup pass (bd_il's duplicate row).
4. **Add a chat-answer so_what/filler-phrase guard** in `ask_grounding.py` reusing
   `eoa.report.style`'s existing phrase list (D2/D5) — the phrase and its variants have now been
   observed in three separate surfaces (item analysis, report prose, chat answers); a single
   shared guard module would close all three at once rather than three separate fixes.
5. **Backfill the 16 legacy so_what_he rows** (D2) — small, mechanical, three rounds overdue; the
   nightly-backfill mechanism round 3 described appears to not exist or not run.
6. **Add a within-table near-duplicate check for the tender-forecast table** (D6/D9) — compare
   normalised (platform, payload, reasoning-text) tuples across rows in the *same* table, not just
   citation-set matches across different tables.
7. **Widen D5's fabrication guards toward specific-technical-claim grounding**, not just
   named-entity grounding — Q1's HEL-laser/ATR fabrication and Q2's conflated-contract-terms
   fabrication both involve grounded, real entity names (item 257 is real, Rafael is real) with
   fabricated *capabilities/relationships* attached — the same blind spot round 3 identified for
   Q5/Q7 and P8 partially closed; it needs generalising to full-sentence claim-vs-source
   entailment, not just entity-token presence.
8. **Item 22 entities backfill** (D1) — small, four rounds overdue, now worth doing before the
   loosened check hides more such cases.
9. **Rebuild `bd_gb` and re-verify `bd_eu`/`bd_in`** so the D7 automated sample stops mixing
   pre-round-5 legacy content with fresh reports — a scoring-hygiene fix, not a product fix.
10. **Standardise the round-end rebuild filename convention** (D6 process note) so the file a
    round's brief names as "fresh" is guaranteed to be the actual end-of-chain build.
