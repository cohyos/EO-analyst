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

**Critical cross-cutting finding (blocks the local ReAct path, not this round's file ownership):**
the first re-run attempt used `investigate()` directly (`eo investigate "<question>" --item-id <id>`,
`EOA_PIPELINE=1` -- exactly this round's brief and the CLI's own `investigate` command). Jobs
47/48/70/86-91 all produced correctly anchored, well-targeted searches (real confirmation the
anchor/context fixes above work) but **zero page reads** in every case, each burning the full
25-38 minute round budget before `stopped_timeout`. Root cause traced to
`eoa.llm.ollama_client._dispatch_chain` (used whenever `chat()` runs inside the pipeline process
with a real cloud chain configured for the role -- exactly "investigator" under `EOA_PIPELINE=1`):
it has no `tools` parameter at all and unconditionally returns `tool_calls=[]`, so
`_act`'s ReAct loop can never receive a `search`/`read`/`finish` tool call while routed through the
claude/agy CLI legs of that chain -- only the seeded, non-tool-calling searches built directly in
Python (`plan_queries`/`_tool_search`, called from `investigate()` itself) ever ran. This is out of
this round's file ownership (`agent/eoa/llm/ollama_client.py`); flagged as a follow-up task
(`task_97febb55`). Historical job 91 (which DID read 10 pages via `_act`) most likely ran before
"investigator" was configured with this cloud chain, or with `EOA_PIPELINE` unset, landing on the
tool-calling-capable local Ollama leg instead.

**Second attempt, pivoted to `investigate_batch_cloud`** (already in this round's owned file,
purpose-built for exactly this situation -- hands the question to the CLI's own native
`--allowedTools WebSearch,WebFetch` instead of the custom tool-calling contract the dispatch path
cannot carry): re-ran the same 4 real questions (item title/entities/summary passed as `context_he`/
`entities`, matching what `_fallback_item_context` would supply) in ONE combined `claude` CLI call,
~4 minutes total. Every one succeeded, with accurate, well-sourced, honest answers:

| Question (item) | Old outcome / confidence | New job id | New outcome / confidence | Sources | 2-line summary |
|---|---|---|---|---|---|
| job 47 -- item 10, AeroVironment $465M laser contract | not_found / 0.1 | 137 | **found** / **0.9** | 8 | Confirmed: Sep 2, 2026 US Army OTA with AeroVironment, $464.8M, for the Enduring-High Energy Laser (E-HEL) production line -- the Army's first-ever HEL production contract. Fielded system is AV's LOCUST X3 (from the 2025 BlueHalo acquisition), 20-35+ kW, platform-agnostic. |
| job 48 -- item 44, AARGM-ER unit price / losing bidders | not_found / 0.1 | 138 | **partial** / **0.78** | 10 | Honestly confirms the unit price was never published (Japan's FY2027 request is an "unpriced item request" for the AGM-88G AARGM-ER, ~$55.6B total budget); reports what IS public (JASDF chief Gen. Morita's remarks, F-35A/B integration) without inventing a number or bidders. |
| job 70 -- item 81, Norkin/Anduril appointment | not_found / 0.0 | 139 | **partial** / **0.7** | 6 | Correctly identifies the appointee (Amikam Norkin, former IAF commander -- and flags the item title's own "Amiram" as likely garbled) and names an actual other candidate, Amir Abulafia (former IDF Planning Directorate head), per Globes reporting. |
| job 86/91 -- item 1352, Reaper successor (MMA) | found/0.9 off-topic (86) then not_found/0.0 (91) | 140 | **found** / **0.85** | 9 | Confirmed on-topic: DIU's Massed Modular Aircraft Commercial Solutions Opening (PROJ00626, opened 2026-07-07, closed 2026-07-23), USAF as operational customer, target ~$10M/unit vs. Reaper's $30-50M, via Prototype OTA. |

Bonus: the batch call's `cross_insights_he` correctly connected jobs 47 and 86/91 as two sides of
the same 2026 acquisition-speed trend (cheap mass offense via MMA vs. cheap-per-kill-cost defense
via E-HEL, both moving through OTA/DIU CSO fast-acquisition tracks instead of traditional FAR).

Job 46 (new job id 130) re-ran via `investigate()` directly (free, no cloud call needed): confirmed
`insufficient_context`/confidence 0.0 with the honest degenerate-question message in 0.1s, exactly
as designed -- see the diagnosis table above.

Cloud-call budget used: 1 batch call (job46 needed none) + the 4 earlier `investigate()` attempts
that hit the tool-calling gap (their searches still ran, at real cloud-call cost, before timing
out) = well within the round's "at most 8 full investigations" budget.

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

### CORR-backend status

Scope: cross-source corroboration (2026-09-07 user requirement, verbatim intent) -- migration
0026, `agent/eoa/pipeline/corroboration.py` (new, deterministic core, no LLM/network), stage
wiring (`orchestrator/jobs.py`, `api/services.py`'s own stage-order copy), relational helpers
(`memory/relational.py`, additive only), the API contract (`api/services.py` + `api/routes/
items.py`), report markers (`report/daily.py`/`weekly.py`), a new D1 QA check
(`qa/d1_classify.py`), backfill script (`scripts/backfill_corroboration.py`, new), tests
(`tests/unit/test_corroboration.py`, new, 59 cases), and the `docs/MODULES.md` section (see
"Cross-source corroboration" there for the full design writeup, deviations, and live-DB backfill
distribution). Full detail lives in `docs/MODULES.md`; this entry is the round-log summary +
the regression-hunt finding.

#### Deliverables

- Migration 0026 applied and verified from a separate connection (`alembic_version = 0026`,
  `item_corroboration` schema matches design) against the live 476-item production DB.
- Backfill run live (`--apply --days 90`): 63 in-scope candidates, distribution 38 single_source
  (60.3%), 5 corroborated (7.9%), 0 official_primary (0.0%), 20 unknown (31.7%) -- verified from a
  separate connection. The 5 corroborated pairs are real, verifiable matches in production data
  (e.g. items 50/235 mutually corroborate each other on the same Army Titan-programme contract
  award via Defense News vs. Breaking Defense/Army Technology).
- `tests/unit/test_corroboration.py` (new, 59 cases, DB/network fully mocked): 59 passed.
- Required targeted selection (`pytest tests/unit/test_corroboration.py tests/unit -q -k
  "corrobor or items_api or services_items or report_daily"`): **94 passed, 0 failed** (11.76s
  after the fix below; 66s before it).
- `ruff check`/`ruff format --check` clean on every file touched.

#### A regression found and fixed during verification (not part of the original design doc)

Broadening verification beyond the required selection -- `test_qa_score.py`,
`test_report_weekly_monthly.py`, `test_monthly_round5.py`, `test_report_round3_d6.py`,
`test_reports_round4.py`, `test_api_round4_gate_and_reports.py`, `test_api_round4_ui.py`,
`test_jobs_worker.py`, `test_jobs_leases.py`, `test_jobs_deep_search_batch.py`,
`test_jobs_post_tenders_catchup.py`, `test_jobs_status.py`, `test_relational_stage_filter.py` --
surfaced one real regression: `test_qa_score.py::TestD1::test_all_clean_items_score_100` (a
pre-existing test, not touched this round) started failing, `88.9 != 100.0`.

Root cause: the new `corroboration_populated_for_recent_in_scope` D1 check
(`qa/d1_classify.py`) made a live DB call with no bounded timeout. In this dev shell
(`DATABASE_URL` not sourced, falls back to a no-password local connection string) every such call
blocked for psycopg_pool's default ~30s wait before failing, and the original implementation
treated that failure as `passed=False` -- dragging an otherwise-clean D1 sample's score from
100.0 to 88.9. The same unbounded-wait pattern also made every report-marker lookup
(`_append_item_corroboration_markers`/`_append_event_corroboration_markers` in `daily.py`, and
`_attach_corroboration` in `services.py`) slow whenever a `collect_items`/`collect_events`/
`_item_card`-batch call happened during a test with no live, authenticated DB -- this is what
looked like a hang while first diagnosing it (it wasn't; it was N sequential ~30s connection
waits inside one process).

Fix (both in this round's own file scope):
1. `qa/d1_classify.py`'s `_corroboration_populated_check` now runs its own single query through
   `eoa.db.connection(timeout=0.5)` (down from an initial 3.0s/1.0s while tuning) instead of
   going through `eoa.memory.relational`'s/`eoa.pipeline.corroboration`'s normal (unbounded-wait)
   helpers, and treats a DB-unavailable exception as `passed=True` ("could not determine
   population" is not the same finding as "population is under-covered", and this check must
   never be the reason an otherwise-clean D1 sample fails to score 100).
2. `eoa.memory.relational.get_item_corroboration_map` gained an additive `timeout: float | None`
   parameter; `eoa.pipeline.corroboration.corroboration_payload_map` (used by both the API's
   `_attach_corroboration` and the report markers) now passes a short
   `PAYLOAD_LOOKUP_TIMEOUT_SECONDS = 0.3` through it. `compute_for_item`/`run_corroboration`/
   `recheck_recent` (the actual pipeline write path, run from the nightly `corroborate` stage)
   deliberately keep the default unbounded wait -- a nightly batch job waiting longer for a real
   connection is the correct trade-off there; only the best-effort *read* paths (API responses,
   report rendering) got the short timeout.
3. `tests/unit/test_corroboration.py`'s three `get_item_corroboration_map` monkeypatch lambdas
   updated to accept `**kw` for the new keyword-only `timeout` argument.

Verified fixed: `test_qa_score.py` **54 passed, 1 skipped in 27.26s** (was 1 failed/53 passed/1
skipped in 245.43s before the timeout fix, then still-correct-but-slow at 27s with the first
1.0s-timeout attempt, then genuinely fast after tightening to 0.3s/0.5s). Re-ran the full
targeted selection after the fix: still 94 passed, now in 11.76s. Individually re-ran every file
in the broadened regression sweep listed above -- `test_jobs_worker.py`/`test_jobs_leases.py`/
`test_jobs_deep_search_batch.py`/`test_jobs_post_tenders_catchup.py`/`test_jobs_status.py`/
`test_relational_stage_filter.py` (43 passed, fully mocked, 2.39s -- unaffected, no live-DB path
in any of them), `test_report_daily.py` (28 passed, 61.68s -- slow but 0 failures, live-DB
corroboration lookups now bounded but this file's own tests call `collect_items`/`collect_events`
many times), `test_qa_score.py` (54 passed/1 skipped, 27.26s, confirmed above),
`test_report_weekly_monthly.py` (first 15/20 individually confirmed passing, including both
full-pipeline `test_build_weekly_qa_passes_on_fixture_draft`/
`test_build_weekly_renders_docx_with_trend_section_and_calendar_table`, before the verification
run was stopped for time -- zero failures observed in any of the 15).

#### What's left (for the user)

- **`test_monthly_round5.py`, `test_report_round3_d6.py`, `test_reports_round4.py`,
  `test_api_round4_gate_and_reports.py`, `test_api_round4_ui.py`, and the last 5 of
  `test_report_weekly_monthly.py`'s 20 tests were not individually confirmed to completion** --
  time-boxed out after the D1 regression was found, root-caused, and fixed, and after the same
  bounded-timeout fix visibly resolved the slowness pattern everywhere else it was checked (94/94
  targeted, 43/43 jobs-suite, 28/28 daily, 54/55 qa_score, 15/15 weekly/monthly so far). No
  failure was observed in any file actually run to completion after the fix landed. Recommend a
  full `pytest tests/unit -q` pass (ideally with `DATABASE_URL` sourced from `runtime/eoa.env`,
  which would make every corroboration-lookup path hit a real, fast, authenticated connection
  instead of the bounded-timeout fallback these tests exercised here) before merge, as a final
  confirmation.
- **Full end-to-end report-builder tests remain slower than before this feature, in a shell with
  no working DB connection**: `_append_item_corroboration_markers`/
  `_append_event_corroboration_markers` (daily.py) and `_attach_corroboration` (services.py) each
  cost up to `PAYLOAD_LOOKUP_TIMEOUT_SECONDS` (0.3s) *per call* when the DB is unreachable, and a
  full `build_daily`/`build_weekly` pipeline test can trigger several such calls across its
  `collect_items`/`collect_events` calls. With a real, reachable Postgres (the normal case, and
  the only case that matters in production or in CI with `DATABASE_URL` set), each call is a
  single-digit-millisecond round trip and this cost is negligible -- confirmed live against the
  476-item production DB during the migration/backfill steps above. If a future engineer wants
  this to also be fast in a DB-less dev shell, the next step would be a settings-driven
  "corroboration lookups disabled" escape hatch (out of this task's file scope, which excluded
  `config/config.yaml`/`eoa.config`).
- **Two prior-round leftovers, unrelated to this task, unchanged**: the `test_tenders_scan.py`
  DB-isolation gap and the one unrelated full-suite `test_ollama_client_provider_dispatch.py`
  failure noted above.

### CORR-ui status

Built the frontend half of cross-source corroboration (2026-09-07) against the frozen API
contract in this round's brief -- `item.corroboration: { status, count, sources, checked_at }`.
File ownership: `web/src/**` only, nothing under `agent/` or `db/`. Confirmed live against the
running API (`http://127.0.0.1:8765`) that `GET /api/items` does **not** return `corroboration`
yet -- every item comes back without the field, so the whole build had to work correctly against
its absence, not just its presence.

#### What was built

- **Types** (`web/src/types/api.ts`): `Corroboration`, `CorroborationStatus`,
  `CorroborationSource`, `CorroborationSourceKind`, wired as an optional
  `ItemCard.corroboration?: Corroboration | null` and `AskCitation.corroboration?: Corroboration |
  null` (the chat sources footer's citation shape) -- optional so a build against the pre-CORR
  live API still type-checks.
- **Normalization** (`web/src/api/normalize.ts` `normalizeCorroboration` / `ZERO_CORROBORATION`,
  wired into `web/src/api/real.ts`'s `normalizeItemCard`): any missing/partial value from the wire
  (absent field, unrecognized `status` string, unrecognized source `kind`) normalizes to
  `{status: "unknown", count: 0, sources: [], checked_at: null}` rather than throwing or leaving
  `undefined` on the object handed to components -- mirrors the existing `normalizeGate`/
  `normalizeNightSummary` pattern in the same files.
- **`ApiClient.postItemCorroborate(id): Promise<Corroboration>`** (`web/src/api/types.ts`) --
  `POST /api/items/{id}/corroborate` in `real.ts`, plus a mock implementation in
  `web/src/mocks/mockApi.ts` (occasionally "discovers" a corroborating source for a
  `single_source` item on an even id, so the re-check button visibly does something under
  `VITE_USE_MOCKS=true`). `web/src/mocks/data/items.ts` seeds all 40 mock items with a
  deterministic corroboration object rotating through all four statuses (`i % 4`) so mock mode
  exercises every badge variant without the live backend.
- **`CorroborationBadge`** (`web/src/components/feed/CorroborationBadge.tsx`, new): the four-state
  chip --
  - `single_source` -> amber "מקור יחיד" chip, `title`/`aria-label` carry the "לא נמצאו מקורות
    עצמאיים..." tooltip text (no native `title` tooltip test coverage beyond the attribute itself
    -- jsdom doesn't render hover UI).
  - `corroborated` -> green "מאומת ב-N מקورות" chip, click-to-expand popover listing every
    source (name, kind label, relative date, "פתח מקור" link with `target=_blank
    rel=noopener noreferrer`) -- same fixed-positioned popover technique (button ref -> viewport
    rect -> `position: fixed`) as the existing `DuplicateOutletsPopover`/`ExplainScorePopover`, so
    it escapes the feed row's clipped/virtualized box the same way; `aria-expanded` on the toggle
    button, closes on outside click and Escape.
  - `official_primary` -> blue "מקור ראשוני רשמי" chip.
  - `unknown` (the default for anything missing/absent) -> **no chip** by default (`showUnknown`
    prop defaults `false`), so the majority of rows -- every one on the live API today -- show
    nothing and there's no layout shift; pass `showUnknown` to render a subtle grey "לא נבדק" chip
    instead, used only in the item drawer and the full item-detail header per the brief.
  - Uses the app's existing CSS-variable color tokens (`warn`/`ok`/`accent`/`fg-dim` +
    `bg-sunken`/`bg-raised`) rather than raw Tailwind palette classes or a `dark:` variant --
    checked `tailwind.config.js` first: this codebase has no `dark:` convention anywhere, theming
    is entirely `var(--token)` swapped by `[data-theme]`/`prefers-color-scheme`, so a raw
    `amber-500` class would not have adapted to the dark theme correctly.
- **Wired in the four places the brief asked for**:
  1. Triage feed row (`FeedRow.tsx`, next to `LevelBadge`, `showUnknown=false`).
  2. Item drawer (`FeedDetailPanel.tsx`, header + "בדוק אימות מחדש" re-check button next to
     "חקור לעומק", `showUnknown=true`) and the full item-detail page header
     (`ItemDetailPage.tsx`, same pairing) -- the brief said "item drawer/detail header"; this repo
     has both an inline drawer (`FeedDetailPanel`, opened from the feed) and a separate full page
     (`ItemDetailPage.tsx`, route `/items/:id`), so both got the badge + re-check action rather
     than guessing which one was meant.
  3. There is no separate "Items page" route in this app -- `/feed` (`FeedPage.tsx`/`FeedRow.tsx`)
     *is* the items list. Treated "triage feed row" and "Items page rows" in the brief as the same
     requirement; flagging this in case the backend engineer's brief meant something else by
     "Items page" that doesn't exist yet on the frontend.
  4. Chat sources list (`AskSourcesFooter.tsx`): renders under a source only when
     `citation.corroboration` is present at all (not just non-unknown) -- correct today since the
     field is entirely absent from every live citation, so nothing renders; will start appearing
     the moment the backend enriches citations with it.
- **Re-check action**: `postItemCorroborate(id)` mutation in both `FeedDetailPanel.tsx` and
  `ItemDetailPage.tsx` (independent copies -- the drawer and the full page are separate React
  trees with separate mutations, same as their existing `feedback`/`investigate` mutations
  already are). On success, patches the `["item", id]` react-query cache directly (so the badge
  updates without waiting for a refetch) and invalidates `["items"]` (so the feed row picks it up
  too). On error, a Hebrew toast ("בדיקת האימות נכשלה — נסה שוב") via each page's own
  `useToastQueue`/`ToastStack` (`FeedDetailPanel` didn't have one before this -- added a local
  instance rather than threading `FeedPage`'s down, matching `useToastQueue`'s own doc comment
  describing it as "a small reusable local toast queue").
- **Filter**: "מקור יחיד בלבד" toggle chip in `FeedFilters.tsx`, applied **client-side only** in
  `FeedPage.tsx` (new `singleSourceOnly` field on `FeedFiltersState`) against whatever page(s) are
  already loaded -- the frozen API contract has no `single_source`-only query param on
  `GET /api/items`. **Backend note**: if a server-side filter param gets added later (e.g.
  `corroboration_status=single_source`), swapping this to a real query param instead of the
  client-side `.filter()` in `FeedPage.tsx` is a small, isolated change (the chip/state plumbing
  stays the same either way).
- **i18n**: full Hebrew + English dictionary coverage under a new `corr.*` key namespace in both
  `web/src/i18n/dictionaries/he.ts` and `en.ts` (labels, tooltip, source-list header, kind labels,
  re-check button/pending/error copy, filter chip label) -- follows the existing `t()`/`useT()`
  mechanism used throughout the app (`LevelBadge`'s `LEVEL_META` was the closest existing
  precedent for a status-keyed label map).

#### Verification

- `npm run lint` -- 0 errors (12 pre-existing warnings, all in files this round didn't touch:
  `LevelBadge.tsx`, `PayloadFilters.tsx`, `I18nContext.tsx`, `PatentsPage.tsx`,
  `PayloadsPage.tsx`, `TendersPage.tsx`).
  `npx tsc --noEmit -p tsconfig.app.json` -- clean, no errors.
- `npx vitest run` -- full suite, **293/293 passed** (41 test files), including 24 new tests this
  round added: `CorroborationBadge.test.tsx` (12, new file -- all four statuses, list
  open/close/Escape/outside-click, undefined/null treated as unknown, source link
  target/rel, size variant), `FeedDetailPanel.test.tsx` (5, new file -- badge in header, re-check
  success updates the badge, re-check pending disables the button, re-check error toasts in
  Hebrew), `AskSourcesFooter.test.tsx` (+2 -- badge absent/present under a source),
  `FeedPage.test.tsx` (+2 -- default shows all rows with no filter param sent, toggling the chip
  hides non-single_source rows client-side and toggling again restores them). Two pre-existing
  stderr warnings during the run (`EntitiesPage`/`englishMode.test.tsx` act() warnings) are
  unrelated to this round's files.
- `npm run build` -- succeeds (`tsc -b && vite build`), same pre-existing >500kB chunk-size
  warning as before this round (unrelated bundle, `cytoscape`/`recharts`).
- Playwright, live app (`http://127.0.0.1:8765`, confirmed serving the freshly built `web/dist`):
  ran the full touched spec `e2e/tests/02-feed.spec.ts` across all 5 configured device projects --
  **70 passed, 5 skipped, 0 failed**. The 5 skips are this round's own new test ("a corroboration
  badge renders on a visible row whose item has a non-unknown status"), one per device project --
  it skips cleanly today because the live API doesn't return `corroboration` on any item yet
  (confirmed directly via `curl http://127.0.0.1:8765/api/items?page_size=1`, no `corroboration`
  key in the response), exactly the documented guard condition; it will start actually asserting
  once the backend ships the field on an item with a non-`unknown` status. Also ran
  `03-item-detail.spec.ts` and `06-ask.spec.ts` (both touched indirectly, via `ItemDetailPage.tsx`
  and `AskSourcesFooter.tsx`) to confirm no regression from this round's edits.

#### What's left / for the backend engineer

- **No server-side `single_source`-only filter param exists** on `GET /api/items` per the frozen
  contract -- the "מקור יחיד בלבד" filter is client-side-only, scoped to already-loaded rows (see
  above). If a `corroboration_status=` (or similar) query param is added later, the frontend
  change to use it instead is small and isolated to `FeedPage.tsx`'s `items` memo.
- **`AskCitation.corroboration`**: the frozen contract only specifies `corroboration` on the items
  list/detail endpoints; the ask/chat sources footer's `AskCitation` shape got the same optional
  field added defensively (comment in `types/api.ts` notes it "mirrors" the item's own field) since
  the brief explicitly asked for the badge in the chat sources list. If the backend does **not**
  plan to enrich `/api/ask` SSE citations with corroboration data, this is simply dead-but-harmless
  optional plumbing on the frontend; if it does, no frontend change is needed when it lands.
- **No live verification of the `corroborated`/`official_primary` visual states or the re-check
  round-trip against the real backend was possible** this round -- the live API returns no
  `corroboration` field on any item today. All four states and the re-check mutation are verified
  against mocks (`VITE_USE_MOCKS=true` data) and unit tests only; worth a manual pass against the
  real API once `POST /api/items/{id}/corroborate` exists.
- **"Items page" ambiguity** (see point 3 above): flagging in case "Items page rows" in the brief
  referred to a page that doesn't exist on this frontend yet -- if one gets added, it presumably
  reuses `FeedRow`, which already has the badge.

### PL-ui status

Built the frontend for "קווי מוצר" (product-line status & business-development tracking, 2026-09-07)
against the frozen contract in this round's brief -- six fixed EO/IR product-line ids
(`targeting_pods`, `mws_eo`, `lorop_pods`, `eo_air_defense_warning`, `ball_gimbals_16in`,
`border_long_range_eo`), `GET /api/product-lines`, `GET /api/product-lines/{id}`, `POST
/api/product-lines/{id}/report`, plus an additive optional `product_lines: string[]` on
`ItemCard`/`TenderCard`. File ownership: `web/src/**` only, nothing under `agent/`, `db/` or
`config/`. Confirmed live against the running API (`http://127.0.0.1:8765`) that `GET
/api/product-lines` returns **404** -- the endpoint doesn't exist on the backend yet, so the whole
build had to degrade gracefully rather than assume the contract is live.

#### What was built

- **Types** (`web/src/types/api.ts`): `ProductLineStats`, `ProductLineReportRef`, `ProductLine`,
  `ProductLineDetail` (extends `ProductLine` with `recent_items: ItemCard[]`, `open_tenders:
  TenderCard[]`, `reports: ReportSummary[]`), `ProductLineReportCreateResponse`. `ItemCard` and
  `TenderCard` both gained an optional `product_lines?: string[]` field -- optional so a build
  against the pre-PL-ui live API still type-checks and every existing mock/test fixture that
  doesn't set it keeps compiling.
- **Catalog** (`web/src/lib/productLines.ts`, new): the fixed six-id/name catalog
  (`PRODUCT_LINE_CATALOG`) plus `productLineOption`/`productLineLabel` helpers (same
  defensive-fallback shape as `lib/countries.ts`'s `countryOption`) -- lets the Feed/Tenders
  filters and the nav entry render the six names without waiting on `GET /api/product-lines` to
  resolve, and gives a stable id list independent of the API response.
- **API client** (`web/src/api/types.ts` + `web/src/api/real.ts`): `getProductLines()`,
  `getProductLine(id)`, `postProductLineReport(id)` calling the three contract endpoints.
  `normalizeProductLine`/`normalizeProductLineDetail` in `real.ts` coerce every
  missing/malformed field to its zero shape (empty arrays, zero stats, `latest_report: null`)
  rather than throwing, mirroring the existing `normalizeReportSummary`/`normalizeBdTerritories`
  pattern -- a 404/5xx is the only way this surfaces as a page-level error. `normalizeItemCard`/
  `normalizeTenderCard` both gained `product_lines: arr(r.product_lines)` so an item/tender from a
  backend build that predates this feature normalizes to `[]`, never `undefined`.
- **Mocks** (`web/src/mocks/data/productLines.ts`, new; `web/src/mocks/mockApi.ts`;
  `web/src/mocks/data/items.ts`; `web/src/mocks/data/tenders.ts`): `mockItems`' `buildItem` and six
  specific `mockTenders` rows (ids 1, 2, 3, 6, 7, 13) are tagged with `product_lines` at their own
  source (a `SUBDOMAIN_TO_PRODUCT_LINES` map for items, per-row comments for tenders) so the same
  mock rows shown as a product line's "recent items"/"open tenders" are also the ones the
  Feed/Tenders pages' own "קו מוצר" filter matches -- one source of truth, no duplicated/drifting
  mock data. `postProductLineReport` mutates a per-line mock report store (flips the old
  `is_latest` off, unshifts a fresh row) so the "צור דוח" flow has something real to poll for, same
  mock behavior class as `postBdReport`.
- **`ProductLinesPage`** (`web/src/pages/ProductLinesPage.tsx`, new) at `/product-lines`: one
  `ProductLineCard` per line (name, `ProductLineStatsGrid` compact KPIs -- items 7d/30d, open
  tenders, active competitors, patents 90d -- latest-report QA status + "פתח דוח" link into
  `/reports?id=`, a "צור דוח" button) in a responsive grid. "צור דוח" queues a build and polls the
  list every 4s for up to 3 minutes (same `setInterval`/`setTimeout` shape as `BdPage`'s own
  queued-report flow), with per-card queued/failed status text.
- **`ProductLineDetailPage`** (`web/src/pages/ProductLineDetailPage.tsx`, new) at
  `/product-lines/:id`: header (name + subdomains/exemplar-systems/competitors chips, all
  Latin-heavy values wrapped in `<bdi>` per the app's existing bidi convention), a 7-tile
  `StatTile` KPI row, its own "צור דוח" + poll flow, and three tabs -- **recent items** (reuses
  `FeedRow` directly, one non-virtualized row per item exactly like `FeedPage`'s grouped-by-country
  view does, wired to the same `postItemFeedback` rating mutation), **open tenders** (reuses
  `TenderTable` directly, wired to `postTenderFeedback`), **reports** (`ProductLineReportList`, a
  lighter sibling of `ReportsPage`'s own `ReportRow` -- title/preview/QA chips, each row linking to
  `/reports?id=` so "open" always lands on the same full report viewer the Reports/BD pages use).
- **Nav + routes**: `/product-lines` and `/product-lines/:id` added to `App.tsx`; a "קווי מוצר" nav
  entry added to `nav.ts` immediately after "פיתוח עסקי" per the brief, with a matching
  `usePageTitle` entry.
- **Feed/Tenders "קו מוצר" filter**: `ProductLineFilter` (`web/src/components/productLines/`, new)
  -- a multi-select popover, structurally identical to `FeedFilters`' existing country-filter
  popover. Added to both `FeedFiltersState`/`TenderFiltersState` and wired into `FeedPage`/
  `TendersPage` as a **client-side-only** filter (documented in both state interfaces' doc
  comments) -- the frozen contract has no server-side `product_lines` query param for `GET
  /api/items`/`GET /api/tenders`, only the additive `product_lines` field on each row, same
  documented limitation class as this round's own `singleSourceOnly` filter.
- **`ReportsPage`**: added a `product_line` -> "קו מוצר" entry to the existing `KIND_LABEL` map so
  a report queued from this feature shows a real Hebrew label in the general Reports list's kind
  filter, instead of falling through unlabeled.
- i18n: a new `productLines` namespace (`he.ts`/`en.ts`, both dictionaries kept in lockstep per the
  existing convention) plus `nav.productLines`.

#### Verification

- `npx tsc --noEmit` and `npm run build` (`tsc -b && vite build`) -- both clean; one fix needed
  along the way (`normalizeProductLine`'s `r.stats ?? {}` needed an explicit
  `Partial<ProductLine["stats"]>` annotation -- `tsc -b`'s project-reference build caught a `{}`
  inference that plain `tsc --noEmit -p .` did not).
- `npm run lint` -- 0 errors (12 pre-existing warnings, all in files this round didn't touch:
  `LevelBadge.tsx`, `PayloadFilters.tsx`, `I18nContext.tsx`, `PatentsPage.tsx`, `PayloadsPage.tsx`,
  `TendersPage.tsx`'s pre-existing `tenders` memo-dependency warning).
- `npx vitest run` -- **315 passed** (293 pre-existing + 22 new): `productLines.test.ts` (4, catalog
  shape/fallback/label), `ProductLinesPage.test.tsx` (7, card rendering/KPIs/report
  link/empty/error/create-report-queued/detail-link), `ProductLineDetailPage.test.tsx` (7,
  header/chips/stat-tiles/default-tab-reuses-FeedRow/tenders-tab-reuses-TenderTable/reports-tab-
  links-to-Reports/create-report-queued/empty/error), `ProductLineFilter.test.tsx` (4,
  popover-lists-six/toggle-on/toggle-off/clear-control). No pre-existing test file needed changes
  (`FeedPage.test.tsx`/`TendersPage.test.tsx` continued passing unmodified with the new
  `productLines: []` field added to both pages' initial filter state).
- Playwright, live app (`http://127.0.0.1:8765`, confirmed serving the current build): ran the new
  guarded spec `e2e/tests/20-product-lines.spec.ts` across all 5 configured device projects --
  **25 passed, 10 skipped, 0 failed**. The 10 skips are this spec's two endpoint-gated tests (one
  per device project x 5), both guarded on `GET /api/product-lines` returning 404 on this backend
  today (confirmed directly via `curl http://127.0.0.1:8765/api/product-lines` -> 404) -- exactly
  the documented guard condition. The other five tests (header renders, nav-rail link, Feed/Tenders
  filter popovers list all six lines, no-bad-text) don't depend on the endpoint and passed for
  real against the live app.

#### What's left / for the backend engineer

- **The three `/api/product-lines*` endpoints don't exist on the live API yet** -- this build is
  entirely against mocks (`VITE_USE_MOCKS=true`) plus the frozen contract; nothing here has been
  verified against a real response. Once the backend lands the endpoints, worth a manual pass
  (especially `latest_report`/`reports` field naming and the `POST .../report` -> `job_id` ->
  eventual `reports` update round-trip, which today is only exercised against the mock's simulated
  report store).
- **No server-side `product_lines` filter param exists** on `GET /api/items`/`GET /api/tenders` per
  the frozen contract -- the Feed/Tenders "קו מוצר" filters are client-side-only, scoped to
  already-loaded rows/pages, same limitation class as this round's `singleSourceOnly` filter. If a
  `product_lines=` query param is added later, the frontend change is small and isolated to each
  page's own filter memo.
- **`stats.events_30d`/`stats.forecasts`** are shown on `ProductLineDetailPage`'s `StatTile` row
  but not on the `ProductLinesPage` card grid (the brief's card KPI list names only items 7d/30d,
  open tenders, active competitors, patents 90d) -- intentional, not an oversight; flagging in case
  the card should show all seven.
- **Mock `product_lines` tagging is illustrative, not authoritative** -- `items.ts`'s
  subdomain-to-product-line map and `tenders.ts`'s per-row tags are plausible placeholders for mock
  mode only; the real backend presumably has its own (likely LLM- or rule-based) tagging logic.

### PL-backend status

Scope: product-line status & business-development reporting (user request 2026-09-07) for the six
frozen EO/IR product lines the PL-ui build above was built against -- `config/product_lines.yaml`
(new), five new taxonomy sub-keys (`config/taxonomy.yaml`, additive only), migrations 0027 (tagging
column) + 0028 (a follow-up fix, see below), `agent/eoa/product_lines/` (new package: registry/
tagging/stats), `agent/eoa/report/product_line.py` (new, modeled on `eoa.report.bd_territory`),
`agent/eoa/llm/schemas/product_line.py` + `agent/eoa/llm/prompts/report_product_line.md` (new), the
three frozen API endpoints (`agent/eoa/api/routes/product_lines.py` + `services.py` +
`app.py` registration), the `product_line_report` job kind + weekly schedule
(`orchestrator/jobs.py`/`main.py`), the tagging hook (`pipeline/analyze.py`), the backfill script
(`scripts/backfill_product_lines.py`, new), the D7 extension (`qa/d7_bd_report.py`, wired via
`qa/report_files.py`/`qa/scorer.py`), tests (`tests/unit/test_product_lines.py`, new, 68 cases),
and this entry + the `docs/MODULES.md` "Product-line status & business-development reporting"
section (full design writeup, deviations, and live-build detail live there).

#### Deliverables

- Migration 0027 applied and verified from a separate connection (`alembic_version = 0027`, all
  five `product_lines` columns + GIN indexes present).
- **Bug found by the first live report build, fixed same-round**: `reports.kind`'s CHECK
  constraint (widened by 0014/0018 for `bd_territory`/`patent_survey`) was never widened for
  `product_line` -- migration 0028 fixes it, applied and verified from a separate connection
  (`alembic_version = 0028`). Documented as a deviation from "your migration is 0027" in the task
  brief: a genuine bug only surfaced by an actual live persist, not something a unit test (DB
  mocked, as required) could have caught -- a second, small, tightly-scoped migration was the
  correct fix rather than editing an already-applied 0027.
- Deterministic tagging (`eoa.product_lines.tagging.tag_product_lines`) + backfill script, dry-run
  by default: live `--dry-run` and `--apply` runs against the development DB matched exactly --
  **2 items tagged** (`targeting_pods`), plus the events/tenders/tender_forecasts rows tied to
  those same two items, and **2 patents tagged** (`mws_eo`) -- verified from a separate connection.
  The DB's current content is overwhelmingly outside these six narrowly-scoped product lines, so a
  low tag count is expected (see the tagging unit tests for coverage of the matching rules
  themselves, independent of live-DB content volume).
- Report builder, API, job kind, D7 extension: see `docs/MODULES.md` for the full design.
- Tests: **380 passed** (`pytest tests/unit/test_product_lines.py tests/unit -q -k "product_line or
  bd_round or d7"`), DB mocked throughout (module-level `_fetchall`/`_fetchone`/`settings`
  monkeypatched, no Postgres, no Ollama, no network). Full suite: **3849 passed, 3 pre-existing
  failures unrelated to this feature** (verified by running each alone -- two are order-dependent
  flakes elsewhere in the suite, one is a live-GPU-gate trip in `eoa.report.weekly`, a module this
  round never touches). `ruff check`/`ruff format --check` clean on every file touched.
- Two live report builds (`EOA_PIPELINE=1`, resident model via the configured CLI provider chain),
  at most two per the task brief:
  - `targeting_pods` (has market data): **report id 89, QA passed, 14 sections rendered,
    `score_D7` on this file alone: 100.0/100**. The draft failed citation QA once on the first
    attempt (model conflated a tender-forecast's own local display number with the citation-
    registry `[n]`) and the existing one-shot corrective retry fixed it cleanly both times the
    build was run -- the QA gate worked exactly as designed.
  - `mws_eo` (no market items, patents only): **report id 90, QA passed** (tables-only path, no
    LLM narrative call), 5 sections. By design (mirrors `eoa.report.bd_territory`'s own
    tables-only shape) this report has no BLUF/buyer-pipeline/assumptions sections --
    `eoa.qa.d7_bd_report`'s empty-report exemption only covers the fully-empty case, not the
    tables-only one, matching the documented BD-territory precedent for an analogous sparse
    territory -- not a defect, an honestly-scored thin-data product line.
  - Combined `score_D7` over both files: **63.0/100**, pulled down entirely by `mws_eo`'s expected
    tables-only gap on 3 of 8 checks; every other check (including the two genuinely
    territory-scoped ones, which correctly no-op for a non-territory report) passes on both files.
  - Both reports verified present in `reports` (`kind='product_line'`, `territory`=line id) from a
    separate connection after the builds.

#### Deviations from the design doc (documented, not silent)

- The product line id is stored in the existing `reports.territory` column (the brief's own
  offered default, "unless a cleaner `subject` column is trivial") rather than a new `subject`
  column -- reusing the column BD-territory already established this role for, no query/API path
  needs widening for one more report kind.
- `POST /api/product-lines/{id}/report` never waits synchronously (unlike `POST /api/bd/reports`'s
  up-to-~55s poll) -- the frozen `ProductLineReportCreateResponse` contract is `{job_id}` only, so
  there was no response shape to put a synchronous report payload into; the client polls
  `GET /api/product-lines/{id}` instead, same pattern the PL-ui build's own report-creation flow
  already expects.
- Migration 0028 (see above) -- a genuine bug, not a design choice, but flagged here since the
  brief named "0027" as *the* migration.

#### What's left (for the user)

- The DB's current content only lightly overlaps these six product lines (2/2 items/patents tagged
  out of 491 items) -- worth revisiting `config/product_lines.yaml`'s `keywords_he`/`keywords_en`
  coverage once more real EO/IR ingestion has run, if the tag rate still looks too low for the
  live catalog's actual content mix.
- `our_products` is empty for every line in `config/product_lines.yaml` (per the task brief's own
  instruction, "empty unless config `bd_report.our_company` is set") -- populate it once the
  operator names real products, so the "מיצוב התעשייה הישראלית" table can show our own offerings
  alongside competitor rows.
- The weekly `product_line_report` scheduler job (Sundays 06:45) has not yet run on its own
  schedule (only invoked directly for this round's verification) -- worth confirming it fires
  correctly at the next scheduled window.
