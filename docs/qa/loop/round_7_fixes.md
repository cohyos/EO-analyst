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
