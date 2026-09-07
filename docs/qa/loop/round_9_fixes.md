### R9-investigations status

**Package:** R9-investigations (docs/qa/loop/round_8_judge.md, D4 = 63; worst-list items #2, #3, #4).
**Files owned/changed:** `agent/eoa/search/deep_search.py`, `agent/eoa/report/daily.py`
(`collect_deep_search`/`reconcile_deep_search_reruns` + helpers only), `tests/unit/test_deep_search_round9.py` (new, 23 tests).

#### 1a. Reruns of jobs 146 and 147

Re-queued via `eoa.memory.relational.enqueue_job("deep_search", payload)` with
`rerun_of_job_id` set to the original job, at the restarted orchestrator (runs current code):

- Job 146 (item 44, AARGM-ER unit price / losing bidders) -> **job 160**
- Job 147 (item 81, Norkin appointment) -> **job 161**

Outcomes (job id, outcome, confidence, whether the round-7/8 defects reproduced):

<!-- FILLED IN BELOW ONCE 160/161 FINISH -->

#### 1b. Hedge-downgrade guard widened to nominalised decision phrasing

`_downgrade_unhedged_decision_claims` previously only matched a fixed list of finite Hebrew
decision verbs (`_DECISION_VERBS_HE`: הוחלט/נבחר/זכה/נחתם). Job 147's own live rerun answer
(fetched from the `jobs` table before this round's fix) reads:

> "בחירת נורקין על פני אבולעפיה נושאת משמעות אסטרטגית משמעותית..."

— a **nominalised** decision phrase ("the selection of Norkin", noun "בחירת") that the round-8
judge explicitly flagged as a phrasing the fixed verb list "likely wouldn't even catch." Confirmed
that live text does not contain any of the four verbs, so it would have sailed through the
existing guard unchanged on a rerun.

Fixed by extracting the check into `_sentence_claims_settled_decision(sentence)`, which now also
matches nominalised Hebrew phrases (`_DECISION_NOMINAL_PHRASES_HE`): בחירת, הבחירה ב-, ההחלטה על,
המינוי של, הזכייה של, החתימה על — and their English equivalents (`_DECISION_NOMINAL_PHRASES_EN`):
"the selection of", "the appointment of", "the decision to". `_downgrade_unhedged_decision_claims`
now calls this helper instead of checking `_DECISION_VERBS_HE` directly; behavior (single hedged
placeholder sentence, removed claims recorded in `contradictions_he`) is unchanged.

#### 1c. Citation-integrity invariant made explicit (low-quality pages can never be cited)

Audited every code path that can populate `InvestigationOut.sources`:
- `_tool_read` has the only `read_urls.append` call site in the module, gated on
  `_low_quality_page_reason(text, title) is None` -- a discarded page's text is never even shown
  to the model (`_tool_read` returns only `{"url": ..., "error": "page discarded: ..."}"`), so it
  cannot influence `answer_he`/`key_facts` either.
- `_finalize_outcome` unconditionally overwrites `InvestigationOut.sources = list(inv.read_urls)`
  (`sources` is provisional on the model's own `finish()` call until then), so a model-claimed
  citation to a URL it never successfully read is always dropped regardless of what the model's
  `finish` payload says.
- `_synthesize_from_reads` (the "model never called finish" fallback) only ever synthesizes from
  `inv.read_summaries`, which is populated at the same gated call site as `read_urls`.

This already held structurally, but nothing made the invariant explicit or resilient to a future
change to this module. Added `Investigation.low_quality_read_urls` (populated in `_tool_read`
alongside the existing discard log line) and changed `_finalize_outcome`'s `sources` assignment to
`[u for u in inv.read_urls if u not in inv.low_quality_read_urls]` -- belt-and-suspenders: a
discarded URL is filtered out of `sources` even if some future code path let it leak into
`read_urls`. Verified with a unit test that directly manipulates `Investigation` state to simulate
that leak and confirms the filter still keeps it out.

#### 2. `reconcile_deep_search_reruns` folds an item's own `dedup_of` chain

Item 96 (Hebrew-language `dedup_of=10` duplicate of golden item 10) has its own never-rerun job 45
(`not_found`) rendering beside item 10's newly-fixed job 156 (`found`) in the same live report --
neither Pass 1 (question-text grouping) nor Pass 2 (rerun/expansion lineage) connects them, since
item 96's question was worded independently and job 45 was never itself rerun from job 156.

`collect_deep_search`'s SQL now also selects `i.dedup_of AS trigger_item_dedup_of` from the
already-joined `items` row and threads it through into each entry. `reconcile_deep_search_reruns`
adds a third union-find pass: for each entry whose trigger item has a `dedup_of` target, union it
with (one representative of) any entry whose trigger item **is** that target. Because the merge
happens directly on the union-find structure (not via a computed "cluster root"), an entire
`dedup_of` chain (A dedup_of B, B dedup_of C) folds into one group transitively and in both
directions, without extra queries beyond the one already-joined column. A `dedup_of` target with
no deep-search entry of its own in the period is a harmless no-op.

Unit-tested with synthetic rows reproducing the item 96/item 10 case exactly (`trigger_item_dedup_of=10`
on item 96's entry), plus a three-item transitive chain, order-independence, and composition with
the existing question-text/lineage passes.

#### 3. Live daily/weekly re-render check

<!-- FILLED IN BELOW -->

#### Tests / lint

- `tests/unit/test_deep_search_round9.py`: 23 new tests, all passing.
- `PYTHONPATH=agent PYTHONUTF8=1 EOA_SEARCH_NO_CACHE=1 .venv/Scripts/python.exe -m pytest
  tests/unit/test_deep_search_round9.py tests/unit/test_deep_search_round8.py
  tests/unit/test_deep_search_round7.py -q`: **96 passed**.
- `.venv/Scripts/ruff.exe check` / `format --diff` on all three changed/added files: clean.

### R9-reports status

**Package:** R9-reports (docs/qa/loop/round_8_judge.md worst-list items #5, #6, #8, #9, #10).
**Files owned/changed:** `agent/eoa/report/indicators.py`, `agent/eoa/report/product_line.py`
(labels only), `agent/eoa/patents/survey.py` (appendix reliability only), `agent/eoa/tenders/scan.py`
(product-line tagging of inserted notices only), `scripts/repair_round9.py` (new),
`tests/unit/test_reports_round9.py` (new, 32 cases, no DB/network/LLM), plus one narrowly-scoped
edit to `tests/unit/test_reports_round8.py` (see #4 below — that file's own existing assertion
directly encoded the pre-fix behavior this round intentionally replaces).

#### 1. Indicator-watchlist evidence column, Hebrew-only indicators (worst #5, D6)

Root cause: `_item_matches_indicator`'s only term source was `extract_key_terms` (Latin-only,
`_KEY_TERM_RE`) — an indicator written entirely in Hebrew (the norm in this corpus) never produced
a single term, so it could never mature or cite evidence regardless of how plainly a report item
covered the same story. `extract_key_terms` itself is untouched (still Latin-only — its own
docstring/tests in `tests/unit/test_report_deltas_round5.py` are unchanged and still pass).

New `_extract_indicator_terms` adds a Hebrew term source, used only by `_item_matches_indicator`'s
own match test: `_content_tokens` (already used by `same_indicator`/`_cluster_key`) plus any
`_taxonomy_subdomain_labels_he`/`_watchlist_hebrew_names` phrase literally present in the text. A
Latin term match stays sufficient alone (unchanged, backward-compatible with every existing
Latin-term test); a Hebrew-only indicator now needs **2+** of its own Hebrew terms to hit the same
item.

**Live-data correction (important):** the naive ">=2 raw content-token hits" version of this rule,
verified directly against the live DB (read-only), badly over-matched — a spot-check of the first
few new "matches" surfaced 3 genuine defects surfaced only by testing against real data, not by
unit tests alone:
- `_content_tokens`'s own stoplist check runs *before* prefix-stripping, so e.g. "הצפויה" strips to
  "צפויה" — a real `_STOP_HE` entry — without ever being excluded (a latent bug in the shared
  function, worked around here rather than fixed in place, since `_content_tokens` is shared with
  `same_indicator`/`_cluster_key`'s own differently-tuned ratio-based tests and out of this round's
  file-ownership scope to touch further than necessary).
- "מערכת"/"מערכות" ("system(s)") is close to the single most common noun in this corpus and matched
  almost everything; several other words (ישראל/ישראלי, אוויר, קרוב, יום, מול, נוכח, month names)
  are similarly domain-ubiquitous.
- A bare 4-digit "2026"-style year alone contributed to at least one confirmed spurious cross-story
  match (an unrelated XTEND/NYSE item vs. an Elbit-Serbia-factory indicator, sharing only "ייצור"
  ("production") + the year).

Fixed with a stricter, count-appropriate filter local to `_extract_indicator_terms` (kept separate
from `_STOP_HE`/`_content_tokens` itself, which stay tuned for their own overlap-*coefficient* use
case): 5+ letters, re-excluded against `_STOP_HE` post-strip, never a bare 1990-2099 year (a
*different* number — amount, quantity, model number — still counts), never one of
`_MATCH_GENERIC_HE`'s domain-ubiquitous words. A taxonomy/watchlist *phrase* match bypasses this
filter entirely (a multi-word named phrase is distinctive by construction).

**Live verification (matched/total, before -> after, `_item_matches_indicator` against real DB rows,
no rebuild):**

| kind | pool used | total open | old (Latin-only) | new (this fix) |
|---|---|---|---|---|
| weekly | last 7 days' red/orange items (39 items — this issue's actual candidate pool) | 15 | 1 | **13** |
| daily | today's red/orange items | 18 | 0 | 0 (0 items published today at verification time — a data-timing gap, not a code defect) |
| daily (proxy) | most recent day with data (2026-09-06, 2 items) | 18 | 0 | **15** |

The daily proxy run's 15/18 matches were individually inspected: both of that day's 2 items are
themselves fresh coverage of the exact two ongoing stories (the Rafael/Volkswagen Germany factory,
and Estonia's David's Sling decision) that dominate the current daily indicator watchlist — genuine,
correctly-firing corroboration, not noise. The weekly run's 2 non-matches were confirmed to
correctly find no real overlap. One residual coincidental-match risk (generic-but-length-passing
words like "ייצור" combined with an unrelated topic) remains possible in principle but is now the
exception, not the default state a naive ">=2" implementation would have shipped.

12 new tests (`TestHebrewIndicatorMatching`) cover: 2-term-match, single-term-insufficient,
no-overlap, Latin-term-still-sufficient (regression guard), the public `extract_key_terms` contract
being unaffected, proper-noun-phrase widening, the two new config-driven helpers'
parenthetical-stripping/Hebrew-filtering directly, generic-domain-words-alone-insufficient, and the
bare-calendar-year exclusion.

#### 2. Product-line report event-kind labels (worst #8, D7)

`eoa.report.product_line.format_events_block`/`events_table` rendered the raw DB `events.kind`
literal untranslated (e.g. "contract_award"), while the same report's `build_docx` call renders its
own built-in events appendix through `docx_builder._EVENT_KIND_LABELS_HE` (Hebrew) — two tables in
the same report, one Hebrew, one English. Added a local `_EVENT_KIND_LABELS_HE_FALLBACK` copy (same
"small local copy over cross-module private-name import" convention already used by
`eoa.report.daily`/`weekly`) and a `_event_kind_label(kind)` helper (`.get(kind, "אחר")` — an
unknown/missing kind falls back to "אחר", never the raw key); both `format_events_block` and
`events_table` now call it. 5 new tests, including one that walks every configured kind and asserts
its label is never equal to the raw key.

#### 3. Tender product-line tagging + `status='unknown'` resolution (worst #6, #10, D9)

**3a. Tagging at intake.** `_insert_tender_and_item` never tagged `product_lines` for a newly
inserted notice — the only tagging paths were the live pipeline hook (`items`/`events` only) and
the one-off `scripts/backfill_product_lines.py` sweep, so any tender inserted after that backfill
(id 42 included) stayed untagged forever. `_insert_tender_and_item` now calls
`eoa.product_lines.tagging.tag_product_lines` at insert time — title + description (`notice.summary`)
+ CPV codes as `text_en`, the LLM's own Hebrew summary as `text_he`, `entities` passed through — and
writes the result into the new `product_lines` INSERT column. 2 new tests cover a real match and the
correctly-untagged (`[]`) case.

**3b. Repair script (`scripts/repair_round9.py`, dry-run default / `--apply`).** Two independent,
idempotent repairs over `tenders`:
- `tag_missing_product_lines`: re-tags every tender whose `product_lines` is still empty (mirrors
  `scripts/backfill_product_lines.py`'s own tenders sweep — title/CPV + summary_he + entities,
  unioned with the linked item's own tags).
- `resolve_unknown_status`: every `status='unknown'` tender (no `deadline` AND no `published_at` at
  all) is resolved via the same relevance gate/learned threshold the W2b feedback loop already uses
  (`eoa.tenders.feedback.get_relevance_threshold`) — `relevance_score >= threshold` -> `'open'`,
  else -> `'archived'` (the same terminal status `_archive_stale_closed` already uses elsewhere in
  this module). Every decision logged (`tender_unknown_status_resolved`, structlog) with the exact
  score/threshold. Never touches `tender_feedback` (standing round-9 rule).

**Dry-run output (live DB, `runtime/eoa.env`, before any write):**
```
product-line tagging: 0 tender(s) would be tagged
status='unknown' resolution: 1 tender(s) would be resolved
  #38     -> archived  (relevance_score=0.50 < learned_threshold=0.60) Counter UAS systems tenders - Unmanned airspace
```
`product_lines` tagging genuinely found 0 rows to tag: every currently-untagged tender (13, 18, 30,
34, 35, 42 — including the round-8-flagged id 42, "Finland – Security cameras – Computer Vision
Technology for Airport Operations") was individually checked against `tag_product_lines` and
against the six configured lines' own keyword/subdomain/exemplar definitions in
`config/product_lines.yaml` (out of this round's file-ownership scope to edit) — none of the six
narrow, frozen product lines (targeting pods, missile-warning EO, LOROP pods, EO air-defense
warning, 16" ball gimbals, border long-range EO surveillance) cover general
airport/perimeter-camera or naval/RPAS EO/IR sensor RFIs. This is an honest, correctly-untagged
result of the deterministic tagger (docs/CONVENTIONS.md rule 6: never invent a signal), not a
tagging-mechanism defect — flagged here as a product-line *coverage* gap for a future round's
`config/product_lines.yaml` owner, not fixed in this one (out of file-ownership scope). Tender 38
was individually inspected before applying the repair: it is not a real single notice at all (a
news-aggregator category-listing page, `summary_he` states as much verbatim, no agency/deadline/
published_at at all) — `archived` is the substantively correct disposition, not just the
mechanically-computed one.

**Applied, then verified from a separate connection:**
```
tender 38: status='archived' (was 'unknown')
tender 42: status='open', product_lines=[] (unchanged -- correctly untagged, see above)
remaining status='unknown' tenders: 0
```
9 new tests (`TestRepairTagMissingProductLines`/`TestRepairResolveUnknownStatus`) cover dry-run vs.
apply, no-match-is-skipped, linked-item-tag union, above/below-threshold, missing-score neutral
default, and no-candidates.

#### 4. Patent-survey appendix reliability for patent-office hosts (worst #9, D8)

Every patent row's "אמינות" column rendered "—" live because both fresh surveys' patents were
exclusively Google-Patents-search-sourced (this codebase's keyless fallback) — never a host also
present in the monitored `sources` table, so the existing host-match lookup legitimately never hit.
A national/international patent office's own record page is itself an official primary source
regardless of whether it also happens to be a monitored news outlet. `_appendix_reliability` now
falls back to `{"kind": "primary", "score": 1.0, "label": "רשומת פטנט רשמית"}` for
patents.google.com / worldwide.espacenet.com / patft.uspto.gov / ppubs.uspto.gov whenever the
`sources`-table host-match lookup found nothing for that host — a real `sources` row still wins
when one exists (verified with a dedicated test: a patents.google.com URL with a low-reliability
`sources` row returns the *secondary* value, not the patent-office override).

**Narrowly-scoped edit to `tests/unit/test_reports_round8.py`:** its own
`test_no_host_match_returns_none` queried a `patents.google.com` URL specifically to prove "no
`sources` match -> None" — exactly the behavior this fix intentionally changes for that host. Updated
the query URL to a host outside both the `sources` table and `_PATENT_OFFICE_HOSTS` (a generic
`example-patent-registry.test` host) so the test still covers the genuine no-match case; no other
line in that file was touched. 6 new tests cover all four patent-office hosts, the still-None
generic-host case, and the real-source-row-wins case.

#### Tests / lint

- `tests/unit/test_reports_round9.py`: 32 new tests, all passing (0 DB/network/LLM).
- `PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_reports_round9.py
  tests/unit/test_reports_round8.py -q -k "indicator or survey or reliability or product_line or
  tender"`: **30 passed**.
- Full regression: `tests/unit/test_reports_round9.py` (32), `tests/unit/test_reports_round8.py`
  (35), `tests/unit/test_report_deltas_round5.py` (6, includes the pre-existing
  `extract_key_terms`/`_item_matches_indicator` tests this round's fix must not break) — **73
  passed** in the combined run.
- `.venv/Scripts/ruff.exe check` / `format --check` on all six changed/added files: clean.
- Live DB verification and repair `--apply` + separate-connection re-verification: see #1 and #3
  above.

#### What remains

- Tender 42 (and 13/18/30/35) are honestly untagged under the current six-line
  `config/product_lines.yaml` — a coverage gap for that config's owner, not a code defect in this
  round's tagging fix (see #3 above).
- The Hebrew indicator-matching fix's remaining false-positive risk (a length-5+, non-generic word
  shared by coincidence across two unrelated stories, e.g. "ייצור") is reduced but not eliminated by
  a purely lexical filter — a future round could tighten further (e.g. corpus-frequency/IDF
  weighting) if live monitoring still shows spurious matches.
- Daily-kind live verification could only use a proxy day (no items had published in the report
  window at verification time) — worth re-checking against a live daily rebuild once fresh items
  land.
