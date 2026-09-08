# Product Dossier — second live-run fix pass (PD-fix-2)

Date: 2026-09-08
Scope: the five findings from comparing the two live SPECTRO XR dossiers
(`product_key elbit-systems-spectro-xr`, `product_dossiers.id=1` vs `id=2`, same product,
`id=2` run after PD-backend/PD-fix landed) — deal-identity false positives in "מה השתנה", spec/
performance rewording false positives, deals-table cell rendering (date/kind label/region), an
item-derived source's url/title, and a pricing `basis_he` consistency rule. Built without rerunning
either live dossier (read-only inspection of both rows' `data`/`sources` JSON via `DATABASE_URL`,
port 5432 verified, never 5433).

Per this round's file ownership: `agent/eoa/dossier/diff.py` (full), `agent/eoa/dossier/report.py`
(deal/price *cell rendering* only), `agent/eoa/dossier/plan.py` (*source registry item entries*
only), `web/src/components/dossiers/DossierTable.tsx` + the deals/pricing section rendering in
`web/src/pages/DossierDetailPage.tsx`. Finding 5 (pricing `basis_he` consistency "via the
extraction post-check") lives in `agent/eoa/dossier/extract.py`, outside this round's ownership —
left open, see below.

## 1. Deal identity for "מה השתנה" (`agent/eoa/dossier/diff.py`)

The live bug: dossier `id=2`'s $270M and $80M deals were reported as `"עסקה חדשה מאז הסקירה
הקודמת"` even though both already existed in dossier `id=1` — the old key,
`(customer, date, kind)`, broke the moment either deal *gained a date* between runs (a backfilled
`published_at` for the $270M deal, a genuinely-discovered `"2021-06"` for the $80M one).

Fix: a deal's identity is now `(normalized customer, amount, kind)` only —

- **Customer**: `eoa.pipeline.entity_normalize.normalize_name_key` (punctuation-stripped,
  whitespace-collapsed, casefolded — the same key the rest of the pipeline already uses for entity
  name comparisons).
- **Amount**: the already-grounded `amount_value` when present (every *current* deal row has it —
  `eoa.dossier.extract`'s post-check always derives it); a *previous* dossier persisted before that
  field existed falls back to parsing the same `amount` text with the identical rules
  (`eoa.dossier.extract.parse_amount_he`, imported — a pure function, not modified), so both sides
  of the comparison land on the same key for the same published figure regardless of which round
  produced the previous row.
- `date`/`date_kind`/`region_he` never participate in the identity — a rerun routinely refines
  exactly those three fields for an already-known deal.

A date that went from unknown to known for an already-identified deal gets its own honest, narrower
note instead of a fabricated "new deal": `"נוסף תאריך לעסקה עם {customer}: {date}."` — exactly the
task brief's wording. A deal whose only change is `region_he`/`date_kind` churn produces no sentence
at all (nothing changed that a reader needs told).

`tests/unit/test_product_dossier_diff.py` (new): `test_same_deal_with_newly_backfilled_date_not_
reported_as_new_deal`, `test_same_deal_with_only_region_change_not_reported_at_all`,
`test_deal_customer_key_is_case_and_whitespace_insensitive`, `test_deal_key_ignores_previous_run_
missing_amount_value_field` (a raw dict lacking the field, as `id=1`'s own data does), `test_
genuinely_different_amount_is_still_a_new_deal` — every one of these reproduces the exact live
$270M/$80M deal shape from `id=1`→`id=2`.

## 2. Token-overlap "same value" for specs and performance (`agent/eoa/dossier/diff.py`)

The live bug: `'חיישני IMU בסיבים אופטיים על הגימבל'` → `'...המורכבים על הגימבל'` (pure rewording,
same fact) was reported as a spec change under the old exact-string comparison.

Fix: `_values_effectively_same(previous, current)` — an exact (casefold) match is always "same";
otherwise compared by token (Jaccard) overlap, `|intersection| / |union| >= 0.6` counts as the same
value. Applied to `_diff_specifications` (replacing the old `!=` check) and to a **new**
`_diff_performance` (per the brief's "same rule for performance rows" — this dossier's diff never
covered performance at all before this round), checked independently for `claimed_value` and
`tested_or_operational_value` per metric (a rewording of one must not mask a real change in the
other).

Verified against the live IMU example (now correctly produces `[]`) and against a live case that
is a genuine, non-trivial rewording just below the threshold — `id=1`'s `'ביצועי עומס 20 אינץ׳
במארז של 15 אינץ׳'` → `id=2`'s `'ביצועי מטע״ד בגודל 20 אינץ׳ בתוך מארז 15 אינץ׳'` (different
terminology — "עומס"/load vs "מטע״ד"/ordnance — not just a rephrase) scores `4/12 ≈ 0.33 < 0.6` and
is still correctly reported (see §5's recomputed list).

`tests/unit/test_product_dossier_diff.py` (new): `test_spec_reworded_value_not_reported_as_change`
(the live IMU example verbatim), `test_spec_genuinely_different_value_still_reported`, `test_
performance_reworded_claimed_value_not_reported`, `test_performance_genuinely_different_claimed_
value_reported`, `test_performance_new_metric_detected`.

## 3. Deals-table cell rendering (`agent/eoa/dossier/report.py`, deal cells only; `web/src/pages/
DossierDetailPage.tsx`, deals section only)

Rendering-only in both places — no stored value changes.

- **Date, no time/timezone**: a deal's `date` can be a full `"2026-09-02 09:04:00+03:00"` (a
  backfilled `published_at`, `eoa.dossier.extract._ground_deal_row`, not owned by this round) — the
  table now shows a date only. `report.py`'s `_date_only` (a `^\d{4}(-\d{2}(-\d{2})?)?` prefix
  match, never `datetime.fromisoformat`/timezone conversion, so a partial-precision date like
  `"2021-06"` passes through unchanged) and the page's local `dealDateOnly` mirror each other
  exactly. The existing "(תאריך פרסום)" backfill marker is unchanged, just built on the trimmed
  date.
- **Hebrew kind label**: `_deal_kind_label` (`report.py`) reuses `docx_builder._EVENT_KIND_LABELS_HE`
  for `contract_award` (the one kind the two enums share) and adds the deal-only kinds
  (`FMS`/`framework`/`option`/`export_license`); an unrecognised kind falls back to the raw value,
  same convention as every other `_EVENT_KIND_LABELS_HE.get(kind, kind)` call site in this
  codebase. The page's `dealKindLabel` reuses the app's own shared map
  (`eventKindLabel`, `@/components/entities/eventKindLabel`, already `contract_award: "זכייה
  בחוזה"`) plus the identical deal-only extra map, so the docx/md/html renderer and the UI agree on
  the same Hebrew text.
- **Region fallback**: `report.py`'s `_cell(r.country or r.region_he)` was already correct from
  PD-fix round 1; the UI's `dealColumns` "customer" cell did not yet fall back to `region_he` at
  all (the field wasn't even on the frontend type) — fixed.

`tests/unit/test_product_dossier_report.py` (new): `test_date_only_strips_time_and_timezone`,
`test_date_only_preserves_partial_precision_dates`, `test_date_only_passes_through_none_and_non_
date_text`, `test_deal_date_cell_strips_time_from_published_fallback`, `test_deal_kind_label_
reuses_shared_contract_award_label`, `test_deal_kind_label_covers_deal_only_kinds`, `test_deal_
kind_label_falls_back_to_raw_kind_when_unrecognised`, `test_deals_table_renders_hebrew_kind_label_
not_raw_kind`. `web/src/pages/DossierDetailPage.test.tsx` (new): a deals-table test asserting
date-only + "(תאריך פרסום)" + region fallback + Hebrew FMS label together, against the page's
aggregate text (bidi-run splitting means a single-element text match is fragile here).

**Scope note (frontend, additive, same pattern PD-fix round 1 used for `progress`)**: the deals
section literally cannot show `date_kind`/`region_he` without them existing on the frontend at
all — three small, additive changes just outside the literal "DossierTable.tsx + DossierDetailPage
deals/pricing rendering" ownership were required, or the backend fields would be silently dropped:
- `web/src/types/api.ts`: `DossierDealRow` gained optional `date_kind?`/`region_he?` (optional, so
  every existing mock/fixture object literal for this type keeps compiling unchanged).
- `web/src/api/real.ts`: `normalizeDossierDealRow` now maps `date_kind`/`region_he` through
  (defensive, same per-field style as every other `normalizeDossier*` helper in that file).
- `web/src/api/real.test.ts`: one new test, `normalizes a deal row's date_kind/region_he through`
  (including a pre-PD-fix-2 deal row missing both fields, to prove it still doesn't throw).

## 4. Item-derived registry sources (`agent/eoa/dossier/plan.py`)

The reported symptom (source `[1]` printing `url: null` in the API JSON) did not reproduce against
either live row as currently stored — both item-kind registry rows in `id=1`/`id=2` (the Wikipedia
false-positive and the Israel Defense $270M item) carry a real `url` today. The underlying gap is
real, though: `run_plan`'s dedup-by-normalized-URL seed only ever looked at existing `"web"`-kind
registry rows (`if row.get("kind") == "web" and row.get("url")`), never at the DB-derived
`item`/`event`/`patent`/`tender` rows `eoa.dossier.corpus` already seeded. A topic's own web search
re-reading the exact same URL as an already-known DB item (a very plausible case — e.g. a "deals"
investigation re-finding a press item already in `items`) would mint a **second, weaker** `"web"`
registry entry for that URL instead of reusing the item-derived one, and that fresh "web" entry's
`title` falls back to a bare hostname whenever the read itself returns none (`_host(url)`) — see
`id=2`'s own `n=3`..`n=6` rows, all titled just `"elbitsystems.com"`/`"instro.com"`/etc. That is
precisely "an item-derived source not carrying the item's own url/title" — just manifesting as a
title downgrade + wasted registry slot rather than a literal `None`, which the finding's own
"once" wording (a single observed instance) is consistent with.

Fix: the dedup seed now covers **every** registry row with a `url`, any kind — an item/event/
patent/tender row's own (real) title/url is reused for a URL a later topic re-reads, instead of a
second, weaker "web" stub being minted for the identical page. This also brings the code in line
with `run_plan`'s own docstring, which already claimed "a page already cited (same normalized URL,
any topic) is never re-numbered" — previously true only for a prior *web* read, not a DB-item URL.

`tests/unit/test_product_dossier_plan.py` (new): `test_run_plan_reuses_item_derived_registry_row_
for_same_url` — a corpus seeded with the live `id=93` Israel Defense item (real title/url), a topic
investigation reading that exact URL again; asserts no second "web" row is minted, the registry
stays at its original length, and the topic's own citation resolves to the original item row (its
real title, not a hostname fallback).

## 5. Left open — outside this round's file ownership

- **Pricing `basis_he` consistency** (a contract total labelled `basis_he = "היקף חוזה (לא מחיר
  ליחידה)"` consistently) is explicitly "via the extraction post-check" per the task brief — that
  is `agent/eoa/dossier/extract.py`'s `_ground_price_row`, which this round's ownership does not
  include (`report.py` is scoped to *cell rendering* only, `plan.py` to *source registry item
  entries* only — neither covers `extract.py`). Not touched.
- The live dossiers (`product_dossiers.id=1`/`id=2`) were **not** rerun — every fix above is
  verified either against unit fixtures reproducing the exact live shapes, or (§1/§2) by
  recomputing `compute_diff` directly over the two stored rows' real `data` JSON (read-only,
  `DATABASE_URL`, port 5432 verified never 5433) — see the recomputed list below.

## Recomputed `what_changed_he` (id=1 → id=2, read-only, no rerun)

```
python -c "from eoa.dossier.diff import compute_diff; ..." over product_dossiers.data for id in (1, 2)
```

10 sentences (was 11 before this round, with 2 wrong "עסקה חדשה" and 1 wrongly-suppressed-nothing
extra — net: the two deal false positives are gone, replaced by 2 honest "נוסף תאריך" notes; the
IMU rewording false positive is gone; one new, *correct* performance-change sentence appears that
the old code never checked at all):

1. נוסף תאריך לעסקה עם לקוח בינלאומי (לא מזוהה): 2026-09-02 09:04:00+03:00.
2. נוסף תאריך לעסקה עם מדינה באזור אסיה-פסיפיק (לא מזוהה): 2021-06.
3. נתון מחיר חדש שפורסם: כ-80 מיליון דולר (לחוזה כולל (לא צוין מחיר ליחידה)).
4. שינוי בערך המפרט 'ביצועי אופטיקה': ביצועי עומס 20 אינץ׳ במעטפת של 15 אינץ׳ -> ביצועי מטע״ד
   בגודל 20 אינץ׳ בתוך מארז פיזי של 15 אינץ׳ בלבד.
5. פרמטר מפרט חדש שפורסם: חיישנים דיגיטליים = עד 9 חיישנים דיגיטליים המשלבים MWIR, VNIR ו-SWIR.
6. שינוי בערך המפרט 'יכולות לייזר': LTDRF, מצביע לייזר, מאיר לייזר -> מד טווח לייזר בטוח לעין
   (Eye-safe LRF) ומערכת סימון מטרות (Laser Target Marker); תמיכה בסימון NIR תואם משקפי ראיית
   לילה (NVG).
7. פרמטר מפרט חדש שפורסם: יכולת ראייה בתנאי מזג אוויר קשים = שימוש בערוץ SWIR לצפייה בתנאי עשן,
   ערפל ואבק.
8. פרמטר מפרט חדש שפורסם: גלאי רביע = גלאי רביע (Quadrant Detector) לעקיבת נקודת לייזר (Laser
   Spot Tracking).
9. פרמטר מפרט חדש שפורסם: בינה מלאכותית = יכולות AI לזיהוי וסיווג מטרות בזמן אמת.
10. שינוי בערך הביצועים המוצהר 'עומס אופטי במארז קומפקטי': ביצועי עומס 20 אינץ׳ במארז של 15 אינץ׳
    -> ביצועי מטע״ד בגודל 20 אינץ׳ בתוך מארז 15 אינץ׳.

Note (row #10): this sentence is new relative to the old (buggy) stored `what_changed_he` — the
old code never diffed `performance` at all. Its token-overlap score (`4/12 ≈ 0.33`) is well below
the 0.6 "same" threshold, confirming it is correctly reported, not a regression of §2's fix.

The old (buggy) `what_changed_he` stored on `id=2` for comparison, for the record:
"עסקה חדשה מאז הסקירה הקודמת: לקוח בינלאומי (לא מזוהה) בהיקף כ-270 מיליון דולר..." and "...מדינה
באזור אסיה-פסיפיק (לא מזוהה) בהיקף כ-80 מיליון דולר..." (both now gone, replaced by the honest
"נוסף תאריך" notes above), and "שינוי בערך המפרט 'חיישני IMU': ...על הגימבל -> ...המורכבים על
הגימבל" (now correctly suppressed as mere rewording).

## Tests

`tests/unit/test_product_dossier_diff.py`: **20** (was 10, +10) — deal identity (customer/amount/
kind only, date-added note, region/date-kind churn ignored, missing-`amount_value`-fallback,
genuine-amount-change-still-new), token-overlap same-value for specs (live IMU example) and the
new performance diff.
`tests/unit/test_product_dossier_report.py`: **25** (was 17, +8) — `_date_only`, `_deal_kind_label`,
deals-table integration for both.
`tests/unit/test_product_dossier_plan.py`: **14** (was 13, +1) — item-derived registry-row reuse.
`tests/unit/test_product_dossier_*.py` total: **126 passed** (was 107, +19;
`pytest tests/unit/test_product_dossier_*.py -q`).

Regression sweep: `pytest tests/unit/test_product_dossier_*.py tests/unit/test_docx_builder.py
tests/unit/test_api_smoke.py tests/unit/test_app_middleware.py tests/unit/test_deep_search*.py -q`
— **422 passed** (was 404, +18; the diff `test_run_plan_reuses_item_derived_registry_row_for_
same_url` test lives in `test_product_dossier_plan.py`, already counted in the +19 above, hence
+18 not +19 here — one of the new dossier tests overlaps a file already in this sweep's own list).
`ruff check agent/ tests/unit/test_product_dossier_*.py` — clean.

Frontend, `web/src` vitest — **482 tests, 60 files, 0 failed** (`npx vitest run`; was 480 tests, 60
files before this round), including:
- `web/src/api/real.test.ts`: 10 (was 9) — deal row `date_kind`/`region_he` normalization.
- `web/src/pages/DossierDetailPage.test.tsx`: 9 (was 8) — deals-table date-only/kind-label/region
  rendering.

`npm run build` (`tsc -b && vite build`): **clean** (pre-existing large-chunk warning only, no
errors).
