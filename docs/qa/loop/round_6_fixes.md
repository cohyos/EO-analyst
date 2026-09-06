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
