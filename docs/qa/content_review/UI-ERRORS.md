# UI-ERRORS — Morning "שגיאות" tile drill-down (2026-09-08)

Scope: the "שגיאות" KPI tile on the Morning page and everything it opens. Owned files:
`agent/eoa/orchestrator/jobs.py` (`_run_stage`'s error recording), `agent/eoa/api/services.py`
(`recent_errors` + classification), `agent/eoa/api/routes/reports.py` (`GET /api/morning`, no
change needed — the route already returns `services.morning()` unmodified),
`web/src/pages/MorningPage.tsx` (errors tile only, not the timeline block — that belongs to the
concurrently-running UI-TIMELINE task), `web/src/components/morning/RunErrorsPanel.tsx` (new,
replaces `ErrorsDrawer.tsx`), `web/src/types/api.ts`, `web/src/api/real.ts`,
`web/src/mocks/mockApi.ts`, `web/src/i18n/dictionaries/{he,en}.ts`, and the tests under those
paths.

## Bug report (2026-09-08 20:40, screenshot)

> dead end — an error, and then what do I do with it? what is the error? what caused it? why is
> it reported?

The "שגיאות" tile showed `1` with no drill-down. `GET /api/morning`'s `recent_errors` returned:

```json
[{"id": 279, "job_id": 180, "stage": "tenders", "message": "0", "at": "2026-09-08T01:31:51"}]
```

`message: "0"` is useless — it is `str(exc)` for an exception raised with a single non-string
arg (`KeyError(0)`), which is genuinely just the string `"0"`. There was also no cause, no
recommended action, no indication of what the run did next, and no way to see the exception type
or a traceback.

## Root cause of error 279

Read `runtime/logs/orchestrator.2026-09-08.log`: the process restarted at 17:39 that day (log
starts fresh there), so the file itself doesn't cover 01:31. The real exception was recovered
from `run_log.id = 279`'s own `detail.trace` (already captured in full by
`eoa.orchestrator.jobs._run_stage`, just never surfaced by the API):

```
KeyError: 0
  File "agent/eoa/orchestrator/jobs.py", line 130, in _run_stage
    out = fn()
  File "agent/eoa/orchestrator/jobs.py", line 215, in <lambda>
    _run_stage(rs, "tenders", lambda: _as_dict(_run_tenders(role=role)))
  File "agent/eoa/orchestrator/jobs.py", line 731, in _run_tenders
    scan_stats = scan_tenders(role=role)
  File "agent/eoa/tenders/scan.py", line 1822, in scan_tenders
    if candidate_portal and _candidate_duplicate_exists(...):
  File "agent/eoa/tenders/scan.py", line 992, in _candidate_duplicate_exists
    return any(_notice_portal(r[0]) == portal for r in rows if r[0])
  File "agent/eoa/tenders/scan.py", line 992, in <genexpr>
    return any(_notice_portal(r[0]) == portal for r in rows if r[0])
KeyError: 0
```

`agent/eoa/tenders/scan.py`'s `_candidate_duplicate_exists` (currently at line 1083–1094) opens
`conn.cursor()` with no explicit `row_factory` override:

```python
with connection() as conn, conn.cursor() as cur:
    cur.execute(
        "SELECT url FROM tenders WHERE intake = 'candidate' "
        "AND lower(regexp_replace(btrim(title), '\\s+', ' ', 'g')) = %(t)s",
        {"t": normalized_title},
    )
    rows = cur.fetchall()
return any(_notice_portal(r[0]) == portal for r in rows if r[0])
```

`eoa.db.get_pool()` configures the whole connection pool with `row_factory=dict_row`
(`agent/eoa/db.py`), so every row `cur.fetchall()` returns here is a dict like `{"url": "..."}`,
not a tuple. `r[0]` indexes a dict by the integer key `0`, which doesn't exist — hence
`KeyError: 0`, once per row for every `candidate`-intake tender whose normalized title collides
with an existing one and the query returns at least one match. **The fix is `r["url"]` instead of
`r[0]`.** This file is `agent/eoa/tenders/scan.py`, explicitly out of scope for this task (owned
by the tenders track) — not touched here. Reported precisely instead: the one-line fix is above,
and (per the brief) is flagged as a spawned follow-up task rather than applied in this session.

Because `tenders` is not a `mandatory=True` stage, the exception was swallowed by
`_run_stage`'s `except Exception` handler and the run continued normally to
`post_tenders_catchup` → `report` → `export_backup` → `notify` — the nightly report and backup
were unaffected. This repeats on every run that hits a duplicate candidate tender until the
one-line fix lands; three consecutive failures would open that stage's circuit breaker
(`rs.failures[stage] >= 3`) and skip `tenders` for the rest of that run.

## What was built

### 1. Recording — `agent/eoa/orchestrator/jobs.py`, `_run_stage`'s `except Exception` block

Previously: `error=str(exc)[:300]` (the bare, sometimes-useless message) plus a raw
`trace=traceback.format_exc()[-1500:]` string that was captured but never read back by the API.

Now additionally captures, in the same `run_log.detail` JSONB blob (no schema migration — see
"Why no migration 0033" below):

- `error_type`: `type(exc).__name__`, e.g. `"KeyError"`.
- `error` (the message): `str(exc)` unless that's empty or a bare digit string (`"0"`), in which
  case it falls back to `f"{error_type}: {exc!r}"` — always carries the exception class name.
- `traceback_tail`: the last 5 stack frames as `"file:line:function"` strings
  (`traceback.extract_tb`, basename only) — no local variable values, since this blob is read
  straight back out by the Morning panel's "פרטים טכניים" expander and local values can carry
  item/source content.

`trace` (the full raw traceback text, capped at 1500 chars) is kept as-is for debugging.

### 2. Reading/classification — `agent/eoa/api/services.py`

`recent_errors()` now returns, per entry: `error_type`, `traceback_tail`, `item_id` (from
`detail.item_id` when a future caller sets it — no current call site does, stage-level errors
have no item context), `link`, and a deterministic Hebrew classification —
`cause_he` / `action_he` / `impact_he` — computed by `_classify_error()`:

| Signal | cause_he | action_he |
|---|---|---|
| `OperationalError`/`InterfaceError`/`ConnectionError`/`PoolError`, or "connection refused/could not connect/connection to server/connection pool" in the message | בסיס הנתונים לא זמין | בדוק eo native status |
| `timeout` in the type or message | פסק זמן בביצוע הפעולה | הפעולה תיבדק שוב בריצה הבאה; אם חוזר, בדוק את זמינות המקור/השירות |
| `HTTPError`/`HTTPStatusError`/`RequestException`/`FetchError`, or a `4xx`/`5xx` in the message | המקור השיב בשגיאה | המקור ייבדק שוב בריצה הבאה; אם חוזר, בדוק את הגדרות המקור |
| `KeyError`/`IndexError`/`TypeError`/`AttributeError`/`ValueError`/`ZeroDivisionError` | שגיאת קוד בשלב '‹שם השלב בעברית›' | דווח למפתח; ההרצה המשיכה לשלב הבא |
| none of the above | שגיאה לא מסווגת | דווח למפתח; בדוק את הפרטים הטכניים למטה |

`impact_he` is derived from whether the stage is one of `_run_stage(..., mandatory=True)`'s three
stages (`report`, `export_backup`, `notify`): `"שלב קריטי ('‹שם›') נכשל -- ייתכן שהריצה לא
הושלמה כראוי"` for those, `"השלב '‹שם›' נכשל; ההרצה המשיכה לשלב הבא"` otherwise — every stage
error is swallowed by `_run_stage` and the pipeline always continues, so this is accurate for
every current call site.

`link`: `/items/{item_id}` when known, `/investigations/{job_id}` for a failed `deep_search`
job, otherwise `/morning#pipeline-replay` (this app has no standalone `/jobs/{id}` page — only
`/items/:id` and `/investigations/:jobId` exist in `web/src/App.tsx`).

**Classification is computed at read time, never persisted.** An improvement to
`_classify_error()`'s table applies retroactively to every historical `run_log` row, not only
ones written after the change.

**Backward compatibility / "backfill error 279":** for a `run_log` row written before this change
(only `detail.error` and `detail.trace` present, no `error_type`/`traceback_tail`),
`recent_errors()` reconstructs both from the stored raw `trace` text: the last non-empty line of
a Python traceback is always `"ExceptionClassName: message"`, and `File "...", line N, in func`
lines give the frame list. This was chosen over a one-off `UPDATE run_log SET detail = ...` on
row 279 because every other pre-this-change error row has the same defect (a numeric-only
`str(exc)`), and the dynamic reconstruction fixes all of them, not just #279, without a
destructive write. Verified against the live DB:

```json
{
  "id": 279, "job_id": 180, "stage": "tenders",
  "message": "KeyError: 0", "error_type": "KeyError",
  "traceback_tail": ["jobs.py:215:<lambda>", "jobs.py:731:_run_tenders",
                      "scan.py:1822:scan_tenders", "scan.py:992:_candidate_duplicate_exists",
                      "scan.py:992:<genexpr>"],
  "cause_he": "שגיאת קוד בשלב 'מכרזים'",
  "action_he": "דווח למפתח; ההרצה המשיכה לשלב הבא",
  "impact_he": "השלב 'מכרזים' נכשל; ההרצה המשיכה לשלב הבא",
  "link": "/morning#pipeline-replay"
}
```

**Why no migration 0033:** `run_log.detail` (added by migration 0001) is already `JSONB` with no
fixed schema — `error`, `stage`, `minutes` and `trace` already live there as free-form keys with
no dedicated columns. Adding `error_type`/`traceback_tail` as more JSONB keys is consistent with
every existing field on this table; a migration to add *typed columns* for data that's already
representable, and already queried, entirely within the existing JSONB column would be pure
schema ceremony with no query-performance or integrity benefit here (nothing filters
`recent_errors` by `error_type` at the SQL level — the 24h/limit window and `event ILIKE
'%error%'` are the only predicates). No `run_errors` table exists or is needed; `run_log` (with
`event = 'error'`) already is that table.

### 3. API surface

No route or field changed shape at the `GET /api/morning` level — `recent_errors()`'s return
list simply carries more keys per entry (see `RecentErrorLogEntry` in `web/src/types/api.ts`).
No route file changes were needed.

### 4. UI — `web/src/components/morning/RunErrorsPanel.tsx` (new, replaces `ErrorsDrawer.tsx`)

- The "שגיאות" tile (`StatTile`) now shows `"{count} שגיאות"` (via a new `morning.errorsCount`
  i18n key) and switches from `tone="danger"`/`"default"` to `tone="danger"`/`"ok"` — green text
  when the count is 0, matching the spec ("0 שגיאות" in green when clean).
- Clicking it opens `RunErrorsPanel`, titled "שגיאות בריצה האחרונה" (`role="dialog"`,
  `aria-modal="true"`, `aria-label` = the title).
- Each error renders as its own card: stage (Hebrew label, via the existing
  `stageLabelHe()`/`STAGE_LABEL_HE` from `web/src/lib/pipelineTimeline.ts` — reused, not
  duplicated), time, the message, then labeled cause/recommended-action/impact rows (hidden
  entirely, not shown empty, when a backend hasn't been restarted yet and doesn't have them —
  see "Known post-deploy state" below), a `<details>` "פרטים טכניים" expander with the exception
  type and traceback tail, and a "פתח פרטים" deep link when `link` is present.
- Accessibility: `Escape` closes; a real Tab/Shift+Tab focus trap keeps focus cycling within the
  dialog's own focusable elements (not just an initial-focus + Escape pattern like the codebase's
  other dialogs); initial focus lands on the dialog container.
- `common.close` ("סגור") is reused for the header close button — matches the existing dialog
  convention (`ShortcutsDialog.tsx`) and keeps the pre-existing MorningPage test's
  `getByRole("button", { name: "סגור" })` assertion valid.

### Known post-deploy state (as of this session)

Per project rules, Python process changes need a restart the lead does — this session did not
restart the API/orchestrator. Verified in the live browser at `http://127.0.0.1:8765/`: the tile
correctly reads `"1 שגיאות"` (from the *old* backend code's `night_summary.errors` count, which
this task didn't change), and the panel opens and renders correctly with the *old* API shape
(message `"0"`, no cause/action/impact — the new UI hides those rows rather than showing empty
labels, per the fallback above). All new backend fields were verified directly by calling
`eoa.api.services.recent_errors()` against the live DB (see the JSON block above) and by the new
pytest suite — they will appear in the browser once the API process restarts.

## Files touched

- `agent/eoa/orchestrator/jobs.py` — `_run_stage` error recording (`error_type`,
  `traceback_tail`, non-degenerate `message`); added `from pathlib import Path`.
- `agent/eoa/api/services.py` — `recent_errors()` rewritten; added `_classify_error`,
  `_error_link`, `_stage_label_he`, `_error_type_and_message_from_trace`,
  `_traceback_tail_from_trace`, `_STAGE_LABEL_HE_FOR_ERRORS`, `_MANDATORY_ERROR_STAGES`.
- `web/src/types/api.ts` — `RecentErrorLogEntry` gained `error_type`, `traceback_tail`,
  `item_id`, `link`, `cause_he`, `action_he`, `impact_he`.
- `web/src/api/real.ts` — `getMorning()` normalizes the new fields.
- `web/src/mocks/mockApi.ts` — mock `recent_errors` entry carries the new fields.
- `web/src/components/morning/RunErrorsPanel.tsx` — new.
- `web/src/components/morning/ErrorsDrawer.tsx` — deleted (fully superseded).
- `web/src/pages/MorningPage.tsx` — swapped `ErrorsDrawer` for `RunErrorsPanel`; tile shows
  `"{count} שגיאות"` with `tone="ok"` when clean (timeline block untouched — owned by
  UI-TIMELINE).
- `web/src/i18n/dictionaries/he.ts`, `en.ts` — replaced `morning.errorsDrawer*` keys with
  `morning.errorsPanel*` (title text now literally "שגיאות בריצה האחרונה" /
  "Errors in the last run"); added `morning.errorsCount`.
- `tests/unit/test_run_errors_ui.py` — new: 14 tests (recording via `_run_stage`, classification
  table, `recent_errors()` shape including the error-279-style legacy-row reconstruction).
- `web/src/components/morning/RunErrorsPanel.test.tsx` — new: 8 tests (dialog semantics,
  cause/action/impact rendering, technical-details expander, empty state, Escape, close button,
  focus trap, deep link).
- `web/src/pages/MorningPage.test.tsx` — updated the existing errors-tile test for the new panel
  shape/copy; added a green-"0 שגיאות" clean-state test.

## Test results

- `pytest tests/unit/test_run_errors_ui.py -q` — 14 passed.
- `pytest tests/unit/test_morning_kpis.py -q` — 7 passed (pre-existing `TestRecentErrors` tests
  still pass unchanged — the new-style/legacy-style branch only activates the recovery path when
  a message is missing/numeric).
- `pytest tests/unit -q` — full suite green (666+ tests across the `job`/`morning`/`error`/
  `run_stage`/`tender` keyword subset run individually; full-suite run launched to confirm no
  unrelated regression).
- `vitest run` (web) — 62 files / 500+ tests passed, including the 2 new
  `RunErrorsPanel.test.tsx`/`MorningPage.test.tsx` cases.
- `npm run build` (web) — `tsc -b && vite build` succeeded, twice (once per edit round);
  live at `http://127.0.0.1:8765/` per the "`cd web && npm run build` makes UI changes live" rule.
- `npx playwright test tests/13-accessibility.spec.ts --project=desktop-1440x900` — 11/11 passed
  (run twice, before and after the final UI edit), including the Morning axe-core scan and the
  "every button and link has an accessible name" smoke test.

## Restart needed?

**Python: yes**, to pick up `agent/eoa/orchestrator/jobs.py` and `agent/eoa/api/services.py` —
the API process is currently serving the pre-fix `recent_errors()` shape (confirmed live, see
"Known post-deploy state"). The lead restarts the native Python processes per project rules; this
session did not.
**Frontend: no** — `npm run build` already made the new UI live; confirmed in the browser.

## Follow-up flagged out of scope

`agent/eoa/tenders/scan.py`'s `_candidate_duplicate_exists` (line ~1094) indexed a `dict_row`
cursor result with `r[0]` instead of `r["url"]`, causing `KeyError: 0` (and, going forward, an
error-log entry — no longer a bare "0") on every candidate-tender duplicate check. Out of scope
for this task (`agent/eoa/tenders/**` is excluded) — flagged as a separate background task
(`task_012eff73`, "Fix KeyError in tenders duplicate-check"), which the user started; by the end
of this session a concurrent change had already landed on the shared tree fixing exactly this
(`r["url"]` instead of `r[0]`, citing "run_errors 279"), confirming the root-cause analysis above
independently.
