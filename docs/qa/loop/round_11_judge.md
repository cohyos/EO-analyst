# Round 11 Judge Report (J11)

**Method:** independent read-only agent. No pipeline runs, no report rebuilds, no fixes, no OMC
skills/agents/tools, no DB writes, no git commands of any kind, no process kills, no stack
start/stop. `DATABASE_URL` was loaded exactly once per shell via `set -a; . runtime/eoa.env; set
+a` and passed straight into short-lived `.venv/Scripts/python.exe` + psycopg scripts
(`PYTHONUTF8=1`) — the value itself was never `cat`/`grep`/`type`/echoed. Every query confirmed
`inet_server_port()`=5432, `current_database()`='eoanalyst', `alembic_version`='0028'. Two
research subagents (plain `general-purpose`, no OMC) did parallel read-only analysis of the QA-loop
docs history and of the fresh report files; every claim either agent surfaced was independently
re-checked by this judge directly against the live DB, live API, or live file content before being
used below — nothing here rests on a subagent's or a fixes-doc's own prose alone.

All 8 golden chat questions (`docs/qa/loop/golden_questions.json`) were asked live, one at a time,
against `POST http://127.0.0.1:8765/api/ask`, each read to its `answer_final` SSE event. 8/8
completed on the first attempt — 0 `gate_busy`, 0 timeouts, 51–84s per question, first byte in
2.5–7.8s. Total chat wall-clock: ~9 minutes, well inside the 45-minute budget.

## Scores (weighted per docs/QA_CONTINUOUS_LOOP.md; D10 skipped, deterministic e2e)

| Domain | R10 | **R11** | Δ | n | One-line why |
|---|---|---|---|---|---|
| D1 | 92 | **92** | 0 | 40 | 0 score↔level mismatches, 16/334 orphans, 63/63 corroboration — all stable/durable |
| D2 | 86 | **86** | 0 | 10 | not a round-11 target; spot-check only, held steady |
| D3 | 93 | **92** | −1 | 20 | dedup/event-kind clean; new minor Atlantic-City-as-entity noise in monthly |
| D4 | 93 | **94** | +1 | 15 | jobs 156/160/161 + provenance API all re-confirmed live, cleanly |
| D5 | 85 | **83** | −2 | 8 | 6/8 clean+honest, but Q6's 4-round-old "סי." fragment resurfaces as this round's headline defect |
| D6 | 92 | **87** | −5 | 3 | monthly Israel table landed perfectly; but monthly has *no* indicator section and weekly's indicator table now exceeds its own cap |
| D7 | 90 | **88** | −2 | 14 | BD/product-line bar still met broadly; pl_mws_eo relevance/dup issues newly found |
| D8 | 93 | **74** | −19 | 16 | appendix reliability still clean, but cluster labels are raw CPC codes in 8/9 clusters, not technology terms |
| D9 | 90 | **94** | +4 | 12 | Graph Explorer 422 regression (R10 worst #1) confirmed fixed live; DB state otherwise stable |

**Weighted (D1–D9, brief's own weights, out of 95): ≈88.0** (round 10: ≈90.3). The net regression
is driven almost entirely by two concretely-quoted, independently-reproduced findings — D8's
cluster-label gap and D6's monthly-indicator-section gap/weekly-indicator-cap overrun — not by any
broad quality collapse. D4 and D9 both improved on the strength of clean live re-confirmation of
round-10's own fixes.

## What round 11 actually shipped

Only **two** fix packages ran this round (`docs/qa/loop/round_11_fixes.md` has exactly two
`### R11-*` sections — confirmed by grepping the file, not just trusting its table of contents):

- **R11-chat**: fixed Q5/Skyranger's item-1353-truncation root cause (`_relevant_excerpt`,
  proven correct offline against the real DB row), fixed the Q6-class "שאר המקורות" dangling
  cross-reference opening, and tuned the entailment-probe size/timeout. **None of this is live** —
  the running API process started at `14:44:40` (one `api.startup` line in today's log, no restart
  since) and the changed files' mtimes (`ask_grounding.py` 16:19, `services.py` 16:23) are strictly
  later. Every live chat answer this judge captured is running pre-round-11 code, exactly as
  R11-chat's own status doc discloses.
- **R11-reports**: fixed monthly's missing Israel-industry table (confirmed live below) and
  investigated Q7's leading-space bug without being able to reproduce it in report code, correctly
  concluding it belongs to the ask/grounding path instead.

No package this round touched D8 (patent clusters) or D6's indicator-table caps — neither gap
found below was a known, tracked target; both are freshly surfaced by this round's direct reading.

## Verified evidence, by domain

### D4 — deep investigations (94, +1)

This is the strongest domain this round, and every claim was checked against a live artifact, not
the fixes doc's own account:

- **Job 156** (rerun of 145, item 10, the $465M AeroVironment laser contract): `jobs` table shows
  `state=done, outcome=found, confidence=1.0`. `weekly_2026-09-07.md`'s "חקירות עומק" section
  renders it with three real, distinct citations (army.mil / avinc.com / militarytimes.com) and an
  honest Hebrew rerun note: *"השאלה נחקרה 4 פעמים השבוע; מוצגת הריצה עם התוצאה הטובה ביותר (ריצות
  נוספות: לא נמצא, לא נמצא, לא נמצא)."*
- **Job 160** (rerun of 146, AARGM-ER/item 44): `partial/0.1`, and the same report explicitly
  discloses why a "better-looking" earlier run was excluded: *"ריצה אחרת עם תוצאה 'טובה יותר'
  לכאורה הושמטה כי מקורה היחיד (או מקורותיה) זוהו כדף חסימה/אימות אנושי ולא כתוכן אמיתי."* This is
  the D4 bar ("no investigation cites an interstitial/challenge page") working exactly as intended,
  with the exclusion disclosed rather than silent.
- **Job 161** (rerun of 147, Norkin/item 81): `found/0.95`, rendered with hedge-downgrade language
  matching the source's own not-yet-decided state: *"לפי הדיווח, ההכרעה בנושא זה טרם אושרה סופית."*
- **Provenance API**: `GET /api/investigations/156` returns a full `provenance` object — correct
  `trigger_item` (id 10), a 3-hop `lineage` (job 137 failed → 145 not_found → 156 found), and a
  `reports[]` list linking to the actual monthly/weekly reports that cite it. Live-tested, not
  assumed.

### D5 — chat (83, −2)

All 8 live answers were read in full. Six were clean: Q2 (Iron Beam) and Q4 (DROIC) correctly
refuse rather than fabricate; Q3 (LORA/Greece) explicitly keeps the German-navy test and the Greek
deal separate; Q1 (XM30) and Q8 (SPECTRO ISR) are accurate and well-cited (SPECTRO verified
word-for-word: $270M, 6-year, MWIR/visible/SWIR, AMPS NG, CEO name); Q7 (RFI) correctly declines
the US framing since only a Finnish RFI exists in the DB.

The headline finding is **Q6 (AUSA 2026)**: the live answer contains a bare, syntactically
detached line — `סי.` — sitting alone between the main paragraph and the gaps section. This is
not a new bug: round 7's D5 findings described the *exact same shape* on the *exact same question*
("a stray corrupted 'סי.' fragment in place of what should be a populated or omitted section
heading"). No fix package across rounds 8–11 has targeted or root-caused it; R11-chat's
cross-reference-opening fix addresses a different defect ("שאר המקורות...") and doesn't touch this
one. Four QA rounds have now passed without this being diagnosed.

Q5 (Skyranger) still doesn't surface item 1353's spec live — expected, not a regression, since the
fix exists in code but the API hasn't restarted (see above). Q7's leading-space cosmetic bug
persists, confirmed unfixed by R11-reports' own investigation this round. Entailment coverage
measured 3/8 live (identical to round 10), not the 8/8 R11-chat's offline replay achieved under
zero RAM contention.

### D6 — reports (87, −5)

The round's flagship fix landed cleanly: `monthly_2026-09-30.md`'s unified "תעשייה ישראלית" table
was read directly and confirmed — header `כותרת | סוג | ישויות | מה זה אומר | מקור` (has the
required `סוג` column), **16 data rows**, plus a dedup note ("7 שורות כבר הופיעו בטבלאות קודמות").
Daily remains correctly tables-only by design (not penalized), H2 counts are within budget across
all three (7/15/12), and single-row Atlantic City discipline holds in both weekly and monthly.

Two gaps pull the score down, both freshly discovered rather than previously tracked: monthly has
**no indicator-tracking section at all** (`grep "אינדיקטור"` → 0 matches, while daily and weekly
both have one), and weekly's indicator table has grown to **10 rows**, over the brief's ≤8-row cap
(2 of the 10 rows show empty `—` evidence).

### D8 — patent surveys (74, −19)

The appendix-reliability fix holds (`מקור ראשוני · רשומת פטנט רשמית · 1.00` on all 16 rows checked
across both surveys). But the round-11 brief's specific ask — "cluster labels are technology
terms" — does **not** hold on direct inspection: `grep "^## אשכול" patent_survey_..._DROIC...md`
returns headers like `אשכול טכנולוגי: אשכול טכנולוגי H04N5`, `H03M1`, `G01S7`, `H04N25` — raw CPC
codes, not named themes. The Anduril survey shows the same pattern (`H04W4`, `F41H3`, `G05D1`,
`F41H11`), with exactly one exception (`lattice / mesh`) out of 9 clusters checked across both
files. The prose inside each cluster remains substantive and accurate, which is why this isn't
scored as a catastrophic failure — but it is exactly the claim under test, and it fails.

### D9 — tenders/forecasts/conferences/UI (94, +4)

Round 10's single worst-list item — `GET /api/graph/overview?limit=300` 422ing on every default
page load — is confirmed fixed: a live curl returns `200` with a 45KB payload. The other three new
graph endpoints (`neighborhood/82`, `search?q=Elbit`, `entities/82/detail`) also all return 200.
Tender/forecast/conference state is stable and matches round 9's description exactly (tender 38
archived, tender 42 open/Finland/TED, 28 forward-dated conferences, 0 in the past).

## Worst 10 (most severe first)

1. **D5**: Q6's "סי." stray-fragment bug reproduces byte-for-byte from round 7, unaddressed for 4 rounds.
2. **D8**: patent-survey cluster headers are raw CPC codes in 8/9 clusters checked, not technology terms.
3. **D6**: monthly has no indicator-tracking section at all.
4. **D6**: weekly's indicator table now has 10 rows, over its own ≤8 cap (2 rows with no evidence).
5. **D5** (expected, not new): Q5/Skyranger's fix is real but not live — API hasn't restarted since before the fix files were saved.
6. **D5** (unchanged): Q7's leading-space bug persists; confirmed out of report-code scope this round.
7. **D1/D9**: product_lines tag coverage is thin (3/63 in-scope items, 2/9 tenders, 5/86 patents) — likely by design, flagged because the brief asked for the number.
8. **D7**: pl_mws_eo is built on an out_of_scope-tagged sole item, plus 2 duplicate patent rows.
9. **D3/D6**: "Operation Atlantic City" leaks into monthly's unrelated landscape/glossary tables as a fake entity.
10. **D9** (informational): blocked-item count grew 19→36, with 19 not matching the interstitial pattern the round-10 cleanup targeted — composition worth re-verifying.

## Comparison with round 7 (context, not a strict re-judge)

Round 7's headline finding was D4's report-generation layer failing to render better rerun results
over stale years-old text — that gap is now closed and re-confirmed three separate ways this round
(job 156/160/161 rendering, honest rerun notes, working provenance API). Round 7's D5 also flagged
a leaked `===SOURCES_JSON===` marker and a "seven vs eight" numeric fabrication on item 257 — the
marker leak is durably gone (0/8 this round too), and the eight-prototypes phrasing recurs but now
reads as plausible arithmetic (1 initial + 7 additional) rather than a flat substitution. What
round 7 did *not* catch cleanly (the "סי." fragment was logged but never became a tracked fix
target) is precisely what resurfaces as this round's top finding — a reminder that a defect logged
once in a judge's notes needs to become a named worst-list item to survive into later rounds' fix
planning.

## Recommended round-12 packages, ordered by expected gain

1. **Restart the API process** before the next judge pass. This is a zero-code, near-zero-risk
   action that would let a live judge finally confirm whether R11-chat's Skyranger fix actually
   changes the model's output (the offline evidence is strong; only the live confirmation is
   missing), and would also pick up the entailment probe/timeout tuning. Highest expected gain for
   lowest effort of anything on this list.
2. **Root-cause the Q6 "סי." fragment.** It has now been visible in the judge notes for 4 rounds
   without being named as a tracked target. Given R11-chat just added a new leading-unit check to
   `enforce_answer_coherence`, the same investigation pattern (offline replay against a
   reconstructed answer skeleton) that found the dangling-cross-reference bug should be pointed at
   this fragment next — it looks like a truncated section heading, not a cross-reference token, so
   likely needs a distinct check.
3. **D8 cluster labeling.** Either wire cluster header generation to translate the dominant CPC
   code into a technology-term label (the body prose already does this translation successfully
   per-cluster — the summarization logic clearly exists, it's just not reaching the header), or
   adjust the QA rubric if raw CPC codes are actually an acceptable interim state — but the current
   mismatch between the brief's expectation and the live content should be resolved one way or the
   other rather than left ambiguous for another round.

Secondary, lower-effort items worth bundling into whichever package touches reports next: give
monthly an indicator-tracking section (or explicitly document why it doesn't need one), and either
re-cap weekly's indicator table at 8 rows or raise the documented cap to match the current design.
