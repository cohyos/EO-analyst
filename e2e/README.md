# EO-Analyst — Web UI quality suite (Playwright)

Rigorous, repeatable UI QA for the `web/` console, run against the **live
app + real backend** at `http://127.0.0.1:8765` — not mocks, not a dev
server started by this suite. It walks every screen listed in
`docs/MODULES.md` ("Web UI" section) and `תוכנית_פיתוח_מפורטת_v2.md` §8.2,
and asserts what a strict QA engineer would (see the per-file test names).

This package is intentionally standalone: its own `package.json`,
`node_modules`, and `tsconfig.json`, independent of `web/`'s toolchain. It
never imports from or modifies anything under `web/`.

## Prerequisites

1. The app must already be running and reachable at `http://127.0.0.1:8765`
   (or set `EOA_BASE_URL` to point elsewhere). This suite does not start,
   build, or proxy the app — it only drives whatever is already live.
2. Node.js 18+.

## Setup

```bash
cd e2e
npm install
npm run install:browsers   # npx playwright install chromium
```

## Running

```bash
npm test                    # headless, both viewports (1440x900 desktop, 390x844 mobile)
npm run test:headed         # watch it click through the app
npm run test:ui             # Playwright's interactive UI mode
npm test -- tests/02-feed.spec.ts   # a single file
npm test -- --project=desktop-1440x900   # a single viewport
```

Point at a different host:

```bash
EOA_BASE_URL=http://127.0.0.1:8765 npm test
```

(PowerShell: `$env:EOA_BASE_URL = "http://127.0.0.1:8765"; npm test`)

## Output

- **HTML report**: `e2e/report/index.html` — open with `npm run report`,
  or directly in a browser. Includes traces/screenshots/video for every
  failure (`trace: retain-on-failure`).
- **`e2e/QA_FINDINGS.md`**: a Hebrew-headed, per-severity list of every
  finding from the run — both explicit findings recorded mid-test (via
  `recordFinding()` in `utils/helpers.ts`, e.g. inconsistent counts, bad
  literal text, console errors, horizontal scroll) and every outright test
  failure (picked up automatically by `utils/qa-findings-reporter.ts`).
  Regenerated fresh on every run — a prior run's findings never leak into
  the new one (`utils/global-setup.ts` clears `report/findings.jsonl` at
  start).

## Structure

```
e2e/
  playwright.config.ts     # baseURL, two viewport projects, reporters
  tests/
    fixtures.ts             # shared `test`/`expect` — attaches a console-error collector
    00-navigation.spec.ts   # nav rail: routing, active state, back, RTL shell
    01-morning.spec.ts      # "/" — tiles, empty states, headline links
    02-feed.spec.ts         # "/feed" — the largest file: filters, keyboard (J/K/1-4/Enter/O),
                             #   infinite scroll vs. API total, detail panel, title links
    03-item-detail.spec.ts  # "/items/:id"
    04-entities.spec.ts     # "/entities", "/entities/:id" — search, Cytoscape canvas, named queries
    05-investigations.spec.ts
    06-ask.spec.ts          # "/ask" — streaming vs. explicit-error within 120s
    07-conferences.spec.ts  # table, outbound links, iCal export (top-level + per-row)
    08-inbox.spec.ts        # survey submit, clarifications, lessons
    09-reports.spec.ts      # list, HTML viewer, docx/md download links
    10-settings.spec.ts     # YAML tabs, no-op save, jobs table
    11-status-strip.spec.ts # persistent footer — service dots, VRAM/RAM, history drawer
    12-theme-rtl-responsive.spec.ts  # theme toggle, dir=rtl, no horizontal scroll (both viewports)
    13-accessibility.spec.ts         # accessible names, focus ring, axe-core (fail on critical only)
  utils/
    helpers.ts               # console-error collector, bad-text scanner, horizontal-scroll check,
                              #   recordFinding() (screenshot + JSON-line finding)
    global-setup.ts           # clears report/findings.jsonl before the run
    qa-findings-reporter.ts   # custom Playwright reporter -> e2e/QA_FINDINGS.md
```

## Notes on live-data side effects

A few tests exercise real state-changing endpoints against the live
backend (re-rating an item via `1`-`4`, saving a Settings tab). Where a
mutation isn't naturally idempotent, the test reads the value first and
writes it back after asserting the request fired (see
`02-feed.spec.ts` → *"keyboard: 1-4 re-rates..."*, `10-settings.spec.ts` →
*"no-op save"*). This is best-effort cleanup, not a hard guarantee under
test failure — if a run aborts mid-test, check for a stray triage-level
change on the item id logged in the failing test's trace.

## What's deliberately out of scope

- No fixture/mock backend — this suite is meaningless without the real
  app; that's the point (per the request: rigorous QA against the actual
  current build, not a synthetic one).
- No CI wiring — this was requested as a manual/on-demand QA pass, not a
  PR gate. Wiring it into CI would need a seeded, resettable database
  (the live app's ~250 items / 40+ entities are real triage data, not a
  fixture) — a follow-up decision for whoever owns the pipeline.
