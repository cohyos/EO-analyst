# iPhone Mobile UI Audit — Round 5

Verification pass against the **live build** at `http://127.0.0.1:8765` (not a dev server), following
rounds 1–4 (commits `dd77220`, `e3ab7cd`, `2e000b2`). Both harnesses ran against the live build; screenshots
and checks JSON live under the session scratchpad (`iphone-audit-r5` / `iphone-audit-r5-deep`), not in the
repo. This round did **not** fix anything — findings below are for a follow-up round.

Harness note: `e2e/scripts/screens-iphone-deep.spec.ts` had three stale selectors/assumptions from round 3,
fixed in this round (diff only, no behavior change to the app):
- `b06-chat-panel-real-question` targeted `getByLabel("פתח את פאנל שאל את האנליסט…")`, which is the
  **desktop** pill's (`chat-panel-fab-dropzone`) aria-label — hidden on phone viewports (`md:hidden`
  reverse). The phone FAB (`chat-panel-fab-compact`) carries the shorter label `"שאל את האנליסט"`
  (`shell.chatFabLabel`, not `shell.chatFabAria`). The old selector silently never opened the panel on
  phone; fixed to `getByTestId("chat-panel-fab-compact")`.
- `b08c`/`b09e`/`b10b` (investigate / dossier rerun buttons) assumed no confirm step existed and only
  screenshotted the button. Round 4 added `ConfirmDialog` (`role="dialog"`) in front of both actions
  (`ItemDetailPage.tsx:177-195`, `DossierCard.tsx:125-146`, `DossierDetailPage.tsx:412-433`) — all three
  tests now open the dialog, screenshot it, and click "ביטול" (never "אישור"), matching the hard rule.

## Verification table

| # | Item | Result | Evidence |
|---|---|---|---|
| 1 | Feed headline renders on 2 lines | PASS | `02-feed_iphone-safari_light.png` |
| 2 | Morning legend: one stage per row | PASS | `01-morning_iphone-safari_light.png` |
| 3 | Every page title complete (not cut) | PASS | all 25 `*_light.png` (25-route sweep) |
| 4 | Bottom tab bar + "עוד" sheet | PASS | `b01b-sheet-open_true.png` — full 2-column menu, nothing cut |
| 5 | TopBar ⋯ menu | PASS | `b02a-overflow-open_true.png` |
| 6 | Reports phone flow (list → report → "חזרה לרשימה") | PASS | `b12a/b12b` — back link reachable |
| 7 | Report tables ≤4 cols wrap normally | PASS | `16/17-reports-*_table.png` |
| 8 | Report tables >4 cols → labelled stacked cards | PASS | `16-reports-monthly_..._table.png`, `17-reports-weekly_..._table.png` — thead hidden, `label: value` cards |
| 9 | Citations ≥12px, adjacent chips grouped | PASS | `22-dossier-detail_..._deals.png` — chips e.g. `24 23 19 15` render as one glued group, ≥14px. Order reflects source `cites[]` order (not re-sorted — confirmed no sort logic in `reportHtml.groupAdjacentCitations`/`DossierFact.tsx`; this is by design, not a bug) |
| 10 | Dossier detail: spec/deals as cards | PASS | `22-dossier-detail_..._spec.png`, `_deals.png` |
| 11 | Dossier: section chip nav | PASS | visible top row in all `22-dossier-detail*` shots |
| 12 | Dossier: share icon row under title | PASS | 💬/✉/⧉ row, `22-dossier-detail_iphone-safari_light.png` |
| 13 | Compare page | PASS | `23-dossier-compare_iphone-safari_light.png` |
| 14 | Tenders cards | PASS | `11-tenders_iphone-safari_light.png` |
| 15 | Tenders "שתף HTML" menu opens in-viewport | PASS | live repro — 6-item menu fully on-screen, no clipping |
| 16 | Tenders share menu → "פתח בלשונית חדשה" | **UNVERIFIED (tooling)** | click registered, no console error, but this Browser-pane sandbox blocks `window.open` of blob: URLs — no new tab observable from here. Code review: `TendersShareMenu.tsx:141/180/186` all use `window.open(url,"_blank","noopener,noreferrer")`, the same pattern as `CitationText.tsx:73`/`AskSourcesFooter.tsx:38`, which work elsewhere in the app. Needs a real-device or Playwright (`page.waitForEvent('popup')`) check next round |
| 17 | Patents cards | PASS | `12-patents_iphone-safari_light.png` |
| 18 | Product-line & settings selects in viewport | PASS | `b17a/b17b/b17e` — models section, quick controls, jobs table all reachable via scroll |
| 19 | Status strip: one clean line, nothing cut on left | PASS | bottom strip on every screenshot, e.g. `01-morning...png` |
| 20 | Chat: compact FAB → real question → answer + no error | PASS | see Chat timing below |
| 21 | Chat FAB hides while scrolling, returns after idle | PASS (by design) | `useMainScrollDirection.ts` — hides only while actively scrolling down, reappears after 800 ms idle (not "stays hidden"); code-confirmed, matches the documented hide‑on‑scroll pattern |
| 22 | Feed product-line popover inside viewport | **FAIL** — see defect P2 below | live repro |
| 23 | Investigate confirm dialog (cancel) | PASS | `b08c2-investigate-confirm-dialog_true.png` → `b08c3-...-dismissed.png` |
| 24 | Dossier rerun confirm (cancel), both surfaces | PASS | `b09f/g` (card), `b10b2/b10b3` (detail page) |
| 25 | Graph explorer opens in table view | PASS | `06-entities-graph_iphone-safari_light.png` — "תצוגת טבלה" toggle active on load |
| 26 | Radar sticky first column | PASS | live repro: `sticky start-0 top-0 z-20` on the header cell; row-label column verified fixed under `scrollLeft` |
| 27 | No phantom outer scroll (`documentElement.scrollHeight === innerHeight`) | PASS | 0 failures across 428 deep-harness checks (`phantomOuterScroll` never true) |
| 28 | No inner (`main`) horizontal overflow except intentional scroll containers | **FAIL** — see defects P1/P1 below | see below |

## Regressions / new defects (P1 → P3)

| P | Defect | Root cause | Fix |
|---|---|---|---|
| P1 | Long unbroken URLs in report body / sources-appendix text overflow `<main>` horizontally on phone — confirmed live on the **actual `/reports?id=191` page** (`main.scrollWidth` 797px vs 365px clientWidth) and on the morning page's embedded RFI/RFP report card (same mechanism). The whole page gains an unwanted horizontal scrollbar and headings/labels get visually cut at the right/left edge. | `web/src/styles/globals.css:289` — `.report-body` (and its table variant, `.report-table-wrap--stacked td` at `:449-457`) has no `overflow-wrap`/`word-break` rule; a raw `<a>`/`<bdi>` URL with no spaces (e.g. `https://sam.gov/workspace/contract/opp/…`, `https://www.army.mil/article/295045/…`) is one unbreakable token and just pokes out of its flex/block container. The only existing `word-break: break-word` in the file is scoped to `.report-table-wrap--narrow` (`:377`), not the general report body or the stacked-card cells. | Add `overflow-wrap: anywhere;` (or `word-break: break-word`) to `.report-body` (covers the `<ul><li><bdi><a>` sources list) and to `.report-table-wrap--stacked td` (covers table cells, e.g. the "קישור" column). Two one-line CSS additions. |
| P1 | Settings → "עריכת קבצי הגדרה" tab bar (`config / sources / watchlist / taxonomy / models`) has no horizontal scroll or wrap on phone — the 5th tab (**"models"**) renders completely off-screen (`left:-80px, right:-6px` measured live), invisible and untappable by a real finger. The Playwright deep-harness "passed" every tab click only because Playwright force-clicks by ref regardless of visibility — a live user cannot reach it. | `web/src/pages/SettingsPage.tsx:336` — `<div className="mb-2 flex gap-1 border-b border-border" role="tablist">` has neither `overflow-x-auto` nor `flex-wrap`. | Add `overflow-x-auto` (+ `flex-nowrap` already implicit) to the tablist div, same pattern already used elsewhere in the app (`.report-table-wrap`, radar/conferences/payload tables); or `flex-wrap` if wrapping to a 2nd row is preferred. |
| P2 | Feed's `ProductLineFilter` popover: long product-line labels don't truncate as the code intends — they overflow the popover box and push `<main>` into horizontal scroll, with the option text cut mid-word at the screen edge (reproduced live: `main.scrollWidth` 456 vs 365). | `web/src/components/productLines/ProductLineFilter.tsx:82-87` — `<bdi className="truncate">` is a flex item of `<button className="flex w-full …">` with no `min-width: 0` on the item; Tailwind's `truncate` (`overflow:hidden;text-overflow:ellipsis;white-space:nowrap`) is a well-known no-op on a flex child whose default `min-width:auto` lets it grow to its content's intrinsic width instead of shrinking to the container. `usePopoverEdgeClamp`'s own width clamp (`w-[min(18rem,calc(100vw-2rem))]`) only bounds the *popover*, not this inner overflow. Likely the same pattern in `FeedFilters`' country-filter popover (cited in the same hook's doc comment as sharing this code shape) — not independently re-verified this round. | Add `min-w-0` to the `<button>` (line 82) or directly to the `<bdi>` (line 87) so `truncate` can actually clip. |
| P3 | Compact chat FAB (`chat-panel-fab-compact`) still visually overlaps static page content at rest — e.g. it sits directly over a citation chip (`[19]`) in the dossier summary bullets, live-reproduced on `/dossiers/elbit-systems-spectro-xr`. This is the residual half of round‑3's defect #1: the hide-on-scroll fix only hides the FAB *while actively scrolling*; it reappears after 800 ms idle regardless of what's underneath it, so any content that happens to sit in the fixed bottom-start corner is covered whenever the page is at rest. | `web/src/hooks/useMainScrollDirection.ts:3,39-46` (by design — `IDLE_MS = 800`) + `ChatPanel.tsx:62-88` (fixed position, no idle-fade/shrink). Not a regression from this round; a known, only-partially-mitigated limitation, worth a follow-up (e.g. reduce opacity instead of a solid circle at rest, or nudge it clear of card content). | No one-line fix — flag for design discussion, not urgent. |

Not a defect: `mainHorizontalOverflow` also fired on `a03-item-detail`, `a08-investigation-detail`, `a14-inbox`
at some scroll steps in the automated sweep — very likely the same P1 URL/token root cause (these routes
also render report/citation body text), but not independently re-confirmed live this round; worth a quick
recheck once the P1 CSS fix lands.

## Chat timing result

`b06-chat-panel-real-question`: opened via the (now correctly targeted) compact FAB, asked
"מה חדש באלביט השבוע?", **completed in ≈61 s** (input filled 23:45:43.8 → final answer rendered
23:45:44.6→23:46:44.6) with a full grounded answer (honest "no new sources this week" response,
`gemini-3.1-pro-high · ענן` badge shown) and **no "Load failed" / error line**. Consistent with the
SSE keep-alive fix (round 3) holding on the live API. Model picker also opened cleanly afterward
(`b06e-model-picker_true.png`).

## Re-run commands

```bash
cd e2e
IPHONE_AUDIT_OUT_DIR=<scratch>\iphone-audit-r5 \
  npx playwright test --config=scripts/pw.iphone-shots.config.ts --project=iphone-safari --grep light

IPHONE_AUDIT_OUT_DIR=<scratch>\iphone-audit-r5-deep \
  npx playwright test --config=scripts/pw.iphone-deep.config.ts --project=iphone-safari
```

Both ran against `PLAYWRIGHT_BASE_URL=http://127.0.0.1:8765` (the live build). Results: 25/25 and 52/52
passed (Playwright's own pass/fail is readability-capture only, not a correctness gate — see this file's
findings for the actual defects the automated counters could not catch or, per the brief's warning about
false positives, over-reported).
