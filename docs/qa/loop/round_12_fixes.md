### R12-reports status

**Package:** R12-reports (docs/qa/loop/round_11_judge.md, D6 = 87/D7 = 88; worst-list #3, #4, #8,
#9).
**Files owned/changed:** `agent/eoa/report/monthly.py` (indicator-watchlist wiring +
exercise/operation entity filter), `agent/eoa/report/indicators.py` (per-story/8-row cap widened
to every report kind), `agent/eoa/report/product_line.py` (item scope filter + patent dedupe only),
`tests/unit/test_reports_round12.py` (new, 22 tests), `tests/unit/test_reports_round8.py` (one
existing test updated in place -- its own assertion contradicted this round's intended behavior
change, see finding #2 below), `db/migrations/versions/0029_indicator_watchlist_monthly.py` (new --
required for finding #1 to actually persist against the live DB; outside this round's named file
list but the same widen-the-CHECK pattern migration 0028 used for `reports.kind`/'product_line',
added because the feature is inert without it). `agent/eoa/report/trends.py`/`weekly.py` needed no
change beyond what's noted under finding #2 (the round-11 leak traced entirely to `monthly.py`'s
own `players_map`/`watchlist_changes`, not to `trends.py`; the weekly cap change is a one-line gate
removal inside `indicators.py`, not a `weekly.py` edit).

#### 1. Monthly had no indicator-tracking section at all (worst #3)

**Root cause:** `eoa.report.monthly.build_monthly` never called
`eoa.report.indicators.build_indicator_watchlist_section` -- `daily`/`weekly` both did (one call
each, `kind="daily"`/`kind="weekly"`), monthly simply never got the equivalent block. Fixed by
adding the identical three-line try/except block `daily`/`weekly` already use, passing
`kind="monthly"`, right after the trend-sections block in `build_monthly` (before the deterministic
players/top-events/horizon/watchlist tables). A failure inside it is caught and logged
(`monthly_report_indicator_watchlist_failed`), never breaking the build -- same contract as every
other additive `extra_sections`/`tables` block in this function (`_israel_month_tables`, the
patents-landscape block, etc.).

**Schema gap found and closed, not left for a live build to discover:** `indicator_watchlist.kind`
(migration `0023`) has `CHECK (kind IN ('daily', 'weekly'))` -- correct when only two report kinds
called into `eoa.report.indicators`. Passing `kind="monthly"` would `INSERT` a `kind='monthly'` row
the first time a monthly outlook raises a new indicator, violating that CHECK exactly the way
migration `0028`'s own docstring describes for `reports.kind`/'product_line' (caught live there
after a build failed; caught here before one could). Added
`db/migrations/versions/0029_indicator_watchlist_monthly.py`, same widen-the-CHECK pattern as 0028,
`revision="0029"`, `down_revision="0028"`.

**Unit-tested** (`TestMonthlyIndicatorWatchlistWiring`, `tests/unit/test_reports_round12.py`):
`build_monthly` calls `indicators.build_indicator_watchlist_section` with `kind="monthly"`
(spied), the returned section's own `"מעקב אינדיקטורים"` heading reaches the rendered `.md`, and a
raised exception from that call does not break the build (`paths.qa.passed` stays true, the `.md`
file is still written). These tests monkeypatch every DB-touching collector (including
`_israel_month_tables` and `eoa.patents.report_section.collect_patents_landscape`, both otherwise
real-DB calls inside `build_monthly`'s own try/except blocks) so they run with **no live Postgres
required** -- they passed locally in 1.86s.

**Live verification -- BLOCKED on DB access, not on code.** `runtime/pgdata/postgresql.conf` shows
`max_connections = 60`; every `psycopg.connect()` attempt this session made (12+ retries, 8-45s
`connect_timeout` each, spanning several minutes) returned `psycopg.errors.ConnectionTimeout`, no
exception. `git status` shows `agent/eoa/api/ask_grounding.py`/`agent/eoa/api/routes/ask.py`/
`tests/unit/test_ask_round12.py` already modified by a concurrent round-12 package (R12-chat, out
of this package's file scope) -- consistent with a sibling agent's own live API/chat testing
saturating the connection pool for the duration of this package's work, not a DB outage this
package caused or can fix (no process was killed, no `git stash`/`reset`/`checkout` run, per the
standing rules).

**What the lead needs to run once DB access frees up** (not run by this package):
```
set -a; . runtime/eoa.env; set +a
PYTHONUTF8=1 .venv/Scripts/python.exe -m alembic upgrade head   # applies 0029
PYTHONPATH=agent PYTHONUTF8=1 EOA_PIPELINE=1 .venv/Scripts/python.exe -c "
import datetime as dt
from eoa.report.monthly import build_monthly
paths = build_monthly(period_end=dt.date(2026, 9, 7))
print(paths)
"
```
then grep the resulting `output/reports/monthly_2026-09-30.md` for `"מעקב אינדיקטורים"` (should be
present, unlike the round-11 live file) and re-run
`eoa.qa.d6_daily_report.score_D6(md_path=<that monthly path>, monthly_path=<same path>)` -- per
round 11's own status doc, `indicator_watchlist_table_present` was one of the checks explicitly
exempted as "daily/weekly-only, expected fail on a monthly" when scored against a monthly file;
with this fix that check should now genuinely pass rather than needing the exemption (worth
re-confirming live, and dropping from the exemption list in `d6_daily_report.py` if a future round
owns that file).

#### 2. Weekly indicator table exceeded its own <= 8-row cap (worst #4)

**Root cause:** `indicators.render_watchlist_table`'s per-story-cluster + 8-row-total cap
(`_cap_watchlist_rows`, added round 8) only ran `if kind == "daily"` -- the round-7 judge that
motivated it only reported the daily table as over-crowded, so weekly/monthly were left uncapped
by design at the time. Round 11 found the weekly table had since grown to 10 rows against the
brief's own <= 8 cap (2 with no evidence). Fixed by removing the `kind == "daily"` gate entirely --
`_cap_watchlist_rows(rows)` now runs unconditionally inside `render_watchlist_table`, for every
`kind` (including the monthly table this same round adds, see finding #1) and for a bare
`kind=None` call. No change to `_cap_watchlist_rows` itself (`max_per_cluster=3`, `max_total=8`,
oldest-`first_seen`-first when trimming -- exactly what the brief asked for, already correct).

**One pre-existing test updated in place:** `tests/unit/test_reports_round8.py`'s
`test_render_watchlist_table_daily_applies_cap_but_weekly_does_not` asserted the pre-round-12
behavior (daily capped at 8, weekly left at 9) -- now factually wrong given this round's own
brief. Renamed to `test_render_watchlist_table_daily_and_weekly_both_apply_cap`, extended to also
assert `kind="monthly"` is capped at 8, with a comment explaining why the assertion changed rather
than leaving a stale test contradicting the new intended behavior.

**Unit-tested** (`TestIndicatorWatchlistCapAppliesToEveryKind`, parametrized over
`["daily", "weekly", "monthly", None]`; plus the updated round-8 test above): 10 distinct-topic
rows render exactly 8 data rows for every kind; 3 rows render all 3 (cap never fires below the
actual row count).

#### 3. "Operation Atlantic City" leaked into unrelated landscape/glossary tables (worst #9, D3/D6)

**Root cause confirmed by reading the live round-11 files directly** (`output/reports/
monthly_2026-09-30.md`): a NATO exercise name that renders correctly in the events table via
`events.program` (`top_events_by_amount`, line 89: `| ... | Kongsberg | Operation Atlantic City |
...`) *also* appears as its own row in two unrelated entity-shaped tables built straight from the
`entities` table: line 369, the "נוף תחרותי" competitive-landscape table (`players_map`) --
`| Operation Atlantic City | 0 | 0 | 0 |`, a fake player with zero competitor/supplier/partner
edges; and line 595, the "שינויים ברשימת המעקב" watchlist bullet list (`watchlist_changes` via
`format_watchlist_he`) -- `- Operation Atlantic City — program`. Both collectors query `entities`
directly (`players_map` joins `entities`/`items` for domain inference + graph edge counts;
`watchlist_changes` selects `entities.created_at` in the period) with no awareness that the
upstream entity-extraction pipeline (out of this round's file scope) had created an `entities` row
for the exercise name alongside its correct `events.program` value.

**Fix is a name-pattern filter, not a `kind` filter:** the same live monthly's glossary list also
shows legitimate `kind='program'` entities ("Arctic Sentry", "Defense Innovation Unit (DIU)",
"Next-gen targeting pod") that must stay -- so `kind='program'` can't be the exclusion test. Added
`monthly._is_exercise_or_operation_label(name)` (English `^operation\s` prefix, case-insensitive,
or a bare Hebrew "תרגיל"/"מבצע" token anywhere in the name) and call it from both `players_map`
(row dropped before domain/edge-count aggregation) and `watchlist_changes` (rows filtered
post-fetch, before returning) -- `top_events_by_amount` needed no change, it already renders the
exercise correctly via `events.program` in the events table, exactly where the brief says it
belongs.

**Unit-tested** (`TestExerciseLabelFilter`, `TestPlayersMapExcludesExerciseLabels`,
`TestWatchlistChangesExcludesExerciseLabels`): the label test itself (English prefix
case-insensitivity, both Hebrew tokens, two real program-kind names from the live file that must
NOT match, a `"Operationally superior radar"` false-positive guard, `None`/empty), plus
`players_map`/`watchlist_changes` each with a fake `connection()` returning one legitimate row and
one "Operation Atlantic City" row -- the exercise row is dropped, the legitimate one survives, and
an all-exercise input yields an empty map/list. A `format_watchlist_he` regression test confirms
the exercise name never reaches the rendered bullet text once filtered upstream.

**Live verification** blocked on the same DB-access issue as finding #1 (see above) -- covered by
the migration/build/D6 command block there; grep the resulting monthly `.md`/`.html` for
`"Operation Atlantic City"` and confirm it appears ONLY inside the events table (line ~89-shape),
never inside the "נוף תחרותי" or "שינויים ברשימת המעקב" sections.

#### 4. pl_mws_eo built on an out-of-scope item, duplicate patent rows (worst #8, D7)

**Item-scope root cause:** `product_line.collect_market_items`'s own `WHERE` clause filtered
`level = ANY(_INSCOPE_LEVELS)` (`red`/`orange`/`yellow` -- `'archive'` already excluded by omission
from that list, satisfying the brief's "level != archive" half on its own) but never filtered
`domain <> 'out_of_scope'` the way every other report collector in this codebase does (e.g.
`eoa.report.monthly.top_events_by_amount`/`watchlist_changes`'s own
`COALESCE(domain, '') <> 'out_of_scope'` clause). A `product_lines` tag is applied independently of
the domain/level scope gate (tagging owned by another package), so nothing stopped an
out-of-scope-tagged item from driving a product-line report. Added
`AND COALESCE(domain, '') <> 'out_of_scope'` to `collect_market_items`'s SQL, matching the
established pattern exactly.

**Patent dedupe:** added `product_line._dedupe_patents_by_pub_number`, called from the end of
`collect_patents` -- keeps the first row seen per non-null `pub_number` (the query is already
`ORDER BY COALESCE(publication_date, filing_date) DESC`, so "first seen" is "newest", per the
brief); a row with a `None`/empty `pub_number` can't be identified as anyone's duplicate by this
key, so every such row is kept rather than being collapsed into one bucket.

**Unit-tested** (`TestProductLineItemScope`, `TestPatentDedupe`): the SQL text itself is asserted
to contain the new `out_of_scope`/`domain` clause (via a monkeypatched `_fetchall` spy, no DB
needed); `_dedupe_patents_by_pub_number` keeps the first (newest) of two same-`pub_number` rows and
drops the older duplicate, and never collapses two distinct `pub_number=None` rows together;
`collect_patents` itself is shown to apply the dedupe end-to-end via a monkeypatched `_fetchall`
returning two same-`pub_number` rows.

**Live verification (pl_mws_eo item count, patent-row uniqueness, per-line tag coverage SQL
count)** blocked on the same DB-access issue as findings #1/#3. Commands for the lead to run once
DB access frees up:
```
set -a; . runtime/eoa.env; set +a
PYTHONPATH=agent PYTHONUTF8=1 EOA_PIPELINE=1 .venv/Scripts/python.exe -c "
from eoa.report.product_line import build_product_line
print(build_product_line('mws_eo'))
"
```
then confirm the market-items table's row set no longer includes the previously out-of-scope item,
and the patents table has zero repeated `pub_number`/title pairs. Per-line tag coverage (informal
SQL the lead can run directly, matching round 11's worst-#7 phrasing "N/63 in-scope items, N/9
tenders, N/86 patents"):
```sql
SELECT unnest(product_lines) AS line, count(*) FROM items
WHERE domain <> 'out_of_scope' AND level IN ('red','orange','yellow') GROUP BY 1 ORDER BY 1;
SELECT unnest(product_lines) AS line, count(*) FROM patents GROUP BY 1 ORDER BY 1;
SELECT unnest(product_lines) AS line, count(*) FROM tenders GROUP BY 1 ORDER BY 1;
```
(round 11's own worst-#7 already flagged coverage as thin and likely by-design, not a defect this
package owns -- repeated here only because the brief asked for the number and this round's item-
scope fix changes the in-scope-item denominator slightly.)

#### Tests

New: `tests/unit/test_reports_round12.py`, 22 tests (see per-finding sections above for what each
group covers). One existing test updated in place:
`tests/unit/test_reports_round8.py::TestRenderWatchlistTableWeeklyEvidence::
test_render_watchlist_table_daily_and_weekly_both_apply_cap` (renamed + extended, see finding #2).

**Required run, green, no DB needed (all DB-touching pieces are monkeypatched in these six
files):**
```
PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest \
  tests/unit/test_reports_round12.py tests/unit/test_reports_round11.py \
  tests/unit/test_reports_round10.py tests/unit/test_weekly_round6.py \
  tests/unit/test_product_lines.py tests/unit/test_reports_round8.py \
  -q -p no:cacheprovider
# 207 passed in 14.20s
```

`ruff check`/`ruff format` on every changed/new file (`agent/eoa/report/{monthly,indicators,
product_line}.py`, `tests/unit/test_reports_round12.py`, `tests/unit/test_reports_round8.py`,
`db/migrations/versions/0029_indicator_watchlist_monthly.py`): clean.

#### What remains for a future round / the lead

- **Live verification of all four findings is blocked on DB access**, not on code (see each
  finding's own "Live verification" note above) -- `max_connections = 60`
  (`runtime/pgdata/postgresql.conf`) appears saturated by a concurrent round-12 package's own live
  API/chat testing (`agent/eoa/api/*` was already modified by another agent mid-session; not this
  package's doing, and per the standing rules this package killed no process and did not attempt
  to intervene). The exact commands to run once access frees up are given inline above (migration
  `0029`, one monthly build, one `pl_mws_eo` build, the D6 monthly check, the tag-coverage SQL).
- **Migration `0029`** widens `indicator_watchlist.kind`'s CHECK constraint. It is outside this
  round's named file list (only `agent/eoa/report/*.py` + tests + this status doc were assigned)
  but was necessary -- without it, finding #1's fix is silently inert against the live DB (the
  try/except around the new call swallows the CHECK violation and logs a warning, so the section
  simply never renders, with no visible error). Flagging explicitly in case the lead wants a
  different engineer to review/apply schema changes as a matter of round process, independent of
  this migration being correct.
- `eoa.qa.d6_daily_report.score_D6`'s `indicator_watchlist_table_present` check is currently
  exempted as "daily/weekly-only" when scoring a monthly file's own content via `monthly_path=`
  (round 11's own status doc) -- with finding #1 landing, that exemption is no longer accurate and
  should be revisited by whichever round next touches `eoa/qa/d6_daily_report.py` (out of this
  round's file scope).
- Round 11's own two pre-existing, still-open D6 checker-precision items
  (`no_row_repeated_across_tables`'s cross-table false positive; the duplicate-sentence scan not
  exempting the auto-generated cross-table dedup note) are untouched -- not this round's assigned
  findings.

### Lead fixes (round 12) -- documentation of commits made outside the agent packages

J12 flagged that two round-12 changes shipped without a status section. They were lead commits:

- `11aeebc` -- patent cluster headings: `_cpc_label` now returns CPC-class Hebrew titles with the code in
  parentheses (e.g. "מיגון והגנה אקטיבית (F41H11)") instead of a bare code, and no longer doubles the
  "אשכול טכנולוגי:" prefix (J11 D8 worst #2: 8/9 headings were raw codes). Both surveys were rebuilt.
- `485348d` + `02561e1` -- D8 checker: `no_unclassified_cluster_when_patents_exist` now treats a cluster as
  unclassified when the label is in the heading or listed as a cluster bullet/table cell; prose that
  mentions an unclassified assignee inside a classified cluster no longer trips it. Both surveys score 100.
- `5fa572f` -- investigation detail normaliser passes security-review / blocked-reason / confidence fields
  through (frontend), tested via the real request path.
- `bb4edb9` -- test expectation for the unknown-CPC fallback label.
