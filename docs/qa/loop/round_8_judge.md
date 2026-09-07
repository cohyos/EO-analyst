# Round 8 judge (J8) — independent read-only pass

**Method:** read-only agent (no pipeline runs, no fixes, no OMC skills/agents/tools, no DB writes,
no git commands, no process kills, no stack start/stop). `DATABASE_URL` loaded once per shell via
`set -a; . runtime/eoa.env; set +a`, never printed. Every query confirmed port 5432, db
`eoanalyst`. The API process restarted today at 10:05:23 (`api.startup`), and daily/weekly/monthly/
`pl_*`/`bd_*` reports were rebuilt after that restart — `weekly_2026-09-07.md` rebuilt 12:21, i.e.
*after* this judge's DB checks began — so round-8 code was live for both the DB/report inspection
and the chat test. D10 skipped per brief.

## Scores (D1–D9)

| Domain | Score | n | Round 7 (post-J7b) | Δ |
|---|---|---|---|---|
| D1 — classification/sorting | 92 | 44 | 91 | +1 |
| D2 — summary/so-what | 86 | 40 | 87 | −1 |
| D3 — events/entities | 90 | 50 | 87 | +3 |
| D4 — deep investigations | 63 | 12 | 45 | +18 |
| D5 — chat ("ask the analyst") | 45 | 1 | 80 | −35 |
| D6 — daily/weekly reports | 85 | 100 | 74 | +11 |
| D7 — BD reports | 90 | 15 | 86 | +4 |
| D8 — patent surveys | 93 | 16 | 94 | −1 |
| D9 — tenders/forecasts/conferences | 89 | 19 | 91 | −2 |

D5's drop is a sample-size and availability artifact, not a content-quality regression — see below.

## Headline story: a real structural fix landed, two of its own claimed sub-fixes did not reach live content, and D5 could barely be tested

The round's single biggest deliverable — **D4's item-10/47 zero-page-read bug**, round 7/7b's most
severe worst-list finding — is **genuinely fixed and verified live**, not just claimed. Job 156 (a
rerun of the exact AeroVironment E-HEL laser question that has returned a blank `not_found` since
round 3) now shows `outcome=found`, `confidence=1.0`, with `investigation_log` recording three real
page reads (army.mil's own release, avinc.com's own announcement, militarytimes.com), a substantive
8-fact Hebrew answer, and an honest disclosure that the official DoD release omits the exact dollar
figure the press/company sources give. This result already renders correctly as the first entry in
today's live `weekly_2026-09-07.md` "חקירות עומק" section. The new `_force_read_top_hits` safety net
is not just unit-tested — it worked on a real live rerun.

But two of the *same* package's other claimed fixes (job 146's undisclosed Cloudflare-interstitial
citation, job 147's settled-fact/hedge inconsistency on the Norkin appointment) are **unchanged,
live, in the current report**, because the underlying jobs were never rerun — the fixes only apply
at investigation time, not retroactively. Job 147's case is worse than a timing gap: its own
contradictions field already discloses the hedge ("no final official appointment was stated"), but
the headline sentence still asserts the decision as fact, and the new hedge-downgrade guard's fixed
verb list (הוחלט/נבחר/זכה/נחתם) likely would not even catch this exact "בחירת X על פני Y" phrasing
on a future rerun — a design gap, not just a rerun-timing gap.

A new, narrower instance of the same underlying problem also surfaced: item 96, a plain
`dedup_of=10` Hebrew duplicate of the golden AeroVironment story, has its own never-rerun `not_found`
investigation (job 45) rendering directly beside item 10's newly-fixed `found` answer in the same
live weekly report — a reader sees contradictory outcomes for the identical real-world fact in one
document. The round-8 reconciliation fix groups reruns by question-text and job lineage, correctly
catching the item-1352 (job 86/148) merge case it was built for, but has no mechanism for an item's
own `dedup_of` chain.

D6's corroboration-marker fix is real and large: appendix rows carrying a marker went from
5%/68%/0% (daily/weekly/monthly, round 7) to 90%/95%/95% today — closing round 7's #4 and #5
worst-list items outright, and the "Operation Atlantic City" triple-listing (carried over unfixed
since round 6) is now a single row. But the same package's indicator-watchlist evidence-column fix,
while real code with real tests, is **not meaningfully populating live output**: daily's table (now
correctly capped at 8 rows) shows evidence on 0 of 8 rows; weekly shows it on 2 of 16. The brief's
own bar ("evidence column populated where matches exist") is not met in practice, and the round-8
fixes doc's own live check only ever confirmed 1/13 for weekly and never checked daily at all.

D5 could not be properly measured this round. Only Q1 of the 8 golden questions completed within a
240-second window; the remaining 7 (including a fresh retry of Q2) returned HTTP 200 immediately and
then hung with zero streamed bytes. No `POST /api/ask` access-log line was written for *any* of the
8 calls today — not even for Q1, which genuinely completed with real, well-cited content — and the
API error log shows sustained `/ws/status` WebSocket churn plus repeated `async generator ignored
GeneratorExit` errors through the same window. This is consistent with, but not proof of, the
brief's disclosed concurrent Playwright e2e load rather than a round-8 code regression in the ask
pipeline itself; it is reported as a live finding either way, since a silent hang with no `gate_busy`
signal reaching the client is a worse user-facing failure mode than round 7's own uniform,
at-least-explicit gate_busy result. The one sample that did complete is genuinely clean on both of
this round's specific D5 target defects (zero `===SOURCES_JSON===` leaks, no numeric-count
fabrication) and shows the same good honest-refusal behavior round 7 praised — but it also renders a
new, minor defect: an empty "### עובדות מרכזיות" section with zero bullets underneath.

## Domain-by-domain evidence

### D1 (92, +1) — classification/sorting
- 0/40 golden items have a score↔level mismatch against `config/config.yaml` thresholds — identical
  clean state to round 7.
- Entity orphan rate holds at 16/334 (4.8%), no regression; a broad junk-name scan (short tokens,
  "partners"/"unknown" substrings) returns only legitimate short designators (IAI, RTX, HAL, IDF,
  AV, NSM, …) — zero junk entities.
- `corroboration_populated_for_recent_in_scope` replicated live: 63/63 (100%), same as round 7, well
  above the ≥90% bar.
- New, real but thin: `product_lines` tags on `items` went from 1 of 6 lines covered
  (targeting_pods=2) to 4 of 6 (targeting_pods=2, mws_eo=1, lorop_pods=1, ball_gimbals_16in=1),
  matching the fixes doc's own claimed counts exactly via a live `GROUP BY unnest(product_lines)`
  query. `eo_air_defense_warning`/`border_long_range_eo` remain at 0 items — the fixes doc's own
  "corpus too small" explanation holds up under a live recheck (502 items total, only 63 pass the
  strict in-scope 90-day gate).

### D2 (86, −1) — summary/so-what
- 0/DB-wide matches for the 4 banned filler phrases in `so_what_he` — same clean result as rounds
  6-7.
- Not independently re-sampled beyond the DB scan and the single live Q1 chat answer that surfaced
  as a D5 byproduct (genuinely analytical, no template structure, honest refusal to bridge two
  unrelated documents). Time was reallocated to D4/D5/D6, which the brief flagged as this round's
  actual scope; score held near round 7's level rather than moved on thin evidence.

### D3 (90, +3) — events/entities
- **Fixed:** "Operation Atlantic City" is now exactly one row in the live weekly business-events
  table (`2026-09-05 | פריסה | Kongsberg | Operation Atlantic City | — | [8]`), down from 3
  near-duplicate rows carried unaddressed since round 6. The new `_merge_same_program_events` pass
  works on live data, not just its 5 new unit tests.
- 0 events belong to an out-of-scope/archive item; 0 mislabeled `kind='ניסוי'` events; all event
  kinds in use remain valid enum values.

### D4 (63, +18) — deep investigations
- **Fixed, verified live:** job 156 (item 10/47) — outcome `found`/1.0, 3 real reads, honest
  disclosure — already rendering correctly in today's weekly report. Closes round 7/7b's #1 and #2
  worst-list items.
- **Unchanged, live:** job 146 (AARGM-ER) still cites a Cloudflare interstitial with no disclosure;
  job 147 (Norkin) still states a hedged fact as settled, with an internal contradiction between its
  own headline and its own gaps section. Both fixes exist in code but were never applied to these
  already-computed jobs.
- **New:** item 96 (dedup of item 10) renders a stale `not_found` investigation beside item 10's own
  fixed `found` answer in the same report — a variant of the contradictory-reruns problem the
  round-8 reconciliation fix does not cover.
- Verified: blocked-vs-not_found distinction works live (Volkswagen-Rafael item correctly shows
  "נחסם", not a generic not_found); job 130's honest short-circuit reproduces as expected.

### D5 (45, n=1) — chat
- 1/8 golden questions completed; 7/8 hung with HTTP 200 and zero streamed bytes past 240s
  (reproduced on retry). No access-log line for any of the 8 calls; heavy concurrent WebSocket
  churn and asyncio errors in the same window's error log.
- The one completed sample: 0/1 SOURCES_JSON leaks, no numeric fabrication, honest refusal to
  bridge XM30 and an unrelated retrieved Finnish RFI document — but a new empty "key facts" section.
- Could not check the entailment-check log claim: the api log's structured event stream stops
  emitting entirely after 11:04:25 today, before this judge's testing window began at 12:27, so zero
  `ask.entailment_check_*` lines exist for any of today's traffic.

### D6 (85, +11) — daily/weekly/monthly reports
- **Fixed:** corroboration markers now reach 90%/95%/95% of appendix rows (daily/weekly/monthly), up
  from 5%/68%/0% in round 7 — closes round 7's #4/#5 worst-list items, including monthly's total
  absence.
- **Fixed:** daily's indicator table capped at 8 rows (was 11).
- **Not delivered as claimed:** the indicator evidence column is still almost entirely blank live
  (daily 0/8, weekly 2/16) — the brief's bar is not met despite real, tested code.
- Heading budgets hold (daily 13≤15, weekly 15≤16, monthly 11≤16); no malformed citation markers or
  banned filler phrases found.

### D7 (90, +4) — BD/product-line reports
- New: all 6 product-line reports meet the D7 bar well — spot-checked `pl_targeting_pods` in full
  (BLUF, honest thin-data caveat, 5 genuinely falsifiable assumptions, populated tables); the two
  empty-line reports render an honest empty path rather than padding.
- Fixed: `bd_kr` now carries a real BLUF line and a "מה נבדק" methodology section, closing round 7's
  #10 worst-list item.
- Minor cosmetic nit: one product-line report mixes a Hebrew-translated and an untranslated English
  event-kind label in two different tables of the same report.

### D8 (93, −1) — patent surveys
- Cluster labels in both fresh surveys are genuine CPC/technology terms, zero assignee-fragment or
  generic-country junk clusters — same clean state as round 7.
- Appendix reliability column remains blank for every patent row in both surveys — documented as
  the correct, honest behavior of the new host-matching logic (all rows are Google Patents URLs,
  never a monitored news host), not independently verified against a live host-match case.

### D9 (89, −2) — tenders/forecasts/conferences
- Unchanged: the one open TED tender (id 42) remains the only open row; the two previously-archived
  false-positive candidates (ids 34/35) remain correctly archived.
- New, real: 2 tenders now carry `product_lines=['targeting_pods']`, up from 0 — matches the fixes
  doc's claim exactly.
- New, minor: the single open TED tender itself carries no `product_lines` tag despite plausible
  relevance — the one row this feature would most matter for.

## Worst 10 (most severe first)

1. D5 — only 1/8 golden questions testable; 7/8 hung silently (HTTP 200, zero bytes, 240s timeout,
   reproduced on retry); no access-log evidence for any of the 8 calls.
2. D4 — job 146 still cites an undisclosed Cloudflare interstitial as its sole source, unchanged
   live.
3. D4 — job 147 still asserts a hedged fact as settled at confidence 0.9, unchanged live; the new
   guard's verb list likely wouldn't catch this exact phrasing even on a rerun.
4. D4 — item 96 (dedup of golden item 10) shows a stale `not_found` beside item 10's own fixed
   `found` answer in the same report.
5. D6 — indicator-watchlist evidence column still almost entirely blank live (daily 0/8, weekly
   2/16) despite a shipped, tested fix.
6. D9 — the one open TED tender itself carries no product-line tag.
7. D5 — the one completed answer renders an empty, bullet-less "key facts" section.
8. D7 — one product-line report mixes translated/untranslated event-kind labels.
9. D8 — patent-survey reliability column still blank for every row (documented as expected, not a
   new defect).
10. D9 — tender id 38 still carries a non-standard `status='unknown'`.

## Comparison with round 7 (post-J7b)

Real, verified structural wins: the D4 zero-read bug (the round's headline defect) is fixed and
live; D3's Atlantic City dedup (carried since round 6) is closed; D6's corroboration-marker coverage
jumped from single digits/two-thirds/zero to 90%+ across all three report kinds; D7's bd_kr stub is
a genuine upgrade; a brand-new, well-built product-line report type shipped. Set against that: two
of D4's three citation-integrity sub-fixes never reached already-computed jobs and remain live
exactly as J7b found them; D6's evidence-column fix shipped but isn't populating output; and D5 — the
domain round 7 fought hardest to get a clean signal on — could only be sampled once this round due to
a live availability problem, so its score drop reflects missing data more than proven regression.

## Recommended round-9 packages, ordered by expected gain

1. **Rerun jobs 146 and 147** (not just item 10/47) through the now-fixed `investigate()` path, and
   separately fix `_downgrade_unhedged_decision_claims`'s verb list to catch nominalized decision
   phrasing ("בחירת X על פני Y", not just the 4 listed verbs) — this closes 2 of the top 4 worst-list
   items in one move and directly validates code that already shipped but was never applied to the
   content it was built to fix.
2. **Diagnose the D5 request hang** (HTTP 200 + zero bytes + no access-log line, even for a
   completed request) before the next round's chat test — whether it's the disclosed e2e contention
   or a code-level issue in the streaming/logging path, it is currently impossible to distinguish,
   and it blocked measurement of 7 of 8 golden questions.
3. **Make the D6 evidence-column fix actually populate report output**, and extend D4's
   reconciliation grouping to also fold in an item's own `dedup_of` chain (not just question-text/
   lineage matching) — the item-96 finding shows the current mechanism misses a real, easily-
   triggered case (any dedup pair where only one side was ever rerun).
