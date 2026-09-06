# Round 2 fixes — D1/D3/D6 (docs/QA_CONTINUOUS_LOOP.md), judge items 2/3/8/9

Scope: `docs/qa/loop/round_1_auto.json` (round 1's own post-fix deterministic score) plus the six
items in this round's task brief, itself built from `docs/qa/loop/round_0_judge.md`'s worst-10 list
(items 1, 2, 3, 8, 9). Every fix below has a **code fix** (prevents recurrence on new data) and,
where applicable, a **data repair** (fixes already-persisted rows) — same convention as round 1.
All data repairs ran against the live DB (`postgresql://eoa:...@127.0.0.1:5432/eoanalyst`).

## 1 — Weekly report migrated to the structured citation schema (D6)

**Problem:** two live rebuilds of the weekly report failed with runaway JSON (24k then 54k chars,
EOF mid-string) on the legacy free-prose `WeeklyReportDraft` (`exec_summary_he` + `sections[]
.prose_he` + `trend_paragraphs[].prose_he`, with the model expected to type its own `"[n]"`
citation markers). The daily report solved the same class of problem months ago (goal 1,
2026-09-06) by moving to a structured, sentence-per-claim schema where an uncited claim is a
pydantic validation error, not a 24k-character free-text blob the model has to get exactly right in
one shot.

**Code:**

- `agent/eoa/llm/schemas/reports.py`: `WeeklyReportDraft` migrated to the daily's structured shape
  — `exec_summary: list[Sentence]` (max 8), `sections: list[StructuredSection]` (max 6 sentences
  each, enforced by a `field_validator` that truncates rather than rejects — a soft length
  preference, not worth burning a retry round-trip on the shared, GPU-contended resident model),
  `analyst_note_he: AnalystNote | None`, `outlook: list[OutlookIndicator]` (max 4),
  `open_points_he` (max 6), and a new `system_note_he` field for the deterministic "no items"/
  "QA failed twice" messages (mirroring `DailyReportDraft`). A new `trends: list[WeeklyTrendSection]`
  field (max 8 trends, 5 sentences each) replaces the free-prose `trend_paragraphs`/`TrendParagraph`
  for the weekly report only — `MonthlyReportDraft`/`BdTerritoryReportDraft`/`TrendParagraph` are
  **unchanged** (still legacy free-prose, out of this round's scope).
- `agent/eoa/report/qa_citations.py`: `_check_structured` extended to also validate `draft.trends`
  (cite-range + cross-group verbatim-duplicate detection) exactly like `draft.sections` —
  `getattr`-guarded so `DailyReportDraft` (no `trends` field) is unaffected. The module docstring
  updated to reflect that weekly is now structured, not legacy.
- `agent/eoa/report/weekly.py`:
  - `select_items_for_prompt` (new): reduces the prompt-visible item list to every `level='red'`
    item, the top 6 items per domain by score, and any item that only feeds a trend's evidence —
    the full item list (and its `n` numbering) stays the citation registry unchanged; only what the
    model actually reads shrinks. Each selected item's `summary_he`/`so_what_he` is also truncated
    to ~500 chars for the prompt line (a shallow copy — the persisted row is untouched).
  - `draft_weekly`/`_corrective_retry` now call `select_items_for_prompt` before formatting the
    items block, and `_no_items_draft`/a new `_qa_failed_twice_draft` replace the old free-prose
    `_no_items_draft`/`_strip_uncited` — the QA-gate flow is now: draft → check → one corrective
    retry → check → **on a second failure, drop all narrative content and render tables + a
    one-line system note instead** (mirrors `eoa.report.daily`'s own two-failure fallback exactly,
    per the task's "tables-only fallback like daily" instruction), rather than the old per-sentence
    `_strip_uncited` stripping.
  - `_render_trend_sentences` (new, small local helper): flattens a `WeeklyTrendSection`'s
    `Sentence` list to one prose string with `"[n]"` markers appended from `cites` — trends are
    still rendered into the document via the existing `extra_sections` hook (unchanged rendering
    plumbing), just fed pre-flattened structured-sentence text now instead of the model's own
    free-prose paragraph.
  - `num_predict` stays 14000 per the task; the schema + prompt-item reduction are what actually
    bound the output now, not a smaller token budget.
- `agent/eoa/llm/prompts/report_weekly.md`: rewritten to the `Sentence`/`cites` JSON-schema
  instructions (mirroring `report_daily.md`'s structure), with a new rule (#9) telling the model the
  item list it sees is a reduced subset, not the full week.
- `agent/eoa/report/bd_territory.py` / `agent/eoa/report/monthly.py`: doc-comment-only touch-ups
  (both had a comment pointing at `eoa.report.weekly._strip_uncited`, which no longer exists —
  reworded to point at `eoa.report.monthly._strip_uncited`'s own copy of that pattern instead; no
  behavior change in either file).

**Tests:** `tests/unit/test_report_qa.py`'s four "legacy shape" tests were rewritten against
`MonthlyReportDraft` instead of `WeeklyReportDraft` (they exist to test `check()`'s generic legacy
dispatch, which any legacy-shaped draft exercises identically — `WeeklyReportDraft` just isn't one
any more). `tests/unit/test_docx_builder.py`'s equivalent legacy-banner test switched the same way.
`tests/unit/test_report_weekly_monthly.py`'s weekly fixture rebuilt with `Sentence`/
`WeeklyTrendSection`/`StructuredSection`/`OutlookIndicator`. Full suite green: `test_report_qa.py`,
`test_report_weekly_monthly.py`, `test_docx_builder.py`, `test_report_daily.py`,
`test_report_bd_territory.py` — 169/169 passed (re-run after a `ruff format` pass: 208/208).

**Data / live rebuild:** `weekly_2026-09-05.md` was already quarantined
(`weekly_2026-09-05.contaminated.md.bak`) before this round started. `build_weekly()` was run for
real against the live DB/GPU (idle, per `eo status`) —see the "Live weekly rebuild" section below
for the path, exec summary, and QA result.

## 2 — Watchlist alias collision: BlueHalo/LOCUST hallucination (D3, judge item 8)

**Problem:** `config/watchlist.yaml` lists `LOCUST` and `Titan` as BlueHalo aliases (search-relevance
matching for BlueHalo's own LOCUST/Titan product lines), but both strings collide with unrelated
generic program names — AeroVironment's own "Locust X3" product and the US Marine Corps' own LOCUST
swarm program; the US Army's own TITAN ground-station program (awarded to Palantir/Anduril, unrelated
to BlueHalo). `eoa.pipeline.entity_normalize.find_watchlist_aliases_in_text` (called by
`eoa.pipeline.analyze`'s deterministic `entities_mentioned` backfill) attributed *any* bare mention of
these words to BlueHalo — verified live: item 47's AeroVironment Locust X3 / E-HEL story picked up a
hallucinated "BlueHalo" `entities_mentioned` entry with zero actual BlueHalo content in the source.

**Code:**

- `config/watchlist.yaml`: new per-company `strict_aliases:` key — the subset of `aliases` that is
  also a generic word/program name. Marked: BlueHalo (`LOCUST`, `Titan`), Anduril (`Lattice`,
  `Anvil`, `Roadrunner`), Rheinmetall (`Skynex`, `Skyranger`), IAI (`POP`). Also fixed a genuine,
  unrelated copy/paste error found during the same audit: Northrop Grumman's aliases wrongly
  included `LITENING` (Rafael's own targeting-pod product, already correctly listed under Rafael) —
  removed.
- `agent/eoa/pipeline/entity_normalize.py`: `_alias_index()`'s canonical record gained a
  `strict_aliases` field; new `_strict_alias_records()` (a `{normalized strict-alias ->
  canonical record}` index) and `_record_mentioned_non_strictly()` (does the company's own name or
  a *non-strict* alias also appear in the text?). `find_watchlist_aliases_in_text` now requires that
  co-occurrence check to pass before a strict-alias match counts — a legitimate "BlueHalo Titan"
  mention still works (both surfaces present); a bare "Locust X3"/generic "TITAN program" mention no
  longer does.
- Audited every other alias against the same collision test (docstring note in `watchlist.yaml`
  records the audit for future additions); nothing else flagged as high-risk.

**Data repair:** `scripts/repair_watchlist_strict_aliases.py` (new) re-checks every item/event whose
`entities_mentioned`/`parties` names a strict-alias-owning company against the corrected logic and
removes the now-unjustified attribution. Ran live: **5 items fixed** (9, 10, 47, 50, 96 — all
`BlueHalo` false positives; 3 of the 5 match the judge's own item list, plus items 10 and 96 found by
the same live sweep). 0 events needed fixing (the false name never made it into an event's own
`parties`/`customer`/`program`). Never deletes the BlueHalo/Anduril/Rheinmetall/IAI `entities` rows
themselves — they are real, legitimately-tracked companies.

**Tests:** no existing tests broke; `test_entity_normalize.py`'s existing coverage (`LOCUST` etc.
resolving via `resolve_canonical`, `is_technique_like`, `normalize_kind`) is unaffected since none of
it goes through `find_watchlist_aliases_in_text`'s co-occurrence gate.

## 3 — Person-name transliteration duplicates (D3, judge item 3)

**Problem:** item 81's Anduril-Israel appointee is recorded under 4 spellings across the corpus:
`entities` rows "Amikam Norkin" (id 258) and "Amiram Norkin" (id 1025) — both also mis-typed
`kind='company'` (an edge endpoint with an unresolved kind defaults to `"company"`) — plus
"עמירם נורקין"/"אמירם נורקין" only ever appearing in event title text (ids 221/245), never as their
own `entities` rows. Neither `normalize_name_key` (case/punctuation only) nor watchlist matching (a
person is never a watchlist entry) can catch this.

**Code:** `agent/eoa/pipeline/entity_normalize.py` gained a self-contained mechanism, scoped
narrowly to plausible person names:

- `_name_consonant_skeleton`: a Hebrew-letter -> Latin-consonant map (`_HEBREW_TO_LATIN_CONSONANTS`)
  plus a Latin-vowel strip, collapsing both scripts to a comparable consonant sequence. Critically,
  Hebrew `ו`/`י` map to `""` (not `"v"`/`"y"`) — in ordinary unvocalized Hebrew they overwhelmingly
  function as *matres lectionis* (vowel-marking letters, e.g. the י in "עמירם" marks the "i" sound),
  which is exactly the source of the transliteration variance this needs to collapse away; treating
  them as true consonants was the first (wrong) version of this, verified against the regression
  before landing (see the function's own docstring for the worked example).
- `person_transliteration_similarity` (difflib ratio of two skeletons) and `is_likely_same_person`
  (>= 0.85, the empirically-chosen bar — all 6 pairwise ratios across the 4 Norkin spellings clear
  it, 0.857-1.0).
- `looks_like_person_name`: scopes the dedup to a 2-4 word name that isn't a recognised
  watchlist/curated-org/country entity and (for a Latin name) carries none of a small
  company-suffix-word list (Inc/Systems/Group/... ) — so a company name never gets swept into
  person-dedup by consonant-skeleton coincidence.

**Data repair:** `scripts/repair_person_transliteration_dedup.py` (new): groups every person-like
`entities` row via `is_likely_same_person` (transitive union-find), picks a winner per group
(Latin-script preferred; tie-broken by reference count across `items.entities_mentioned` +
`events.parties`/`customer`/`program`, then lowest id), repoints every reference (`graph_edges`,
`items.entities_mentioned`, `events.parties`/`customer`/`program`, and a plain-text replace over
event `title`/`summary_he` prose), fixes `kind` to `'person'` on the survivor, and deletes the
loser(s). Ran live over the whole `entities` table (32 person-like candidates): **1 group found and
merged** — exactly the Norkin pair (winner: id 258 "Amikam Norkin", kind corrected `company` ->
`person`; loser: id 1025 "Amiram Norkin" merged in). No false-positive merges elsewhere in the table.

Out of scope (flagged for a follow-up session, not fixed here): item 81 also has 3 near-duplicate
*event rows* for this same appointment (ids 22/221/245, reworded titles) plus 2 duplicate
funding-round events and 2 duplicate Elbit-partnership events — a different problem (event-row
dedup, not entity-name dedup) that the existing `(item_id, kind, title)`-keyed gate misses on a
reworded title. A background task was spawned for this.

**Tests:** `tests/unit/test_entity_normalize.py` gained a `TestPersonTransliterationDedup` class (10
parametrized cases: all 4 Norkin spellings pairwise match each other; 3 unrelated-name negative
controls; positive/negative `looks_like_person_name` cases). 146/146 passed.

## 4 — Entity recall gap: partial entities_mentioned never re-checked (D3, judge item 2)

**Problem:** `eoa.pipeline.analyze._backfill_entities_from_watchlist` (Q3-8) only ever ran when
`entities_mentioned` was *completely* empty — once the LLM's own extraction found *something*, no
further watchlist check ever ran, even when the item's own text plainly named another company the
model missed. Verified live: item 50's TITAN award ($192M) named Palantir alongside Anduril in its
own text, but `entities_mentioned` only ever carried `[US Army, Anduril, BlueHalo]` (the BlueHalo
entry itself a false positive, fixed in item 2 above) — Palantir, a lead named party, was silently
dropped.

**Code:**

- `config/watchlist.yaml`: added `Palantir` (alias `Palantir Technologies`) as a tracked company —
  it was previously only known to `entity_normalize._COMPANY_COUNTRY_MAP` (country lookup only, not
  NER/watchlist matching), so `find_watchlist_aliases_in_text` could never find it at all regardless
  of the recall-gap fix below.
- `agent/eoa/pipeline/analyze.py`: `_backfill_entities_from_watchlist` no longer bails out when
  `entities_mentioned` is non-empty — it always re-runs `find_watchlist_aliases_in_text` against the
  item's title/text and unions any new match into whatever the model already found (returns `None`,
  a no-op, only when there is truly nothing new to add). A new `_recall_event_parties` applies the
  same union-in-additional-matches logic to one event's own `parties`, checked against that event's
  own `summary_he` text (an event can rephrase differently than the item) — wired into the event
  loop in `persist_analysis` right before `insert_event`.

**Data repair:** `scripts/repair_entity_recall.py` (new) re-runs both helpers over every in-scope
item/event. Ran live: **18 items fixed** (including item 50 -> `Palantir` added, and item 235, which
already carried the un-canonicalized alias "Palantir Technologies" — now also has the canonical
"Palantir", a pre-existing, separate canonicalization gap not fixed here) and **48 events fixed**
(recalled names spanning US Air Force/Navy/Army, Elbit, IAI, Rafael, Hensoldt, Saab, Kongsberg,
Xtend, AeroVironment, Mossad, and others — all genuine watchlist/curated-org mentions the original
extraction missed).

**Tests:** `tests/unit/test_analyze_key_facts_entities.py`: `test_no_backfill_when_already_populated`
renamed/retargeted to `test_no_backfill_when_already_complete` (nothing new in the text, still a
no-op) plus a new `test_unions_additional_match_into_already_populated_list`; a new
`TestRecallEventParties` class (3 cases); the pre-existing
`test_persist_analysis_does_not_touch_entities_when_already_present` integration test was renamed to
`test_persist_analysis_does_not_touch_entities_when_nothing_new_found` (its own title accidentally
contained a *new* watchlist match under the old test data, which is exactly the round-2 behavior
change — fixed the fixture instead of the code) plus a new
`test_persist_analysis_unions_additional_entity_into_non_empty_list`. Full file green: 139/139 (one
test, `TestPersistAnalysisIntegration`, needs a live DB for a best-effort `israel_focus` refresh call
that fails-soft with a ~30s connection-pool timeout in this sandbox when run outside `runtime/
eoa.env` — logged as a warning, asserted nothing, does not fail the test).

## 5 — NULL triage state despite `processed_stages` recording `'triage'` done (D1, judge item 9)

**Problem:** the judge found 4/40 golden items (1, 4, 7, 14) with `level`/`score`/`triage_reason`
all `NULL` despite `'triage'` in `processed_stages`. A full-corpus live sweep for the same pattern
(`'triage' = ANY(processed_stages) AND (level IS NULL OR score IS NULL)`) found **30 affected rows**,
in two distinct root causes:

- **All three NULL** (the judge's exact 4 items): `processed_stages` never reached `'analyze'` —
  these were marked triage-done without ever actually being scored (pre-dates this round's
  investigation; the exact original bug is no longer live-reproducible, only the stale rows remain).
- **Only `level` NULL, `score`/`triage_reason` stale** (26 more items, e.g. 177, 263, 595-602, 604,
  605, 844, 925, 927, 1436, 1863, 1864, 1949, 5121, 5123, 5604): traced to
  `scripts/backfill_analysis_gaps.py`'s `stub_cleanup_pass` (pass 3, Q3-10) — when discarding an
  untrustworthy stub-content analysis, it reset `level` to `NULL` and `domain` to `'out_of_scope'`
  but left the `score`/`triage_reason` that same untrustworthy analysis had produced untouched. Worse,
  this is a **permanent dead end**: both `eoa.pipeline.classify.run_classify` and
  `eoa.pipeline.triage.run_triage` explicitly skip an already-classified `domain='out_of_scope'`
  item, so nothing downstream would ever re-set `level` on these rows — they were stuck inconsistent
  forever.

**Code:**

- `scripts/backfill_analysis_gaps.py`: `stub_cleanup_pass` now sets `level='archive'`, `score=1`,
  and `triage_reason='gate:stub_content_cleared_non_defensible'` together (matching the one
  convention `eoa.pipeline.classify.run_classify` already uses for every other out-of-scope item)
  instead of leaving `level` alone as a NULL dead end.
- `agent/eoa/pipeline/triage.py`: new `validate_triage_consistency(item_id, level=, score=)` —
  scorer-independent (doesn't judge what value `level`/`score` take, only that they're both present
  or both cleared together); raises `ValueError` rather than letting a caller silently persist the
  mismatched pair. Wired into both of `run_triage`'s persist call sites (plain and U8-6 batch mode),
  each already inside a try/except that turns the raise into a counted failure rather than crashing
  the run.

**Data repair:** `scripts/repair_null_triage_state.py` (new): for every
`domain='out_of_scope' AND level IS NULL` row, sets `level='archive'`, keeps the existing `score`
when present (else `1`), keeps the existing `triage_reason` when present (else the same
`gate:stub_content_cleared_non_defensible` marker) — one target state covers both root causes. Ran
live: **30 items fixed** (1, 4, 7, 14, 177, 263, 278, 300, 310, 595-602, 604, 605, 844, 925, 927,
1436, 1863, 1864, 1949, 5121, 5123, 5604, 5721). Post-repair sweep confirms **0 remaining** rows
with `'triage'` done and `level`/`score` NULL.

**Tests:** `tests/unit/test_backfill_analysis_gaps.py`'s `TestStubCleanupPass` updated for the new
target state (13/13 passed). `tests/unit/test_triage_levels.py` gained a
`TestValidateTriageConsistency` class (4 cases: both set, both `None`, level-without-score raises,
score-without-level raises) — 21/21 passed.

## 6 — Round-1-auto (`round_1_auto.json`) D2/D3/D4/D7 residual failures

- **D3 (`events_duplicate_groups_zero`/`no_narrative_events`/`entity_junk_gate`/`entity_kind_valid`/
  `entity_country_resolves`): already 100.0** in `round_1_auto.json` — nothing failing here; the
  judge's D3 findings (items 2/3 above) are semantic/hallucination issues this deterministic check
  set doesn't test for, not a regression in these specific checks.
- **D2 `terminology_in_parens_when_relevant`** (85.7, 18/31 items carry a parenthesised English
  term): the check's own evidence string says *"informational, high false-positive rate"* — an
  LLM-prose-quality preference, not a deterministic bug; **documented, not chased** (matches this
  round's "not the LLM-quality ones" instruction).
- **D4 `queries_anchored_to_question`** (81.2 in the stale `round_1_auto.json` snapshot, jobs
  46/47/86/91): re-ran `eoa.qa.d4_investigations.score_D4` live against the current DB — **100.0**,
  all 4 checks pass. All 4 jobs now carry `result.legacy_unanchored=true` (round 1's own fix,
  `scripts/mark_legacy_investigations.py`, had already landed for all 4 by the time this round
  started — `round_1_auto.json` was just a snapshot taken before that specific write reached the
  DB this round is running against). No further action needed; `qa_score.py --round 2` will show
  the current, already-fixed number.
- **D7 `actions_table_nonempty`** (`bd_kr_2026-09-06.md`): rebuilt `bd_kr` for real (`eo run bd
  --territory KR`, GPU idle per `eo status`) to pick up round 1's own deterministic-actions-fallback
  fix (`eoa.report.bd_territory._deterministic_candidate_actions`). The rebuild succeeded
  (`qa_passed=true`, report id 37) but the actions table is **still empty** — not a code bug this
  time: the live collection window has **zero** KR-territory market items and **zero** KR-territory
  conferences (`bd_territory_market_items_collected count=0`, `bd_territory_conferences_collected
  territory_count=0`), so the deterministic fallback (one action per competitor win / upcoming
  territory conference / open tender / recent platform event) has nothing to build from — there is
  no data to report on, not a broken pipeline. Documented, not force-fixed.
- **D7 `conference_dates_match_db`**: the freshly-rebuilt `bd_kr` report now reads conference dates
  straight from the live `conferences` table, so this should also read as fixed in `qa_score.py
  --round 2`'s fresh run (the round-1 snapshot's mismatch was against the *old*, pre-rebuild report
  file).

## Live weekly rebuild

*(filled in after `build_weekly()` finished — see below.)*

## Quality gates

- `ruff check`/`ruff format --check` on every touched file: clean.
- Full targeted pytest run across every new/extended test file: green (see per-section counts
  above).
- `scripts/qa_score.py --round 2 --no-links`: see the table pasted below / appended to
  `docs/qa/loop/SCORES.md`.
