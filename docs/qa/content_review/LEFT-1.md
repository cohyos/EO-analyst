# LEFT-1: three small leftovers (dossier pricing, dossier progress, forecast softening)

Investigated and closed 2026-09-08.

## 1. Dossier pricing `basis_he` consistency

**File:** `agent/eoa/dossier/extract.py`.

`_ground_price_row`'s post-check previously only checked `basis_he` for *presence* (`if not
row.basis_he.strip(): drop`) -- it never verified the model actually wrote one of the three
literal forms the prompt (`agent/eoa/llm/prompts/product_dossier_extract.md`) asks for
("ליחידה" / "למנה של N יחידות" / "לתוכנית כולה"). Live evidence this drifts: the SPECTRO XR
dossier's real price row carried `basis_he = "לחוזה כולל (לא צוין מחיר ליחידה)"` -- not any of
the three prompted forms, but not garbage either.

**Fix:** added `_classify_price_basis` (deterministic, no LLM call, same "code, not another model
call" discipline the module's docstring already commits to) that maps whatever free-text
`basis_he` the model wrote into exactly one of three canonical forms:

- contract/programme total -> `"היקף חוזה (לא מחיר ליחידה)"` (`extract.BASIS_TOTAL_HE`) --
  matched by an explicit "no per-unit price stated" negation (checked first, since a phrase like
  "לא צוין מחיר ליחידה" contains the word "ליחידה" itself and would otherwise false-match the
  per-unit keywords below), or by a contract/programme/total keyword (חוזה/תוכנית/כולל/היקף/
  contract/programme/total).
- per-unit -> `"מחיר ליחידה"` (`extract.BASIS_UNIT_HE`) -- matched by a per-unit keyword
  (ליחידה/יחידה/per-unit/unit price), checked only after the negation/total checks above.
- a lot of N units -> `"למנה של N יחידות"`, N read off the model's own text (never invented) --
  matched first (before total/unit), since it is the most specific pattern and its own text
  ("למנה של 5 יחידות") also contains "יחידות", which would otherwise false-match the per-unit
  keywords.
- anything else (no keyword match at all, e.g. "מחיר משוער") -> unparseable -> the whole row is
  dropped and logged via `_drop(..., "pricing", f"unparseable basis_he: ...")` ->
  `dossier.field_dropped`, same convention every other post-check drop already uses.

`_ground_price_row` now calls this right after the existing "missing basis_he" check and before
the citation/number-grounding checks; a row that survives every check is returned with
`basis_he` replaced by the canonical form (`row.model_copy(update={..., "basis_he":
canonical_basis})`), never the model's original free text.

**Tests** (`tests/unit/test_product_dossier_extract.py`): `test_classify_price_basis_contract_
total_spectro_xr_live_case` (the exact SPECTRO XR string), `test_classify_price_basis_per_unit`,
`test_classify_price_basis_lot`, `test_classify_price_basis_unparseable_returns_none`,
`test_price_row_spectro_xr_contract_total_is_normalized_and_kept` (full `ground_dossier` round
trip), `test_price_row_unparseable_basis_is_dropped`; the pre-existing `test_price_row_fully_
grounded_is_kept` was strengthened to also assert the normalized `basis_he` value.

## 2. Per-topic progress lands in the wrong jsonb column

**Files:** `agent/eoa/dossier/report.py` (`_write_job_progress`), `agent/eoa/api/services.py`
(`_pending_dossier_job`).

**What I found reading job 194's row live (read-only, via `eoa.db.connection()`):**

```
id 194        kind product_dossier      state done
created_at   2026-09-08 14:51:13+03     started_at 2026-09-08 14:51:20+03
finished_at  2026-09-08 15:56:28+03
payload keys: ['aliases', 'budget_multiplier', 'product_key', 'product_line', 'product_name', 'vendor']
result keys:  ['product_dossier']
payload.progress: null
result.progress:  null
```

(`run_log` has zero rows for `job_id=194` -- `eoa.dossier.plan.run_plan`'s `on_progress` callback
never calls `eoa.memory.relational.heartbeat`, so `run_log` is not a source of evidence here
either way.)

**Root cause:** the original PD-fix (item 5) wired `_write_job_progress` to merge into
`jobs.result -> 'progress'` while the job is `running` (`UPDATE jobs SET result = COALESCE
(result, '{}'::jsonb) || patch WHERE id = ... AND state = 'running'`), and `_pending_dossier_job`
read it back from the same column. That merge itself is fine while the job runs -- but
`eoa.memory.relational.finish_job` (called once, when the job reaches a terminal state)
**REPLACES `result` wholesale** (`result = %(result)s`, not a jsonb merge) with whatever the job
handler returned (`{"product_dossier": {...}}` for this job kind). So the instant job 194 finished,
its mid-run progress list was overwritten and is gone -- exactly what the live row shows: `result`
now holds only `product_dossier`, no `progress` key anywhere. A waiter polling `jobs.result->
'progress'` *while the job was still running* should have seen it (nothing in `finish_job` runs
until the job is done, and `claim_next_job` flips `state` to `'running'` the instant a worker
claims the job, before the handler is ever invoked) -- but `result` is also not a location safe to
document, since it silently reverts to progress-free the moment the job finishes, and a second job
kind reusing the same "write progress into `result`" pattern would each need to remember that
`finish_job` will eventually erase it. The `jobs.payload->>'progress'` a waiter reportedly polled
during job 194 was reading a column `_write_job_progress` never wrote to at all -- so it saw
nothing not because of a race, but because progress genuinely never landed in `payload`.

**Fix:** standardized on `jobs.payload.progress` as the ONE documented location:

- `_write_job_progress` now does `UPDATE jobs SET payload = COALESCE(payload, '{}'::jsonb) ||
  %(patch)s::jsonb WHERE id = %(id)s AND state = 'running'` -- `payload` already carries the
  job's enqueue-time fields (`product_key`/`vendor`/...) and `finish_job` never touches `payload`
  at all (only `state`/`finished_at`/`result`/`error`/`not_before`), so a `progress` key merged in
  there survives for the job's entire `queued`/`running`/terminal lifetime, unlike `result`.
- `_pending_dossier_job` now selects `payload` instead of `result` and reads `payload.get
  ("progress")`.
- Both docstrings updated to state `jobs.payload.progress` as the single source of truth and to
  record why `result` was wrong (this investigation).

**Tests:**
- `tests/unit/test_product_dossier_services.py`: the two existing `pending_job` tests
  (`test_dossier_detail_includes_pending_job`, `test_dossier_detail_pending_job_surfaces_
  progress`) updated to mock `payload` instead of `result` (both still monkeypatch `services.
  _fetchone`, no real DB -- pre-existing convention).
- `tests/unit/test_product_dossier_report.py`: new `test_write_job_progress_round_trips_through_
  service` -- a fake in-memory `jobs` table + fake cursor applying the same jsonb `||` merge
  semantics Postgres would (mirrors `tests/unit/test_backfill_analysis_gaps.py`'s existing "fake
  DB" convention), simulates **two** `on_progress`-style calls to `dossier_report.
  _write_job_progress` (the first topic starting, then finishing while the next starts -- the
  same sequence `eoa.dossier.plan.run_plan` produces), asserts the enqueue-time `payload` key
  (`product_key`) survives the merge, then reads the persisted list back through `eoa.api.
  services._pending_dossier_job` and asserts it matches the second call's list exactly.

## 3. Unsoftened intensifier in a forecast-table cell (weekly/monthly)

**File:** `agent/eoa/tenders/report_section.py`.

Grepped `agent/eoa/tenders/report_section.py` and `agent/eoa/payloads/report_section.py` for
`"ייתכן ש"`/`"so_what"` -- neither literal string appears in either file (the payloads module has
no free-text cells at all, only formatted numbers/dates). The forecast module's equivalent
free-text field is `tender_forecasts.rationale_he` (the forecast-drafting LLM stage in
`eoa.tenders.forecast`, out of this task's owned scope) -- rendered into a report table cell by
this module's `tenders_extra_section` (`_trim_rationale(f.get('rationale_he') or '')`, currently
dead/superseded code kept for back-compat -- see `agent/eoa/report/daily.py`'s own comment) and,
in the live pipeline, by `eoa.report.daily._tenders_forecast_table` (also outside this task's
owned scope). Both read `rationale_he` straight from `collect_tenders`'s `new_forecasts` return
value, unlike `eoa.report.israel_section`/`eoa.report.tech_watch`, which already pass their own
free-text cell (`so_what_he`) through `eoa.report.claims_gate.soften_text` before rendering.

**Fix:** added `_soften_forecast_rationales` (mirrors `claims_gate.gate_item_texts`'s "soften if
present, else leave alone" convention) and call it once in `collect_tenders`, right after
`dedupe_forecasts_by_topic`, mutating every forecast's `rationale_he` in place before the function
returns. Because every downstream forecast-table renderer (this module's own `tenders_extra_
section`, and `eoa.report.daily`'s `_tenders_forecast_table`, wired into the daily/weekly/monthly
`extra_sections`/`tables` hooks per this module's own docstring) reads `rationale_he` from
`collect_tenders`'s output, softening it once at the shared collection point fixes every renderer
without touching `eoa.report.daily` (out of scope for this task) or duplicating the gate call
per-renderer.

**Tests** (`tests/unit/test_tenders_report_section.py`): `test_collect_softens_unsupported_
intensifier_in_forecast_rationale` -- uses the exact live sentence from the task brief
("ייתכן שהמימוש המסחרי של הפלטפורמה יאפשר להוזיל משמעותית עלויות אימון."), asserts "משמעותית" is
gone from the persisted `rationale_he` but the rest of the sentence survives (softened, not
dropped -- `_has_quantity` finds no digit, `TRIGGER_RE` matches "משמעותית", `_is_vacuous` finds
plenty of content tokens left after the trigger phrase is stripped); and `test_collect_leaves_
unflagged_rationale_untouched` -- confirms the pre-existing fixture rationale ("נימוק לדוגמה
[item 10]", no trigger word) round-trips unchanged, so nothing else in `collect_tenders`'s
existing test coverage regressed.

## Verification

`ruff check` clean on every touched file. Full targeted run:

```
PYTHONUTF8=1 PYTHONPATH=agent python -m pytest \
  tests/unit/test_product_dossier_extract.py tests/unit/test_product_dossier_report.py \
  tests/unit/test_product_dossier_services.py tests/unit/test_product_dossier_api.py \
  tests/unit/test_product_dossier_corpus.py tests/unit/test_product_dossier_diff.py \
  tests/unit/test_product_dossier_plan.py tests/unit/test_product_dossier_schema.py \
  tests/unit/test_tenders_report_section.py tests/unit/test_reports_round4.py \
  tests/unit/test_round6_forecast.py -q
```

242 passed, 1 pre-existing unrelated deprecation warning (`starlette.testclient`'s `anyio`
alias). No test skipped/xfailed/stubbed.
