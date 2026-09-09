# Product Dossier — fourth live-run fix pass (PD-fix-4)

Date: 2026-09-09
Scope: five findings from the SPECTRO XR rerun (`product_key elbit-systems-spectro-xr`,
`product_dossiers.id=8`, codex leg, 19 topics, 25 sources, `product_line="targeting_pods"`) —
un-keyed overflow facts that had real vocabulary matches, an under-extracted deals table, a
collapsed dossier-level confidence score, one junk PDF source, and a thin timeline/empty variants
table. Built without rerunning the live dossier (read-only inspection of `id=8`'s `data`/`sources`
JSON via `DATABASE_URL`, port 5432, never 5433). Each fix was verified offline by re-running the
affected, now-fixed deterministic post-check function directly against `id=8`'s own stored
`data`/`sources` — no new dossier run, no LLM/network calls — via a throwaway script in the
scratchpad dir; per-defect before/after counts are printed below.

Per this task's file ownership: `agent/eoa/dossier/**` (extract, report, datasheet, vocabulary) and
`config/spec_vocabulary.yaml` (additive synonym entries only — no key renamed/removed/added), plus
`tests/unit/test_product_dossier_extract.py`, `test_dossier_vocabulary.py`,
`test_product_dossier_report.py`, `test_product_dossier_lessons2.py`, `test_dossier_datasheet.py`.

## 1. Overflow-row promotion (`agent/eoa/dossier/extract.py`, `config/spec_vocabulary.yaml`)

The live bug: 15 real, cited facts (weight, envelope diameter/height, average power, an MWIR
spectral band, three per-channel FOV counts, two laser lines) sat in `other_specifications` with
`key: ""`, while `specifications` carried a null row for the matching vocabulary key right next to
them. Root cause: `_finalize_other_specifications` ran `match_key_by_synonym` against an overflow
row's own `parameter_he` purely as a **non-destructive QA hint** (logged, never reclassified), and
never checked the row's own `value` text or applied any units/number heuristic at all.

Fix, new `apply_vocabulary` step 0 (`_promote_overflow_rows`, runs before the existing steps 1-5):
`_match_overflow_key` tries `match_key_by_synonym` against `parameter_he`, then against `value`,
then falls back to `_overflow_unit_heuristic_key` — a narrow regex heuristic (`ק"ג`/`kg` → weight;
`מ"מ`/`mm` + "קוטר"/"גובה" wording → envelope_dimensions; a bare `W`/"וואט" → power_consumption;
`µm`/`nm` + "לייזר"/"laser" + "מציין/designator" or "מד טווח/rangefinder" wording → the two laser
keys; `FOV`/"שדה/שדות ראייה" → field_of_view). A match is **actually promoted** — appended to
specifications/performance with the real key set, its own value/cites/confidence untouched — and
flows through the existing table-relocation/canonical-label/dedup-merge/required-backfill steps
unchanged. `_overflow_variant_hint` tags a promoted row (קוטר/גובה, per-channel, PRF vs.
wavelength-description) whenever two genuinely different overflow facts key to the same vocabulary
entry, so the same-key/same-variant dedup step never silently collapses one away (caught live during
verification: a laser PRF fact and a separate laser-wavelength fact both key to
`laser_rangefinder`). Only a row matching nothing at all (label, value, AND the heuristic) stays in
`other_specifications`.

`config/spec_vocabulary.yaml` (additive only): `envelope_dimensions` +`קוטר`/+`גובה`/+`height`;
`laser_designator_illuminator` +`מציין לייזר`/+`designator`; `laser_rangefinder` +`מד הטווח`/
+`rangefinder`; `field_of_view` +`שדות הראייה` (the definite-article phrasing three of run-8's own
FOV-count rows used, which the existing `שדה ראייה`/`שדה-ראייה` synonyms don't substring-match).
`weight`/`power_consumption`/the MWIR band's `detector_type` match already worked via existing
synonyms once actually promoted — no synonym addition needed for those.

Before/after (offline replay, `apply_vocabulary` over `id=8`'s stored specs/performance/overflow):

| | before (persisted) | after (fixed code) |
|---|---|---|
| `other_specifications` rows | 15 | 3 |
| rows promoted this pass | — | 12 |

The 3 still-unmatched rows (laser-payload count, spotter-channel count, imaging-channel count) have
no corresponding vocabulary key at all under `common`+`targeting_pods` — correctly left as overflow,
not force-matched. One live nuance found during verification: the MWIR-band row promotes to
`detector_type` (its "MWIR" synonym is declared earlier, in `common`, than
`mws_eo.spectral_band_coverage`'s own later "MWIR" synonym) — a pre-existing vocabulary ambiguity
this fix surfaces (by actually promoting) rather than introduces; also, `spectral_band_coverage`
itself isn't even part of `targeting_pods`' effective vocabulary, so it was never a reachable target
for this run regardless.

Tests (`test_product_dossier_extract.py`): `test_overflow_weight_row_promoted_via_existing_label_
synonym`, `test_overflow_diameter_and_height_promoted_as_distinct_envelope_variants`,
`test_overflow_average_power_promoted_via_label_synonym`, `test_overflow_mwir_band_promoted_via_
existing_mwir_synonym`, `test_overflow_fov_counts_promoted_and_kept_distinct_per_channel`,
`test_overflow_laser_lines_promoted_to_designator_and_rangefinder_respectively`,
`test_overflow_prf_and_wavelength_facts_for_same_laser_key_both_survive`,
`test_overflow_row_with_no_match_at_all_stays_in_other_specifications`, plus the updated
`test_other_specification_matching_known_synonym_is_promoted_out_of_overflow` (was
`..._is_flagged_but_not_reclassified` pre-fix — rewritten for the new behavior).
`test_dossier_vocabulary.py`: `test_envelope_dimensions_matches_diameter_and_height_hebrew_
synonyms`, `test_laser_designator_illuminator_matches_designator_gloss`, `test_laser_rangefinder_
matches_rangefinder_gloss`, `test_field_of_view_matches_definite_article_fov_count_phrasing`.

## 2. Deal under-extraction (`agent/eoa/dossier/extract.py`)

The live bug: `id=8` kept exactly ONE deal (`amount="יותר מ־90 מיליון דולר"`, `date=null`,
`customer=null`) even though the registry itself carried separate press-release sources naming the
~80M/~90M/~270M contract awards (sources 4/5/6/21/23/25) — the model's own "deals" topic simply
under-extracted them.

Fix: `_deal_candidates_from_registry` builds one `DealRow` candidate per registry source whose own
text (title+summary, the same `registry_text` the row-level grounding already builds) carries BOTH
a monetary figure (`_DEAL_NUM_RE` + `parse_amount_he`, reused as-is) AND an award/contract keyword
(`awarded|contract|חוזה|זכתה|זכה|נחתם|supply agreement|purchase order`) — `date` backfilled from the
source's own `published_at` exactly like the existing grounding rule already does for a model row.
`_merge_deal_candidates` appends a candidate only when none of its own `cites` is already covered by
an existing model-authored deal (never duplicates a press release the model already turned into a
proper row). `ground_dossier` runs this merge BEFORE `_ground_deal_row`, so every existing grounding
rule (customer normalization, amount-digit-grounding, region/country split, confidence) still
applies to every candidate exactly like a model row — this only guarantees the candidate SET is
complete, never bypasses a check.

Before/after (offline replay — registry_text approximated from each source's own `title` + URL path
slug, since the original run's page-read summaries are ephemeral and not persisted between runs;
see caveat below):

| | before (persisted) | after (candidates, pre-grounding merge) |
|---|---|---|
| deals | 1 | 6 |

Caveat: this offline check used URL-slug/title text as a stand-in for the real (ephemeral, not
persisted) page summaries the original live run actually read — the informative slugs themselves
(e.g. `.../elbit-systems-awarded-contract-over-90-million-supply-asia-pacific-country-spectrotm-
xr`) already carry an amount + award keyword, so the mechanism visibly fires and each source keys to
a real, distinct dollar figure; the exact post-grounding row count on a fresh live run may differ
slightly (grounds against the real page text, not the URL slug).

Tests (`test_product_dossier_extract.py`): `test_deal_candidate_built_from_registry_press_release_
with_award_keyword_and_amount`, `test_deal_candidate_not_duplicated_when_model_already_cited_same_
source`, `test_deal_candidate_not_built_without_award_keyword`, `test_deal_candidate_multiple_press_
releases_each_yield_own_deal`.

## 3. Confidence formula rebalance (`agent/eoa/dossier/report.py`)

The live bug: dossier-level confidence collapsed to **0.11** — the pre-fix formula was "share of
`high`-confidence rows over EVERY confidence-bearing row, placeholder/null rows included" — a run
with 25 sources (8 primary/vendor) and dozens of filled rows still scored near-zero because most
rows individually only reached medium/low.

Fix: `_compute_outcome_confidence` (now takes an optional `corpus` param) computes a **weighted
score** (`high=1.0, medium=0.6, low=0.3`) over **filled rows only** (`_row_confidence_values` now
filters to a row with a real `value`/`claimed_value`/`amount or customer`/`name`/`partner`/`product`
— a `_backfill_required` placeholder is never counted either way), floored at **0.3** whenever the
run has `>= 5` filled rows AND `>= 2` primary/datasheet sources in its own registry
(`_primary_source_count`: `reliability == "primary"` or `source_kind == "datasheet"`). The formula
is documented in the methodology appendix (`_methodology_entry`, new closing line) so a reader of
the report itself can see it, not just the code.

Before/after (offline replay, `_compute_outcome_confidence` over `id=8`'s stored dossier + registry):

| | before (persisted) | after (fixed formula) |
|---|---|---|
| confidence | 0.11 | 0.62 |
| filled confidence-bearing rows | — | 8 |
| primary/datasheet sources | — | 9 |

Tests (`test_product_dossier_report.py`): `test_placeholder_rows_are_excluded_from_the_weighted_
confidence_score`, `test_confidence_floored_at_0_3_for_well_sourced_run_with_mostly_low_medium_
rows`, `test_confidence_not_floored_without_enough_primary_sources`. Pre-existing
`test_dossier_level_confidence_is_weighted_share_of_high_rows`
(`test_product_dossier_lessons2.py`) updated: one high + one medium row now averages to 0.8 (weighted
score), not 0.5 (pre-fix share-of-high).

## 4. Junk PDF source (`agent/eoa/dossier/datasheet.py`)

The live bug: source `[9]` is a German hospital incident-reporting PDF
(`ukw.de/.../CIRS-Handlungsempfehlung.pdf`, "Meldung von kritischen Ereignissen im Krankenhaus")
registered as `source_kind="datasheet"` — the hunt downloaded it because it merely *looked*
datasheet-ish to a generic search hit, without ever checking its own content against the product.

Fix: `_pdf_is_relevant(text, url, *, product_name, aliases, vendor_domain, catalog_domains)` — a
downloaded PDF is only registered when its own extracted text names the product (exact name or a
known alias, case-insensitive substring) OR its URL host is the vendor's own domain or a known
EO/IR catalogue domain. `hunt_datasheets` calls this right after `fetch_pdf` and, on a miss, logs
`dossier.pdf_rejected` (url + reason) and moves on to the next candidate instead of appending to
`results`.

Before/after (offline check, `_pdf_is_relevant` against source 9's own persisted `title`/`url` —
the full extracted text isn't persisted either, but the title alone is already conclusively
irrelevant):

```
source 9: url=https://www.ukw.de/fileadmin/uk/qm/07-07-25-CIRS-Handlungsempfehlung.pdf
source 9: title=Microsoft Word - 07-07-25-CIRS-Handlungsempfehlung.doc
_pdf_is_relevant(...) = False  -> would now be rejected + dossier.pdf_rejected logged
```

Tests (`test_dossier_datasheet.py`): `test_pdf_is_relevant_true_when_text_names_the_product`,
`test_pdf_is_relevant_true_when_text_names_an_alias`, `test_pdf_is_relevant_true_for_vendor_domain_
even_without_product_name_in_text`, `test_pdf_is_relevant_true_for_known_catalog_domain`,
`test_pdf_is_relevant_false_for_cirs_handlungsempfehlung_regression` (the exact live case),
`test_hunt_datasheets_rejects_irrelevant_pdf_and_logs_pdf_rejected`, `test_hunt_datasheets_keeps_
relevant_pdf_after_rejecting_an_irrelevant_one`.

## 5. Thin timeline / empty variants (`agent/eoa/dossier/extract.py`)

The live bug: `id=8` kept only 1 timeline row (a programme deal) and 0 variants, even though its own
already-grounded `risks_and_gaps_he`/`gaps_tracking` sentences explicitly named dates (a 2016
brochure/publish date, a 2 June 2021 press-release mention) and a variant name ("SPECTRO XR CU")
that the structured deals/timeline/versions extraction never turned into a row of its own — the
model's own notes explicitly said it found the sighting but couldn't promote it to a full row.

Fix: `build_timeline` now also folds in `_timeline_rows_from_findings` — every `risks_and_gaps_he`/
`gaps_tracking` sentence that carries BOTH a real citation and a recognizable date
(`_finding_date_hint`: dd/mm/yyyy, "<day> ב<month> <year>", or a bare year) becomes its own
`kind="milestone"` row, date lifted verbatim, `cites` reused as-is — never invented. Separately,
`build_variant_mentions` scans `risks_and_gaps_he`/`gaps_tracking`/`bd_implications_he` for the
product's own name immediately followed by a short, distinctly-capitalized suffix token (a real
model/variant code, e.g. "CU"/"NG"/"II" — deliberately narrow: an ordinary lower-case word never
matches) and turns each sighting into its own `VersionRow` (`name`, `evidence_he` = the sentence,
`cites` copied verbatim) — deduped against variant names the "versions" topic already produced.
`ground_dossier` folds `build_variant_mentions`'s output into `variants_and_versions` before
`build_timeline` runs, so both fixes compose correctly.

Before/after (offline replay, `build_timeline`/`build_variant_mentions` over `id=8`'s stored
dossier):

| | before (persisted) | after (recomputed) |
|---|---|---|
| timeline rows | 1 | 4 |
| variants | 0 | 1 (`SPECTRO XR CU`, cites=[18, 20, 25]) |

New timeline rows: a 2016-09-12 price-publish-date milestone (cites=[17]), a 2016 brochure-data
milestone (cites=[22]), and a June 2021 press-release-mention milestone (cites=[15, 16, 17]) — all
alongside the pre-existing 2022 Romania/Watchkeeper X programme-deal row.

Tests (`test_product_dossier_extract.py`): `test_timeline_includes_dated_risk_sentence_even_
without_a_structured_deal_row`, `test_timeline_ignores_uncited_or_undated_findings`,
`test_variant_named_in_risk_sentence_is_captured_as_version_row`, `test_variant_mention_not_
duplicated_when_already_in_versions_table`, `test_variant_mention_requires_citation`.

## Test run

```
PYTHONUTF8=1 PYTHONPATH=agent .venv/Scripts/python.exe -m pytest \
  tests/unit/test_product_dossier_extract.py tests/unit/test_dossier_vocabulary.py \
  tests/unit/test_product_dossier_report.py tests/unit/test_product_dossier_lessons2.py \
  tests/unit/test_dossier_datasheet.py -q
```
189 passed. `ruff check` on every touched file: clean. Broader regression sweep (corpus/diff/plan/
schema/services/api/jobs dossier test files): 176 passed, no regressions.
