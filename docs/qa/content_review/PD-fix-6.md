# Product Dossier — sixth live-run fix pass (PD-fix-6): deal-row hygiene

Date: 2026-09-09
Scope: the SPECTRO XR dossier (`product_dossiers.id=12`, `product_key elbit-systems-spectro-xr`)
is otherwise good (18/22 keyed specs all high-confidence, 10 deals, 11 timeline rows,
`confidence=0.85`), but its `deals` table has six garbage rows produced by the PD-fix-4
press-release candidate builder (`_deal_candidates_from_registry`). Built and verified
**read-only** against `id=12`'s own persisted `data`/`sources` JSON (`DATABASE_URL`, port 5432,
never 5433) — no new dossier run, no LLM/network call. No dossier job was started.

Per this task's file ownership: `agent/eoa/dossier/extract.py`, plus
`tests/unit/test_product_dossier_extract.py` and `tests/unit/test_product_dossier_lessons2.py`
(the latter for `build_timeline`'s `programme_deals` hygiene, item 5). `report.py`/`diff.py` were
inspected but needed no change — both already treat `customer=None`/`date=None` correctly (that
discipline predates this pass, PD-fix-3/PD-fix-5).

## Root cause

`_deal_candidates_from_registry` scans a registry source's own text for a monetary-figure candidate
using `_DEAL_NUM_RE = re.compile(r"(\d[\d,.']*)")` — this matches **any** digit run, including the
bare "1"/"3" inside a markdown list marker ("**(1)**", "1.") in an unrelated
"deep investigation findings" writeup that happens to also contain an award/contract keyword
(`_DEAL_AWARD_KEYWORD_RE`) elsewhere in the same source text. `parse_amount_he` was then called on
a ~30-char window around that digit and happily returned a number even with no currency symbol/word
and no scale word anywhere nearby — so `amount_value=1.0` for a fragment like
`"**(1) עובדות רלוונטיות:** ב-1 בספט"` was accepted as a real deal amount. Because the candidate
builder never sets `customer` at all, these six rows kept `customer=None` (not itself wrong), but
their `amount`/`date` fields carried torn investigation prose instead of a real figure/date, and
that same prose then leaked into the timeline via `build_timeline`'s
`f"עסקה: {customer}{amount_part}."` rendering (`amount_part` uses the raw `amount` text verbatim).

## Fixes (`agent/eoa/dossier/extract.py`)

**1. Customer must look like a real name.** New `_looks_like_customer_name(text)`: `<= 60` chars,
no `"**"`, doesn't open with a punctuation character (catches a torn markdown bullet "- ..."),
has at least one real letter (rejects a bare number/citation-index string), and doesn't contain
`"עובדות רלוונטיות"` / `"ניתוח הדף"` / `"מקורות"` (only ever seen in torn investigation-prose
fragments, never in a real organisation/country name). `_normalize_customer` now also runs this
gate — a value that fails it is normalized to `None`, same as the existing placeholder-string check
(PD-fix-3 item 4). This is applied to every `DealRow.customer`, model-authored or candidate-derived,
and reused for `programme_deals` rows in `build_timeline` (item 5, below).

**2. Deal dates are ISO or null.** New `_normalize_deal_date(date)`: parses an already-ISO date
("YYYY-MM-DD", validated against real calendar ranges) or year-month ("YYYY-MM") through unchanged;
strips a time-of-day component off a registry `published_at` timestamp
("2026-09-02 09:04:00+03:00" → "2026-09-02"); parses `"D/M/YYYY"` into ISO; parses a Hebrew
("ספטמבר 2026" → "2026-09") or English ("March 2023" → "2023-03") month-year phrase into
`"YYYY-MM"`; anything else (including the `_finding_date_hint` textual fallback's own raw
sightings, and pre-existing unparseable garbage) becomes `None` rather than being persisted as free
text. Applied in `_ground_deal_row` to whichever value `date` ends up as (model-authored, the
registry-`published_at` backfill, or the `_finding_date_hint` textual fallback) — a `DealRow.date`
is never free text again. Also applied to `programme_deals` rows rendered into the timeline (item
5).

**3. Candidate amounts need real currency/scale evidence.** `_deal_candidates_from_registry` now
only promotes a digit run to a candidate when `parse_amount_he` itself found a currency (symbol or
word) **or** the snippet names a scale word (מיליון/million/...) — exactly the evidence
`parse_amount_he` already looks for, just required rather than optional. A digit run with neither
is skipped outright; a source whose every digit run fails this check yields no candidate for that
source at all (this is the actual fix for the five garbage rows below — they are never created in a
fresh run, not merely cleaned up after the fact).

**4. Dedup + drop-if-empty.** New `_deal_dedup_key(deal)`:
`(amount_value or amount text casefolded, kind, customer casefolded or date[:4])`; new
`_finalize_deals(deals, dropped)` — a row with none of amount/customer/date/country/region_he/
platform/quantity is dropped (item 4; the `country`/`region_he`/`platform`/`quantity` check is not
literally named by item 4 but follows the same "nothing worth keeping" rule — PD-fix-5 item 3
deliberately keeps a deal whose customer is unknown but whose region IS grounded, and that row must
survive this check too), then the second and later row sharing a dedup key is dropped as a
duplicate (first occurrence — model-authored rows arrive before PD-fix-4 candidates, per
`_merge_deal_candidates`, so a model's own richer row always wins). Wired in right after
`_ground_deal_row` in the main extraction pipeline.

**5. Same hygiene for `programme_deals` in the timeline.** `build_timeline`'s `programme_deals` loop
(LESSONS-fable-dossier item 3's per-platform programme-deal search, a separate deterministic
pipeline in `eoa.dossier.programs`, not touched by this pass) now runs each row's `date` through
`_normalize_deal_date` (dropping the row if unparseable, same as the pre-existing "no
date/no cites → skip" rule) and its `customer` through `_looks_like_customer_name` (falling back to
naming just the `platform` when the customer fails the gate) before rendering the row's event text,
plus a dedup guard on `(date, who, amount_text)` so the same programme deal is never rendered twice.

## Offline verification (read-only, `product_dossiers.id=12`)

Script re-ran `parse_amount_he` + the new currency/scale gate directly on the exact `amount`
snippets already persisted in row 12 (these snippets ARE the same text the candidate builder would
re-derive from the same source on a fresh run), then ran `_normalize_customer` /
`_normalize_deal_date` / `_finalize_deals` — the actual fixed functions — over all 10 persisted
rows.

**Before (10 rows, as persisted):**

| cites | kind | customer | amount | date |
|---|---|---|---|---|
| [21,22] | framework | משרד ההגנה הלאומי של רומניה | (empty) | 2022-12-21 |
| [21,22] | contract_award | משרד ההגנה הלאומי של רומניה | כ-180 מיליון דולר | 2023-06-21 |
| [2] | contract_award | לקוח בינלאומי (זהות לא צוינה) | (empty) | 2022-11-14 |
| [2] | contract_award | הצי הרומני | (empty) | 2023-03-14 |
| [1] | contract_award | **null** | `nds $270M ISR Deal חברת אלביט מערכות ז` | `2026-09-02 09:04:00+03:00` |
| [14] | contract_award | **null** | `הביא 3 מקורות נוספים. להלן ניתוח הדף` | **null** |
| [17] | contract_award | **null** | `**(1) עובדות רלוונטיות:** ב-1 בספט` | `ספטמבר 2026` |
| [18] | contract_award | **null** | `ews, 03/2023) **(1) עובדות רלוונטיות` | `מרץ 2023` |
| [19] | contract_award | **null** | `**1. עובדות רלוונטיות:** לפי הודעת` | **null** |
| [20] | contract_award | **null** | `**(1) עובדות רלוונטיות:** מתוך ארב` | `20/3/2023` |

**Candidate-creation-time gate replayed on the 6 candidate-derived rows** (would this row even be
created in a fresh run under the fixed `_deal_candidates_from_registry`?):

| cites | snippet | currency found | scale word found | verdict |
|---|---|---|---|---|
| [1] | `nds $270M ISR Deal חברת אלביט מערכות ז` | USD (`$`) | no | **kept** — real currency evidence |
| [14] | `הביא 3 מקורות נוספים. להלן ניתוח הדף` | no | no | **rejected** — never created |
| [17] | `**(1) עובדות רלוונטיות:** ב-1 בספט` | no | no | **rejected** — never created |
| [18] | `ews, 03/2023) **(1) עובדות רלוונטיות` | no | no | **rejected** — never created |
| [19] | `**1. עובדות רלוונטיות:** לפי הודעת` | no | no | **rejected** — never created |
| [20] | `**(1) עובדות רלוונטיות:** מתוך ארב` | no | no | **rejected** — never created |

**After (end-to-end: the 4 real model-authored rows unchanged, plus only the 1 candidate row that
survives the gate, with its date normalized) — 10 rows → 5 rows:**

| cites | kind | customer | amount | date |
|---|---|---|---|---|
| [21,22] | framework | משרד ההגנה הלאומי של רומניה | (empty) | 2022-12-21 |
| [21,22] | contract_award | משרד ההגנה הלאומי של רומניה | כ-180 מיליון דולר | 2023-06-21 |
| [2] | contract_award | לקוח בינלאומי (זהות לא צוינה) | (empty) | 2022-11-14 |
| [2] | contract_award | הצי הרומני | (empty) | 2023-03-14 |
| [1] | contract_award | null | `nds $270M ISR Deal חברת אלביט מערכות ז` | **2026-09-02** (time stripped) |

No dedup collision fired in this particular replay (the 4 real rows all have distinct
`(amount, kind, customer)` triples, and only one candidate survived) — dedup is covered separately
by dedicated unit-test fixtures (below) reproducing two rows over the same amount/kind/customer.

Field-level replay of rules 2/3/4 alone (customer/date normalized on all 10 rows, without applying
the candidate-creation-time gate) is in the script output too and shows every Hebrew month-year date
correctly becomes `YYYY-MM` ("ספטמבר 2026" → "2026-09", "מרץ 2023" → "2023-03"), the D/M/Y date
becomes ISO ("20/3/2023" → "2023-03-20"), and the timestamp is stripped to a date
("2026-09-02 09:04:00+03:00" → "2026-09-02") — confirming rule 2 independent of rule 1's candidate
gate.

**Known follow-up, out of this pass's scope:** `parse_amount_he` doesn't recognize the "M"/"B"
abbreviation ("$270M"), only spelled-out scale words (מיליון/million/...) — so the one surviving
candidate's `amount_value` is `270.0`, not `270_000_000.0`. This predates PD-fix-6 and is a
`parse_amount_he` amount-magnitude bug, not a customer/date/dedup/empty-row hygiene bug; flagged for
a follow-up pass rather than folded in here.

## Tests

`tests/unit/test_product_dossier_extract.py` — new fixtures use the exact garbage strings named in
the task brief (`_PD_FIX_6_GARBAGE_CUSTOMERS`), plus:
- `_looks_like_customer_name`/`_normalize_customer` reject all 4 garbage strings, accept real
  organisation/country names.
- A `DealRow` with a garbage customer grounds to `customer=None` but keeps its real `amount`.
- `_normalize_deal_date`: Hebrew month-year, English month-year, timestamp-with-time, D/M/Y,
  already-ISO passthrough, unparseable → `None`.
- A `DealRow` with a Hebrew-month-year or timestamped `date` grounds to ISO.
- A registry source whose only digit runs are markdown list markers yields no deal candidate at all
  (root-cause regression test).
- Dedup: two model-authored rows over the same amount/kind/customer (different `cites`) collapse to
  one, first occurrence kept; a same-amount/same-published-year candidate collapses against a
  customer-less model row via the `customer or date year` identity; same amount but different `kind`
  (framework vs. contract_award) are NOT collapsed.
- A fully empty row is dropped by `_finalize_deals` directly.
- Two pre-existing tests (`test_deal_customer_placeholder_dash_normalized_to_none`,
  `test_deal_customer_placeholder_hebrew_text_normalized_to_none`) were given an explicit `date` so
  they keep testing customer-placeholder normalization in isolation from the new item-4 drop rule
  (their original bare-placeholder-only row is now covered by the new
  `test_deal_row_with_no_content_at_all_is_dropped`). One pre-existing test
  (`test_deal_date_backfilled_from_cited_text_when_no_registry_published_at`) had its assertion
  updated from the raw Hebrew-month-year hint `"יוני 2021"` to the now-normalized `"2021-06"` —
  this is the intended behavior change from rule 2, not a regression.

`tests/unit/test_product_dossier_lessons2.py` — three new tests for item 5: a Hebrew month-year
`programme_deals` date normalizes to ISO in the timeline; an unparseable `programme_deals` date
drops the row; a garbage `programme_deals` customer is excluded from the rendered event text
(falls back to naming just the platform).

Run: `PYTHONUTF8=1 PYTHONPATH=agent .venv/Scripts/python.exe -m pytest
tests/unit/test_product_dossier_extract.py tests/unit/test_product_dossier_lessons2.py
tests/unit/test_product_dossier_report.py tests/unit/test_product_dossier_diff.py -q`

**226 passed, 0 failed.** `ruff check agent/eoa/dossier/ tests/unit/test_product_dossier_extract.py
tests/unit/test_product_dossier_lessons2.py` — all checks passed.
