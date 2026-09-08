# Fixed specification vocabulary for the product dossier — design, frozen for parallel implementation

User request 2026-09-09: the product dossier feature (`docs/PLAN_PRODUCT_DOSSIER.md`) lets the LLM
name each specification/performance parameter freely (`SpecRow.parameter_he`, `PerformanceRow.metric_he`
are free text). Live evidence confirms the predicted failure mode: `product_dossiers` id=1/2/3 are all
the SAME product (Elbit SPECTRO XR, `targeting_pods`) and the SAME underlying fact — "20-inch-class
optical/aperture performance delivered inside a 15-inch physical envelope" — is named FOUR different
ways across three runs:

| dossier id | section | label written |
|---|---|---|
| 1 | performance.metric_he | "עומס אופטי במארז קומפקטי" |
| 2 | specifications.parameter_he | "ביצועי אופטיקה" |
| 2 | performance.metric_he | "עומס אופטי במארז קומפקטי" |
| 3 | specifications.parameter_he | "ביצועי עומס אופטי במארז קומפקטי" |
| 3 | performance.metric_he | "יחס גודל-לביצועים (עומס אופטי)" |

Same fact, inconsistent naming AND inconsistent placement (specifications vs. performance) between
runs. Run-to-run results are not comparable and competitor products cannot be compared row by row.
This document is the frozen design both implementation lanes below build against — `config/
spec_vocabulary.yaml` (the vocabulary data itself, already written) is the other half of this delivery.

## 0. What's already done vs. what this plan hands off

**Done in this round** (analysis/design lane, no code changed — CLAUDE.md scope for this task is
docs+config only):
- `config/spec_vocabulary.yaml`: `common` (24 parameters) + one block per `config/product_lines.yaml`
  id — `targeting_pods` (19), `mws_eo` (17), `lorop_pods` (16), `eo_air_defense_warning` (15),
  `ball_gimbals_16in` (15), `border_long_range_eo` (15). 121 parameters total, every `key` globally
  unique, every entry validated against a schema (see that file's own header comment for the full
  field contract: `key`/`label_he`/`label_en`/`unit`/`value_type`/`enum_values`/`synonyms`/`group_he`/
  `required`/`notes_he`).
- The vocabulary was built by reading the live `product_dossiers` rows (ids 1–3) directly — every
  `synonyms` list for the parameters implicated in the drift above (`common.size_to_performance_ratio`,
  `targeting_pods.pod_class_diameter`, `targeting_pods.common_aperture_telescope`, `targeting_pods.
  laser_spot_tracker`, `targeting_pods.ai_target_recognition`, `common.gnss_receiver`, `common.
  inertial_sensors_imu`, `common.trl_status`) includes the EXACT Hebrew strings the extractor already
  wrote in those three live runs, so the new matcher (lane a) has real positive examples on day one,
  not just guessed phrasing.

**Not done, this document specifies for four parallel lanes**: (a) extraction, (b) diff, (c) UI,
(d) product-line reports, plus (e) tests. No code in `agent/eoa/**` or `web/**` has been touched by
this round — every file path below is a target for the lane owner, not something already edited.

## 1. Why a `key`-based vocabulary, not smarter fuzzy matching

`eoa.dossier.diff` already tried to paper over free-named parameters with fuzzy matching
(`_find_matching_prev_row`, token-Jaccard ≥ 0.5 on the name) — see that module's own PD-fix-3 item 3
comment. It works well enough for a *rename* of the same fact but cannot fix the deeper problem: two
different runs also disagree on WHICH SECTION a fact belongs to (specifications vs. performance in the
table above) and cannot be compared across competitor products at all (Elbit's "ביצועי אופטיקה" and a
Rafael pod's own free-named equivalent share no string in common for a UI to align side by side). A
closed vocabulary with a stable `key` fixes both: extraction always writes to the same named slot
regardless of phrasing, and a slot is either "spec" or "performance" by construction (the vocabulary
doesn't have separate spec/performance parameter sets — see §3 for how the two existing tables absorb
one flat vocabulary).

## 2. Vocabulary file contract (already delivered)

`config/spec_vocabulary.yaml`, `schema_version: 1`. Read that file's own header comment block first —
it is the authoritative field-by-field contract (do not duplicate it out of sync here). Summary only:

- `common` + one block per `product_lines.yaml` id. **Effective vocabulary for a dossier of product
  line X = `common` followed by `vocabulary[X]`** (concatenation, not merge-by-key — no key collides
  between `common` and any line block, or between two line blocks; the loader in §3.1 asserts this at
  import time). A dossier with `product_line: null` uses `common` alone.
- Every parameter: `key` (snake_case, stable, never renamed — this is the join key for diff and for
  cross-product comparison), `label_he`/`label_en`, `unit`, `value_type` (`number|range|text|enum|bool
  |list`), `enum_values` (when `value_type: enum`), `synonyms` (plain literal Hebrew/English phrases —
  matched via the SAME word-boundary-safe matcher `config/product_lines.yaml`'s own `keywords_he`/
  `keywords_en`/`aliases` already go through, see §3.2), `group_he` (one of the 8 fixed section groups),
  `required` (row always renders, "לא נמצא במקורות" when unknown), `notes_he` (grounding caveats —
  claimed-vs-demonstrated, target-size assumptions for DRI, which sibling `key` a param must NOT be
  confused with).
- **`group_he`** — the fixed 8 groups, in this order (UI groups by this order, §5.3): אופטיקה,
  חיישנים, לייזר, ייצוב ובקרה, מכניקה וסביבה, ממשקים, ביצועי מערכת, בשלות ולוגיסטיקה.

## 3. Lane (a): Extraction — owner PD-vocab-extract

**Files**: `agent/eoa/dossier/vocabulary.py` (NEW), `agent/eoa/dossier/extract.py` (MODIFY),
`agent/eoa/llm/schemas/product_dossier.py` (MODIFY), `agent/eoa/llm/prompts/product_dossier_extract.md`
(MODIFY), `db/migrations/versions/00NN_dossier_spec_vocabulary.py` (NEW, only if a stored-shape change
needs a migration — see §3.4, likely NOT needed since `product_dossiers.data` is schemaless JSONB).
**Excludes** (per this task's constraint): `agent/eoa/search/deep_search.py`, `agent/eoa/llm/chain.py`
— another lane is editing those; do not touch.

### 3.1 Loading the vocabulary

New `agent/eoa/dossier/vocabulary.py`:
```python
@dataclass(frozen=True)
class SpecParam:
    key: str
    label_he: str
    label_en: str
    unit: str | None
    value_type: str
    enum_values: list[str] | None
    synonyms: list[str]
    group_he: str
    required: bool
    notes_he: str

def load_vocabulary() -> dict[str, list[SpecParam]]: ...       # raw common + per-line blocks
def effective_vocabulary(product_line: str | None) -> list[SpecParam]: ...  # common + vocabulary[line]
def param_by_key(product_line: str | None) -> dict[str, SpecParam]: ...
```
Loaded once from `config/spec_vocabulary.yaml` (same `yaml.safe_load` + repo-root-relative path
convention `eoa.config`/`eoa.product_lines.registry` already use), cached (`functools.lru_cache`, same
pattern as `eoa.product_lines.registry`). At import/first-load time, assert: every `key` globally unique
across `common` + all six line blocks (a duplicate key is a config bug, fail loud, not silently pick
one) — this is the one integrity check this lane owns; the vocabulary file itself already passed it in
this round's own validation (121 keys, 0 duplicates, see the file's own header for how it was checked).

### 3.2 Synonym matching

Reuse — do not reimplement — the existing word-boundary-safe matcher: `eoa.dossier.corpus._word_present`
(whole-word regex, case-insensitive) already does exactly what a Latin synonym/acronym needs (the same
concern `config/product_lines.yaml`'s own header docs for `keywords_en`/short-acronym matching); a
Hebrew synonym matches as a plain case-insensitive substring (Hebrew has no word-boundary ambiguity
issue the way "MWS" does inside "MWSXYZ" — substring matching for Hebrew is what `eoa.product_lines.
tagging`'s own `keywords_he` matching already does). Promote `_word_present` to a shared, non-private
location (`eoa.pipeline.text_match` or similar — lane owner's call; do not import a `_`-prefixed name
across package boundaries, same layering rule `eoa.dossier.extract`'s own docstring already states for
why it duplicates `eoa.pipeline.analysis_grounding`'s digit matcher instead of importing it).

### 3.3 Extraction prompt change

`product_dossier_extract.md` currently (implicitly) lets the model invent `parameter_he`/`metric_he`.
Change: the prompt is handed the EFFECTIVE vocabulary (§3.1) as an enumerated, numbered list — one line
per `SpecParam`: `key | label_he (label_en) | value_type [enum: ...] | unit` — and instructed:
1. For every vocabulary parameter the sources actually establish a value for, emit ONE `SpecRow` (or
   `PerformanceRow` when the vocabulary entry's own semantics are a measured/claimed-vs-demonstrated
   metric — see §3.4 for exactly which `group_he`/parameters route to which table) with `key` set to
   the vocabulary key VERBATIM (never invented, never reworded) and `parameter_he`/`metric_he` set to
   that key's own `label_he` VERBATIM (also never reworded — this is what makes cross-run/cross-product
   labels identical by construction, not by luck).
2. A vocabulary parameter marked `required: true` that the sources say nothing about is NOT emitted as
   a row by the model — §3.5's post-check fills every required-but-missing row deterministically
   (`value: null` → renders "לא נמצא במקורות"), so the model is never asked to "helpfully" invent a row
   just to satisfy a required flag.
3. A genuine fact that matches NONE of the vocabulary parameters (by key or by any of its synonyms)
   goes into a NEW, small `other_specifications: list[SpecRow]` bucket (§3.4) with a model-chosen
   `parameter_he` as today — this is the deliberate overflow valve so a real, unanticipated spec is
   never silently dropped; it is NOT diffed by key (§4) and NOT grouped in the UI table (§5.3 renders
   it as its own small "אחר" appendix table, unsorted, exactly as free text today).
4. The `value_type`/`enum_values`/`unit` given for each vocabulary key are a FORMAT hint, not a
   validation gate at the schema level — `SpecRow.value` stays a free string (unchanged from today,
   "as published, never normalized" per that field's own docstring) so a source's own phrasing is
   never mangled to fit a type; a value that doesn't look like its declared `value_type` is not
   rejected, just a signal lane (e) tests should include as a P2, non-blocking QA warning.

### 3.4 Schema change

`agent/eoa/llm/schemas/product_dossier.py`:
- `SpecRow` gains `key: str = Field(default="", description="מפתח יציב מתוך config/spec_vocabulary.yaml, ריק אם 'אחר'")`.
- `PerformanceRow` gains the same `key: str = Field(default="")`.
- `ProductDossierOut` gains `other_specifications: list[SpecRow] = Field(default_factory=list)` — the
  §3.3-item-3 overflow bucket, keyed rows never land here (`key` always empty in this list, enforced by
  the post-check in §3.5).
- **Which vocabulary parameters route to `specifications` vs. `performance`**: a vocabulary entry with
  `value_type` in `{number, range}` AND whose `notes_he`/semantics describe a MEASURED outcome (claimed
  vs. demonstrated is a meaningful distinction for it) routes to `performance` — concretely: `common.
  detection_range_dri`, `common.size_to_performance_ratio`, `common.adverse_weather_performance`,
  `mws_eo.false_alarm_rate`, `mws_eo.declaration_time`, `mws_eo.probability_of_detection`,
  `eo_air_defense_warning.*_range`/`*_rate`, `lorop_pods.gsd`/`standoff_range`, `border_long_range_eo.
  dri_at_long_range`, `ball_gimbals_16in.slew_rate`/`stabilization_class_microrad` — this is the exact
  fix for the id=1/2/3 drift (the "20-in-15-in" fact is `common.size_to_performance_ratio`, ALWAYS a
  `performance` row now, never split across both tables). Every other vocabulary entry (mechanical
  dimensions, interfaces, enums, bools, identity-shaped facts) routes to `specifications`. This mapping
  is itself DATA, not model judgment: add a `table: "specifications" | "performance"` field to every
  `SpecParam` in `config/spec_vocabulary.yaml` (a small follow-up edit to the vocabulary file, owned by
  whichever lane implements §3.1 first, since it's additive and doesn't change any `key`/counts already
  frozen this round) rather than hand-coding a key list in Python — keeps the vocabulary file the single
  source of truth the prompt-builder and the post-check both read.
- No DB migration needed: `product_dossiers.data` is schemaless `JSONB` (see `docs/PLAN_PRODUCT_DOSSIER.
  md` §2) — a new `key`/`other_specifications` field is just a new key in that JSON, old rows without it
  read back as `key=""`/`other_specifications=[]` via Pydantic defaults, no backfill required.

### 3.5 Post-check (grounding) changes

`eoa.dossier.extract.ground_dossier` currently drops/trims per-row. Add, after the existing per-row
grounding (`_ground_spec_row`/`_ground_performance_row` unchanged):
1. **Key validation**: a `SpecRow`/`PerformanceRow` whose `key` is non-empty but not in
   `effective_vocabulary(corpus.product_line)` — the model hallucinated a key — clears `key` to `""` and
   moves the row into `other_specifications` (same "don't lose the fact, demote it" discipline the rest
   of this module already applies). Logged as `dossier.field_dropped` reason `"invalid_vocabulary_key"`
   same as every other drop in this file, but this one demotes rather than deletes.
2. **`parameter_he`/`metric_he` normalization**: for a row with a VALID `key`, overwrite `parameter_he`/
   `metric_he` with `param_by_key[key].label_he` unconditionally (never trust the model's own copy of
   the label, even if it typed it correctly — one canonical write path, not a "matches" check that can
   silently drift over time the way `eoa.dossier.diff`'s fuzzy-name matching already had to work around).
3. **Required-row backfill**: after grounding, for every `required: true` vocabulary param with no
   matching `key` in `specifications`/`performance`, append a placeholder row (`value: ""`, `cites: []`)
   — renders via the existing `_cell()` → "לא נמצא במקורות" path in `eoa.dossier.report`, no renderer
   change needed for this part.
4. **Duplicate-key collapse**: if the model emits two rows with the same `key` (e.g. once per variant),
   keep both ONLY if they carry different `variant` values (a legitimate per-variant spec); two rows
   with the same `key` AND the same/empty `variant` collapse to whichever carries a non-empty `value`
   (first one wins on a further tie) — logged as `"duplicate_vocabulary_key"`.

## 4. Lane (b): Diff — owner PD-vocab-diff

**Files**: `agent/eoa/dossier/diff.py` (MODIFY). No schema/migration changes.

With `key` now stable, `_diff_specifications`/`_diff_performance` no longer need `_find_matching_prev_
row`'s fuzzy Jaccard matching AT ALL for a keyed row — replace the matching step with a direct dict
lookup by `key` (`{r["key"]: r for r in prev_rows if r.get("key")}`), falling back to the EXISTING fuzzy
matcher (`_find_matching_prev_row`, unchanged) ONLY for `other_specifications` rows (which still have no
`key`, by construction — §3.3 item 3) and for any pre-vocabulary-rollout previous dossier whose own rows
have no `key` at all (an old dossier diffed against a new one — the fuzzy path is the graceful fallback,
not removed, just demoted to "only when there's no better signal"). `_deal_key`/`_diff_deals`/`_diff_
versions`/`_diff_pricing` are untouched (deals/versions/pricing were never the free-naming problem).
Sentence text generation (`"שינוי בערך המפרט '...':"` etc.) uses `param_by_key[key].label_he` for the
name shown, same normalization discipline as §3.5 item 2 — never the raw stored `parameter_he` (which
is now redundant with the label but kept on the row for renderer simplicity/back-compat).

Add one new diff class the vocabulary makes possible for the first time: a `key` present in the CURRENT
vocabulary's `required` set but missing from both this run's row AND the previous run's data is not
worth a sentence (both runs equally "unknown" — no change); a `key` that flips from a real value to
empty (a source apparently retracted/superseded) DOES deserve a sentence — add this as a new branch in
`_diff_specifications` (`prev had a value, current key is required-but-backfilled-empty` → `"פרמטר
'{label}' לא אושר יותר במקורות (היה: {prev_value})."`).

## 5. Lane (c): UI — owner PD-vocab-ui

**Files**: `web/src/lib/specVocabulary.ts` (NEW — a straight TS port of the parameter list actually
needed client-side: `key`/`label_he`/`label_en`/`group_he`/`unit`/`required`, generated from or kept in
sync with `config/spec_vocabulary.yaml` — lane owner decides codegen vs. hand-sync; either way this file
must not silently drift from the YAML, add a vitest that round-trips a few known keys/counts against a
fixture copy of the YAML, same spirit as §7's schema test), `web/src/types/api.ts` (MODIFY — `DossierSpecRow`/
`DossierPerformanceRow` gain `key: string`, `ProductDossierOut`-mirroring type gains
`other_specifications`), `web/src/components/dossiers/DossierSpecTable.tsx` (NEW, replaces the current
generic table call for the specifications/performance sections in `DossierDetailPage.tsx`),
`web/src/components/dossiers/DossierComparisonView.tsx` (NEW), `web/src/pages/DossierComparisonPage.tsx`
(NEW), route + nav entry in whatever router config `DossiersPage`/`DossierDetailPage` already register
in (MODIFY), `web/src/mocks/data/dossiers.ts` (MODIFY — add `key`/`other_specifications` to fixtures).

### 5.1 Grouped spec table (single dossier)

`DossierSpecTable` replaces the flat `DossierTable` currently used for specifications/performance
(`DossierDetailPage.tsx`'s existing table wiring — inspect its current props before changing; keep
`DossierTable`'s own `<= 6 columns, sticky header` primitive underneath, this is a grouping WRAPPER
around it, not a replacement of it). Renders one sub-table per `group_he`, groups in the fixed order
from §2, each sub-table's rows in vocabulary declaration order (not alphabetical, not by extraction
order — `common` params for that line's effective vocabulary first within each group, then that line's
own params). A `required: true` row always renders (placeholder "לא נמצא במקורות" cell, styled distinct
from a genuinely-empty-but-not-required row so a BD reader can tell "nobody looked" apart from "not
applicable to this line"). `other_specifications` renders as one small un-grouped table titled "פרמטרים
נוספים" at the end, unchanged styling from today's free-text row rendering.

### 5.2 Comparison view (up to 3 dossiers, same product line)

New `/dossiers/compare?keys=a,b,c` route (query-param list of `product_key`, 2–3 entries, same line —
reject/warn if the selected products don't share a `product_line`, since the whole point of a keyed
vocabulary is that comparison only means something within one line's own fixed table). Fetches each
product's `latest` dossier via the existing `GET /api/dossiers/{key}` (no new API endpoint needed — this
is a client-side join of up to 3 existing calls). Renders one row per vocabulary `key` (grouped by
`group_he`, same order as §5.1) with one value column per selected product; a cell a product's own
dossier doesn't have renders "לא נמצא במקורות" same as the single-dossier view. Entry points: (1) a
"השווה" multi-select action on `DossiersPage`'s existing card list (checkbox per card, up to 3, "השווה
נבחרים" button when 2–3 are checked); (2) from `DossierDetailPage`'s existing `competitors` table
(`_competitors_table` in `report.py`) — a competitor row names a `product`/`vendor` that MAY already
have its own dossier (`GET /api/dossiers` list, matched by name — best-effort, no guaranteed link since
a competitor mention doesn't imply a dossier was ever run for it); when matched, render a "השווה" link
next to that competitor row; when not matched, render "הרץ סקירה למוצר זה" which POSTs a new dossier job
for that competitor (`POST /api/dossiers` — the existing "סקירה חדשה" form's own submit path, pre-filled
from the competitor row's `product`/`vendor`) rather than silently doing nothing.

### 5.3 Product-line report ("השוואת מפרט")

See §6 (lane d) for where this table is generated; §5 only owns rendering it once it exists as report
content (reuses `DossierSpecTable`'s grouping logic in read-only/report-embed mode, or the report's own
docx/md/html renderer per §6 — lane owner's call whether this is a live React view or a static
generated table baked into the existing product-line report's render path; recommend the latter, since
`eoa.report.product_line`'s existing report already has md/html/docx renderers this can extend rather
than inventing a fourth, web-only rendering path).

## 6. Lane (d): Product-line reports — owner PD-vocab-reports

**Files**: `agent/eoa/report/product_line.py` (or wherever `eoa.report.product_line`'s section builders
live — locate the existing report's section-builder module before editing; MODIFY, additive section
only), whatever docx/md/html shared renderer that report already calls (reuse `eoa.report.docx_builder`
the same way `eoa.dossier.report` already does — no new renderer).

Add one new section, "השוואת מפרט" (spec comparison), to the existing product-line report: for the
report's own product line, query `product_dossiers` for every DISTINCT `product_key` with `product_line
= <this line>` and take each one's `latest` (max `created_at`) row. If fewer than 2 such products have a
dossier, render the section's placeholder "אין מספיק סקירות מוצר בקטע זה להשוואה" (never omit the
section — same "every section always present" discipline `eoa.dossier.report._ordered_report_entries`
already documents). Otherwise render one grouped table per `group_he` (§5.1's grouping, reused
server-side), one column per product (their `product_dossiers.product_name`), rows = the LINE's
effective vocabulary in declaration order, `<= 6` total columns per `docx_builder`'s existing table
constraint — cap at 5 products per table (label column + 5) if `<= 6` is a hard limit already enforced
elsewhere (`DossierTable.tsx`'s own dev-time console warning is the UI-side version of this same rule);
when more than 5 products have dossiers, split into multiple 5-product tables rather than truncating
silently.

## 7. Lane (e): Tests and acceptance checks — owner PD-vocab-tests (or shared across a/b/c/d)

**Files**: `tests/unit/test_spec_vocabulary.py` (NEW), `tests/unit/test_product_dossier_extract.py`
(MODIFY — add key-routing/normalization/backfill/duplicate-collapse cases per §3.5), `tests/unit/
test_product_dossier_diff.py` (MODIFY — add by-key diff cases per §4), `web/src/pages/
DossierDetailPage.test.tsx` (MODIFY — grouped table renders, required-placeholder styling),
`web/src/pages/DossierComparisonPage.test.tsx` (NEW), e2e `25-dossier-comparison.spec.ts` (NEW,
alongside the existing `24-dossiers.spec.ts` per `docs/PLAN_PRODUCT_DOSSIER.md` §7).

Acceptance checks (mirrors `docs/PLAN_PRODUCT_DOSSIER.md` §7's own D7-style QA list, additive):
1. `config/spec_vocabulary.yaml` loads, 121 keys (24 common + 19/17/16/15/15/15 per line), zero
   duplicate keys, every entry's `group_he` in the fixed 8-value set, every `enum` entry carries
   `enum_values` — **already verified this round** (`yaml.safe_load` + a structural check script; see
   §0). Lane (a)'s `test_spec_vocabulary.py` should re-assert this as a standing regression test, not
   just a one-off manual check.
2. A rebuilt SPECTRO XR dossier (re-run against the live corpus, once lane (a) ships) has ZERO
   free-named rows in `specifications`/`performance` for any fact the vocabulary already covers —
   `other_specifications` should be small/empty for a well-covered product line; a non-trivial
   `other_specifications` count is itself a signal the vocabulary is missing something real (feed back
   into `config/spec_vocabulary.yaml` — this file is expected to grow, `key`s are stable but the lists
   are not closed forever).
3. The exact id=1/2/3 drift this document opened with does not recur: re-running the SPECTRO XR dossier
   twice back-to-back produces the SAME `key` (`common.size_to_performance_ratio`) and the SAME table
   (`performance`, per §3.4's routing) for that fact both times.
4. Comparison view renders for 2–3 `targeting_pods` dossiers (SPECTRO XR + at least one competitor
   dossier run ad hoc for the test) with every row keyed, no fuzzy-matched/misaligned row.
5. Every spec/comparison table stays `<= 6` columns (existing `DossierTable` dev warning + the report
   table's own 5-product cap, §6).

## 8. Ownership (parallel lanes, no file overlap)

- **PD-vocab-extract** (backend core): `agent/eoa/dossier/vocabulary.py` (new), `agent/eoa/dossier/
  extract.py`, `agent/eoa/llm/schemas/product_dossier.py`, `agent/eoa/llm/prompts/
  product_dossier_extract.md`, plus the `table:` field addition to `config/spec_vocabulary.yaml` (§3.4
  — the only planned follow-up edit to the vocabulary file itself). Blocks lane (b) (needs `key` on the
  schema) and lane (c) (needs `key` in the API response) — land first.
- **PD-vocab-diff** (backend): `agent/eoa/dossier/diff.py`. Depends on PD-vocab-extract's schema change.
- **PD-vocab-ui**: `web/src/lib/specVocabulary.ts`, `web/src/types/api.ts`, `web/src/components/
  dossiers/DossierSpecTable.tsx`, `web/src/components/dossiers/DossierComparisonView.tsx`,
  `web/src/pages/DossierComparisonPage.tsx`, router/nav wiring, `web/src/mocks/data/dossiers.ts`.
  Depends on PD-vocab-extract's schema change for real data shape; can start against mocks immediately.
- **PD-vocab-reports**: `agent/eoa/report/product_line.py` (or the located equivalent). Depends on
  PD-vocab-extract (reads `product_dossiers.data` with `key`s) but not on lane (b)/(c).
- **PD-vocab-tests**: owns the NEW test files; each other lane owns the MODIFY of its own existing test
  files as part of its own change (not a separate handoff).
- **Lead**: reviews the `table:` field addition to `config/spec_vocabulary.yaml` before it lands (the
  one place a downstream lane edits the file this round froze), first live re-run of SPECTRO XR against
  the new vocabulary, final review, commit.

**Explicitly excluded from every lane above** (being edited by another, unrelated lane right now):
`agent/eoa/search/deep_search.py`, `agent/eoa/llm/chain.py`.

## 9. What the vocabulary could not settle (flagged for the lead, not resolved unilaterally)

1. **`table:` routing field is designed but not yet added to the YAML.** §3.4 specifies exactly which
   parameters route to `performance` vs. `specifications` and why, but per this task's write-scope
   (`config/spec_vocabulary.yaml` + this doc only, no `agent/eoa/**` code this round) the field itself
   was not added to avoid landing a schema change with no code yet reading it. Whichever lane implements
   §3.1 first should add `table: "specifications" | "performance"` to each of the 121 entries per §3.4's
   rule before wiring the prompt — flagged explicitly so it isn't missed.
2. **Unit normalization is explicitly NOT attempted.** `SpecRow.value` stays free text "as published"
   (existing rule, unchanged) — `unit` in the vocabulary is a UI/prompt hint only, not a conversion
   target (e.g. a source publishing detection range in nautical miles is never converted to km). If the
   BD user later wants unit-normalized comparison, that is a distinct, larger feature (parsing +
   conversion + a "value_si"-style derived field) — out of scope here, flagged rather than half-built.
3. **`other_specifications` growth is a signal, not a steady state.** §7 item 2 already says this: if a
   product line's `other_specifications` stays consistently non-trivial after real re-runs, the right
   fix is to extend that line's vocabulary block (new `key`s), not to keep tolerating an overflow
   bucket. Nobody currently owns watching this metric long-term — the lead should decide whether that's
   a recurring manual review or an automated QA check (`docs/qa/loop`-style round) once lane (a) ships.
4. **Cross-line comparison (e.g. a targeting pod vs. a ball gimbal) is deliberately unsupported.** §5.2
   requires all compared dossiers share one `product_line` because only `common` keys would align
   otherwise (a small, mostly-mechanical subset — weight/envelope/power/interfaces) and a table that's
   90% "לא רלוונטי" for one side is not useful. If the user later wants a `common`-only cross-line
   comparison, that's a straightforward variant of §5.2 (render only `common` keys, drop the line-block
   rows) — not built now since it wasn't asked for, flagged as a cheap extension if it comes up.
5. **`config/product_lines.yaml`'s own R8-tagging `conditional_keywords_en` pattern (a term counts only
   together with a context term) was considered for `spec_vocabulary.yaml` synonyms** (e.g. `mws_eo.
   dircm_integration`'s own `notes_he` flags exactly this ambiguity — "DIRCM" alone is not enough) but
   NOT implemented as a first-class YAML feature this round, to keep the schema lane (a) reads simple on
   day one. The note documents the caveat in prose; if extraction-time false positives on `dircm_
   integration` (or a similarly ambiguous term) turn out to matter in practice, port the `conditional_
   keywords` shape from `product_lines.yaml` into `spec_vocabulary.yaml` as a follow-up — same schema,
   proven pattern, deliberately deferred rather than spent here on an unconfirmed need.
