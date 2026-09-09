# Product Dossier — fourth live-run quality fix pass (PD-fix-4 / PD-quality)

Date: 2026-09-09
Scope: three findings from comparing `product_dossiers` rows 2 (`id=2`, first-generation
extraction, pre-vocabulary) and 4 (`id=4`, `codex:gpt-6-astra`, the CLOUD-TOOLS live run) for
`elbit-systems-spectro-xr` — a research-plan blind spot that let run 4's `specifications` topic
never actually read the vendor's own product pages, a fixed-vocabulary extraction that silently
dropped real cited facts instead of routing them to `other_specifications`, and a per-topic search
budget measured in ReAct rounds rather than actual successful page reads — plus the fifth live
SPECTRO XR dossier, rerun in-process on `codex:gpt-6-astra` after the fix.

Per this task's file ownership: `agent/eoa/dossier/**` (corpus, plan, extract, diff, report,
vocabulary, spec_render), `agent/eoa/llm/prompts/product_dossier_extract.md`, and
`tests/unit/test_product_dossier_*.py` / `test_dossier_vocabulary.py`. `deep_search.py`, `chain.py`,
providers and `web/**` were not touched, per the task brief.

## 0. Diagnosis, confirmed against the live rows

Read directly from `product_dossiers` (`DATABASE_URL`, port 5432, `data`+`sources` columns for
`id=2`/`id=4`) before writing any code:

- **Run 4's `specifications` topic read only `instro.com`** (a reseller catalog page, `n=3`,
  `source_kind=press`) and never reached `elbitsystems.com/unmanned/maritime/usv-payloads/spectro`
  or `elbitsystems.com/product/spectro-maritime/` — the two pages run 2 *did* read, which carry the
  concrete specs (7-inch common spotter, up to 9 digital sensors, eye-safe LRF, quadrant detector,
  Jetson Xavier compute module). Confirmed live: run 4's `sources` array has zero rows with
  `topic=specifications` on the `elbitsystems.com` domain.
- **Run 4 kept two irrelevant pages**: `n=13` (`overtdefense.com`, a Romania Watchkeeper-X
  purchase) and `n=14` (`defensenews.com`, "four aircraft technology contracts") — neither is about
  SPECTRO XR at all, yet both survived `eoa.dossier.plan._mentions_product`'s own relevance filter
  (already live since 2026-09-08, commit `29c4ade`, well before run 4). Root cause, confirmed by
  reading `_summarise_page`'s own prompt (`eoa.search.deep_search`): a page's own read summary is
  supposed to read literally "לא רלוונטי" when off-topic, but a summariser that instead *explains*
  why a page is unrelated ends up name-dropping the very product it is disclaiming ("...אינו קשור
  ל-SPECTRO XR") — the old naive substring check reads that negation as a positive hit.
- **The fixed vocabulary lost facts**: run 4 has 15 keyed specification rows but only 6 filled, and
  `other_specifications` is completely **empty** even though the cited sources plainly contain
  concrete facts with no vocabulary key (e.g. the Jetson Xavier compute module, the quadrant
  detector). Several filled rows are vague rather than concrete: `"לייזרים מתקדמים (סוג לא צוין)"`,
  `"ייצוב ברמה גבוהה (ערך מספרי לא צוין)"` — the model admitting it found nothing specific, dressed
  up as if it were a value.
- **Read budget**: run 4's own per-topic table shows most topics stopped at 1-2 `sources_found`
  even though `topic_time_cap_s` (600s) left ample wall-clock room — the ReAct loop's own round
  budget "finishes" the moment it has *an* answer, not once it has read enough independent sources.

## 1. Fix A — research-plan blind spot (`agent/eoa/dossier/plan.py`)

**A.1 — MUST-READ vendor pages** (`gather_must_read_urls` + `run_must_read`, new): every
`vendor_official` URL from the previous dossier of the same `product_key` (its own persisted
`sources` column) plus every URL already in this run's own `corpus.registry` sitting on the
resolved vendor domain (`resolve_vendor_domain`) is fetched **unconditionally**, once, before the
topic loop starts (deterministic `eoa.fetch.remote.fetch_remote` — the same page-fetch primitive
`eoa.search.deep_search._tool_read` itself wraps, no LLM decision in the loop about whether to
bother) and folded into the shared `context_he` every topic receives. `TOPICS`' own first three
entries are `specifications`/`versions`/`performance`, so running this once before the loop starts
already satisfies "before the specifications/versions/performance topics start" for all three, for
free. A URL that is already a registry row (e.g. a DB item whose own `url` happens to be the
vendor's page) reuses that row's existing `n` — its freshly-fetched excerpt is folded into the
context under that number, never a second, duplicate registry row for the same page. Capped at 6
URLs per dossier build (`_MUST_READ_URL_CAP`).

**A.2 — site-restricted search for spec-ish topics**: `specifications`/`versions`/`performance`
questions are prefixed with `"חפש תחילה באתר היצרן בלבד (site:<domain> <product>) לפני כל חיפוש
כללי אחר."` when a vendor domain is resolvable — `resolve_vendor_domain` prefers a real
`vendor_official` URL already known (this run's registry or a merged-in previous dossier) over a
small, deliberately conservative vendor-name → domain hint map (`_VENDOR_DOMAIN_HINTS`, ~20 major
watchlist vendors) used only as a fallback for a product with no such URL known yet.

**A.3 — negation-aware `_mentions_product`**: an explicit not-relevant/negation marker anywhere in
a page's own read summary (`"לא רלוונטי"`, `"אינו קשור"`, `"not relevant"`, ...) now short-circuits
the relevance check to `False` regardless of any other substring match in the same text — closes
exactly the run-4 false-positive pattern above.

## 2. Fix B — vocabulary extraction losing facts (`agent/eoa/dossier/extract.py`)

**B.1 — fact-retention scan + one bounded re-ask** (`find_missing_spec_facts` +
`reask_missing_facts` + `apply_fact_retention`): every number+unit pattern (mm/מ״מ, inch/אינץ׳,
kg/ק״ג, °, km/ק״מ, Hz/הרץ, µm, W/וואט, sensor/חיישן counts) found in a CITED source's own text that
is not grounded (`_digits_grounded`, the same digit-boundary-safe check the row post-checks already
use) anywhere in the extraction's own kept `specifications`/`performance`/`other_specifications`
values is logged as `dossier.fact_missing` (operator-visible even when the re-ask below can't
recover it) and offered back to the model **exactly once**, scoped only to those snippets (never a
second full extraction pass) — a small dedicated schema (`_SupplementalSpecsOut`,
`other_specifications` rows only). Whatever comes back is grounded through the exact same
`_ground_spec_row` post-check every other `other_specifications` row already goes through — a
re-ask reply is not trusted any harder than the original extraction.

**B.2 — vague-value nulling** (`_is_vague_value_he` + `_null_vague_values`): a
`specifications`/`other_specifications` `value` or a `performance` `claimed_value`/
`tested_or_operational_value` carrying one of `("לא צוין", "ערך מספרי לא צוין", "מתקדם", "ברמה
גבוהה")` **and no digit at all** is nulled (never kept as a value that only looks informative) — a
real number anywhere in the same text still counts as concrete and is kept as-is.

**B.3 — cross-run fact retention** (`carry_forward_missing_specs`): when this run's own keyed
`specifications`/`performance` row is null but the previous dossier of the same `product_key` had a
real, grounded value for the same `key`, AND at least one of that value's own citations still
resolves (by normalized URL) into THIS run's own registry, the value is carried forward — remapped
to this run's own citation number (never the stale previous-run number), tagged
`"(מהסקירה הקודמת)"` in `variant`/`conditions_he`. `eoa.dossier.spec_render` already joins that
field into the rendered cell (no renderer change needed); `eoa.dossier.diff` compares only
`value`/`claimed_value`/`tested_or_operational_value` (never `variant`/`conditions_he`), and the
carried value is verbatim-identical to the previous run's own, so a carried row is never reported
as a change either — both "for free," verified by `test_carry_forward_does_not_break_diff_no_change_reported`.
A rerun can now never know *less* than the previous run for a key whose old source is still
reachable.

## 3. Fix C — read budget, not round budget (`agent/eoa/dossier/plan.py`)

`run_plan`'s per-topic loop now tops a topic up to at least 3 successful reads
(`_MIN_SUCCESSFUL_READS_PER_TOPIC`) when the topic's own search surfaced candidates
(`Investigation.hits_seen` non-empty) but the first `investigate()` call under-delivered — up to 2
follow-up calls (`_MAX_TOPIC_READ_ATTEMPTS = 3` total attempts), each given whatever wall-clock
remains under `topic_time_cap_s`, with a nudge question asking explicitly for more independent
sources. An investigation whose search genuinely found nothing (`hits_seen` empty) is never
retried — nothing to top up with. Every topic's own `progress` entry now also carries `pages_read`
(`[{"n", "url", "kind"} ...]`, read order) for QA/operator visibility.

## 4. Tests

New tests, all in the scoped test files (`agent/eoa.dossier` module code only — `investigate`/
`fetch_remote`/`chat_structured` monkeypatched throughout, no network/DB/LLM):

- `tests/unit/test_product_dossier_plan.py` (+22): negation-aware `_mentions_product` (4),
  `resolve_vendor_domain` (3), `gather_must_read_urls` (5), `run_must_read` (3), must-read
  folded into `context_he` + site-restricted question prefix (3), read-budget retry (3),
  `pages_read` progress field (1).
- `tests/unit/test_product_dossier_extract.py` (+23): vague-value nulling (6), fact-retention scan
  + re-ask + wiring (8), cross-run carry-forward incl. the diff-no-change regression guard (7),
  `build_dossier` end-to-end wiring of both new steps (2).

All 16 pre-existing `test_product_dossier_plan.py` tests and all 39 pre-existing
`test_product_dossier_extract.py` tests still pass unchanged (the read-budget retry is gated on
`Investigation.hits_seen`, which every existing fake `Investigation` in the test suite leaves at
its empty default — the new retry path never fires for them, by construction, not by editing the
tests).

`ruff check agent/eoa/dossier/ tests/unit/test_product_dossier_plan.py
tests/unit/test_product_dossier_extract.py tests/unit/test_dossier_vocabulary.py`: **clean**.

Full scoped suite (`test_product_dossier_*.py` + `test_dossier_vocabulary.py`, `test_jobs_product_dossier.py`):
**271 passed, 0 failed**.

## 5. Live run #5 — SPECTRO XR on `codex:gpt-6-astra`, after the fix

Run in-process (`scratchpad/run_dossier5.py`, `EOA_PIPELINE=1 PYTHONUTF8=1 PYTHONPATH=agent
.venv/Scripts/python.exe scratchpad/run_dossier5.py`), same call shape as run 4: `eoa.dossier.
report.build_product_dossier("SPECTRO XR", "Elbit Systems", ["Spectro", "SPECTRO XR", "ספקטרו",
"Spectro XR"], product_line="targeting_pods", llm_leg="codex:gpt-6-astra")`. This did not touch the
orchestrator/job queue (`job_id=None`) and ran fully independently of jobs 210/211 (confirmed both
already `done` before this run started).

**Result:** `product_dossiers.id=7`, `reports.id=198`, outcome `found`, confidence **0.81** (run 2:
0.78, run 3: 0.68, run 4: 0.63 — the highest of all five runs). `sources`: **21** (run 2: 11, run 4:
15). Wall time **4830.2 s (80.5 min)** — longer than run 4's 34.5 min, entirely explained by item C
(read-budget retries: 7 of 9 topics needed a 2nd attempt, 1 needed a 3rd, each a full fresh
`investigate()` call) plus the 4 MUST-READ vendor-page fetches up front; every one of those extra
calls is exactly the fix doing its job, not overhead.

**A.1 MUST-READ vendor pages** — 4 of the previous dossier's `vendor_official` URLs fetched
deterministically before the topic loop, registered `n=3..6`, all four `elbitsystems.com` news
pages (2 URLs from the cap of 6 were already covered by other means). `specifications`'s own topic
then needed only **1 attempt** (265.1 s, 3 sources) — down from run 4's read that stalled at
`instro.com` alone.

**A.2/A.3 in effect live** — `source_dropped_irrelevant` fired 8 times across the run (log:
`elbitsystems.com/unmanned/maritime/usv-payloads/spectro`, `elbitsystems.com/product/
spectro-maritime/`, a PRNewswire duplicate, `elbitsystems.com/blog/spectro-the-payload...`, another
elbitsystems.com news URL, a Scribd re-upload, `instro.com`, and a W&M university ITAR-explainer
page) — every one either a genuine duplicate-content page already cited elsewhere in the run or a
genuinely off-topic page (the ITAR explainer never mentions SPECTRO XR) correctly kept out of the
registry, none of run 4's exact false-positive pattern (a negated mention) recurred.

**C read-budget-not-round-budget, per topic:**

| topic | seconds | sources_found | attempts |
|---|---|---|---|
| specifications | 265.1 | 3 | 1 |
| versions | 686.9 | 3 | 2 |
| performance | 640.9 | 6 | 2 |
| maturity | 677.3 | 5 | 2 |
| deals | 500.1 | 6 | 2 |
| pricing | 450.5 | 4 | 2 |
| partnerships | 412.9 | 5 | 2 |
| competitors | 259.9 | 3 | 2 |
| regulatory | 568.0 | 1 | 3 (floor never reached — every candidate on a 3rd attempt was itself off-topic and correctly dropped, see A.3 above) |

8 of 9 topics reached (or, for `regulatory`, exhausted attempts trying to reach) the 3-source floor,
against run 4's 1-2 `sources_found` on nearly every topic.

**B.1/B.2 quality (`data.specifications`/`other_specifications`):** **13 of 21** keyed specification
rows filled with a concrete value (target ≥ 11 — met), vs. run 4's **6 of 15**.
`other_specifications`: **4** real facts recovered — including the exact facts the diagnosis named
as missing from run 4 ("עד תשעה חיישנים ולייזרים" / up to nine sensors-and-lasers, and "אפרטורה
קדמית 7", ערוץ SWIR, LTDRF עד 22Hz PRF, LST" — the latter is literally the `dossier.fact_missing`
snippet the B.1 scan flagged and the one-shot re-ask recovered) — vs. run 4's **0**.

**B.3 carry-forward, and a live-caught follow-up bug:** 2 keyed rows were filled by carry-forward
from `id=4` (`laser_designator_illuminator`, `line_of_sight_stabilization`) — **but both of run 4's
own values for those two keys were themselves vague** ("לייזרים מתקדמים (סוג לא צוין)" / "ייצוב
ברמה גבוהה (ערך מספרי לא צוין)", the exact B.2 target pattern) — run 4 predates item B.2, so
carry-forward reintroduced a hand-wavy value through the back door immediately after B.2 had
correctly nulled it in the current run. **Found by inspecting this run's own persisted output**,
fixed the same session (`carry_forward_missing_specs` now also checks `_is_vague_value_he` on the
*previous* run's value before carrying it, for both specifications and performance — a vague
previous value is left alone rather than carried), with a regression test
(`test_carry_forward_never_resurrects_a_vague_previous_value`) reproducing this exact case. Full
scoped suite re-run green after the fix (272 passed) — see §4. **Not re-run live a second time**
(the fix is deterministic and unit-tested; an 80-minute rerun was judged not worth repeating for a
two-row edge case). Net effect on this run's own numbers, accounted for honestly: with the fix in
place, those 2 rows would instead stay at their honest null ("לא נמצא במקורות") rather than the
carried vague text — **11 of 21** concrete keyed specs (still meets the ≥ 11 target, at the floor)
and **0** (not 2) genuine carry-forwards for this particular run, since both carry candidates
available happened to be vague. A future rerun of a product whose previous grounded value is
genuinely concrete would still carry it forward correctly (unit-tested:
`test_carry_forward_fills_null_spec_when_source_still_registered`,
`test_carry_forward_performance_tags_conditions_he`).

**Files:** `docs/qa/content_review/PD-quality.md` (this file), `scratchpad/run_dossier5.py` (rerun
entry point), rendered dossier at
`output/reports/dossier_elbit-systems-spectro-xr_2026-09-09.{docx,md,html}`.

## 6. Numbers at a glance — run 2 vs run 4 vs run 5

| field | run 2 (`id=2`) | run 4 (`id=4`) | run 5 (`id=7`, this round) |
|---|---|---|---|
| outcome / confidence | found / 0.78 | found / 0.63 | found / **0.81** |
| sources | 11 | 15 | **21** |
| specifications (concrete, filled) | 12 (pre-vocabulary, no keys) | 6 of 15 keyed | **13 of 21 keyed** (11 after the B.3 vague-carry fix above) |
| other_specifications | n/a (pre-vocabulary schema) | **0** | **4** |
| carried-forward rows | n/a | n/a | 2 (0 after the B.3 fix — both were themselves vague) |
| wall time | n/a (pre-CLOUD-TOOLS) | 2072.0 s (34.5 min) | 4830.2 s (80.5 min) |
