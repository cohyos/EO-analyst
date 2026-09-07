# Round 12 Judge Report (J12)

**Method:** independent read-only agent. No pipeline runs, no report rebuilds, no fixes, no OMC
skills/agents/tools, no DB writes, no git commands of any kind, no process kills, no stack
start/stop. `DATABASE_URL` was loaded exactly once per shell via `set -a; . runtime/eoa.env; set
+a` and passed straight into short-lived `.venv/Scripts/python.exe` + psycopg scripts
(`PYTHONUTF8=1`) — the value itself was never `cat`/`grep`/`type`/echoed. Every query confirmed
`inet_server_port()`=5432, `current_database()`='eoanalyst', `alembic_version`='0029'.

All 8 golden chat questions (`docs/qa/loop/golden_questions.json`) were asked live against
`POST http://127.0.0.1:8765/api/ask`. A first attempt had a client-side SSE-parsing bug (it looked
for an `event:` line the server never sends — the server emits bare `data: {"type": ..., ...}`
frames per `agent/eoa/api/routes/ask.py`'s `_sse()`), so `answer_final` payloads were not captured
even though all 8 server-side answers completed successfully with 0 errors. The parser was fixed
and all 8 questions were re-asked and captured cleanly, 46–145s per question, comfortably inside
the 5-minute per-question / 45-minute total budget.

**Freshness, verified not assumed:** every judged report file (daily/weekly/monthly, all 8 BD
territories, both fresh patent surveys, all 6 product-line reports) was built strictly *after* the
round-12 source-file edits (`agent/eoa/patents/cluster.py` 17:07, `report/indicators.py` 17:12,
`report/product_line.py` 17:19, `report/monthly.py` 17:24, `api/ask_grounding.py` +
`api/routes/ask.py` 17:21–17:22) and after the single `api.startup` log line for today
(15:32:55 log-time, no restart since). Every judged report and every live chat answer therefore
reflects round-12 code, not stale pre-fix behavior.

**Process finding, verified via file mtimes/diffs, not asserted:** `docs/qa/loop/round_12_fixes.md`
documents only **one** of **three** code packages that actually shipped this round (the single
`### R12-reports` section, covering D6/D7). Two more real, substantial, independently-verified-live
changes shipped with **no fixes.md section at all**: `agent/eoa/patents/cluster.py` (the D8
cluster-label fix) and `agent/eoa/api/ask_grounding.py` + `routes/ask.py` +
`tests/unit/test_ask_round12.py` (a D5 package whose own test-file docstring explicitly says it
targets "round_11_judge.md ... worst-list #1, #6"). Every claim below was checked directly against
the live DB, live API, live files, or live source code — nothing here rests on `round_12_fixes.md`'s
own prose alone.

## Scores (weighted per docs/QA_CONTINUOUS_LOOP.md; D10 skipped, deterministic e2e)

| Domain | R11 | **R12** | Δ | n | One-line why |
|---|---|---|---|---|---|
| D1 | 92 | **92** | 0 | 40 | 0 score↔level mismatches, 16/334 orphans, 63/63 corroboration — all stable |
| D2 | 86 | **86** | 0 | 8 | not a round-12 target; spot-check only, held steady |
| D3 | 92 | **96** | +4 | 20 | "Operation Atlantic City" leak into monthly's landscape/glossary tables (round 11 worst #9) confirmed fixed |
| D4 | 94 | **94** | 0 | 8 | jobs 156/160/161 + provenance API re-confirmed live, unchanged |
| D5 | 83 | **89** | +6 | 8 | round 11's top-2 worst items (סי. fragment, leading-space) durably fixed; Q6 shows a new incoherent-opening variant, Q5 only partially fixed |
| D6 | 87 | **95** | +8 | 3 | monthly indicator section landed (4 rows, cited), weekly indicator cap fixed (10→8 rows) |
| D7 | 88 | **87** | −1 | 14 | pl_mws_eo item-scope fix works; patent-dedupe fix does NOT work live — real duplicates persist |
| D8 | 74 | **93** | +19 | 16 | cluster headers now technology terms with CPC in parens — round 11's headline regression is fixed |
| D9 | 94 | **93** | −1 | 9 | stable; one new low-severity junk-tender note (archived, no live impact) |

**Weighted (D1–D9, brief's own weights, out of 95): ≈86.9** (round 11: ≈88.0 out of the same
95-point scale by this judge's independent recompute — see round 11's own report for its stated
≈88.0). Net gain driven almost entirely by three concretely-quoted, independently-reproduced fixes
— D8's cluster labels, D6's monthly-indicator-section + weekly-cap, and D5's two top worst-list
items — offset by D7's patent-dedupe fix not actually working and D9's minor new junk-tender note.

## What round 12 actually shipped (and what it claims to have shipped)

`docs/qa/loop/round_12_fixes.md` contains exactly one `### R12-*` section, **R12-reports**, which
targeted round 11's D6/D7 worst-list items #3, #4, #8, #9. Its own text repeatedly says live
verification of all four findings was **blocked on DB access** (a concurrent package saturating
`max_connections=60`) and hands the lead a set of commands to run once access freed up. This judge
found live evidence that those commands (or equivalent ones) **were** run afterward: `alembic_version`
is `0029` (the migration R12-reports wrote), `indicator_watchlist` has 4 `kind='monthly'` rows, and
every fresh report file postdates the fix. Three of R12-reports' four findings are confirmed fixed
live; the fourth (patent dedupe) is confirmed **not** fixed despite the fresh rebuild — see D7 below.

Separately, two more code packages shipped this round with no `round_12_fixes.md` section:
`agent/eoa/patents/cluster.py` (D8's cluster-label fix, confirmed live in both fresh surveys) and a
D5 package touching `agent/eoa/api/ask_grounding.py`, `routes/ask.py`, and a new
`tests/unit/test_ask_round12.py` (32+ tests across 6 classes) that — per its own docstring — targets
exactly round 11's worst #1 (the "סי." fragment) and worst #6 (the leading-space bug). Both are
confirmed working live in this round's chat sample. This is a genuine loop-process gap worth a
future round's attention: two-thirds of this round's real engineering work has no written record in
the file the brief and the loop's own triage step both point to.

## Verified evidence, by domain

### D3 — events/entities (96, +4)

The round's cleanest fix. `output/reports/monthly_2026-09-30.md` was grepped for "Atlantic City" —
it appears **exactly once**, on line 89, inside the events table via `events.program`, which is
precisely where the brief says it belongs. The two tables it used to leak into were read in full:
all 7 "נוף תחרותי" competitive-landscape subsection tables (no Atlantic City row in any) and the
full "שינויים ברשימת המעקב" watchlist glossary list (30+ entities read line-by-line, no Atlantic
City entry — while legitimate `kind='program'` entities like "Next-gen targeting pod" and "Defense
Innovation Unit (DIU)" correctly still appear, proving the fix is a name-pattern filter, not a
blanket kind exclusion). Source code confirms: `monthly._is_exercise_or_operation_label` (English
`^operation ` prefix or a bare Hebrew תרגיל/מבצע token) is called from both `players_map` and
`watchlist_changes`, exactly as `round_12_fixes.md` describes.

### D6 — reports (95, +8)

Two major, DB-level-confirmed fixes. First: `monthly_2026-09-30.md` now has a
`## מעקב אינדיקטורים` section (line 316) with 4 rows, all carrying populated evidence citations.
This isn't just a rendering change — `indicator_watchlist` now has 4 rows with `kind='monthly'`
(there were 0 before), which only exists because migration `0029` (widening the `kind` CHECK
constraint) is applied (`alembic_version='0029'`, confirmed live) *and* the new
`build_monthly` → `indicators.build_indicator_watchlist_section(kind="monthly")` wiring actually
ran and persisted data — report_id 136 was built at 17:45:26, strictly after the 17:24 edit to
`monthly.py`. Second: weekly's indicator table is now exactly 8 rows (was 10 as of round 11),
confirmed by direct count of the rendered table; `indicators.py`'s `_cap_watchlist_rows` call was
confirmed in code to no longer be gated `if kind == "daily"`. The one thing that did *not* land:
2 of those 8 weekly rows still show `—` (no evidence) — the row-count cap is fixed, the
underlying evidence-population gap is not, and was never part of this round's targeted fix.

### D8 — patent surveys (93, +19)

The single biggest score swing this round, and the fix with the least documentation. Round 11's
whole D8 score (74) was driven almost entirely by cluster headers being raw CPC codes
("אשכול טכנולוגי H04N5") instead of technology-term labels. This round, both fresh surveys were
grepped directly: `## אשכול טכנולוגי: הדמיה, מצלמות ווידאו (H04N5)`,
`## אשכול טכנולוגי: מיגון והגנה אקטיבית (F41H11)` — 8 of 9 clusters checked across both surveys now
carry exactly the `<technology term> (<CPC code>)` format the round-12 brief specifically asked
for. The 9th (`אשכול נושאי: lattice / mesh` in the Anduril survey) uses a different, still-thematic
label without the CPC-in-parens suffix — a minor format inconsistency, not a regression. Everything
else that was already solid remains solid: appendix reliability populated on every checked row, no
bogus assignee in either survey's own table (the one DB-wide bogus assignee, "Europe" on patent id
14, is not part of either judged survey), no unclassified cluster, a CPC×assignee matrix in the
DROIC survey, and a populated methodology box. This fix's source file
(`agent/eoa/patents/cluster.py`, touched 17:07:33, before both surveys were rebuilt at 17:16/17:24)
has zero mention in `round_12_fixes.md`.

### D5 — chat (89, +6)

All 8 live answers were read in full, twice (once server-processed but client-unparsed, once fully
captured). Round 11's headline D5 finding — a bare "סי." fragment on Q6, reproduced verbatim across
rounds 7–11 — **does not recur**: a systematic scan of every line in all 8 answers for short
(<15-char), non-list, period-terminated bare fragments found zero matches. Round 10–11's other
worst-list item, Q7's stray leading-space character, also does not recur — every line in every
answer was checked via `repr()` for a leading space, and Q7 specifically now opens cleanly with
`ה-RFI היחיד המופיע...`. Both fixes trace to an undocumented `R12-chat`-shaped package
(`ask_grounding.py`, `routes/ask.py`, `tests/unit/test_ask_round12.py`) whose own docstring
confirms it targeted exactly these two findings.

But Q6 is not fully clean: its live answer now opens with `מים, ודירוג הכנסות של חברות ביטחון
גלובליות)...` — a headless, syntactically incoherent mid-clause continuation. The answer's own
metadata shows 3–4 leading units were removed by grounding guards
(`removed_by_guard: {grounded_entity: 1, low_citation_caveat: 1, uncited_factual_claim: 2}`)
without any check on whether what survived still reads as a coherent opening. This is the *same
underlying defect class* as the old "סי." bug — guard-removal leaving an incoherent leftover — in a
*new shape* (a longer continuation, not a short bare fragment) that neither of round 12's two new
checks catches, since both are scoped to short (<3-word) fragments.

Q5 (Skyranger) shows genuine, partial progress: item 1353 is now cited (`[2]`) and its content is
used — Skyranger is characterized as an AHEAD-ammunition, gun-based, ~4km-range hard-kill system,
a specific and correct technical framing rounds 9–11 never achieved. But the exact "1,000 rounds
per minute" figure that motivated the round-11 `_relevant_excerpt` fix still does not appear
verbatim in the final synthesized text — confirmed by direct search of the live answer against the
DB row's known content. The data-layer fix appears to work; the model's own synthesis still omits
the specific number.

Every other answer (Q1/XM30, Q2/Iron Beam, Q3/LORA-Greece, Q4/DROIC, Q8/SPECTRO-ISR) reads clean,
honest, and well-cited: correct refusals with explicit low-source caveats (Q2, Q4), an explicit
warning against conflating two unrelated stories (Q3, calling the conflation "בדיה" — fabrication),
a correct identification of the only real RFI in the DB as Finnish, not American (Q7), and an
accurate, fully-cited SPECTRO ISR answer (Q8). Two round-11 minor flags — Q1's borderline "eight
prototypes" arithmetic and Q3's Cyrillic-character rendering glitch on "Barak MX" — do not recur
this round. Entailment coverage: 8 `ask.entailment_check_removed` events logged across this
session's ~16 total questions asked (this judge's two full 8-question passes), comparable in order
of magnitude to rounds 10–11's ~3/8–4/8 measured coverage — no clear breakthrough, but 0
`_skipped`/`_unavailable` events either.

### D7 — BD/product-line reports (87, −1)

BD reports remain strong across all 8 territories — every BLUF specific and cited, bd_kr's empty
stub still well-formed, bd_eu's TED tender still present. `pl_mws_eo`'s item-scope half of round
11's worst #8 is confirmed fixed: the report's own exec summary now shows zero market items (the
formerly out-of-scope-tagged sole trigger item is gone), and `product_line.py` line 168 carries the
`AND COALESCE(domain, '') <> 'out_of_scope'` clause exactly as `round_12_fixes.md` describes.

The patent-dedupe half of the same worst-list item is **not** fixed, despite the fixes doc's claim.
`pl_mws_eo_2026-09-07.md` (rebuilt 17:33:25, strictly *after* the 17:19:24 code edit — this is not
a stale-content artifact) still shows two real duplicate pairs: "US7378626B2" twice (DB rows with
`pub_number` = `'US7378626'` and `'US7378626B2'`, ids 57/55) and "US20030142005A1" twice
(`pub_number` = `'US20030142005A1'` and `'US20030142005'`, ids 45/77). The root cause is visible
directly in `product_line._dedupe_patents_by_pub_number`: it keys on exact `pub_number` string
equality with no normalization of the trailing patent-kind-code suffix, so two DB rows for what is
clearly the same patent are never recognized as duplicates of each other. The fix's own docstring
claims to keep "the first row seen per pub_number" — that claim does not hold for kind-code
variants, and this is now a concrete, reproducible gap for a future round to close.

### D9 — tenders/forecasts/conferences/UI (93, −1)

Stable across the board: tender 38/42 status unchanged, 10 forecasts (4 correctly product-line
tagged), 28 forward-dated conferences, Graph Explorer endpoints still returning 200, BD appendix
reliability columns still populated. One new, low-severity finding: tender id 34
("Expert / Coach Transformatie en Contracten Juridisch - cluster SO", a Dutch legal/HR consulting
listing with no EO/IR/defense relevance) is a clear junk candidate still sitting in the DB —
`status='archived'` so it's excluded from every active report and has zero live-content impact, but
round 11's "no junk candidates found" characterization did not specifically scan archived rows.
`security_status='blocked'` composition (36 total: 17 interstitial-pattern + 19 non-interstitial)
is identical to round 11's figures — still an open question worth a future round's attention.

## Worst 10 (most severe first)

1. **D7**: `pl_mws_eo`'s patent-dedupe fix does not work live — real duplicate pairs persist due to a `pub_number` kind-code-suffix normalization gap in `_dedupe_patents_by_pub_number`.
2. **D5**: Q6 (AUSA 2026) — the "סי." shape is fixed, but a new headless mid-clause opening appears after guard removal, same defect class, uncaught by round 12's two new checks.
3. **D5**: Q5 (Skyranger) — item 1353 now cited and used, but the specific rate-of-fire figure still doesn't reach the final synthesized text.
4. **Process gap**: 2 of 3 round-12 code packages (D5 chat fixes, D8 cluster-label fix) have zero `round_12_fixes.md` documentation despite being real and verified-working.
5. **D2/D1**: 11 in-scope items have `so_what_he IS NULL` despite completed `analyze` stage — small, pre-existing, unaddressed.
6. **D9** (informational, unchanged): blocked-item composition (36, 19 non-interstitial) unchanged since round 11.
7. **D9** (low severity): tender id 34 is a Dutch legal/HR junk candidate, archived, no live impact, newly noticed.
8. **D6**: weekly's indicator table is now correctly capped at 8 rows, but 2 of those still show empty evidence.
9. **D8** (very minor): one Anduril cluster label lacks the CPC-in-parens suffix the other 8 fixed clusters carry.
10. **D5**: entailment coverage remains modest (8 events / ~16 questions), no clear breakthrough despite round-11's tuning.

## Comparison with round 7

Round 7's headline finding — D4's report layer failing to render better rerun results over stale
text — remains fixed and was re-confirmed once again this round (jobs 156/160/161, provenance API).
Round 7 also flagged the `===SOURCES_JSON===` leak (durably gone, 0/8 again this round) and a
"seven vs eight" numeric fabrication (does not recur). What round 7 first noticed but never turned
into a tracked fix target — the "סי." fragment — took 5 rounds (7 through 11 as an unaddressed
finding, fixed in 12) to close. The lesson from round 11's own report holds: a defect needs to
become a named worst-list item to survive into later rounds' fix planning, and this round is
evidence that once it did, it got fixed cleanly and verifiably.

## Recommended round-13 packages, ordered by expected gain

1. **Fix the patent-dedupe key.** Normalize `pub_number` (strip/ignore the trailing patent-kind
   code — B1/B2/A1/A2 etc. — when comparing) before the `seen` set check in
   `product_line._dedupe_patents_by_pub_number`. This is a narrowly-scoped, well-evidenced,
   low-risk fix (the two duplicate pairs found this round are the exact reproduction case) with a
   test that should assert `'US7378626'` and `'US7378626B2'` collapse to one row.
2. **Root-cause Q6's new incoherent-opening shape.** The two round-12 checks (dotted-acronym
   terminator, short orphan-fragment sweep) both work as designed but are scoped too narrowly to
   catch a *longer* leftover unit that lost its subject to guard removal. A general check — does
   the surviving first unit of a section, after all removal guards have run, still parse as a
   complete independent clause (not just "not too short") — is the natural next iteration on the
   same investigation pattern that found the last two fixes.
3. **Write up the two undocumented round-12 packages.** Both the D8 cluster-label fix and the D5
   chat fixes are real, tested, and verified working live — but neither has a `round_12_fixes.md`
   section, which breaks the loop's own audit trail (this judge only found them by diffing file
   mtimes against the judge report, not by reading the fixes doc as instructed). A retroactive
   write-up costs little and restores the process the loop depends on for future rounds' triage.

Secondary, lower-effort items worth bundling into whichever round next touches these files: populate
evidence for weekly's 2 remaining empty-evidence indicator rows; give the Anduril survey's "lattice
/ mesh" cluster the same CPC-in-parens suffix format as its siblings; and archive or otherwise flag
tender id 34 as clearly out of the corpus's scope so it doesn't need re-discovering by a future
round's junk scan.
