# TEST-ISO: hermetic isolation for the product-dossier unit tests (2026-09-09)

## Problem

`tests/unit/test_product_dossier_plan.py` made live network calls. A run took **86 minutes** and 4
tests failed while DuckDuckGo was blocked:

- `test_run_plan_dedupes_same_url_across_topics`
- `test_run_plan_reuses_item_derived_registry_row_for_same_url`
- `test_run_plan_classifies_and_titles_web_rows`
- `test_run_plan_drops_page_whose_summary_only_mentions_product_via_negation`

The datasheet hunt (commit `debe7c0`) and the vendor must-read / site-restricted search (commit
`d785aa2`) both added new, unconditional network-touching stages to `eoa.dossier.plan.run_plan`.
Unit tests must be hermetic and fast; they were not.

## Where the network can be reached

Four `agent/eoa/dossier/*.py` modules together can reach the live network. Every path funnels
through one of two low-level primitives: `httpx.Client.get`/`.post` (used directly or via `import
httpx` inside the function) and `ddgs.DDGS.text` (DuckDuckGo).

| Module | Entry point | Network primitive | Called from |
|---|---|---|---|
| `eoa.dossier.plan` | `investigate()` (`eoa.search.deep_search`) | LLM + `ddgs.DDGS.text` + `httpx.Client` (transitively, inside `investigate`) | every topic in `run_plan`'s topic loop |
| `eoa.dossier.plan` | `run_must_read()` -> `fetch_remote()` (`eoa.fetch.remote`) | `httpx.Client(...).get()` | `run_plan`, once per must-read vendor URL, before the topic loop |
| `eoa.dossier.plan` | `hunt_datasheets()` (`eoa.dossier.datasheet`) | see below | `run_plan`, once, before the topic loop |
| `eoa.dossier.datasheet` | `search()` (`eoa.search.provider`) | `ddgs.DDGS.text` (default `ddgs` backend) or `httpx` (`searxng` backend) | `gather_candidates()` |
| `eoa.dossier.datasheet` | `fetch_pdf()` (`eoa.search.pdf_reader`) | `httpx.Client(...).get()` (`_validated_get`) | `hunt_datasheets()`, downloading a candidate PDF |
| `eoa.dossier.datasheet` | `fetch_remote()` (imported lazily inside `hunt_datasheets`) | `httpx.Client(...).get()` | `hunt_datasheets()`'s non-PDF product-page fallback |
| `eoa.dossier.corpus` | `collect_patents_ops()` -> `search_records_for_applicant()` (`eoa.patents.scan`) | `httpx.Client(...).get()` (EPO OPS / USPTO ODP `_common` helper), falling back to `eoa.search.provider.search` (`ddgs.DDGS.text`) for the keyless Google Patents query | `build_corpus()` / any direct caller |
| `eoa.dossier.programs` | none | — pure text functions (`identify_platforms`, `parse_programme_deals`, `detect_customer_countries`, ...); no network, no DB | — |

`eoa.dossier.corpus`'s other collectors (`collect_items`, `collect_events`, `collect_patents`,
`collect_tenders`, `collect_forecasts`, `collect_entities_and_edges`, `previous_dossier`) are DB-only
(`eoa.db.connection` / `_fetchall`/`_fetchone`) -- out of scope for this pass (DB access in unit
tests is acceptable; only network is not).

## What was already isolated (before this pass)

Per-test/per-file monkeypatching was already largely in place:

- `test_product_dossier_plan.py`: every test monkeypatches `dossier_plan.investigate` directly; a
  file-level autouse fixture (`_no_live_datasheet_hunt`) stubs `dossier_plan.hunt_datasheets` to
  `[]` for every test that doesn't explicitly exercise the datasheet-hunt wiring; the dedicated
  must-read tests monkeypatch `dossier_plan.fetch_remote` explicitly.
- `test_dossier_datasheet.py`: every test monkeypatches `datasheet.search` and/or
  `datasheet.fetch_pdf` / the lazily-imported `eoa.fetch.remote.fetch_remote`.
- `test_product_dossier_corpus.py`: a file-level autouse fixture (`_no_live_patents_ops`) stubs
  `eoa.patents.scan.search_records_for_applicant` to `[]` for every test; the dedicated
  `collect_patents_ops` tests override it per-test.
- `test_dossier_programs.py`: no network-touching import at all -- pure functions only.

Re-running the four previously-failing tests individually against the current tree (before adding
anything new) already passed in under 1 second, and the full `test_product_dossier_plan.py` file
passed in ~8-9 seconds -- the specific 86-minute/4-failure incident was not reproducible against the
current code, most likely because `_no_live_datasheet_hunt` (added in the same commit,
`debe7c0`, that introduced the datasheet hunt) already closed that particular hole. What was
missing was a **safety net**: nothing stopped a *future* code path (a new call site, a test that
forgets to monkeypatch one specific entry point) from reaching the network unmocked and hanging or
failing exactly the same way again.

## Fix

`tests/conftest.py`: one additive autouse fixture, `_dossier_network_guard`, scoped by test-file
basename to exactly the four owned dossier files (`test_product_dossier_plan.py`,
`test_dossier_datasheet.py`, `test_dossier_programs.py`, `test_product_dossier_corpus.py`). For
every test in those files it monkeypatches the two low-level primitives every entry point in the
table above funnels through -- `httpx.Client.get`, `httpx.Client.post`, `ddgs.DDGS.text` -- to raise
`RuntimeError` immediately, naming the primitive and the failing test's node id. Every other test
file's own `httpx`/`ddgs` use is untouched (verified: `test_pdf_reader.py`, `test_remote_fetch.py`,
`test_html_fetch.py` all still pass unaffected).

This is defense-in-depth, not a replacement for the existing per-test mocks: every test in scope
already mocks the specific higher-level entry point it exercises (`search`, `fetch_pdf`,
`fetch_remote`, `investigate`, `search_records_for_applicant`), so the guard should never actually
fire in the normal run. If it does fire, the failure is immediate and loud -- pointing at exactly
which primitive and which test -- instead of a real, possibly multi-hour-hanging-when-blocked HTTP
request.

A regression test for the guard fixture itself (proving it is wired up and active) was added to
`test_product_dossier_plan.py`:

- `test_network_guard_blocks_httpx_client_get`
- `test_network_guard_blocks_httpx_client_post`
- `test_network_guard_blocks_ddgs_text`

The four originally-failing tests needed no code changes -- they already passed deterministically
against the current tree; they now also run under the network guard with no change in behavior.

## Verification

Run with `PYTHONUTF8=1 PYTHONPATH=agent .venv/Scripts/python.exe -m pytest <file> -q`:

| File | Alone | Notes |
|---|---|---|
| `tests/unit/test_product_dossier_plan.py` | 52 passed in 9.18s | 49 original + 3 new guard-regression tests |
| `tests/unit/test_dossier_datasheet.py` | 15 passed in 1.92s | |
| `tests/unit/test_dossier_programs.py` | 23 passed in 2.89s | |
| `tests/unit/test_product_dossier_corpus.py` | 33 passed in 4.64s | |
| All four together | 123 passed in 18.91s | well under the 60s target |

`ruff check` on all touched files: clean.

No production code was changed. `tests/conftest.py` gained exactly one additive autouse fixture
(`_dossier_network_guard`); `tests/unit/test_product_dossier_plan.py` gained three guard-regression
tests. The other three owned test files were already correctly isolated and needed no changes.
