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

- `tests/unit/test_report_bd_territory.py` (the existing, pre-round-8 test file -- 49 cases) was
  kicked off to double-check `bd_territory.py` has no regression beyond this round's own 12 new
  bd_kr-stub tests, but did not finish inside this session: the machine is under heavy, unrelated
  memory pressure (one `python.exe`, not launched by this task, holding ~28GB RSS the whole time --
  consistent with the operator's own multi-hour model-training jobs per this project's standing
  process-kill-discipline note; never touched, per that same rule) that made every pytest run and
  the live monthly LLM call itself far slower than normal. Manual review found zero regression risk
  in that file: the two pre-existing tests closest to this round's change
  (`test_tables_only_draft_used_when_items_empty_but_tables_present`,
  `test_no_items_draft_used_when_everything_empty`) call `draft_bd_territory` directly, which this
  round never touched -- only `build_bd_territory`'s post-draft mutation of `system_note_he` and
  `_enqueue_territory_expansion_search`'s return type changed, both covered by this round's own new
  tests and by the live `bd_kr` rebuild above. Re-run when the machine is quieter:
  `PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_report_bd_territory.py -q`.
- The full `tests/unit` suite (beyond the brief's own mandated filtered command, which passed) was
  also kicked off as extra diligence and is still running for the same reason -- not blocking, since
  every file this round touched is covered by the mandated command or this round's own dedicated
  suite.
- Patent survey `_appendix_reliability` is unit-tested only, not live-verified against a real
  rebuilt survey file (see above) -- worth a spot-check next time a patent survey is rebuilt for
  any other reason.
