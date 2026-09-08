# Product Dossier — first live-run fix pass (PD-fix)

Date: 2026-09-08
Scope: the six fixes requested against the first live dossier (`product_key
elbit-systems-spectro-xr`, `product_dossiers.id=1`, `reports.id=182`, job 192) — corpus
false-positive matching, web-source citation hygiene, deal amount/date/country parsing, report
section order, per-topic progress, and a per-topic time cap. Built on top of PD-backend
(`docs/qa/content_review/PD-backend.md`) without re-running the live dossier itself (left for the
lead, per the task brief).

## 1. Corpus false positives — whole-word alias matching (`agent/eoa/dossier/corpus.py`)

The SQL-side `ILIKE %term%` candidate query is kept (cheap, recall-favoring), but every candidate
row is now re-checked in Python by `_matches_product_precisely` before being kept:

- The product name, or any alias **>= 8 characters**, must match as a whole word (`\b`-delimited,
  case-insensitive) — `"Spectro"` no longer substring-matches `"spectroscopy"`.
- An alias **shorter than 8 characters** only counts together with the vendor name also appearing
  (both whole-word) — never the short alias alone.
- A general-reference/encyclopedia domain (wikipedia.org, wiktionary.org, britannica.com, …)
  additionally requires the **full product name** itself, not just an alias, to appear.

Applied uniformly to all five collectors (`collect_items`/`collect_events`/`collect_patents`/
`collect_tenders`/`collect_forecasts`, now accepting `product_name`/`vendor`/`aliases`); the
domain-drop rule is item-specific (only `items` carries a `domain` column). A regression test
(`test_collect_items_filters_out_wikipedia_spectroscopy_false_positive`) reproduces the exact live
false positive ("Infrared spectroscopy - Wikipedia") and asserts it is dropped while the genuine
item is kept.

## 2. Web-source citation hygiene (`agent/eoa/dossier/plan.py`)

`run_plan`'s web-source handling was rewritten:

- **Dedupe by normalized URL** (`normalize_url`: lower-cased host, `www.` stripped, scheme forced
  to `https`, trailing slash/query/fragment dropped) — a page read by two different topics (or
  under two URL spellings) collapses to **one** registry row; every topic's finding still
  references that row's existing `n`.
- **Title capture**: prefers `read_sources`' own title, falls back to the matching
  `read_summaries` entry's title, and as a last resort to the page's hostname — never a bare
  `None`.
- **Kind/reliability classification** (`classify_web_source`): `vendor_official`/`primary` when
  the domain itself contains the vendor's slugified name, `reference`/`low` for a known
  encyclopedia domain, `forum`/`low` for a forum-shaped domain, `trade_press`/`secondary` for a
  small curated list of defense/business trade-press domains, else `press`/`secondary`.
- **Junk-page drop**: a page whose own read summary mentions neither the product name nor an
  alias is dropped outright (reproduces the live French WeTransfer forum thread, source [11]).
- **`accessed_at`** is now stamped (UTC ISO timestamp) on every web row.

Because filtering/deduping happens **before** `corpus.next_n` ever hands out a number, no citation
number is ever reserved for a row that gets dropped or merged — the "renumber after dropping,
never leave a dangling cite" concern from the task brief cannot arise by construction; there is
nothing to remap.

The registry's own internal `kind` field (item/event/patent/tender/forecast/web/previous_dossier —
`eoa.dossier.extract`'s grounding-text dispatch key, locked in by
`test_product_dossier_corpus.py`'s ordering assertions) is **not** repurposed for this
classification (that would have broken the record-type dispatch used throughout the pipeline).
Instead, a web row now additionally carries `source_kind`/`reliability`, and the mapping onto the
`DossierSource` shape (`{n, url, title, kind, reliability, accessed_at}`) the frontend actually
expects happens at the API boundary: `eoa.api.services._dossier_source_view` returns
`source_kind` when present, falling back to the internal `kind` for a DB-kind row (same "item"/
"patent"/… label as before this fix).

## 3. Deal amount/date/country parsing (`agent/eoa/dossier/extract.py`,
`agent/eoa/llm/schemas/product_dossier.py`)

`DealRow` gained three fields, all **deterministically derived after extraction, never
model-authored**:

- `amount_value: float | None` — parsed from `amount` by `parse_amount_he` (Hebrew *and* English
  scale words — אלף/מיליון/מיליארד, thousand/million/billion — and currency words/symbols;
  `"כ-80 מיליון דולר"` → `(80_000_000.0, "USD")`). `currency` is populated from the same parse
  when the model didn't already set it. The published figure (`amount`) is never replaced, only
  annotated.
- `date_kind: "deal" | "published"` — when a deal carries no date of its own, `_ground_deal_row`
  backfills `date` from the first cited source's own `published_at` and marks `date_kind:
  "published"` so it is never indistinguishable from an actual deal-closing date.
- `region_he: str` — `split_country_region` reclassifies a `country` value that reads like a
  region ("Asia-Pacific country", "מדינה באסיה-פסיפיק", Gulf/Middle East/Europe/Africa/…) rather
  than a specific country: `country` is left empty and the region text moves to `region_he`. The
  extraction prompt (`product_dossier_extract.md`) was updated to tell the model the same rule
  up front, and the post-check backstops it either way.

The renderer (`agent/eoa/dossier/report.py`'s `_deals_table`) reflects all three without adding
columns (still 6: תאריך/לקוח/מדינה/סוג/היקף/מקור) — the date cell appends "(תאריך פרסום)" when
backfilled, the amount cell appends the parsed numeric value in parentheses, and the country cell
falls back to `region_he` when `country` is empty.

**Scope note**: the shipped frontend (`DossierDealRow.amount`/`web/src/pages/
DossierDetailPage.tsx`'s `dealColumns`) already reads the existing `amount`/`country` field names;
this round intentionally did **not** rename `amount`→`amount_text` (as one reading of the task
brief's wording could suggest) to avoid silently breaking that already-shipped contract, since the
UI's own deal-table columns are outside this round's file ownership (`web/src/pages/
DossierDetailPage.tsx` + `web/src/components/dossiers/*` are scoped to the progress banner only —
see item 5's note below). `amount` continues to serve as the verbatim "as published" string field.

## 4. Report section order (`agent/eoa/dossier/report.py`)

The previous renderer put every prose section (identity/maturity/regulatory/risks/bd/what-changed)
in `docx_builder`'s `draft.sections` slot, which always renders as one block **before** every
table (a `tables` slot) — the two could not be interleaved to match the plan's exact order without
touching `docx_builder.py` (shared, not owned by this round).

Fix: `draft.sections` is now always empty (only `exec_summary`/"תקציר מנהלים" still uses that
slot), and every other section — prose included — moved into the `tables` list as ordered entries
(`_ordered_report_entries`). A `tables`-list entry with no `headers`/`rows`, only `body_he`,
already renders as plain prose in all three of `docx_builder`'s renderers (docx/md/html) — this
was true before this fix, just not used for the dossier's own prose sections. Order is now exactly:

תקציר, זיהוי, מפרט, גרסאות, ביצועים, בשלות, עסקאות, מחירים, שותפויות, מתחרים, פטנטים, מכרזים
ותחזיות, רגולציה, פערים, משמעות עסקית, מה השתנה, מקורות

(`test_ordered_report_entries_follow_plan_order_for_empty_dossier`/`..._for_full_dossier` lock
this in.) `"מקורות"` itself is `docx_builder`'s own fixed, always-last "נספח מקורות" appendix
heading (shared across every report kind, unchanged/out of this round's scope) — it renders last
regardless, which is what matters for the ordering requirement.

Every one of the 15 entries is **always present** — an empty section renders its own placeholder
line (`PLACEHOLDER_HE`, "לא נמצא במקורות") instead of being omitted; the 9 table builders
(`_specifications_table`, etc.) no longer return `None` for an empty dossier, they return a
placeholder-body entry. "מה השתנה" gets two distinct honest placeholders instead of the generic
one: *"אין סקירה קודמת להשוואה — זוהי הסקירה הראשונה של מוצר זה"* (no previous dossier at all) vs.
*"לא זוהו שינויים לעומת הסקירה הקודמת"* (a previous dossier exists but nothing changed —
`eoa.dossier.diff.compute_diff`'s own documented distinction between `None` and `[]`).

## 5. Per-topic progress (`agent/eoa/dossier/plan.py`, `agent/eoa/dossier/report.py`,
`agent/eoa/api/services.py`, UI)

`run_plan` now takes an `on_progress` callback, called once up front (every topic `"pending"`) and
again after every topic's status changes (`"running"` → `"done"`/`"failed"`, with elapsed
`seconds` and `sources_found`). `eoa.dossier.report.build_product_dossier` wires this to
`_write_job_progress`, a best-effort raw SQL `UPDATE jobs SET result = result || {"progress":
[...]} WHERE id = :job_id AND state = 'running'` — never touches a job that already finished
(`finish_job`'s own terminal write is untouched), never raises (a progress-write failure must not
break the dossier build). Each topic completion also logs `dossier.topic_done` (topic, seconds,
sources_found), per the brief.

`eoa.api.services._pending_dossier_job` now selects `jobs.result` too and surfaces
`pending_job.progress` (`[]` for a job enqueued before this fix, or before the first snapshot
lands). The detail page's pending banner (already polling every 10s, unchanged) now renders a
per-topic list — a status icon (pending/spinner/check/x) + localized status label + elapsed
seconds + source count once known.

**Scope note (frontend)**: this round's file ownership for the UI reads "`web/src/pages/
DossierDetailPage.tsx` + `web/src/components/dossiers/*` for the progress banner only." Making
the banner actually work end-to-end required three small, additive changes just outside that
literal list — without them the new backend field would be silently dropped:
- `web/src/types/api.ts`: added `DossierProgressTopic` + `DossierPendingJob.progress` (the
  existing `normalizeDossierPendingJob` in `web/src/api/real.ts` explicitly whitelisted only
  `{job_id, state}` and would have discarded `progress` even with the backend field present).
- `web/src/api/real.ts`: `normalizeDossierPendingJob` now maps `progress` (defensive, per-field —
  same style as every other `normalizeDossier*` helper in that file).
- `web/src/i18n/dictionaries/{en,he}.ts`: four short strings (`dossiers.progress.status.*`,
  `.seconds`, `.sourcesFound`) — the app is fully bilingual everywhere else; hardcoding English/
  Hebrew status text in the new component would have been a real regression against that
  convention.
- `web/src/mocks/data/dossiers.ts`: the mock's own `MockDossierProduct.pending_job` type needed
  the same `progress` field to keep compiling against the now-stricter `DossierPendingJob` type
  (a representative `MOCK_DOSSIER_PROGRESS` fixture was added so the mock UI demonstrates the
  banner too).

New component: `web/src/components/dossiers/DossierProgressBanner.tsx` (`DossierProgressList`).
Topic labels reuse the existing `dossiers.sections.*` i18n keys for the 8 topics that already have
a top-level nav section (specifications/versions/performance/maturity/deals/pricing/partnerships/
competitors); `"regulatory"` (a `plan.py` topic with no dedicated top-level section — it renders
inline under "deals") and any future/unknown topic key fall back to the backend's own Hebrew
`title_he` rather than a raw i18n key.

## 6. Per-topic time cap (`agent/eoa/search/deep_search.py`, `agent/eoa/config.py`,
`config/config.yaml`, `agent/eoa/dossier/plan.py`)

`investigate()` gained an optional `deadline_s: float | None = None` parameter (explicitly
permitted by the task brief) — when given, it is combined with (never *extends*) the
multiplier-scaled per-investigation timeout via `min()`; `None` (the default) preserves every
other caller's exact prior behavior unchanged. New config `dossier.topic_time_cap_s` (default
`600`, `DossierCfg` + `config/config.yaml`). `run_plan` forwards it as `investigate()`'s
`deadline_s` on every topic call — a topic exceeding the cap stops with whatever it already found
instead of running the full budget.

## Tests

New/updated, `tests/unit/test_product_dossier_*.py` — **107 tests, 0 failed**
(`pytest tests/unit/test_product_dossier_*.py -q`):

- `test_product_dossier_corpus.py`: 15 (was 10) — `_word_present`/`_matches_product_precisely`/
  `_is_general_reference_domain` unit tests + the live Wikipedia-false-positive regression.
- `test_product_dossier_extract.py`: 17 (was 10) — `parse_amount_he` (Hebrew + English scale/
  currency, no-digits), `split_country_region`, and full deal-grounding round trips (amount
  parsing + region split + date backfill, and the "deal already has its own date" non-backfill
  case).
- `test_product_dossier_plan.py`: **13, new file** — `classify_web_source`/`normalize_url` unit
  tests, dedupe-across-topics, irrelevant-page drop, title/kind/reliability capture, progress
  callback (pending → done, failed-topic marking, callback-failure swallowed), and
  `deadline_s` forwarding.
- `test_product_dossier_report.py`: 17 (was 12) — placeholder-never-`None` contract, full section
  order (empty + populated dossier), "מה השתנה" first-run vs. no-change distinction, deal cell
  renderers (amount/date/country-region).
- `test_product_dossier_schema.py`: 12 (was 10) — `DealRow`'s new fields' defaults/validation.
- `test_product_dossier_services.py`: 14 (was 11) — `_dossier_source_view` mapping (both branches)
  + `pending_job.progress` surfacing.
- `test_product_dossier_api.py`, `test_product_dossier_diff.py`: unchanged, still pass (9, 10).

Frontend, `web/src` vitest — **480 tests, 60 files, 0 failed** (`npx vitest run`), including:
- `web/src/api/real.test.ts`: 9 (was 8) — `pending_job.progress` normalization (well-formed +
  malformed-status fallback).
- `web/src/pages/DossierDetailPage.test.tsx`: 8 (was 7) — the pending banner's per-topic list
  (status/label/elapsed, "regulatory"'s title_he fallback).

`npm run build` (`tsc -b && vite build`): **clean** (pre-existing large-chunk warning only, no
errors).

Regression sweep: `pytest tests/unit/test_product_dossier_*.py tests/unit/test_docx_builder.py
tests/unit/test_api_smoke.py tests/unit/test_app_middleware.py tests/unit/test_deep_search*.py -q`
— **404 passed, 0 failed** (5m03s). `ruff check agent/ tests/unit/test_product_dossier_*.py` —
clean.

## Left open

- The live dossier itself (`product_dossiers.id=1`) was **not** re-run this round, per the task
  brief's own instruction ("Do NOT rerun the full dossier (the lead does)") — every fix above is
  verified against unit fixtures reproducing the exact live findings (the Wikipedia/spectroscopy
  false positive, the WeTransfer forum thread, the "כ-80 מיליון דולר"/"Asia-Pacific country" deal
  shape), not against a fresh live run.
- The deal table's own UI columns (`web/src/pages/DossierDetailPage.tsx`'s `dealColumns`) were
  **not** touched — out of this round's file ownership (scoped to the progress banner only); the
  renderer-side (docx/md/html) enrichment in item 3 is complete, but a UI lane still owns
  surfacing `amount_value`/`date_kind`/`region_he` in the on-screen deal table if desired (the
  data is already flowing through `ProductDossierOut`/`DossierDealRow` once that type is
  extended — not done here, since it wasn't needed for anything already shipped to break).
- MCP-sourced web reads (SAM.gov, Janes, etc. — still unconfigured in this environment per
  PD-backend.md) would flow through the same `classify_web_source`/dedupe/relevance-drop path
  unchanged once enabled; no special-casing was added for them.
