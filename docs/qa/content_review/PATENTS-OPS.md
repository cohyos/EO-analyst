# PATENTS-OPS — EPO OPS Goes Live (CQL Fix, Biblio Fetch, Scan Switchover, Dossier Query)

Date: 2026-09-08. Trigger: `EPO_OPS_KEY`/`EPO_OPS_SECRET` are now configured and verified live
(`POST https://ops.epo.org/3.2/auth/accesstoken` returns 200; the stack was restarted after) — this
round makes the OPS client actually usable end to end (it previously only had unit tests against
guessed shapes, since no credentials existed on this machine), switches `eoa.patents.scan` to use
it as the primary structured source, and adds a live per-product OPS query to the dossier pipeline.
User finding that motivated task 3: the SPECTRO XR dossier (`product_dossiers` rows 1-2,
`product_key = elbit-systems-spectro-xr`) had `data->'patents'` completely empty.

## 1. EPO OPS client — live verification, the CQL bug, and biblio

### 1.1 Token acquisition

Verified live: `_epo_token_value()` (`agent/eoa/mcp_servers/patents.py`) successfully exchanges
`EPO_OPS_KEY`/`EPO_OPS_SECRET` for a bearer token via `POST
https://ops.epo.org/3.2/auth/accesstoken` (Basic auth, `grant_type=client_credentials`) and caches
it in-process for its reported `expires_in` (minus a 30s safety margin). No code change was needed
here — the implementation already matched the real API.

### 1.2 The reported bug, isolated

Task brief: "a naive query `pa=\"Elbit Systems\" and ti=electro-optical` returned 'No results'."
Reproduced live and isolated to its precise trigger — **quoting a hyphenated value**:

| Query | Result |
|---|---|
| `pa="Elbit Systems" and ti=electro-optical` | **200, 42 hits** — this specific combination actually works (the hyphenated term here is *not* quoted) |
| `pa=elbit and ta=electro-optical` | 200, 2 hits — hyphenated compound, **unquoted** |
| `pa=elbit and ti="electro-optical"` | **404 "No results found"** — the identical term, **quoted** |
| `pa=elbit and ta=gimbal` | 200, 2 hits |
| `pa=elbit and ti="night vision"` (no hyphen) | 200 — quoting a non-hyphenated phrase works fine |
| `pa=elbit systems and ta=infrared` | 200, 9 hits — **same total as `pa=elbit` alone**: an *unquoted* multi-word value silently drops every word after the first, rather than erroring |
| `pa=elbit and ta=(electro-optical or gimbal or payload)` | 200, 6 hits — parenthesised OR group |
| `pa=elbit and cpc=G02B27` | 200, 97 hits — bare class/subclass/main-group prefix, case-insensitive; `ipc=` behaves the same |

**Net rule** (encoded in `agent/eoa/mcp_servers/patents.py::_cql_term`/`build_epo_query`): quote a
value if and only if it is multi-word **and** contains no hyphen; a hyphenated value is always left
bare, regardless of word count. A 404 from `epo_ops_search` is now reported as
`{"total_result_count": 0, "results": []}`, not an error — OPS's own `SERVER.EntityNotFound`/"No
results found" response is identical for a genuine zero-match and for the quoting bug above, so a
caller cannot (and should not try to) tell them apart from the response alone.

`build_epo_query(*, applicant="", keywords=None, cpc="", field="ta")` is the new, recommended way
to build a query — it applies the quoting rule automatically so no call site has to rediscover it.

### 1.3 Live verification: applicant "Elbit Systems" × 4 keywords

`build_epo_query(applicant="Elbit Systems", keywords=[kw])` for each keyword, live against
`https://ops.epo.org/3.2/rest-services/published-data/search`:

| Keyword | CQL query | Total hits | Sample publication numbers |
|---|---|---|---|
| electro-optical | `pa="Elbit Systems" and ta=electro-optical` | 2 | US2024220012A1, US2015168730A1 |
| payload | `pa="Elbit Systems" and ta=payload` | 4 | WO2025088600A1, IL295833A, US2010290554A1, WO2010079477A2 |
| gimbal | `pa="Elbit Systems" and ta=gimbal` | 0 | — (genuine zero-match: `ta=gimbal*` wildcarded returns 2 — Elbit's own gimbal patents apparently use an inflected form of the word) |
| infrared | `pa="Elbit Systems" and ta=infrared` | 9 | US2020073126A1, US2016080668A1, US2015323656A1, US2015112260A1, US2014139684A1 |

5 sample publication numbers + titles (via `epo_ops_biblio`, see §1.4):

1. **US2024220012A1** — "Optical see through (OST) head mounted display (HMD) system and method
   for precise alignment of virtual objects with outwardly viewed objects" — Elbit Systems Ltd [IL]
2. **US2015168730A1** — "Wearable optical display system for unobstructed viewing" — Elbit Systems
   Ltd [IL] + Everysight Ltd [IL]
3. **WO2025088600A1** — "SAFETY DEVICE AND METHOD FOR WEAPONS" — Elbit Systems C4I & Cyber Ltd [IL]
4. **US2020073126A1** — "Covert target acquisition with coded short-wave infrared glasses" — Elbit
   Systems America LLC [US]
5. **US2016080668A1** — "Systems and methods for detecting light sources" — Elbit Systems America
   LLC [US]

### 1.4 New: `epo_ops_biblio` — per-publication title/abstract/applicants/CPC/IPC/dates/family

The search endpoint alone only returns a bare country/doc-number/kind reference. `epo_ops_biblio
(pub_number)` (new MCP tool) fetches
`https://ops.epo.org/3.2/rest-services/published-data/publication/docdb/{cc}.{num}.{kind}/biblio`
(the **docdb** number form — dot-separated `country.doc-number.kind` — is the one that reliably
resolves; the bare `epodoc` form with a kind letter glued on, e.g. `US20250199318A1`, 404s even
though the docdb form of the identical publication returns 200) and parses it into
`{"pub_number", "kind", "country", "title", "abstract", "applicants", "assignees", "inventors",
"cpc", "ipc", "family_id", "application_number", "priority_date", "filing_date",
"publication_date", "url"}`. Verified live against 5 real Elbit Systems publications (§1.3) —
titles, applicants, CPC codes (e.g. `G02B27/0093`, `A61B90/50`), family IDs (e.g. `83846750`) and
dates all round-trip correctly. `_pub_number_to_docdb`/`_extract_epo_biblio` and their helpers
(`_extract_party_names`, `_extract_cpc_codes`, `_extract_ipc_codes`, `_extract_title`,
`_extract_abstract`) are unit-tested against trimmed real live response fixtures
(`tests/unit/test_mcp_servers_patents.py::BIBLIO_SINGLE_APPLICANT`/
`BIBLIO_TWO_APPLICANTS_APPLICANT_BLOCK`).

### 1.5 Throttling

EPO OPS's `X-Throttling-Control` response header (verified live, e.g. `"busy (images=green:100,
inpadoc=green:45, other=green:1000, retrieval=green:100, search=green:15)"`) is now parsed and
tracked per service (`_parse_throttling_control`/`_record_throttle_state`); `_epo_throttle_wait
(service)` adds a short courtesy delay (amber/red: 2s/8s, black: 60s) before the next call to that
service when its last-seen color was not green. This is a best-effort avoidance of predictably
tripping the real limit, not a substitute for it — the authoritative enforcement is still whatever
HTTP status/quota headers OPS itself returns, and the module never loops or retries a throttled
call on its own account. Wired into both `epo_ops_search` ("search" service) and `epo_ops_biblio`
("retrieval" service) via `_common.py`'s new opt-in `include_headers=True` (additive-only change —
every existing caller/test keeps its exact prior output shape since the default is `False`).

## 2. `patent_scan` — OPS as the primary source

### 2.1 What changed

`agent/eoa/patents/scan.py`:

- **CQL construction fixed.** The per-assignee scan previously built one flat, imprecise free-text
  string (`f"{assignee} electro-optical infrared imaging patent"` — never actually restricted to
  the assignee as an applicant on any provider, "patent" pure noise). It now uses
  `build_epo_query(applicant=assignee, keywords=_ASSIGNEE_SCAN_KEYWORDS)` — the same 4 keywords
  verified live in §1.3 (`electro-optical`, `infrared`, `gimbal`, `payload`) — for EPO OPS, and the
  clean keyword-only free text for USPTO ODP's `q=` (assignee passed via ODP's own structured
  `assignee=` parameter, already correctly AND'd by `build_odp_search_body`).
- **Biblio enrichment.** `_epo_records` now fetches `epo_ops_biblio` for the top
  `_EPO_BIBLIO_ENRICH_CAP = 5` hits per query (bounding EPO OPS's separate "retrieval" fair-use
  quota to a small, fixed number of extra calls per query rather than one per hit), turning a
  bare publication reference into a fully-populated `PatentRecord` (title/abstract/assignees/
  cpc/dates/family_id). A hit beyond the cap, or a biblio fetch/parse failure, still yields the
  same title-less record the pre-biblio version always produced (docs/CONVENTIONS.md rule 9 — one
  bad fetch never aborts the batch).
- **`max_inserted` cap** added to `scan_patents()` for a bounded one-off run (stops issuing further
  topic/assignee queries once the cap is reached; a query already in flight still finishes, so the
  final count can land slightly above the cap, never below it).
- **New `search_records_for_applicant(applicant, keywords, *, limit=20)`** — a provider-aware,
  applicant-scoped public gather (EPO OPS `pa=`/USPTO ODP `assignee=` when configured, else the
  keyless Google Patents fallback), added for `eoa.dossier.corpus` (§3).
- Assignee-safe matching (`_assignee_candidates_in_text` →
  `find_watchlist_company_names_in_text`, CR-patents) and the text-grounding rules
  (`eoa.patents.analyze.ground_generated_text`, CR-patents-2) are **unchanged** — both already
  apply after ingestion regardless of source, and neither file was touched this round.

### 2.2 A regression found and fixed during live verification

The first live run inserted 51 records but **every single one had an empty title/assignees/cpc** —
biblio enrichment appeared to do nothing. Root cause: `_within_window` (the recency filter that
bounds a routine re-scan's traffic) exempts a record with **no** `publication_date` from age
filtering — every EPO record used to have no date at all, so the whole assignee loop was silently
exempt from the window. Once biblio enrichment started setting a real (often years-old)
`publication_date`, the filter began *rejecting exactly the enriched, highest-quality hits* while
the un-enriched, title-less hits for the *same* patents sailed through unfiltered — backwards from
the intent. Fixed by adding `apply_window: bool = True` to `_ingest_records`, and passing
`apply_window=False` for the assignee loop specifically (an assignee scan builds a company's patent
*portfolio*, not a novelty signal — pub_number dedup already provides correctness for re-scans, per
this module's own pre-existing docstring reasoning). Pinned in
`tests/unit/test_patents_scan.py::TestIngestRecordsApplyWindow` (4 tests, including an
integration-level `scan_patents` pin: an old-dated record via the assignee loop is inserted, the
identical record via the topic loop is not).

### 2.3 Live scan — watchlist assignees, capped at ~50 new records

Run: `scan_patents(topics=[], assignees=load_assignees(), max_inserted=50)`, `EOA_PIPELINE=1`.

First attempt (before the regression fix, §2.2) inserted 51 title-less rows; those were deleted
(`DELETE FROM patents WHERE source='epo_ops' AND title IS NULL` — the run's own only rows, `raw`
untouched otherwise) and the scan re-run after the fix landed:

```
PatentScanStats(topics_scanned=0, assignees_scanned=6, queries_failed=0,
                 records_fetched=72, duplicates=11, inserted=61,
                 structured_sources_used=True)
```

`structured_sources_used=True` confirms EPO OPS was the primary source (not the Google Patents
fallback) for this run. 6 assignees scanned before the 50-cap was reached (`max_inserted` stops
*issuing further queries*, not mid-query — the 6th query's own hits are fully ingested, landing the
final count at 61, not exactly 50): Elbit, IAI, Rafael, Controp (EPO 404'd — genuine zero-match for
this assignee+keyword combination, fell through to the Google Patents fallback), RTX, Lockheed
Martin.

Post-scan verification (fresh query):

```
patents table: 160 rows total (98 google_patents_search, 61 epo_ops — up from 0 epo_ops before this round)
epo_ops rows with a non-empty title/assignees/cpc: 21 of 61 (the biblio_cap=5-per-query top hits
  across the 5 EPO-successful queries; Controp's query fell to Google, contributing 0)
```

Sample enriched rows (real `title`/`assignees`/`cpc`/`family_id`, not search snippets):

| pub_number | title | assignees | cpc (partial) | family_id |
|---|---|---|---|---|
| US11789252B1 | Optical-inertial stabilization for electro-optical systems | LOCKHEED CORP [US] | G01C19/5776, G02B26/0816 | 88309173 |
| US20250044158A1 | SPATIAL-MODE-RESOLVING BOLOMETER | RTX BBN TECH INC [US] | G01J5/023, G01J5/046, G01J5/20 | 94388135 |
| US12445208B1 | Multiplexed quantum communication and remote sensing | LOCKHEED CORP [US] | G06N10/70, H04B10/70 | 97348968 |

## 3. Dossier patents — live OPS query per product

`agent/eoa/dossier/corpus.py::collect_patents_ops(product_name, *, vendor=None, aliases=None,
product_line=None, limit=10)`: applicant = `vendor` (falling back to the first alias when no
vendor is given), keywords = `[product_name, product_line]` (both real, user-supplied — never
invented). Calls `eoa.patents.scan.search_records_for_applicant`, upserts the results into the
`patents` table (`eoa.patents.scan.upsert_records` — the same gather → upsert → real-`id` flow
`eoa.patents.survey.build_patent_survey` already uses, so a row is citable with a genuine registry
number, not a synthetic one), and returns rows in the exact shape `collect_patents` already emits.
`build_corpus` merges the two lists (deduped by `pub_number`, the pre-scanned DB-table match always
wins on a collision) before the citation registry is built. A network/DB failure at any step is
caught and logged — returns `[]` rather than ever failing the dossier build.

`include_live_patents_ops: bool = True` on `build_corpus` — pass `False` to keep stage 1 fully
offline (the module's pre-existing "no network calls, pure SQL" invariant, still available for the
task brief's own live-dry-run mode; the module docstring now documents this exception explicitly).

**Tests** (`tests/unit/test_product_dossier_corpus.py`, `TestCollectPatentsOps` +
`TestBuildCorpusPatentsOpsMerge`, 10 tests): `search_records_for_applicant`/`upsert_records` fully
mocked (recorded-shape `PatentRecord`/row fixtures) — no live network or DB calls. Covers: no
vendor/alias → no query issued; blank product name → empty; vendor used as applicant, keywords =
`[product_name, product_line]`; alias fallback when no vendor; empty gather never calls upsert;
search/upsert failures caught and return `[]`; the OPS row merges into `build_corpus`'s `patents`
list and the registry; a pub_number already present via the DB-table match is not duplicated;
`include_live_patents_ops=False` skips the live query entirely. Every pre-existing test in this
file continues to pass unchanged, offline, via a new autouse fixture that stubs
`search_records_for_applicant` to an empty gather by default.

## 4. Tests / lint

- `tests/unit/test_mcp_servers_patents.py`: 64 tests (36 new — `TestCqlTerm`, `TestBuildEpoQuery`,
  `TestThrottlingControl`, `TestPubNumberToDocdb`, `TestExtractEpoBiblio`, `TestEpoOpsBiblio`),
  all passing.
- `tests/unit/test_patents_scan.py`: 40 tests (6 new — `TestIngestRecordsApplyWindow`, the
  `max_inserted` pair), all passing.
- `tests/unit/test_product_dossier_corpus.py`: 27 tests (10 new — `TestCollectPatentsOps`,
  `TestBuildCorpusPatentsOpsMerge`), all passing.
- Full `tests/unit/` suite (4900+ tests, pre-existing + new): all passing, no regressions.
- `ruff check` clean on every file touched: `agent/eoa/mcp_servers/patents.py`,
  `agent/eoa/mcp_servers/_common.py`, `agent/eoa/patents/scan.py`, `agent/eoa/dossier/corpus.py`,
  `tests/unit/test_mcp_servers_patents.py`, `tests/unit/test_patents_scan.py`,
  `tests/unit/test_product_dossier_corpus.py`.
- `agent/eoa/mcp_servers/_common.py`'s only change (`include_headers: bool = False` opt-in on
  `http_get_json`/`_parse_response`) is additive and backward-compatible — verified by re-running
  `test_mcp_servers_common.py`/`test_mcp_servers_janes.py`/`test_mcp_servers_procurement.py`
  unchanged, all still passing.

## 5. What was not done (out of scope / follow-ups)

- The 40 EPO OPS records beyond the biblio_cap=5-per-query cap (of the 61 inserted in §2.3) are
  still title-less — the existing Google-Patents-detail-page enrichment pass
  (`enrich_stored_patents_missing_assignee`) does not currently know how to enrich an `epo_ops`-
  sourced row via a second EPO OPS biblio call; a follow-up could extend that pass (or add a
  parallel EPO-specific one) to backfill them the same way it backfills `google_patents_search`
  rows today.
- USPTO ODP (`USPTO_ODP_API_KEY`) is still not configured on this machine — every ODP call in this
  round returned `not_configured` and the code path fell through to EPO OPS/Google Patents as
  designed; ODP's own request/response contract (documented in the module's docstring from an
  earlier round, based on the OpenAPI spec) remains unverified against a real authenticated
  response.
- weekly/monthly/bd_territory report rebuilds that surface `patents` data
  (`agent/eoa/patents/report_section.py`) are out of this task's ownership boundary
  (`agent/eoa/report/**`) — flagged to that owner as a follow-up, same convention as CR-patents/
  CR-patents-2.
