# Round 9 judge (J9) — independent read-only pass

**Method:** read-only agent (no pipeline runs, no fixes, no OMC skills/agents/tools, no DB writes,
no git commands, no process kills, no stack start/stop). `DATABASE_URL` loaded once per shell via
`set -a; . runtime/eoa.env; set +a`, never printed; every query confirmed port 5432, db
`eoanalyst`, via short-lived `.venv/Scripts/python.exe` + psycopg scripts (`PYTHONUTF8=1`). The
live API process (uvicorn, confirmed via `Get-CimInstance Win32_Process`) started at **14:11:20**
local time — about 15 seconds *after* the R9-chat commit (`900ae1e`, 14:11:05) — so the 8-question
chat test ran on live round-9 code. D10 skipped per brief.

**Report freshness varied sharply and matters a lot for this round's verification:**
`daily_2026-09-07.md` (built 14:10:49) and `weekly_2026-09-07.md` (built 14:19:33) were both
rebuilt *after* all three round-9 commits (R9-reports `5f66f79` 13:49:30, R9-investigations
`3733496` 14:08:50, R9-chat `900ae1e` 14:11:05) and are genuinely fresh. But
`monthly_2026-09-30.md` (10:52), all 6 `pl_*_2026-09-07.md` (11:39–12:58), both
`patent_survey_*_2026-09-07.md` (11:12–11:19) and all 8 `bd_*_2026-09-07.md` (00:xx–04:xx, `bd_kr`
10:41) were built **before** `5f66f79` (13:49:30) — so none of them reflect this round's D6/D7/D8
report-layer fixes, even though direct source-code inspection confirms all three are correctly
implemented. This is the round's single biggest cross-cutting finding (see worst #1).

A second, concurrent `scripts/qa_score.py --round 9 --e2e --no-links` process started at 14:20:00,
sharing the API during the back half of the chat test, per the brief's disclosed contention window.
No `gate_busy`, no hangs, and no access-log gaps were observed this round — a much healthier
environment than round 8's.

## Scores (D1–D9)

| Domain | Score | n | Round 8 | Δ |
|---|---|---|---|---|
| D1 — classification/sorting | 91 | 514 | 92 | −1 |
| D2 — summary/so-what | 86 | 514 | 86 | 0 |
| D3 — events/entities | 91 | 125 | 90 | +1 |
| D4 — deep investigations | 74 | 9 | 63 | +11 |
| D5 — chat ("ask the analyst") | 78 | 8 | 48 | +30 |
| D6 — daily/weekly/monthly reports | 90 | 27 | 85 | +5 |
| D7 — BD + product-line reports | 78 | 14 | 90 | −12 |
| D8 — patent surveys | 85 | 16 | 93 | −8 |
| D9 — tenders/forecasts/conferences | 94 | 13 | 89 | +5 |

D7 and D8's drops are **artifact-freshness artifacts, not content regressions** — see below. D5's
jump is the round's real headline.

## Headline story: three real fixes verified live in chat and investigations, one architectural gap exposed by a rerun, and a report-freshness gap that hides real D6/D7/D8 work from view

The round's chat-retrieval package (**R9-chat**) is a genuine, dramatic, live-verified win. All 8
golden questions completed with real citations, no hangs, no `gate_busy`, first-token in 1.7–6.0s
and full answers in 50–91s. Round 8's two worst D5 findings are both closed: **Q8 (SPECTRO ISR)**
now retrieves item 321 and gives a substantive, correctly-cited answer instead of "I don't
recognize this term"; **Q1 (XM30)** no longer fabricates "53 מיליארד דולר" against a cited
$1.53bn source, and `ask.entailment_check_removed claims=4 removed=3` fired live for that exact
call — direct proof the round-9 fix (pinning the entailment probe to the local `ollama` leg
instead of an unbounded cloud chain) works. **Q3 (LORA/Greece)** also stands out: both real
disambiguating items (the German-navy LORA firing, the Greek $4b deal) are now retrieved and
explicitly, correctly kept separate — restoring round 2's own anti-conflation design intent that
round 8 had silently broken. Set against this: a new, systematic **truncated-opening-sentence**
defect appeared in 5 of the 8 sampled answers — a side effect of the grounding/entailment guards
now removing more content than before, without re-stitching the remaining text into a grammatical
opening (see below).

**R9-investigations** delivered two of its three targeted fixes fully live: item 96's `dedup_of`
chain now correctly folds into item 10's rerun cluster in the *freshly-rebuilt* weekly report (one
bullet, not two contradictory ones), and job 161's rerun of the Norkin question correctly fires the
widened nominalized-decision-phrase guard, with the *fixed* rerun — not the stale original —
rendering live. But the third target, job 146's undisclosed Cloudflare-interstitial citation,
reveals something more interesting than "needs a rerun": job 160 (the clean rerun) *does* correctly
discard the same bad URL this time, proving the low-quality-page filter works. Yet the live report
still shows job 146's stale, badly-sourced answer, because the report's rerun-reconciliation logic
picks the "best" answer by outcome-tier (`found` > `partial`) rather than citation cleanliness. A
rerun that honestly produces a *lower-confidence* answer than a badly-sourced original will always
lose that tie-break — this is an architectural gap, not a timing gap, and it means round 8's worst
#2 is not actually closed for the reader even though the underlying code fix is real and verified.

**R9-reports** shipped three real fixes (indicator-evidence Hebrew-term matching, product-line
event-kind labels, patent-office reliability fallback) that all check out on direct source-code
inspection and, in weekly's case, on live report output too (indicator evidence jumped from 2/16 to
17/19 rows populated — a bigger live win than the fixes doc's own claimed 13/15). But the six
product-line reports and both patent surveys on disk were all built *before* the `5f66f79` commit
landed, so the event-kind-label and patent-reliability fixes cannot be confirmed against the actual
artifacts a reader would see today — `pl_targeting_pods_2026-09-07.md` still shows a raw,
untranslated `"test"` label in one table, right next to a correctly-Hebrew `"ניסוי"` in another
table of the *same* report, purely because of when each file happened to get rebuilt. This is a
process gap the loop should close before round 10: rebuild every report kind, not just daily/
weekly, once a round's fixes land.

## Domain detail

### D1 (91, −1) — classification/sorting
- 0 real score↔level mismatches DB-wide (one apparent mismatch, item 1863, resolved as a correctly
  score-exempt `domain='out_of_scope'` item).
- Junk-entity-name scan clean (0 junk survives; all short names — IAI, RTX, HAL, IDF, etc. — are
  legitimate).
- Corroboration coverage 43/43 (100%) for recent in-scope items, comfortably above the ≥90% bar.
- Entity orphan rate under my own `graph_edges`-linkage measure is 25/334 (7.5%), moderately above
  round 8's reported 16/334 (4.8%) — flagged as a methodology-uncertain observation, not a proven
  regression, since I could not reproduce the original scoring script's exact definition.
- product_lines tagging on items unchanged at 4/6 lines (not a round-9 target).

### D2 (86, 0) — summary/so-what
- 0 banned filler phrases DB-wide, same clean state as rounds 6–8.
- Not independently re-sampled beyond the DB-wide scan this round (not a round-9 target); the 8
  chat answers' own analytical sections show genuine reasoning, not template paraphrase, but that's
  D5 content, not `so_what_he`.

### D3 (91, +1) — events/entities
- Atlantic City still exactly 1 row in the freshly-rebuilt weekly report — durable fix.
- 0 events belong to an out-of-scope/archive item; event-kind distribution has no junk values
  ('test' is a legitimate enum member, not junk data).

### D4 (74, +11) — deep investigations
- **Fixed, verified live:** item 96/10 dedup-of fold (round 8 worst #4) and job 147/161's hedge
  downgrade (round 8 worst #3).
- **Still live, deeper root cause exposed:** job 146's interstitial citation (round 8 worst #2) —
  a clean rerun exists but loses the report's outcome-tier-first "best answer" selection.
- No SOURCES_JSON leaks, no malformed sections, no exact-duplicate questions across ~17 distinct
  investigation entries in the live weekly report.

### D5 (78, +30) — chat
- 8/8 completed, fast, stable, richly cited (up from round 8's 1/8 with any citations at all).
- Q8 (SPECTRO ISR) and Q3 (LORA/Greece) are full, verified fixes; Q1 (XM30) shows the entailment
  guard actually removing a fabricated claim live.
- New: a truncated/dangling-opening-sentence artifact in 5/8 answers — a side effect of the guards'
  new effectiveness, not a factual-accuracy regression.
- Partial: Q5 (Skyranger) — item 183 confirmed used, item 1353's cannon spec still not evidenced.
- Entailment-guard log coverage confirmed for only 1/8 calls, though the one firing is clean
  evidence the fix works.

### D6 (90, +5) — daily/weekly/monthly reports
- Weekly indicator-evidence column: 17/19 populated (~89%), a major live-verified win.
- Daily indicator-evidence column: 1/8, essentially unchanged from round 8's 0/8 — the fix's own
  live verification only had a proxy day available.
- Heading budgets, malformed-marker checks, single-Israel-heading all clean and unchanged.

### D7 (78, −12) — BD + product-line reports
- All 6 product-line reports and all 8 BD reports predate the round-9 code by 1–4 hours — the
  round-9-specific event-kind-label check cannot be positively confirmed against the actual
  artifacts, even though the code (`product_line.py`'s `_event_kind_label`/
  `_EVENT_KIND_LABELS_HE_FALLBACK`) is correct on inspection.
- Otherwise structurally sound and unchanged from round 7/8 (BLUF, buyer-pipeline, falsifiable
  assumptions, honest empty paths on thin lines).

### D8 (85, −8) — patent surveys
- Cluster labels clean and durable (0 junk clusters in either fresh survey).
- Appendix reliability column still blank for all 16 rows on disk — code fix confirmed correct by
  reading `agent/eoa/patents/survey.py`, but both survey files predate the fix commit by ~2.5h.

### D9 (94, +5) — tenders/forecasts/conferences
- Tender 38 resolved to `archived` with a logged, substantively correct reason — closes round 8
  worst #10, verified via a fresh DB read.
- Tender 42 remains correctly, honestly untagged (no invented product-line signal).
- Minor: round_9_fixes.md's own Q7 retrieval-verification note overstates item 5721's relevance (it
  is a Finnish RFI, not American) — the live chat correctly declines to use it, so this is a
  documentation-precision nit, not a code defect.

## Worst 10 (most severe first)

1. **D7/D8 (systemic):** 6 product-line reports, both patent surveys, monthly, and all 8 BD reports
   on disk predate the round-9 code commits by 1–4 hours — none of those artifacts can evidence
   this round's D6/D7/D8 report-layer fixes, even though the code itself checks out.
2. **D4:** job 146 (AARGM-ER) still renders in the live weekly report citing an undisclosed
   Cloudflare interstitial at confidence 0.85 — a clean rerun exists but loses the reconciliation
   tie-break to the stale, badly-sourced original.
3. **D5:** a new, systematic truncated/dangling-opening-sentence defect in 5/8 sampled chat
   answers, a side effect of the guards now removing more content than before.
4. **D5:** entailment-guard log coverage confirmed for only 1/8 questions; the other 7 answers'
   guard activity cannot be verified from the log either way.
5. **D5:** Q5 (Skyranger) — item 1353's cannon/rate-of-fire spec still not evidenced in the final
   answer, despite round-9's own offline retrieval table predicting it would surface.
6. **D1:** entity orphan rate 25/334 (7.5%) under my own measure, moderately above round 8's
   16/334 (4.8%) — unconfirmed, methodology-uncertain, not a proven regression.
7. **D7:** `pl_targeting_pods_2026-09-07.md` still shows raw untranslated `"test"` in one table
   beside correctly-Hebrew `"ניסוי"` in another — a direct instance of finding #1.
8. **D9 (documentation, not code):** round_9_fixes.md's own Q7 verification note overstates item
   5721's relevance (Finnish, not American RFI) — live chat correctly declines it anyway.
9. **D6:** daily's indicator-evidence column remains weak (1/8) even as weekly's jumped to 17/19
   from the same fix.
10. **D8 (paired with #1):** patent-survey appendix reliability blank for all 16 rows on disk; code
    fix confirmed correct but the files predate its commit by ~2.5h.

## Comparison with round 7 (via round 8)

Round 9 is the strongest round yet on the metric that matters most to end users: **D5 chat quality**
went from round 8's near-total retrieval blindness (1/8 testable, 0/8 real citations found once
retrieval was actually sampled at n=8 by J8b) to 8/8 fast, richly-cited, largely-honest answers,
closing both of round 8's worst D5 findings outright. D4 also closed 2 of its 3 targeted defects
live. But D7 and D8's *reported* scores drop this round not because anything got worse, but because
the artifacts I was asked to judge — product-line reports, patent surveys, monthly, BD reports —
simply hadn't been rebuilt yet when I read them, so this round's real report-layer work is
invisible in the deliverables. Net: real, durable progress on the hardest problems (chat, deep
investigations), offset by a process gap (report freshness) that a round-10 rebuild should close
for free.

## Recommended round-10 packages, ordered by expected gain

1. **Rebuild every report kind** (`pl_*`, `patent_survey_*`, `bd_*`, `monthly`) after this round's
   commits, then re-verify D7/D8's round-9-specific checks against the fresh artifacts — this is
   very likely a free, immediate win recovering most or all of D7/D8's reported drop, since the
   underlying code for all three targeted fixes checks out on direct inspection.
2. **Make the deep-search rerun-reconciliation "best answer" selection weight citation cleanliness,
   not just outcome-tier** — job 146/160 shows that rerunning a flawed investigation is not
   sufficient by itself when the honest rerun comes back lower-confidence than the badly-sourced
   original; without this, every future "clean rerun of a bad citation" will keep losing the same
   tie-break.
3. **Investigate the new truncated-opening-sentence defect** in the chat's grounding/entailment
   pipeline — when a guard removes a leading clause or sentence, either remove the whole sentence
   cleanly or restitch a grammatical opening; this affected 5 of 8 sampled answers this round and is
   a real readability regression riding on top of a real accuracy improvement.
