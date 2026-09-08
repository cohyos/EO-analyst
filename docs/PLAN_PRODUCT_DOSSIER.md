# Product dossier ("סקירת שוק עמוקה למוצר") — design, frozen contract

User request 2026-09-08: a deep market survey per product — specification, maturity, performance,
versions/variants, deals, prices, partnerships and more. Example: Elbit **SPECTRO XR** (EO/IR
payload). This document is the contract both implementation lanes work against.

## 1. What it is

A new report kind `product_dossier`: an on-demand, multi-round investigation of ONE product
(name + vendor + aliases) that combines what the system already holds (items, events, patents,
tenders, forecasts, entities, previous dossiers) with fresh web research through the deep-search
machinery (`eoa.search.deep_search.investigate`, its tools and MCP sources), extracts a
**structured, cited** record, passes the same grounding discipline as every other report
(entity/number/affiliation grounding, claims gate, citation registry) and renders md/html/docx plus
a dedicated UI page. Reruns produce a "מה השתנה" diff against the previous dossier of the same
product.

Every fact cell carries citations; a field the research could not establish is rendered as
"לא נמצא במקורות" — never estimated, never filled from model memory. Prices in particular: only
figures that appear in a contract award, tender, budget line, FMS notice or a quoted official;
each with source, date, quantity context and currency; ranges are allowed; no derived unit prices.

## 2. Data model

Migration `0031_product_dossiers`:

```
product_dossiers
  id BIGSERIAL PK
  product_key TEXT NOT NULL          -- slug: elbit-spectro-xr
  product_name TEXT NOT NULL         -- "SPECTRO XR"
  vendor TEXT                        -- "Elbit Systems"
  aliases TEXT[] NOT NULL DEFAULT '{}'
  product_line TEXT                  -- optional link to config/product_lines.yaml id
  job_id BIGINT                      -- the product_dossier job
  report_id BIGINT REFERENCES reports(id)
  data JSONB NOT NULL                -- ProductDossierOut (below), with citations per field
  sources JSONB NOT NULL             -- [{n, url, title, kind, reliability, accessed_at}]
  outcome TEXT NOT NULL              -- found | partial | not_found
  confidence REAL
  created_at TIMESTAMPTZ DEFAULT now()
  UNIQUE (product_key, created_at)
+ index on product_key
reports.kind CHECK widened with 'product_dossier' (same pattern as 0028).
jobs kind 'product_dossier' (payload {product_key, product_name, vendor, aliases, product_line,
budget_multiplier}).
```

## 3. Structured schema (`agent/eoa/llm/schemas/product_dossier.py`)

Pydantic, every text field Hebrew unless noted; every fact carries `cites: list[int]` (citation
numbers from the dossier's registry) and an optional `as_of` date. A field whose value is unknown
is `null` (rendered "לא נמצא במקורות"), never a guess.

```
ProductDossierOut
  identity: {product_name, vendor, product_family, category_he, first_announced (date|null),
             status_he (in development / in production / fielded / retired), cites}
  summary_he: <= 6 sentences, what the product is and where it stands (cites per sentence via Sentence objects like the reports)
  specifications: list[SpecRow]  # {parameter_he, value (string as published), unit, variant, source_kind (datasheet/brochure/article/official), cites}
  variants_and_versions: list[VersionRow]  # {name, year|null, changes_he, platforms (list), cites}
  performance: list[PerformanceRow]  # {metric_he, claimed_value, tested_or_operational_value|null, conditions_he, cites}  -- "claimed" vs "demonstrated" kept apart
  maturity: {trl (int|null), operational_users (list[str]), platforms_integrated (list[str]), first_fielding (date|null), assessment_he, cites}
  deals: list[DealRow]  # {date, customer, country, kind (contract_award/FMS/framework/option/export_license), amount, currency, quantity|null, platform|null, cites, confidence}
  pricing: list[PriceRow]  # {figure, currency, basis_he (per unit / lot of N / programme total), date, source_kind, cites}  -- ONLY grounded figures
  partnerships: list[PartnerRow]  # {partner, role_he (integrator/subcontractor/co-development/reseller), since|null, cites}
  competitors: list[CompetitorRow]  # {product, vendor, comparison_he, cites}  -- only competitors named in sources or the watchlist
  regulatory_export: {export_regime_he, restrictions_he, cites}   # ITAR/EAR/Israeli DECA where sourced
  patents: list[{pub_number, title, assignee, relevance_he, cites}]   # from the patents table + OPS
  tenders_and_forecasts: list[{tender_id|null, title, status, relevance_he, cites}]
  risks_and_gaps_he: list[Sentence]     # what is NOT known, contradictions between sources
  what_changed_he: list[Sentence] | null  # vs. previous dossier of the same product_key
  bd_implications_he: list[Sentence]     # for the user's BD role, hedged, quantity first
```

## 4. Pipeline (`agent/eoa/dossier/`)

1. `corpus.py` — gather everything the DB already has for the aliases: items (title/summary/so_what/
   text), events, patents, tenders/forecasts, entities + graph edges, previous dossier. Build the
   citation registry seed from these (existing `qa_citations` conventions).
2. `plan.py` — fixed research plan, one `investigate()` call per topic with a targeted question and
   `context_he` carrying the corpus summary; topics: spec sheet & datasheet (prefer vendor pages
   and PDFs), variants/versions timeline, performance (claimed vs demonstrated), maturity &
   fielding (users, platforms, exercises), deals & customers (contracts, FMS, exports), pricing
   (contract values with quantities, budget lines), partnerships/integrations, competitors,
   export/regulatory. `max_rounds` per topic from config (`dossier.rounds_per_topic`, default 3),
   total budget capped (`dossier.max_topics`, `dossier.budget_multiplier`). Topics run
   sequentially on the cloud chain (EOA_PIPELINE=1), each result appended to the registry.
3. `extract.py` — one structured-extraction call (cloud chain, JSON schema = ProductDossierOut)
   over the corpus + all topic findings, with the same "never invent, cite every fact" rules as
   reports; then deterministic checks: every cite exists in the registry; every number in a value
   appears in the cited source text (reuse `eoa.pipeline.analysis_grounding` helpers and
   `ask_grounding` number matching); competitor/partner names must appear in sources or the
   watchlist; affiliation claims via `config/company_facts.yaml`; claims gate over prose; drop
   the offending field to null and log `dossier.field_dropped`.
4. `diff.py` — what changed vs the previous dossier (new deals, spec changes, new variants, price
   points), rendered as `what_changed_he`.
5. `report.py` — persist `product_dossiers` row + `reports` row (kind product_dossier) and
   render md/html/docx through `docx_builder` (tables <= 6 columns, bidi rules, captions), file
   name `dossier_<product_key>_<date>.md`.
6. `jobs.py` handler `run_product_dossier(job)`; failures never block the orchestrator.

## 5. API (frozen)

```
GET  /api/dossiers                      -> [{product_key, product_name, vendor, latest: {id, created_at, outcome, confidence, report_id}, count}]
POST /api/dossiers                      body {product_name, vendor?, aliases?: [], product_line?, budget_multiplier?} -> {job_id, product_key}
GET  /api/dossiers/{product_key}        -> {product_key, product_name, vendor, aliases, dossiers: [{id, created_at, outcome, confidence, report_id}], latest: ProductDossierOut + sources, pending_job: {job_id, state} | null}
GET  /api/dossiers/{product_key}/{id}   -> one dossier (data + sources + report paths)
POST /api/dossiers/{product_key}/rerun  -> {job_id}   (budget_multiplier optional)
```
Job progress is visible through the existing jobs/status endpoints and `/ws/status`.

## 6. UI (frozen)

- Nav entry "סקירות מוצר" (route `/dossiers`), list page: cards per product (name, vendor,
  outcome badge, last run, deal count, "הרץ שוב"), "סקירה חדשה" form (product, vendor, aliases,
  product line select, budget).
- Detail page `/dossiers/:key`: header (identity, status, TRL, confidence), tabs or anchored
  sections in this order: תקציר | מפרט | גרסאות | ביצועים | בשלות | עסקאות | מחירים | שותפויות |
  מתחרים | פטנטים | מכרזים ותחזיות | פערים | משמעות עסקית | מה השתנה | מקורות. Every table <= 6
  columns with sticky header and citation chips using the existing `CitationText`/source preview
  card; "לא נמצא במקורות" placeholders; pending-run banner with progress from the job; run
  history with a diff link between two runs.
- Reuse `AnswerText`-style rendering for prose, `bidiText` for mixed runs, typography from the
  report viewer.

## 7. Quality gates

- Unit tests for schema, corpus, extraction checks (fixtures built from real SPECTRO XR items in
  the DB), diff, renderer; API tests through TestClient; vitest for the pages; e2e spec
  `24-dossiers.spec.ts` (list, create form validation, detail sections, empty states).
- D7-style QA checks: no uncited fact cell, no number outside its source, tables <= 6 columns, no
  intensifier without quantity, no invented competitors.
- First live run: `SPECTRO XR` / `Elbit Systems` / aliases ["Spectro", "SPECTRO XR", "ספקטרו"],
  product_line `targeting_pods`... (the lead picks the line); reviewed by the lead before release.

## 8. Ownership

- PD-backend: migration, schema, `agent/eoa/dossier/*`, jobs handler, API routes + services,
  report rendering hooks in docx_builder (additive), tests, docs/MODULES.md row.
- PD-ui: `web/src/pages/DossiersPage.tsx`, `DossierDetailPage.tsx`, `web/src/components/dossiers/*`,
  api client + types + mocks, nav + routes, i18n, vitest, e2e spec.
- Lead: config keys (`dossier.*` in config.yaml), first live run, review, commit.
