# Product-line spec comparison — vocabulary endpoint + "השוואת מפרט" report section (PD-vocab-reports)

Date: 2026-09-09
Scope (per this task's file ownership): `agent/eoa/report/product_line.py` (new "השוואת מפרט"
section only), `agent/eoa/api/services.py` (product-line detail — which products in the line have
dossiers — plus the vocabulary service function), `agent/eoa/api/routes/dossiers.py` (ONLY the new
`GET /api/dossiers/vocabulary/{product_line}` route), `tests/unit/test_pl_spec_comparison.py` (new),
`tests/unit/test_product_dossier_services.py` (additions). No file under `agent/eoa/dossier/**`,
`deep_search.py`, `chain.py`, or `web/**` was touched — `eoa.dossier.vocabulary` is imported
(read-only) from `agent/eoa/report/product_line.py`, never edited.

This is lane (d) of `docs/PLAN_SPEC_VOCABULARY.md` §6, run concurrently with lane (a) (extraction,
`PD-vocab-extract`). Lane (a)'s own `agent/eoa/dossier/vocabulary.py` landed on disk (confirmed via
`git status`) partway through this round, so the loader/matcher story below reflects the *actual*
API that showed up, not the design doc's `eoa.dossier.vocabulary`-doesn't-exist-yet fallback plan.

## 1. `eoa.dossier.vocabulary` had already landed — used directly, not re-implemented

Before writing any code, `agent/eoa/dossier/vocabulary.py` was checked (`git status` showed it as a
new, untracked file). It had: `SpecParam` (with a `table: "specifications"|"performance"` field,
§9 item 1's flagged follow-up already applied), `effective_vocabulary(product_line)`,
`GROUP_ORDER_HE`, and — critically — `match_key_by_synonym(text, product_line)`, the exact reused
word-boundary-safe matcher §3.2 specifies, already wired to `eoa.pipeline.text_match.synonym_present`.

This lane imports all four names directly from `eoa.dossier.vocabulary` rather than loading
`config/spec_vocabulary.yaml` itself. No local YAML loader, no local fuzzy-matching fallback — the
task brief's "otherwise load the YAML yourself" branch was not needed. `eoa.dossier.vocabulary` is
never edited by this lane.

## 2. The vocabulary endpoint

`GET /api/dossiers/vocabulary/{product_line}` (`agent/eoa/api/routes/dossiers.py`, registered before
the existing single-segment `/dossiers/{product_key}` route — no actual routing ambiguity since one
path is one segment and the other is two, but registered first for readability/safety anyway) calls
`services.dossier_vocabulary(product_line)`. `product_line == "common"` returns the common-only
vocabulary (`effective_vocabulary(None)`); any other unrecognized line id returns `None` → 404 (never
silently falls back to `common` for a typo). Each parameter is serialized with the full field
contract (`key`/`label_he`/`label_en`/`unit`/`value_type`/`enum_values`/`synonyms`/`group_he`/
`required`/`notes_he`/`table`) so the UI lane (PD-vocab-ui) has everything it needs without a second
call.

Verified live: `targeting_pods` → 43 parameters (24 common + 19 line), 12 `required: true`;
`common` alone → 24; an unknown line id → `None`/404.

## 3. Product-line detail: which products have a dossier

`services.product_line_detail` gained a `dossiers` key (new `services._product_line_dossiers(line_id)`
helper): one card per `DISTINCT product_key` with `product_line = line_id`, its latest run
(`_dossier_run_card`, the same shape `list_dossiers` already returns), sorted by product name. This
is the same set the report section (§4 below) reads server-side, surfaced here so the product-line
detail page doesn't need a second `GET /api/dossiers` round trip + client-side filter.

Live: `targeting_pods` → one product (SPECTRO XR, `elbit-systems-spectro-xr`, latest run id=3).

## 4. The "השוואת מפרט" report section

`agent/eoa/report/product_line.py`, new section 5c (between "5b. pipeline dedupe" and "6. drafting"),
`spec_comparison_entries(line_id, citation_items)`, wired into `build_product_line` right after the
existing `tables` list is assembled (after the QA citation gate has already run and after
`_downgrade_aggregator_only_actions`'s own post-QA mutation — same "only ever adds a citation, never
invalidates one already checked" discipline that function's own docstring documents).

**What it does, for every product on the line with a dossier (latest run each):**
- Caption entry naming each compared dossier's own product name + last-update date.
- One grouped table per vocabulary `group_he` (the fixed 8-group order from
  `eoa.dossier.vocabulary.GROUP_ORDER_HE`), rows = this section's own **required** vocabulary
  parameters in declaration order, columns = parameter + unit + up to 4 products (≤ 6 columns total,
  per the task brief's own cap) — a 5th+ product spills into a second `"{group} (2/2)"` table of the
  same `group_he`.
- Every cell: the product's value with its citation number(s) registered into the report's own
  shared citation registry (`[n]`, resolving in the same "נספח מקורות" appendix as every other table
  in the report), or "לא נמצא במקורות" for a required-but-unfound value.
- One deterministic sentence per row where ≥ 2 products carry a *numerically different* parsed value:
  `"{X} מציע {N} ב{פרמטר} לעומת {M} של {Y}"` — quantity right after the verb, no adjectives/
  intensifiers, so it passes `eoa.report.claims_gate.gate_text` unchanged (no trigger word, and it
  always carries a digit either way — locked in by
  `TestSpecDiffSentence::test_sentence_passes_claims_gate_unchanged`). A comparison sentence is only
  ever emitted when at least one of the two compared rows actually carries a citation
  (`Sentence.cites` requires ≥ 1 entry by schema — an uncited numeric claim is simply never
  asserted, matching that field's own "no business being a Sentence" rule); this was caught live by
  the new unit tests (a `ValidationError` on an all-empty-`cites` comparison) before it ever reached
  a real report.
- The section itself is **never omitted** — a placeholder ("אין מספיק סקירות מוצר בקטע זה להשוואה")
  renders when zero products on the line have a dossier at all, or a distinct placeholder when the
  line's effective vocabulary happens to carry no `required` parameter.

**Legacy / free-named dossiers, tolerated without crashing:** a `SpecRow`/`PerformanceRow` with a
non-empty, *valid* `key` (the new field lane (a) added) is read directly. Every other row — including
every row on a dossier built before this round (`key` is `null`/absent on all three live SPECTRO XR
runs, ids 1–3) — is matched into a vocabulary key via `eoa.dossier.vocabulary.match_key_by_synonym`,
the SAME reused matcher §3.2 specifies, and kept only when it resolves to one of this section's own
required parameters. A row that matches nothing is simply absent from the table — never raises, never
half-crashes the report. Both `specifications` and `performance` rows are read regardless of a
matched param's own `table` routing (a robustness net for exactly the pre-routing dossiers this
section is verified against).

### Deliberate deviation from the plan doc's §6 "fewer than 2 products → placeholder" rule

`docs/PLAN_SPEC_VOCABULARY.md` §6 says the section renders its placeholder for fewer than 2 products
with a dossier. The live catalog (2026-09-09) has exactly **one** `targeting_pods` product with any
dossier at all (SPECTRO XR — confirmed via a live DB query before writing any code: `SELECT DISTINCT
product_line FROM product_dossiers` returns only `('targeting_pods',)`, and that line has a single
distinct `product_key`). Requiring 2 would make the section permanently render only its "not enough"
placeholder for the one product line this task explicitly asks to verify against
("...to show the section with the SPECTRO XR dossier; verify the caption/columns/claims gate").

This lane renders the real grouped table(s) starting from **1** product with a dossier, reserving the
placeholder for the true empty case (zero products). The cross-product "differs by number" sentences
still correctly require ≥ 2 numeric values and simply don't appear for a single-product table — this
is not a relaxation of that rule, only of the table-rendering gate. Flagged here explicitly for the
lead's review, per the plan doc's own §8 "Lead reviews... before it lands" convention for anything
this round had to decide beyond the frozen design.

### Required-only rows (not the full effective vocabulary)

The task brief asked for "the **required** vocabulary parameters as rows" (not "the line's effective
vocabulary" verbatim, which is what §6 of the plan doc itself says). This lane follows the task
brief: `required_params = [p for p in effective_vocabulary(line_id) if p.required]` — 12 rows for
`targeting_pods` (11 common + 1 line-specific), grouped across 8 sub-tables. This is a narrower
row set than §6's literal wording; flagged here alongside the ≥1-product deviation above so the lead
can reconcile both against the frozen doc in one pass if desired.

## 5. Citation registration across dossiers

Each dossier's own `SpecRow`/`PerformanceRow.cites` are numbered against *that dossier's own*
`product_dossiers.sources` registry — a completely separate numbering scheme from the product-line
report's own `citation_items`. `_register_dossier_source` resolves each dossier-local `n` into the
report's shared registry, deduping by `(kind, id)` identity (so the same underlying record cited from
two different products' dossiers — or already present in this report's own item/event collection —
reuses one shared `[n]` instead of being listed twice in "נספח מקורות"). Verified live: the SPECTRO
XR "DRI" row cited an `instro.com` product-page source that was **not** already in the report's own
market-item registry — it was correctly appended as a new, second entry (`[2]`, after the report's
own `[1]` aggregator-tender item) and resolved correctly in the rendered sources appendix.

## 6. Live verification — rebuilt `pl_targeting_pods`

`EOA_PIPELINE=1`, `build_product_line("targeting_pods")` (DATABASE_URL port 5432, never 5433).
Result: `reports.id=194`, `qa_passed=True` (`output/reports/pl_targeting_pods_2026-09-09.{docx,md,html}`).

Section excerpt (`output/reports/pl_targeting_pods_2026-09-09.md`, lines 93–147):

```
## השוואת מפרט

השוואה מבוססת על הסקירות העדכניות ביותר של כל מוצר: SPECTRO XR (עדכון אחרון: 2026-09-08).

### אופטיקה

| פרמטר | יחידה | SPECTRO XR |
|---|---|---|
| שדה ראייה | מעלות (°) / מיליראד (mrad) | לא נמצא במקורות |

### חיישנים
...
### ביצועי מערכת

| פרמטר | יחידה | SPECTRO XR |
|---|---|---|
| טווח זיהוי/הכרה/זיהוי-ודאי (DRI) | ק״מ | Ultra-Long-Range (טווח ארוך במיוחד) [2](#src-2) |

### בשלות ולוגיסטיקה
...
## נספח מקורות
| # | כותרת | מקור | אמינות | תאריך | קישור |
|---|---|---|---|---|---|
| 1 | Litening advanced targeting pod Tender... | usarfp.com | מצבור מכרזים (אמינות נמוכה) · 0.30 | 2015-08-11 | ... |
| 2 | instro.com | instro.com | — | — | https://instro.com/products/electro-optical-surveillance-systems/spectro-xr/ |
```

Confirms: caption names the dossier + its date; 8 grouped sub-tables render in the fixed group order
(all rendered, even the ones with nothing found, per "required always renders"); the one legacy
free-named row that matched (`"טווח תפעולי"` → `detection_range_dri` via `match_key_by_synonym`)
carries a correctly resolved `[2]` citation pointing at the right appendix row; no "הבדלים כמותיים"
entry (correctly omitted — a single product has nothing to compare against); the whole report's own
QA (`_run_qa`) still passed.

## 7. Tests

`tests/unit/test_pl_spec_comparison.py` (new, 39 tests): `_dossier_spec_perf_rows`,
`_resolve_product_vocab_values` (direct key, legacy label match, unmatched-never-crashes,
non-required-match-ignored, invalid-key-falls-through, empty-value-never-resolves, first-match-wins),
`_register_dossier_source` (new registration, kind+id dedup, cross-kind non-collision, unresolvable
local_n, default-kind-for-untagged-entries), `_first_number`, `_spec_diff_sentence` (including the
`Sentence.cites` empty-citations guard found via this suite), and `spec_comparison_entries`
end-to-end (no-dossiers placeholder, no-required-params placeholder, single-product render, >4-product
batch split, diff-sentence emission, unmatched-row tolerance) against a small fixed 4-parameter fake
vocabulary — isolated from `config/spec_vocabulary.yaml`'s own 121-entry content.

`tests/unit/test_product_dossier_services.py` (+8 tests): `dossier_vocabulary` (targeting_pods shape,
common-only subset relationship, unknown-line → `None`), `_product_line_dossiers` (latest-per-product,
alphabetical order, empty case), `product_line_detail` (`dossiers` key wiring, unknown-line → `None`).

```
PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest \
  tests/unit/test_pl_spec_comparison.py tests/unit/test_product_dossier_services.py \
  tests/unit/test_pl_report_round15.py tests/unit/test_product_lines.py \
  tests/unit/test_report_editing_round14.py tests/unit/test_reports_round9.py \
  tests/unit/test_reports_round10.py tests/unit/test_reports_round12.py \
  tests/unit/test_reports_round13.py -q
```
→ 330 passed (DATABASE_URL loaded from `runtime/eoa.env`, port 5432; without it, 2 pre-existing,
unrelated `test_reports_round12.py` monthly-report tests fail on a Postgres connection timeout — a
DB-URL-not-loaded environment issue, unrelated to this change, confirmed by re-running with the env
loaded).

`ruff check` clean on every touched file:
`agent/eoa/api/services.py`, `agent/eoa/api/routes/dossiers.py`, `agent/eoa/report/product_line.py`,
`tests/unit/test_pl_spec_comparison.py`, `tests/unit/test_product_dossier_services.py`.

## 8. Files changed

- `agent/eoa/report/product_line.py` — new imports (`eoa.dossier.vocabulary`), new section 5c
  (`spec_comparison_entries` + 8 helpers), one call site added in `build_product_line`.
- `agent/eoa/api/services.py` — new `_product_line_dossiers`, new `dossier_vocabulary`,
  `product_line_detail` gained a `dossiers` key.
- `agent/eoa/api/routes/dossiers.py` — new `GET /dossiers/vocabulary/{product_line}` route.
- `tests/unit/test_pl_spec_comparison.py` — new, 39 tests.
- `tests/unit/test_product_dossier_services.py` — +8 tests.
- `docs/qa/content_review/PD-vocab-reports.md` — this file.

No migration needed (no schema change — the section reads existing `product_dossiers.data`/
`sources` JSONB; the vocabulary endpoint is a pure read over `config/spec_vocabulary.yaml` via
`eoa.dossier.vocabulary`, already loaded/validated by lane (a)).
