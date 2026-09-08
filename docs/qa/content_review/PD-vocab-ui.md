# Spec vocabulary UI — PD-vocab-ui

Date: 2026-09-09
Scope: `web/src/**` (grouped spec table, comparison view/page, types, api client + mocks, i18n)
plus `e2e/tests/24-dossiers.spec.ts` additions. Built against `docs/PLAN_SPEC_VOCABULARY.md`
lane (c), §5. The backend lane (PD-vocab-extract, `agent/eoa/dossier/vocabulary.py` +
`extract.py`/schema/prompt changes) had **not landed** at the time this was built — no
`vocabulary.py`, no `key` field on `SpecRow`/`PerformanceRow`, no `other_specifications` on
`ProductDossierOut`. Everything below is built against typed fixtures/mocks and made tolerant of
both shapes: a keyed row (once the backend ships it) and today's legacy free-named row.

## What "tolerant of both shapes" means concretely

- `DossierSpecRow`/`DossierPerformanceRow.key` and `ProductDossierOut.other_specifications` are
  all optional, additive fields on the frozen types (`web/src/types/api.ts`) — normalizers in
  `web/src/api/real.ts` default `key` to `""` and `other_specifications` to `[]` when a backend
  response omits them, so today's live app (no `key` anywhere) renders exactly as before, just
  through the new grouping wrapper.
- `DossierSpecTable`/`DossierComparisonView` group rows with a non-empty `key` under their
  vocabulary parameter; a row with `key: ""` (every row on the current live backend) renders in a
  trailing, un-grouped "פרמטרים נוספים" appendix table instead of being grouped OR dropped — see
  the vitest `"an unkeyed (legacy) specification row still renders, in the appendix table"`.

## `web/src/lib/specVocabulary.ts` (new) — the interim vocabulary source

The plan's own lane-(c) doc note anticipated this exact gap: *"check whether the backend exposes
`GET /api/dossiers/vocabulary/{product_line}` per the plan; if the backend lane has not added it
yet, read `config/spec_vocabulary.yaml` at build time into a generated TS module as an interim and
document it."* No such endpoint exists yet either. This file is that interim: a hand-synced TS
mirror of `config/spec_vocabulary.yaml`'s `key`/`label_he`/`label_en`/`group_he`/`unit`/`required`
fields (produced once via a one-off `yaml.safe_load` + template script, not checked in — the
output is this file, reviewed and committed), same convention `web/src/lib/productLines.ts`
already uses for `config/product_lines.yaml`. `specVocabulary.test.ts` pins the exact contract
(121 keys: 24 common + 19/17/16/15/15/15 per line, zero duplicates, every `group_he` one of the
fixed 8) as a standing regression test so a future hand-edit that drifts from the YAML fails a
test instead of silently rotting.

**`PERFORMANCE_ROUTED_KEYS`** — §3.4 of the plan specifies exactly which vocabulary keys route to
the `performance` table vs. `specifications`, by name, but flags in §9 item 1 that the YAML's own
`table:` field to encode this "is designed but not yet added." This constant mirrors §3.4's prose
list by hand as the interim (documented as superseded, not duplicated, once a real `table:` field
ships) — it's what lets the grouped specifications table and the grouped performance table each
own a disjoint slice of the vocabulary's `required` placeholders, rather than the same required
key showing "לא נמצא במקורות" in both sections at once.

## §5.1 — `DossierSpecTable.tsx` (new)

Grouping **wrapper** around the existing `DossierTable` (kept, not replaced, per the plan's own
instruction) for the specifications/performance sections on `DossierDetailPage.tsx`. One
sub-`DossierTable` per `group_he`, in the fixed 8-group order, each group's rows in vocabulary
declaration order. A `required: true` vocabulary key with no matching row still renders — a
distinct amber "לא נמצא במקורות *" placeholder cell (`RequiredMissingCell`), visually different
from the plain "not found" placeholder used elsewhere, so a BD reader can tell "nobody looked"
apart from "not applicable to this line." Everything with no vocabulary key at all (a legacy
unkeyed row, a stray/hallucinated key, or `other_specifications`) renders in one trailing "אחר"
table instead of being silently dropped.

Consequence worth flagging: with no `product_line` (see the gap below), `DossierSpecTable` falls
back to the `common`-only vocabulary, which already carries several `required: true` keys (weight,
detector type, LOS stabilization, interfaces, TRL, …) — the specifications/performance sections are
therefore **never** the flat empty-table state anymore, even for a dossier with zero grounded spec
rows. `DossierDetailPage.test.tsx`'s old "shows the empty-table placeholder for a section with no
rows" test was retargeted to the still-flat `versions` section (unaffected by this change) plus two
new tests asserting the required-placeholder behavior directly.

## §5.2 — Comparison (`DossierComparisonView.tsx` + `DossierComparePage.tsx`, both new)

Route `/dossiers/compare?keys=a,b,c` (2–3 `product_key`s, deduped, order preserved). Joins each
product's data client-side via the **existing** endpoints — but not quite the ones the plan's own
prose named, see the backend gap below. Rejects (not silently truncates) a keys list outside the
2–3 range with an honest Hebrew error; shows a distinct warning instead of a table when the
selected products don't share one `product_line` (§9 item 4: cross-line comparison is deliberately
unsupported — a table that's mostly "not applicable" isn't useful). When they do share a line,
renders one grouped table per `group_he` for specifications and one for performance, one column
per product, a small dot next to a parameter's label when the products' values differ.

Entry points:
1. `DossiersPage.tsx` / `DossierCard.tsx` — a per-card checkbox (up to 3), a "השווה נבחרים (N)"
   button once 2+ are checked.
2. `DossierDetailPage.tsx`'s competitors table — a new "פעולה" column: "השווה" (links straight to
   the compare page) when `GET /api/dossiers` already has a dossier matching the competitor's
   `product` name (best-effort, case-insensitive exact match), otherwise "הרץ סקירה למוצר זה"
   (POSTs a new dossier pre-filled from that competitor row, navigates to its detail page once
   queued).

## Backend gap flagged, not worked around silently: `product_line` is not on the list/detail endpoints

`agent/eoa/api/services.py`'s `list_dossiers()` (`GET /api/dossiers`) and `dossier_detail()`
(`GET /api/dossiers/{key}`) do **not** return `product_line`, even though the DB column exists and
`get_dossier()` (`GET /api/dossiers/{key}/{id}`, the single-run endpoint) already does. The plan's
own §5.2 text says the comparison page "fetches each product's `latest` dossier via the existing
`GET /api/dossiers/{key}`" — that endpoint alone can't answer "do these share a product_line."
Worked around, not ignored: `DossierComparePage` additionally calls `getDossierRun(key, latestId)`
per selected product (using `dossiers[0].id` from `getDossier`) purely to read its `product_line`,
and `DossierDetailPage`'s competitor-match lookup works fine without it (name-matching doesn't need
a line). `DossierSummary`/`DossierDetail`/`DossierRunDetail` all gained an optional, tolerant
`product_line`/`product_key`/`product_name`/`vendor` field set in `web/src/types/api.ts` (each with
a doc comment naming exactly which live endpoint does/doesn't return it) so the day PD-backend adds
`product_line` to the list/detail endpoints, the extra `getDossierRun` round-trip becomes
unnecessary with no further UI change — normalizers already read it opportunistically.

A spawn_task suggestion was filed for the backend lane to consider adding `product_line` to
`list_dossiers()`/`dossier_detail()` directly (see the session's flagged-tasks queue).

## Files

- New: `web/src/lib/specVocabulary.ts`, `web/src/lib/specVocabulary.test.ts`,
  `web/src/components/dossiers/DossierSpecTable.tsx`,
  `web/src/components/dossiers/DossierComparisonView.tsx`, `web/src/pages/DossierComparePage.tsx`,
  `web/src/pages/DossierComparePage.test.tsx`.
- Modified: `web/src/types/api.ts` (`key`/`other_specifications`/`product_line` additions, all
  optional/additive), `web/src/api/real.ts` (normalizer updates for the above), `web/src/api/
  real.test.ts` (one pre-existing whole-object `toEqual` updated to include `llm_leg`/
  `product_line`, unrelated latent gap fixed in passing), `web/src/mocks/data/dossiers.ts` +
  `web/src/mocks/mockApi.ts` (both fixtures now on the same mock `product_line` so the comparison
  flow has a real same-line pair under `VITE_USE_MOCKS`, plus keyed rows on some spec/performance
  rows and one `other_specifications` entry to exercise every render path), `web/src/pages/
  DossierDetailPage.tsx` (spec/performance sections → `DossierSpecTable`; competitors table gains
  the "פעולה" column + entry point 2), `web/src/pages/DossierDetailPage.test.tsx` (mocks extended
  for the two new API calls, 6 new tests), `web/src/pages/DossiersPage.tsx` +
  `web/src/components/dossiers/DossierCard.tsx` (entry point 1, 2 new tests in `DossiersPage.
  test.tsx`), `web/src/App.tsx` (new route, ranked before `dossiers/:key`), `web/src/i18n/
  dictionaries/{he,en}.ts` (`dossiers.compare.*`, `dossiers.compareLink`/`runForCompetitor`/
  `selectForCompareAria`/`compareSelectedButton`/`compareMaxWarning`, `dossiers.table.colAction`/
  `otherSpecifications`), `e2e/tests/24-dossiers.spec.ts` (5 new tests, same skip-when-backend-
  lacks-data discipline as the existing suite).

## Test counts

- `npm run build`: clean (`tsc -b && vite build`).
- `npx vitest run`: **520 tests passing across 64 files** (full suite, no regressions) —
  `specVocabulary.test.ts` (9), `DossierComparePage.test.tsx` (4), `DossierDetailPage.test.tsx`
  (14, 6 new), `DossiersPage.test.tsx` (9, 2 new).
- `npx playwright test 24-dossiers.spec.ts 13-accessibility.spec.ts --project=desktop-1440x900`
  against the live app: **21 passed, 1 skipped** (the "select 2 dossiers and compare" e2e test
  skips — the live DB currently has exactly one product dossier, SPECTRO XR; the test is written
  to run once a second product dossier exists). Accessibility suite (11 tests) unaffected.
