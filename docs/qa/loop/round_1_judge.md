# Round 1 -- Independent Judge Report

**Judge:** independent read-only agent (no pipeline runs, no fixes, no OMC tools). DB accessed read-only via
`runtime/eoa.env` + psycopg (port 5432, alembic head `0019`, matching repo HEAD). Live chat tested against the
running server on `127.0.0.1:8765` (`POST /api/ask`), one question at a time, 300s wall-clock cap enforced
client-side, one retry after ~2 minutes on timeout, per the task brief. Re-graded the SAME golden items/questions
as round 0 for comparability.

## Scores (judge component; merge with `round_1_auto.json`'s deterministic component per the 0.5/0.5 rubric)

| Domain | Score /100 | n | Round 0 | Delta | One-line verdict |
|---|---|---|---|---|---|
| D1 Classification/triage | 40 | 40 | 55 | -15 | One real fix landed (gershayim); the flagship null-triage bug is unchanged and confirmed active on items ingested *today*; several other claimed data fixes verifiably never reached the production DB. |
| D2 Summary/so-what | 55 | 40 | 58 | -3 | Untouched by round 1; unchanged truncation, worse templated-phrase count. |
| D3 Events/entities | 38 | 40 | 42 | -4 | Every round-0 defect (BlueHalo hallucination, missing Palantir, triplicate Norkin events, amount-unit bug) confirmed unchanged; the unit bug is now worse. |
| D4 Deep investigations | 48 | 6 | 35 | +13 | Genuine, verified fix: the anchored re-run of round 0's worst case (item 1352) now answers honestly instead of hallucinating off-topic. |
| D5 Chat ("ask the analyst") | 28 | 8 | 25 | +3 | A severe NEW infinite-repetition-loop bug offsets a modest completion-rate gain and the (client-side) markdown-rendering fix; hallucination rate stays high. |
| D6 Daily/weekly reports | 35 | 1 | 28 | +7 | Daily report's honesty about its own QA-gate failure improved; substance (tables-only, no synthesis) unchanged; weekly's flagship defect was quarantined, not fixed. |
| D7 BD focus reports (US/IL/KR) | 62 | 3 | 45 | +17 | Real, verified content improvement in bd_us; offset by a new doubled-quote defect and BD-report conference dates diverging from the DB. |
| D8 Patent survey | 45 | 2 | 20 | +25 | Real narrative/synthesis leap in the Anduril survey; offset by a new analytical overclaim and an untraceable relationship claim; core patent-attribution problem and empty timeline/CPC tables persist. |
| D9 Tenders/forecasts/conferences | 42 | 3 | 50 | -8 | Tenders table substantively unchanged (still 0 open); sources-freshness backfill claimed but not reflected (0/60, worse than round_1_auto.json's own same-day 23/40). |

## Cross-cutting finding: claimed data repairs not reflected in the production DB

`round_1_fixes.md` explicitly states its data repairs ran against `postgresql://...@127.0.0.1:5433/eoanalyst`
(alembic `0010`, "eight migrations behind" the repo's HEAD `0019`). This judge (per the task brief) reads
`runtime/eoa.env`, which points at port `5432` -- and that instance is at alembic **`0019`** (current HEAD), not
behind at all. Port `5433` refused this judge's credentials entirely (password auth failed), meaning it is a
genuinely different instance. Multiple explicit "Data: ... repaired" claims in `round_1_fixes.md` do **not**
appear on the `5432`/HEAD database:

- Item 24's subdomain: claimed "repaired directly -> `detectors_fpa`"; live DB shows `subdomain=NULL`.
- Items 5/10/51's `key_facts`: claimed deduped 8->6; live DB shows item 5 and item 51 both still at 8 facts,
  including two **literal** exact-duplicate sentences in item 51 (worse than the near-duplicate round 0 found).
- Items 8/33/39/55/1091: claimed re-triaged clean of truncation; live DB shows item 39's `summary_he` is still
  the exact truncated fragment from round 0 ("עדשה חדשה למטע") and its `triage_reason` also truncates mid-word.
- Jobs 46/47: claimed labelled `result.legacy_unanchored = true`; live DB shows neither job carries that key
  (a different key, `legacy_no_sources`, is present instead).
- `sources.last_fetched_at`: claimed 22 sources backfilled, round_1_auto.json itself reports 23/40 fetched
  within 7 days; a direct query at measurement time shows **0/60** active sources have `last_fetched_at` set at
  all -- worse than even the auto-score's own same-day reading.

Not every claimed repair is affected -- the gershayim/ASCII-quote fix (D1) and the source-fetch **code** fix
are confirmed present and working going forward -- but enough of round 1's "Data" claims are unverifiable or
contradicted on the DB this judge (and presumably the live app) actually serves from that round 2 should start
by confirming every repair script's `DATABASE_URL` against the one in `runtime/eoa.env` before claiming success.

## Evidence log (selected, with sources)

### D1 -- classification
- Items **1, 4, 7, 14**: still `domain='out_of_scope'`, `level=NULL`, `score=NULL`, `triage_reason=''`,
  identical to round 0. Items 1 and 4 were fetched **today** (2026-09-06 10:00:24/10:00:28) with
  `processed_stages` already including `'triage'` and still null output -- proof the bug is live, not legacy.
- **NEW**: items **52, 56, 57** have `domain=NULL` entirely (not even `out_of_scope`), `processed_stages =
  ['embed_dedup','classify']` -- the triage stage was never even attempted, despite being 1-2 days stale.
  Item 57 ("Army awards $192M to Palantir and Anduril to produce TITAN system") and item 52 ($465M AV laser
  contract) are both clearly EO/IR-relevant and sitting un-triaged.
- Item 24: `domain='secondary', subdomain=NULL` -- unchanged despite the explicit repair claim.
- Item 39: `summary_he='עדשה חדשה למטע'` (truncated) and `triage_reason` truncates mid-word at "...העדשה מיועדת
  למטע" -- unchanged despite the explicit re-triage claim.
- gershayim: 0/60 hits confirmed by direct query -- this one genuinely landed.

### D2 -- summary / so-what
- Templated so_what phrase `מחזק את מעמדה של` now found on **34 items DB-wide** (vs. round 0's 7-item
  golden-sample count) -- unaddressed and directionally worse.
- Item 39's truncated summary persists (see D1).

### D3 -- events / entities
- Item **47**: `entities_mentioned` still includes hallucinated **BlueHalo** -- unchanged.
- Item **50**: `entities_mentioned = [US Army, Anduril, BlueHalo]` still omits **Palantir**, though event 138's
  own title names Palantir as a lead party -- unchanged.
- Item **81**: still 3 duplicate Norkin-appointment events (ids 22/221/245, three spellings), 2 duplicate
  funding events (23/222), 2 duplicate Elbit-partnership events (24/223), plus one more row (224) -- unchanged,
  if anything one row worse.
- amount_usd unit bug: now confirmed on **4 rows across 3 items** (event 121/item 10, event 89/item 47, event
  84/item 96 -- all the same $464.8M AeroVironment E-HEL story duplicated across 3 item rows -- plus event
  55/item 269 at $540.7 instead of the real dollar figure). Round 0 found only 1 instance.

### D4 -- deep-search investigations (golden job ids 46, 47, 48, 70, 86, 91)
- Job **91** (2x-budget anchored re-run of item 1352, `expanded_from_job_id=86`): `outcome="not_found"`,
  `confidence=0.0`, `stopped_reason="not_found"` after 30 queries/10 pages/4 rounds. Unlike job 86, it did
  **not** repeat the confident off-topic MOSP-5000 hallucination -- a genuine, verified fix of round 0's
  single worst D4 finding. (Note: the task brief described this job as ending in "stopped_timeout"; the DB's
  own field says "not_found" -- a budget-cap exhaustion, not a wall-clock timeout; flagging the discrepancy.)
- Job 86 unchanged: still carries its original off-topic answer plus the 2026-09-06 retroactive
  `relevance_check` correction note round 0 already found.
- Jobs 46/47: neither carries the `legacy_unanchored` key claimed in round_1_fixes.md (see cross-cutting
  finding above); `confidence_capped_by_outcome` still fails on job 46 (0.3 confidence on `not_found`).

### D5 -- chat ("ask the analyst"), live-tested against `POST /api/ask`
- **NEW, severe**: Q1 (XM30) and Q8 (SPECTRO ISR) both produced a genuinely good, well-cited opening (direct
  answer + key facts with real inline `[n]` citations + analyst assessment) and then entered an **infinite
  repetition loop** inside the "## פערים / מה לא ידוע" (gaps) section -- Q1 ran 537s and generated 49,251
  characters of near-identical, combinatorially-growing hyphenated-compound bullets before the client gave up;
  Q8 hit a 300s wall-clock cap at 29,522 characters showing the same pattern (a ~3-bullet cycle repeated
  verbatim ~15+ times in a row). This reproduced identically in two independent questions -- a decoding
  degeneracy specific to the "gaps" section, not a fluke, and a materially worse failure mode than round 0's
  clean 120s timeouts (it burns GPU time and produces garbage rather than nothing).
- Completion this round (300s cap, one retry on timeout): Q2, Q3, Q4, Q7 completed cleanly in 25-38s; Q6
  completed on retry (41s, first attempt timed out at the retrieval stage); Q5 timed out at 300s and was
  confirmed as a true timeout on retry (30s read-gap, zero bytes -- GPU busy).
- Citation practice unchanged from round 0: Q2, Q3, and Q6 (the three cleanly-completed, non-looping answers)
  contain **zero inline `[n]` citations** in the body text despite a populated sources array. Only Q1 and Q8,
  before they looped, showed correct `[n]` usage.
- Markdown: raw `##`/`**` syntax is still emitted, but per the task brief the chat UI now renders Markdown, so
  this is no longer user-visible as raw text -- effectively resolved client-side, not a model-behavior change.
- Hallucination/conflation, still high: **Q2** (Iron Beam) answers almost entirely about a Rafael-India "Iron
  Dome" manufacturing deal while mislabeling it "Iron Beam (Tamir)" -- Tamir is Iron Dome's interceptor; Iron
  Beam is a laser system with no interceptor. **Q3** (Greece LORA) never once discusses LORA and instead
  answers entirely about Greece's unrelated $3.5-4B David's Sling/Barak-MX/Spyder package; the two LORA
  citations actually retrieved are about *Germany*, not Greece; also highly repetitive across its own sections
  (unchanged from round 0's complaint about this exact question). **Q4** and **Q7** both cite and describe the
  *same* unrelated academic arXiv paper (item 127, a visible/IR object-detection paper) as, respectively, the
  leading DROIC hardware technology and a government RFI document -- suggests retrieval-pollution, not two
  independent errors. **Q6** (AUSA 2026) is generic filler, entirely ungrounded in its own retrieved citations.
  Q5's partial transcript (before timeout) shows likely category errors (Barak-8, Arrow-3 framed as C-UAS
  comparators to Skyranger, when neither is a counter-drone system).

### D6 -- daily/weekly reports
- `daily_2026-09-06.md` (report id 28, `qa_passed=False`): exec summary still fully dropped (tables-only),
  same substance as round 0, but now states an honest, specific reason (citation-QA gate rejected the draft
  even after a repair attempt) -- honest, but not more useful, since no synthesis is offered in its place.
- Confirms round_1_fixes.md's own documented-not-fixed finding: the duplicate-sentence title "אילו מקצועות
  יהפכו מבוקשים בעידן ה-AI?" appears both as a table row title and an appendix-source title.
- **NEW**: the "תעשייה ישראלית -- תחרות ומתחרים" table includes two items that are not business/competitor
  intelligence at all (an IDF-scapegoat opinion piece; a Lebanon-ridge sovereignty piece), both tagged only
  with the generic entity "IDF" -- a relevance gap in what feeds this table.
- `weekly_2026-09-05.md` (round 0's single worst-listed item) was renamed to `.contaminated.md.bak`; no
  replacement was generated. The defect is quarantined, not fixed (not independently re-graded in full since
  it was outside this round's named new-artifact list).

### D7 -- BD focus reports
- `bd_us_2026-09-06.md`: real, populated executive summary and a genuinely cited 7-row actions table -- a
  substantial, verified improvement over round 0's completely empty version.
- **NEW**: doubled ASCII quotes ("ארה\"\"ב" -- two literal `"` characters) appear 7 times across prose and
  table cells, instead of a single Hebrew gershayim ("ארה\"ב"). Same defect *class* round 1 claims fixed for
  DB fields, resurfacing in a different form at report-generation time.
- **NEW**: conference dates cited in bd_us.md diverge from the canonical `conferences` DB table -- AUSA 2026
  cited as "2026-10-01 - 2026-10-18" vs. DB `start_date=2026-10-12`; SOF Week 2027 cited as starting
  "2027-05-01" vs. DB `2027-05-03`. Matches round_1_auto.json's own "2/4 matched" finding.
- `bd_kr_2026-09-06.md` (not rebuilt): still fails `actions_table_nonempty`, but is an honest, well-formed
  "no activity this window" report for two named watchlist competitors -- arguably the check penalizes correct
  behavior (no fabricated actions) rather than flagging a real defect.

### D8 -- patent survey (reports rows 35 and 36, both from 2026-09-06 ~09:19-09:23, both `qa_passed=true`)
- Row 36 (`patent_survey_Anduril_Lattice_...`): real per-patent narrative, technology-cluster writeup,
  assignee profile, and relationship map now present for all 6 patents -- a genuine synthesis leap over round
  0's flat title+score rows.
- Same survey's executive summary asserts Anduril is "the exclusive player" in the field based on 5/6 patents
  simply lacking assignee data (a data-source gap, not evidence of exclusivity) -- an unsupported inference
  presented as fact.
- Same survey's relationship map includes "Anduril <-> Elbit Systems | מיזוג/רכישה | Sigma 155 howitzer
  system" -- untraceable to any of its 14 cited sources (the only Elbit-adjacent source is solely about a
  personnel appointment); flagged as a likely fabricated/cross-wired relationship.
- Timeline and CPC-code tables remain completely empty in both rows 35 and 36, exactly as round 0 found; the
  deterministic auto-check reports 100% because it only checks heading presence, not data population.
- Row 35 (`patent_survey_FPA...DROIC...`): commendably honest about its own gap (LLM was unreachable at
  generation time; explicitly refuses to invent assignee profiles "כדי לא להמציא נתונים") but delivers
  near-zero synthesis as a result.

### D9 -- tenders / forecasts / conferences
- `tenders` table: still exactly 5 rows, ALL `status='closed'`, deadlines 2015/2016/2023/2025-10-01 (past) --
  unchanged, word-for-word, from round 0. The `tenders_open_rows_have_dates` auto-check passes only because
  it is vacuous with zero open rows to check.
- `bd_us.md`'s own tenders table lists 5 different, unpersisted RFI/sources-sought listings not present in the
  `tenders` DB table at all -- report generation and the persisted table are out of sync.
- `conferences`: still solid, unchanged (15 rows, real 2026-2028 dates, matches watchlist config).
- `sources.last_fetched_at`: claimed backfill (22 sources) not reflected; **0/60** active sources have this
  field set as of this measurement, worse than round_1_auto.json's own same-day 23/40 reading.

## Worst 10 items (this round)

1. **D5** Infinite repetition-decoding loop in the chat "gaps" section (Q1 XM30, Q8 SPECTRO ISR) -- new,
   severe, reproducible; burns 300-537s and 30-49K characters of garbage per occurrence.
2. **Process/D1/D9** Round 1's claimed data repairs ran against a different DB instance (port 5433, alembic
   0010) than the one this judge and (presumably) the live app read (port 5432, alembic HEAD 0019) -- multiple
   "fixed" claims in round_1_fixes.md do not reflect in the production data.
3. **D1** Items 1, 4, 7, 14 -- silent triage failure, unchanged from round 0, confirmed actively reproducing on
   items ingested today.
4. **D1** Items 52, 56, 57 -- new, worse null-triage variant: never even reach the triage pipeline stage.
5. **D3** Item 81 -- still 3 duplicate Norkin-appointment events with 3 name spellings, unchanged.
6. **D3** amount_usd unit bug -- now spans 4 events across 3 items, worse than round 0's single instance.
7. **D5** Q3 (Greece LORA) -- confidently answers an entirely different, unrelated Greek air-defense deal,
   never once addressing LORA itself.
8. **D5** Q4/Q7 -- the same unrelated academic arXiv paper is cited as both a DROIC hardware trend and a
   government RFI document across two different questions.
9. **D8** Anduril patent-survey exec summary overclaims market exclusivity from a data gap, plus an
   untraceable Anduril-Elbit relationship claim.
10. **D9** Tenders table -- still 0 open rows, all deadlines already past, unchanged from round 0.
