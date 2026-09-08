# UI-TIMELINE — night-run replay legend fix (2026-09-08)

Scope: the "ציר זמן — שחזור ריצה לילית" (night-run replay timeline) card on the Morning page
(`הבוקר`, `/`). Owned files only: `web/src/pages/MorningPage.tsx`,
`web/src/lib/pipelineTimeline.ts`, `web/src/components/morning/PipelineReplayTimeline.tsx`, and
their tests.

## Bug report (screenshot, 2026-09-08 20:35)

The stage legend to the right of the bar was clipped and overlapping:

1. Labels lost their first letters ("יהוי כפילויות רב-לשוני" instead of "זיהוי ...",
   "ייצוא/ארכיון" cut).
2. Durations overlapped the labels ("ניתוח 14.5 דקי" collided).
3. The minutes abbreviation rendered as "דקי" — the geresh (U+05F3) after "דק" reads, in the
   app's font stack, as a glyph indistinguishable from a Hebrew yod.
4. One bar segment was red and the "שגיאות" tile showed 1, but the legend gave no hint which
   stage had failed.

## Root cause

`PipelineReplayTimeline.tsx`'s legend was a plain CSS grid (`grid-cols-1 sm:grid-cols-2
lg:grid-cols-3`) whose `<li>` cells had no `min-w-0`. A CSS grid item's default `min-width: auto`
ignores a descendant's `truncate` — the browser let each cell's intrinsic content width push past
its track instead of clipping, so a long label overlapped the next column's duration text. The
duration itself had no fixed-width column (`ms-auto shrink-0` only pushes it to the trailing
edge), so its position shifted with every row depending on label length, reading as a collision.
Separately, every duration string used the abbreviated `"N דק׳"` form instead of the spelled-out
`"N דקות"`, and a failed stage carried no visible text marker — only the bar segment's/dot's
color, which is invisible to anyone not distinguishing red from the other status colors and gives
no reason.

## Fix

`web/src/components/morning/PipelineReplayTimeline.tsx`:

1. **Legend grid**: `grid-cols-2 lg:grid-cols-4` (2 columns narrow, 4 wide), every `<li>` cell
   given `min-w-0`, the label wrapped in its own `min-w-0` span so `truncate` actually clamps, and
   the duration moved into a fixed-width column (`w-16 shrink-0 text-end`) instead of trailing the
   label with no reserved space. No absolute positioning is used anywhere in the component.
2. **Duration wording**: every duration in the component (bar segment tooltip, legend tooltip,
   legend duration column) now reads `"14.5 דקות"` / `"0.4 דקות"` via a new
   `formatStageMinutes()` helper in `pipelineTimeline.ts`, replacing the geresh-abbreviated
   `"דק׳"` that rendered ambiguously as a yod.
3. **Failed-stage marking**: a failed stage's legend row now shows a visible red `נכשל` badge
   next to its label (never truncated away — it sits outside the truncating span), the label
   span's `aria-label` states `"<label> — נכשל — <error>"` when an error is present, and the
   row's `title` tooltip carries the same status + duration + error text for mouse users. The
   error text comes from a new `error` field threaded through `buildStageTimeline()`, sourced
   from `PipelineStageInfo.detail.error` — a field the backend (`agent/eoa/api/services.py`'s
   `_stage_timeline_from_log`) already returns for a failed stage's terminal `run_log` row but
   which the frontend `PipelineStageInfo` type never declared, so it was silently dropped before
   reaching the component.

`web/src/types/api.ts`: added `PipelineStageInfo.detail?: Record<string, unknown> | null`.

`web/src/lib/pipelineTimeline.ts`: `StageTimelineEntry` gained an `error: string | null` field;
`buildStageTimeline()` reads it from `info.detail?.error` (only a non-empty string counts, else
`null`); added `formatStageMinutes()`.

## Which stage failed, and why (from the live API)

`GET /api/status` → `pipeline.last_run.stages.tenders`, at the time of this QA pass:

```json
{
  "status": "failed",
  "minutes": 1.8,
  "last_event": "error",
  "detail": {
    "error": "0",
    "trace": "... KeyError: 0\n  File \".../agent/eoa/tenders/scan.py\", line 992, in _candidate_duplicate_exists\n    return any(_notice_portal(r[0]) == portal for r in rows if r[0])\n"
  }
}
```

The **מכרזים** (tenders) stage failed with `KeyError: 0` inside
`agent/eoa/tenders/scan.py:992` (`_candidate_duplicate_exists`), indexing a DB row with an
integer key that isn't valid for however `rows` is shaped there (looks like a dict-style row
object being accessed positionally). `str(exc)[:300]` for a bare `KeyError(0)` is just `"0"`,
which is what `detail.error` — and now the legend's tooltip/aria-label — actually shows; it is
technically correct but not very informative on its own. `agent/eoa/tenders/scan.py` already has
unrelated uncommitted changes on this shared tree from other in-flight work, so this fix does not
touch it — flagging it here as a separate, real backend bug for whoever owns that file next.

## Screenshots

`docs/qa/content_review/screens/`, captured with the Playwright CLI (`npx playwright screenshot`)
against the live app + real backend data (the same `tenders`-failed run above), not the
interactive browser pane, so pixel dimensions are exact:

- `UI-TIMELINE-before__1440x900.png` / `UI-TIMELINE-before__1180x820.png` — pre-fix build,
  captured by building the last **committed** revision of these files
  (`git worktree add <scratch>/before-wt HEAD` — a separate worktree, main tree never touched;
  `npm run build` there with `node_modules` junctioned from the main install; served via
  `vite preview --port 4199` with its `/api`/`/ws` proxy pointed at the same running backend on
  `127.0.0.1:8765`) against the exact same job data as the "after" shots.
- `UI-TIMELINE-after__1440x900.png` / `UI-TIMELINE-after__1180x820.png` — the fixed build, live at
  `http://127.0.0.1:8765` after `cd web && npm run build`.

Visible in the before/after pair: the before legend is a cramped 3-column grid with `"דק"`
abbreviations and no failure text (only the red dot on `מכרזים`); the after legend is a 4-column
grid with aligned duration columns, spelled-out `"דקות"` throughout, and `מכרזים` marked with a
bold label plus a red `נכשל` badge.

## Tests

- `npx vitest run src/lib/pipelineTimeline.test.ts src/components/morning/PipelineReplayTimeline.test.tsx src/pages/MorningPage.test.tsx`
  → **24 passed** (3 files: 12 + 5 new/updated in `pipelineTimeline.test.ts`, a new 5-test
  `PipelineReplayTimeline.test.tsx`, and the pre-existing 7-test `MorningPage.test.tsx`
  unaffected).
- `npm run build` → clean (`tsc -b && vite build`), no type errors.
- `cd e2e && npx playwright test 13-accessibility.spec.ts 01-morning.spec.ts --project=desktop-1440x900`
  → **15 passed**, including `axe-core scan: Morning (/) has no critical violations`.

## Files changed

- `web/src/components/morning/PipelineReplayTimeline.tsx`
- `web/src/components/morning/PipelineReplayTimeline.test.tsx` (new)
- `web/src/lib/pipelineTimeline.ts`
- `web/src/lib/pipelineTimeline.test.ts`
- `web/src/types/api.ts`
- `docs/qa/content_review/screens/UI-TIMELINE-{before,after}__{1440x900,1180x820}.png` (new)
- `docs/qa/content_review/UI-TIMELINE.md` (new, this file)
