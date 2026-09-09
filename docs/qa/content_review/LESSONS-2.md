# LESSONS-2 — the analysis/rendering side of the hand-written-review lessons (Fable 5.1, 2026-09-09)

Scope per this task's own brief ("שלב 2", analysis + rendering): `agent/eoa/eoa/llm/schemas/
product_dossier.py`, `agent/eoa/dossier/extract.py`, `agent/eoa/dossier/report.py`, `agent/eoa/
dossier/spec_render.py`, `agent/eoa/dossier/diff.py` (read-only — no change needed), `agent/eoa/
llm/prompts/product_dossier_extract.md`, `web/src/pages/DossierDetailPage.tsx` +
`web/src/components/dossiers/*` + `web/src/types/api.ts` + `web/src/api/real.ts` normalisers +
i18n, and tests. `corpus.py`, `plan.py`, `config/product_lines.yaml`, `deep_search.py`, `chain.py`
were not touched, per the task brief — a parallel lane (LESSONS-1) owns them.

**Mid-task landing**: the LESSONS-1 lane (`gaps.py`, `datasheet.py`, `programs.py`,
`pdf_reader.py`, plus `corpus.py`/`plan.py`/`config.py`/`config/product_lines.yaml` edits) landed
*while this round was in progress*. `CorpusResult.gap_status`/`datasheets`/`programme_deals`/
`competitor_seeds` — declared "not yet landed, code defensively via `getattr`" at the start of this
task — are real by the end of it. Every integration point below was updated to use the REAL
interface (`eoa.dossier.gaps.gap_status`/`diff_new_gaps`, `CorpusResult.programme_deals`/
`datasheets`) rather than the placeholder `getattr` stubs originally planned, while keeping
`getattr`-based defensiveness as a no-op fallback for a corpus build that predates that lane
(never a crash either way).

## 1. `timeline` (schema field + deterministic build + LLM milestones + render + UI)

`TimelineRow(date, event_he, kind, cites)`, `kind ∈ {launch, contract, integration, exhibition,
variant, milestone}` (`agent/eoa/llm/schemas/product_dossier.py`). The model is asked (prompt) for
only the three kinds it can uniquely contribute — `integration`/`exhibition`/`milestone`, each with
a real cited date — never `launch`/`contract`/`variant`, which are always built deterministically
(never duplicated by asking the model for both). `eoa.dossier.extract._ground_timeline` drops any
LLM row with no citation or no real date (a chronological table has no place for either).
`eoa.dossier.extract.build_timeline(dossier, corpus=None)` merges the model's grounded rows with:
`identity.first_announced` → `launch`, every dated+cited `DealRow` → `contract`, every
year+cited `VersionRow` → `variant`, and — once `corpus.programme_deals` is available (the
LESSONS-1 lane, "עסקת תוכנית שהמוצר רכיב בה") — every dated+cited programme deal → `contract` with
a "component of package" phrasing; sorted chronologically. Called from `eoa.dossier.extract.
ground_dossier` right after the rest of the row grounding, so the PERSISTED `data.timeline` already
carries the full merged/sorted list. Rendered as its own table (`eoa.dossier.report._timeline_table`,
4 columns) and its own UI section (`DossierDetailPage`'s `#dossier-timeline`, `timelineColumns`).

## 2. `pricing_estimate` (separate from `pricing`, gated, disclaimed)

`PricingEstimateBlock(method_he, assumptions[cited], market_anchors[cited], range_low/high,
currency, basis_he, confidence: Literal["low"])`. Gate (`eoa.dossier.extract.
_ground_pricing_estimate`): kept only when at least one cited market anchor survives grounding AND
at least one cited contract total with a duration/scope is present (`_has_cited_contract_total_
with_scope` — a `DealRow` with `amount`+cites and `quantity`/`platform`, OR a `PriceRow` whose
`basis_he` is the canonical `BASIS_TOTAL_HE` form) — otherwise the whole block drops to `None`
(never a half-formed estimate). Rendered as two entries, ALWAYS both present (prose
method/assumptions/range + a market-anchors table) so the report's own section order never varies
with whether the gate passed; the mandatory Hebrew disclaimer
(`"אומדן אנליטי, לא נתון ממקור"`, `PRICING_ESTIMATE_DISCLAIMER_HE`) is rendered every time the
block is non-null. UI mirrors this exactly (`#dossier-pricing-estimate`), including the disclaimer
and the placeholder when the gate didn't pass.

## 3. `claims_review` (up to 5, capped deterministically)

`ClaimReviewRow(claim_he, basis_he, verifiability_he, comparability_he, verdict, cites)`,
`verdict ∈ {plausible, unverified, contradicted}`. No `max_length` on the schema field itself (that
would fail the WHOLE extraction call if the model overshot) — `eoa.dossier.extract.
_ground_claims_review` drops any row with no surviving citation, then truncates to 5, logging every
drop. Rendered as a 6-column table (`_claims_review_table`) and a UI section
(`#dossier-claims-review`).

## 4. Per-row confidence (specs, performance, deals, variants, partnerships, competitors)

`RowConfidenceHe = Literal["high", "medium", "low"]`, declared once near the top of
`product_dossier.py` so every row model can reference it. Definition
(`eoa.dossier.extract._row_confidence_he`): **high** = a qualifying `source_kind`
(datasheet/official/contract/tender/budget) OR ≥2 surviving citations; **medium** = exactly one
surviving citation with no qualifying source kind; **low** = no surviving citation (inference).
Computed deterministically, AFTER grounding, in every row-grounding function that didn't already
have one (`_ground_spec_row`, `_ground_performance_row`, `_ground_deal_row` →
`DealRow.confidence_level` — a NEW field, kept distinct from the pre-existing continuous 0–1
`DealRow.confidence` float, which is the model's own subjective per-deal estimate, never touched —
`_ground_version_row` [new], `_ground_competitor_row`, `_ground_partner_row`). Never authored by
the model. Rendered as a real column where a table has room within the shared ≤6-column cap
(other_specifications, partnerships, competitors, variants), merged into an existing cell where it
doesn't (specifications' "סוג מקור" cell, performance's "תנאים" cell, deals' citations cell) — same
merge-not-add discipline on the UI side (`DossierSpecTable`'s specifications columns). The
dossier-level `confidence` (`eoa.dossier.report._compute_outcome_confidence`) is now the weighted
share of `"high"` rows across all six row kinds (falling back to the pre-existing
investigation-confidence average only when there are zero confidence-bearing rows at all, e.g. a
fully empty dossier).

## 5. `gaps_tracking` (closed/open/new, persisted forward for the next run)

`GapTrackingRow(gap_he, status, cites)`, `status ∈ {closed, open, new}`. Built by
`eoa.dossier.report._build_gaps_tracking_raw(corpus, dossier)`: `CorpusResult.gap_status`'s own
closed/open rows (this run's gap-followup topics, `eoa.dossier.gaps.gap_status`, landed by
LESSONS-1 — read via `getattr` so an older corpus build is a no-op, never a crash) UNION
`eoa.dossier.gaps.diff_new_gaps(previous_gap_texts, current_risks_and_gaps_texts)` (a gap in THIS
run's own final `risks_and_gaps_he` that wasn't already known) — exactly the merge that module's
own docstring names this lane to perform. The merged raw list is written verbatim into
`product_dossiers.data.meta.gaps` (`_persist`) — the ONE documented location
`eoa.dossier.gaps.extract_gaps_from_previous` reads (priority 1) for the NEXT run's own
gap-followup topics, closing the loop LESSONS-fable-dossier item 8 asked for. Rendered as its own
table (`_gaps_tracking_table`) and UI section (`#dossier-gaps-tracking`).

## 6. Sources appendix grouped by kind + automatic "מתודולוגיה" appendix

`docx_builder` (reused, not modified — the whole module's own established convention) already
renders its own flat, ungrouped sources appendix at the very end of every report; these are two
ADDITIVE sections, not a replacement. `eoa.dossier.report._grouped_sources_table(corpus)`: every
registry row classified into one of "יצרן ועלונים / עיתונות ביטחונית וכלכלית / מתחרים / מחקר
ודוחות שוק / פטנטים / מכרזים / אחר" by its own `kind`/`source_kind`/`topic` fields
(`_source_group_he`), one flat table sorted by group then registry number (kept to 4 columns rather
than N variable-length per-group entries, so the report's own fixed section order/count never
varies with how many groups a given run actually populated). `_methodology_entry(corpus, progress,
llm_leg)`: per-topic status/seconds/pages-read (from the SAME live `on_progress` callback
`build_product_dossier` already had — captured into a plain local variable, `_write_job_progress`'s
own side effect left untouched), which topics hit the per-topic time cap
(`settings().dossier.topic_time_cap_s`), vendor/datasheet page counts (`source_kind` ∈
{vendor_official, datasheet}) and MUST-READ count (`topic == "must_read"`) from the registry,
`corpus.datasheets`' own count (LESSONS-1), the LLM leg used, and — honestly, not invented — a
static statement of which languages this pipeline actually searches in today (Hebrew internal DB +
English web search; the LESSONS-fable-dossier "multi-lingual search" item is explicitly a
LESSONS-1/plan.py concern, not yet landed, so this appendix never claims a language coverage that
didn't happen). Both entries render their own honest placeholder when no `corpus` is available at
all (the two optional parameters `_ordered_report_entries` gained).

## 7. Variants (evidence + confidence) and platforms (own table)

`VersionRow` gained `evidence_he` (what supports the variant actually existing — a model field,
distinct from `changes_he`) and `confidence` (deterministic, see item 4). The variants table lost
its `platforms` column (moved out, see below) to stay within the 6-column cap while adding evidence
+ confidence: `["גרסה/דגם", "שנה", "שינויים", "עדות", "ביטחון", "מקור"]`.
`PlatformRow(platform, domain, integration_evidence_he, cites)` — a NEW top-level list, built
deterministically (`eoa.dossier.extract.build_platforms`, NEVER by the model, so there is exactly
one platform list, not two that can drift) from `maturity.platforms_integrated` (bare names) merged
with every distinct `DealRow.platform` a grounded deal actually names (its own cites become the
platform's evidence/citations — richer than the bare maturity list, so a deal-derived row wins the
merge when both name the same platform). Rendered as its own table
(`_platforms_table`/`platformColumns`) and UI section (`#dossier-platforms`).

## Grounding discipline (unchanged, extended)

Every new deterministic step follows the module's existing "additive, independently fail-safe"
convention: a claims_review/timeline row with no surviving citation is dropped, not half-kept; the
pricing_estimate gate drops the WHOLE block rather than rendering a partially-grounded estimate; a
row-confidence computation never raises (pure function over already-grounded `cites`/`source_kind`).
Every drop is logged via the existing `DroppedField`/`dossier.field_dropped` mechanism.

## Files

- `agent/eoa/llm/schemas/product_dossier.py` — `RowConfidenceHe`, `TimelineKind`/`TimelineRow`,
  `AssumptionRow`/`MarketAnchorRow`/`PricingEstimateBlock`, `ClaimVerdict`/`ClaimReviewRow`,
  `GapStatusHe`/`GapTrackingRow`, `PlatformRow`; `confidence`/`evidence_he`/`confidence_level`
  fields on the existing row models; 5 new top-level `ProductDossierOut` fields.
- `agent/eoa/dossier/extract.py` — `_row_confidence_he`, `_ground_version_row`,
  `_ground_claims_review`, `_ground_timeline`/`build_timeline`, `build_platforms`,
  `_ground_pricing_estimate`, wired into `ground_dossier`.
- `agent/eoa/dossier/report.py` — `_platforms_table`, `_timeline_table`,
  `_pricing_estimate_entries`, `_claims_review_table`, `_build_gaps_tracking_raw`/
  `_build_gaps_tracking`/`_gaps_tracking_table`, `_grouped_sources_table`/`_source_group_he`,
  `_methodology_entry`, confidence rendering on `_variants_table`/`_deals_table`/
  `_partnerships_table`/`_competitors_table`, reworked `_compute_outcome_confidence`,
  `_persist`'s new `gaps_tracking_raw` → `data.meta.gaps`, `build_product_dossier`'s captured
  `last_progress` + `_ordered_report_entries(dossier, corpus, progress=, llm_leg=)`.
- `agent/eoa/dossier/spec_render.py` — `CONFIDENCE_LABEL_HE`, confidence merged into
  specifications/performance, added as its own column on `other_specifications`.
- `agent/eoa/llm/prompts/product_dossier_extract.md` — `timeline`/`pricing_estimate`/
  `claims_review` extraction rules.
- `web/src/types/api.ts`, `web/src/api/real.ts` — mirrored types + defensive normalizers for every
  new field (absent/`[]`/`null` on older data, never throws).
- `web/src/pages/DossierDetailPage.tsx`, `web/src/components/dossiers/DossierSpecTable.tsx` — 5 new
  sections, confidence columns on 6 existing tables/sections, nav items.
- `web/src/i18n/dictionaries/{he,en}.ts` — section titles, table columns, confidence/verdict/
  status/timeline-kind label maps, empty-state copy.
- Tests: `tests/unit/test_product_dossier_lessons2.py` (33 new), `tests/unit/
  test_product_dossier_report.py` (`_EXPECTED_ORDER_HE` updated for the 8 new/relocated sections),
  `web/src/pages/DossierDetailPage.test.tsx` (8 new tests + the fixed-order test updated).

## Tests

`PYTHONUTF8=1 PYTHONPATH=agent .venv/Scripts/python.exe -m pytest tests/unit/test_product_dossier_report.py
tests/unit/test_product_dossier_diff.py tests/unit/test_product_dossier_schema.py
tests/unit/test_product_dossier_extract.py tests/unit/test_dossier_vocabulary.py
tests/unit/test_product_dossier_api.py tests/unit/test_product_dossier_services.py
tests/unit/test_product_dossier_corpus.py tests/unit/test_jobs_product_dossier.py
tests/unit/test_product_dossier_lessons2.py -q` → **267 passed**.
`ruff check agent/eoa/dossier/ agent/eoa/llm/schemas/product_dossier.py
tests/unit/test_product_dossier_lessons2.py tests/unit/test_product_dossier_report.py` → clean.

`cd web && npx vitest run` → **527 passed** (64 files, including the updated
`DossierDetailPage.test.tsx`, 21 tests). `npx eslint` on every changed file → clean. `npx tsc
--noEmit -p tsconfig.app.json` → clean. `npm run build` → succeeds (this IS the deploy step per
this repo's own convention — the UI changes are live).

**Known pre-existing, out-of-scope failure** (not caused by this round, not fixed — outside this
lane's file ownership): `tests/unit/test_product_dossier_report.py::
test_build_topics_names_product_in_every_question` fails (`assert 9 == 10`) because
`config/config.yaml`'s `dossier.max_topics: 9` is now one short of `eoa.dossier.plan.TOPICS`'s own
10 entries (the LESSONS-1 lane added a topic without bumping that config value) — a `plan.py`/
`config.yaml` inconsistency, both outside this task's file ownership.

## Offline proof — re-render of `product_dossiers.id=7` (SPECTRO XR run 5)

`scratchpad/rerender_dossier7.py` (temp scratchpad, not committed) loads run 7's PERSISTED
`data`/`sources` (no network/LLM), parses them into the current schema (every new field defaults
cleanly on old JSON), recomputes row confidence purely from each row's own already-persisted
cites/source_kind, rebuilds `timeline`/`platforms` deterministically from that data, and renders
through the exact new `_ordered_report_entries` → `render_markdown` path. Result:
**17 of 27** confidence-bearing rows (specifications+performance+deals) now `"high"`; **2** timeline
rows and **2** platform rows recomputed from the existing deals/variants/maturity (the flattened
document's own pre-existing cross-table row-dedup, `docx_builder.dedupe_rows_across_tables`,
collapses them against matching content already shown in the deals/versions tables earlier in the
SAME document — exactly the same dedup every other section already gets, confirmed via the
script's own "Recomputed: 2 timeline rows, 2 platform rows" print, independent of the final
rendered table); the 21-row sources registry grouped into "יצרן ועלונים" (7)/"עיתונות ביטחונית
וכלכלית" (10)/"מתחרים" (1)/"אחר" (2, the two DB-kind rows); methodology renders MUST-READ (4/7
vendor pages), datasheet count (0 — this run predates the datasheet-hunt lane), and the LLM leg
(`codex:gpt-6-astra`) actually used. `pricing_estimate`/`claims_review`/`gaps_tracking`/LLM-timeline
correctly render their own honest placeholders (this run's extraction predates those fields — a
live rerun, not attempted here, would populate them for real).

Excerpt (specifications row showing the merged confidence column, `## מפרט`):

```
| קבוצה | פרמטר | ערך | יחידה/וריאנט | סוג מקור / ביטחון | מקור |
|---|---|---|---|---|---|
| אופטיקה | קוטר אפרטורה אופטית | 7 אינץ׳ | אינץ׳ | official · גבוה | [3], [7], [8] |
```

Platforms (`## פלטפורמות`, built purely from `maturity.platforms_integrated` + deal platforms):

```
| פלטפורמה | תחום | עדות לשילוב | מקור |
|---|---|---|---|
| מגוון פלטפורמות ימיות | לא נמצא במקורות | מוזכר בעסקה עם כוחות ימיים במדינה באסיה־פסיפיק. | [4], [5], [6], [19] |
| מסוקי IAR | לא נמצא במקורות | מוזכר בעסקה עם הצי הרומני. | [8], [9], [10], [12], [13] |
```

Grouped sources (`## מקורות מקובצים`, excerpt):

```
| קבוצה | # | כותרת | סוג/קישור |
|---|---|---|---|
| יצרן ועלונים | 3 | elbitsystems.com | vendor_official |
| עיתונות ביטחונית וכלכלית | 10 | instro.com | press |
| מתחרים | 21 | militaryembedded.com | press |
```

Methodology (`## מתודולוגיה`):

```
נושאי מחקר: אין נתוני התקדמות זמינים לריצה זו.
עמודי יצרן/עלונים במאגר המקורות: 7 (מתוכם 4 נקראו מראש כ-MUST-READ לפני תחילת המחקר).
עלונים/דפי נתונים שאותרו ונקראו: 0.
שפות חיפוש: עברית (מאגר פנימי) ואנגלית (חיפוש רשת).
רגל מודל (LLM leg): codex:gpt-6-astra.
```
