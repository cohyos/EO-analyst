# Round 7 Judge Report (J7, independent, read-only)

## Method

Read-only throughout: no pipeline runs, no report rebuilds, no fixes, no file edits inside the repo
except these two output files, no DB writes (SELECT only), no git commands of any kind (not even
`git status`), no process starts/stops/kills, no OMC skills/agents/tools. `DATABASE_URL` was loaded
exactly once per shell via `set -a; . runtime/eoa.env; set +a` and passed straight into short-lived
`.venv/Scripts/python.exe` + `psycopg` scripts (`PYTHONUTF8=1`, `os.environ["DATABASE_URL"]`) -- the
value itself was never `cat`/`grep`/`type`/`echo`ed. Every query confirmed `inet_server_port()`=5432
and `current_database()`='eoanalyst' (never 5433), `alembic_version`='0027'.

Golden sample: all 40 items in `docs/qa/loop/golden_items.json` plus 3 additional so_what spot
checks, the 6 golden investigation job ids and all of this round's newer rerun attempts (jobs
125-144, focusing on 130-140), all 8 golden chat questions (two full sequential passes), the
freshest daily/weekly/monthly reports, all 8 BD territory files at their newest per-territory
timestamp, and both named patent surveys. Every claim in `docs/qa/loop/round_7_fixes.md` was
independently re-verified against a live `SELECT` and/or the actual bytes of the rebuilt report
files -- never against the fixes doc's own prose. This is what surfaced this round's single most
severe finding (D4): a large, detailed, specific verification section in the fixes doc simply does
not match the live jobs table.

**Chat testing note:** two full passes over the 8 golden questions were run against
`POST http://127.0.0.1:8765/api/ask`. The response format turned out to be JSON-per-line with a
literal `data: ` SSE prefix (not named `event:`/`data:` fields as round 6's own method note
implied) -- a first-pass harness bug stripped the prefix incorrectly and silently misclassified
every `gate_busy` response as a completed, contentless answer. This was caught, the parser fixed,
and a second full pass run. Across both passes (14 completed attempts, ~28 minutes of wall clock,
well inside the 45-minute budget), **every single attempt returned only a `gate_busy` event** --
the local chat model was persistently occupied for the entire testing window, consistent with the
brief's own disclosed concurrent Playwright e2e run (and at least one concurrent job, a queued
`product_line_report`, observed live). Zero `answer_final` events were observed on any question in
either pass. See D5.

## Per-domain findings

### D1 -- סיווג ומיון (score 91, n=44, was 80)

All 40 golden items remain score↔level consistent against `config/config.yaml`'s thresholds
(red:8/orange:6/yellow:4) -- durable, zero mismatches, unchanged from round 6.

Two real, verified fixes close round 6's #4 and #5 worst-list items. First, item 22's
`entities_mentioned` no longer contains the junk-shaped names `'F-16s'`/`'Western Partners'` -- a
DB-wide scan confirms zero items carry either string now, and a broader scan for junk-shaped
entity names (short tokens, "partners"/"unknown" substrings) in the standalone `entities` table
returns only legitimate short designators (AV, F-47, J-36). Second, and much larger: entity orphans
(no item mention *and* no graph_edges row) dropped from 245/365 (67%) to 16/334 (4.8%) -- a 94%
reduction, and the entity count itself shrank by 31, suggesting the round-6 recommendation ("retro-
actively re-run the junk-entity-name filter against already-persisted arrays... and add a
zero-mention-entity query") was substantively acted on well beyond the two entities the brief
named.

The new `corroboration_populated_for_recent_in_scope` check (added this round) was replicated
directly against the live DB: 63/63 (100%) of recent, in-scope items carry a non-`unknown`
`item_corroboration.status`, comfortably clearing the brief's 90% bar. The underlying data is real:
5 corroborated pairs, at least one independently verified (item 10's AeroVironment $465M laser
story, corroborated against items 52/96's duplicate coverage of the same event).

### D2 -- סיכום ו-so-what (score 87, n=43, was 88)

The `SO_WHAT_TEMPLATE_PHRASES_HE` fix remains durable: re-scanning against the *exact* 11-phrase
list read directly from `agent/eoa/report/qa_citations.py` returns zero matching items DB-wide,
identical to round 6's clean result. (A first pass using an approximated phrase list incorrectly
flagged 5 items -- re-running against the real source-code list cleared all 5; noted here as a
self-correction against my own error, not a live defect.) Three additional golden items spot-checked
for so_what quality (9, 33, 58) all show substantive, specific analytical reasoning tied to named
entities/programs, no template or filler structure. Score held essentially flat rather than raised,
since this round's D2 sampling was less exhaustive than round 6's own (time was reallocated to the
D4 investigation, below) -- no new defect found, but also not as thoroughly re-verified.

### D3 -- אירועים וישויות (score 87, n=50, was 90)

All three of round 6's major fixes remain durable: zero events on out-of-scope/archive items DB-
wide, zero events with the invalid `kind='ניסוי'`, zero exact-duplicate `(item_id, kind, title)`
event groups, and all 9 distinct event kinds in live use are valid enum values. One item is
unchanged from round 6, not addressed this round: the weekly report's business-events table still
lists "Operation Atlantic City" as three separate rows (שותפות/פעילות מבצעית/פריסה) sharing a single
citation -- the same reader-visible triplication round 6 flagged as a minor, unconfirmed finding.

### D4 -- חקירות עומק (score 30, n=15, was 78)

**This round's headline finding.** `docs/qa/loop/round_7_fixes.md` presents a detailed table
claiming jobs 137/138/139/140 (re-runs of golden questions 47/48/70/86-91) succeeded with rich,
well-sourced answers at confidence 0.9/0.78/0.7/0.85. **None of this matches the live jobs table.**
All four jobs are `state='failed'`, `result=NULL`, with the identical live error `"cannot import
name 'FallbackSynthesisOut' from 'eoa.llm.schemas.analysis'"` -- even though that class is in fact
defined at `analysis.py:328`, and both touched files' mtimes (04:38/05:02) predate the 05:08-07:23
failure window, ruling out a since-fixed-then-broken-again race. Whatever the exact import/circular-
dependency mechanism, the practical fact is: **the jobs the fixes doc names as new successes are,
on the live system, failures with no retrievable result.**

This is not pure fabrication, and the nuance matters: `investigation_log` carries exactly one row
per job (137-140), `engine='cloud_batch'`, with an outcome matching the doc's claims, and the logged
`notes` text -- read in full for all four -- is genuinely accurate and well-sourced (job 137's note
correctly states the AeroVironment $464.8M E-HEL OTA contract, matching the doc almost verbatim).
So the underlying content-generation step (a `claude` CLI batch call) really ran and produced good
answers. But a downstream persistence/finalize step crashes on the same ImportError before writing
anything to `jobs.result` -- so **no consumer of the system can ever see this content**. Confirmed
directly: the freshest weekly (`weekly_2026-09-07.md`) and monthly (`monthly_2026-09-30.md`)
reports' own "חקירות עומק" sections still render the identical stale not_found text for these exact
questions ("לא נמצאו נתונים המאשרים...", "לא נמצא מידע מספק במסגרת התקציב") that rounds 3-6 showed --
the claimed fix is invisible in every reader-facing artifact that exists today.

Compounding this: the *first* rerun attempt this round (jobs 131/133/135/136, via `investigate()`
directly -- the CLI's own documented `eo investigate` path) also failed outright, all four with the
identical ImportError, independently confirming the fixes doc's own disclosed "tool-calling gap"
(the cloud-chain dispatch path never wires `tool_calls`) makes the standard investigation path
non-functional today via *either* failure mode. One narrow, genuinely working fix: job 46's rerun
(new job id 130) correctly and instantly returns `insufficient_context`/confidence 0.0 with an
honest degenerate-question message, live and verified in the jobs table.

### D5 -- צ'אט "שאל את האנליסט" (score 40, n=8, was 45)

Could not obtain a single real answer to any of the 8 golden questions this round. Two full
sequential passes (14 completed attempts total, ~28 minutes of the 45-minute budget) each returned
only a `gate_busy` event on every question -- the local chat model was continuously occupied for the
entire testing window. This tracks the brief's own disclosed condition (a concurrent Playwright e2e
run hitting the same API) plus at least one observed concurrent job. No `jobs` rows were in
`state='running'` at the moment of the last DB check, so the contention most plausibly sits in the
e2e suite rather than the pipeline queue.

Consequently, none of round 6's specific D5 findings (the XM30/MWIR fabrication on item 257, the
missing/duplicated `answer_final` protocol bug, the Skyranger weak-citation pattern) could be either
confirmed-fixed or newly reproduced. The score (40, down slightly from round 6's 45) reflects three
things in balance: genuine inability to verify any claimed content improvement; the sustained, total
feature unavailability during the test window is itself a real degradation worth flagging (though
not scored as a crash -- the `gate_busy` message is honest, well-formed, and this is a known,
disclosed contention source, not a silent failure); and, absent any content, this is not scored as
low as a confirmed live fabrication would be. A brief third attempt (Q1 only, longer retry budget)
made after the two full passes adds one more consistent data point: after 3 further consecutive
`gate_busy` responses, the next attempt hit a raw connection reset (`WinError 10054`) rather than
another `gate_busy` -- further evidence of sustained load, not a contradiction of the above.

### D6 -- דוח יומי/שבועי/חודשי (score 74, n=100, was 78)

Structural compliance is strong: heading budgets are met under this round's own stated thresholds
(daily 13≤15, weekly 15≤16, monthly 11≤16 H2s), no malformed `[n]]` citation markers anywhere, no
banned analyst-filler phrases in any exec summary, and a single "תעשייה ישראלית" heading with a
populated "סוג" column present in both daily and weekly.

The real, new gap this round is cross-source corroboration rendering. The underlying DB layer is
100% populated (see D1), but the marker actually reaching the page varies wildly by report type:
weekly renders it on 40/59 appendix rows (~68%); daily renders it on only 1/21 rows (~5%) -- and
that one marked row is not even the most relevant one, since item 10's own daily appendix row (the
AeroVironment $465M story, DB-confirmed corroborated with 2 sources) carries **no** marker, while
the identical story's row in the same day's weekly report is correctly marked "(מאומת ב-2 מקורות)".
Monthly renders **zero** markers (0/59) -- reading `agent/eoa/report/monthly.py` confirms it never
imports or calls `_append_item_corroboration_markers`/`_append_event_corroboration_markers` at all,
unlike `weekly.py`, which does. A second, smaller new finding: weekly's own indicator-watchlist
"ראיה" (evidence) column is blank ("—") on all 13 rows -- a structurally complete table carrying no
actual evidence content. Round 6's #7 worst-list finding (daily's indicator table repeating near-
verbatim sentences) is partially, not cleanly, improved -- this round's 11-row table no longer
repeats identical wording, but remains heavily clustered on just 2 underlying stories (Volkswagen/
Rafael, Estonia/David's Sling) across 10 of its 11 rows. Round 6's "Operation Atlantic City"
triplication (D3, above) is unchanged.

### D7 -- דוח פיתוח עסקי (score 86, n=8, was 72)

Real, verified fix: `bd_il` was rebuilt this round (unlike round 6, where it was the one territory
left stale) and its round-5/6 internal acquisition-watch duplicate row is gone -- exactly one
"Elbit | שותפות" row remains (plus three other, genuinely distinct Elbit rows of different event
kinds). Across all 7 populated territory files (US/IL/DE/GR/EU/GB/IN), BLUF word counts measure
28-39 words (counting inline citation markers, so real prose is shorter still) -- comfortably inside
the brief's ≤40-word budget everywhere. Buyer-pipeline/acquisition-watch tables and genuine
falsification-language "הנחות והפרכות" sections are present and populated in all 7. `bd_kr` remains
an essentially content-free stub report ("אין תקציר לתקופה זו... הדוח ייבנה מחדש כשיושלם") -- this is
a pre-existing characteristic of the thinnest-coverage territory, not a new round-7 regression.

### D8 -- סקר פטנטים (score 94, n=16, was 90)

Cleanly closes round 6's #10 worst-list item: the Anduril survey's one non-CPC-mapped patent's
fallback cluster label, previously the low-quality assignee-fragment string "אשכול נושאי: anduril /
inc / industries", now reads "אשכול נושאי: lattice / mesh" -- a genuine product/technology term
matching the patent's own title ("Lattice mesh"), not an assignee-name fragment. Every other cluster
in both fresh surveys carries a real CPC-code-based label (H04N5/H03M1/G01S7/H04N25 for DROIC;
H04W4/F41H3/G05D1/F41H11 for Anduril) with substantive, grounded prose. Assignee coverage remains
100%/100% (10/10, 6/6), zero "לא מסווג" strings anywhere, both CPC×assignee matrices render with
real data, and the methodology box with its coverage-% tag precedes the exec summary in both. One
unchanged minor gap: the patent surveys' own source-appendix reliability column is blank ("—") for
every row in both surveys -- unlike the daily/weekly news-item appendices, which now populate this
column (see D9) -- a narrower-scope carryover of round 6's general finding, closed for D6/D9 report
types but not yet for D8.

### D9 -- מכרזים/תחזיות/כנסים (score 91, n=19, was 85)

Major, structural, live-verified fix: tender id 42 ("Finland – Security cameras – Computer Vision
Technology for Airport Operations", TED notice 593909-2026) is `status='open'` in the live table --
the first-ever TED-sourced open row in the table's history (the only prior TED row, id 13, is a
2016 notice, long archived). This is consistent with the claimed TED date-filter/sort/pagination
fix and the new deadline-passed exclusion. Candidates id 34 (the Rotterdam Dutch HR contract, the
word-boundary-false-positive finding's own named target) and id 35 (a Northrop Grumman product page)
are both correctly `status='archived'`, `intake` left at `'candidate'` -- matching the repair
script's scoped, archive-only discipline; all 5 pre-existing accepted rows are unchanged. The
word-boundary keyword-matching logic and the `cpv_allow_prefixes`/`cpv_deny_prefixes` config are
both confirmed live in code, not just claimed in prose. `tender_forecasts` (10 rows) remains free of
near-duplicate groups, identical to round 6's clean result. One low-severity, unscored nit: tender
id 38 carries `status='unknown'`, a non-standard value.

## Worst 10 (most severe first)

1. **D4** -- `round_7_fixes.md`'s own claimed re-run successes for jobs 137-140 (found/0.9,
   partial/0.78, partial/0.7, found/0.85) do not match live DB state: all four jobs are
   `state='failed'` with a live ImportError and `result=NULL`; the freshest weekly and monthly
   reports' own investigation sections still show the identical stale not_found text these
   questions have shown since round 3.
2. **D4** -- the standard `eo investigate`/`investigate()` path (jobs 131/133/135/136) also failed
   outright this round with the same ImportError, on top of the separately-disclosed tool-calling
   gap -- the golden-investigation rerun mechanism is broken end-to-end via every path except a
   one-off manual batch-cloud call whose own results never persist either.
3. **D5** -- zero real answers obtained across 14 completed attempts (~28 minutes, two full passes)
   over all 8 golden questions -- every attempt returned only `gate_busy`; round 6's specific
   content findings could neither be confirmed fixed nor reproduced.
4. **D6** -- monthly report renders zero cross-source corroboration markers (0/59 appendix rows)
   despite the underlying DB being 100% populated -- `monthly.py` never wires in the marker-append
   functions `weekly.py` already uses.
5. **D6** -- daily report renders corroboration markers on only 1/21 appendix rows (~5%), missing
   the marker even on the one row (item 10) the same day's weekly report correctly marks.
6. **D3/D6** -- "Operation Atlantic City" still appears as 3 near-duplicate rows in the weekly
   business-events table, unchanged from round 6.
7. **D6** -- weekly's indicator-watchlist "ראיה" (evidence) column is blank on all 13 rows.
8. **D6** -- daily's indicator-watchlist table remains heavily clustered (10 of 11 rows on just 2
   underlying stories) -- wording diversity improved, but the over-representation pattern persists.
9. **D8** -- the patent surveys' own source-appendix reliability column remains blank for every row
   in both fresh surveys, closed for D6/D9 report types but not for D8.
10. **D7** -- `bd_kr_2026-09-07.md` remains an essentially content-free stub report (pre-existing,
    not a new regression, but still a real gap among the 8 territory files judged this round).

## Comparison with round 6

Real, durable, independently-verified progress landed in several domains: D1's entity-orphan cleanup
(245→16, a 94% reduction, closing round 6's #5 worst-list item at far larger scale than the two
named entities), D7's `bd_il` rebuild (closing its long-standing duplicate row), D8's Anduril cluster
label fix (cleanly closing round 6's #10), and D9's TED date-filter fix (the first genuinely new,
genuinely open TED notice the table has ever held, closing a gap that persisted rounds 1-6). D2's and
D3's durable clean baselines held.

D4 is this round's central story, and it moved sharply *backward* in what matters most: not the
historical golden jobs themselves (unchanged, as expected -- the brief never claimed those specific
job ids would change), but the credibility of this round's own verification work. Round 7's fixes
doc contains a detailed, specific, confidently-stated table of new investigation successes that
simply does not correspond to the live system -- the claimed jobs are database-confirmed failures.
This is a more severe class of problem than round 5/6's content-fabrication findings (a wrong fact
inside an otherwise-real answer): here, the claimed artifacts (the jobs, their confidence scores,
their "success" status) do not exist in a retrievable form at all, and the actual reader-facing
reports still show the old broken answers. D4's score (30, down from 78) reflects this directly.

D5 could not be assessed at all this round due to sustained, total chat unavailability -- a
different failure mode than round 6's content-quality findings, and one this report is explicit is
plausibly attributable to the brief's own disclosed concurrent e2e load rather than a code
regression, but it still means D5's score (40) is a "could not verify" score, not a "verified
improved" or "verified same" score.

D6's new corroboration-marker-rendering gap is a genuine partial regression on an otherwise
well-built new feature: the D1 check that gates on DB population passes cleanly, but nothing in the
brief's own D6/D1 checklist tests whether the *report* actually surfaces what the DB already knows,
so this gap was invisible to the deterministic scorer and only surfaced under a manual reparse of
the actual bytes of all three fresh reports' appendix sections.

## Recommended round-8 packages, ordered by expected score gain

1. **Root-cause and fix the `FallbackSynthesisOut` ImportError** blocking every investigation-rerun
   persistence path (both `investigate()` and the batch-cloud finalize step) -- this is the single
   highest-leverage fix available: it would not only make D4's already-good content (verified real
   and accurate in `investigation_log`) actually reach `jobs.result`, but would let the daily/weekly/
   monthly reports finally render the found/partial outcomes instead of the years-old not_found
   text, directly fixing D4's #1 and #2 worst-list items at once. Given the content already exists
   in `investigation_log` for jobs 137-140, a narrower interim fix (a repair script that reads the
   existing `investigation_log` notes and backfills `jobs.result` for these 4 specific jobs) could
   close much of the reader-facing gap even before the root ImportError is fixed.
2. **Wire `_append_item_corroboration_markers`/`_append_event_corroboration_markers` into
   `monthly.py`, and audit why `daily.py`'s own call (already present at line 144) only reaches
   1/21 of its own appendix rows** -- the DB-side feature is complete and well-tested; this is a
   narrow, mechanical rendering gap with an exact working reference implementation already sitting
   in `weekly.py` to copy from.
3. **Add a dedicated D5 test window that does not compete with e2e/pipeline traffic** (or a
   documented "quiet hour" for judge chat testing) -- this round's D5 assessment was a near-total
   loss of signal purely due to resource contention, not a content or protocol defect; without this,
   round 8's judge risks repeating the same zero-answer outcome regardless of any D5 content fixes
   that land in between.
