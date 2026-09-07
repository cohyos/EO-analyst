### R8-reports status

Scope: `docs/qa/loop/round_7_judge.md` sections D6/D3/D8/D7 (report-side findings only). Files
touched: `agent/eoa/report/daily.py`, `agent/eoa/report/weekly.py`, `agent/eoa/report/monthly.py`,
`agent/eoa/report/indicators.py`, `agent/eoa/patents/survey.py` (appendix rendering only),
`agent/eoa/report/bd_territory.py` (empty-territory path only), `tests/unit/test_reports_round8.py`
(new, 35 cases, no DB/network/LLM). Lint/format clean (`ruff check`/`ruff format --check`).

#### 1. Corroboration markers reach every appendix row (D6 #4/#5)

Root cause, found by reproducing the round-7 judge's own live daily (`daily_2026-09-07.md`)
directly against the DB (`DATABASE_URL` from `runtime/eoa.env`, read-only): `_append_item_
corroboration_markers` was only ever called on `collect_items()`'s own ~10-row result (the cap is
`daily_report_max_items=10`) and, separately, `collect_week_items()`'s own result -- never again on
the *fully extended* `citation_items` registry each report builds afterward (event fallbacks,
tender-forecast citations, the tech-watch/Israel-industry/patents/acquisition-watch additive
tables). `monthly.py` never called either marker function at all. Confirmed live: item id=10 (the
AeroVironment $465M laser story, `item_corroboration.status='corroborated'`, count=2, independently
queried) sits at appendix row n=5 in the live `daily_2026-09-07.md` with no marker, even though
`collect_items()` alone (run directly against the live DB) correctly marks every row it returns --
proving the gap is specifically in what happens to rows added *after* that first pass.

Fix: both marker functions (`eoa.report.daily._append_item_corroboration_markers`/
`_append_event_corroboration_markers`) are now idempotent (skip a title/summary already ending
with the exact marker string), and `build_daily`/`build_weekly`/`build_monthly` each call
`_append_item_corroboration_markers(citation_items)` / `_append_event_corroboration_markers
(events_with_n)` a second time, right before rendering, after every additive-table hook has had a
chance to extend the registry. `monthly.py` now imports both functions from `eoa.report.daily` (it
imported neither before). Event markers were already reaching 100% of rows via `collect_events`'s
own internal call (on the fully filtered/deduped/limited list) in all three report kinds -- this
was not itself broken, but the second call is cheap and idempotent, so it runs unconditionally for
symmetry and defense-in-depth.

Verified live (see "Live verification" below for the daily/monthly rebuild counts).

#### 2. "Operation Atlantic City" triple-listing (D3/D6 #6)

`eoa.report.daily._dedup_events`'s two existing passes both key on `kind` (directly or via a
kind-scoped amount match), so three events sharing the same item + `program` but three different
`kind` values (deployment/test/partnership) never collided. Added a third pass,
`_merge_same_program_events`: groups by (item_id, non-empty `program`) or, when `program` is empty,
(item_id, customer, date); merges each group into one row, keeping the most specific `kind`
(`_EVENT_KIND_PRIORITY`: contract_award > m_and_a > deployment > partnership > investment > launch
> regulation > test > other) and unioning `parties` (case-insensitive dedup). 5 new unit tests
cover the exact three-kind scenario, a contract_award-outranks-deployment case, the
customer+date fallback, and the negative case (different items with the same program string must
not merge).

#### 3. Indicator-watchlist evidence column (D6 #7)

`render_watchlist_table`'s evidence cell only ever cited a row's `matured_evidence_item_id` --
every `open`/`new`/`dropped` row showed "—" even when this issue's own items plainly matched. New
`_evidence_cell`: keeps the precise matured-evidence id first, then falls back to a fresh
`_item_matches_indicator` search over this issue's `items` (up to 3 distinct `[n]` citations); a
`dropped` row (unmatched by definition at drop time) still correctly stays "—". `render_
watchlist_table` and `build_indicator_watchlist_section` now thread `items` through for this.

#### 4. Daily indicator-table per-story clustering (D6 #8)

New `_cluster_key` (top-2 longest content tokens, reusing the existing `_content_tokens` helper) +
`_cap_watchlist_rows`: at most 3 rows per cluster (preferring a row with evidence, then the most
recently seen), then the table capped at 8 rows total (keeping the oldest-`first_seen` rows when
trimming further). Gated on `kind == "daily"` only (`render_watchlist_table`'s new `kind` param) --
the weekly/monthly tables weren't reported as over-crowded and are left uncapped. Documented in the
module docstring per the brief.

#### 5. Patent-survey appendix reliability column (D8)

`docx_builder`'s appendix renderers already call `_reliability_for(item)` (which returns
`item["reliability"]` verbatim when present) for every report kind -- a `db_item` registry entry
(a real news article cited into the survey) was already covered for free via its real
`source_name`; the gap was pure-patent entries, whose `source_name` is a comma-joined assignee list
(never a real outlet). New `agent/eoa/patents/survey.py::_appendix_reliability` derives a value for
those via a host match between the patent's own `url` and `sources.url` (a new, read-only,
host-keyed `_source_host_reliability_map`, the mirror of `docx_builder`'s own name-keyed one), and
`items_for_appendix` now sets `"reliability"` on every entry. Most pure-patent rows (Google Patents
/ a national patent office, never a monitored news source) will still legitimately render "—" --
that's an honest result, not a bug; a hit only happens when the same host is also a monitored
source.

#### 6. `bd_kr`-style empty-territory stub (D7 #10)

`_no_items_draft()`'s empty-territory case rendered only one system-note sentence. `build_bd_
territory` now prepends a deterministic BLUF-style line to `system_note_he` ("שורה תחתונה: לא
זוהתה פעילות בטריטוריה בחלון הנבדק; הופעלה חקירה ממוקדת" when an expansion search was actually
triggered, an honest shorter variant otherwise -- never a `bluf: list[Sentence]`, since there is no
item in an empty registry for one to cite) and appends a new "מה נבדק" extra_sections bullet list
(sources scanned, the exact time window, the watchlist companies checked, and the expansion
deep-search job id). `_enqueue_territory_expansion_search` now returns that job's id (a fresh one,
or an already-pending one it reused) instead of `None` always, via a new shared
`_pending_expansion_search_id` helper. D7's own empty-territory exemption
(`eoa.qa.d7_bd_report._EMPTY_TERRITORY_MARKER_HE`) keeps passing -- it only requires that marker
string to stay present in the text, which it does (still `_no_items_draft`'s own base sentence,
now inside a longer note).

#### Tests

`tests/unit/test_reports_round8.py`, 35 cases, all green, no DB/network/LLM (every DB-touching
function monkeypatched at module level, same convention as `test_report_daily.py`/
`test_corroboration.py`/`test_report_bd_territory.py`): corroboration-marker status mapping +
idempotency + the full-registry-extension mechanism (daily/weekly/monthly), the Atlantic-City
event merge (3 kinds -> 1, priority ordering, customer+date fallback, negative case), the evidence
column (matured/open/dropped/no-match), the per-story cap (cluster grouping, per-cluster
preference, total cap, daily-vs-weekly gating), the patent-appendix reliability derivation
(db_item short-circuit, host match, no match, no url), and the bd_kr stub (pending-job reuse,
new-job creation, failure handling, the two BLUF variants, the D7 exemption marker survives).

```
PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_reports_round8.py \
  tests/unit/test_report_daily.py tests/unit/test_report_weekly_monthly.py \
  tests/unit/test_weekly_round6.py tests/unit/test_corroboration.py tests/unit \
  -q -p no:cacheprovider -k "indicator or survey or patents_round"
```
257 passed, 0 failed.

#### Live verification

**Daily** (`eo run report`, `EOA_PIPELINE=1`, `DATABASE_URL` from `runtime/eoa.env` -> report_id
92, `qa_passed=True`, `output/reports/daily_2026-09-07.md`, rebuilt over the same live daily window
the round-7 judge itself examined):
- Corroboration markers: **19/21** sources-appendix rows now carry a marker (18 `(מקור יחיד)` + 1
  `(מאומת ב-2 מקורות)`), up from the round-7 judge's own count of 1/21; the remaining 2 rows are
  legitimately `status='unknown'` (no marker by design, per the original feature's own rule).
- D6 score: 87.2 (n=44), up from round-7's 74 -- the one failing check
  (`every_factual_exec_summary_sentence_cited`) is a pre-existing exec-summary drafting issue
  outside this package's file scope.
- Indicator watchlist: 14 rows processed this issue (open=11, new=3, matured=0, dropped=0) -> the
  daily table renders exactly **8** rows (the new per-story cap), down from round-7's 11.
- Events table: only 2 distinct events this run (thin daily window, no live Atlantic-City-style
  triplication to observe today) -- the merge logic itself is covered by 5 dedicated unit tests
  including the exact 3-kind scenario.

**Weekly evidence column** (re-rendered directly from live DB rows via `collect_week_items` +
`indicators._fetch_open_indicators("weekly")` + `render_watchlist_table` -- no full weekly rebuild
needed, per the brief): 39 items collected this week, 13 open weekly indicator rows, of which 1 now
shows a real citation (previously all 13 were "—"). Three example rows (verbatim from the render):
  - `| צבא ארה״ב מתכנן הזמנות TITAN נוספות בשנת הכספים 2027. | 2026-09-07 | פתוח | [13][19] |`
  - `| להערכתנו מגמת ההשקות תימשך ברבעון הקרוב. | 2026-09-06 | פתוח | — |` (no matching item this
    week -- correctly "—")
  - `| החלטת אסטוניה בנוגע לרכש קלע דוד מרפאל צפויה תוך כשני חודשים מיום הפרסום. | 2026-09-07 |
    פתוח | — |` (no matching item this week -- correctly "—")

**bd_kr** (`eo run bd --territory KR`, live -- genuinely empty territories skip the LLM entirely,
so this rebuild was near-instant, not a 3-10 min cost): report_id 94, `qa_passed=True`. The rebuilt
`output/reports/bd_kr_2026-09-07.md` now reads:
```
שורה תחתונה: לא זוהתה פעילות בטריטוריה בחלון הנבדק; הופעלה חקירה ממוקדת. לא זוהו בטריטוריה זו
פריטים חדשים בחלון הזמן שנבדק. אין ממצאים לדוח המיקוד. הופעל חיפוש ממוקד בטריטוריה; הדוח ייבנה
מחדש כשיושלם.

## מה נבדק
- מקורות: כל פריטי החדשות הנקיים (רמה צהוב ומעלה) שפורסמו בחלון הזמן, מסוננים לפי זיהוי
  גיאוגרפי/ישויות/טקסט לטריטוריה KR.
- חלון זמן שנבדק: 2026-06-10 עד 2026-09-07 (90 ימים).
- חברות מעקב (watchlist) שנבדקו: Hanwha, LIG Nex1.
- חקירה ממוקדת: הופעלה (מזהה משימה #149).
```
D7 score on the rebuilt file: **100.0** (n=1) -- the empty-territory exemption
(`_EMPTY_TERRITORY_MARKER_HE`) still fires correctly (the marker string survives inside the longer
note), so BLUF/buyer-pipeline/assumptions are still correctly treated as not-applicable rather than
scored against an empty registry.

**Monthly** (`build_monthly(period_end=2026-09-07)` -> the same `2026-09-01`..`2026-09-30` window
and the same `output/reports/monthly_2026-09-30.md` filename the round-7 judge's own report
examined; report_id 95, `qa_passed=True`; the `claude` CLI timed out after 360s on the first
attempt and the pipeline's own fallback chain finished the draft via `agy`/gemini-3.1-pro-high,
which is why this rebuild ran well past the brief's ~10-min estimate -- an environment/provider
latency issue, not a defect in this package):
- Corroboration markers: **56/59** sources-appendix rows now carry a marker (51 `(מקור יחיד)` + 3
  `(מאומת ב-2 מקורות)` + 2 `(מאומת ב-1 מקורות)`), up from the round-7 judge's own count of **0/59**.
  The 59-row appendix size itself exactly matches what the judge examined, confirming this is the
  same underlying report content, not a smaller/easier window.
- `score_D6` was not run on this file: its checks (israel/tenders/indicator-watchlist section
  presence, "daily" heading budget, etc.) assume the daily/weekly report shape and are not
  monthly-aware -- running it here produces a misleadingly low score against checks that don't
  apply to monthly's own (different, correct) structure. Out of this package's scope to fix.

**Patent survey**: not rebuilt live this round (a full survey run re-scans/re-analyzes patents,
materially more expensive than a report rebuild, and wasn't in the brief's mandated live-check
list) -- covered instead by 5 dedicated unit tests (`db_item` short-circuit, a real host match, no
match, no url, and the secondary-vs-primary reliability split).

#### What's left (for the user)

- `tests/unit/test_report_bd_territory.py` (the existing, pre-round-8 test file -- 49 cases) took
  6 minutes to finish (364.49s) instead of its normal few seconds, because the machine was under
  heavy, unrelated memory pressure the whole session (one `python.exe`, not launched by this task,
  holding ~28GB RSS -- consistent with the operator's own multi-hour model-training jobs per this
  project's standing process-kill-discipline note; never touched, per that same rule), which also
  slowed every other pytest run and the live monthly LLM call itself. Result: **49 passed, 0
  failed** -- no regression from this round's `build_bd_territory`/`_enqueue_territory_expansion_search`
  changes.
- The full `tests/unit` suite (beyond the brief's own mandated filtered command, which passed) was
  also kicked off as extra diligence; still running as of this writing for the same memory-pressure
  reason -- not blocking, since every file this round touched is covered by the mandated command or
  this round's own dedicated suite (now fully confirmed, including bd_territory).
- Patent survey `_appendix_reliability` is unit-tested only, not live-verified against a real
  rebuilt survey file (a full survey run re-scans/re-analyzes patents, materially more expensive
  than a report rebuild, and wasn't in the brief's mandated live-check list) -- worth a spot-check
  next time a patent survey is rebuilt for any other reason.

### R8-tagging status

#### Step 1 -- measurement (before any code change)

In-scope query used throughout this section: `items` rows with `level IN ('red','orange','yellow')
AND security_status = 'clean' AND dedup_of IS NULL AND COALESCE(published_at, fetched_at,
created_at) >= now() - interval '90 days'` -- **63 rows**, read from the live DB (`runtime/eoa.env`,
port 5432) with `set -a; . runtime/eoa.env; set +a` and never printed/catted. Pre-existing state at
measurement time: only 2 `items` rows carried any `product_lines` tag at all (both `targeting_pods`
-- "Litening advanced targeting pod Tender..." id 5122 and "Sniper advanced targeting pod (atp)
Tender..." id 1861), 0 `events`/`tenders`/`tender_forecasts`, and 2 `patents` (`mws_eo`) -- matching
the brief's own "targeting_pods has 2 items, mws_eo 2 patents, other four lines nothing".

A broad recall-measurement script (`scripts` was not touched for this -- ad hoc, in the session
scratchpad) matched each line's *pre-broadening* `keywords_he`/`keywords_en`/`aliases`/
`exemplar_systems`/`competitors` against `title + summary_he + clean_text` for all 63 in-scope
rows (word-boundary for Latin terms, substring for Hebrew, competitor names included on their own
-- i.e. deliberately *not* applying the tagger's own "competitor alone is not enough" gate, since
the point here is raw text-recall, not simulating the real tagger):

| line | broad-text candidates | already tagged | missed |
|---|---|---|---|
| targeting_pods | 13 | 1 | 12 |
| mws_eo | 13 | 0 | 13 |
| lorop_pods | 12 | 0 | 12 |
| eo_air_defense_warning | 14 | 0 | 14 |
| ball_gimbals_16in | 14 | 0 | 14 |
| border_long_range_eo | 9 | 0 | 9 |

Inspecting the "missed" rows by hand: the overwhelming majority (54/74 raw hits, all six lines) are
**competitor-name-only** hits (Elbit/Rafael/Hensoldt/Rheinmetall/Anduril mentioned in an unrelated
story -- e.g. "Elbit Systems beats analysts, backlog reaches new peak", "Rafael-VW talks continue
despite Qatari opposition") that a human analyst would *not* tag to a specific product line either
-- these confirm the tagger's rule 5 (competitor-alone-insufficient) is working as intended, not a
recall bug to fix. Excluding those, real keyword/exemplar misses a human probably would tag are
sparse in this 63-row window because the corpus itself is small and genuine hardware-specific
copy is rare at this level/date scope -- 5 representative samples a human would tag but the
deterministic tagger (pre-broadening) missed:

- `targeting_pods`: id 90 "Updated 13.30" -- false positive in the raw scan (stub/placeholder
  title, not a real miss; excluded from the real gap list below).
- `mws_eo`: id 183 "Rheinmetall and HENSOLDT demonstrate successful integration of passive sensor
  technology into a modern air defence system" -- passive-sensor self-protection context, no
  `missile warning`/`MWS`/`DIRCM` keyword text present verbatim, so pre-broadening this is a
  genuine subdomain-classification gap (item's own `subdomain` was not `airborne_pods.mws`/
  `eo_warfare`), not a keyword-set gap -- out of this round's scope (classify.py, not owned here).
- `eo_air_defense_warning`: id 305 "RAF boosts counter-drone defence with Saab's Giraffe 1X radars"
  -- radar, not EO/IR; correctly *not* a real miss (raw-scan false positive via a stray
  `air defense` substring hit inside a competitor pattern).
- `ball_gimbals_16in` / `border_long_range_eo`: no genuine non-competitor keyword misses found in
  this 63-row window at all -- every raw hit for these two lines in the table above is
  competitor-name-only.

**Conclusion driving step 2/3 below**: this 63-row, level-gated, 90-day window is too small and too
clean (little raw exemplar-system copy) for keyword broadening alone to move the needle much on its
own -- most of the "hundreds of in-scope EO/IR items" the brief refers to live outside this strict
`level IN (red,orange,yellow)` gate (502 `items` total in the DB; 127 have `domain NOT IN
('out_of_scope')` regardless of level; 386 are `level = 'archive'`). Both steps are pursued as
briefed regardless: (2) broadens the keyword/alias/exemplar/competitor sets per-line so future
in-scope items get real recall, and (3) adds LLM-assisted tagging as the fallback for items where a
human would tag a line but no deterministic signal exists in the text (e.g. the `mws_eo` id-183 case
above, or any paraphrase that never uses one of the fixed keyword strings).

#### Step 2 -- broadened deterministic tagger (config/product_lines.yaml)

Every line's `keywords_he`/`keywords_en`/`aliases`/`exemplar_systems` broadened per the brief's own
per-line term lists (all six lines touched; see the file's own `# R8-tagging` header comment and
per-line inline notes for the reasoning behind each addition). Two additions needed real logic, not
just data, so `ProductLineDef` grew a new `conditional_keywords_en` field (parsed in
`eoa.product_lines.registry`, matched by a new rule 6 in `eoa.product_lines.tagging` --
`_conditional_match`/`_term_present`): a bare term only counts when it AND at least one of its own
`context` terms are both present, script-agnostic (Hebrew substring or Latin word-boundary, either
side):
- `mws_eo`: "DIRCM" alone names a jam/dazzle emitter, not a warning sensor -- gated on
  self-protection/missile-warning context (English or Hebrew).
- `eo_air_defense_warning`: "EO tracker" / "electro-optical tracking" are generic enough to
  appear in unrelated EO contexts -- gated on air-defense/C-UAS context.

Deterministic-only re-run (`--apply`, no `--llm`, full 502-row DB, no date/level filter -- this
script has never filtered by date, matching its own pre-existing convention):

| line | items (before -> after) | events | tenders | forecasts | patents |
|---|---|---|---|---|---|
| targeting_pods | 2 -> 2 | 0 -> 1 | 0 -> 2 | 0 -> 2 | 0 -> 0 |
| mws_eo | 0 -> 0 | 0 -> 0 | 0 -> 0 | 0 -> 0 | 2 -> 5 |
| lorop_pods | 0 -> 0 | 0 -> 0 | 0 -> 0 | 0 -> 0 | 0 -> 0 |
| eo_air_defense_warning | 0 -> 0 | 0 -> 0 | 0 -> 0 | 0 -> 2 | 0 -> 0 |
| ball_gimbals_16in | 0 -> 0 | 0 -> 0 | 0 -> 0 | 0 -> 0 | 0 -> 0 |
| border_long_range_eo | 0 -> 0 | 0 -> 0 | 0 -> 0 | 0 -> 0 | 0 -> 0 |

Confirms step 1's own conclusion: broadening keywords alone moved `mws_eo` patents (2 -> 5),
added `eo_air_defense_warning` forecasts (0 -> 2) and `targeting_pods` events/tenders, but added
zero new `items` tags -- this 63-row in-scope window genuinely has very little raw keyword/
exemplar-system copy left to catch once the competitor-alone false leads are excluded (step 1's own
finding). `lorop_pods`/`ball_gimbals_16in`/`border_long_range_eo` items still at 0 after step 2 --
exactly the gap step 3's LLM-assisted fallback exists for.

#### Step 3 -- LLM-assisted tagging fallback

New module `eoa.product_lines.llm_tagging` (`llm_tag_batch`, `LLM_TAG_MIN_CONFIDENCE = 0.6`,
`BATCH_SIZE = 15`) + new schema `eoa.llm.schemas.product_line.ProductLineTagResult`
(`{line_ids, confidence}`, closed-catalog validation happens in `llm_tagging`, not the schema, so
the schema module stays a leaf) + new prompt `agent/eoa/llm/prompts/product_line_tagging.md`. One
`chat_structured_batch` call per ~15-item chunk, `role="light"` (cloud mode -> `agy`/
`gemini-3.8-flash-medium` per `config/config.yaml`'s `light` chain), only for in-scope items
(`level IN (red,orange,yellow)`, `security_status='clean'`, `dedup_of IS NULL`, last 90 days) the
deterministic pass left untagged. Gated by the new `config/product_lines.yaml` top-level
`llm_tagging: true` key (`eoa.product_lines.registry.llm_tagging_enabled()`), wired into both:

- `eoa.pipeline.analyze`'s post-tagging hook -- one item at a time, only when the deterministic
  pass found nothing for that item; logs `method="llm"` vs `"deterministic"` on the
  `product_lines_tagged` log line (no DB column for a method marker exists on `items`, so this is
  the "else log" branch the brief allows for).
- `scripts/backfill_product_lines.py --llm [--llm-budget 40] [--llm-since-days 90]` -- real
  batches of ~15, results merged into the same `by_item_id` map the deterministic items sweep
  built so events/tenders (which inherit an item's tags) see LLM-assisted tags too.

Live run (`EOA_PIPELINE=1`, `role=light`, real `agy`/Gemini Flash calls -- 5 calls used, well
under the 40-call budget, over the 61 in-scope items still untagged after step 2):
```
2026-09-07 11:26:24 [warning] llm_schema_invalid  attempt=0 error="... EOF while parsing a value ..."
2026-09-07 11:27:54 [info]    product_lines_llm_tagged  n_items=15 n_no_result=0 n_tagged=0
2026-09-07 11:29:20 [info]    product_lines_llm_tagged  n_items=15 n_no_result=0 n_tagged=2
2026-09-07 11:30:58 [info]    product_lines_llm_tagged  n_items=15 n_no_result=0 n_tagged=0
2026-09-07 11:32:03 [info]    product_lines_llm_tagged  n_items=15 n_no_result=0 n_tagged=0
2026-09-07 11:32:56 [info]    product_lines_llm_tagged  n_items=2  n_no_result=0 n_tagged=0
```
One batch's first attempt returned an empty/invalid JSON body from the CLI provider --
`chat_structured`'s own existing one-retry-with-error-message handled it transparently (the batch
still returned a valid result on the next line); `llm_tag_batch` itself never raised. 4 items ended
up newly tagged this run (LLM output is not deterministic call-to-call; a dry run immediately before
this one, on the same candidate set, tagged 3 different items in a different distribution --
expected variance from a live model, not a defect): `ball_gimbals_16in` (+1 item), `lorop_pods`
(+1 item), `mws_eo` (+1 item id, on top of its already-tagged patents), plus the events inheritance
that follows from those newly-tagged items. `border_long_range_eo` and (this run)
`eo_air_defense_warning` items got no LLM tag -- consistent with step 1's read that this specific
63-row window has essentially no genuine border-surveillance copy at all.

#### Step 4 -- final backfill counts (deterministic + LLM, `--apply`, verified from a separate
connection) and live `GET /api/product-lines` stats

DB state after both `--apply` runs (`SELECT unnest(product_lines), count(*) ... GROUP BY 1`, run
from a fresh connection, not the same one the backfill script used):

| table | targeting_pods | mws_eo | lorop_pods | eo_air_defense_warning | ball_gimbals_16in | border_long_range_eo |
|---|---|---|---|---|---|---|
| items | 2 | 1 | 1 | 0 | 1 | 0 |
| events | 1 | 0 | 1 | 0 | 1 | 0 |
| tenders | 2 | 0 | 0 | 0 | 0 | 0 |
| tender_forecasts | 2 | 0 | 0 | 2 | 0 | 0 |
| patents | 0 | 5 | 0 | 0 | 0 | 0 |

Before this round: only `items`(targeting_pods)=2 and `patents`(mws_eo)=2, everything else 0 across
every table/line (matching the brief's own starting description). After: 4 of 6 lines now have at
least one `items` tag (targeting_pods/mws_eo/lorop_pods/ball_gimbals_16in), `mws_eo` patents
2 -> 5, `eo_air_defense_warning` gained forecasts (0 -> 2). `border_long_range_eo` remains at 0
everywhere -- both the deterministic broadening and the LLM fallback agree there is no genuine
border-surveillance content in the current 63-row in-scope window; this is a corpus-size/ingestion
finding, not a tagger defect (see step 1's own broader-corpus numbers: 502 items total, only 63
pass the strict `level IN (red,orange,yellow)` 90-day gate).

Live `GET /api/product-lines` (`http://127.0.0.1:8765/api/product-lines`, the already-running local
API process) `stats` field per line, rebuilt after the backfill:

| line | items_7d | items_30d | events_30d | open_tenders | forecasts | patents_90d | active_competitors |
|---|---|---|---|---|---|---|---|
| targeting_pods | 2 | 2 | 1 | 0 | 2 | 0 | 1 |
| mws_eo | 1 | 1 | 0 | 0 | 0 | 5 | 0 |
| lorop_pods | 1 | 1 | 1 | 0 | 0 | 0 | 1 |
| eo_air_defense_warning | 0 | 0 | 0 | 0 | 2 | 0 | 0 |
| ball_gimbals_16in | 1 | 1 | 1 | 0 | 0 | 0 | 1 |
| border_long_range_eo | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

#### Tests / lint

- `tests/unit/test_product_lines_round8.py` -- new, 34 cases: `ConditionalKeyword` parsing +
  defaults (registry), `llm_tagging_enabled()` (true/false/absent-key), rule-6 conditional-match
  (term alone, context alone, both, Hebrew-side context, word-boundary still enforced, combines
  correctly with other rules), `ProductLineTagResult` schema (defaults, dedup, blank-stripping,
  confidence bounds), `eoa.product_lines.llm_tagging.llm_tag_batch` (empty input, accept/reject by
  confidence threshold incl. the exact-0.6 boundary, invalid line-id filtering, item silently
  absent from the LLM response, exception degrades to `{}`, items without `id` excluded, no
  configured product lines short-circuits without calling the LLM, multi-item batch), catalog/
  prompt helper text. All mocked -- no DB, no LLM, no network.
- `PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_product_lines_round8.py tests/unit/test_product_lines.py -q -p no:cacheprovider` -- 102 passed (34 new +
  68 pre-existing, zero regressions).
- `.venv/Scripts/ruff.exe check` / `format --check` on every touched Python file -- clean.

#### What's left (for the user)

- `border_long_range_eo` has zero tagged rows end-to-end after this round's work -- not a code
  defect (both the broadened deterministic tagger and the LLM fallback independently agree), but
  worth revisiting once more border-surveillance-relevant items are actually ingested, or by
  widening the LLM pass's own scope (`--llm-since-days`/dropping the strict level gate) if the
  operator wants to backfill against the full 502-row/127-domain-scoped corpus rather than just the
  63-row `level IN (red,orange,yellow)` window this round measured against.
- `eo_air_defense_warning`/`border_long_range_eo` items are 0/0 even though `eo_air_defense_warning`
  picked up `forecasts=2` -- tender-forecast text (`platform`/`payload_need`) apparently mentions
  this line's vocabulary even where the news-item corpus doesn't; worth a follow-up look at why the
  underlying tender-forecast rows have that content but no linked/matching item does.
  `mws_eo`'s id-183 case from step 1 (subdomain-classification gap, not a keyword gap) is a
  `classify.py` fix, out of this package's owned-files scope.
- The LLM-assisted pass is stochastic by nature (see step 3's dry-run-vs-apply-run item-set
  difference) -- re-running `--llm --apply` later will likely pick up a slightly different subset
  of the same untagged candidates each time, which is expected, not a bug, given
  `LLM_TAG_MIN_CONFIDENCE = 0.6` deliberately accepts borderline-confidence guesses.

### R8-chat-b status

Package scope: `agent/eoa/api/routes/ask.py`, `agent/eoa/api/ask_grounding.py`,
`agent/eoa/api/services.py` (ask message-building/sources-footer helpers only),
`agent/eoa/llm/prompts/ask_answer_format.md`, `tests/unit/test_ask_round8.py`, plus the `ask:` keys
in `config/config.yaml`. Fixes `docs/qa/loop/round_7_judge_b.md`'s D5 findings #1/#2 (score 80) and
verifies the round-7 entailment check's actual live behaviour.

#### 1. Leaked internal delimiter (D5 finding #2)

`===SOURCES_JSON===\n"}]}` reached the end of `answer_final.text` in 2 of 8 sampled answers (Q4/
DROIC, Q6/AUSA). Root-caused by reading the code, not just the symptom: `_run_citation_repair`
(`routes/ask.py`) is a one-shot, non-streamed corrective rewrite fired whenever the streamed answer
has zero `[n]` citations -- it reuses the exact same system+sources messages the original answer
saw, which carry the same `ask_answer_format.md` instructions telling the model to always append
the sentinel+JSON tail, but that rewritten text (`corrected`) never passed through the streaming
loop's own sentinel-split logic at all, so a leaked tail sailed straight into
`answer_text`/`answer_final` unfiltered. This is almost certainly the actual mechanism behind both
of the round-7b judge's 2 live samples (Q4/Q6 are exactly the kind of answer -- unusual/niche
subject -- most likely to have started with zero `[n]` on the first pass and gone through repair).

Fix, three layers (per the round-8 brief: "a single robust split ... before any guard runs and
before the `answer_final` event", "keep the streaming path's early split as an optimisation only",
"a last-resort sanitizer"):

1. `_SOURCES_SENTINEL_RE` / `_split_sources_json` (new, `routes/ask.py`) -- a single tolerant regex
   match (optional markdown code fence, optional stray `#` heading marker, any `=` count/
   whitespace around `SOURCES_JSON`), applied to the fully-assembled `answer_text` right after the
   streaming loop ends (before every grounding guard) *and* to `_run_citation_repair`'s own
   `corrected` text (the actual live root cause) before it is ever assigned to `answer_text`.
2. The streaming loop's own exact-literal `_SOURCES_SENTINEL` match is unchanged -- still the
   low-latency common-case path, now demoted to "an optimisation" rather than the only defence.
3. `_strip_residual_sources_block` (new) -- a last-resort sanitizer run unconditionally,
   immediately before `answer_final` is yielded.

Also strengthened `ask_answer_format.md` itself: an explicit instruction that the sentinel must be
emitted exactly as specified (no heading marker, no code fence, exact `=` count) -- a
prompt-side mitigation on top of the code-side fix, not a replacement for it.

#### 2. Numeric slip (D5 finding #1, "seven" -> "eight")

Item 257's own text says the company plans to deliver "seven" additional prototypes; the answer
said "eight" -- both single, independently-plausible small integers naming the same citation, so
none of the existing entity/money/year grounding checks ever fired (a lone 1-digit number is
explicitly auto-passed by `_digits_grounded`'s own `< 2`-digit floor, and that pass-through is
*unchanged* -- this is a new, separate, narrower guard, not a tightening of the old one; see
`test_single_digit_non_money_still_auto_passes_the_older_grounding_guard` in the new test file).

New in `ask_grounding.py`:

- `_normalize_spelled_numbers` -- maps every spelled-out cardinal number word (English "one" ..
  "twenty", Hebrew "אחד" .. "עשרים", both grammatical genders, two-word teens) to its digit form,
  so a source's "seven"/"שבעה" and a claim's "8" are compared on equal footing.
- `filter_claim_count_mismatch` -- for every `[n]`-cited unit, finds a claimed digit count and the
  noun phrase it quantifies, and checks that same noun phrase against its own citation (source
  spelled numbers normalised first). An **exact** noun-phrase match with a different count gets the
  digit corrected in place (kept, not dropped); a **fuzzy**-only match (e.g. "prototype" vs.
  "prototypes") gets the whole unit dropped instead, per the brief's "prefer removal ... unless the
  noun phrase match is exact, then correct". No comparable count anywhere in the citation is left
  alone (unverifiable, not contradicted -- same precision-first stance as every other guard in this
  module).

Wired into the main `if citations:` guard chain (right after `filter_claim_grounding`) and into
`_run_removal_guards` (the repair/demotion re-apply helper), so a count mismatch introduced or
reintroduced by a citation-repair rewrite or an anchor-demotion is still caught.

Also added a numeric-accuracy rule to `ask_answer_format.md` (copy quantities from the source
exactly, never round/guess) as a prompt-side mitigation alongside the guard.

#### 3. Entailment check live-log audit (round-7 `ask.entailment_check`)

Grepped `runtime/logs/api.2026-09-07.log` for `ask.entailment_check_*` event names only (never
dumped the log): 9 `POST /api/ask` requests that day, 5 `ask.entailment_check_skipped
reason=timeout_or_error` lines, **0** `ask.entailment_check_removed` lines ever. That is a 100%
skip rate among every attempt that had an in-scope candidate at all -- zero observed successes.

```
2026-09-07 09:08:48 [info] ask.entailment_check_skipped claims=6 reason=timeout_or_error
2026-09-07 09:11:06 [info] ask.entailment_check_skipped claims=4 reason=timeout_or_error
2026-09-07 09:13:41 [info] ask.entailment_check_skipped claims=5 reason=timeout_or_error
2026-09-07 09:15:44 [info] ask.entailment_check_skipped claims=3 reason=timeout_or_error
2026-09-07 09:17:02 [info] ask.entailment_check_skipped claims=6 reason=timeout_or_error
```

Root cause (found by reading `entailment_filter`'s own `_call` closure, not guessed): the
`chat_structured("light", ...)` call never passed `interactive=True`, so every attempt queued
behind the resource gate's patient *batch* budget (`queue_timeout_min`, minutes) instead of the
short interactive one -- the function's own 20s outer wall-clock (`_run_with_timeout`) was
essentially guaranteed to expire first regardless of how fast the light model itself would have
answered once actually admitted. This is the same "batch queue vs. interactive budget" gap
`routes.ask`'s own P1 fix (2026-09-06, `_MAX_ANSWER_SECONDS`/`gate_busy` handling) already closed
for the main chat generation -- it was just never applied to this later addition.

Fix, per this round's own brief ("raise its budget to 30s and cap claims to 4"):

- `entailment_filter`'s `_call` now passes `interactive=True` -- the actual root-cause fix.
- `entailment_filter`'s `timeout_s` default raised `20.0 -> 30.0`.
- Claims-per-call capped at 4 via a new `_ENTAILMENT_MAX_CLAIMS_CAP = 4` constant in `routes/ask.py`,
  applied at the call site (`min(ask_cfg.entailment_max_claims, _ENTAILMENT_MAX_CLAIMS_CAP)`) --
  **not** by lowering `config/config.yaml`'s `ask.entailment_max_claims` value itself (left at 6),
  because that raw value is asserted `== 6` by `tests/unit/test_ask_round7.py`'s
  `TestAskConfig.test_default_config_values`, a shared-suite test this package does not own and may
  not edit. `config/config.yaml`'s `ask:` block comment documents this decision explicitly (why the
  number itself didn't move even though the effective cap did).

This audit could not distinguish an actual >20s wall-clock timeout from an instant provider/HTTP
error under the same `reason=timeout_or_error` bucket (both funnel through the same
`except Exception: return None` in `_run_with_timeout`) -- the `interactive=True` fix is correct
either way (a non-interactive call queuing behind a patient batch budget explains both a slow
admit-then-succeed case and, if the "light" role's provider itself is misconfigured for batch mode,
an immediate error too), but the live stack was never restarted to re-verify against real traffic
(this package's own standing rule: the lead restarts, not sub-packages) -- see "What's left" below.

#### Tests / lint

- `tests/unit/test_ask_round8.py` -- new, 33 cases: `_split_sources_json`/
  `_strip_residual_sources_block` unit tests (single-chunk whole-text, the exact live-found
  no-valid-JSON leak shape, code-fence-wrapped, `=`-count/`#`-prefix variant, no-delimiter no-op,
  offline replay of both live-captured Q4/Q6 leak tails), `_normalize_spelled_numbers` (English
  1-20, Hebrew bare units, non-number text no-op), `filter_claim_count_mismatch` (the live
  "seven"->"8" repro in English and in Hebrew, fuzzy-match removal, matching-count no-op,
  unverifiable-count no-op, uncited no-op, the 1-digit-auto-pass regression guard), the
  entailment timeout/interactive/cap fixes (default-value introspection, mocked-call kwarg
  assertion, config-value-vs-effective-cap design-decision guard), and 6 end-to-end
  `TestClient`-through-the-real-route cases (whole-response-single-chunk leak, delimiter split
  across two streamed chunks, the citation-repair leak reproduced end-to-end, no-delimiter
  regression, count-mismatch corrected end-to-end, entailment call capped at 4 even with the
  config value at 6). All mocked (`chat_stream`/`chat`/`chat_structured`/`db.get_pool`) -- no DB,
  no network, no live LLM call; at most 4 live `POST /api/ask` probes were budgeted for this
  package and none were needed since the offline replay + TestClient coverage above reproduced
  and verified the fix directly against the live-captured leak text.
- `PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_ask_round8.py
  tests/unit/test_ask_round7.py tests/unit/test_ask_round6_grounding.py tests/unit/test_ask_round5.py
  tests/unit/test_ask_round3_grounding.py tests/unit/test_ask_round2_chat_fixes.py
  tests/unit/test_ask_sse_sources.py -q -p no:cacheprovider` -- **217 passed** (184 pre-existing +
  33 new, zero regressions).
- `.venv/Scripts/ruff.exe check` / `format --check` on every touched Python file -- clean.

#### What's left (for the user)

- The live stack is still running pre-round-8 code (this package's own standing rule: the lead
  restarts, sub-packages verify offline). Once restarted, worth a quick live re-check of
  `ask.entailment_check_removed`/`ask.entailment_check_skipped` counts against real traffic to
  confirm the `interactive=True` fix actually clears the 100% skip rate found above, not just that
  the unit-level wiring is correct.
- The `entailment_check_skipped reason=timeout_or_error` log bucket does not distinguish a real
  timeout from an instant error -- if skips persist after the restart, splitting that reason string
  in `entailment_filter`'s own log call (`ask_grounding.py`, owned by this package) into e.g.
  `timeout` vs. `error` would make the next round's diagnosis faster; not done this round since it
  wasn't necessary to reach or verify the fix itself.
- `filter_claim_count_mismatch`'s noun-phrase matching is deliberately narrow (a single word token,
  optionally fuzzy on a >= 4-char prefix relationship) -- it does not resolve a Hebrew number word
  glued to a `ו`/`ה`/`ב`/`ל` conjunction/prefix letter (e.g. "ושמונה עשר" written as one token) the
  way `ask_grounding._strip_hebrew_head_prefix` does for institution names elsewhere in this module;
  out of scope for the two required test cases ("seven" -> "8", "שבעה" -> "8") but worth folding in
  if a future live sample shows a prefixed spelled-out number slipping past normalisation.

### R8-investigations-b status

Scope: `agent/eoa/search/deep_search.py`, `agent/eoa/report/daily.py` (`collect_deep_search` /
`reconcile_deep_search_reruns` and helpers only), `agent/eoa/report/weekly.py` (deep-search section
wiring only), `tests/unit/test_deep_search_round8.py` (new). Fixes `docs/qa/loop/round_7_judge_b.md`
D4's three remaining defects (score 45): the report-selection gap, the live zero-page-read
repro on job 145, and the two citation-integrity spot-check failures (jobs 146/147).

#### 1. Reports don't surface fixed investigations (finding #1)

Live-reproduced directly against the DB (`DATABASE_URL` from `runtime/eoa.env`, read-only) before
touching any code: calling `collect_deep_search`/`_filter_deep_search_to_items_included` for the
current weekly window returned **both** job 148 (item 1352, `partial`/0.5, real content,
`rerun_of_job_id=140`) **and** job 86 (item 1352, `off_topic`, the stale answer the round-7b judge
found in the rendered report) as two separate surviving entries -- `reconcile_deep_search_reruns`
never merged them. Root cause, read directly off the two jobs' payloads: job 86's `question` is job
148's `question` with an appended `" בהקשר: <asker's own context commentary>"` clause (a pattern
several enqueue paths use to carry the analyst's own framing alongside the core question); the old
grouping key compared the raw question text verbatim, so the two runs landed in different groups.
(job 145/146/147/148 themselves -- the reruns of jobs 137/138/139/140 -- were *not* independently
broken by this: 146/147 group and reconcile correctly today because their reruns' question text is
byte-identical to the original; only the item-1352 pair had a divergent question.) Separately,
`collect_deep_search` never even extracted `rerun_of_job_id`/`expanded_from_job_id` from the job
payload into its entries, so lineage-based grouping wasn't possible at all regardless of question
text.

Fix, two independent, composable mechanisms in `reconcile_deep_search_reruns`:

1. `_normalize_question_for_grouping` strips a trailing `" בהקשר: ..."` clause (and casefolds/
   collapses whitespace, as before) before comparing questions.
2. `collect_deep_search` now also extracts `rerun_of_job_id`/`expanded_from_job_id` from each job's
   payload into the entry dict; `reconcile_deep_search_reruns` unions a job's group with the group
   of the job it points back to (via a small union-find over the entries' original indices),
   independent of whether the question text matches at all -- catching a rerun that meaningfully
   reworded the question, not just one with an appended clause.

Both mechanisms compose transitively (a chain of question-match + lineage-match links still ends up
in one group) and neither changes the existing tie-break (best `_OUTCOME_RANK`, ties -> newest by
original `finished_at DESC` position) or the existing `rerun_count`/`rerun_note_he` annotation
behavior -- verified against the two pre-existing tests in `test_deep_search_reconcile_round4.py`
(unchanged, still pass).

**Live verification** (read-only, same DB, after the fix): re-running the identical
`collect_deep_search`/`_filter_deep_search_to_items_included` call for the current weekly window now
returns job 148 alone for item 1352 (`rerun_count=3`, folding in job 86 and one other same-question
run), and jobs 145/146/147/130 unchanged (each already correctly reconciled with their own
same-question prior runs). Total deep-search entries for the window dropped from 19 to 18, exactly
the one entry eliminated by the 86/148 merge.

#### 2. Zero-page-read -> blank not_found (finding #2)

Job 145 (item 10, `"Verify and expand: US Army launches laser production with $465M contract
award"` -- the exact question the round-7 fixes doc showcased as fixed via job 137) reproduces the
original bug on today's live code, confirmed by reading `investigation_log` directly: 16 rows across
3 full rounds, up to 8 hits per query, **zero** `read`/`fetch` rows, ending at the round-4 `final`
catch-all with `outcome=not_found`/`confidence=0.0`/`pages_read=0` and the generic
`"לא נמצא מידע מספק במסגרת התקציב."` message. Root cause read directly off the code (not just the
symptom): `MIN_PAGES_BEFORE_NOT_FOUND`'s rejection of a lazy `finish(not_found)` only fires while
`not budget.exhausted` -- once the query budget itself runs out (job 145 spent 15/15), that guard's
own `not budget.exhausted` clause goes false and the whole rejection is skipped; and
`_synthesize_from_reads` (round-7's fallback for "reads happened but no `finish()` call") is a
no-op by its own docstring/contract when `inv.read_summaries` is empty, which it always is when the
round loop *never once called* `read`. Neither of round 7's two safety nets assumes a loop that
searches without ever reading at all.

Fix: `_force_read_top_hits` (new), a last-resort safety net run once after the round loop ends, only
when `inv.result` is still unset, no page was read, hits exist, and the investigation wasn't
explicitly stopped by the user (`inv.stop_requested`). Ranks `inv.hits_seen` by the search
provider's own relevance `score` and force-reads the best remaining, not-yet-attempted hit(s) via
the same `_tool_read` the model itself would call (so a successful read is logged/summarised/
budget-charged identically either way), trying up to `_FORCE_READ_MAX_ATTEMPTS=3` hits or until one
succeeds or the page budget runs out. Wired into `investigate()` immediately before the existing
`_synthesize_from_reads` call, so a successful forced read flows straight into that same salvage
path instead of the blank not_found default -- an investigation with hits available now always ends
with at least one real read, or an honest account of why every candidate failed.

**Job 145 re-run**: queued (job 156, `rerun_of_job_id=145`, same item/question) via
`eoa.memory.relational.enqueue_job` per this round's DB rules. **Caveat, same as R8-chat-b's own
standing note above**: the live orchestrator is a long-running in-process worker (`claim_next_job`
in a persistent loop, not a per-job subprocess) that already had `deep_search.py` imported before
this fix was written to disk -- confirmed by watching job 156 start executing within seconds of being
queued, well before this file was even written. Its outcome (below) therefore reflects the
*pre-fix* code and cannot on its own demonstrate `_force_read_top_hits`; the fix is verified instead
by the 12 new mocked unit tests in `TestForceReadTopHits` (best-score selection, quarantine
fallback/retry, max-attempts cap, page-budget respect, no-op when reads/hits already
present-or-absent, and a full `investigate()`-level integration test asserting a forced read
prevents the blank not_found). A live re-check that job 145's *specific* symptom (zero reads despite
hits) no longer reproduces needs a restart, which is outside this package's authorization this round
(this package's own standing rule: the lead restarts).

Job 156 outcome: still `running` as of this writing, ~35 minutes after being queued (picked up
within seconds; 3 `search` rounds then 3 `fetch`/`read` rows logged, `pages_read=1` each -- unlike
job 145 it did actually call `read` this time, a reminder that the zero-read failure isn't 100%
deterministic run-to-run even under identical pre-fix code). No `finished_at`/`result` yet; likely
resource contention on this shared machine (this package's own test runs plus concurrent activity)
rather than anything code-related, since it is untouched pre-fix code either way. Left running --
not killed/restarted per this round's standing rules -- outcome to be checked by the user
separately from this report.

#### 3. Citation integrity (finding #3)

**(a) Uncited-quality gate.** Job 146 cited a Cloudflare bot-challenge interstitial
(`investigation_log`'s own fetch row logged `title='Just a moment...'` for its sole source) as
though it were the real article -- silently counted as `pages_read=1` and cited with no disclosure
(re-fetching the same URL independently now returns HTTP 403, confirming it was never real content).
`screen()` (the security guard) looks for prompt-injection signals, not "is this actually the
article" -- a clean-but-wrong page sails straight through. Fix: `_low_quality_page_reason` (new),
run in `_tool_read` immediately after fetch and *before* the (costlier) security guard -- rejects a
page whose title+body matches a known challenge/consent/paywall interstitial signature (Cloudflare's
"Just a moment"/"Enable JavaScript and cookies"/"Verify you are human" family, generic
"Access Denied"/403, JS-required/paywall/cookie-wall phrasing) or whose body is under 400 characters.
A rejected page is logged (`outcome=not_found`, `notes="discarded, low-quality page: ..."`) but never
reaches `screen()`, `_summarise_page`, `read_urls`, or `read_summaries` -- structurally uncitable, not
just discouraged.

**(b) Decision-verb / hedge cross-check.** Job 147 stated as settled fact, at confidence 0.9, "בחירת
נורקין על פני אבולעפיה" (Norkin chosen over Abulafia), citing a Globes article that (independently
re-fetched) actually describes an **unresolved** process ("expected to meet next week... to
determine who gets the role") -- job 147's own buried gaps section even admits "no final official
appointment was stated," directly contradicting its own confident headline sentence. Neither the
synthesis prompt (`agent/eoa/llm/prompts/deep_search_system.md`) nor `InvestigationOut`'s validation
(`agent/eoa/llm/schemas/analysis.py`) are in this package's file ownership this round, so this is a
deterministic, Python-level post-check instead: `_source_text_is_hedged` flags a fetched page whose
raw text itself carries a hedge marker ("צפוי"/"שוקל"/"טרם", "expected"/"considering"/"not yet"/
`\bmay\b`), recorded per-URL on the investigation (`inv.hedged_read_urls`) at read time.
`_downgrade_unhedged_decision_claims`, run once `inv.result` is set (covers both a normal `finish()`
call and the `_synthesize_from_reads` fallback) and before `_finalize_outcome`, collapses every
sentence in `answer_he` using a decision verb ("הוחלט"/"נבחר"/"זכה"/"נחתם") into one hedged
placeholder sentence whenever at least one of the sources actually read for this investigation was
itself flagged as hedged -- the original flagged sentence(s) are preserved verbatim in
`contradictions_he` so the reader still sees what was claimed and why it was downgraded, rather than
the claim silently vanishing.

#### Tests / lint

- `tests/unit/test_deep_search_round8.py` -- new, 36 cases: question-normalization grouping (the
  live job-86/job-148 repro plus order-independence, cross-item non-merging, and the normalize
  helper in isolation), lineage-based grouping via `rerun_of_job_id`/`expanded_from_job_id`
  (including a job pointed at that isn't in the entry set, and a 3-way transitive chain mixing both
  mechanisms), `collect_deep_search`'s payload extraction (mocked cursor/connection, no DB),
  `_force_read_top_hits` (best-score selection, quarantine-then-retry, max-attempts cap, remaining
  page-budget respect, no-op when already satisfied/no hits, and a full `investigate()`-level
  integration case), `_low_quality_page_reason` (Cloudflare interstitial, short body, a realistic
  article passing, Access Denied, cookie wall) plus a `_tool_read`-level case asserting the security
  guard is never even reached, and `_source_text_is_hedged`/`_downgrade_unhedged_decision_claims`
  (Hebrew/English markers, the job-147 repro end-to-end, no-op when nothing is flagged/cited/a
  decision verb is absent/the outcome isn't confident/`inv.result is None`, and multiple flagged
  sentences collapsing into one placeholder). All mocked (`eoa.fetch.remote.fetch_remote`,
  `eoa.security.guard.screen`, `_summarise_page`, `daily.connection`) -- no DB, no network, no live
  LLM call, matching this round's unit-test rule.
- One pre-existing fixture in `tests/unit/test_deep_search_round7.py`
  (`TestToolReadUsesRetryAndLogsQuarantine.test_successful_read_after_one_transient_failure`) used a
  14-character fetched-page stub, which the new < 400-char quality gate now correctly discards before
  it reaches the retry-path assertions the test is actually about -- the fixture's `text` value was
  padded to a realistic length (no other change) so it keeps exercising the transient-retry logic it
  was written for. Flagging this explicitly since it's a one-line edit to a file outside this
  package's normal ownership, made only because it was a direct, foreseeable consequence of shipping
  finding #3's own explicitly-specified 400-char threshold.
- `PYTHONPATH=agent PYTHONUTF8=1 EOA_SEARCH_NO_CACHE=1 .venv/Scripts/python.exe -m pytest
  tests/unit/test_deep_search_round8.py tests/unit/test_deep_search_round7.py
  tests/unit/test_deep_search_reconcile_round4.py -q -p no:cacheprovider` -- **75 passed** (39
  pre-existing + 36 new, zero regressions after the one fixture fix above).
- `PYTHONPATH=agent PYTHONUTF8=1 EOA_SEARCH_NO_CACHE=1 .venv/Scripts/python.exe -m pytest
  tests/unit/test_deep_search_round8.py tests/unit -q -p no:cacheprovider -k "deep_search or
  reconcile or rerun"` -- **222 passed**, 0 failed (186 pre-existing tests this filter matches
  across the whole `tests/unit` tree + 36 new), confirming no collateral effect anywhere outside
  the files this package touched.
- `.venv/Scripts/ruff.exe check` / `ruff.exe format --check` on every touched Python file -- clean.
- Daily rebuild (`build_daily(force=True)`) -- report id 105, `qa.passed=True`, no crash. The daily
  window (trailing 24h) doesn't happen to include items 10/44/81/1352 (published 09-03..09-05, only
  the weekly/monthly window covers them), so this run mainly verifies the changed code path executes
  cleanly end-to-end on live data; finding #1's actual fix was verified directly via
  `collect_deep_search`/`_filter_deep_search_to_items_included` against the live weekly window (see
  "Live verification" above), since that's the window the original finding was reported against.

#### What's left (for the user)

- Job 156 (item 10's re-run) was queued but will run under the pre-fix code until the orchestrator
  is restarted (see the caveat under finding #2) -- worth a quick live re-check of item 10's question
  after a restart to directly confirm `_force_read_top_hits` fires on the exact showcased question,
  not just the mocked unit coverage.
- `_low_quality_page_reason`'s signature list is a fixed, hand-picked set of English-language
  interstitial phrases; a non-English (e.g. Hebrew-language CDN/WAF) challenge page wouldn't match
  any of them and would fall through to the <400-char check alone -- fine for the two live-observed
  cases, but worth widening if a future round finds a non-English interstitial slipping through with
  a body over 400 chars.
- `_source_text_is_hedged`'s `\bmay\b` marker will also match the English month name "May" (e.g. a
  source dated "May 2026") -- an accepted false-positive per the brief's literal marker list rather
  than a bug; if this proves noisy in practice, gating it on proximity to a decision-topic noun
  phrase (candidate/appointment/מועמד/מינוי) rather than presence anywhere in the page would tighten
  it without dropping the marker the brief asked for.
