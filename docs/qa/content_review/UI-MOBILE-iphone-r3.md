# iPhone mobile UI audit — round 3 (deep pass: below-the-fold + interactive states)

Date: 2026-09-15. Base commit: `dd77220` (main, `ahead 1` of origin at run time — see `git log -1`).

## 1. Method

Round 1/2 (commit `dd77220`) fixed page titles, the bottom tab bar, TopBar overflow menu, FeedRow
2-row layout, DossierTable phone cards, tender/patent card lists, ReportsPage phone flow,
StatusStrip compaction, select width caps, citation sizes, bidi spacing — but the method only ever
screenshotted the **first viewport** of `<main>` (the app scrolls inside
`<main class="… overflow-y-auto">`, not the document), so everything below the fold and every
interactive state (menus, sheets, drawers, popovers, forms, chat, dark theme, landscape) went
unchecked.

This round adds `e2e/scripts/screens-iphone-deep.spec.ts` (+ `e2e/scripts/pw.iphone-deep.config.ts`),
run against the live app + real Postgres data at `http://127.0.0.1:8765`, project `iphone-safari`
(`devices["iPhone 14"]`, WebKit — 390×844 portrait / 844×390 landscape):

- **Section A — scroll-through**: 26 routes (+3 tab/sub-route variants) in light theme, each
  scrolled in steps of `main.clientHeight − 80px` (cap 12 steps), screenshot + a programmatic
  readability sweep at every step. 4-route dark-theme subset and 4-route landscape subset repeat
  the same scroll-through.
- **Section B — interactive states**: 17 tests covering the bottom-nav "עוד" sheet, TopBar overflow
  menu + global search, RunNow popover, StatusStrip resource drawer, the full "שאל את האנליסט" chat
  panel (real question sent, ≤90s wait), feed filters/popovers/row-tap, item-detail actions, the
  new-dossier form, dossier rerun + section-nav jump, dossier compare selection, report controls,
  tender/patent tabs, entities→graph, tech-radar/payloads/inbox/BD/product-line/conferences, and
  every Settings section/tab.
- **Section C — programmatic sweep**, run on every single screenshot in both sections: clipped
  headings, tiny text, un-wrapped table overflow, small touch targets, LTR-with-Hebrew direction
  bugs, text-node overlap (>30% of the smaller rect), ancestor-`overflow:hidden` clipping, oversize
  media, `document.activeElement` visibility, >40%-viewport fixed/sticky coverage, `<main>`'s own
  horizontal overflow, and phantom outer/document scroll.

**Counts**: 52/52 Playwright tests passed, 0 failures. 292 screenshot + checks-JSON pairs written to
the scratchpad (`iphone-audit-r3/`; not committed, per the task's file allowlist). Re-run with:

```
cd e2e && npx playwright test --config=scripts/pw.iphone-deep.config.ts --project=iphone-safari
```

(Set `IPHONE_AUDIT_OUT_DIR` to redirect output; defaults to the scratchpad path baked into the spec.)

**Methodology caveat (read before trusting the checks JSON alone)**: the automated sweep's
`textOverlap`, `smallTouchTargets`, `clippedHeadings`, `ancestorClipped`, and `ltrWithHebrew`
counters fired on **nearly all 292** screenshots. Spot-checking a representative sample against the
actual screenshots showed the overwhelming majority are false positives — `sr-only` headings
flagged as "clipped", `<bdi>` elements flagged as "ltr-with-Hebrew" (bdi's Unicode bidi isolation
doesn't correlate with `getComputedStyle().direction`), 1-2px ancestor-clip pokes from an
intentional `overflow-hidden` safety net (`FeedRow.tsx`), and `dir="ltr"` YAML/code textareas
flagged as direction bugs when that's correct by design. `main`'s own `scrollWidth > clientWidth`
flag fired on **100%** of screenshots including the simplest pages; re-measuring live in a
Chromium tab showed `0px` diff, and document-level overflow (`documentElement.scrollWidth`) was
`false` everywhere in the Playwright run too — this reads as a WebKit `overflow-y-auto`
scrollbar-gutter measurement quirk, not a real horizontal-scroll defect, and is **not** included in
the table below. Every defect below was visually confirmed against its screenshot, not taken on the
checks JSON's word alone.

One test-environment note, not a scored defect: the real chat question sent in `b06`
("מה חדש באלביט השבוע?") came back **"Load failed"** rather than a real answer within the wait
window, so the final answer's citation/sources-footer layout could not be visually verified this
round — worth a follow-up run.

## 2. Defect table

| # | Route / state | Severity | What the user sees | Evidence | Root cause | Proposed fix |
|---|---|---|---|---|---|---|
| 1 | Nearly every page, mid/lower scroll (morning, feed, item detail, dossiers list/detail/compare, reports, settings, entities graph, dossier new-form) | **P1** | The opaque teal "שאל את האנליסט" pill sits at a fixed viewport position and, as the user scrolls, floats **on top of** real content — page text, form fields, card rows, table cells — making it unreadable/unclickable underneath the pill | `a01-morning_light-s00.png` (covers pipeline timeline card), `b02a-overflow-open_true.png` (covers a feed headline), `b05b-status-strip-drawer_true.png` (covers "אוסף נתונים…" resource card), `a16-reports-monthly_light-s05.png` + `s08.png` (covers report body prose + citation), `a22-dossier-detail_light-s11.png` (covers a platforms-section fact), `b09b-new-dossier-form_true.png` (covers the budget-multiplier select), `b11a-compare-selection_3.png` (covers a dossier card's date row), `b15c-open-full-graph_true.png` (covers a graph-explorer filter), `b17d-settings-yaml-tab-1_true.png` (covers an LLM chain row) | `web/src/components/shell/ChatPanel.tsx:76-78` — the FAB is `position: fixed` with only a `bottom`/`start` offset and no scroll-aware repositioning or content-avoidance; `<main>`'s reserved bottom padding (`AppShell.tsx`) only clears the FAB at the very end of scrollable content, not while scrolling past it | Shrink the closed-state FAB to a small circular icon button (not a full-width opaque pill) so it obscures far less, and/or give it a semi-transparent/blurred background, and/or auto-collapse it to icon-only while `<main>` is mid-scroll (only expand to the full pill when the user is near the top or has been idle) |
| 2 | `/feed` — Product-line filter popover (`b07c-productline-menu_true.png`) | **P2** | Opening the "קו מוצר" filter chip shows a menu whose text is cut off at the left edge of the phone screen — six option lines all truncated mid-word with no way to read or scroll to the missing text | `b07c-productline-menu_true.png` | `web/src/components/productLines/ProductLineFilter.tsx:59-60` — the popover is `absolute`, fixed `w-72` (288px), anchored via `insetInlineStart: 0` to the trigger button's own position with no viewport-edge collision detection; since the trigger sits mid-row (not at the row's start), the 288px box runs off the 390px viewport's left edge | Constrain the popover's width to `min(18rem, calc(100vw - 2rem))` and/or clamp its position with a flip/shift (e.g. Floating UI `shift`/`flip` middleware) instead of a bare `insetInlineStart: 0` |
| 3 | `/feed` — Country filter popover (same component pattern) | **P3** | Not visually clipped in this run (its trigger happens to sit further from the row's end), but shares the exact same `insetInlineStart: 0`, fixed-width, no-collision-detection popover pattern as #2 | `web/src/components/feed/FeedFilters.tsx:165` (`style={{ insetInlineStart: 0 }}`) | Same root-cause class as #2 | Same fix as #2, applied to both popovers (or factor into one shared positioned-popover primitive) |
| 4 | `/items/:id` — "חקור לעומק" (investigate) button | **P2** | The brief expected a confirm-and-cancel dialog before a real investigation is queued; there isn't one — the button calls the mutation directly on click, so a single mis-tap on a phone immediately queues a real backend job with no way to back out | Code read, not exercised (see spec comment at `b08c`) — `web/src/pages/ItemDetailPage.tsx:173-179`: `onClick={() => investigate.mutate()}` | `ItemDetailPage.tsx:173-179` (and `FeedDetailPanel.tsx`'s twin `investigate` mutation, same pattern) | Add a confirm step (native `confirm()` at minimum, or a small dialog matching the rest of the app's style) before `investigate.mutate()` fires, especially given phones have no hover state to "undo" an accidental tap |
| 5 | `/` (StatusStrip resource toggle) | **P3** | The entire VRAM/GPU/RAM/disk resource readout is one `<button>` only 20px tall (`186×20`) — below the ~44px minimum comfortable phone touch target — that opens the resource-history drawer | `a01-morning_light-s00-checks.json` → `smallTouchTargets.examples[0]` (width 186, height 20); visually confirmed in `a01-morning_light-s00.png` | `web/src/components/shell/StatusStrip.tsx` — the whole meter row is wrapped in one compact `<button>` with no extra vertical hit-slop | Add `padding-block` (or a larger invisible hit-area via negative margin + padding) to the button without changing its visual height, so the tap target grows without changing the compact footer's look |
| 6 | Landscape subset (`a01-morning_landscape-s00.png`, `a22-dossier-detail_landscape-s01.png`) | P3 (unconfirmed) | At 844×390 landscape the app crosses the `md:` breakpoint and renders the tablet/desktop shell (NavRail + full TopBar) instead of the phone shell — expected per the `md:` breakpoint, but the status-strip's horizontal resource row shows a visible gray scrollbar track under it (its own label, e.g. "Search", is scrolled half off the left edge) | `a01-morning_landscape-s00.png`, `a22-dossier-detail_landscape-s01.png` | Possibly a WebKit/Playwright headless-only scrollbar-rendering artifact (`no-scrollbar-x` utility class not suppressing the track in this engine) rather than something a real iPhone in landscape would show — **flagged for a real-device recheck, not filed as a confirmed defect** | If reproducible on a real device: hide the scrollbar track the same way other `no-scrollbar-x` elements do, or verify the CSS actually applies at this breakpoint |

No P1/P2 clipped headings, tiny text, un-wrapped table overflow, or genuine LTR/Hebrew direction
bugs were found after visual confirmation (the automated sweep's raw hits in these categories were
all false positives — see the methodology caveat above).

## 3. Cross-cutting patterns

- **Fixed chat FAB overlaps scrolled content app-wide** (defect #1) — the single highest-value fix
  in this round; it is not page-specific, it reproduces on every route with content tall enough to
  scroll past the FAB's fixed band, in both light and dark theme.
- **Unbounded-width popovers anchored with a bare `insetInlineStart: 0`** (defects #2/#3) —
  `ProductLineFilter.tsx` and `FeedFilters.tsx`'s country menu share the identical positioning
  pattern with no viewport-edge collision handling; any other popover built the same way (worth a
  repo-wide grep for `insetInlineStart: 0` alongside a fixed `w-`) is a latent instance of the same
  bug, just waiting for a trigger button positioned far enough toward the row's end.
- **Mutating actions with no confirm step** (defect #4) — `ItemDetailPage.tsx`'s and
  `FeedDetailPanel.tsx`'s "חקור לעומק", and separately `DossierCard.tsx`/`DossierDetailPage.tsx`'s
  "הרץ שוב" (rerun) button, all call their mutation directly `onClick` with no confirmation dialog.
  Per the audit's hard rules against real side effects, none of these were actually clicked this
  round (screenshotted-in-place only) — but the absence of a confirm step is itself worth a
  deliberate design decision, not an accident, given how easy an accidental tap is on a touch
  screen.

## 4. Verified-OK (this round only — not re-checking round 1/2's own fixes)

- Bottom-nav "עוד" sheet: opens correctly, backdrop dims, close X reachable, Escape closes it, and
  it correctly layers **above** the chat FAB (z-40 vs FAB's z-30) so no double-overlap there.
- TopBar overflow menu and global search (⌘K): open/close correctly; search returns live results
  ("אלביט" → items + entity) with readable Hebrew/English mixed rows.
- Full-screen chat panel (`ChatPanel.tsx`'s `fixed inset-0` mobile mode): clean layout, no double
  bottom-nav, composer input measured at `y:614-656` inside a 664px-tall viewport — never hidden
  behind the keyboard-safe-area or bottom bar. Model picker opens without layout breakage.
- Dossier detail sticky section-nav → jump-to-section: the "עסקאות" (deals) chip correctly scrolls
  its heading to `top:120px`, clear of both the sticky section-nav (`bottom:107px`) and the TopBar
  (`bottom:56px`) — no heading-under-sticky-bar regression.
- Reports "גרסאות קודמות" (older versions) expander, "חזרה לרשימה" back button, and the
  docx/md/html download row: all present, readable, and reachable on phone.
- Dark theme (morning, feed, dossier detail, monthly report): no contrast, legibility, or new
  layout defects found beyond the FAB overlap already tracked as defect #1.
- Dossier compare page and dossier "סקירה חדשה" (new) form: open, fill, and cancel cleanly; neither
  was submitted (per the hard rule against real side effects).

Report file: `docs/qa/content_review/UI-MOBILE-iphone-r3.md`. Spec: `e2e/scripts/screens-iphone-deep.spec.ts`. Config: `e2e/scripts/pw.iphone-deep.config.ts`.
