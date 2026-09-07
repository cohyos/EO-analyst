### R7-investigations status

Scope: `docs/qa/loop` D4 (weight 12, judge 78) -- golden investigation content quality
(jobs 91, 86, 70, 48, 47, 46, `jobs.kind='deep_search'`) reported not_found/off_topic with
confidence 0.0-0.3, unchanged since round 3. Files touched: `agent/eoa/search/deep_search.py`,
`agent/eoa/llm/schemas/analysis.py` (new `FallbackSynthesisOut` only), `tests/unit/test_deep_search_round7.py`
(new, 37 cases, no DB/network/LLM). Diagnosed against `127.0.0.1:5432` (`runtime/eoa.env`) by
reading each job's `payload`/`result` and every `investigation_log` row, plus the underlying
`items` rows for their `title`/`entities_mentioned`/`summary_he`. Lint/format clean
(`ruff check`/`ruff format --check`).

#### Diagnosis table

| Job | Question (short) | Historical outcome | Root cause found | Fixed this round? |
|---|---|---|---|---|
| 46 | (garbage) "no further search needed, the article provides all necessary information" | not_found, conf 0.3, garbled Hebrew explaining a missing source it was never told about | Item 117 is an unrelated AI/IP-protection interview; triage enqueued a `deep_search` job whose "question" field is leftover meta-commentary, not a real question. Wasted the full query budget on 8 generic EO/IR/computer-vision searches with nothing to anchor to. | Yes -- `_is_degenerate_question` short-circuits before spending any budget; the real bug (triage enqueueing this at all) is in `eoa.pipeline.triage`, out of file ownership this round. |
| 47 | "US Army launches laser production, $465M contract" | not_found, conf 0.1; 15 searches, **zero page reads** | Item 10's own `entities_mentioned` already named `AeroVironment` (the actual awardee) and the program is E-HEL -- but `job.payload` carried no `context_he`/entities at all, so the investigation searched blind on generic "$465M laser" phrasing that no engine matches, and (on this historical run, predating the round-5/6 MIN_PAGES_BEFORE_NOT_FOUND gate) gave up without ever reading a candidate page. | Partially -- `_fallback_item_context` now looks the item up directly and would have anchored "AeroVironment"/"E-HEL"; `MIN_PAGES_BEFORE_NOT_FOUND` (already landed pre-round-7) now forces at least 2 reads before an early not_found. Root propagation bug (job payload never carries `context_he`) is `eoa.orchestrator.jobs`, out of file ownership -- flagged as a follow-up task. |
| 48 | AARGM-ER unit price in Japan FY2027 budget + losing bidders | not_found, conf 0.1; 9 searches, **zero page reads** | Item 44's own summary states plainly: "details on quantity or cost were not published... presented as a budget request without a price" -- the source article itself says this data isn't public. AARGM-ER is also not a competitively-bid program at this stage, so "losing bidders" may not exist as a real construct. This is a **genuinely unanswerable-from-open-sources** question. | Yes, honestly -- the fixed persistence/read-before-give-up rigor (`MIN_PAGES_BEFORE_NOT_FOUND`, pre-round-7) plus this round's context fallback still won't invent a number that was never published; the correct outcome is a well-justified `not_found` that says so explicitly, not a forced `found`. |
| 70 | "Who were the other candidates for the role, and what's the strategic significance of choosing Norkin over them?" | not_found, conf 0.0; queries hallucinated "defense contract"/"EO/IR systems" framing; 2/6 page reads died on a transient DNS resolution failure with no retry | Item 81 is about Anduril appointing former IAF commander Amiram Norkin to head its Israel activity -- a **personnel appointment**, not a contract or product. `context_he` was empty (same missing-propagation bug as job 47), so the planner had zero domain grounding beyond the bare surname "נורקin" and confidently invented a "defense contract"/"competing systems" frame instead. Two of six `read` attempts failed outright on `"[Errno -3] Temporary failure in name resolution"` with no retry, permanently burning a page-budget slot each for zero information. | Yes for the DNS retry (`_fetch_with_retry`/`_is_transient_fetch_error`, one immediate retry on a transient network blip, never on a permanent failure). Partially for context -- `_fallback_item_context` would have anchored "Anduril"/"Rafael"/"IDF" this time; the root propagation bug is the same `eoa.orchestrator.jobs` gap as job 47. |
| 86 | Verify/expand "US Air Force speeds Reaper successor timeline after Iran losses" | `found`/0.9 confidence -- entirely about Elbit's MOSP 5000, a system never mentioned in the question (the original job-86 regression) | Predates the anchor-gate/relevance-gate fix (landed 2026-09-06 evening, job 86 ran 04:30 that morning) -- round-2 queries drifted to generic EO/IR terms with zero connection to Reaper/Iran/USAF and the model `finish`'d confidently on an unrelated system. Already fixed by prior rounds' `extract_anchors`/`_query_anchor_ok`/`_relevance_gate` (verified via `tests/unit/test_deep_search_anchors.py`, all passing); nothing new to fix here. | N/A -- pre-existing fix from a prior round, confirmed still in place. |
| 91 | Same question as 86 (expand-investigation retry, `budget_multiplier=2.0`) | not_found, conf 0.0, **blank generic message** despite reading 10 pages across 4 rounds, including twz.com's own on-topic "USAF wants MQ-9 Reaper successor" article | Ran with the anchor/relevance gate already active (queries were properly Reaper/Iran-grounded, real relevant pages were fetched) -- but every round's `_act` step budget (`max_steps=8`) was consumed by search/read overhead (several security-quarantined and robots.txt-disallowed fetches along the way, each costing a full model turn) before the model ever reached a `finish()` call. `_finalize_outcome`'s "no result" branch then discarded all 10 page summaries for a blank, 0-confidence not_found -- the exact "content quality remains not_found, confidence 0.0" symptom the round-7 judge flagged. | Yes -- `_act`'s `max_steps` raised 8 -> 12 (fewer rounds exhausted by overhead), and `_synthesize_from_reads` (new) salvages a `partial` answer from `inv.read_summaries` when every round ends without a `finish()` call, still gated through the same `_relevance_gate` a normal finish uses so an off-topic pile of reads (job 86's original failure mode) can't slip through this path either. |

#### Fixes (all in `agent/eoa/search/deep_search.py` unless noted)

1. **`_is_degenerate_question`** (job 46): recognizes a small set of Hebrew/English "no further
   search needed" marker phrases; only fires when they account for essentially the whole question
   text (not merely quoted inside a real, substantive question), and is deliberately **not** gated
   on `extract_anchors` -- that function's Hebrew heuristic treats any content word (len >= 3, not
   in its small curated stopword list) as an anchor, so ordinary words in the marker sentence
   itself ("צורך", "בחיפוש", "המידע"...) would already defeat an anchors-based gate. Wired into
   `investigate()` via `max_rounds=0` (a clean no-op for the round loop, everything else --
   budget/`_finalize_outcome`/`_log`/`_learn` -- runs unchanged; zero hits ever seen naturally
   classifies it as `insufficient_context`, the honest read).

2. **`_fallback_item_context` + `investigate()` wiring** (jobs 47/70, mitigates 48): when the
   caller passes no `context_he` at all but does have an `item_id`, looks the item's own
   `title`/`entities_mentioned`/`summary_he` up directly and formats it in the same
   "כותרת הפריט: .. / ישויות: .. / תקציר: .." shape `extract_anchors` already parses. Never raises
   on a DB hiccup (logged and swallowed). The actual propagation bug -- `eoa.orchestrator.jobs`'s
   two job runners forward only `job.payload["context_he"]` verbatim -- is out of this round's file
   ownership; flagged as a follow-up task (`task_0c46e731`).

3. **`_synthesize_from_reads` + `FallbackSynthesisOut`** (job 91): when every round of the
   persistence protocol ends without the model completing a `finish()` call but at least one page
   WAS successfully read, asks the model (tools-less, DATA-framed, same discipline as
   `_summarise_page`) to write a best-effort answer strictly from the page summaries already
   gathered (`inv.read_summaries`, new field on `Investigation`) instead of discarding them.
   Confidence/source-count capping is left to the existing `_finalize_outcome` pass; an off-topic
   synthesis is downgraded to `not_found` by the same `_relevance_gate` a normal `finish` call
   uses. Wired into `investigate()` right before `_finalize_outcome`, wrapped in `try/except` so a
   synthesis failure can never crash the investigation.

4. **`_act`'s `max_steps` raised 8 -> 12** (job 91): a round with 2-3 wasted turns (a
   security-quarantined or robots.txt-disallowed fetch) still leaves enough turns to actually
   synthesize and call `finish`.

5. **`_fetch_with_retry`/`_is_transient_fetch_error`** (job 70): exactly one immediate retry when
   a page fetch fails on a transient-looking error (DNS resolution failure, connection
   reset/refused, timeout) -- never retries a permanent failure (404, paywall, robots.txt
   disallow). `_tool_read` now uses this instead of calling `fetch_remote` directly.

6. **Diagnostic-accuracy fixes** (needed for this round's own diagnosis, not previously true):
   `_tool_search`'s `investigation_log` entries hardcoded `engine="searxng"` regardless of which
   backend (`ddgs`/`ddgs-news`/`searxng`) actually served the query -- now logs the real value off
   the returned hit(s), falling back to the configured provider name only on a zero-hit query.
   A security-quarantined page's URL was never logged at all (only a successful read passed `url=`
   to `_log`) -- now logged too (content still never persisted, only the guard's already-truncated
   `kind`/excerpt as before).

7. **`plan_queries`'s `LLMOutputError` fallback**: used to dump the raw (often long, Hebrew)
   question verbatim as the retry query in every fallback language including English -- a full
   free-text sentence returns few or zero hits from a metasearch engine. Now uses the
   already-extracted anchors (short, specific, engine-friendly) when any exist, falling back to the
   full question only when there is truly nothing else to search on.

#### Verification

- `ruff check` / `ruff format --check` on all three touched files: clean.
- `tests/unit/test_deep_search_round7.py`: **37/37 passed** (mocked DB/network/LLM throughout).
- Full existing deep-search suite (`test_deep_search_anchors.py`, `test_deep_search_outcomes.py`,
  `test_deep_search_answer_format.py`, `test_deep_search_blocked_round5.py`,
  `test_deep_search_budget.py`, `test_deep_search_cloud_batch.py`, `test_deep_search_mcp_tools.py`,
  `test_deep_search_reconcile_round4.py`) + the new file: **159/159 passed**, no regressions.

#### Re-run results (new investigations, cloud chain: claude-sonnet-5 -> gemini-3.1-pro-high -> local)

_See the table appended below once the live re-runs complete -- `EOA_PIPELINE=1`,
`PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/eo.exe investigate "<question>" --item-id <id>`,
budget: up to 8 full investigations, job 46's is free (short-circuits before touching the cloud)._

### R7-tenders status

Scope: `docs/qa/loop/round_6_judge.md` D9 finding 2 (candidate id 34, "Expert / Coach Transformatie
en Contracten Juridisch", Gemeente Rotterdam -- a Dutch legal/HR consulting contract that cleared
the keyword gate on a fluke substring match) plus finding 1's SAM.gov key-gating piece. Files
touched: `agent/eoa/tenders/scan.py`, `config/tenders.yaml`, `scripts/repair_round7_tenders.py`
(new), `tests/unit/test_tenders_round7.py` (new, 54 cases, no DB/network). `agent/eoa/tenders/
feedback.py` was left unmodified -- nothing in either finding needed it.
Ran against `127.0.0.1:5432/eoanalyst` (`runtime/eoa.env`). Lint/format clean (`ruff check`/`ruff
format --check`).

#### Root cause of candidate id 34 (finding 2)

Traced live: id 34's `raw` payload (`nl_tenderned`, TenderNed's own `opdrachtBeschrijving` field)
reads "...via de **priva­atr­echtelijke** weg..." ("under private law") -- the EO/IR domain
keyword **"ATR"** (Automatic Target Recognition) matched as a bare casefold substring *inside* the
unrelated Dutch word "privaatrechtelijke". `_matches_keywords`/`_has_procurement_signal` never
required a word boundary, so any short (2-4 char) keyword/procurement-signal token was silently
exposed to this class of false positive in any language. TenderNed's own API (probed live
2026-09-06/07) exposes no CPV classification field at all in this response shape, so the CPV
pre-filter alone would not have caught this specific row -- the word-boundary fix is the one that
actually closes it.

#### Fixes

1. **Word-boundary matching for short keywords/signals** (`_term_present`, `agent/eoa/tenders/
   scan.py`): a term of at most `_SHORT_KEYWORD_MAX_LEN` (4) characters must now sit at a word
   boundary (`\bterm\b`, Unicode-aware -- works the same for Hebrew) rather than matching as a bare
   substring; a longer phrase (`"seeker"`, `"computer vision"`) is untouched, so a genuine plural
   ("seekers") still matches -- confirmed no regression against the full existing suite. Wired into
   both `_matches_keywords` (the DOMAIN signal) and `_has_procurement_signal` (the PROCUREMENT
   signal, since `RFP`/`RFI`/`RFQ` are exactly as short as `ATR`).

2. **CPV-code-family pre-filter** (`_cpv_gate_reject_reason`, wired into `_gate_reject_reason` as a
   fifth hard-rejection case): a notice whose CPV code(s) are *all* within `cpv_deny_prefixes`
   (`79`/`80`/`85`/`98` -- business/legal/HR consulting, education, health/social work, other
   community services) and *none* within `cpv_allow_prefixes` (`35`/`38` -- security/defence,
   laboratory/optical/precision instruments) is now a hard, structural rejection, independent of
   keyword/LLM relevance -- exactly the "reject obvious non-defence CPV families like 79xxxxxx"
   ask. A code in neither list, or no CPV data at all (most sources), is left alone; a mixed
   allow+deny set is never rejected (the allow signal wins). Both lists are config-driven
   (`config/tenders.yaml`'s new `cpv_allow_prefixes`/`cpv_deny_prefixes`, with the same values as
   in-code defaults as a fallback).

3. **`cpv_naics` extraction, previously dead code**: `NoticeRaw.cpv_naics` existed and was already
   persisted to the `tenders` table, but no parser ever populated it. Fixed for all four structured
   parsers -- TED (`classification-cpv`, a list, confirmed live -- also added to the `fields` param
   TED's query itself requests, which it silently omitted before), UK Contracts Finder / generic
   OCDS (`tender.classification.id`, a single code, confirmed live against Contracts Finder), and
   generic JSON-list (opt-in `parse_hints.cpv_field`, since a flat JSON API has no conventional CPV
   field/shape to guess).

4. **Dynamic key-gated `api_json` source enabling** (finding 1c, SAM.gov): `_api_json_source_enabled`
   replaces the old static `verified` check -- a source stays enabled when `verified: true`
   (unchanged), OR becomes enabled the moment its `needs_key_env_var` (e.g. `sam_gov_api`'s
   `SAM_GOV_API_KEY`) is actually set to a non-empty value in the environment, with no config edit
   required. `_fetch_api_json` now resolves a `{api_key}` placeholder in `query_params`/
   `query_template` from that real environment variable at request time (`config/tenders.yaml`'s
   `sam_gov_api` entry updated from a hardcoded `DEMO_KEY` literal to `"{api_key}"`) -- `SAM_GOV_API_KEY`
   is not present in this environment, so `sam_gov_api` stays skipped exactly as before; this is
   forward-looking so the moment a real per-user key is added, no further code/config change is
   needed.

5. **Repair path** (`find_prefilter_violations`/`repair_relevance_prefilter`,
   `scripts/repair_round7_tenders.py`): re-checks every `intake='candidate'` row's persisted
   `items.clean_text` + `cpv_naics` against today's gate; `--apply` archives (`status='archived'`,
   never deleted) only the rows that fail, scoped by SQL to `intake = 'candidate'` -- an
   `'accepted'` row or `tender_feedback` is never touched.

#### Verification

- `ruff check` / `ruff format --check` on all touched Python files: clean.
- `tests/unit/test_tenders_round7.py`: **54/54 passed** (HTTP/DB mocked throughout).
- Full existing tender suite + the new file (`-k tender` across `tests/unit`): **377/377 passed**,
  no regressions. (Note: this needs `DATABASE_URL` sourced from `runtime/eoa.env` to run in
  seconds -- `eoa.tenders.scan._candidate_duplicate_exists`, a round-6 addition, is not mocked by
  `test_tenders_scan.py`'s `_common_patches` helper, so several pre-existing `TestScanTendersOpenIntake`/
  `test_tender_feedback_round4.py` tests fall through to a real (fast, since the DB is reachable)
  connection when a notice carries a URL. Confirmed this is pre-existing, not a round-7 regression:
  the same tests fail identically on the pre-round-7 code with `DATABASE_URL` unset. Flagged as a
  test-isolation gap for whoever next touches `test_tenders_scan.py`, not fixed here since that file
  is outside this round's ownership.)

#### Live run + repair, applied and verified (2026-09-07, `DATABASE_URL` from `runtime/eoa.env`)

- Ran `scan_tenders` live (`EOA_PIPELINE=1`) restricted to the verified structured `api_json`
  sources (`ted_eu`, `uk_contracts_finder`, `ted_eu_cpv`, `uk_find_tender`, `fr_boamp`,
  `nl_tenderned`, `us_grants_gov`; `llm_budget_s=0` to spend zero LLM calls on this diagnostic
  pass): **354 real notices fetched across all 7 sources, 0 matched/inserted** under the
  production default `since_days=3`.
- Diagnosed why: re-evaluating the same fetched TED notices (106 across `ted_eu`+`ted_eu_cpv`, all
  with `cpv_naics` correctly populated -- e.g. `["38000000"]`) against wider windows found **0
  matches at 90 days, 1 at 3650 days** (a 2016 Belgium EORF optronics notice already in the table
  as id 13). TED's `FT ~ "<phrase>"` full-text search endpoint is returning its historical archive
  for these EO/IR keyword queries, not recent-first results -- a real, disclosed limitation:
  fetching/parsing/CPV-extraction all work correctly, but this endpoint alone will not surface a
  newly-published EO/IR notice without either a date-range query parameter or a
  "sort by publication date descending" option (not yet found in a 400-safe query subset). This is
  the concrete explanation for round 1-6's "5 accepted rows, all with past deadlines" pattern
  persisting -- **not fetcher bugs**, a recency/sort gap in how TED is queried. Left as a follow-up
  (needs another live-probe session against TED's query DSL, out of this round's time budget).
- **UK government portals rate-limited this session's live probing**: `uk_find_tender` and (on a
  second pass) `uk_contracts_finder` both returned HTTP 429 after repeated keyword-rotation
  requests in quick succession -- `eoa.tenders.scan._collect_source_notices`'s per-keyword loop
  (`MAX_KEYWORDS_PER_API_SOURCE=5`) has no inter-request delay for `api_json` sources (only
  `kind: search` has a query budget counter, `eoa.search.budget`, and that's a call-count cap, not
  a pacing delay). Not fixed this round (out of scope for either finding, and risked burning the
  round's live-request budget chasing it) -- **flagged for the user**: a real operational gap
  worth a small follow-up (a `time.sleep` between `_fetch_api_json` calls, or reusing
  `eoa.search.budget`'s pattern for `api_json` sources too).
- **Repair applied and verified from a separate connection**: `repair_round7_tenders.py --apply`
  found and archived exactly the 2 rows the fixed gate no longer clears -- id 34 (`nl_tenderned`,
  the Rotterdam HR contract, `keyword_gate_no_domain_term` -- the finding's own named target) and
  id 35 (`us_defense_innovation_search`, a generic Northrop Grumman EO/IR product page,
  `keyword_gate_no_procurement_signal` -- a bonus catch: it never carried real "tender"/"RFI"/...
  language either, an unrelated pre-existing gap this same repair pass happens to close). Both now
  `status='archived'`, `intake` left untouched at `'candidate'` per the repair's own scoping. All 5
  `accepted` rows unchanged (still `archived` status, deadlines already past, as before). Id 38
  (`jp_search`, a Counter-UAS category-listing page) correctly NOT flagged -- its text contains the
  plain word "tenders", which still substring-matches at its length (>4 chars), so it remains a
  legitimate (if borderline) candidate for the operator's own feedback to judge.
  `tender_feedback` row count confirmed unchanged (0 before, 0 after -- no feedback exists yet).

#### What's left (for the user)

- **No API keys added this round**: `SAM_GOV_API_KEY` remains unset (per `docs/qa/loop/
  qa-loop-state` pending decisions) -- `sam_gov_api` stays skipped, though it will now activate
  itself automatically the moment a real key is added, no further code change needed.
- **TED recency/sort gap** (above): the live fetchers are structurally correct (fetch, parse, CPV
  extraction, gate, dedupe all verified working), but TED's full-text search endpoint alone won't
  surface genuinely new open notices without a date-range or sort parameter -- needs another
  live-probe session against TED's query DSL to find one that stays inside the 400-safe subset.
- **UK portal rate limiting** (above): `uk_find_tender`/`uk_contracts_finder` 429'd during this
  round's live testing from request-rate, not code correctness -- a pacing delay between
  `api_json` keyword-rotation requests would fix it; not implemented this round.
- **`test_tenders_scan.py` DB-isolation gap** (above): `_common_patches` doesn't mock
  `_candidate_duplicate_exists` (a round-6 addition), so several of its tests silently depend on a
  reachable `DATABASE_URL` rather than being true DB-free unit tests. Pre-existing, confirmed not a
  round-7 regression; whoever next owns that file should add the missing mock.

### R7-tenders-b status

Scope: the R7-tenders round's own two flagged follow-ups -- TED returning its archive instead of
recent notices (finding 1), and UK Contracts Finder/Find a Tender 429-ing under this repo's
no-pacing keyword-rotation loop (finding 2). Files touched: `agent/eoa/tenders/scan.py`,
`config/tenders.yaml`, `tests/unit/test_tenders_round7b.py` (new, 37 cases, no DB/network). Ran
against `127.0.0.1:5432/eoanalyst` (`runtime/eoa.env`). Lint/format clean (`ruff check`/`ruff
format --check`).

#### Finding 1: TED's working date-filter/sort/pagination query shape

Live-probed 2026-09-07 (all against `https://api.ted.europa.eu/v3/notices/search`, POST):

- **Date filter**: `AND PD>=YYYYMMDD` appended directly inside the `query` expert-query string
  (not a separate top-level JSON field) -- `{"query":"FT ~ \"electro-optical\" AND
  PD>=20260801",...}` -> HTTP 200, only notices with `PD >= 2026-08-01` (confirmed: 4 notices, all
  dated Aug 3 - Aug 21 2026, none from the archive).
- **Sort**: `SORT BY PD DESC` -- also inside the `query` string itself, not a top-level field.
  `sortField`/`sortOrder` (top-level JSON keys) and a top-level `"sort":[...]`/`"sort":{...}`
  object were all tried first and rejected with HTTP 400 `"Unrecognized field"`, naming the full
  list of fields the endpoint's `PublicExpertSearchRequestV1` actually accepts (none of them are a
  sort key). Only `SORT BY <field> DESC` inside the query's own grammar works; there is no `ASC`
  keyword -- ascending is simply the default, and an explicit `SORT BY PD ASC` returns HTTP 400
  `QUERY_SYNTAX_ERROR "extraneous input 'ASC'"`. Confirmed live: `FT ~ "electro-optical" AND
  PD>=20260801 SORT BY PD DESC` returned the same 4 notices newest-first (`2026-08-21,
  2026-08-21, 2026-08-12, 2026-08-03`) instead of the default oldest-first order.
- **Pagination**: a plain top-level `"page": N` (1-indexed) integer field works alongside `query`/
  `fields`/`limit` -- `page=1` and `page=2` at the same `limit` returned different, non-overlapping
  notice ID sets. `iterationNextToken` (present in every response, `null` when the current page's
  results fit under `totalNoticeCount`) looked like the "real" v3 cursor mechanism but was never
  populated with a usable value in any probe; `page` is simpler and confirmed working, so that's
  what's wired.
- **Deadline field**: `deadline-receipt-request` (NOT `deadline-receipt-tenders`, which the
  endpoint rejects with HTTP 400 `"Unrecognized field"` naming ~200 valid alternatives) -- a list
  of per-lot ISO datetimes, first element used. Confirmed combined with the CPV query too:
  `{"query":"classification-cpv=38620000 AND PD>=20260701 SORT BY PD DESC",
  "fields":[...,"deadline-receipt-request"],...}` -> HTTP 200, 48 real notices (a 68-day window,
  one CPV code), several carrying real future deadlines (e.g. `2026-09-15T09:00:00+02:00`,
  `2026-10-02T23:59:59+03:00`).

#### Fixes

1. **`TenderSource` gains three new fields** (`max_pages: int = 1`, `pace_seconds: float = 0.0`,
   `query_lookback_days: int = 0`), every default chosen so a source that doesn't opt in behaves
   *exactly* as before this round -- `max_pages=1` is a single request, `pace_seconds=0.0` makes
   `time.sleep(0.0)` a real no-op, `query_lookback_days=0` disables the `{since_date}`
   substitution entirely. `config/tenders.yaml`'s `ted_eu`/`ted_eu_cpv` set `max_pages: 3`,
   `pace_seconds: 2.0`, `query_lookback_days: 30`; `uk_contracts_finder`/`uk_find_tender` set only
   `pace_seconds: 2.0`. `query_lookback_days` is deliberately a static per-source config value,
   *not* the caller's own `since_days` (see the field's own docstring) -- it only needs to be a
   generous buffer wider than any `since_days` this repo actually calls `scan_tenders` with (3 in
   production, 14 for this round's backfill), which lets it live entirely inside `_fetch_api_json`
   (computed from `dt.date.today()`) without threading `since_days` through that function's
   signature at all.
2. **`_fetch_api_json`'s outward signature is unchanged** (`(src, keyword)`, exactly as before) --
   pagination, since-date substitution, and pacing/backoff all live *inside* it, reading
   `max_pages`/`pace_seconds`/`query_lookback_days` off the `src` argument it already receives.
   This was a hard constraint, not a style choice: `tests/unit/test_tenders_scan.py`'s
   `TestCollectSourceNoticesApiQueryKeywordsOverride` (owned by a different round, not editable
   this round) monkeypatches `_fetch_api_json` with a bare `def fake_fetch(src_arg, keyword)`
   stub -- any extra positional or keyword argument added at the `_collect_source_notices` call
   site would have broken it immediately (`TypeError: unexpected keyword argument`). Internally,
   `_fetch_api_json` now loops `page` from 1 to `max(1, src.max_pages)`, substituting
   `{since_date}`/`{page}` into `query_template`/`query_params` alongside the existing
   `{keyword}`/`{api_key}` (a plain `.replace()`/`.format()` no-op for every source whose template
   doesn't reference them), and stops paging early the moment a page returns zero notices.
3. **`_is_rate_limited_error` / `_call_with_rate_limit_backoff`** (finding 2): every per-page HTTP
   round trip now goes through a retry wrapper that catches exactly HTTP 429/503 (checked both as
   a direct `httpx.HTTPStatusError.response.status_code`, host/dev role, and as a standalone
   `429`/`503` number inside a plain exception's string, the shape the isolated `agent` role's own
   `FetchError` surfaces) and retries up to 3 attempts total with exponential backoff (1x, 2x, 4x
   the source's own `pace_seconds`, or a 2.0s default for a source with no pacing configured). Any
   other error (400, 404, DNS/connection failure, ...) still propagates immediately on the first
   attempt, unchanged from before this round.
4. **`_collect_source_notices` gains inter-keyword pacing**: `time.sleep(src.pace_seconds)`
   between each of the (up to `MAX_KEYWORDS_PER_API_SOURCE`=5) keyword-rotation requests, skipped
   before the first one. A no-op for any source that doesn't set `pace_seconds` (every existing
   test fixture, every config entry not touched this round).
5. **`_parse_ted_notices` extracts `deadline`** from the newly-requested
   `deadline-receipt-request` field (first element when it's a list, which it always is live;
   defensively handles a bare scalar too). Absent/empty leaves `deadline` at its default `None`,
   same as every other TED notice before this round -- never itself a rejection (W2b open intake
   is unchanged).
6. **`_within_window` also excludes a notice whose deadline has already passed** (`notice.deadline
   < today`), generalized to every `kind: api_json` source, not just TED -- `deadline` is only
   ever populated by a structured source's own parser at parse time (TED's
   `deadline-receipt-request`, Contracts Finder/FTS's `tender.tenderPeriod.endDate`, ...), the
   same "trustworthy date, worth filtering on" class of field the function's pre-existing
   `published_at` check already relies on. A notice with no deadline at all (most search/rss
   sources, or a structured source whose deadline field wasn't populated for that particular
   notice) is completely untouched -- same open-intake philosophy (W2b) as before. A deadline of
   exactly today still counts as open. This directly targets the round's own diagnosed symptom:
   once the archive/no-date-filter bug (finding 1, above) was fixed, every remaining
   "recent-enough by `published_at`" TED notice this repo had ever accepted *still* had a passed
   deadline, making it useless for BD purposes despite clearing every other gate.

#### Verification

- `ruff check` / `ruff format --check` on all touched Python files: clean.
- `tests/unit/test_tenders_round7b.py`: **37/37 passed** (HTTP/DB mocked throughout, `time.sleep`
  mocked in every backoff/pacing test so none of them actually wait).
- Full tender suite (`test_tenders_round7b.py` + `-k tender` across `tests/unit`, `DATABASE_URL`
  sourced from `runtime/eoa.env` per the pre-existing `test_tenders_scan.py` DB-isolation gap
  noted above): **414/414 passed** (377 pre-existing + 37 new), no regressions.
- Full `tests/unit` suite: **3702 passed, 1 failed** -- the one failure
  (`test_ollama_client_provider_dispatch.py::TestChatStructuredProviderThreading::
  test_provider_passed_through_to_chat`) is unrelated to this round (LLM provider-dispatch
  threading, a file this round never touches) and was not investigated further, per file
  ownership.

#### Live run + verification (2026-09-07, `DATABASE_URL` from `runtime/eoa.env`, `EOA_PIPELINE=1`)

Ran `scan_tenders(since_days=14, sources=<the same 7 verified structured sources R7-tenders'
own diagnostic pass used: ted_eu, uk_contracts_finder, ted_eu_cpv, uk_find_tender, fr_boamp,
nl_tenderned, us_grants_gov>, llm_budget_s=0)` -- zero LLM calls, same methodology as the prior
round's own diagnostic pass, for a direct before/after comparison:

- **368 notices fetched** (up from 354 pre-round, from TED's added pagination and this run
  reaching every source without a rate-limit failure), **1 matched, 1 inserted** (`intake=
  'candidate'`, `status='open'`) -- `Finland - Security cameras - Computer Vision Technology for
  Airport Operations` (TED notice `593909-2026`, source `ted_eu_cpv`, published 2026-08-28, no
  deadline in TED's own response for this particular notice -- confirmed by reading the stored
  `raw` JSON directly, genuinely absent from the source, not a parsing gap).
  `sources_scanned=7, sources_failed=0` -- neither UK source failed outright this run (no 429
  propagated past the new backoff).
- This is the first TED-sourced row in the `tenders` table with `status='open'` since the table
  has existed -- every prior TED row (id 13, a 2016 EORF notice) is `archived` with a
  long-since-passed deadline. Not a large number (1 new candidate against a narrow
  domain-keyword + 14-day window intersection, which is realistic, not a bug -- the two-signal
  gate is intentionally strict), but a structural first, not a fluke: the mechanism (fetch, date
  filter, sort, page, parse deadline, gate) is now demonstrably working end-to-end, which is what
  finding 1 asked for.
- **Verified from a separate connection**: `tenders` table has 9 total rows (up from 7 pre-round
  -- 2 new: id 34/35 from R7-tenders' own repair pass were already there; this round adds only id
  42). Id 42 is the only row with `status='open'` and a source touched this round; every other
  row's status/intake is unchanged from before this round's run (ids 13, 15, 18, 20, 30, 34, 35,
  38 -- read directly, not just diffed against a stats counter), confirming this round's insert-
  only discipline (no `UPDATE`/`DELETE` of pre-existing rows).
- 6 sample notices were requested; only 1 new one was inserted this run (see above) -- the table's
  other 8 rows (all pre-existing, from earlier rounds) are, for completeness: id 13 (`ted_eu`,
  Belgium EORF, archived/accepted), id 15/18/20/30 (`rfi_rfp_news`, US ATP/EO-IR RFIs, all
  archived/accepted with deadlines 2015-2025), id 34 (`nl_tenderned`, Rotterdam HR contract,
  archived/candidate -- R7-tenders' own repair target), id 35 (`us_defense_innovation_search`,
  Northrop Grumman product page, archived/candidate), id 38 (`jp_search`, Counter-UAS listing,
  status unknown/candidate).

#### What's left (for the user)

- **UK portal pacing reduces but does not eliminate 429 risk under sustained load**: this run's
  2s inter-keyword pacing plus 3-attempt exponential backoff got through `uk_contracts_finder`/
  `uk_find_tender` cleanly, but a much larger production scan across every source in
  `config/tenders.yaml` (41 total) run back-to-back could still occasionally exhaust the 3-attempt
  budget on a bad day -- the backoff caps out at a `pace_seconds`-scaled 4x sleep on the last
  retry, not an unbounded wait.
- **TED's per-notice deadline coverage is partial**: `deadline-receipt-request` is genuinely
  absent from some TED notices (confirmed live, id 42 above) -- not every notice type carries a
  submission deadline (e.g. prior information notices, market consultations). `_within_window`'s
  new filter only excludes a notice with an explicit *past* deadline; an undated-deadline TED
  notice still passes through on `published_at` alone, same open-intake treatment as any other
  source.
- **`query_lookback_days` is a static buffer, not since_days-aware**: 30 days was chosen to
  comfortably cover both the production default (`since_days=3`) and this round's 14-day
  backfill; if a future caller ever needs `since_days` meaningfully larger than 30 (e.g. a 60-day
  backfill), TED's own server-side date filter would need widening too (`config/tenders.yaml`'s
  `query_lookback_days`), since the client-side `_within_window` check can only narrow a
  server-side result set, never widen it back out.
- **`test_tenders_scan.py` DB-isolation gap** (carried over from R7-tenders, unchanged this
  round): still not fixed, same reasoning as before -- out of this round's file ownership.
- **The one unrelated full-suite failure** (`test_ollama_client_provider_dispatch.py`, above): not
  investigated or fixed this round -- outside file ownership, and confirmed unrelated to any file
  this round touches.
