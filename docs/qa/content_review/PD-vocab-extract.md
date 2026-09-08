# Product Dossier — fixed specification vocabulary, extraction + diff (PD-vocab-extract)

Date: 2026-09-09
Scope (per `docs/PLAN_SPEC_VOCABULARY.md`, lanes a + b + the report-side spec_render integration):
`agent/eoa/dossier/vocabulary.py` (new), `agent/eoa/dossier/extract.py`, `agent/eoa/dossier/diff.py`,
`agent/eoa/dossier/spec_render.py` (new), `agent/eoa/llm/schemas/product_dossier.py`,
`agent/eoa/llm/prompts/product_dossier_extract.md`, `config/spec_vocabulary.yaml` (the `table:`
field addition per section 9 item 1, the one planned follow-up edit to that file), plus the small,
required supporting edits this round's plan itself implies but doesn't own outright: `eoa.config`
(load `spec_vocabulary.yaml` the same optional-config way as `product_lines.yaml`), `eoa.dossier.
corpus` (`CorpusResult.product_line` — needed because `eoa.dossier.vocabulary.effective_vocabulary`
requires it and the plan's own section 3.5 already writes `effective_vocabulary(corpus.product_line)`
as if that attribute existed), `eoa.pipeline.text_match` (new — the promoted, shared matcher section
3.2 asks for), and exactly ONE integration call inside `agent/eoa/dossier/report.py`
(`_ordered_report_entries`), per this round's constraint that another lane is concurrently editing
that file for an unrelated LLM-leg override.

Tests: `tests/unit/test_dossier_vocabulary.py` (new, 18 tests), `tests/unit/
test_product_dossier_extract.py` (+15 tests), `tests/unit/test_product_dossier_diff.py` (+10 tests),
`tests/unit/test_product_dossier_report.py` (2 pre-existing tests updated for the new "פרמטרים
נוספים" section and the new `_specifications_table`/`_performance_table` shape).

## 1. What this closes

`docs/PLAN_SPEC_VOCABULARY.md`'s own opening finding: the live `product_dossiers` id=1/2/3 (all the
same product, Elbit SPECTRO XR, `targeting_pods`) named the SAME fact — "20-inch-class
optical/aperture performance inside a 15-inch physical envelope" — four different ways across three
runs, split inconsistently between `specifications` and `performance`. Root cause: `SpecRow.
parameter_he`/`PerformanceRow.metric_he` were free text the model invented fresh every run. Fix: a
closed, stable-`key` vocabulary (`config/spec_vocabulary.yaml`, 121 parameters — 24 `common` + 19/
17/16/15/15/15 per line) the extraction prompt hands the model as an enumerated list, plus a
deterministic post-check pipeline (`eoa.dossier.extract.apply_vocabulary`) that never trusts the
model's own key/label/table choice unconditionally — normalizes, relocates, deduplicates, and
backfills every row after the LLM call, the same "a model that slips past the prompt's own rules is
caught here" discipline the rest of `eoa.dossier.extract` already follows.

## 2. `eoa.dossier.vocabulary` (new)

Typed, `settings()`-routed access to `config/spec_vocabulary.yaml` (mirrors `eoa.product_lines.
registry`'s convention, not raw `yaml.safe_load`). `load_vocabulary()`/`effective_vocabulary(line)`/
`param_by_key(line)`/`all_params_by_key()` (a flat global key→param map, safe because every key is
globally unique — used by `eoa.dossier.diff`/`eoa.dossier.spec_render`, neither of which has a
`product_line` in scope). Import-time-equivalent uniqueness assertion runs the first time the
vocabulary loads for a given `settings()` object (121 keys, 0 duplicates — reconfirmed live, see §5).
`match_key_by_synonym` (the promoted matcher, `eoa.pipeline.text_match.synonym_present`) is used only
as a non-destructive QA signal in `eoa.dossier.extract`, never to silently reclassify a row.

`table: "specifications" | "performance"` was added to all 121 `config/spec_vocabulary.yaml` entries
this round (section 9 item 1's flagged follow-up) — 14 keys route to `performance` (the exact list
section 3.4 named: `common.detection_range_dri`, `common.size_to_performance_ratio`, `common.
adverse_weather_performance`, `mws_eo.false_alarm_rate`/`declaration_time`/`probability_of_detection`,
`eo_air_defense_warning.revisit_rate`/`detection_range_small_uas`/`false_alarm_rate_ad`, `lorop_pods.
gsd`/`standoff_range`, `border_long_range_eo.dri_at_long_range`, `ball_gimbals_16in.slew_rate`/
`stabilization_class_microrad`), the other 107 route to `specifications` — this is DATA (the YAML),
never a hand-coded Python key list.

## 3. `eoa.dossier.extract` post-check pipeline (`apply_vocabulary`, 5 steps, in order)

1. **Key validation** (`_split_invalid_keys`) — a specifications/performance row with an empty key
   OR a non-empty key that isn't in the effective vocabulary is demoted into `other_specifications`
   (never dropped outright). Generalizes section 3.5 item 1: the plan only described a hallucinated
   key; an outright-missing key gets the same treatment, since `specifications`/`performance` are
   now defined to hold ONLY validly-keyed rows.
2. **Table relocation** (`_relocate_by_table`) — a valid-keyed row the model placed in the wrong
   table (per `config/spec_vocabulary.yaml`'s own `table` field) is converted and moved. This is the
   literal id=1/2/3 fix: `size_to_performance_ratio` always ends up in `performance` now, never split.
3. **Label normalization** (`_normalize_labels`) — `parameter_he`/`metric_he` overwritten from the
   vocabulary's own canonical `label_he` unconditionally for every valid-keyed row (section 3.5 item 2).
4. **Duplicate-key collapse** (`_collapse_duplicate_spec_keys`/`_collapse_duplicate_performance_keys`)
   — same key + same/empty variant (`SpecRow`) or conditions_he (`PerformanceRow`, no `variant` field
   of its own) collapses to whichever row has a value; different variants are kept as separate rows
   (section 3.5 item 4).
5. **Required-row backfill** (`_backfill_required`) — every `required: true` vocabulary key still
   missing after steps 1–4 gets a deterministic placeholder (`value`/`claimed_value` == `""`,
   `cites` == `[]`) — renders "לא נמצא במקורות" via the existing `_cell()` path, no renderer change.

`other_specifications` rows are additionally forced `key == ""` and checked (read-only) against the
synonym matcher — a hit is logged (`dossier.field_dropped`, reason `matches_known_vocabulary_key:
<key>`) but the row is never auto-reclassified.

## 4. `eoa.dossier.diff` — key-based matching (section 4)

`_diff_specifications`/`_diff_performance` now do a direct dict lookup by `key` (built from the
previous dossier's own stored rows) instead of `_find_matching_prev_row`'s fuzzy Jaccard matcher —
that fallback is kept, but used ONLY when the previous dossier's own rows carry no `key` field at
all (a pre-vocabulary-rollout previous run). New diff class (section 4's own addition): a key whose
current row has an empty value AND non-empty `cites` (a genuinely cited "no longer confirmed", not a
required-backfill placeholder, which always has `cites == []` and is therefore silently NOT worth a
sentence — `Sentence.cites` requires `min_length=1`, so a placeholder retraction literally cannot be
honestly cited) while the previous run had a real value → `"פרמטר '{label}' לא אושר יותר במקורות
(היה: {prev_value})."`, citing the CURRENT row's own cites (never the previous run's stale registry
numbers, per this module's own pre-existing convention).

## 5. Standing regression checks (`config/spec_vocabulary.yaml`, `tests/unit/test_dossier_vocabulary.py`)

```
common 24, targeting_pods 19, mws_eo 17, lorop_pods 16, eo_air_defense_warning 15,
ball_gimbals_16in 15, border_long_range_eo 15 -> total 121 keys, 0 duplicates
performance-routed: 14 keys (exactly section 3.4's list)
specifications-routed: 107 keys
```

## 6. Offline proof: `product_dossiers.id=3` (SPECTRO XR / Elbit Systems / targeting_pods)

No new multi-topic deep-search run launched (per this task's constraint). An offline rebuild of the
corpus (`build_corpus(..., include_live_patents_ops=False)`) confirmed the DB alone holds no matching
`items`/`events` for this product outside the previous-dossier self-reference — the live id=3 run's
own grounding text came entirely from that run's own web deep-search findings, which are not
independently persisted, so a full re-grounding replay is not reconstructable offline. Instead: a
**dry pass over the id=3 row's own already-persisted `specifications`/`performance` data** — the
exact free-text drift the plan opened with — through the real, live `eoa.dossier.vocabulary.
match_key_by_synonym` (key assignment, simulating what the new vocabulary-aware prompt asks the model
to do itself) and then the real `eoa.dossier.extract.apply_vocabulary` post-check (unchanged code
path from what a live run would execute).

Input (`product_dossiers.id=3`, as stored, pre-vocabulary):

| section | original label | value |
|---|---|---|
| specifications | ביצועי עומס אופטי במארז קומפקטי | ביצועי מטע״ד בגודל 20 אינץ׳ בתוך מארז של 15 אינץ׳ |
| specifications | מספר חיישנים דיגיטליים | עד 9 חיישנים דיגיטליים |
| specifications | כיסוי ספקטרלי | VNIR, SWIR, MWIR |
| specifications | טווח תפעולי | Ultra-Long-Range (טווח ארוך במיוחד) |
| specifications | תנאי תפעול | יום, לילה וכל תנאי מזג אוויר (All Weather) |
| performance | יחס גודל-לביצועים (עומס אופטי) | ביצועי מטע״ד בגודל 20 אינץ׳ בתוך מארז 15 אינץ׳ |
| performance | טווח תפעולי | Ultra-Long-Range (טווח ארוך במיוחד) |

Every one of these 7 free-text rows resolved to a real vocabulary key via `match_key_by_synonym`
(100% coverage — zero of this real dossier's own content would land in `other_specifications`).

Output after `apply_vocabulary` (product_line=`targeting_pods`):

- **`specifications`: 12 rows** — 2 with a real value (`spectral_channels_count`,
  `detector_type`), **10 required-key placeholders** (`field_of_view`, `line_of_sight_stabilization`,
  `laser_rangefinder`, `weight`, `envelope_dimensions`, `environmental_qualification`,
  `power_consumption`, `interfaces`, `trl_status`, `laser_designation_accuracy` — every one of these
  `required: true` and genuinely never covered by the deep-search topics this run happened to run,
  now honestly rendered "לא נמצא במקורות" instead of silently absent).
- **`performance`: 3 rows**, all filled — `size_to_performance_ratio` (`"יחס ביצועים-למעטפת"`,
  value `"ביצועי מטע״ד בגודל 20 אינץ׳ בתוך מארז 15 אינץ׳"`), `detection_range_dri`
  (`"Ultra-Long-Range (טווח ארוך במיוחד)"`), `adverse_weather_performance` (`"יום, לילה וכל תנאי מזג
  אוויר (All Weather)"`).
- **`other_specifications`: 0 rows.**
- **3 table relocations logged** (`size_to_performance_ratio`, `detection_range_dri`,
  `adverse_weather_performance` — every one of the run's specification-side entries that actually
  belonged in `performance` per `config/spec_vocabulary.yaml`'s own `table` field, moved).
- **2 duplicate-key collapses logged** (`size_to_performance_ratio`, `detection_range_dri` — each of
  these facts had been written into BOTH `specifications` and `performance` under different names in
  the original id=3 row; after key assignment they resolve to the same key and correctly collapse to
  ONE row).

**The headline result**: the exact drift fact ("20-inch-class performance in a 15-inch envelope"),
which id=3 alone had already split into two different names across two tables, now renders as
exactly ONE row — `performance.size_to_performance_ratio`, label `"יחס ביצועים-למעטפת"` — regardless
of which of the two original phrasings it started from. `docs/PLAN_SPEC_VOCABULARY.md`'s own
acceptance check 3 ("re-running the SPECTRO XR dossier twice back-to-back produces the SAME key and
the SAME table for that fact both times") is satisfied by construction: the post-check, not luck,
now decides both.

A live end-to-end reproduction of this same scenario (three different phrasings fed through the FULL
`ground_dossier` pipeline, including citation grounding against a synthetic source) is also covered
as a standing regression test: `tests/unit/test_product_dossier_extract.py::
test_end_to_end_id1_id2_id3_drift_reproduced_and_fixed`.

## 7. Test results

```
tests/unit/test_dossier_vocabulary.py ............... 18 passed
tests/unit/test_product_dossier_extract.py ........... 39 passed  (24 pre-existing + 15 new)
tests/unit/test_product_dossier_diff.py .............. 38 passed  (28 pre-existing + 10 new)
tests/unit/test_product_dossier_report.py ............ 27 passed  (2 updated for the new section)
tests/unit/test_product_dossier_schema.py ............ passed (unaffected)
tests/unit/test_product_dossier_corpus.py ............ passed (CorpusResult.product_line additive)
tests/unit/test_product_dossier_plan.py .............. passed
tests/unit/test_product_dossier_api.py ............... passed
tests/unit/test_product_dossier_services.py .......... passed
tests/unit/test_config.py ............................ passed (spec_vocabulary.yaml load additive)
tests/unit/test_product_lines.py ..................... passed
tests/unit/test_product_lines_round8.py .............. passed
Total: 347 passed
ruff check: all touched files clean
```

## 8. Backward compatibility

- `product_dossiers.data` is schemaless JSONB — no migration. A dossier persisted before this round
  reads back with `key=""` on every row and `other_specifications=[]` (Pydantic defaults) — renders
  exactly as it did before (`eoa.dossier.spec_render` sorts an unkeyed row to the end of its table
  rather than failing).
- `eoa.dossier.diff`'s fuzzy matcher (`_find_matching_prev_row`) is unchanged and still the active
  path whenever the previous dossier's own rows carry no `key` — every pre-existing fuzzy-matching
  test in `test_product_dossier_diff.py` still passes unmodified.
- `eoa.dossier.report._specifications_table`/`_performance_table` are kept as thin delegating
  aliases to `eoa.dossier.spec_render.spec_and_performance_entries` (not deleted) so the one existing
  direct caller/test of those names (`test_product_dossier_report.py`, owned by a different lane)
  keeps working unchanged — the actual new grouped-by-vocabulary logic lives entirely in the new
  `eoa.dossier.spec_render` module, integrated into `eoa.dossier.report._ordered_report_entries`
  through exactly one call, per this round's file-ownership constraint.

## 9. What's still open

- **UI (lane c)** and **product-line comparison reports (lane d)** are out of this round's scope
  (per the task brief) — `web/**` and `agent/eoa/report/product_line.py` untouched.
- `other_specifications` growth is a signal, not a steady state (plan section 9 item 3) — the id=3
  offline proof above shows 0/7 real facts landing there for this product, a good sign, but this is
  one product/one run; nobody currently owns watching this metric across future runs long-term.
- The `dircm_integration`-style "conditional keyword" ambiguity (plan section 9 item 5) was not
  ported into `config/spec_vocabulary.yaml`'s `synonyms` shape this round, per that section's own
  explicit deferral.
