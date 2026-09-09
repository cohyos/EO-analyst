# Product Dossier — fifth live-run fix pass (PD-fix-5)

Date: 2026-09-09
Scope: five findings from comparing `product_dossiers` rows **8** and **11** (`product_key
elbit-systems-spectro-xr`, `product_line targeting_pods`) against
`docs/qa/content_review/LESSONS-fable-dossier.md`'s hand-made reference facts. Row 11 ran AFTER
PD-fix-4 shipped and improved filing (12/21 keyed specs, 5 deals, 4 overflow vs. row 8's 5/16 keyed,
1 deal, 15 overflow) but LOST real facts row 8 had, and several hand-made-dossier targets were still
missed. Built and verified **read-only** against `id=8`/`id=11`'s own persisted `data`/`sources` JSON
(`DATABASE_URL`, port 5432, never 5433) — no new dossier run, no LLM/network call. No dossier job was
started; per the operator's standing instruction the lead runs it.

Per this task's file ownership: `agent/eoa/dossier/extract.py`, `agent/eoa/dossier/plan.py`, plus
`tests/unit/test_product_dossier_extract.py` and `tests/unit/test_product_dossier_plan.py`.
`datasheet.py`/`report.py`/`vocabulary.py`/`config/spec_vocabulary.yaml` were inspected but needed no
change for this pass (see each item's own root-cause note for why).

## 1. Carry-forward: overflow coverage + datasheet/vendor value wins over a weaker one

**Live bug.** Run 8 (unfixed, predates PD-fix-4's overflow-promotion fix in its own persisted data)
had `weight="51 ק״ג"`, `power_consumption="500W"`, two laser-wavelength facts, an MWIR spectral band,
three FOV counts, and an imaging-channel count sitting in `other_specifications` (`key=""`), cited to
the vendor's own `air-space/airborne-c4isr/.../spectro-xr` page (n=24, `source_kind=vendor_official`)
and the Scribd brochure copy (n=22, `source_kind=press`). Run 11 re-read the SAME Scribd document
(now n=33) but the model extracted a *different, less precise* set of values from it this time —
`weight="52 ק״ג בתצורת החיישנים המרבית"` (imprecise), no `power_consumption`, no laser wavelengths.
`carry_forward_missing_specs` (PD-fix-4, item B.3) only ever read a previous run's own **keyed**
`specifications`/`performance` — it never looked at `other_specifications`, so it found nothing to
carry for these facts (they were unkeyed in row 8's own persisted record), and even where it *did*
carry, it only ever filled a row whose CURRENT value was empty — a weaker current value that already
existed (like run 11's own imprecise "52 ק״ג") was never replaced.

**Fix (`agent/eoa/dossier/extract.py`).** `_prev_overflow_candidates` re-matches every row in the
previous run's own `other_specifications` to today's vocabulary with the SAME matcher
`_promote_overflow_rows` already applies to THIS run's own overflow rows (label → value → units
heuristic); `carry_forward_missing_specs` now checks a previous KEYED row first, falling back to this
overflow-matched candidate for the same key. Separately, `_carry_forward_decision` (shared by both
the null-fill and the new upgrade path) now also fires when the CURRENT row already has a value: a
coarse source-kind ranking (`_SOURCE_KIND_STRENGTH` — `datasheet=3 > vendor_official/official/
brochure/contract/tender/budget=2 > trade_press/press/article/item=1 > reference/forum/other=0`,
resolved from each citation's own REGISTRY entry via `_registry_kind_by_n`, previous run's registry
and this run's registry each read independently) decides whether the previous run's value should WIN;
a win is logged as `dossier.value_kept_from_datasheet` (key, both values, both strengths). A vague
previous value (`_is_vague_value_he`) is still never resurrected, exactly as before.

Before/after (offline replay, `carry_forward_missing_specs` over `id=11`'s own persisted dossier with
`corpus.previous = id=8`'s raw row, `corpus.registry = id=11`'s own persisted `sources`):

| | before (persisted, run 11) | after (fixed carry-forward) |
|---|---|---|
| keyed specifications+performance filled | 14 | **16** |
| rows carried/upgraded this pass | — | 4 (2 new fills: `power_consumption`, one `field_of_view` row; 2 upgrades: `weight` 52→51 ק״ג, `envelope_dimensions` refined — both logged `dossier.value_kept_from_datasheet`) |
| `"51 ק״ג"` present anywhere in specs/performance/overflow | ✗ | ✓ |
| `"500W"` present | ✗ | ✓ |
| a `"2 FOV"` fact present | ✗ | ✓ |

The laser-wavelength/MWIR-band/remaining-FOV facts did **not** recover: run 11's OWN
`laser_designator_illuminator`/`laser_rangefinder`/`detector_type` rows already carry a value cited to
a `vendor_official` page (n=7, `spectro-maritime`) or the same Scribd document (n=33, `press` — equal
strength to row 8's own citation for the matching fact) — per the strength rule this is correctly a
**no-op**, not a regression: a previous weaker/equal-strength value must never downgrade an
already-adequately-sourced current one. This is the intended behavior of the rule, not a gap in it.

Tests (`test_product_dossier_extract.py`): `test_carry_forward_covers_previous_overflow_row_matched_
by_label`, `test_carry_forward_replaces_weaker_current_value_with_stronger_previous_datasheet_value`,
`test_carry_forward_does_not_downgrade_when_current_source_is_at_least_as_strong`.

## 2. Datasheet placement/priority + bounded key-retention re-ask

**Live bug.** `context_he` (every research topic's own context) already folds in both the MUST-READ
vendor-page blocks and the hunted-datasheet blocks BEFORE the topic loop starts — so the mechanism
described in the original brief ("place the datasheet text in every spec-ish topic's context") was
already structurally true. The actual mechanism that dropped the spec-bearing vendor page: row 8 (the
previous run) had **8** distinct `vendor_official` URLs in its own persisted `sources`, but
`_MUST_READ_URL_CAP` was **6** — n-order (registration order, dominated by 6 deal-announcement press
releases read earliest) truncated exactly the two pages that carry the full spec table
(`usv-payloads/spectro`, n=20, and `air-space/.../spectro-xr`, n=24) before run 11's MUST-READ phase
ever re-fetched them. Confirmed directly: `air-space/.../spectro-xr` DOES appear in run 11's own
registry (n=17) — but under topic `platforms_and_programmes` (found live during that topic's own
search), never under `topic="must_read"`, proving it missed the pre-loop MUST-READ pass entirely.

**Fix (`agent/eoa/dossier/plan.py`).** `_previous_vendor_official_urls` now orders the previous run's
own `vendor_official` URLs with every URL already known to have carried a real
specifications/performance/`other_specifications` VALUE last run (`_previous_spec_bearing_ns`) FIRST,
generic press/announcement URLs after — `_MUST_READ_URL_CAP` truncates the (now correctly ordered)
tail instead of an arbitrary n-ordered one. The cap itself is also raised `6 → 8` (SPECTRO XR alone
needed all 8) as defense in depth alongside the ordering fix.

**Fix (`agent/eoa/dossier/extract.py`, new post-check).** Independently of the placement/priority fix,
`apply_datasheet_key_retention` (wired into `build_dossier` after `apply_fact_retention`) is a second,
narrower bounded re-ask: for every still-null REQUIRED vocabulary key, `_datasheet_snippet_for_param`
scans ONLY `corpus.datasheets`' own already-fetched text (never a new web/network call) for that key's
own label/synonyms; a hit becomes one `{"key","n","snippet"}` entry (capped at 8), logged
`dossier.datasheet_key_missing`, and `reask_datasheet_keys` issues exactly one follow-up structured
call scoped to those snippets alone — grounded through the SAME `_ground_spec_row`/
`_ground_performance_row` post-checks as every other row, filling only a key still null afterward.

Before/after (`_previous_vendor_official_urls`, offline, over `id=8`'s own 8 `vendor_official` URLs —
`data.other_specifications`/`data.specifications` supply the "spec-bearing" signal):

| | before (n-order) | after (spec-bearing-first) |
|---|---|---|
| position of `usv-payloads/spectro` (n=20) | 7th of 8 | before every non-spec-bearing URL |
| position of `air-space/.../spectro-xr` (n=24) | 8th of 8 | before every non-spec-bearing URL |
| both survive a cap of 6 | ✗ (both truncated) | ✓ |

Tests (`test_product_dossier_plan.py`): `test_previous_vendor_official_urls_prioritizes_spec_bearing_
page_over_press_release`, `test_previous_vendor_official_urls_spec_bearing_page_survives_the_cap`,
`test_previous_vendor_official_urls_stable_order_when_none_are_spec_bearing`.
`test_product_dossier_extract.py`: `test_find_missing_datasheet_keys_finds_required_null_key_in_
registered_datasheet_text`, `test_find_missing_datasheet_keys_skips_a_key_already_filled`,
`test_apply_datasheet_key_retention_fills_null_required_key`, `test_apply_datasheet_key_retention_no_
op_when_no_datasheets_registered`.

## 3. Deals: date from the cited page's own text; country/region independent of customer

**Live bug.** All 5 deals in run 11 have `date: null` even though every cited press release names its
own publish date in its own text (Sep 2016, Jun 2021, Nov 2022, Dec 2022, Jun 2023). Root cause:
`_ground_deal_row`'s date backfill only ever reads `published_by_n` — built purely from a registry
row's own `published_at` field — but a press release fetched as a plain `"web"`-kind source (via
MUST-READ or a topic's own search, not a DB-native `"item"`/`"event"` row) NEVER carries a
`published_at` at all; only a DB-native row does. `country`/`region_he`, on inspection, were already
independent of `customer` in both `_ground_deal_row` and `eoa.dossier.report`'s own `_cell(r.country
or r.region_he)` deals-table renderer — the task's premise item 3 raised was already true structurally
(no code change needed there; locked in with a regression test).

**Fix (`agent/eoa/dossier/extract.py`).** `_ground_deal_row` gets a second, textual fallback — only
when the registry has no `published_at` for any of the deal's own citations: the SAME date-hint scanner
`build_timeline` already uses (`_finding_date_hint` — dd/mm/yyyy, "<day> ב<month> <year>", or a bare
year, lifted verbatim, never fabricated/normalized) runs over the deal's own already-grounded cited
text; a hit sets `date`/`date_kind="published"` exactly like the registry-metadata path.

Before/after (unit-level — the original run's own fetched page TEXT is ephemeral, never persisted, so
this is demonstrated with the same mechanism against representative text, not a replay of `id=11`'s
own literal bytes):

```
DealRow(customer="Some AF", amount="כ-80 מיליון דולר", date=None, cites=[1])
registry n=1: kind="web", published_at=None, title="הודעת חברה מ-2 ביוני 2021 על חוזה בכ-80 מיליון דולר."
before: date=None, date_kind="deal"
after:  date="יוני 2021", date_kind="published"
```

Tests (`test_product_dossier_extract.py`): `test_deal_date_backfilled_from_cited_text_when_no_
registry_published_at`, `test_deal_country_and_region_kept_when_customer_unknown` (regression lock for
the already-true independence).

## 4. Variants: config-word/family-anchor scan + cross-run carry-forward

**Live bug.** Run 11 kept 0 variants even though its OWN `gaps_tracking` already names "SPECTRO XR
CU" with real citations `[15, 13, 33]` — confirmed live: calling `build_variant_mentions` directly
against `id=11`'s own persisted dossier (unmodified code) finds it correctly, so the SAME-run scan
mechanism (PD-fix-4, item 5) itself works; the persisted row simply predates it having actually run
(the live build process likely hadn't picked up the code change yet — outside this task's read-only
scope to confirm further). Two real gaps beyond that: the scan's narrow ALL-CAPS-suffix rule never
matches a lowercase configuration word ("SPECTRO XR maritime"), and never matches a variant named off
just the product's shorter "family" word ("SPECTRO CU" contains no substring match for the full
`product_name` "SPECTRO XR" at all). Variants were also never carried forward across runs at all,
unlike keyed specs.

**Fix (`agent/eoa/dossier/extract.py`).** `_variant_anchor_names` adds the product name's own first
word as a second anchor when it's >=4 chars and differs from the full name. `_variant_mentions_from_
text` now also matches a curated, case-insensitive deployment-domain word list right after any anchor
(`_VARIANT_CONFIG_WORDS_HE`/`_EN`: ימי/אווירי/יבשתי, maritime/airborne/land/naval/shipborne/ground).
Guard against a false positive the family anchor introduces on its own: a code-suffix match is
dropped when its own FIRST token is itself one of the full product name's words (catches "SPECTRO" +
"XR CU" → would otherwise mint "SPECTRO XR XR CU" out of the text "...SPECTRO XR CU..." itself).
`build_variant_mentions(dossier, corpus=None)` now also scans `corpus.datasheets`' own registered text
when a `corpus` is given (backward compatible — every existing single-argument call site is
unaffected). New `carry_forward_missing_variants` (same shape as `carry_forward_missing_specs`):
every previous-run variant whose `name` isn't already present (case-insensitive) and whose citations
still resolve into this run's registry is carried forward, tagged, wired into `build_dossier`.

Before/after:

```
_variant_mentions_from_text("SPECTRO XR", "...SPECTRO XR maritime לשימוש ימי.", [7], set())
before: []                         after: ["SPECTRO XR maritime"]

_variant_mentions_from_text("SPECTRO XR", "הכינוי SPECTRO CU מוזכר ללא פירוט.", [9], set())
before: []                         after: ["SPECTRO XR CU"]
```

Cross-run carry-forward found nothing to carry for `id=11` specifically (row 8's own persisted
`variants_and_versions` is itself empty — nothing to carry from), so the mechanism is verified via
unit tests (below) rather than a live before/after on these two rows.

Tests (`test_product_dossier_extract.py`): `test_variant_config_word_maritime_captured_as_version_
row`, `test_variant_base_name_anchor_captures_family_suffix_variant`, `test_variant_family_anchor_
does_not_re_match_full_product_name_as_fake_variant`, `test_variant_scan_covers_registered_datasheet_
text_when_corpus_given`, `test_variant_scan_without_corpus_ignores_datasheets`, `test_carry_forward_
missing_variants_fills_from_previous_run`, `test_carry_forward_missing_variants_skips_name_already_
present`, `test_carry_forward_missing_variants_no_op_with_no_previous_dossier`.

## 5. Confidence: registry-kind-aware row confidence

**Live bug.** `id=11`'s dossier-level confidence is **0.36** with 12 keyed rows filled — several of
them citing the vendor's OWN page (`optical_aperture`, `platforms`, `laser_spot_tracker`,
`laser_designator_illuminator`, `ai_target_recognition`, `pod_class_diameter` — 6 of the 12 already
"high" per the stored data) but several MORE citing the same vendor/press pages stayed "medium" (e.g.
`weight`, `envelope_dimensions`, `detector_type`, all cited to n=33, `source_kind=vendor_official`'s
own sibling in the registry — wait: n=33 is `source_kind="press"` for the Scribd copy specifically, so
these three are legitimately medium; the actual bug is elsewhere). Root cause, found by tracing
`_row_confidence_he`: it only ever checked the ROW's own model-authored `SourceKind` field (`SpecRow.
source_kind`/`PriceRow.source_kind` — schema default `"other"`, and the extraction prompt never asks
the model to classify its own citation) — never the REGISTRY's own classification of the CITED URL
itself (`vendor_official`/`datasheet`, computed and persisted every run by `eoa.dossier.plan.classify_
web_source`/`eoa.dossier.datasheet`, but never consulted here). Worse, `PerformanceRow`/`DealRow`/
`CompetitorRow`/`PartnerRow`/`VersionRow` have NO schema-level `source_kind` field at all — none of
these row kinds could ever reach "high" via a single citation, no matter how authoritative it was.

**Fix (`agent/eoa/dossier/extract.py`).** `_row_confidence_he` takes an optional `kind_by_n` (this
run's own `corpus.registry`, `n -> source_kind`, `_registry_kind_by_n`); a row is "high" when EITHER
its own schema-level `source_kind` says so (unchanged) OR any surviving citation resolves to a
registry row whose OWN `source_kind` is `vendor_official`/`datasheet`
(`_HIGH_CONFIDENCE_REGISTRY_KINDS`) OR (unchanged) it carries >=2 citations. `kind_by_n` is computed
once in `ground_dossier` and threaded into every row-confidence call site: specifications,
performance, deals, competitors, partnerships, variants — `kind_by_n=None` (the default) preserves
prior behavior exactly for any caller that doesn't pass it.

Before/after (`_row_confidence_he`, single citation to a `vendor_official` registry row, schema-level
`source_kind` left at default):

| | old rule | fixed rule |
|---|---|---|
| `_row_confidence_he([1], None, None)` | `"medium"` | — |
| `_row_confidence_he([1], None, kind_by_n={1: "vendor_official"})` | — | `"high"` |

Recomputing `id=11`'s own already-persisted filled spec/performance rows against its own registry's
`kind_by_n` (offline, no re-run): **6 high / 3 medium (persisted)** → **11 high / 3 medium / 0 low**
of the 14 filled rows — the weighted score over just these rows moves from the persisted mix toward
**0.74** (specs+performance only; the full dossier-level 0.36 also weighs deals/variants/competitors/
partnerships, most of which are thin or absent in `id=11`, so a live rerun's actual dossier-level
number would land between the two, not jump straight to 0.74 — reported here as the row-confidence
half's own honest effect size, not a promised final score).

Tests (`test_product_dossier_extract.py`): `test_spec_row_confidence_high_for_single_vendor_official_
citation`, `test_spec_row_confidence_stays_medium_for_single_press_citation` (regression guard),
`test_performance_row_confidence_high_for_single_datasheet_citation`, `test_deal_row_confidence_high_
for_single_vendor_official_citation`, `test_variant_row_confidence_high_for_single_vendor_official_
citation`.

## Verification summary (offline, `id=8`/`id=11`, no new dossier run)

| | run 11 before (persisted) | run 11 after (fixed code, offline replay) |
|---|---|---|
| keyed specifications+performance filled | 14 | 16 |
| `"51 ק״ג"` / `"500W"` / a `"2 FOV"` fact present | all ✗ | all ✓ |
| deals | 5 | 5 |
| deals with `date` set | 0 | 0 (persisted-row replay only — the fix needs the original page TEXT, which is ephemeral/not persisted; demonstrated via unit test above with representative text instead) |
| variants (same-run scan, `build_variant_mentions` over `id=11`'s own data) | 0 (persisted) | 1 (`SPECTRO XR CU`, cites=[15, 13, 33]) — the mechanism itself is unchanged from PD-fix-4 and already worked when called directly; item 4's own fix widens what it can additionally catch (config words, family anchor) and adds cross-run carry-forward |
| high-confidence filled spec/performance rows (registry-kind-aware recompute) | 6 (persisted) | 11 |

## Test run

```
PYTHONUTF8=1 PYTHONPATH=agent .venv/Scripts/python.exe -m pytest \
  tests/unit/test_product_dossier_extract.py tests/unit/test_product_dossier_plan.py \
  tests/unit/test_dossier_datasheet.py tests/unit/test_product_dossier_report.py \
  tests/unit/test_product_dossier_lessons2.py tests/unit/test_dossier_vocabulary.py -q
```
266 passed (25 new tests added: 22 in `test_product_dossier_extract.py`, raising it 80 → 102; 3 in
`test_product_dossier_plan.py`, raising it 52 → 55; `test_dossier_datasheet.py`/
`test_product_dossier_report.py`/`test_product_dossier_lessons2.py`/`test_dossier_vocabulary.py`
unchanged, all still green as a regression check). `ruff check` on every touched file: clean.
Broader regression sweep
(`test_jobs_product_dossier.py`, `test_product_dossier_api.py`, `test_product_dossier_corpus.py`,
`test_product_dossier_diff.py`, `test_product_dossier_schema.py`, `test_product_dossier_services.py`):
124 passed, no regressions.
