# LESSONS-1: datasheet hunt, programme deals, competitor sets, gap tracking, multilingual (research side)

Date: 2026-09-09
Scope: the five research-side (שלב 1) items from `docs/qa/content_review/LESSONS-fable-dossier.md`
("what is adopted", items 1-2 and 4-5 of that doc's "מה מאומץ" list, plus the item-3 competitor-set
half that belongs on this lane). Files owned/touched by this pass: `agent/eoa/dossier/corpus.py`,
`agent/eoa/dossier/plan.py`, `agent/eoa/dossier/datasheet.py` (new), `agent/eoa/dossier/programs.py`
(new), `agent/eoa/dossier/gaps.py` (new), `agent/eoa/search/pdf_reader.py` (new),
`config/product_lines.yaml`, `config/config.yaml` (one number: `dossier.max_topics`),
`agent/eoa/config.py` (`DossierCfg.max_topics` default), `pyproject.toml` (+`pypdf`), and the
matching test files. **Not touched**: `agent/eoa/dossier/extract.py`, `report.py`,
`spec_render.py`, the schema (`agent/eoa/llm/schemas/product_dossier.py`), prompts,
`agent/eoa/search/deep_search.py`, `chain.py`, `web/**` — those are LESSONS-2's own lane. This doc
is the handoff: what this lane produced, and the exact interface LESSONS-2 consumes it through.

## 0. Where this plugs into the existing pipeline

Nothing here changes the call shape of `eoa.dossier.report.build_product_dossier` (the file that
owns that entry point wasn't touched). Every new capability is wired into the two stages this lane
already owned — `eoa.dossier.corpus.build_corpus` (unchanged signature) and
`eoa.dossier.plan.run_plan` (unchanged signature) — and surfaces its output as new fields on
`CorpusResult` (`agent/eoa/dossier/corpus.py`), which `eoa.dossier.extract.build_dossier` already
receives as its `corpus` argument. LESSONS-2 reads these fields; nothing new needs to be threaded
through `build_product_dossier`'s own call signature.

## 1. Datasheet hunt (`eoa.dossier.datasheet.hunt_datasheets`)

**What it does**: for a product, searches the vendor's own domain (when resolvable, via
`eoa.dossier.plan.resolve_vendor_domain` — a real `vendor_official` URL already known, or the
static hint map) plus a small curated set of EO/IR catalogue sites (`instro.com`,
`army-technology.com`, `naval-technology.com`, `airforce-technology.com`, `defense-update.com`) for
a brochure/datasheet/spec-sheet PDF ("`<product> datasheet`", "`<product> brochure pdf`",
"`site:<domain> <product>`"), ranks candidates (a PDF that also reads as a datasheet/brochure
first, then any PDF, then a datasheet-ish non-PDF page, then everything else — vendor-domain and
known-catalogue hits ranked ahead of a plain hit within each tier), downloads the top few PDFs
(SSRF-guarded exactly like the existing page reader — `eoa.search.pdf_reader.fetch_pdf`, byte-capped
at 20 MB, page-capped at 60, text-capped at 120k chars) and extracts their text with `pypdf`. If
**no** PDF could be downloaded at all, falls back to reading the top 2 non-PDF candidate pages as
plain HTML (`eoa.fetch.remote.fetch_remote`) — the task's own explicit acceptance case ("the
elbitsystems.com product page + instro.com page" as an accepted alternative to a literal PDF).

**Wiring**: `eoa.dossier.plan.run_plan` calls `hunt_datasheets(corpus.product_name, corpus.vendor,
corpus.aliases, vendor_domain=vendor_domain)` once, before the topic loop starts — same placement
discipline as the existing MUST-READ vendor-page block (PD-fix-4 item A.1), so the found text is
already sitting in `context_he` before the `specifications`/`versions`/`performance` topics run,
per the task brief's "the must-read step ... reads them before the spec topics." Each result is
registered as a `corpus.registry` row: `kind="web"`, `source_kind="datasheet"`,
`reliability="primary"` — the exact citable-source shape every other registry row already has, so
LESSONS-2's citation/grounding logic in `extract.py` needs no special-casing for it, just a new
`source_kind` value to recognize.

**Interface — `CorpusResult.datasheets: list[dict]`**, one entry per source actually read:
```python
{"url": str, "title": str, "text": str, "pages": int | None, "kind": "pdf" | "page", "n": int}
```
`n` is the citation registry number this source was assigned (same row also lives in
`corpus.registry`, so `n` is redundant with a registry lookup by `url` but saved here for
convenience). `pages` is `None` for a `kind="page"` (HTML fallback) entry.

**Proof target** (task brief): the 2023 Elbit brochure (or the elbitsystems.com product page +
instro.com page) found, with `"InSb"` or `"1280"` in its text — see the live-run section below.

## 2. Programme deals (`eoa.dossier.programs`)

**What it does**: `identify_platforms(texts, limit=6)` scans free text (the new
`"platforms_and_programmes"` topic's own `answer_he`/`key_facts`, plus the corpus's own item/event
titles) for a whole-word/phrase match against `KNOWN_PLATFORMS` — a curated list of real UAV/aircraft
platforms (Hermes 900/StarLiner, Watchkeeper X, IAR 330 Puma, Heron TP, F-16/F-35, etc.). For each
identified platform (capped at `MAX_PROGRAMME_PLATFORMS = 4`), `eoa.dossier.plan.run_plan` runs one
dedicated `investigate()` call (`platform_deal_question_he`) asking specifically about deals/
contracts for that platform, then `parse_programme_deals` turns the result's `answer_he`/`key_facts`
into structured rows via deterministic regex (money amount + scale + currency, in both English
"$72 million" and Hebrew "72 מיליון דולר" orderings; a best-effort textual date hint; a customer
country match against both the English and Hebrew country name).

**The relevance-filter fix** (the task's own "a page about a carrier platform is never dropped"):
`eoa.dossier.plan._process_topic_reads`/`_mentions_product` now accept `extra_relevance_terms` — for
a programme-deal-topic's own reads, the platform name itself counts as relevance, so a page that
never names the product at all (only the platform) is still kept, flagged
`component_of_package: True` on its own registry row. This is the direct fix for
LESSONS-fable-dossier finding 2 (a Romania Watchkeeper-X purchase page thrown away for never naming
SPECTRO XR).

**Interface — `CorpusResult.programme_deals: list[dict]`**, one row per platform+monetary-sentence
found:
```python
{
    "platform": str, "customer": str | None, "date": str | None,
    "amount_text": str, "amount_value": float | None, "currency": str | None,
    "cites": list[int], "note_he": str, "component_of_package": True,
}
```
A finding with several distinct figures (a framework value AND a follow-on order — the task's own
Romania Watchkeeper X example: ~410M$ framework + ~180M$ order) yields one row per sentence that
actually carries a monetary figure, never merged into one.

**Multilingual (item 5)**: once a customer country is detected from the programme-deal search
(`detect_customer_countries` + `extra_langs_for_countries`, e.g. Romania → `"ro"`), every
`investigate()` call for the REMAINING topics in the same `run_plan` run requests that language too
(alongside `deep_search`'s own configured primary languages, which always include Hebrew) —
`eoa.search.deep_search.investigate`'s existing `langs` parameter, no change to that function
needed.

**Proof target**: Romania Watchkeeper X (framework ~410M$ Dec 2022; order ~180M$ Jun 2023) and the
Hermes 900 72M$ (Nov 2022) deal found for SPECTRO XR — see the live-run section below.

## 3. Competitor sets (`config/product_lines.yaml` + `eoa.dossier.plan._resolve_competitor_seeds`)

**Deliberately a new key, `competitor_products`, not a change to `competitors`**: the existing
`competitors` field (plain company-name strings) is already consumed by
`eoa.product_lines.tagging`/`eoa.product_lines.registry` (not owned by this lane) with a
company-name-only shape and a specific "never enough on its own, only with a subdomain match too"
semantic — changing its shape would have broken that consumer. `competitor_products` is additive:
`[{name, vendor}, ...]`, 4-8 real named products per line, populated for all six lines (`targeting_
pods`: Litening 5/Sniper ATP/Talios/ASELPOD/ATLIS; `mws_eo`; `lorop_pods`; `eo_air_defense_warning`;
`ball_gimbals_16in`: WESCAM MX-15/MX-20/Star SAFIRE 380X/Euroflir 410/Toplite EOS/ARGOS-II/MOSP
3000; `border_long_range_eo`, which deliberately EXCLUDES SPECTRO XR itself from its own
competitor list, since it's one of that line's own products, not a competitor of itself). A new
top-level `vendor_domains: {vendor: domain}` map covers every vendor named in both fields.

**Wiring**: `run_plan` resolves `corpus.product_line`'s own `competitor_products` (reading
`settings().product_lines` directly, the same raw-YAML-dict path `eoa.product_lines.registry`
itself reads through) and appends the named products explicitly into the `"competitors"` topic's
own question — "בדוק במפורש מול המתחרים הידועים הבאים בשוק: Litening 5 (Rafael); Sniper ATP
(Lockheed Martin); ...".

**Interface — `CorpusResult.competitor_seeds: list[dict]`**: `[{"name": str, "vendor": str |
None}, ...]` — the exact list the topic's question was pointed at, for LESSONS-2's extraction stage
to compare the dossier's own found competitors against (e.g. flag a named seed that never showed up
in the extracted `competitors` list as a possible research gap, or cross-check a found competitor
against this list for a `vendor` value the source page didn't state).

## 4. Gap tracking (`eoa.dossier.gaps`)

**Scope note, read this first**: this module can only ever determine **closed**/**open** status for
a gap that already existed in the previous dossier of the same product — classifying a gap as
**new** requires this run's own FINAL `risks_and_gaps_he` list, which only exists after
`eoa.dossier.extract.build_dossier` runs (LESSONS-2's own stage, downstream of everything this lane
touches). `eoa.dossier.gaps.diff_new_gaps(previous_gaps, current_gaps)` is written and unit-tested,
ready for LESSONS-2 to call once it has that final list, and to merge into
`CorpusResult.gap_status` before persisting.

**What runs today**: `extract_gaps_from_previous(corpus.previous)` reads the previous
`product_dossiers` row of the same `product_key` (already available on `CorpusResult.previous`, no
extra query) — in priority order: (1) an explicit `data.meta.gaps` list (forward-compatible; empty
today until LESSONS-2 wires the write — see below), (2) `data.risks_and_gaps_he` (the schema's own
existing field, already populated by every run to date), (3) a `data.specifications` row with a
null/empty `value` (an unfilled required spec IS an information gap). `run_plan` turns each gap
(capped at 6, `MAX_GAP_FOLLOWUP_TOPICS`) into an extra topic — literally appended to the topic list
and run through the exact same loop as every fixed topic, no special-casing needed downstream.

**Interface — `CorpusResult.gap_status: list[dict]`**, one row per gap that got a follow-up topic
this run:
```python
{"gap": str, "status": "closed" | "open", "cites": list[int]}
```
`"closed"` when the follow-up topic's own investigation outcome was `found`/`partial` at confidence
≥ 0.5, else `"open"`. **What LESSONS-2 still needs to do**: after computing this run's own final
`risks_and_gaps_he`, call `eoa.dossier.gaps.diff_new_gaps(previous_gaps, current_gaps)` (where
`previous_gaps = eoa.dossier.gaps.extract_gaps_from_previous(corpus.previous)`, already computed;
`current_gaps` is the just-computed final list) and merge its `"new"`-status rows into
`corpus.gap_status` before persisting the merged list into `product_dossiers.data.meta.gaps` — the
one write this lane could not make itself, since it happens after `build_dossier` (excluded file)
runs, on data this lane's own stage never sees.

## 5. Config/dependency changes

- `agent/eoa/config.py`: `DossierCfg.max_topics` default `9` → `10` (a 10th topic,
  `"platforms_and_programmes"`, was added to `eoa.dossier.plan.TOPICS`, between `"maturity"` and
  `"deals"`) — kept in sync with `config/config.yaml`'s own `dossier.max_topics: 10` (which had been
  overriding the Python default to `9`).
- `pyproject.toml`: added `pypdf>=4.0` (installed and verified in `.venv`, version 6.18.0 resolved)
  — only PDF library needed; no `pdfminer`/`pymupdf` were already present in the venv.
- `config/product_lines.yaml`: see §3 above.

## 6. Tests

New files (all pure-function/monkeypatched, no live network/DB/LLM calls):
`tests/unit/test_pdf_reader.py` (10), `tests/unit/test_dossier_datasheet.py` (15),
`tests/unit/test_dossier_programs.py` (23), `tests/unit/test_dossier_gaps.py` (19). Extended
`tests/unit/test_product_dossier_plan.py` with a new autouse `_no_live_datasheet_hunt` fixture
(mirroring `test_product_dossier_corpus.py`'s own `_no_live_patents_ops` precedent for
`collect_patents_ops` — a network-touching stage defaults ON in `run_plan`, so the TEST FILE stubs
it for every test that doesn't explicitly exercise that path) plus 14 new tests covering the
datasheet/competitor-seed/programme-deal/multilingual/gap-followup wiring. `CorpusResult` gained
four new fields (`datasheets`, `programme_deals`, `competitor_seeds`, `gap_status`), all defaulting
to `[]` — no existing test constructing a bare `CorpusResult(...)` needed updating.

`ruff check` clean on every file this lane touched. Full existing product-dossier suite (corpus,
plan, extract, diff, report, api, services, schema, jobs, vocabulary) plus `test_product_lines.py`:
**418 passed, 0 failed** — nothing pre-existing broke; every new network-touching addition
(datasheet hunt, programme-deal search) is stubbed by default in the plan test file and only
exercised by the new dedicated tests.

## 7. Live run — corpus + datasheet + programmes stages only, SPECTRO XR

Run in-process (`run_lessons1_live.py`, kept in the session's own temp scratchpad, not the repo, per
this task's own "keep temp files out of the repo" rule): `build_corpus("SPECTRO XR", "Elbit
Systems", [...], product_line=
"border_long_range_eo", include_live_patents_ops=False)`, then `datasheet.hunt_datasheets(...)`,
the `"platforms_and_programmes"` topic's own `investigate()` call + per-platform deal search,
`_resolve_competitor_seeds`, and `gaps.extract_gaps_from_previous` + `build_gap_followup_topics` —
each called directly, not through the full 10-topic `run_plan` loop, per the task's own "no full
dossier" scope. `EOA_PIPELINE=1`, `runtime/eoa.env` loaded.

**Environment finding, not a code defect**: every web-search call in this environment failed —
`ddgs`'s `html.duckduckgo.com` backend times out and the legacy `searxng` fallback gets a
connection-refused (confirmed independently: a bare `curl https://html.duckduckgo.com/html/`,
including with the shell's sandbox disabled, times out from this host/network entirely, while
`https://elbitsystems.com` and `https://www.google.com` both resolve instantly — this network
specifically cannot reach DuckDuckGo, unrelated to anything in this task's own code). The circuit
breaker (`eoa.search.provider`, pre-existing, not touched by this lane) correctly opened after 3
failures and skipped every subsequent query rather than retrying into more timeouts — exactly its
documented behavior. Net effect: `hunt_datasheets`/the platform-deal search found zero results this
run, `investigate()` degraded to `outcome="not_found"` for `"platforms_and_programmes"` — not
because the code is wrong (every path here is unit-tested with mocked search/fetch proving correct
behavior when data IS returned — see §6), but because no query could reach a search backend at all
in this run's environment. This is the same class of finding PD-quality's own diagnosis process
depends on: run it live, read exactly what happened, don't assume.

**What DID run and prove out live, unaffected by search availability:**

- **Stage 0 (corpus)**: `product_key=elbit-systems-spectro-xr`, found the real previous dossier
  (`id=7`, the PD-quality run-5 row) via `corpus.previous`.
- **Stage 1 (datasheet hunt)**: `vendor_domain=elbitsystems.com` resolved correctly (hint-map
  fallback, no registry `vendor_official` row yet at this point in a from-scratch corpus); every
  query attempt failed gracefully (logged, no crash, no partial/corrupt state) once search was
  unavailable — proves the "a search failure must never break the build" discipline actually holds
  under a genuine full-outage, not just the unit tests' simulated one.
- **Stage 2 (`platforms_and_programmes` + per-platform search)**: `investigate()` itself completed
  cleanly end-to-end (LLM tool-calling round succeeded — `cli_provider_call` logged real
  `claude-sonnet-5` calls — only the search TOOL calls inside it failed), returned an honest
  `not_found` with an accurate Hebrew explanation of exactly why ("שגיאת 'circuit-open' בכל ספקי
  החיפוש"). Zero platforms identified (nothing to identify from), zero programme deals (nothing to
  search for) — the correct behavior given zero search results, not a bug.
- **Stage 3 (competitor seeds) — fully proven, no network dependency**: `product_line=
  border_long_range_eo` resolved its real `competitor_products` from `config/product_lines.yaml`:
  LORROS (Elbit Systems), Long-View (Teledyne FLIR), Cerberus (Chess Dynamics), Ranger HDC (Teledyne
  FLIR), iSea (Controp) — 5 named products, config read correctly end-to-end.
- **Stage 4 (gap follow-up) — fully proven, no network dependency, and caught a real bug**: against
  the ACTUAL previous dossier's `data.risks_and_gaps_he`, the first version of
  `extract_gaps_from_previous` produced unreadable Python-dict-repr gap text (`"{'cites': [3, 4, ...
  ], 'text_he': '...'}"`) instead of the Hebrew sentence, because `risks_and_gaps_he` is persisted
  as `list[Sentence]` (`{"text_he", "cites"}` dicts) — this lane's original assumption ("a plain
  free-text gap list") was wrong. **Fixed the same session** (`agent/eoa/dossier/gaps.py`, now
  extracts `text_he` from a dict entry, matching `eoa.dossier.report._previous_gap_texts`'s own
  identical extraction for the identical field — confirmed by reading that function, which the
  LESSONS-2 lane had already landed independently and arrived at the same fix), with a regression
  test (`test_extract_gaps_from_risks_and_gaps_he_sentence_shaped_dicts`). Re-ran the stage-4 query
  directly against the DB after the fix: 18 real, clean, readable Hebrew gaps extracted (10 from
  `risks_and_gaps_he`, 8 `"מפרט חסר: ..."` rows from null specification values), 6 gap-follow-up
  questions correctly generated (capped at `MAX_GAP_FOLLOWUP_TOPICS`), each one a clean, well-formed
  Hebrew question naming the actual gap. Full suite re-run green after the fix (419 passed, see §6).

**Cross-lane confirmation, found while reading `report.py`/`extract.py` to verify this fix**: the
LESSONS-2 lane has already landed its own consumption of every interface this doc hands off —
`agent/eoa/llm/schemas/product_dossier.py`'s `GapTrackingRow`/`PlatformRow`, `report.py`'s
`_build_gaps_tracking_raw`/`_previous_gap_texts` (which already calls
`eoa.dossier.gaps.diff_new_gaps` exactly as §4 above asked for, and persists the merged list into
`data.meta.gaps` — the one write this lane couldn't make itself), and `extract.py`'s
`build_platforms`/`getattr(corpus, "programme_deals", ...)` — all read via `getattr(corpus, "...",
None) or []` (defensive against an older `CorpusResult` build) and all matching this lane's exact
field names and shapes with zero adjustment needed on either side.

## 8. Interface summary (for LESSONS-2, at a glance)

```python
@dataclass
class CorpusResult:
    ...
    datasheets: list[dict]        # {"url","title","text","pages","kind","n"}
    programme_deals: list[dict]   # {"platform","customer","date","amount_text","amount_value",
                                   #  "currency","cites","note_he","component_of_package"}
    competitor_seeds: list[dict]  # {"name","vendor"}
    gap_status: list[dict]        # {"gap","status":"closed"|"open","cites"} -- LESSONS-2 merges in
                                   #  diff_new_gaps(...)'s "new" rows before persisting
                                   #  data.meta.gaps for the next run.
```

Helper LESSONS-2 will want directly: `eoa.dossier.gaps.diff_new_gaps(previous_gaps, current_gaps)`.
