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
