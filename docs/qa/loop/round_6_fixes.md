### R6-data status

Scope: `docs/qa/loop/round_5_judge.md` D1/D2/D3/D9 -- the data defects the judge flagged for 3-4
rounds running (so_what template phrases, item 22's empty `entities_mentioned`, item 5604's
triage inconsistency, the "owl leak" analyze-stage scope gap, and the junk tender candidates) --
plus the pipeline gaps that produced them. Files touched: `scripts/repair_round6.py` (new),
`agent/eoa/memory/relational.py` (`get_items_for_stage`'s analyze-stage filter only),
`agent/eoa/pipeline/analyze.py` (new `repair_so_what_text` entry point only),
`agent/eoa/tenders/scan.py` (candidate dedupe only), `tests/unit/test_round6_data.py` (new, 31
cases, no DB). Ran against `127.0.0.1:5432/eoanalyst` (alembic head `0025`), `EOA_PIPELINE=1`
forced by the script so every LLM call used the cloud chain. Lint/format clean
(`ruff check`/`ruff format --check`).

#### 1. so_what template phrases (D2)

**Pipeline entry point:** `agent/eoa/pipeline/analyze.repair_so_what_text` -- a new function
alongside the existing `_repair_generic_so_what` (which only fires during a *fresh* analyze pass
and gates on its own narrower `_GENERIC_SO_WHAT_RES`, which does not cover every phrase in
`eoa.report.qa_citations.SO_WHAT_TEMPLATE_PHRASES_HE`, e.g. "מהווה צעד נוסף"/"מהווה צעד חשוב").
`repair_so_what_text` shares the exact LLM-call shape but is driven by an *already-persisted*
`so_what_he` + the caller-matched banned phrase.

**Repair script (`repair_round6.py so_what`):** queried the DB directly against
`SO_WHAT_TEMPLATE_PHRASES_HE` (not just the judge's original 16 ids) and found **26** items:
9, 13, 15, 26, 51, 70, 82, 89, 105, 112, 120, 126, 134, 143, 149, 157, 180, 235, 271, 313, 317,
747, 1011, 1132, 1353, 1493. Each candidate rewrite is validated before being written: no banned
phrase left, and 1-3 Hebrew sentences (`eoa.report.qa_citations.split_sentences`).

**Applied 2026-09-07 00:19-00:23** (shared with tasks 2/3 under one 25-call LLM budget, per this
round's brief): **14 repaired** (13, 15, 51, 70, 82, 105, 112, 120, 134, 143, 157, 180, 271, 317),
**7 rejected** for producing 4 sentences instead of the required 1-3 (9, 26, 89, 126, 149, 235,
313 -- old text left in place, model consistently overshoots the sentence cap on this prompt),
**5 skipped** once the shared 25-call budget ran out (747, 1011, 1132, 1353, 1493).

**Verified from a separate connection:** re-running the same `SO_WHAT_TEMPLATE_PHRASES_HE` scan
finds **12** items still banned -- exactly the 7 rejected + 5 budget-skipped ids above, confirming
every one of the 14 "repaired" ids is clean.

**Left for a follow-up run:** `python scripts/repair_round6.py so_what --apply --llm-budget 12`
covers the remaining 12. The 7 sentence-count rejections suggest `_SO_WHAT_REPAIR_INSTRUCTION_HE`'s
"1-3 משפטים" instruction is being undershot by the model on this prompt fairly often (7/21
attempts, i.e. 1/3) -- worth a stricter re-ask-on-4-sentences retry in a future round rather than a
silent reject, but that would mean editing `_SO_WHAT_REPAIR_INSTRUCTION_HE`/`analyze.py` beyond
this round's "so_what entry point only" ownership.

#### 2. item 22 entities_mentioned (D3)

**In-scope sweep (level in red/orange/yellow, domain != out_of_scope, empty
`entities_mentioned`):** only **2** other items, 153 and 290 -- well under the 30-item cap. Full
target list: 22 (mandatory), 153, 290.

**Repair path:** `eoa.pipeline.analyze.analyze_item` + `persist_analysis` (the pipeline's own
per-item entry point) on the cloud chain. **Finding:** re-running full analysis alone was
*insufficient* for all three items -- `persist_analysis` only ever writes `entities_mentioned` via
the deterministic watchlist-alias backfill (`_backfill_entities_from_watchlist`), which fires only
for a *watchlist* company/program name; none of 22/153/290 have one in their text, so all three
came back `still_empty_after_reanalysis` even though the fresh analyze pass legitimately extracted
real entities into `events.parties` (item 22: a new graph edge AIM-120 AMRAAM
INTEGRATES_WITH NASAMS; items 153/290 already had `events.parties` from *earlier* analyze passes
that had never been unioned back into `items.entities_mentioned` at all -- e.g. item 153's events
already named TC-Next/GraphCast/WeatherNext/Pangu-Weather/IFS HRES).

**Added a second, deterministic fallback in the repair script only** (`_entities_from_events`,
`scripts/repair_round6.py` -- not a pipeline change, since this round's `analyze.py` ownership is
scoped to the so_what entry point only): unions every `events.parties` name plus every
`graph_edges` src/dst entity name already stamped with the item's id, and backfills
`items.entities_mentioned` from that when the reanalysis pass alone leaves it empty. Applied
directly (no extra LLM calls -- pure DB read/write) for all three items.

**Verified from a separate connection:**
- item 22: `['AIM-120 AMRAAM', 'NASAMS', 'Ukraine', 'F-16s', 'Western Partners', 'Russia']`
- item 153: `['TC-Next', 'GraphCast', 'WeatherNext', 'Pangu-Weather', 'IFS HRES']`
- item 290: `['UK Ministry of Defence (MoD)', 'UK Defence Innovation (UKDI)', 'UK Ministry of Defence']`

**Pipeline gap flagged for a future round (not fixed here, out of this round's file-ownership):**
`items.entities_mentioned` has no mechanism at all that backfills it from an item's own
`events.parties`/`graph_edges` once classify.py's extraction and the watchlist-alias backfill both
come up empty -- items 153/290 prove this can sit wrong for a long time even when the item's own
events already name real parties. A general fix belongs in `agent/eoa/pipeline/analyze.py`'s
`persist_analysis` (or `classify.py`), reviewed on its own.

#### 3. item 5604 triage inconsistency (D1)

**Before:** score=6, level='archive', reason stated core_relevance=5/magnitude=3/novelty=3 (sum=11
-> table score should be 7, not 6) and `level_for(6)` is 'orange' per `config/config.yaml`
thresholds (`red:8, orange:6, yellow:4`) -- 'archive' was wrong on two independent counts.

**Repair path:** `eoa.pipeline.triage.triage_item` (the per-item entry point) on the cloud chain,
`validate_triage_consistency` before persisting.

**After (applied 2026-09-07 00:19):** score=1, level='archive', reason "RFI... ללא פירוט טכנולוגי
או מפרט מערכת EO/IR ספציפית... core_relevance=1, magnitude=1, novelty=1" (sum=3 -> table score 1;
`level_for(1)` = 'archive'). The model's fresh, independent read of the item scored it much lower
than the original triage -- expected, since re-triage is not a "fix the numbers, keep the verdict"
operation.

**Verified from a separate connection:** `_reason_conflicting_level` returns `None` and
`level_for(score) == level` -- fully consistent.

#### 4. analyze-stage scope gap / stray events (D9)

**Code fix:** `agent/eoa/memory/relational.get_items_for_stage` now applies
`AND domain IS DISTINCT FROM 'out_of_scope' AND level IS DISTINCT FROM 'archive'` for
`stage == "analyze"` only (classify/triage still see every item -- domain/level aren't set yet at
those stages). Unit test asserts the filter appears for `"analyze"` and is absent for
`"classify"`/`"triage"`.

**Data repair:** found **51** events (event 257/item 2463, the "owl leak", plus 50 more) belonging
to items with `domain='out_of_scope'` or `level='archive'`. Deleted under `--apply`. The same run's
own entities repair (task 2) transiently created 2 more such events on item 22 (level='archive')
before the "events" subcommand ran and deleted them in the same pass -- final `found_count`/
`deleted_count` was **53**.

**Verified from a separate connection:** 0 events remain matching
`domain='out_of_scope' OR level='archive'`.

#### 5. junk tender candidates (D9)

**Code fix:** `agent/eoa/tenders/scan.py` adds a second dedupe layer
(`_normalize_tender_title`/`_notice_portal`/`_candidate_duplicate_exists`), applied before the LLM
classification call: a 'candidate'-intake tender is skipped when a 'candidate' row with the same
normalized title *and* URL host ("portal") already exists. Root cause: `external_ref` is built as
`"<source_id>:<url>"`, so the *same* URL surfaced by different per-country `search`-kind sources
(us_defense_innovation_search, pl_search, jp_search, gcc_search, nz_search, ...) produced a
different `external_ref` each time and slipped past the existing exact-ref dedupe
(`_tender_exists`). Never touches 'accepted'/'archived' rows.

**Data repair:** found 5 duplicates in the live 13-row `tenders` table: the same Northrop Grumman
EO/IR marketing page 5x (ids 36/37/39/40, keeping 35) and the same "Unmanned Airspace" Counter-UAS
category listing 2x (id 41, keeping 38). Deleted under `--apply`; `tender_feedback` and every
'accepted'/'archived' row untouched.

**Verified from a separate connection:** `tenders` table now has **8** rows (13 - 5); the 5
'accepted'/'archived' rows (ids 13/15/18/20/30) and the one non-duplicate candidate (id 34,
tenderned.nl) are all still present and unchanged; 0 duplicate groups remain.

#### Tests / lint

`PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_round6_data.py -q`
-> 31 passed (no DB; fake cursor/connection per `test_relational_stage_filter.py`'s pattern).
`ruff check`/`ruff format --check` clean on every touched file.

#### What's left

- so_what: 12 items still banned (7 sentence-count rejects + 5 budget-skipped, both listed above)
  -- re-run `so_what --apply` with additional budget.
- The entities_mentioned <- events/graph_edges backfill is currently only in the repair script; if
  this class of bug recurs it belongs in the pipeline itself (`persist_analysis`), which is outside
  this round's `analyze.py` (so_what entry point only) ownership.

### R6-patents status

Scope: `docs/qa/loop/round_5_judge.md` D8 findings 1-3 (0%/17% assignee coverage on the keyless
Google-Patents-search fallback -> no assignee profiles / no CPC x assignee matrix; the literal
"לא מסווג" prefix still surviving into a term-derived cluster label; keeping the "מטריצת אשכול x
מקצה" heading and ensuring the matrix renders whenever there's a known assignee + cluster). Files
touched: `agent/eoa/patents/scan.py`, `agent/eoa/patents/survey.py`, `agent/eoa/patents/cluster.py`
(all three owned this round), `tests/unit/test_patents_round6.py` (new, 33 cases, no DB/network --
every DB call and HTTP fetch mocked). `agent/eoa/llm/prompts/patent_survey.md` needed no change --
`_cluster_data_lines` already forwards `cluster.label_he` to the LLM verbatim, so the finding-2
label fix flows through the prompt for free. Lint/format clean (`ruff check`/`ruff format --check`
on every touched file).

#### 1. assignee/CPC/priority-date enrichment (D8 finding 1)

**Root cause confirmed:** `eoa.patents.scan._google_patents_records` (the keyless
`site:patents.google.com <query>` search fallback used whenever `EPO_OPS_KEY`/
`PATENTSVIEW_API_KEY` aren't configured, which is this dev machine) only ever has a search hit's
title+snippet to work with -- no assignee/CPC/date fields exist in a Google search result at all,
so `assignees`/`cpc` land empty for most gathered records.

**Fix:** `eoa.patents.scan.enrich_stored_patents_missing_assignee(patent_ids, cap=25, sleep_s=2.0)`
-- for up to 25 of a survey's own patents (fresh-this-run *and* supplemented-from-store alike,
called once from `eoa.patents.survey.build_patent_survey` right after both are merged and before
`_fetch_patent_rows`) still carrying no assignee in the `patents` table, fetches that patent's own
**individual** Google Patents detail page (`patents.google.com/patent/<pub>/en` -- a real page keyed
by publication number, not a search result) via `eoa.fetch.remote.fetch_raw_remote` (the project's
shared SSRF-guarded HTTP client -- never a raw `requests`/`httpx` call), 2 seconds apart, and parses
its structured `<meta name="DC.contributor" ... scheme="assignee">` / `<span itemprop="Code">` +
`<meta itemprop="IsCPC">` / `<time itemprop="priorityDate">` markup. `_backfill_patent_fields`
(existing, non-destructive `COALESCE`-based) now also backfills `priority_date` (previously
assignees/cpc only). CPC codes are normalized to this project's own class+subclass+main-group
convention (`"G01S13/00"` -> `"G01S13"`, matching `config/patents.yaml`'s `"G01J5"` shape).

**Design/verification fetches (live, <= 12 per this round's brief):** 5 real
`patents.google.com/patent/<pub>/en` pages fetched against real `pub_number`s pulled read-only from
this project's own `patents` table (`US10795010B2`, `WO2017087031A1`, `US8212996`,
`US20230082239A1`) -- confirmed the exact markup shape (assignee vs. inventor distinguished only by
the `scheme` attribute; CPC hierarchy lists every level as its own `<li>`, only `IsCPC=true` ones
count) before writing the parser, then re-verified the parser's own output against the same fetched
HTML.

**Live rebuild verification (`EOA_PIPELINE=1`, DROIC survey, 2026-09-07 02:54, report_id=74,
10 patents):**
- Methodology box: `כיסוי נתוני מקצה: 100% (10/10)` and `כיסוי קודי CPC: 100% (10/10)` -- up from
  the round-5-reported 0% on this exact topic before this fix.
- `## מטריצת CPC x מקצה (White Space)` and `## מטריצת אשכול x מקצה` both render with fully
  populated data rows (real assignees: Raytheon Co, Sensors Unlimited Inc, Sabanci Universitesi,
  Massachusetts Institute of Technology, Black Forest Engineering LLC).
- `eoa.qa.d8_patent_survey.score_D8(md, html)` -> **100.0** (all 10 checks pass, including
  `coverage_tag_present`, `cpc_assignee_matrix_present`, `no_unclassified_cluster_when_patents_exist`,
  `no_bogus_assignee`, `patent_numbers_ltr_isolated` 10/10).
- Every one of this run's 10 patents got a real CPC code from the enrichment pass, so this
  particular rebuild never actually populated the "unclassified" bucket -- finding 2's fix is
  covered live end-to-end by the D8 checker's own `no_unclassified_cluster_when_patents_exist`
  check (vacuously true here, genuinely exercised by this round's new unit tests below) rather than
  by this one live run.

#### 2. "לא מסווג" cluster-label fix (D8 finding 2)

**Root cause:** `eoa.patents.cluster._unclassified_label_he` already sub-clustered the leftover
"unclassified" bucket by TF-IDF top terms (round 5), but still prefixed every labelled sub-cluster
with the literal `UNCLASSIFIED_LABEL_HE` ("לא מסווג: <terms>") -- and, when the whole bucket
collapsed into exactly one sub-cluster (`multi=False`), used the *bare* literal with no terms at
all even when real terms existed. `eoa.qa.d8_patent_survey._no_unclassified_cluster_check` matches
that literal substring anywhere in an "אשכול"-heading section's heading+body, so either shape still
tripped the checker.

**Fix:** `_unclassified_label_he` now always builds `"אשכול נושאי: <term1> / <term2> / ..."` when
there is at least one real top term (regardless of whether the bucket split into one sub-cluster or
several -- the `multi` gate on which branch got a descriptive label at all is removed), and falls
back to the bare `UNCLASSIFIED_LABEL_HE` only when a sub-cluster's patents carry no title/abstract
tokens whatsoever (nothing honest to name it by -- this one case is unchanged and still covered by
the pre-existing `test_unclassified_bucket_for_no_signal`/round-6's own
`test_truly_nameless_single_row_still_falls_back_to_bare_label`). `methodology_box_lines_he` gained
a `term_derived_cluster_count` parameter -- the survey now discloses, in its own "שיטה והיקף" box,
how many technology clusters are TF-IDF term-derived rather than CPC/taxonomy-mapped, whenever that
count is non-zero.

**Known conflict with an existing test (not fixed, out of this round's file ownership):**
`tests/unit/test_patents_round5.py::TestClusterPatentsUnclassifiedSubclustering::
test_multiple_subclusters_get_distinct_labelled_keys` hard-codes the *old* buggy shape it asserts
`c.label_he.startswith(f"{UNCLASSIFIED_LABEL_HE}:")` (i.e. starts with `"לא מסווג:"`) for every
sub-cluster -- exactly the literal this finding requires removing. That assertion now fails
(confirmed: `1 failed, 349 passed` when running `-k "patent or survey"` across the whole `tests/unit`
tree). `test_patents_round5.py` is outside this round's file ownership (only `survey.py`/`scan.py`/
`cluster.py`/`patent_survey.md`/a *new* `test_patents_round6.py` are), so it was left as-is rather
than edited -- **needs**: update that one assertion (line 348) to expect the new `"אשכול נושאי:"`
prefix instead of `f"{UNCLASSIFIED_LABEL_HE}:"`; `test_patents_round6.py`'s own
`TestClusterPatentsUnclassifiedLabellingRound6` re-implements the same 4-row thermal/battery
fixture under the corrected expectation for reference.

#### 3. matrix heading / build condition (D8 finding 3)

**Verified, no code change needed:** the "מטריצת אשכול x מקצה" table (exact heading preserved,
matches the checker's `"מטריצ"` substring match) already only requires a non-empty top-assignees
list and a non-empty `clusters` list (`if matrix_assignees and clusters:`) -- not a "both top-5 axes
non-empty" gate the way the *other* CPC x assignee white-space matrix uses (that one genuinely needs
both a CPC axis and an assignee axis to mean anything, and already degrades gracefully to an empty
list when either is). The round-5-reported "no CPC x assignee matrix" symptom was entirely a
downstream consequence of finding 1's 0% assignee coverage (`top_assignees` was empty, so
`matrix_assignees` was empty too) -- fixed by finding 1's enrichment pass, confirmed by the same
live rebuild above (`cpc_assignee_matrix_present` check passes, both matrix sections render with
real data).

#### Tests / lint

`PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_patents_round6.py
tests/unit -q -p no:cacheprovider -k "patent or survey"` -> **349 passed, 1 failed** (the known
`test_patents_round5.py` conflict above; every other patent/survey test, including this round's own
33 new cases, passes). `ruff check`/`ruff format --check` clean on `scan.py`/`survey.py`/
`cluster.py`/`test_patents_round6.py`.

#### What's left

- `test_patents_round5.py` line 348 needs updating to the new `"אשכול נושאי:"` label prefix (see
  finding 2 above) -- outside this round's file ownership.
- This round's live verification never actually exercised a populated "unclassified" bucket (every
  DROIC patent got enriched to a real CPC this time) -- covered by unit tests instead
  (`TestClusterPatentsUnclassifiedLabellingRound6`), but a future round's live rebuild on a topic
  with genuinely CPC-less patents (e.g. one where the enrichment fetch itself comes back empty)
  would be a good end-to-end confirmation.
- The enrichment pass is capped at 25 patents/survey and only fires for patents still missing an
  assignee at survey time -- the routine watch-topic `scan_patents()` path (not the on-demand
  survey) still never enriches, by design (this round scoped the fix to the on-demand survey per
  the brief); the same `enrich_stored_patents_missing_assignee` function could be wired into the
  routine scan too in a future round if that gap turns out to matter.

### R6-entities status

Follow-up to R6-data (task 2's `entities_mentioned` backfill and D9's "owl leak" events cleanup
above) -- the round-5 judge D1/D3 finding + the live `monthly_2026-09-30.md` "ישויות חדשות החודש"
symptom: entities mentioned *only* by out-of-scope/archived items pollute both the `entities`
table and the monthly's new-entities list (e.g. entity 1208 "Western Burrowing Owl", kind=company;
'Rees Training Center'; 'Global Owl Project'; 'F-16s'; 'Western Partners'). Files touched:
`scripts/repair_round6.py` (new `entities_cleanup` subcommand), `agent/eoa/pipeline/analyze.py`
(entity persistence only: `persist_analysis`'s entities_mentioned/entities/graph_edges writes,
plus its new junk-name-shape helper), `agent/eoa/memory/relational.py` (`upsert_entity`'s junk
gate only), `tests/unit/test_round6_entities.py` (new, 33 cases, no DB). Ran against
`127.0.0.1:5432/eoanalyst` (alembic head `0025`). `entities_cleanup` is pure SQL (no LLM calls).
Lint/format clean (`ruff check`/`ruff format --check`) on every touched file.

#### 1. `entities_cleanup` subcommand (D1/D3)

**Query** (`find_out_of_scope_only_entities`): confirmed `items.entities_mentioned` is `TEXT[]`
live, then re-ran the brief's query DB-wide (not capped at the original 151): **160** entities
currently mentioned by at least one item but never by any item outside
`domain='out_of_scope'`/`level='archive'` (the count moved from the brief's 151 because items have
kept flowing through the pipeline since that finding was written, and R6-data's own item-22
entities backfill in the prior round added 6 more names -- AIM-120 AMRAAM/NASAMS/Ukraine/Russia/
F-16s/Western Partners -- that immediately qualified, since item 22 is `level='archive'` even
though its `domain='air_defense'`).

**Protection rules** (never deleted, per the brief's three exemptions), implemented in
`_protection_reason`:
- (a) `_watchlist_protected_names()`: normalized name/alias set from `config/watchlist.yaml`
  (`companies` name+aliases+strict_aliases, `programs` name+aliases, `agencies` name+aliases,
  `acquisition_watch` name+`peers_of`) and `config/payloads_seed.yaml`'s
  `payloads[].vendor_entity_name`.
- (b) `kind in (company, org, system, program)` with `country` set AND at least one in-scope
  mention -- implemented and unit-tested (`test_protection_reason_never_fires_for_country_rule_
  without_in_scope_mention`), but verified live/by construction that it can **never** actually
  protect a row `find_out_of_scope_only_entities` returns: that query's own `WHERE` clause already
  requires zero in-scope mentions, so `has_in_scope_mention` is `False` for every candidate row.
  Kept exactly as specified, as a documented safety net against a future loosening of that
  population query.
- (c) `referenced_by_report_state`: every non-null `reports.report_state` cast to text and
  substring/word-boundary-matched against each candidate name -- `report_state`'s own
  `item_ids`/`item_levels`/`indicator_ids` fields are never entity names, only
  `trend_titles[].title_he` free text can name one (e.g. "מגמה: פעילות מוגברת סביב Hezbollah
  בתחום out_of_scope").

**Dry run** (first 40 rows in `candidates_preview`, full counts): **160** candidates, **8**
protected, **152** to delete.

| id | name | reason |
|---|---|---|
| 13 | Safran | watchlist (companies) |
| 20 | Aselsan | watchlist (companies) |
| 28 | Hanwha | watchlist (companies) |
| 31 | HAL | watchlist (companies) |
| 138 | Marine Corps | referenced by report_state (trend_titles "US Marine Corps") |
| 893 | Iran | referenced by report_state |
| 1232 | Hezbollah | referenced by report_state |
| 1296 | Mossad | referenced by report_state |

**Applied 2026-09-07**: `entities_cleanup --apply` deleted **152** entities. Dependent rows,
discovered live via `information_schema` (`_entity_fk_columns`) rather than hardcoded -- confirmed
the only FK-to-`entities.id` columns in the live schema are `graph_edges.src_entity_id`/
`dst_entity_id` (both already `ON DELETE CASCADE` per migration `0006_drop_extensions.py`; deleted
explicitly anyway for an accurate touched-row count): **23** `graph_edges` rows via `dst_entity_id`
+ **25** via `src_entity_id` = 48 total. `patents.entity_ids` (`BIGINT[]`, a soft reference found
by inspecting every migration for a column naming entities by id, not a declared FK) had **0**
rows needing an update this run (no patent currently references any of the 152 deleted ids). All
three steps ran inside one transaction.

**Verified from a separate connection:** `entities` table: 518 -> 366 rows. Re-running the same
population query finds exactly **8** remaining out-of-scope-only entities -- the 8 protected ones
above, untouched and still present. Every one of the "known junk" names from the original finding
and R6-data's item-22 backfill is now gone: `F-16s`, `Western Partners`, `AIM-120 AMRAAM`,
`NASAMS`, `Ukraine`, `Russia` (all mentioned only by archived item 22) plus e.g. `Airbus`/
`Honeywell` all confirmed absent. `graph_edges`: 347 -> 299 rows.

**Follow-up finding (not fixed here, out of this round's specific query):** entity 1208 "Western
Burrowing Owl" -- the finding's own headline example -- is no longer in the 160-row population at
all, because the item that named it (2463, the "owl leak") no longer mentions it in
`entities_mentioned` (0 items currently do; the row is a true orphan, mentioned by *zero* items,
not just zero in-scope ones). `find_out_of_scope_only_entities`'s `EXISTS (...)` clause requires at
least one mentioning item, so a fully-orphaned entity falls outside this round's query by
construction. Confirmed via `find_junk_shaped_entities` (task 3 below), which flags it DB-wide with
`in_out_of_scope_population: false` -- per the brief's "do not delete them unless they are in the
151 set" instruction, it was correctly left untouched. A future round should add a fourth
subcommand (or extend this one) for "entity mentioned by zero items at all" as its own, distinct
population.

#### 2. Pipeline fix: entity persistence skipped for out-of-scope/archived items (D3/D9)

**`_entity_persistence_allowed(item)`** (`analyze.py`): `False` when `item.domain ==
'out_of_scope'` or `item.level == 'archive'` -- mirrors `relational._ANALYZE_STAGE_SCOPE_FILTER`'s
own `IS DISTINCT FROM`-safe condition (a missing/unset domain or level, i.e. not yet classified/
triaged, is never blocked). `get_items_for_stage`'s analyze-stage filter (R6-data) already keeps
such items out of the normal `run_analyze` sweep, but a *direct* `analyze_item`/`persist_analysis`
call bypasses that filter entirely -- which is exactly how item 22 (domain='air_defense' but
level='archive') got AIM-120 AMRAAM/NASAMS/F-16s/Western Partners/Ukraine/Russia written into
`entities_mentioned` by R6-data's own targeted repair. `persist_analysis` now checks this guard and
skips only entity persistence -- `items.entities_mentioned` (both the watchlist backfill and the
new events-fallback below) and the `entities`/`graph_edges` upserts in the edges block -- while
`summary_he`/`so_what_he`/`key_facts`/`uncertainty_he`/`tech_*`/events are written exactly as
before (unit-tested: `test_persist_analysis_skips_entities_mentioned_write_for_archived_item`).
Deliberately narrow scope (per this round's "entity persistence only" file ownership): the
pre-existing A13 israel-relevance re-scoring block (`score_and_persist_entity_israeli`, which also
writes to `entities`) was left untouched -- it re-scores entities already on record rather than
extracting new ones, a different concern from the pollution this task targets.

**Events/graph_edges fallback moved into the pipeline** (`_entities_from_persisted_events`): when
neither the LLM's own extraction nor `_backfill_entities_from_watchlist` populates
`entities_mentioned` for an in-scope item, `persist_analysis` now falls back to the party names
from its own just-inserted `events` and the endpoint names from its own just-written `graph_edges`
-- the exact mechanism R6-data's `scripts/repair_round6.py._entities_from_events` implemented as a
one-off, script-only fallback (discovered live on items 153/290), now generalized into the
pipeline itself for every future analyze pass, in-memory (no extra DB round trip) rather than
re-querying. Every candidate name is passed through the new junk-shape filter (task 3) before
being written, so a bare "F-16s"/"Western Partners" surfacing in an event's own `parties` list
never backfills the field (unit-tested:
`test_persist_analysis_backfills_entities_from_events_when_still_empty`,
`test_persist_analysis_events_fallback_filters_junk_names`,
`test_persist_analysis_events_fallback_skipped_when_entities_already_present`,
`test_persist_analysis_events_fallback_skipped_for_out_of_scope_item`).

#### 3. Junk-entity guard (D9)

**`is_junk_candidate_entity_name(name, kind=None)`** (`analyze.py`), mirrored as a small local
duplicate `_is_junk_shaped_entity_name` in `relational.py` (not imported -- this codebase's own
convention for a cross-module-boundary helper, e.g. `analyze._event_dedup_key` mirroring
`report.daily._normalize_event_key`; `entity_normalize.py`, home of the broader
`is_junk_entity`, is not owned by this round's file list). Three conservative, deterministic
checks: (1) a bare pluralised platform/weapon designation, `^[A-Z]{1,3}-?\d{1,3}[A-Za-z]?s$` (e.g.
"F-16s", "M1s", "AK47s" -- "F-16" itself, no trailing "s", is untouched); (2) an exact
case-insensitive match against a 5-entry stoplist of generic "who talked" phrases (Western
Partners, Local Partners, Industry Partners, Defense Officials, Government Officials); (3) a
wildlife/nature word (owl, eagle, habitat, wildlife, conservation) present in a name typed
`kind == 'company'` only (the same word under `program`/`org`/no kind is left alone -- a real
conservation program is not junk). Wired in at two persistence points: `relational.upsert_entity`
(rejects before any DB call, alongside the existing `is_junk_entity` gate -- returns `None`,
matching that function's existing "junk -> no id" contract) and `analyze.persist_analysis`'s new
events-fallback (task 2).

**Dry-run visibility** (`find_junk_shaped_entities`, task 1's `entities_cleanup` output): scanned
every entity DB-wide (not just the out-of-scope-only population) for a match -- **3** existing
rows flagged: `F-16s` (id 1196) and `Western Partners` (id 1198), both also in the 160-row
out-of-scope-only population and therefore deleted by task 1's apply above; `Western Burrowing Owl`
(id 1208) flagged but `in_out_of_scope_population: false` (see task 1's follow-up finding above) --
correctly **not** deleted, per the brief's "do not delete them unless they are in the 151 set"
instruction. The filter is informational-only inside `entities_cleanup`; it never triggers a
deletion on its own.

#### Tests / lint

`PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_round6_entities.py
tests/unit/test_round6_data.py -q -p no:cacheprovider` -> **64 passed** (33 new, no DB; fake
cursor/connection per `test_round6_data.py`'s and `test_upsert_entity_normalization.py`'s
patterns). Also re-ran the full existing suite touching these modules for regressions
(`test_persist_analysis.py`, `test_upsert_entity_normalization.py`, `test_relational_stage_filter.py`,
`test_entity_normalize.py`, `test_entity_canonical_write_regression.py`,
`test_persist_classification_entities.py`, `test_analyze_key_facts_entities.py`,
`test_events_dedup.py`, `test_graph_edges.py`, plus the two round-6 files above): **273 passed, 0
failed**. `ruff check`/`ruff format --check` clean on `scripts/repair_round6.py`,
`agent/eoa/pipeline/analyze.py`, `agent/eoa/memory/relational.py`,
`tests/unit/test_round6_entities.py`.

#### What's left

- The fully-orphaned "Western Burrowing Owl" (entity 1208, mentioned by zero items at all, not
  just zero in-scope ones) needs a follow-up subcommand/query -- see task 1's follow-up finding.
- Task 1(b)'s "kind+country+in-scope-mention" protection rule is implemented and tested but
  structurally never fires against `find_out_of_scope_only_entities`'s current population (see
  above) -- worth revisiting only if that population query is ever widened.
- The A13 israel-relevance re-scoring block in `persist_analysis` still runs
  `score_and_persist_entity_israeli` against an out-of-scope/archived item's *pre-existing*
  `entities_mentioned` (a deliberate scoping decision, not a bug -- see task 2 above); if that
  turns out to matter it belongs in a future round scoped to `israel_focus.py`/that block
  specifically, not this round's "entity persistence only" ownership.
- `entities_cleanup` is not yet wired into any scheduled/routine job -- it is a manual, on-demand
  repair subcommand like `events`/`tenders`, run the same way (`--apply` after reviewing the dry
  run).

### R6-weekly status

Scope: `docs/REPORT_TEMPLATE_BENCHMARK.md` sec 3.2 (weekly budget ~14 H2, the D6
`heading_count_within_budget` check allows 16) -- the live weekly/monthly had drifted to 33/36 H2
headings (one per trend, one per domain section, one per Israel/tech/patents/BD table or prose
block). Files touched: `agent/eoa/report/docx_builder.py` (section-level rendering only),
`agent/eoa/report/weekly.py`, `agent/eoa/report/monthly.py`, `tests/unit/test_weekly_round6.py`
(new, 26 cases, no DB/LLM). `agent/eoa/llm/prompts/report_weekly.md`/`report_monthly.md` needed no
change -- the draft schema's section list is unchanged, only how `docx_builder` renders the
already-existing `extra_sections`/`tables`/`draft.sections` data was restructured.

#### Grouping mechanism (docx_builder.py)

**Fix:** an optional `group_he` key on any `extra_sections` or `tables` dict: every entry sharing
the same non-empty `group_he` now collapses into one `##`/Heading-1 parent (the `group_he` text)
with each member rendered one level down (`###`/Heading-2, `docx`'s `Heading 2` style via a new
`_heading2` helper) -- in markdown, html, *and* docx consistently (`_group_entries`/`_group_title`,
new pure helpers; `_extra_sections_md/_html`, `_tables_md/_html`, and `build_docx`'s own
extra-sections/tables loops all rewritten to use them). A member whose own `title_he` equals the
group's `group_he` (the Israel report's pre-existing merged table) renders directly under the
parent with no `###` of its own -- everyone else in the group gets one. A `tables`-list entry may
also now be a *prose* member (`body_he`, no `headers`/`rows`), so a previously-`extra_sections` item
(patents/IP writeup, acquisition watch, the monthly watchlist) can join a group whose other members
are real tables, without leaving the `tables` list (which would have skipped
`dedupe_rows_across_tables`'s cross-table row de-duplication and the per-row `related_trend_he`
note). Two more additive, default-off hooks: `domain_group_he` (wraps `draft.sections` -- the
per-domain narrative sections -- under one parent) and `open_points_in_outlook` (nests "נקודות
פתוחות" as a child of "מבט קדימה" instead of its own top-level heading, right after the outlook
text). Every hook defaults to `None`/`False`, so the **daily report call sites are byte-for-byte
unaffected** (`eoa.report.daily` never passes any of them).

**Wiring (weekly.py/monthly.py):** trend sections -> "מגמות השבוע"/"מגמות החודש"; domain sections ->
"סקירה לפי תחום"; the Israel merged table + per-company summary table -> "תעשייה ישראלית" (the
per-company table's title had its redundant "תעשייה ישראלית — " prefix stripped in `weekly.py`
after receiving it from `israel_section.py`, since keeping it would give the D6
`israel_single_table_with_type_column` check two headings matching the keyword again -- the exact
failure mode this round fixes); tech-radar + developments-to-watch + patents/IP (now a `tables`-list
prose member instead of an `extra_sections` entry) + new-patents table -> "טכנולוגיה ו-IP";
acquisition-watch prose (also moved into `tables`) + the 90-day conference calendar -> "פיתוח עסקי".
Monthly: the players-map table (one per domain), top-10-events table, 24-month conference horizon,
and watchlist-changes prose (moved into `tables`) all group under "נוף השוק החודשי"; the patents
landscape prose + table group under "טכנולוגיה ו-IP". Both reports also get `domain_group_he` and
`open_points_in_outlook=True`.

#### Live verification

`set -a; . runtime/eoa.env; set +a; PYTHONPATH=agent PYTHONUTF8=1 EOA_PIPELINE=1
.venv/Scripts/python.exe -c "from eoa.report.weekly import build_weekly; r=build_weekly();
print(r.md, r.qa.passed)"` -> `output/reports/weekly_2026-09-07.md True` (qa_passed, report_id=76,
cloud chain per `EOA_PIPELINE=1`, ~12 min). **H2 count: 15** (`grep -c "^## "` on the rendered
markdown), down from 33, matching the brief's target structure exactly: שורה תחתונה, תקציר מנהלים,
מה השתנה מאז הדוח הקודם, מגמות השבוע, סקירה לפי תחום, טבלת אירועים עסקיים, חקירות עומק, מבט קדימה,
הנחות והפרכות, מעקב אינדיקטורים, סיכום מטא שבועי, פיתוח עסקי, טכנולוגיה ו-IP, תעשייה ישראלית, נספח
מקורות. The docx sibling shows 16 Heading-1 paragraphs (the same 15 plus the TOC page's own "תוכן
עניינים" heading, which `_planned_headings` never counts) and 40 Heading-2 children, confirming
every group actually nested its members (8 trends under "מגמות השבוע", 6 domains under "סקירה לפי
תחום", "לוח 90 הימים הקרובים" under "פיתוח עסקי", "רדאר טכנולוגי"/"התפתחויות שכדאי לעקוב"/"פטנטים
ו-IP"/"פטנטים חדשים" under "טכנולוגיה ו-IP", "סיכום שבועי לפי חברה" under "תעשייה ישראלית" with no
duplicate "תעשייה ישראלית" child heading).

`score_D6` on that file (`run_link_check=False`): **87.2/100**, one failing check --
`every_factual_exec_summary_sentence_cited` (one uncited sentence in the model-drafted executive
summary, an `eoa.report.qa_citations`/prompt content matter, unrelated to this round's heading
restructuring and outside this round's scope). `heading_count_within_budget` (15 <= 16) and
`israel_single_table_with_type_column` (the exact D6 check this round's Israel-grouping fix
targets) both **pass**, along with `bluf_present_and_short`, `what_changed_section_present`,
`indicator_watchlist_table_present`, `outlook_likelihood_and_confidence_separated`,
`exec_summary_no_filler_phrases`, `no_duplicate_sentences`, `no_row_repeated_across_tables`.

#### Tests / lint

`PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_weekly_round6.py
tests/unit/test_report_weekly_monthly.py tests/unit/test_docx_builder.py
tests/unit/test_renderer_round5.py tests/unit/test_bluf_round5.py -q -p no:cacheprovider` ->
**26 new + 133 existing = 159 passed, 2 failed** (both in `test_report_weekly_monthly.py`, see
"Known conflict" below). `ruff check`/`ruff format --check` clean on every touched file.

**Known conflict with existing tests (not fixed, out of this round's file ownership):**
`tests/unit/test_report_weekly_monthly.py::test_build_weekly_renders_docx_with_trend_section_and_calendar_table`
asserts `"לוח 90 הימים הקרובים"` and the trend's own title are top-level (`Heading 1`) headings, and
`::test_build_monthly_renders_players_map_table_per_domain` asserts a `"נוף תחרותי"`-prefixed
top-level heading -- exactly the three heading classes this round intentionally demotes to
`Heading 2` children (of "פיתוח עסקי"/"מגמות השבוע"/"נוף השוק החודשי" respectively). Confirmed: `2
failed, 18 passed` running that file alone; every other test in it (citation counts, table cell
values, QA pass/persist, month-range math, etc. -- the actual *content* those two tests also check)
is unaffected. `test_report_weekly_monthly.py` is outside this round's file ownership (only
`weekly.py`/`monthly.py`/`docx_builder.py` section-level rendering/`report_weekly.md`/
`report_monthly.md`/a *new* `test_weekly_round6.py` are) -- **needs**: update those two
assertions' `heading_texts` checks to look for the new group headings ("פיתוח עסקי"/"מגמות
השבוע"/"נוף השוק החודשי") instead of the now-nested child headings, and/or additionally assert the
child appears as a `Heading 2` under that parent (`tests/unit/test_weekly_round6.py`'s own grouping
tests demonstrate the exact pattern).

#### What's left

- The two `test_report_weekly_monthly.py` assertions above need updating by whoever owns that file
  (see "Known conflict").
- `every_factual_exec_summary_sentence_cited` failed on this round's live rebuild -- a
  `qa_citations`/prompt content issue (one uncited sentence slipped past the citation gate into the
  exec summary), unrelated to and outside the scope of this round's heading-grouping work.
- The monthly report's own live rebuild (`build_monthly()`) was not separately run this round (the
  brief scoped the single live build to weekly); its grouping is exercised by
  `test_weekly_round6.py`'s synthetic-draft tests and the unchanged `test_report_weekly_monthly.py`
  monthly QA/persist tests (`test_build_monthly_qa_passes_and_persists` passes), but a live
  `EOA_PIPELINE=1` monthly rebuild would be a good end-to-end confirmation in a future round.
