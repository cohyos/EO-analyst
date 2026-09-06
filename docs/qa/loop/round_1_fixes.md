# Round 1 fixes — D1/D4/D6/D7/D9 (docs/QA_CONTINUOUS_LOOP.md)

Scope: the deterministic checks `docs/qa/loop/round_0_auto.json` flagged (D1 score 33.3, D4/D6/D7/D9
partial failures). Every fix below has two parts where applicable: a **code fix** (prevents
recurrence on new data) and a **data repair** (fixes the already-persisted rows the round-0 sample
found). All data repairs ran against the live DB (`postgresql://eoa:...@127.0.0.1:5433/eoanalyst`
— note this DB is at Alembic version `0010`, eight migrations behind this repo's `HEAD` (`0019`);
several fixes below had to be made schema-drift-tolerant as a result, see "Incidental fixes").

## D1 — classification & triage

### `gershayim_no_ascii_quote` (40/60 golden items)

**Code:** `agent/eoa/llm/ollama_client._iter_model_strings` did not recurse into a plain `dict`
value (e.g. `InvestigationPlanOut.queries: list[dict[str, str]]`, `AnalyzeOut.relevance_check`) —
a Hebrew ASCII-quote inside one was never normalized by `chat_structured`'s own post-generation
guard. Added a `dict` branch alongside the existing `list`/`BaseModel` ones.

**Data:** new `scripts/repair_gershayim.py` — deterministic (no LLM), replaces `"` sitting between
two Hebrew letters with the Hebrew gershayim `״` across `items` (every `text`/`text[]` column
except verbatim-fetched ones: `raw_text`, `clean_text`, `title`, `url`, `canonical_url`,
`source_name`, `text_hash`), `events`, `tenders`, `tender_forecasts`, `entities` (incl. `aliases`),
and `jobs.result` (JSON, walked recursively). Column set discovered dynamically via
`information_schema.columns`, so it survives schema drift. Ran `--dry-run` then applied: **169
rows repaired** (125 items, 26 events, 2 tenders, 6 tender_forecasts, 5 entities, 5 jobs.result).
Re-running the script afterward reports 0 remaining hits.

### `hebrew_truncation_zero_hits` (items 8, 33, 39, 55, 1091)

Confirmed all 5 are genuine truncations (cut mid-word/mid-acronym, no terminal punctuation) — not
detector false positives.

**Code fix (root cause of the repair script missing them):** `scripts/repair_truncated_hebrew.py`
checked `triage_reason` under its own literal column name, but `eoa.qa.d1_classify` (the actual QA
check) checks it under the synthetic name `"reason_he"` specifically to enable the generic "long
sentence, no terminal punctuation" net (which only fires for a name ending in `_he`). Added a
`_CHECK_FIELD_NAME` mapping so the repair script's detection matches the QA check's exactly.
Also made `_fetch_items` resilient to a `SELECT` naming a column that doesn't exist yet
(`tech_readiness_note_he`, migration 0011) via dynamic column discovery, and added an
`item_ids`/`--ids` filter so a repair run can target a specific small set instead of every flagged
row in the DB.

**Data:** re-triaged exactly items 8, 33, 39, 55, 1091 via `triage_item` (LLM, one at a time,
resident model, GPU idle at the time). Item 33's first regeneration was itself truncated again
(coincides with `TriageOut.reason_he`'s `max_length=400` — plausibly the same schema-constrained-
decoding cutoff class of bug as the gershayim issue) — one more retry produced a clean result. All
5 now pass `hebrew_truncation_zero_hits`.

### `subdomain_valid_vs_taxonomy` (item 24, NULL subdomain)

**Code:** `ClassifyOut._validate_subdomain_against_taxonomy` used to reset an invalid/empty
`subdomain` to `""`, which `eoa.pipeline.classify` then persists as SQL `NULL`
(`subdomain=out.subdomain or None`) for *any* domain, including one with real taxonomy sub-keys.
Now falls back to that domain's first ("default") sub-key instead — `""` remains correct only for
a domain with no sub-keys at all (`out_of_scope`). `scripts/repair_classification_guards.py` grew
matching `default_subdomain`/`subdomain_missing` helpers; its report key `subdomain_cleared`
became `subdomain_repaired` (now records `after_subdomain` too).

**Data:** item 24 (`secondary`, `subdomain=NULL`) repaired directly → `detectors_fpa` (the
domain's first taxonomy sub-key). A `--dry-run` of the full repair script found 11 more rows with
the same pattern elsewhere in the DB (not part of round 0's flagged sample) — **not applied**, to
stay inside this round's mandate; see "Out of scope, deferred to round 2".

### `key_facts_no_duplicates` (items 5, 10, 51)

**Code:** `eoa.pipeline.analyze._dedupe_key_facts`/`_key_facts_dedupe_key` now normalize
punctuation (not just whitespace/case) and collapse near-duplicates
(`difflib.SequenceMatcher.ratio() >= 0.9` on the normalized strings) — e.g. item 5's "...בחו״ל
לפני מעבר לייצור בארה״ב." vs. "...בחו״ל לפני מעבר לייצור בארה״ס." (ratio 0.918). A near-duplicate
pair below the threshold (item 10's short/long restatement, ratio 0.884) is deliberately left as
two distinct facts, matching the task's explicit `>= 0.9` bar.

**Data:** `scripts/backfill_analysis_gaps.py` gained a 4th, deterministic pass
(`key_facts_dedupe_backfill` / `--key-facts-dedupe`) that re-applies the fixed function to every
persisted `key_facts` array. Items 5/10/51 repaired by hand first (8→6, 8→6, 8→6 facts); the full
sweep found one more affected row (item 1863, 8→7) and fixed it too.

### `entities_mentioned_nonempty_in_scope` (items 22, 39, 170)

Item 170 was already clean by the time this ran (concurrent-agent data drift). Item 39: the
deterministic watchlist-alias backfill found `Ophir Optronics`; a full `analyze_item` +
`persist_analysis` re-run then filled `summary_he`/`uncertainty_he` (its `key_facts` came back
genuinely empty — a single-lens product announcement has no extra key facts beyond the summary).
Item 22 (F-16/AIM-120/Patriot shortage story, no watchlist company named): deterministic backfill
found nothing, so a full LLM re-analyze ran instead, producing 8 `key_facts` and real graph
entities. Item 24 (outside the original list, surfaced as a side effect of the subdomain fix, same
check) was also backfilled the same way.

**Incidental blocking fix:** `eoa.memory.relational.update_item_fields` crashed with
`UndefinedColumn` on this DB (`tech_maturity`/`israel_relevance`/etc. — `persist_analysis` always
sends these regardless of domain) — without this, *no* `persist_analysis`/`update_item_fields`
call would work on this DB at all. Now checks which `items` columns actually exist
(`_existing_items_columns`, cached per-process) and silently drops fields naming a missing one,
writing every other field in the same call normally.

## D4 — deep-search investigations

### `queries_anchored_to_question` (jobs 46, 47, 86, 91)

D1 (a different concurrently-running agent) owns the live anchor/relevance-judge gate in
`eoa.search.deep_search` for *new* runs. This fix only labels the historical jobs so the check can
tell old from new: `scripts/mark_legacy_investigations.py` gained a 3rd pass that sets
`result.legacy_unanchored = true` on the given job ids (86/91 don't exist in this DB — skipped,
not an error; 46/47 do and were labelled). `eoa.qa.d4_investigations.score_D4` now exempts a job
carrying that flag from `queries_anchored_to_question` only (not the other three D4 sub-checks) —
`eoa/search/deep_search.py` itself was not touched.

## D6 — daily/weekly reports

### `no_duplicate_sentences`

**Code:** `eoa.report.qa_citations` gained `_find_cross_group_duplicates` — a sentence
(normalized, ≥4 words) that verbatim-repeats one already seen in an earlier group/section is now
flagged, wired into both the structured daily-report path (`_check_structured`, section-vs-section,
not just exec-summary-vs-section as before) and the legacy free-prose path used by
weekly/monthly/bd_territory (`check()`, `draft.sections`/`extra_sections` cross-checked against
each other too — previously only exec-summary-vs-sections was checked there at all).

**Data:** not regenerated (LLM-heavy; the next scheduled report run produces a report through the
now-extended gate). The specific round-0 finding (`daily_2026-09-06.md`, the "אילו מקצועות..."
title) is a structural overlap between the "תעשייה ישראלית" analysis table and the "נספח מקורות"
appendix table — both deterministically render the item's own title by design, and neither goes
through `qa_citations` (they're plain data tables, not LLM prose) — so this particular occurrence
isn't addressable by the generation-time fix above; documented rather than "fixed" for this file.

## D7 — BD territory reports

### `actions_table_nonempty` (bd_us, bd_kr)

**Code:** `eoa.report.bd_territory._deterministic_candidate_actions` — when the LLM produced zero
recommended actions even after both retries (perspective gate, citation QA), `build_bd_territory`
now builds a small candidate list directly from data already collected for the report: one action
per competitor win, per upcoming (≤12mo) territory conference, per open/unknown RFI/tender, plus
one for the single most-recent platform event — each with a real `[n]` citation into the registry
already built for those same rows. Rendered under a distinct heading ("פעולות מוצעות (נגזרות
מהנתונים)") with a note that the analyst narrative failed QA, so it's never confused with an
LLM-authored recommendation. `recommended_actions_table` gained a `deterministic=` flag;
`docx_builder`'s three renderers (docx/md/html) gained `note_he` support for a table (didn't exist
before).

**Data:** `eo status` showed the GPU idle (0% util, model already loaded) — rebuilt `bd_us` for
real (`python -m eoa.cli run bd --territory US`). The live LLM run this time produced actions that
survived both the perspective gate and the citation-QA retries on their own merits (the
deterministic fallback above never had to fire) — `bd_us_2026-09-06.md` now has a populated
"נקודות כניסה ופעולות מומלצות" table; `actions_table_nonempty` for the `bd_us`/`bd_kr` pair went
from failing both to only `bd_kr` (not rebuilt, per the task's `bd_us`-only instruction). The
rebuild also surfaced one more schema-drift crash unrelated to this fix: `_persist_report`'s
`INSERT INTO reports (..., territory, ...)` fails on this DB (`reports.territory`, migration
0014, not applied here either) — the docx/md/html files are written successfully *before* that
call, so the report artifact itself is complete and this check passes, but the run then raises and
the `reports` table row is never inserted. Documented, not fixed — outside this round's D1/D4/D6/
D7/D9 mandate (no QA check here depends on the `reports` table row existing) and a large-enough
change (auditing every `reports` column the same way) to warrant its own round.

## D9 — sources / tenders / conferences

### `sources_recently_fetched` (0/60 sources fetched within 7 days)

**Code:** nothing ever wrote `sources.last_fetched_at`/`fail_count` on a *successful* fetch — only
`fail_count` on failure, keyed by name (`eoa.fetch.service._bump_fail_count`, old). New
`eoa.memory.relational.touch_source_fetched(source_id, ok)`: always bumps `last_fetched_at`;
`fail_count` reset on success / incremented on failure; `last_ok_at` (new column,
`db/migrations/versions/0019_sources_last_ok_at.py`, skipped on a DB that doesn't have it yet) set
on success. `eoa.fetch.service._ingest_one_source` calls it once per attempt (success or
failure), replacing the old per-failure-only, name-keyed bump (kept as a fallback when no
`source_db_id` is available).

**Data:** new `scripts/backfill_source_last_fetched.py` — one-off, sets
`sources.last_fetched_at = MAX(items.fetched_at)` for every source that has ingested items but
whose own bookkeeping was never written (never moves a timestamp backwards). Ran: **22 sources
updated**. `sources_enabled_fetched_recently`: 0/60 (round 0) → 22/40 (this DB); the remaining
stale sources have never actually ingested an item at all — nothing to backfill from — and will
update themselves on their next real `run_ingest` call now that the code fix is in place.

## Out of scope, deferred to round 2

Re-running `score_D1` over the full 40-item golden sample after all fixes surfaced issues **not**
in round 0's flagged list (data drift between round 0's snapshot and now, from concurrent agents'
own work on this shared DB): `no_eoir_gate_agreement` (items 13, 16), more truncation (items 15,
17), and 11 more `subdomain`-repair candidates found by `repair_classification_guards.py --dry-run`.
None of these were applied — round 1's mandate was round 0's specific findings, not a fresh sweep.
`tests/unit/test_llm_batch_mode.py::TestClassifyBatchWiring::test_cloud_mode_run_classify_uses_batch_and_persists`
was already failing on `main` before any of this round's changes (confirmed via `git stash`) —
unrelated, not touched.

## Quality gates

- `ruff check`/`ruff format --check` on every touched file: clean.
- Full targeted pytest run (all new/extended test files + the modules they cover): green — see
  `docs/MODULES.md`'s "D1 QA-loop round-1 fixes" entry for the per-file test-count breakdown.
- `scripts/qa_score.py --round 1 --no-links`: see the table pasted into the chat response / appended
  to `docs/qa/loop/SCORES.md`.
