# Product-line report — targeting_pods fix pass (PL-REPORT-FIX / round 15)

Date: 2026-09-08
Scope: the four defects flagged against the user's screenshot (20:30) of the product-line report
`reports.id=184` (`targeting_pods`, `output/reports/pl_targeting_pods_2026-09-08.md`). Files
touched: `agent/eoa/report/product_line.py`, `agent/eoa/report/bd_territory.py` (recommendations +
opportunities tables — no change needed there, see §0), `agent/eoa/report/docx_builder.py` (table
caption only), `agent/eoa/report/qa_citations.py` (source reliability for tender/forecast
sources), `web/src/components/reports/ReportBody.tsx` + `web/src/lib/reportHtml.ts` (caption
rendering — no change needed there, see §3), `tests/unit/test_pl_report_round15.py` (new),
`tests/unit/test_report_editing_round14.py` (two pre-existing caption tests updated for the new
format).

## 0. `bd_territory.py` / web layer — confirmed no change needed

`bd_territory.py`'s recommendations/opportunities tables carry no caption logic and no
buyer/reliability rendering of their own to duplicate — this round's fixes live entirely in
`product_line.py`, `docx_builder.py` (the one shared `_table_caption_text`/`reliability_label`
implementation both reports render through) and `qa_citations.py`. `ReportBody.tsx`/`reportHtml.ts`
render server HTML as-is with no caption-specific string handling of their own (confirmed by
inspection and by `npm run build` + the existing `ReportBody.test.tsx`/`reportHtml.test.ts` suite,
both green after this round) — the RTL-mirroring bug (defect #3) was entirely a server-side text
issue (literal ASCII parentheses), fixed at the source in `docx_builder.py`.

## 1. Opportunities-table near-duplicate dedupe (`product_line.py`)

New `dedupe_pipeline_rows(rows, *, today)`, called in `build_product_line` right before
`pipeline_table(...)`. Rows sharing platform+payload text, buyer, and stage
(`_pipeline_dedupe_key`) whose target-date windows both fall within 14 days of each other
(`_pipeline_rows_are_near_duplicates`, checking both `window_from` and the window's end) collapse
into one row: the better-graded (A > B > C) candidate's fields survive, the date cell widens to
cover every merged window and gains a "N תחזיות מאוחדות" note, and citation numbers union
(`extra_ns`, rendered as multiple `[n][n2]` markers).

**Data-level gap (acknowledged, out of scope):** the near-duplicate pair in the original
screenshot is almost certainly the same underlying `tender_forecasts` row re-derived a day apart —
that dedupe belongs to `eoa.tenders.forecast`, not this report layer. This round only prevents the
*rendered* symptom.

**Live-rebuild finding:** rebuilding `pl_targeting_pods` today, the exact reproduction pair does
**not** merge — the two forecast rows turned out to have genuinely different `buyer_country`
values (`GR` vs `US`) once defect #4's buyer-rendering fix stopped hiding them behind a hardcoded
"—". The dedupe rule's own buyer-match requirement correctly treats them as distinct opportunities
now that the real difference is visible; merging them would have been the wrong call. The exact
screenshot scenario (same buyer, dates one day apart) is verified directly by
`TestDedupePipelineRows` in `tests/unit/test_pl_report_round15.py`, which reproduces it with
controlled input data.

## 2. Tender-aggregator domain reliability, action downgrade, tier cap

### 2a. Domain reliability map (`qa_citations.py`)

`TENDER_AGGREGATOR_DOMAINS` (usarfp.com, tendersinfo.com, bidnetdirect.com/bidnet.com,
globaltenders.com, tenderdetail.com, tendersontime.com, biddingo.com, tenderswala.com,
eibidding.com, tenderguru.com, biddetail.com, tendersgate.com — not exhaustive, extend as
confirmed), `is_tender_aggregator_domain(url=, source_name=)`, and
`tender_aggregator_reliability()` (the `{"kind", "score", "label"}` shape
`docx_builder.reliability_label` already renders — label "מצבור מכרזים (אמינות נמוכה)", score 0.3).

`product_line._attach_tender_aggregator_metadata(items, tenders)` (called in `build_product_line`
right before `citation_items` is built) attaches this descriptor to any market item whose
`url`/`source_name` resolves to a known aggregator and that doesn't already carry a `reliability`
value, and backfills a missing `published_at` from the linked `tenders` row (`tenders.item_id`) —
"their date comes from the tender row."

**Live-rebuild finding + follow-up fix:** the Litening item's own linked `tenders` row exists but
is `status='archived'` with a 2015 deadline — excluded by `collect_tenders_and_forecasts`'s own
`status IN ('open','unknown')` filter (correct for the "מכרזים ו-RFI פתוחים" table, which should
only list currently-actionable tenders). The date backfill therefore never saw it through that
list alone. Added `_any_status_tender_date_for_item(item_id)`, a status-unrestricted direct lookup
used only as a fallback for the appendix-date backfill (never for the open-tenders table itself).
Confirmed live: the appendix row for the Litening item now reads אמינות "מצבור מכרזים (אמינות
נמוכה) · 0.30" and תאריך "2015-08-11" (previously "—"/"—").

### 2b. Recommended-action downgrade (`product_line.py`)

`_downgrade_aggregator_only_actions(actions, citation_items)`, applied to `draft.recommended_actions`
right after the QA-gate loop resolves (only ever adds a citation reusing an already-valid `n`, so
it can never invalidate a passed QA result). An action is downgraded — `action_he` gains a "לאימות:
" prefix, `priority` forced to `"L"`, a deterministic rationale sentence explaining the aggregator
caveat appended — only when **every** citation it can resolve to a source is a confirmed
aggregator; an action citing at least one non-aggregator source alongside an aggregator is left
untouched. Confirmed live: all three recommended actions in the rebuilt report (whose only source
is the usarfp.com item) now read "נמוכה" priority with a "לאימות: " prefix and the aggregator
disclosure sentence.

### 2c. Opportunity tier cap (`product_line.py`)

`PipelineRow.is_aggregator_only` (set per-row by each `_pipeline_rows_from_*` builder from the
row's own citation source(s) — a tender's own `url`, a forecast's `trigger_item_id` resolved to its
triggering item's `url`/`source_name`, an event's own `item_url`/`source_name`) caps
`PipelineRow.tier()` at `"C"` regardless of the computed score. Confirmed via unit test
(`test_pipeline_row_tier_capped_at_c_when_aggregator_only`) that an aggregator-only row which would
otherwise score "A" is forced to "C".

**Live-rebuild note (defect #4's own diagnostic ask):** the RFI-tender row ("REQUEST FOR
INFORMATION... EO/IR") renders grade C — not because of the aggregator cap (its source is
sam.gov, not an aggregator) but because the row carries no amount/likelihood/level data and no
deadline, so `_tier_score` bottoms out at the lowest magnitude/recency score. This is the tier
formula working as designed on a genuinely thin data row, not a bug.

## 3. Row-count caption RTL fix (`docx_builder.py`, single shared implementation)

`_table_caption_text` no longer wraps the synthesized row count in ASCII parentheses — inside an
RTL Hebrew paragraph, `(`/`)` are mirrored characters under the Unicode bidi algorithm, which is
exactly why `"(3 שורות)"` rendered as `")שורות 3("` in the UI. The synthesized form is now plain
`"N שורות"`. Also drops the caption entirely for a table of **3 rows or fewer** (a bare row count
under a table the reader can already see is that short is noise). An author-provided `note_he` is
untouched either way — it's free text, not the synthesized shape, and still renders even under the
3-row threshold. Single implementation, shared by docx/md/html renderers for both product_line and
bd_territory (neither duplicates this logic).

`eoa.report.textnorm.strip_bidi_isolates` only strips U+2066–U+2069 (LRI/RLI/FSI/PDI); it never
touched the ASCII parentheses either way, so no interaction there — no RLM marker was needed once
the parentheses themselves were dropped.

Two pre-existing tests in `tests/unit/test_report_editing_round14.py` asserted the old
`"(3 שורות)"` format; updated to the new format (moved to a 5-row table so the caption still
synthesizes) plus three new tests for the ≤3-row drop and the note_he-still-wins case.

## 4. Buyer display — never "—" (`product_line.py`)

`pipeline_table`'s "גורם רוכש" cell now falls back to "לא צוין" instead of "—" when a row's
`buyer_he` is empty. All three `_pipeline_rows_from_*` builders were changed to store `""` (not a
literal "—") for a missing buyer, so the fallback applies uniformly.

Separately, `_pipeline_rows_from_forecasts` was hardcoding `buyer_he="—"` even though
`tender_forecasts.buyer_country` (already selected by `collect_tenders_and_forecasts`) frequently
carries a real value — a render-side bug, not a data gap. Now uses `f.get("buyer_country")`.
Confirmed live: the two forecast-based opportunity rows now show real buyer countries ("GR", "US")
instead of "—", and the tender-based RFI row (whose `agency` really is unset) reads "לא צוין".

## Tests

`tests/unit/test_pl_report_round15.py` (new, 33 tests, no DB/LLM calls — every DB-touching path is
monkeypatched): `TestDedupePipelineRows` (5), `TestTenderAggregatorReliability` (18, covering the
domain map, metadata attachment incl. the archived-tender DB-fallback path, the action downgrade,
and the tier cap), `TestTableCaptionRtlFix` (3), `TestBuyerNeverEmDash` (5), plus 2 more.
`tests/unit/test_report_editing_round14.py`'s caption tests updated (2 changed, 2 added).

Full results:

- `pytest tests/unit/test_pl_report_round15.py` — 33 passed.
- `pytest tests/unit/test_product_lines.py tests/unit/test_product_lines_round8.py
  tests/unit/test_docx_builder.py tests/unit/test_report_editing_round14.py` — 178 passed.
- Broader report-suite regression sweep (`test_reports_round9/10/12/13`, `test_bd_round4b/5`,
  `test_bd_runaway_round3`, `test_bd_structured_round3`, `test_bd_tenders_round3`,
  `test_report_qa`, `test_report_round3_d6`, `test_bluf_round5`, `test_monthly_round5`,
  `test_ask_round6_grounding`) — 700 passed, 3 failed. All 3 failures are pre-existing,
  environment-only `psycopg_pool.PoolTimeout` errors inside `monthly.py`-dependent tests (no DB
  password available to this sandboxed run at the time; `monthly.py` was not touched by this
  round) — confirmed unrelated to this change set.
- `ruff check` clean on all touched Python files.
- `cd web && npm run build` — succeeds, no source changes needed on the web side.
- `npx vitest run src/components/reports/ReportBody.test.tsx src/lib/reportHtml.test.ts` — 25
  passed.
- Live rebuild: `EOA_PIPELINE=1`, `build_product_line("targeting_pods")` — `qa_passed=True`,
  `reports.id=188`, all four defects confirmed gone in the regenerated
  `output/reports/pl_targeting_pods_2026-09-08.md` (see live-rebuild notes above for the two
  follow-up fixes the first rebuild attempt surfaced: the archived-tender date-backfill fallback
  and the buyer-country-driven non-merge explanation for defect #1).
