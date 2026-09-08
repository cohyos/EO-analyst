# TENDERS-SAM: SAM.gov ingestion, relevance scoring, and dossier corpus matching

Date: 2026-09-08
Scope: three findings from the 2026-09-08 review --
(a) reports/dossiers reference irrelevant tenders,
(b) no SAM.gov tender search is visible even though `SAM_GOV_API_KEY` has been set since
2026-09-07 evening,
(c) the SPECTRO XR product dossier's `tenders_and_forecasts` is empty.

Files touched: `config/tenders.yaml`, `agent/eoa/tenders/scan.py`, `agent/eoa/dossier/corpus.py`,
`scripts/rescore_tenders_relevance.py` (new), `tests/unit/test_tenders_scan.py`,
`tests/unit/test_product_dossier_corpus.py`.

---

## 1. SAM.gov: why it never ran, and the two independent bugs found

### 1a. It never actually ran since the key was added

Read every `runtime/logs/orchestrator.*.log` for a `tender_scan_done` event:

| Log file | tender_scan_done events |
|---|---|
| `orchestrator.2026-09-05.log` | 0 |
| `orchestrator.2026-09-06.log` | **1**, at `2026-09-06T22:09:51Z` (`daily_run` job 126) -- `sources_scanned=30, notices_fetched=704, matched=9, inserted=8` |
| `orchestrator.2026-09-07.log` | 0 (only `ingest`/`deep_search` jobs ran; no `daily_run`/`weekly_run` job appears in the whole file -- the log ends at `21:00:58Z`) |
| `orchestrator.2026-09-08.log` | 0 (orchestrator just (re)started at `17:39:18Z`; only `worker_start`/`orchestrator_started`/`ntfy_sent` logged, no job claimed yet in this file) |

`SAM_GOV_API_KEY` was set 2026-09-07 evening, but the only `tender_scan` that has ever run in the
captured logs was on **2026-09-06**, *before* the key existed. No `daily_run`/`weekly_run` job
(the only job kinds that invoke `scan_tenders`) has completed since then in any log this repo
still has -- so SAM.gov's own code path was never exercised live even once, key or no key. This is
finding (b)'s primary explanation, and it is independent of the two bugs below (both of which
would have kept it silently empty even if a scan *had* run).

### 1b. Bug 1 -- the query itself: `keyword` is not a documented SAM.gov v2 parameter

The pre-fix `config/tenders.yaml` sent `keyword={term}` to
`https://api.sam.gov/opportunities/v2/search`. Live-probed 2026-09-08 with a real
`SAM_GOV_API_KEY`:

```
GET .../opportunities/v2/search?api_key=...&keyword=infrared        -> HTTP 400
"postedFrom and postedTo are required fields"
```

`keyword` is silently ignored (not a documented v2 parameter at all -- confirmed via the official
API reference, `open.gsa.gov/api/get-opportunities-public-api/`); the real culprit that always
would have 400'd is the missing **mandatory** `postedFrom`/`postedTo` date range (`MM/dd/yyyy`,
both required). The real full-text-search parameter is **`title`** (title-only substring/phrase
match). Live-probed with the fix:

```
GET .../opportunities/v2/search?api_key=...&title=infrared&postedFrom=08/10/2026&postedTo=09/08/2026&limit=10
-> HTTP 200, totalRecords=23
```

5 sample titles from that response (all genuine EO/IR defence notices):
1. "Infrared Imagers Justification" (NAICS 334511, PSC 5855)
2. "Advanced Threat Warning System (ATWS) and Common Infrared Countermeasure (CIRCM) Development and Support"
3. "CH-53K Electrical Optical (EO) / Infrared (IR) Sensor System Competition"
4. "66--CAMERA,INFRARED,IND"
5. "14--TRACKER,INFRARED,GU"

`title=electro-optical` (the hyphenated compound that happens to be the vocabulary's first entry)
returned **0** in-window results -- the title-search field doesn't literally contain that
hyphenated compound in any current notice, which is why `sam_gov_api` now gets its own
`api_query_keywords` override (`["infrared", "thermal imaging", "night vision", "FLIR", "gimbal"]`)
instead of silently always rotating the vocabulary's first `MAX_KEYWORDS_PER_API_SOURCE=5` entries
(all electro-optical/EO-IR hyphenated variants). `title=thermal imaging` -> 8 results (e.g. "FLIR
RECON V Thermal Imaging Device Repair", "Thermal Imaging Binoculars"); `title=night vision` -> 7
(e.g. "Night Vision Devices for Foreign Military Sales (FMS)").

A second query strategy, `sam_gov_api_psc` (new source), uses the `ccode` (PSC classification)
parameter instead of a title keyword, with `api_query_keywords: ["1240", "5855"]`:

```
GET .../opportunities/v2/search?api_key=...&ccode=1240&postedFrom=...&postedTo=... -> HTTP 200, totalRecords=13
GET .../opportunities/v2/search?api_key=...&ccode=5855&postedFrom=...&postedTo=... -> HTTP 200, totalRecords=19
```

`ccode=1240` ("Optical Sighting and Ranging Equipment") sample titles: "Night Vision Devices for
Foreign Military Sales (FMS)", "M68 Optic Close Combat Sight Reflex", "Periscope, Armored
Vehicle". `ccode=5855` ("Night Vision Equipment, Emitted and Reflected Radiation") sample titles:
"Infrared Imagers Justification", "VIEWER,NIGHT VISION, IN REPAIR/MODIFICATION OF", "Light
Interference Filter (LIF) -- Enhanced Night Vision Goggles". `ccode=5840` ("Radar Equipment,
Except Airborne") -- suggested alongside 1240/5855 as a plausible EO/IR-adjacent PSC code -- was
also probed (`totalRecords=28`) and found to return radar/acoustic/ultrasonic notices with **no**
EO/IR content ("Acoustic Projector", "Echodyne EchoShield Radar System", "Speed Trailers"): radar
is a distinct sensing modality, not electro-optical, so it was deliberately excluded rather than
included on the strength of its name alone. `ncode=334220` (a NAICS code named in the brief) was
also probed (`totalRecords=259`, mostly antennas/switches/RF hardware, only 1-2 in 10 arguably
EO/IR-relevant) and excluded for the same reason -- too noisy relative to the title/PSC strategies
above.

### 1c. Bug 2 -- the parser: `parse_hints.format: json` silently resolved to nothing

Independent of the query fix, `eoa.tenders.scan._fetch_api_json`'s parser lookup is:

```python
parser = _API_PARSERS.get(src.id)                                    # sam_gov_api: not present
parser = _GENERIC_API_PARSERS.get((src.parse_hints or {}).get("format", ""))  # "json" not a key!
page_notices = parser(data, src) if parser else []                   # always []
```

`_GENERIC_API_PARSERS` is keyed `"ocds"`/`"json_list"` only -- `sam_gov_api` had
`parse_hints: {format: json, ...}` (note: `json`, not `json_list`), which matches neither dict.
**Every response, even a real HTTP 200 with real data, would have parsed to zero notices** --
this bug alone would have kept SAM.gov silently empty forever, completely independent of the query
parameters or whether a scan ever ran. Fixed to `format: json_list` with the real v2 response
field names (all confirmed live 2026-09-08 against the actual JSON payload):
`noticeId`/`title`/`postedDate` (YYYY-MM-DD)/`responseDeadLine`/`fullParentPathName` (full agency
hierarchy)/`uiLink`/`description` (a link to the notice-description endpoint, used as the summary
field)/`naicsCode` (feeds `cpv_naics` for the existing CPV-family pre-filter, even though these are
NAICS/PSC codes rather than CPV codes -- deliberately left permissive for SAM.gov's own code
families, see the YAML comment; the two-signal gate plus the new negative-keyword/defence-context
check do the real filtering for this source).

### 1d. Fix applied

`config/tenders.yaml`'s `sam_gov_api` entry: `query_params` now sends `title`/`postedFrom`/
`postedTo` (never `keyword`); `parse_hints.format: json_list` with the correct field map;
`api_query_keywords` set to 5 confirmed-good title terms; `query_lookback_days: 29`;
`pace_seconds: 6.0` (see 1e below). A new `sam_gov_api_psc` entry added alongside it, using
`ccode` + `api_query_keywords: ["1240", "5855"]`. `eoa.tenders.scan._fetch_api_json` gained two new
placeholders, `{since_date_us}`/`{today_us}` (MM/dd/yyyy, alongside the pre-existing YYYYMMDD
`{since_date}` TED uses), substituted in both the `query_template` (JSON-body) and `query_params`
(querystring) branches.

### 1e. Live scan attempt -- rate-limited by this session's own diagnostic probing

Per the task brief, a live scan restricted to `sam_gov_api`/`sam_gov_api_psc` was attempted via
`scan_tenders(since_days=29, sources=[...])`. Diagnosing the two bugs above required ~20 live curl
probes against `api.sam.gov` within roughly an hour (title=infrared, title=electro-optical,
ccode=1240/5840/1240, ncode=334220, title="thermal imaging"/"night vision"/"gimbal"/"laser
rangefinder"/"counter-UAS"/"targeting pod"/"hyperspectral", ...). The last several of those probes
already came back `HTTP 429`, and every subsequent attempt at the actual pipeline-level
`scan_tenders()` run -- including a maximally minimal single-keyword, single-source, single-request
attempt -- also came back `429 Too Many Requests`, retried 2x with exponential backoff
(`eoa.tenders.scan._call_with_rate_limit_backoff`, 6s/12s) and still failing; a final bare `curl`
sanity check ~10 minutes after the diagnostic probing also still returned `429`. SAM.gov does not
expose rate-limit headers on either the 200 or 429 responses, so the exact quota/window size and
reset time are unknown from the outside; it is clearly longer than the few-minute gaps available
within this session.

**Evidence for "how many notices it ingested and 5 sample titles" is therefore the raw,
successful (HTTP 200) API probes in 1b/1c above** (23/13/19 total records respectively, with
sample titles), not a completed `scan_tenders()` DB-insert run -- the config/parser fix itself is
independently verified correct (real 200 responses, real parseable JSON, field names matched
against the actual payload) and covered by unit tests (`TestSamGovApiConfig`,
`TestFetchApiJsonUsDateParamSubstitution` in `tests/unit/test_tenders_scan.py`) that exercise
`_fetch_api_json`'s placeholder substitution and the parser-resolution path directly, without
touching the network. **Recommendation**: the next regularly-scheduled `daily_run`/`weekly_run`
(which paces at 6s between the 5+2=7 configured keyword/PSC-code requests, nothing like this
session's rapid-fire diagnostic probing) should clear SAM.gov's cooldown and ingest normally; no
further manual live-scan attempt was made in this session to avoid extending whatever cooldown
window is in effect.

---

## 2. Relevance scoring -- why irrelevant tenders could reach a report/dossier, and the fix

### 2a. Mechanism before this fix

`eoa.tenders.scan`'s two-signal gate (`_passes_gate`: a DOMAIN term (EO/IR vocabulary) AND a
PROCUREMENT signal ("tender"/"RFI"/... or an implicit pass for a structured `api_json` source))
plus the CPV-family pre-filter (`_gate_reject_reason`/`_cpv_gate_reject_reason`) are the only hard
rejections (W2b, "open intake" -- see `eoa.tenders.scan` module docstring, a deliberate
2026-09-06 user requirement: never reject outright on a relevance judgment, only ever demote
`relevance_score`/`intake`). Neither of those two checks requires any *defence/security context*
at all -- a notice whose title says "Infrared spectroscopy analyzer for university laboratory RFQ"
clears both signals (DOMAIN: "infrared"; PROCUREMENT: "RFQ") with zero military/defence context
anywhere in it. The daily/weekly report (`eoa.tenders.report_section.collect_tenders`) and the
dossier corpus (`eoa.dossier.corpus.collect_tenders`, before this fix) both trusted
`intake='accepted'`/no filter respectively, so a false positive of this shape, once it also cleared
the self-tuning relevance threshold (LLM relevance/10, or the neutral 0.5 default when the LLM was
unavailable/deferred), could show up in a report table or be cited in a dossier.

### 2b. Fix -- deterministic negative-keyword / defence-context demotion (never a hard reject)

Two new top-level `config/tenders.yaml` lists: `negative_keywords` (28 off-topic-domain terms --
spectroscopy, laboratory reagent, veterinary, office supplies, janitorial, recruitment services,
...) and `defence_context_signals` (24 broad military/security terms -- military, defense/defence,
army/navy/air force, DoD, NATO, MOD, DAPA, ...). New functions in `eoa.tenders.scan`:

- `_has_defence_context(notice, signals)` -- True if any defence-context term is present in
  title+summary+**agency** (so a buyer name like "DEPARTMENT OF THE NAVY" counts even if the title
  text itself is generic).
- `_negative_keyword_penalty(notice, negative_keywords, defence_context_signals)` -- returns the
  first matched negative term when one is present AND `_has_defence_context` is False for the same
  text; `None` otherwise (no negative term, or a negative term but with a defence-context term also
  present -- e.g. "military infrared spectroscopy sensor for battlefield chemical detection" is
  **not** demoted).
- `_relevance_score_for(extract, notice=None, negative_keywords=None, defence_context_signals=None)`
  -- when `notice` is supplied (the live `scan_tenders` call site; every pre-existing caller/test
  passing only `extract` is unaffected, back-compat verified by
  `test_notice_none_is_backward_compatible`), the score is additionally capped at
  `_NEGATIVE_KEYWORD_RELEVANCE_CEILING = 0.2` when the penalty fires -- well below both the default
  0.6 threshold and the self-tuned floor of 0.3, so a demoted notice can never be accidentally
  "accepted". This can only ever *lower* the score, never raise it, and never touches
  `_gate_reject_reason` -- the notice is still stored, still visible to an operator's own 👍/👎, per
  W2b's "the system tunes itself through feedback" design.

### 2c. `eoa.dossier.corpus.collect_tenders` -- now requires `intake='accepted'`

Before this fix, the dossier corpus's own `collect_tenders` had **no** intake/relevance filter at
all -- any tender row whose title/summary ILIKE-matched a product alias, including a
`'candidate'`/negative-keyword-demoted row, could be cited in a product dossier. Now:
`WHERE intake = 'accepted' AND (...)`, matching the report's own philosophy
(`eoa.tenders.report_section.collect_tenders`'s `"status = 'open' AND intake = 'accepted'"`).
Covered by `test_collect_tenders_query_restricts_to_accepted_intake`/
`test_collect_tenders_returns_empty_when_only_candidate_rows_exist` in
`tests/unit/test_product_dossier_corpus.py`.

### 2d. Re-scoring the existing tenders table

New script `scripts/rescore_tenders_relevance.py` (`--dry-run` default, `--apply` to write; a JSON
backup of every affected row's *before* state is written to `runtime/backups/` before any `--apply`
write). Reuses new `eoa.tenders.scan.find_relevance_demotions`/`repair_relevance_scores` (same
`find_*`/`repair_*` shape as the pre-existing `find_prefilter_violations`/
`repair_relevance_prefilter`) -- scoped to every row except `intake='rejected-by-user'` (an
explicit operator decision this must never override), and only ever a demotion (never re-promotes
a `'candidate'` to `'accepted'`).

**Before/after, run against the live `tenders` table (15 rows) on 2026-09-08:**

```
$ PYTHONPATH=agent PYTHONUTF8=1 python scripts/rescore_tenders_relevance.py
{
  "mode": "dry_run",
  "demotions_found": 0,
  "demotions": []
}
```

**0 of the current 15 rows trigger the new negative-keyword demotion** -- none of them happen to
contain an off-topic term like "spectroscopy"/"laboratory"/"office supplies" (the mechanism is
forward-looking, targeting exactly the failure shape described in the task brief and covered by
direct unit tests -- `TestNegativeKeywordPenalty`/`TestRelevanceScoreForNegativeKeywordCap` in
`tests/unit/test_tenders_scan.py` -- rather than a pattern already present in this small,
already-curated 15-row table). The *pre-existing* CPV/keyword-gate prefilter
(`find_prefilter_violations`/`repair_relevance_prefilter`, from an earlier round) caught a
different, already-known false positive still sitting in the table -- id 34, "Expert / Coach
Transformatie en Contracten Juridisch" (Rotterdam legal/HR consulting) -- and id 35 (a general
Northrop Grumman product page with no procurement signal). Both were dry-run confirmed and then
applied (`--apply`), archiving them (`status='archived'`; F1: never deleted outright):

```
$ python scripts/repair_round7_tenders.py --apply
{
  "mode": "apply",
  "violations_found": 2,
  "violations": [
    {"id": 34, "source": "nl_tenderned", "title": "Expert / Coach Transformatie en Contracten Juridisch - cluster SO", "reason": "keyword_gate_no_domain_term"},
    {"id": 35, "source": "us_defense_innovation_search", "title": "Electro Optical and Infrared Sensors | Northrop Grumman", "reason": "keyword_gate_no_procurement_signal"}
  ]
}
```

Neither id 34 nor id 35 was ever `intake='accepted'` (both were already `'candidate'`), so item
2c's `collect_tenders` fix alone already excluded them from any dossier citation; the archiving is
additional, independent cleanup (removes them from any other view that lists by `status` without
checking `intake`). **Before: 5 `intake='accepted'` rows (ids 13, 15, 18, 20, 30), all genuinely
EO/IR tender notices (RF/optronics sensors, targeting pods, EO/IR RFIs) -- after: unchanged (still
5)**, since the archived rows were never accepted in the first place; **`status='open'` count:
before 1 (id 42, a Finland "Security cameras -- Computer Vision Technology for Airport Operations"
CPV-classified notice, `intake='candidate'` so it never actually reached a report either), after:
unchanged (still 1, still `candidate`)**.

---

## 3. Dossier corpus tender matching (item 3) -- already fixed, plus the new relevance threshold

The whole-word alias-matching rule (a >=8-char alias, or the product name itself, must match as a
`\b`-delimited whole word; a short alias only counts together with the vendor name) was already
implemented for all five collectors including `collect_tenders`, per `docs/qa/content_review/
PD-fix.md` item 1 (same-day fix, 2026-09-08) -- `eoa.dossier.corpus._matches_product_precisely`/
`_filter_precise`. This round's item 3 work is additive on top of that: the `intake='accepted'`
relevance-threshold filter from section 2c above.

### SPECTRO XR dossier (`product_dossiers` id 1/2) -- why `tenders_and_forecasts` is empty

Investigated directly against the live DB (not a bug):

```python
>>> from eoa.dossier.corpus import collect_tenders
>>> collect_tenders(["SPECTRO XR", "Spectro", "Spectro XR"], product_name="SPECTRO XR",
...                  vendor="Elbit Systems", aliases=["Spectro", "SPECTRO XR", "Spectro XR"])
[]  # 0 matches
```

None of the 15 (now 13 non-archived) rows currently in the `tenders` table mention "SPECTRO XR" or
"Spectro" anywhere in their title/summary -- the existing tenders are generic EO/IR notices
(targeting pods, CH-53K EO/IR sensors, RF/optronics RFIs) rather than anything naming this specific
Elbit product. **The corpus stage is matching correctly and returning an honestly empty result**;
it is not silently dropping a real match. This will resolve itself once a SAM.gov (or any other
source) scan ingests a notice that actually names SPECTRO XR or a close variant -- worth revisiting
once section 1's SAM.gov fix has had a chance to run past its current rate-limit cooldown.

---

## 4. Tests / lint

- `tests/unit/test_tenders_scan.py`: 130 tests pass (19 new -- `TestSamGovApiConfig` (5),
  `TestFetchApiJsonUsDateParamSubstitution` (2), `TestLoadNegativeKeywordsAndDefenceContextSignals`
  (2), `TestHasDefenceContext` (3), `TestNegativeKeywordPenalty` (3),
  `TestRelevanceScoreForNegativeKeywordCap` (4)).
- `tests/unit/test_product_dossier_corpus.py`: 17 tests pass (2 new).
- Related suites also green: `test_tenders_forecast.py`, `test_tenders_report_section.py`,
  `test_bd_tenders_round3.py`, `test_tenders_round7.py`, `test_tenders_round7b.py`,
  `test_purge_stale_tenders.py`, `test_api_tenders_service.py` -- 260 tests, no regressions.
- `ruff check agent/eoa/tenders/scan.py agent/eoa/dossier/corpus.py scripts/rescore_tenders_relevance.py
  tests/unit/test_tenders_scan.py tests/unit/test_product_dossier_corpus.py` -- clean.

Total: 390 tests passing across the touched/adjacent tenders+dossier-corpus suites.
