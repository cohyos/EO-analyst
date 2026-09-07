# CR Patents — Assignee Misattribution Root-Cause, Fix, and Repair (Round 14)

Date: 2026-09-07. Trigger: user report (severe) — patent row id 64, CN112074705A ("Method and
system for optical inertial tracking of moving object"), was attributed to Anduril
(`patents.assignees = ['Anduril']`, `entity_ids = [7]`) and surfaced as an Anduril patent in
`output/reports/weekly_2026-09-07.md` lines 460/475 (also independently caught by the
line-by-line CR-factcheck pass, `docs/qa/content_review/CR-factcheck.md`'s weekly table, row
"460, 475"). Its raw record is only a Google-Patents search snippet mentioning "fpga ...
(Lattice Semiconductor Corporation, USA)"; the watchlist alias "Lattice" (one of Anduril's own
**product** names in `config/watchlist.yaml`) matched and was turned into an assignee. 86 of the
86 stored patents had no independently-confirmed (detail-page/structured-API) assignee on record,
so a full audit was run, not just a point fix for id 64.

## 1. Root cause

`agent/eoa/patents/scan.py::_assignee_candidates_in_text` (called from the keyless
Google-Patents-search fallback, `_google_patents_records`) extracted assignee candidates via
`eoa.pipeline.entity_normalize.find_watchlist_aliases_in_text`, filtered to `kind == "company"`
records. That function's alias table treats every one of a watchlist company's `aliases` —
including its own **products/systems** ("Lattice", "Anvil", "Roadrunner" for Anduril; "LOCUST",
"Titan" for BlueHalo; "POP" for IAI; etc.) — as an equally valid surface for attributing a text
mention to the company. There was no distinction between "this text names the company" and "this
text names a product the company happens to make."

Patent id 64 was scanned 2026-09-06 08:30:13, **before** the round-2 strict-alias co-occurrence
gate (commit `85c59c7`, 2026-09-06 11:24:47) existed at all, so a bare, unaccompanied "Lattice"
mention was enough on its own. But the round-2 gate would not have generally prevented this class
of bug either: it only requires the company's *own* name/alias to also appear somewhere in the
same text, which says nothing about whether the matched span is actually naming the company —
a genuine, unrelated "Lattice Semiconductor Corporation, USA" citation reads the same to that gate
as a real Anduril mention that happens to also cite "Lattice." The real fix needed is structural:
(a) a product/system alias must never become an assignee at all, regardless of co-occurrence, and
(b) a match that is plainly a fragment of a *different*, longer organisation's name (or a bare
citation/component mention) must be rejected on its own terms.

## 2. Code fix

### (a) Product aliases are never assignee-eligible

- `config/watchlist.yaml`: new `product_aliases:` key per company entry — a subset of `aliases`
  that names a product/system/programme, not the company itself. Populated for the 20 companies
  whose alias lists plainly mix in product/program names (Anduril: Lattice/Anvil/Roadrunner;
  Lockheed Martin: Sniper ATP/HELIOS; L3Harris: WESCAM/MX-Series; Shield AI: Hivemind/V-BAT;
  BlueHalo: LOCUST/Titan; Epirus: Leonidas; Fortem: DroneHunter/SkyDome; Thales: TALIOS; Safran:
  Paseo/Euroflir; MBDA: DragonFire; Rheinmetall: Skynex/Skyranger; Saab: Giraffe; Terma: Scanter;
  Aselsan: ASELFLIR/İHTAR; IAI: MOSP/POP; Rafael: Litening/Iron Beam/מגן אור/Drone Dome/Toplite;
  Smart Shooter: SMASH; D-Fend: EnforceAir; UVision: Hero-120/Hero 120; Aeronautics: Orbiter).
  Still used for topic/NER matching (`find_watchlist_aliases_in_text`, unchanged) exactly like any
  other alias — only assignee inference excludes them.
- `agent/eoa/pipeline/entity_normalize.py`: `_alias_index()` now carries `product_aliases` per
  record; new `find_watchlist_company_names_in_text(text)` — the assignee-safe matcher — restricts
  to `kind == "company"` records, their own canonical name plus genuine company-name aliases
  (never a `product_aliases` entry), and additionally keeps the round-2 strict-alias co-occurrence
  gate as defense in depth for any future alias that is strict without being product-flagged.

### (b) Reject a match embedded in a longer/unrelated organisation name or citation

- `_looks_like_longer_org_name_or_citation(text, start, end, surface)` (same module): for a
  **single-word** matched surface only (a multi-word alias like "Anduril Industries" or "Lockheed
  Martin" is already specific enough on its own), rejects the match when the next word is a
  generic corporate-entity suffix ("Semiconductor", "Corporation", "Inc", "Ltd", "GmbH",
  "University", "Technologies", ... — the full list is in the module) — e.g. "Lattice" immediately
  followed by "Semiconductor Corporation" names a different company entirely — or when the
  immediately preceding token is itself a capitalised word, reading as a fragment of a longer
  capitalised phrase.

### (c) A snippet-derived assignee is low-confidence; a detail-page assignee always wins

- `agent/eoa/patents/scan.py::_google_patents_records`: when `_assignee_candidates_in_text` finds
  an assignee, `raw.assignee_source = "snippet"` is stamped on the record.
- `_patents_missing_assignee` now also selects a row whose `raw->>'assignee_source' = 'snippet'`
  (not only a row with no assignee at all), so the routine enrichment pass
  (`enrich_stored_patents_missing_assignee`, called from every `build_patent_survey` run) keeps
  re-confirming a low-confidence row until a detail page is actually fetched.
- `_backfill_patent_fields(..., overwrite_assignees=True)`: unlike the routine, never-overwrite
  backfill path (`COALESCE(NULLIF(assignees, ...), ...)`), this unconditionally replaces
  `assignees` and stamps `raw.assignee_source = "detail_page"` — used only by the enrichment pass
  when a genuine detail-page assignee was just fetched, so it always wins over whatever was
  stored before (empty or a low-confidence guess).

### (d) entity_ids always derived from the final assignees

- `enrich_stored_patents_missing_assignee` now also re-derives and writes `entity_ids` (via
  `eoa.patents.analyze._resolve_entity_ids`, reused read-only — this round does not edit
  `analyze.py`) immediately after a detail-page assignee overwrite, since `analyze_patents` itself
  only (re)computes `entity_ids` for a row still missing `claims_summary_he` and would otherwise
  never revisit an already-analyzed row's now-stale `entity_ids`. A failure resolving/writing
  `entity_ids` is logged and skipped on its own — it never rolls back the assignee/CPC correction
  that was already applied.

## 3. Audit/repair script

`scripts/repair_round14_patents.py` (dry-run by default, `--apply` to write, `--pub-number` to
scope to one patent, backs up every touched row's pre-change state to
`runtime/backups/repair_round14_patents_<UTC timestamp>.json` before writing, re-verifies its own
writes from a separate connection). Scope: every `patents` row with
`source = 'google_patents_search'` (i.e. every row whose current assignee, if any, was never
independently confirmed by a structured API or a detail page — `epo_ops`/`uspto_odp` rows are
always trusted and skipped) — **all 86 rows** in the live table, since none had gone through
structured-source scanning. For each: fetch the patent's own Google Patents detail page (reusing
`eoa.patents.scan`'s existing SSRF-guarded fetch + 2s pacing), compare its real `DC.contributor`
assignee(s) against what is currently stored, and apply the correction. When the detail page
itself carries no assignee data (this round: never actually hit) but the row's only support for
its stored assignee is a product-alias-only match with no genuine company-name evidence in the
same text, the fixed matcher is used as a same-process fallback to blank the disproven value
rather than leave a known-wrong value in place.

### id 64 — before / after (fixed first, per instruction)

| Field | Before | After |
|---|---|---|
| `assignees` | `['Anduril']` | `['ALT LLC']` |
| `entity_ids` | `[7]` (Anduril's entity row) | `NULL` (no fabricated link — "ALT LLC" is not a watchlist/Israeli company and no existing `entities` row matches it case-insensitively) |
| `raw.assignee_source` | *(not set — predates this round)* | `"detail_page"` |
| Culprit alias | "Lattice" (Anduril's own product alias), matched against "...CTR50(**Lattice** Semiconductor Corporation, USA)." | — |

Verified live 2026-09-07 against `https://patents.google.com/patent/CN112074705A/en`: the page's
own `DC.contributor`/`scheme="assignee"` meta tag names **ALT LLC**, an entity wholly unrelated to
Anduril and unrelated to Lattice Semiconductor.

### Full sweep — results (all 86 candidate rows audited and corrected)

| Outcome | Count | Meaning |
|---|---|---|
| `confirmed` | 16 | Stored assignee already matched the detail page exactly (mostly earlier, correctly-derived detail-page values from the round-6 enrichment pass) — no value change, `raw.assignee_source` stamped `detail_page` |
| `gap_filled` | 68 | Stored assignee was empty; the detail page had one — filled in |
| `mismatch_corrected` | 2 | Stored assignee disagreed with the detail page — corrected |

**Both `mismatch_corrected` rows (plus id 64, fixed separately first):**

| id | pub_number | Stored | Real (detail page) | Culprit |
|---|---|---|---|---|
| 64 | CN112074705A | `Anduril` | `ALT LLC` | watchlist alias "Lattice" (Anduril product) matched an unrelated FPGA-vendor citation |
| 14 | US20150015759 | `Europe` | `Individual` | pre-existing bad value from before the `kind == "company"` filter existed in `_assignee_candidates_in_text`/its predecessor (curated-org "Europe" record); the "kind == company" filter has correctly rejected this class since it was added, so no code fix was needed for this class specifically — the row just still carried the pre-fix value |
| 62 | US10506436B1 | `Anduril` | `Anduril Industries Inc` | not a misattribution (same real company) — the bare canonical name from a *snippet* match ("Assigned to **Anduril Industries Inc.**") is upgraded to the detail page's own precise legal name |

**CN patents: 7 of 7 changed** (all were gap-fills — no CN patent besides id 64 was a
misattribution; the other 6 simply had no assignee on record at all before this round).
**Previously-assignee-less patents: 68 of 68 changed** (all filled from empty to a genuine
detail-page assignee).

Verified independently from a fresh connection (not the script's own):

```
still empty assignees: 0
raw.assignee_source = 'detail_page': 86 (all rows)
id=14  US20150015759   assignees=['Individual']            entity_ids=None
id=62  US10506436B1    assignees=['Anduril Industries Inc'] entity_ids=None
id=64  CN112074705A    assignees=['ALT LLC']                entity_ids=None
```

Backup: `runtime/backups/repair_round14_patents_20260907T190234Z.json` (id 64 alone, applied
first) and `runtime/backups/repair_round14_patents_20260907T191111Z.json` (the remaining 85 rows,
full sweep).

### A known, pre-existing, out-of-scope gap surfaced by this repair

`entity_ids` is now `NULL` for every one of the 86 rows, including the four genuine Anduril
patents (61, 62, 80, 82 — `assignees = ['Anduril Industries Inc']`). This is **not** a regression
introduced by this round: `eoa.patents.analyze._resolve_entity_ids` (which this round reuses
read-only, per the ownership boundary — `analyze.py` is being edited concurrently by another
agent) only links a name to an entity via an *exact* (case-insensitive) match against
`entities.name` or a watchlist alias/canonical-name match. "Anduril Industries Inc" does not
exactly match the watchlist's "Anduril Industries" alias (extra "Inc") or any existing `entities`
row, so it resolves to no entity — same as it already did, pre-repair, for ids 61/80/82 (which
already carried this exact string and already had `entity_ids = NULL` before this round). This is
a real gap (a full legal-name assignee string not fuzzy-matching its watchlist company) but it is
a pre-existing limitation of `analyze.py`'s own resolution logic, not something introduced or
silently patched around here — flagged for the `analyze.py` owner as a follow-up, not fixed in
this round.

## 4. Tests

`tests/unit/test_patents_round14.py` (21 tests, all passing) — id-64 snippet as a literal fixture
throughout. Covers: the `product_aliases` watchlist concept; `find_watchlist_company_names_in_text`
(rules a+b, including that a multi-word alias followed by its own ordinary corporate suffix is
*not* rejected, only a bare single-word alias is); that the general-purpose
`find_watchlist_aliases_in_text` is unaffected (product aliases still count for topic/NER
matching); `_assignee_candidates_in_text`; `_google_patents_records`'s new `assignee_source`
marker; `_backfill_patent_fields`'s `overwrite_assignees` path; `_patents_missing_assignee`'s
widened query; `enrich_stored_patents_missing_assignee`'s `entity_ids` resync and its
failure-isolation (rule 9: an entity_ids-resync failure never undoes an already-applied assignee
correction).

`tests/unit/test_patents_*.py` (the full pre-existing suite, 556 patents/entity_normalize tests
across all files) — all still green. Two existing tests in `tests/unit/test_patents_round6.py`
(`test_enriches_target_and_writes_via_backfill`,
`test_one_backfill_failure_does_not_abort_the_rest_of_the_batch`) were extended with new mocks for
the added `_entity_ids_for_assignees`/`_set_entity_ids` calls (their original assertions are
unchanged) so they keep exercising only mocked DB/network calls, per this test file's own
"no live DB/network calls" convention.

`ruff check` clean on every file touched.

## 5. Reports that must be rebuilt

The `patents` table is now correct at the data layer; every report that reads it needs a fresh
build to show the corrected assignees (report_section.py / survey.py read `patents` live at
build time — no caching — so this is a straightforward rebuild, not a further code change):

- **Both on-demand patent surveys** (`eoa.patents.survey.build_patent_survey`, this round's own
  responsibility) — both rebuilt live 2026-09-07 with `EOA_PIPELINE=1`:
  - Topic `"Anduril Lattice counter-UAS EO/IR optical tracking patents"` → `patent_surveys.id=31`,
    `reports.id=144`. Confirmed clean: the assignee table/profile now shows a single, consistent
    `Anduril Industries Inc` (4 of 6 patents) instead of the previous split between the bare,
    ambiguous `Anduril` and `Anduril Industries Inc` spellings — no bare "Anduril" attribution
    left anywhere in the rebuilt survey.
  - Topic `"FPA עם פיקסל דיגיטלי DROIC"` → `patent_surveys.id=32`, `reports.id=145`, 10 patents.
    This topic's own sample does not happen to include CN112074705A or any of the Anduril/id-14
    patents (a different DROIC-focused search), so it carries no direct before/after contrast for
    this specific bug — its rebuild instead confirms the fixed pipeline runs cleanly end-to-end
    (scan → enrich → analyze → render) with no regression.
- **weekly** (`eoa.report.weekly`, via `eoa.patents.report_section.collect_patents_window` +
  `acquisition_watch`'s patent-proxy lines) — this is the report the original bug report was
  filed against (lines 460/475 of `weekly_2026-09-07.md`).
- **monthly** (`eoa.report.monthly`, via `collect_patents_landscape`).
- **bd_<territory>** for every territory with patent coverage (`eoa.report.bd_territory`, via
  `collect_patents_bd`) — in particular `bd_us` (id 64's real assignee, ALT LLC, and ids 61/62/80/82's
  Anduril patents are all US-jurisdiction) and `bd_cn`/wherever CN patents surface.

Rebuilding weekly/monthly/bd_territory is out of this round's ownership (`agent/eoa/report/**` is
being edited concurrently by another agent per the task's ownership split) — flagged to the lead
to run after this round lands.
