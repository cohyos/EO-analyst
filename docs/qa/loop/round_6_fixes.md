# Round 6 fixes

## R6-forecast package

### R6-forecast status

Scope: `docs/qa/loop/round_5_judge.md` findings D6/D9 (tender-forecast near-duplicates) and D3
(event-kind mismatch), as assigned in the R6-forecast task package. Files touched: only
`agent/eoa/tenders/forecast.py`, `agent/eoa/tenders/report_section.py`,
`agent/eoa/report/daily.py` (`_dedup_events`/`_event_has_signal`/`_sanitize_event_kind` and the
forecast-table builder only), `agent/eoa/pipeline/analyze.py` (event-kind classification for
exercises only), and `tests/unit/test_round6_forecast.py` (new). Did not touch
`agent/eoa/memory/relational.py` or `agent/eoa/tenders/scan.py` (R6-data's files).

#### Finding 1 — tender-forecast near-duplicate rows

**Root cause found**: not buyer_country volatility as initially hypothesised, but a Hebrew
punctuation drift. `platform_payloads.yaml` today uses a plain ASCII quote/apostrophe for Hebrew
abbreviations (`כטב"ם`, `רק"ם`, `מטע"ד`, `ג'ימבלי`), but a number of `tender_forecasts` rows were
written earlier with the Hebrew gershayim/geresh marks (`כטב״ם`, `רק״ם`, `מטע״ד`, `ג׳ימבלי`) — the
exact same platform, but different bytes. `tender_forecasts` has `UNIQUE (platform, buyer_country,
payload_need)` (db/migrations/versions/0004_tenders.py) on that literal text, so the punctuation
mismatch alone makes `ON CONFLICT` miss and insert a new row (with the window recomputed from
today) instead of updating the old one — exactly the "same platform/payload/reasoning text, window
shifted by a day or two" pattern in the finding. Confirmed by reading the live `tender_forecasts`
rows (id 5 vs 53, id 1 vs 59, id 2 vs 54, id 9 vs 57): each pair's `rationale_he` is about the same
underlying subject; the only reliable signal distinguishing genuine near-duplicates from real
distinct-buyer forecasts (id 6 vs 55, id 7 vs 56 — same platform text, genuinely different
`buyer_country`, kept separate by design) was the punctuation.

Fixes:
1. **Generation time** (`forecast.py`): new `normalize_hebrew_punctuation()` — canonicalises
   gershayim (`״`) → `"` and geresh (`׳`) → `'`. Applied in `PlatformSpec.__init__` so
   every candidate built from `platform_payloads.yaml` always writes the same canonical text
   regardless of which punctuation mark a future yaml edit uses, keeping `ON CONFLICT` working
   correctly going forward.
2. **Existing-row repair (dry-run + apply)** (`forecast.py`): `_forecast_stable_key()` — a
   normalised `(platform, buyer_country, payload_need)` identity ignoring window/likelihood/
   rationale/sources/dates. `find_duplicate_forecast_groups(rows)` — pure grouping function (no DB),
   returns `ForecastDuplicateGroup(key, ids, kept_id, dropped_ids)` for every group with >1 member
   (kept = most recently updated). `dedupe_existing_forecasts(conn, *, apply=False)` — reads all
   `tender_forecasts` rows, reports duplicate groups; with `apply=True`, merges each dropped row's
   `sources` into the kept row (order-preserving, deduped), stamps `updated_at`, and deletes the
   dropped rows. Does not commit — the caller's `with connection() as conn:` (or explicit
   `conn.commit()`) controls the transaction, same convention as every other `conn`-taking helper
   in this codebase.
3. **Report-table collection time** (`report_section.py`): `_forecast_topic_key()` now runs through
   `normalize_hebrew_punctuation()` before casefold, so a punctuation-drift pair collapses under the
   existing `dedupe_forecasts_by_topic` (W1) topic grouping even before the DB repair runs. Also
   added `_windows_close()`/`_cluster_by_window_proximity()`: within one topic group, rows are only
   merged if their windows overlap or are within `_FORECAST_WINDOW_MERGE_GAP_DAYS` (3) days of each
   other — two forecasts sharing a topic but describing genuinely distant windows now render as
   separate rows instead of being silently collapsed.

**Dry-run duplicate-group listing (live DB, 2026-09-07, `dedupe_existing_forecasts(conn,
apply=False)` — no writes)**:

```
3 duplicate group(s) found:
key=('כטב"ם טקטי/זעיר', 'EU', 'מטע"ד זעיר (micro-gimbal)')
  ids=[1, 59]  kept_id=59  dropped_ids=[1]
key=('תוכנית נגד כטב"ם (c-uas)', 'US', 'עוקבים eo/ir לגילוי-סיווג-עקיבה ואפקטורים')
  ids=[9, 57]  kept_id=57  dropped_ids=[9]
key=('רק"ם (apc/ifv/טנק)', 'EU', 'מטע"ד מפקד/תותחן (commander/gunner sights)')
  ids=[2, 54]  kept_id=54  dropped_ids=[2]
```

These three groups are the ones the DB-level (platform, buyer_country, payload_need)-exact key
flags for repair. The rendered report's third named pair (MALE UAV, `כטב"ם MALE`) is *not* in this
DB-level list because its two rows (id 5 buyer=GR, id 53 buyer=US) carry genuinely different
`buyer_country` values — kept as distinct DB rows by design (finding 1's spec: the stable key
includes buyer). It still rendered as a visible duplicate in `daily_2026-09-06.md`/`_07.md` purely
because the report table's own topic-key grouping (W1) ignores `buyer_country`; fix #3 above (the
`normalize_hebrew_punctuation` call in `_forecast_topic_key`) resolves that at render time
independently of the DB-level buyer-country difference.

**For the lead to apply the DB repair**:
```python
from eoa.db import connection
from eoa.tenders.forecast import dedupe_existing_forecasts

with connection() as conn:
    groups = dedupe_existing_forecasts(conn, apply=True)
    # conn.commit() happens automatically on successful exit of `connection()`
print(groups)  # ForecastDuplicateGroup list — same 3 groups as the dry-run above
```

#### Finding 2 — business-events near-duplicate (empty-fields copy)

`agent/eoa/report/daily.py`'s `_dedup_events` collapse key (`_normalize_event_key`: kind + parties +
customer + program) never caught a pair where one copy has every descriptive field empty and the
other has them populated (the finding's example: `2026-09-05 | אחר | — | — | 10,000,000 USD | [4]`
immediately followed by the same date/amount/kind with `US Air Force` / `Massed Modular Aircraft`
populated) — the two rows produce different content keys. Added a second dedup pass,
`_item_amount_kind_key()` / extended `_dedup_events`: groups by `(item_id, kind, date, amount_usd)`
after the existing content-key pass, keeping the richer (more populated fields) row. Deliberately
narrow — requires a concrete `item_id` *and* a non-null `amount_usd` on both sides, so two distinct
events sharing an item/date/kind but no (or different) monetary figures are never merged by this
pass; a row missing either field passes through unaffected, so the existing F9/F16 regression tests
(`test_report_daily.py::test_dedup_events_keeps_richest_of_duplicate_group` /
`test_dedup_events_keeps_distinct_kinds_separate`) still pass unchanged.

#### Finding 3 — event-kind mismatch (Kongsberg StrikeMaster / Operation Atlantic City)

Confirmed against the live DB: item 3702 produced three `events` rows sharing
`program='Operation Atlantic City'` — id 173 `kind='deployment'`, id 174 `kind='test'`
(`title='ניסוי מערכת ה-StrikeMaster בתנאים ארקטיים'`), id 175 `kind='partnership'`. Row 174 is not a
standalone weapon test; it is one facet of the same NATO Arctic exercise the sibling rows describe.
`events.kind` carries a DB CHECK constraint to the 9 `EventOut` literals (no `'exercise'` value
exists to write), so:

1. **Ingestion time** (`analyze.py`): new `_reclassify_exercise_kind()`, wired into
   `persist_analysis`'s events loop right before the narrative-title check / `insert_event` — a
   `kind='test'` event whose own title/summary/program also names an exercise/deployment/named
   operation (`_EXERCISE_VOCAB_RE`: `תרגיל`, `exercise`, `deployment`, `Operation <Name>`) is
   reclassified onto the existing `'deployment'` literal before it is ever written. Conservative:
   only ever touches `kind='test'` events carrying that vocabulary; a genuine test with none of it
   (e.g. "ירי ניסיוני של הטיל בוצע בהצלחה") is untouched, and every other kind is untouched.
2. **Report render time, existing rows** (`daily.py`): `_sanitize_event_kind` checked first for the
   same exercise vocabulary (via `_looks_like_exercise`) — a `kind='test'` row already persisted
   with the stale kind (like the live id 174) is relabeled directly to the display string
   `"פעילות מבצעית"` rather than either the misleading `"ניסוי"` or the existing W5 `'other'`
   downgrade. This label is never written back to the DB, so it isn't limited to the 9-literal CHECK
   constraint: both `docx_builder._EVENT_KIND_LABELS_HE` and this module's own
   `_EVENT_KIND_LABELS_HE_FALLBACK` render an unrecognised kind via `.get(kind, kind or "—")`, so the
   raw string renders correctly with no label-map edit needed (`docx_builder.py` is out of this
   package's scope). Pre-existing W5 behaviour (`'test'` with no exercise vocab and no test vocab
   either → `'other'`; `'test'` with only test vocab → stays `'test'`) is unchanged.

#### Tests

`tests/unit/test_round6_forecast.py` — 42 new tests, all pure logic (no DB/LLM/network; DB
interaction tested only via fake `conn`/`cursor` doubles matching this codebase's existing
convention in `test_tenders_forecast.py`). Covers: `normalize_hebrew_punctuation` (5),
`PlatformSpec` load-time normalization (2), `_forecast_stable_key` (4), `find_duplicate_forecast_groups`
(4), `dedupe_existing_forecasts` dry-run/apply (3), `report_section` topic-key normalization +
window-proximity clustering (6), `daily._dedup_events` item/amount/kind pass (6),
`analyze._reclassify_exercise_kind` + `persist_analysis` wiring (6), `daily._sanitize_event_kind`
exercise relabeling (6).

Full run (`PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest
tests/unit/test_round6_forecast.py tests/unit/test_report_daily.py tests/unit/test_tenders_forecast.py
tests/unit/test_events_dedup.py tests/unit/test_analyze_key_facts_entities.py -q -p no:cacheprovider`):
**164 passed**. `ruff check`/`ruff format --check` clean on every touched file.
