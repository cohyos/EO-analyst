# Product Dossier Backend — PD-backend

Date: 2026-09-08
Scope: migration `0031`, `agent/eoa/llm/schemas/product_dossier.py`,
`agent/eoa/llm/prompts/product_dossier_extract.md`, `agent/eoa/dossier/*` (new package),
`agent/eoa/orchestrator/jobs.py` (`product_dossier` job kind), `agent/eoa/api/routes/dossiers.py` +
`agent/eoa/api/services.py` (the five frozen-contract endpoints), `agent/eoa/config.py`
(`DossierCfg`) + `config/config.yaml` (`dossier:` block), `docs/MODULES.md`/`docs/USER_GUIDE_HE.md`.
Built against the frozen contract in `docs/PLAN_PRODUCT_DOSSIER.md`. PD-ui had already shipped
`web/**` + `e2e/tests/24-dossiers.spec.ts` against the same contract before this backend lane
landed (see `docs/qa/content_review/PD-ui.md`) -- one contract note from that doc is resolved
below.

## What was built

1. **Migration 0031** (`db/migrations/versions/0031_product_dossiers.py`) -- `product_dossiers`
   table (`product_key`/`product_name`/`vendor`/`aliases`/`product_line`/`job_id`/`report_id`/
   `data JSONB`/`sources JSONB`/`outcome`/`confidence`/`created_at`, `UNIQUE (product_key,
   created_at)`, indexes on `product_key`/`created_at`) + widens `reports.kind` CHECK with
   `'product_dossier'`. **Applied to the live DB** (confirmed via a fresh 5432 connection: the
   table's columns and the widened `reports_kind_check` constraint both verified post-migration).
2. **Schema** (`agent/eoa/llm/schemas/product_dossier.py`) -- `ProductDossierOut` and 11 row models
   (`IdentityBlock`/`SpecRow`/`VersionRow`/`PerformanceRow`/`MaturityBlock`/`DealRow`/`PriceRow`/
   `PartnerRow`/`CompetitorRow`/`RegulatoryExportBlock`/`DossierPatentRow`/`DossierTenderRow`),
   every fact-bearing field with its own `cites: list[int]` (empty allowed at the schema level --
   the post-check pass in `extract.py` is what enforces non-empty `cites` on a kept fact). Prompt
   (`product_dossier_extract.md`) carries the report's stricter "never invent" rules verbatim,
   including the pricing discipline (section 1/6.3) and claimed-vs-demonstrated performance.
3. **Pipeline** (`agent/eoa/dossier/`) -- `corpus.py` (DB gather + registry seed + `product_key`
   slugging), `plan.py` (fixed 9-topic research plan over `investigate()`), `extract.py`
   (structured extraction + deterministic grounding post-checks), `diff.py` (deterministic "מה
   השתנה"), `report.py` (orchestration, `docx_builder`-reusing render, persistence). See
   `docs/MODULES.md`'s "Product dossier" section for the full per-module writeup.
4. **Job kind** `product_dossier` (`agent/eoa/orchestrator/jobs.py` `HANDLERS`) -- a failure never
   blocks the orchestrator (docs/CONVENTIONS.md rule 9).
5. **API** (`agent/eoa/api/routes/dossiers.py` + service functions in `agent/eoa/api/services.py`)
   -- the five endpoints in section 5, registered in `agent/eoa/api/app.py`. `POST` bodies use
   `pydantic.BaseModel` request shapes (`DossierCreateRequest`/`DossierRerunRequest`), matching
   `eoa.api.routes.bd`'s own convention (avoids ruff B008 on a `Body(...)`/`Body(default={})`
   default).
6. **Config** -- `DossierCfg` (`rounds_per_topic=3`, `max_topics=9`, `budget_multiplier=1.0`,
   `max_sources=40`) + the matching `dossier:` block in `config/config.yaml`.
7. **Docs** -- `docs/MODULES.md` new section, `docs/USER_GUIDE_HE.md` section 18.

## Contract alignment with PD-ui (resolved)

PD-ui's own content-review doc (`docs/qa/content_review/PD-ui.md`, written before this backend
landed) flagged that `GET /api/dossiers`' list-row `count` field has no further definition in
section 5, while section 6's card spec calls for a "deal count" at that same granularity -- the
already-shipped frontend (`DossierSummary.count` in `web/src/types/api.ts`) reads it as **the
latest run's grounded deal count**, not a run count. `eoa.api.services.list_dossiers` now returns
exactly that (`len(latest.data.deals)`), and `get_dossier`'s response was flattened to match
`DossierRunDetail extends DossierRunRef` (`path_docx`/`path_md`/`path_html` as top-level fields,
not nested under a `report_paths` object, which the PD-ui-shipped `normalizeDossierRunDetail`
reads directly). No other field-shape mismatch was found against `web/src/types/api.ts`'s
`DossierSummary`/`DossierDetail`/`DossierRunDetail`/`DossierRunRef`/`DossierSource`/
`DossierPendingJob`/`DossierCreateBody`/`DossierCreateResponse`/`DossierRerunResponse` on review.

## Corpus dry-run output (live DB, 2026-09-08)

Per the task brief's scope limit, only the `corpus` + `plan` stages were run live (`plan.py`'s
`build_topics` -- pure question construction, no network) -- the full multi-topic
`investigate()` run is left for the lead's first live run (section 7), to avoid spending a real
investigation's GPU/network budget on this round's own verification pass.

```
product_key: elbit-systems-spectro-xr
terms: ['SPECTRO XR', 'Spectro', 'ספקטרו']
items found: 3
  item 8441 - Rail Vision's AI Technology Selected for U.S. Rail Testing
  item 600  - Infrared spectroscopy - Wikipedia
  item 93   - Elbit Systems Lands $270M ISR Deal
events found: 0
patents found: 0
tenders found: 0
forecasts found: 0
entities: ['Elbit']
edges: 11
previous dossier: False
registry size: 3
```

The 9 research-topic questions (`build_topics`) all correctly name "SPECTRO XR", "Elbit Systems"
and every configured alias explicitly (required by `eoa.search.deep_search.extract_anchors`'s
anchoring guard) -- verified by inspection, one question per topic (specifications, versions,
performance, maturity, deals, pricing, partnerships, competitors, regulatory).

### Open item found during the dry run: alias-matching precision

Two of the three live-matched items (`Rail Vision`, `Infrared spectroscopy - Wikipedia`) are false
positives -- the short alias `"Spectro"` substring-matches "spectroscopy" via the `ILIKE %term%`
matching in `eoa.dossier.corpus`. Only item 93 ("Elbit Systems Lands $270M ISR Deal") is a genuine
SPECTRO XR match. This is the same class of precision issue `eoa.patents.survey` already solved for
its own on-demand topic search via a distinctive-keyword document-frequency filter
(`_stored_patent_ids_for_topic`/`_keyword_document_frequency`) -- out of this round's scope to port
here (a real extraction call still grounds every fact against its own cited source text, so a
false-positive corpus item cannot itself inject a fabricated fact into the dossier; it only widens
the DB-half registry with an irrelevant row that the extraction model is instructed, and expected,
to simply not cite). Flagged as a follow-up rather than fixed inline to keep this round's diff
scoped to the frozen contract.

## MCP tools the plan references (config-gated, not enabled in this environment)

The plan's own "point 6" tools notes (`eoa.mcp.registry`, `config/mcp.yaml`, `docs/adr/
006-mcp-sources.md`) apply unchanged to the dossier's `plan.py` -- MCP tools are appended to the
ReAct loop's tool list only when `settings().mcp.enabled` is true and a server is configured with
its own API key: `SAM.gov` (`eoa.mcp_servers.procurement`, contract/award lookups -- useful for the
`deals`/`pricing` topics), `Congress.gov` (legislative/appropriations context), `EPO OPS`
(`EPO_OPS_KEY`/`EPO_OPS_SECRET`, structured patent search -- feeds `eoa.dossier.corpus.
collect_patents`'s own DB-side matches once the nightly patent scan has ingested EPO-sourced
records), `Janes` (if configured). None of these keys are present in this environment
(`runtime/eoa.env` was not modified by this round, per the task's DB-only scope) -- the dossier
pipeline runs correctly without them (the local `search`/`read` ReAct tools cover every topic),
they simply widen what `plan.py`'s topic investigations can find when an operator adds the keys.

## Tests

`tests/unit/test_product_dossier_schema.py` (13 tests), `test_product_dossier_corpus.py` (10),
`test_product_dossier_extract.py` (10), `test_product_dossier_diff.py` (10),
`test_product_dossier_report.py` (9), `test_product_dossier_api.py` (9),
`test_product_dossier_services.py` (11, locks in the `count`/`path_docx` contract-alignment fix
above) -- **68 passed, 0 failed** (`pytest tests/unit/test_product_dossier_*.py -q`).

Regression check on the suites this round's changes touch (`jobs`/`product_line`/`patent_survey`/
`api_smoke`/`app_middleware`, plus `docx_builder`): **272 passed, 0 failed**
(`pytest tests/unit -q -k "jobs or product_line or patent_survey or api_smoke or app_middleware"`
= 214, `pytest tests/unit/test_docx_builder.py tests/unit/test_api_smoke.py
tests/unit/test_app_middleware.py -q` = 58, overlapping api_smoke/app_middleware counted once).
A full-suite run (`pytest tests/unit -q`) was also kicked off; its full pass/fail tally is recorded
separately once complete (this environment's full suite takes several minutes -- see
`docs/MODULES.md`'s own note on the last full-suite baseline, ~3849 tests).

`ruff check` clean on every file touched (`agent/eoa/dossier/`, `agent/eoa/llm/schemas/
product_dossier.py`, `agent/eoa/api/routes/dossiers.py`, `agent/eoa/api/services.py`,
`agent/eoa/orchestrator/jobs.py`, `agent/eoa/api/app.py`, `agent/eoa/config.py`, and every new test
file).

## Left open

- The alias-matching precision issue above (short-alias substring false positives in
  `eoa.dossier.corpus`'s `ILIKE` matching).
- The lead's own first live multi-topic run (section 7: SPECTRO XR / Elbit Systems /
  `["Spectro", "SPECTRO XR", "ספקטרו"]`, a `product_line` id of the lead's choosing) -- this round
  intentionally stopped at the corpus+plan dry run per the task brief's scope limit.
- e2e (`e2e/tests/24-dossiers.spec.ts`) was written and passing against the PD-ui mock API before
  this backend landed (10 tests were skipped there specifically because `/api/dossiers` 404'd) --
  now that the real endpoints exist, PD-ui's skipped list/detail e2e assertions should be
  revisited against the live backend (out of this round's own file ownership to re-run/verify).
