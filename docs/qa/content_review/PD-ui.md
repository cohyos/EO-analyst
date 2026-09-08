# Product Dossier UI — PD-ui

Date: 2026-09-08
Scope: `web/src/**` (types, api client + mocks, pages, components, i18n) plus
`e2e/tests/24-dossiers.spec.ts`. Built against the frozen contract in
`docs/PLAN_PRODUCT_DOSSIER.md` sections 3/5/6 — the backend lane (PD-backend) had not landed
`/api/dossiers*` at the time this was built (`GET /api/dossiers` returns 404 on the live app),
so every API-shaped piece follows the same "never throw on a missing/malformed/absent field"
discipline the product-lines (PL-ui) surface already established, and the e2e spec skips its
data-dependent tests exactly like `20-product-lines.spec.ts` does.

## Contract note flagged for PD-backend

`GET /api/dossiers`'s list row declares only a bare `count` field with no further definition in
section 5, while section 6's card spec calls for a "deal count" at that same list granularity (not
a run count — run history only exists on the detail endpoint). `DossierSummary.count` in
`web/src/types/api.ts` is therefore read as **the latest run's deal count**, with a doc comment
asking PD-backend to confirm/align `_dossier_card`'s `count` field to that reading (or add a
distinct field if a run count is also needed at list granularity).

## Screens

- **`/dossiers`** (`DossiersPage.tsx`) — one `DossierCard` per product (name, vendor, outcome
  badge, last-run date, deal count, "הרץ שוב"), a "סקירה חדשה" button that reveals `NewDossierForm`
  inline, loading/error/empty states via the shared `states.tsx` components.
- **`/dossiers/:key`** (`DossierDetailPage.tsx`) — header (identity/vendor/status/TRL/confidence
  chips + aliases), a sticky in-page `DossierSectionNav`, the fixed section order from section 6
  (תקציר | מפרט | גרסאות | ביצועים | בשלות | עסקאות | מחירים | שותפויות | מתחרים | פטנטים |
  מכרזים ותחזיות | פערים | משמעות עסקית | מה השתנה | מקורות), a pending-run banner that polls
  `getDossier` every 10s while `pending_job` is set, and a run-history list with a "השווה"
  expander (`DossierRunHistoryList`) that lazily fetches one run (`getDossierRun`) and shows its
  own `what_changed` — the API only exposes one run at a time, so "compare" surfaces that run's
  already-computed diff against its immediate predecessor rather than a two-id diff request.

## Components (`web/src/components/dossiers/`)

- `DossierCard.tsx` / `NewDossierForm.tsx` — list-page building blocks; the form validates a
  required product name (touched/inline-error pattern mirroring `SurveyDialog.tsx`), a chips-style
  aliases input (Enter/comma to add, per-chip remove button, backspace-to-pop), a product-line
  select sourced from `getProductLines()`/`PRODUCT_LINE_CATALOG`, and a 1x/2x budget select.
- `DossierFact.tsx` — `DossierFactText`/`DossierPlainText`/`NotFoundInSources`/
  `DossierSentenceList`/`DossierCiteChips`: the shared "every fact cell carries citations; an
  unestablished field renders 'לא נמצא במקורות'" primitives every section reuses. Table cells use
  a dedicated trailing "מקורות" column of `[n]` citation chips (via `DossierCiteChips`) rather
  than appending markers per-cell, keeping every other cell a plain value; prose (`Sentence`)
  fields append `[n]` markers inline through the existing `CitationText` component.
- `DossierTable.tsx` — generic `<= 6`-column table (dev-time console warning if a caller exceeds
  that) with a self-contained sticky header + scrollable body (`max-h-96 overflow-auto`), reused
  by all nine section tables (specifications, versions, performance, deals, pricing, partnerships,
  competitors, patents, tenders/forecasts) plus the sources registry table.
- `DossierSectionNav.tsx` — sticky anchor-link bar for the fixed section order (plain native
  anchors + `scroll-mt-16` on each section, no IntersectionObserver).
- `DossierRunHistoryList.tsx` — run-history rows + the "השווה" expander described above.

## API / types / mocks

- `web/src/types/api.ts` — `ProductDossierOut` and every nested row type mirroring
  `agent/eoa/llm/schemas/product_dossier.py` field-for-field (snake_case, `cites: number[]` on
  every fact-bearing object), plus `DossierSummary`/`DossierDetail`/`DossierRunDetail`/
  `DossierCreateBody`/`DossierCreateResponse`/`DossierRerunResponse`.
- `web/src/api/types.ts` — `ApiClient.getDossiers/postDossier/getDossier/getDossierRun/
  postDossierRerun`.
- `web/src/api/real.ts` — `normalizeDossier*`/`normalizeProductDossierOut` family: every array
  defaults to `[]`, every nested object defaults to its own zero-shape, `outcome` validated
  against the three known values, ids coerced string/number-safe — never throws on a partial or
  missing backend response.
- `web/src/mocks/data/dossiers.ts` + `web/src/mocks/mockApi.ts` — a fully-populated **SPECTRO XR**
  fixture (Elbit Systems; specs, two versions, claimed-vs-demonstrated performance, three deals,
  a partnership, two competitors, one patent, one tender, gaps, BD implications) and a sparse
  **STRATOS** fixture (Safran Electronics & Defense; mostly nulls/empty arrays, `outcome:
  "partial"`) demonstrating the honest empty-state rendering. `postDossier`/`postDossierRerun`
  register a `pending_job` and resolve it ~6s later via `setTimeout`, mirroring the
  queue-then-poll pattern `postProductLineReport`/`postBdReport` already use.

## i18n

`dossiers.*` (he/en) covers every UI-chrome string used above (nav label, form, sections, table
headers, empty-section copy, outcome labels, maturity/regulatory-export labels) — no hardcoded
Hebrew UI strings in the new files (report/citation content itself is exempt, same convention as
the rest of the app).

## Routing / nav

`App.tsx`: `/dossiers` and `/dossiers/:key` routes. `components/shell/nav.ts`: nav-rail entry
"סקירות מוצר" (FileSearch icon) after "קווי מוצר", and the matching `usePageTitle` mapping so the
top-bar `<h1>` reads "סקירות מוצר" on both routes.

## Tests

- **vitest** (`cd web && npx vitest run`): 60 files / **478 passed**, 0 failed — includes
  `src/pages/DossiersPage.test.tsx` (7 tests: card rendering, outcome badges + deal count, empty
  state, error state, detail link, form open/validate/submit/navigate, alias chip add/remove,
  rerun queued status) and `src/pages/DossierDetailPage.test.tsx` (7 tests: header identity/
  status/TRL/confidence, all 15 fixed section headings present in order, empty-table placeholder,
  pending-run banner, not-found state, rerun trigger, run-history "השווה" expansion), plus
  4 new normalizer tests in `src/api/real.test.ts` (well-formed list, malformed/partial list
  degrading to safe defaults, nested `ProductDossierOut` defaulting every omitted array/object,
  and a fully-null response normalizing to the zero shape).
- **build** (`cd web && npm run build`): clean, `tsc -b` + `vite build` both pass; bundle is live
  at `http://127.0.0.1:8765`.
- **e2e** (`cd e2e && npx playwright test 24-dossiers.spec.ts 13-accessibility.spec.ts`): the new
  spec — header render, nav-rail adjacency, required-field form validation, alias-chip add/remove,
  list rendering (skipped — `GET /api/dossiers` 404s on the live backend today), detail-page
  section headings (skipped, same reason), and the bad-literal-text scan — is **25 passed / 10
  skipped** across all 5 device projects (0 failed); `13-accessibility.spec.ts` (unmodified;
  `/dossiers` is not in its fixed `SCREENS` list, so this is a no-regression check on the existing
  scan) stays green.

## Files touched

- `web/src/types/api.ts`, `web/src/api/types.ts`, `web/src/api/real.ts`, `web/src/api/real.test.ts`
- `web/src/mocks/data/dossiers.ts` (new), `web/src/mocks/mockApi.ts`
- `web/src/pages/DossiersPage.tsx` (new), `web/src/pages/DossiersPage.test.tsx` (new)
- `web/src/pages/DossierDetailPage.tsx` (new), `web/src/pages/DossierDetailPage.test.tsx` (new)
- `web/src/components/dossiers/DossierCard.tsx`, `NewDossierForm.tsx`, `DossierFact.tsx`,
  `DossierTable.tsx`, `DossierSectionNav.tsx`, `DossierRunHistoryList.tsx` (all new)
- `web/src/i18n/dictionaries/he.ts`, `web/src/i18n/dictionaries/en.ts`
- `web/src/App.tsx`, `web/src/components/shell/nav.ts`
- `e2e/tests/24-dossiers.spec.ts` (new)
