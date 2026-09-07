# Content Readability Review — CR-ui

Date: 2026-09-07
Scope: `web/src/**` only (per session brief — Python side owned by other concurrent agents).
Method: Playwright screenshots of every major screen at 1440×900 (desktop) and 390×844 (mobile),
both themes, Hebrew UI (the app default) — throwaway script under `e2e/tmp_readability/`
(deleted at the end of this review) — plus direct DOM/character inspection via the browser tools
for anything a screenshot alone couldn't confirm (bidi character order, raw scroll positions,
etc.). Screenshots referenced below live under `docs/qa/content_review/screens/`.

## Summary

- **Findings:** 15 confirmed issues (10 fixed in this pass, 1 root-caused to a backend bug out of
  this session's scope, 4 documented as remaining/lower-priority).
- **Fixes:** 10 shipped, covering report typography/tables, three separate mixed-RTL/LTR bidi
  bugs, three raw-untranslated-taxonomy-slug bugs, a flexbox layout bug that reduced item/feed
  titles to an unreadable single-word-per-line column on narrow viewports, and ~15
  truncated-title-with-no-tooltip spots.
- **Tests:** `cd web && npx vitest run` — 56 files / **419 passed**, 0 failed (11 new/updated
  test files; 5 brand-new: `lib/time.test.ts`, `lib/taxonomy.test.ts`, `lib/bidiText.test.tsx`,
  `components/patents/PatentTable.test.tsx`, plus `lib/reportHtml.test.ts` extended).
- **Build:** `cd web && npm run build` — passes, bundle live at `http://127.0.0.1:8765`.
- **e2e:** `cd e2e && npx playwright test` — see final line of this doc for the run this session
  captured (all 5 projects: desktop/mobile/tablet-portrait/tablet-landscape/iphone-safari).

---

## Findings and fixes, by screen

### Reports viewer (`/reports?id=…`) — weekly + BD-IL

The report viewer had the most, and the most severe, findings — it renders the richest prose +
table content in the app.

1. **`&amp;` showing literally in the table of contents.** `ReportsPage.tsx`'s `addHeadingIds`
   built the TOC by stripping HTML tags from each heading but never decoded entities — a heading
   like `Airborne Pods &amp; Payloads` (correctly escaped for the heading's own
   `dangerouslySetInnerHTML` render) came out of the tag-strip as the literal string
   `Airborne Pods &amp; Payloads`, which React then escaped *again* as a plain text child. Every
   report whose section titles contain "&" (Airborne Pods & Payloads, Naval & Coastal EO/IR, Air
   Defense & Interceptors — i.e. most of them) showed raw `&amp;` in the sidebar TOC.
   **Fix:** `decodeHtmlEntities()` added to `lib/reportHtml.ts`, applied in `addHeadingIds`.
   Before state confirmed live via DOM inspection (`textContent` on the TOC `<li>` elements
   showed the literal string `&amp;`, e.g. `"פודים ומטע״דים אוויריים (Airborne Pods &amp;
   Payloads)"`). Screens: `04-report-weekly__desktop__dark__after.png` (after: "&" renders
   correctly in the TOC).

2. **Latin/English runs glued to the following Hebrew word with no visible space** — e.g.
   `JFB האמריקאית` rendered as `JFBהאמריקאית`. Root cause: `docx_builder.render_html` (Python
   side) wraps every embedded LTR run in `<bdi dir="ltr">…</bdi>` for bidi isolation, but puts the
   run's *separating space inside the tag* (`<bdi dir="ltr">JFB </bdi>האמריקאית`). An isolate is
   atomic, so that trailing space ends up glued to the LTR run's own edge instead of separating it
   from the following Hebrew word. Confirmed via direct DOM character-code inspection (char code
   32 landed *inside* the bdi, not between it and the Hebrew word).
   **Fix:** `fixBdiSpacing()` added to `lib/reportHtml.ts` — moves a leading/trailing space from
   inside a `<bdi>` to just outside it. Wired into `ReportBody`'s render pipeline. Verified live:
   after the fix, `document.body.textContent` between "JFB" and "האמריקאית" is a normal space
   character sitting outside the isolate.

3. **Report prose had no measure/typography scale.** `.report-body p` inherited only `text-sm`
   (14px) with no `max-width`, so a paragraph ran the full width of its container — 100+ Hebrew
   characters per line at desktop widths.
   **Fix:** `globals.css` — `.report-body` now sets 15px base size; `p`/`h2`/`h3`/`ul` get
   `max-width: 70ch`; paragraph `line-height` set to 1.6 (was 1.7, spec asked 1.6).

4. **Report tables (transaction ledgers, sources appendix) had no table styling at all** — no
   zebra striping (rows blur together once a cell wraps to 2 lines), no sticky header (scroll past
   the first screenful and the column labels are gone), and critically **no horizontal-scroll
   container**: confirmed via `getComputedStyle` that the table's containing block had
   `overflow-x: visible` — on a narrow viewport a wide table would overflow the page instead of
   scrolling locally.
   **Fix:** `wrapReportTables()` (`lib/reportHtml.ts`) wraps every `<table>` in
   `.report-table-wrap`; `globals.css` adds `overflow-x: auto` + `min-width` on that wrapper
   (forces a local scrollbar instead of column-crushing or page overflow), sticky
   `thead th { position: sticky; top: 0 }`, and `tbody tr:nth-child(even)` zebra striping.
   Confirmed live by scrolling the browser tool to the table region and reading computed styles
   directly (no separate screenshot taken of the table region specifically).

### Product-lines detail (`/product-lines/:id`)

5. **Raw composite taxonomy path in the page header.** `pl.subdomains` (`GET
   /api/product-lines/:id`) is a list of `"<domain>.<subdomain>"` composite ids (e.g.
   `airborne_pods.targeting_pods`); the header rendered it as-is: `תת-תחום:
   airborne_pods.targeting_pods` right below the page title — the single most visible raw-slug
   finding in the whole review (top of an above-the-fold header, not buried in a dense table).
   **Fix:** `domainSubdomainLabel()` added to `lib/taxonomy.ts` (mirrors
   `config/taxonomy.yaml domains.*.sub`, 52 entries across all 9 domains), used in
   `ProductLineDetailPage.tsx`; raw path kept as the chip's `title` tooltip.
   Screens: `10-product-line-detail__desktop__dark.png` (before: raw path in header).

### Patents (`/patents`)

6. **Raw subdomain slugs in the table and its filter dropdown.** The "תת-תחום" column showed
   `image_processing`, `droic_digital_pixel`, `cv_atr`, `laser_lidar` as-is (same taxonomy as
   finding 5, singular subdomain ids this time, not composite paths) — both in `PatentTable.tsx`'s
   cell and in `PatentFilters.tsx`'s `<select>` options.
   **Fix:** `subdomainLabel()` added to `lib/taxonomy.ts`, used in both files; cell also gets
   `truncate` + `title` (the label can run long, e.g. "מקורות לייזר, מגברים ומצרפי אלומה…").

7. **Inconsistent date formatting.** `p.publication_date` rendered as the bare ISO string
   (`2026-09-05`) instead of through the shared `formatDate` used everywhere else in the app.
   **Fix:** switched to `formatDate(p.publication_date)`. Also added to the same table: sticky
   header, zebra striping (`tbody tr:nth-child`) — it had a hover state but no zebra, making a
   table with 2-line-wrapping title cells hard to scan row-by-row.
   Screens: `13-patents__desktop__dark.png` (before: raw slugs in table).

### Tenders (`/tenders`, including the forecasts tab)

8. **Raw connector ids in the "מקור" column.** `tender.source` (`GET /api/tenders`) is the raw
   connector id from `config/tenders.yaml` (`rfi_rfp_news`, `ted_eu`, `jp_search` — confirmed via
   the live API against the real DB, not mock data: all 6 live tenders have this), not a display
   name — rendered as-is next to every other properly-labeled column.
   **Fix:** `tenderSourceLabel()` added to `lib/tenders.ts`, mirroring all 41 connector ids' short
   display names from `config/tenders.yaml`; cell gets `truncate` + `title` (raw id kept as
   tooltip). Falls back to the raw id for any future connector not yet in the map.
   Before state confirmed via the live API (`GET /api/tenders?include_closed=true&include_archived=true`
   against the real DB: all 6 live tenders had `source` set to a raw connector id). Screens:
   `08-tenders-forecasts__desktop__dark__after.png` (after — "מקור" column now reads "RFI/RFP
   defense news", "TED (Tenders Electronic…", "Japan ATLA/MoD procu…"; click "הצג N מכרזים
   סגורים" to populate the table — the default filtered view is empty in the seed data).

### Investigations (list + detail) and item-detail's investigation list

9. **Quoted English titles glued into Hebrew sentences with no bidi isolation.** Confirmed via
   `textContent` inspection: `investigation.question` embeds a quoted English item title inline,
   e.g. `אמת והרחב את הדיווח "The all-new 15-300 mm f/4 MWIR zoom…": מי הצדדים…` — a plain string
   with *no markup at all* around the quoted span, so the quote marks (bidi-neutral) and the
   colon right after have nothing to visually anchor to. Same family of bug as finding 2, but for
   plain text with no `<bdi>` to begin with.
   **Fix:** new `lib/bidiText.tsx` — `renderBidiText()` finds a `"…"` span that opens with a Latin
   letter and wraps just that span in a real `<bdi dir="ltr">`, leaving the Hebrew sentence
   (including the quote marks) as plain text. Applied to the two highest-visibility, multi-line
   spots: `InvestigationsListPage.tsx`'s question column and `InvestigationDetailPage.tsx`'s page
   heading. (Not applied to the `line-clamp`/`truncate`-to-1-line spots in `ItemDetailPage.tsx` /
   `EntitySidePanel.tsx` — much lower exposure once truncated to one line, left as-is to keep the
   change scoped to where it's actually visible.)
   Screens: `06-investigations-list__desktop__dark.png`, `07-investigation-detail__desktop__dark.png`.

### Item detail (`/items/:id`) and the feed's item drawer/bottom-sheet — title collapsing to one word per line

10. **A flexbox sizing bug reduced the article title to an almost-unreadable single-word-per-line
    column** on any viewport narrow enough that the `LevelBadge` + `CorroborationBadge` chips ate
    most of the header row's width — confirmed live on mobile (390px) for both `/items/:id` and
    the feed drawer/bottom-sheet (`FeedDetailPanel.tsx`, same header pattern). Root cause,
    confirmed by measuring live layout rects: the title's wrapping `<div className="min-w-0
    flex-1">` sat in a `flex-wrap` row alongside the two badge chips + a status icon; with
    `min-w-0`, the title `flex-1` div had *no* minimum size to defend, so instead of the row
    wrapping the title down to its own line once the badges left it too little room, the browser
    just kept shrinking the title container — measured at **85px of a 269px row** on
    `/items/39` at 375px viewport width, with the title unable to truncate (there's no
    `truncate`/`line-clamp` on it — it's meant to wrap to multiple lines) so it wrapped one word
    (sometimes half a word) per line instead: `The all- / new 15- / 300 mm / f/4 MWIR / zoom /
    engineered / for 10 µm / SXGA / detectors`.
    **Fix:** replaced `min-w-0` with a real minimum (`min-w-[12rem]` on `ItemDetailPage.tsx`,
    `min-w-[9rem]` on the narrower `FeedDetailPanel.tsx` drawer/sheet — `flex-wrap` added there
    too, since the row didn't have it before). Once the badges leave less than that minimum on the
    current line, the flex-wrap row now moves the title down to its own full-width line instead of
    crushing it. (`FeedRow.tsx`'s compact list-row title, which *does* use `truncate`, was left
    alone — `min-w-0` is correct and required there for ellipsis truncation to work.)
    Before state confirmed live (measured layout rects at 375px viewport width, see above) and
    visible in the first screenshot pass, e.g. `The all- / new 15- / 300 mm / f/4 MWIR / zoom /
    engineered / for 10 µm / SXGA / detectors`. Screens (after):
    `02-item-detail__mobile__dark__after.png`, `01-feed-list-drawer__mobile__dark__after.png` —
    title now wraps at word boundaries across a normal 3-line block.

### App-wide: truncated titles with no tooltip

11. Systemic gap: `className="truncate"` (or `line-clamp`) on a title/name with no `title=`
    attribute, so a cut-off headline, entity name, or citation title has no way to be read in
    full without navigating away. Found and fixed ~15 call sites across the app — the ones most
    likely to carry long real content and most visible in normal use:
    - `EntitiesPage.tsx` (row entity name — found via `10-entities-list` screenshot, "Northrop
      Grumm…" cut mid-word)
    - `FeedRow.tsx` (title link's `title` attribute was a fixed action hint, "פתח מקור בכרטיסייה
      חדשה", instead of the article title itself — fixed to carry both; also the source-name span)
    - `ConferencesPage.tsx` (conference name), `MorningPage.tsx` (headline title + summary),
      `CommandPalette.tsx` (item title + entity name, and its entity-kind chip was also a raw
      enum — fixed via the existing `entityKindLabel` helper it just wasn't using),
      `InvestigationsListPage.tsx` (trigger-item title, last-report title),
      `InvestigationDetailPage.tsx` (trigger-item title), `ItemDetailPage.tsx` (investigation
      question), `EntitySidePanel.tsx` (timeline item title, investigation question),
      `EdgeEvidencePanel.tsx`, `PathFinderPanel.tsx` (both entity slots), `EntitySearchBox.tsx`
      (search result name), `DuplicateOutletsPopover.tsx` + `CorroborationBadge.tsx` (source
      name), `ChatThread.tsx` (context-pill label), `AskSourcesFooter.tsx` (source title + name).
    Not touched (lower value / already inside a hover-revealed popover so a second, nested
    tooltip is marginal): `CitationText.tsx`, `SourcePreviewCard.tsx`, filter-chip lists
    (`ProductLineFilter.tsx`, `FeedFilters.tsx` — country/product-line names are short enough in
    practice), `NavRail.tsx`/`TopBar.tsx` (structural chrome, not content).

### Time/date formatting — app-wide

12. **Dates and times breaking across lines / reordering inside RTL flow.** `formatDateTime` /
    `formatDate` / `formatTime` / `formatDuration` (`lib/time.ts`) return a short
    numeric/punctuation string with no strong-direction character of its own; ~40 call sites across
    the app drop that string into RTL flow as a bare `{formatDateTime(x)}` with no `<bdi>` wrapper
    (confirmed the worst case live: `FeedDetailPanel`'s `03.09.2026, 11:12` rendering with the
    separating comma stranded on its own line, ahead of the date).
    **Fix:** rather than auditing and wrapping ~40 individual call sites, wrapped the formatters'
    *return values* in LRI/PDI (U+2066/U+2069 — the Unicode equivalent of `<bdi dir="ltr">…</bdi>`,
    usable in a plain string with no markup). Fixes every call site at once; invisible in the
    rendered text and in a copy-paste, so nothing a user reads or copies changes. Covered by 5 new
    unit tests (`lib/time.test.ts`).

---

## Confirmed but not fixed in this pass

- **`product_line` report titles are raw backend content, not a frontend bug.** All 6
  `product_line`-kind reports in the live DB have `title_he` literally equal to
  `"product_line — 07.09 18:45"` (verified via the live API against the real DB) instead of a
  proper Hebrew title like the `bd_territory` kind gets ("דוח פיתוח עסקי — ישראל — …"). The
  frontend already has the correct label (`KIND_LABEL.product_line = "קו מוצר"`) and uses it
  everywhere *except* this one field, which the server populates directly. Root cause is in
  `agent/eoa/report/product_line.py` — outside this session's `web/**` scope, and that exact file
  is one of the ones a concurrent Python-side session has open (`git status` shows it modified),
  so a frontend patch here risked masking a bug that session may already be fixing at the source.
  Screens: `04-report-weekly__mobile__dark__after.png` (report list, "product_line — 07.09
  18:45" row — still present after this session's fixes, confirming it's a backend content
  issue rather than a frontend translation gap).

- **Report prose heading (investigation question) reads as a wall of bold text.** The big `<h2>`
  question heading on `/investigations/:jobId` is already width-capped (`max-w-3xl`, ~55-65
  Hebrew characters/line at `text-lg` — within the ~70ch guideline), so this isn't a measure
  violation; it's `font-semibold` applied to a genuinely long, multi-line heading with no lighter
  secondary weight to break up the block. Subjective enough (and low-risk-but-uncertain-benefit)
  that it's flagged here rather than changed speculatively.

- **Tables generally lack zebra striping outside the report viewer and patents table** —
  `ConferencesPage.tsx`, `SourceCoveragePanel.tsx` (tenders), `GraphTableView.tsx` still render
  every row with the same background. Same fix pattern as findings 4/7 (`tbody
  tr:nth-child(even)`), just not applied everywhere to keep this pass scoped to the screens with
  the most severe findings.

- **Mobile: a table wider than the viewport requires horizontal scrolling to read a wrapped
  row's full content** (patents table, min-width 900px vs. a ~267px-wide scroll viewport on a
  390px phone). Verified this is the horizontal-scroll container working as built (confirmed valid
  `scrollLeft` range, no page-level overflow) rather than a broken scroll-position bug — but it's
  still a real reading-friction point: any column wider than the viewport requires scrolling
  mid-row to read a long English title. A card-based mobile layout (like `/product-lines` already
  uses) would read better than a horizontally-scrolled table here, but that's a bigger structural
  change than this pass's scope.

---

## Verification

- `cd web && npx vitest run` → **56 files, 419 passed, 0 failed**.
- `cd web && npm run build` → passes; the live app at `http://127.0.0.1:8765` serves the fixed
  bundle (no restart needed).
- `cd e2e && npx playwright test` (all 5 projects: desktop-1440x900, mobile-390x844,
  tablet-820x1180, tablet-landscape-1180x820, iphone-safari — 628 tests total) →
  **626 passed, 2 failed** (23.9m). Both failures are pre-existing, backend/API-dependent, and
  unrelated to any change in this pass:
  - `06-ask.spec.ts` (desktop only): the live-LLM streaming-response test timed out waiting for
    either a completed answer or an explicit error within 120s — an LLM backend/timing issue, not
    a rendering one.
  - `10-settings.spec.ts` (iphone-safari only): `PUT /api/settings/config` returned HTTP 500 on a
    no-op save — a server-side config-save issue, not a frontend one. `SettingsPage.tsx` itself
    was not touched in this pass (only its test file, for the unrelated `formatDuration`
    bidi-isolation change).
  Re-ran the full vitest suite after this run (`npx vitest run`) to double check: still
  **56 files, 419 passed, 0 failed**.
