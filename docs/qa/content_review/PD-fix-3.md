# Product Dossier — third live-run fix pass (PD-fix-3)

Date: 2026-09-08
Scope: the four findings from comparing the third live SPECTRO XR dossier
(`product_key elbit-systems-spectro-xr`, `product_dossiers.id=3` vs `id=2`, `id=3` run after
PD-fix-2 landed) — an ungrounded patents section, a deal-identity false positive reintroduced by a
missing/placeholder customer, spec-parameter-name drift between runs, and a deals-table rendering
gap — plus a fifth, standalone question about whether the dossier's per-topic time cap
(`dossier.topic_time_cap_s`) is actually enforced. Built without rerunning the live dossier
(read-only inspection of `id=2`/`id=3`'s `data` JSON via `DATABASE_URL`, port 5432 verified, never
5433; `compute_diff` was run offline over the two stored rows, see the recomputed list below).

Per this task's file ownership: `agent/eoa/dossier/**` (corpus, plan, extract, diff, report) and
`tests/unit/test_product_dossier_*.py`. Item 5's root cause and fix live in
`agent/eoa/search/deep_search.py` (`eoa.dossier.plan.run_plan` already forwards
`deadline_s=cfg.topic_time_cap_s` into `investigate()` correctly — the enforcement gap was one
level deeper, inside `investigate()`'s own `_act()` loop) — outside the literal `dossier/**` path,
but the task explicitly asked for this specific fix, so it is included; its own test lives in
`tests/unit/test_deep_search_budget.py`.

## 1. Patents section: ungrounded rows (`agent/eoa/dossier/corpus.py`, `agent/eoa/dossier/extract.py`)

The live bug: `id=3`'s patents section held 8 rows, all generic "pod"/"target"/"cooling" hits —
`CN113804187A` (target positioning), `US6205803B1` (avionics-pod cooling), `WO2020086710A1`
(storage pods), and five more — **every one with an empty `assignee`**, and every one's own
`relevance_he` (model-authored) already reading "אין אישור במקורות לקישור ישיר ומאומת בין הפטנט
לבין המוצר הספציפי" (no confirmed direct link in the sources) — an ungrounded row that nothing
upstream ever dropped.

Fix, two independent layers:

- **`agent/eoa/dossier/corpus.py`** (new): `patent_assignee_matches_vendor` (substring-either-way
  match on `entity_normalize.normalize_name_key`, so `"Elbit Systems Ltd"` matches vendor `"Elbit
  Systems"`) and `patent_relevance_he` — the single relevance rule: an **empty `assignees` list is
  an absolute gate, `None` unconditionally** (never falls through to a title/abstract check); among
  rows that DO carry an assignee, kept when that assignee matches the vendor/an alias, OR the
  product name itself (whole word, `_word_present`) appears in the title/abstract. `build_corpus`
  applies this once via `_filter_and_cap_patents`, **after** merging the DB-table match
  (`collect_patents`) and the live OPS/keyless-fallback query (`collect_patents_ops`) — one shared
  gate neither path can reintroduce the defect through — then caps the survivors at 15
  (`_PATENT_RELEVANCE_CAP`), assignee-matched rows ranked ahead of product-name-only matches
  (stable sort, so each rank group keeps its existing `publication_date DESC` order).
- **`agent/eoa/dossier/extract.py`** (new `_ground_patent_row`, replacing the old
  `_ground_generic_row` call for patents): a second, independent line of defense at
  extraction-grounding time — `relevance_he` on the persisted `DossierPatentRow` is now **entirely
  code-derived** (`corpus.patent_relevance_he`, reusing the same rule against the row's own
  `assignee` field and its cited source text), overwriting whatever text the model wrote; a row
  with neither a grounded applicant nor a product-name match is dropped outright, in case a model
  ever cites an (already relevance-filtered) registry patent but still invents its own
  `assignee`/`title` text that doesn't actually match.

`tests/unit/test_product_dossier_corpus.py` (new, `TestPatentRelevanceGate`):
`test_empty_assignee_row_is_dropped_even_with_product_name_in_title`,
`test_unrelated_assignee_and_no_product_name_is_dropped`,
`test_matching_assignee_is_kept_and_states_the_grounded_link`,
`test_unrelated_assignee_but_product_name_in_title_is_kept`,
`test_build_corpus_drops_empty_assignee_generic_keyword_patents` (the live 8-row shape, one
genuinely relevant row mixed in), `test_cap_is_15_and_assignee_matches_ranked_first`.
`tests/unit/test_product_dossier_extract.py` (new): `test_patent_row_with_matching_assignee_gets_
deterministic_relevance_he`, `test_patent_row_admitting_no_confirmed_link_is_dropped` (the live
`"אין אישור במקורות לקשר"` case verbatim), `test_patent_row_product_name_in_title_is_kept_without_
assignee_match`.

## 2. Deal identity: customer became a placeholder (`agent/eoa/dossier/diff.py`, `agent/eoa/dossier/extract.py`, schema)

The live bug: `id=2`'s three known deals each had a real (if unidentified) customer description —
e.g. `"מדינה באזור אסיה-פסיפיק (לא מזוהה)"` — while `id=3`'s re-extraction of the *same* three
deals lost that text entirely (`customer` came back `""`/placeholder). Deal identity already keyed
on `(normalized customer, amount, kind)` since PD-fix-2 — but a real customer string on one side and
an empty one on the other normalize to two *different* keys, so all three deals were reported as
`"עסקה חדשה מאז הסקירה הקודמת: — בהיקף ..."` (and printed the raw `"—"` placeholder as if it were a
customer name).

Fix:

- **`agent/eoa/dossier/diff.py`**: `_is_placeholder_customer` recognizes both an actually-empty/
  `None` customer and common literal placeholder text (`"—"`, `"-"`, `"לא ידוע"`, `"לא צוין"`,
  `"unknown"`, `"n/a"`, `"לא מזוהה"`, ...) — never a real, if partial, description like `"מדינה
  באזור אסיה-פסיפיק (לא מזוהה)"` (that names an actual region/qualifier). `_diff_deals` now keeps
  the existing exact `(customer_key, amount_key, kind)` lookup as the first try, but on a miss also
  checks a second index keyed on `(amount_key, kind)` alone, built from every previous deal
  regardless of its own customer — and accepts that fallback match **only when at least one side's
  customer is a placeholder** (current or the matched previous row), never merely because two real
  customer strings differ (a deliberate non-goal — two rows naming different real customers at the
  same amount+kind must still read as two deals). `_customer_display_he` replaces every `d.customer
  or '—'` call site in the "new deal"/"date added" sentences with `"לקוח לא צוין"` when the customer
  is missing/placeholder — the raw `"—"` is never printed into change text again.
- **`agent/eoa/dossier/extract.py`** (`_ground_deal_row`, new `_normalize_customer`): the root
  cause one level up — a placeholder string the model wrote literally into `customer` is normalized
  to `None` at grounding time, going forward, so a future rerun no longer produces the drifting
  placeholder text `diff.py` has to tolerate in the first place.
- **`agent/eoa/llm/schemas/product_dossier.py`**: `DealRow.customer` is now `str | None = None`
  (was `str = ""`) — the persisted/API value for an unknown customer is a real null, not a
  placeholder string baked into the data (item 4 below renders it).

`tests/unit/test_product_dossier_diff.py` (new): `test_customer_became_placeholder_not_reported_
as_new_deal` (the live case verbatim), `test_customer_was_placeholder_now_identified_not_reported_
as_new_deal` (symmetric), `test_new_deal_with_no_customer_never_prints_raw_dash`, `test_two_
different_real_customers_with_different_amounts_still_distinct` (fallback doesn't erase real
identity). `tests/unit/test_product_dossier_extract.py` (new): `test_deal_customer_placeholder_
dash_normalized_to_none`, `test_deal_customer_placeholder_hebrew_text_normalized_to_none`, `test_
deal_real_customer_name_is_kept_verbatim`.

## 3. Spec/performance parameter-name drift (`agent/eoa/dossier/diff.py`)

The live bug: the extraction model renamed the same parameter between runs —
`"ביצועי אופטיקה"` → `"ביצועי עומס אופטי במארז קומפקטי"` (and similarly `"יחס גודל-לביצועים (עומס
אופטי)"` on the performance side) — producing 5 false "פרמטר מפרט חדש"/"מדד ביצועים חדש" entries
under the old exact-`casefold()`-string matching.

Fix: `_find_matching_prev_row` (new, shared by `_diff_specifications` and `_diff_performance`) —
a previous row counts as the same parameter/metric when its NAME is a fuzzy (token-Jaccard >= 0.5)
match to the current name, **or** (independently) its VALUE is already effectively the same
(`_values_effectively_same`, >= 0.6 — PD-fix-2's existing helper, unchanged, now factored through a
shared `_token_jaccard`). Either signal alone is enough: the live `"ביצועי אופטיקה"` example itself
scores well under 0.5 on names (`1/6 ≈ 0.17`) but is caught by the VALUE side of the OR (identical
published figure); a moderate rewording that also changed its value is still caught by the NAME
side and correctly reports the value change instead of a fabricated "new parameter". The 0.5
name threshold is deliberately looser than the 0.6 value threshold — a name is a short, low-entropy
label where even a real rewording often shares under 60% of its tokens.

`tests/unit/test_product_dossier_diff.py` (new): `test_spec_renamed_parameter_same_value_not_
reported_as_new` (the live case verbatim), `test_spec_fuzzy_renamed_parameter_name_with_changed_
value_reports_value_change`, `test_spec_genuinely_new_parameter_neither_name_nor_value_match`,
`test_performance_renamed_metric_same_claimed_value_not_reported_as_new`, `test_performance_
genuinely_new_metric_neither_name_nor_value_match`.

## 4. Deals-table customer-cell rendering (`agent/eoa/dossier/report.py`)

The live bug: the customer cell rendered the raw `"—"` a model had written literally into
`customer` — the general `_cell()` placeholder check only ever catches `None`/`""`, not a
truthy-but-meaningless string like `"—"`.

Fix: with item 2's schema change (`customer: str | None`) and extraction-side normalization
(`_normalize_customer`), the persisted value is a real `None` going forward — `_deal_customer_cell`
(new, replacing the bare `_cell(r.customer)` call) renders `CUSTOMER_PLACEHOLDER_HE = "לא צוין"` for
it, deliberately distinct from the generic `PLACEHOLDER_HE` ("לא נמצא במקורות" — the deal row itself
IS grounded/cited; only the customer's identity is unknown, and "לא צוין" says that honestly without
implying the whole row is unsourced). The underlying data value stays `None` (never a baked-in
`"לא צוין"` string) so the API/UI layer — outside this round's file ownership — can render its own
placeholder for it independently.

`tests/unit/test_product_dossier_report.py` (new): `test_deal_customer_cell_placeholder_for_none`,
`test_deal_customer_cell_real_name_kept_verbatim`, `test_deals_table_customer_column_never_shows_
raw_dash`.

## 5. Per-topic time cap not actually enforced (`agent/eoa/search/deep_search.py`)

The question: the `maturity` topic took 17 minutes against `dossier.topic_time_cap_s = 600` (10
min) — is the deadline enforced inside `investigate()` (`deadline_s`), or only checked between
rounds?

Finding: `eoa.dossier.plan.run_plan` already forwards `deadline_s=cfg.topic_time_cap_s` correctly
into `investigate()`, and `investigate()`'s outer round loop already checks `budget.exhausted`
before starting a new round. The gap was one level deeper, inside `_act()`'s own per-round step
loop (`for _ in range(max_steps)`, `max_steps=12` by default): `budget.exhausted` WAS checked every
step — but once true, the code only appended a "budget exhausted, summarize now" nudge message to
the transcript and kept going, calling `chat()` again (plus whatever `search`/`read` tool calls the
model chose to make regardless) for every one of the remaining steps up to `max_steps`. The deadline
was a polite request the model could keep ignoring, not an enforced stop — exactly how a single
round blew a 600s cap out to 17 minutes (up to 11 more full LLM/tool round-trips after the deadline
had already passed).

Fix: a hard one-step grace period in `_act()` — the FIRST time `budget.exhausted` is found true, the
model gets exactly one more turn (with the nudge message) to call `finish`; if that turn doesn't
finish, the loop breaks for real on the very next check, rather than cycling through the rest of
`max_steps`. `investigate()`'s outer round loop then re-checks `budget.exhausted` at the top of its
own next iteration and stops the whole investigation, same as before this fix — only the previously-
unbounded inner step loop is now actually bounded by the deadline.

`tests/unit/test_deep_search_budget.py` (new): `test_act_stops_after_one_grace_step_past_deadline_
instead_of_cycling_to_max_steps` — a model that never calls `finish` gets at most 2 `chat()` calls
total (the step already in flight plus the one grace turn), never all the way to `max_steps=12`.
The existing `test_act_appends_exhaustion_message_to_transcript` (unchanged) still passes: it only
asserts the nudge message appears at least once, which the fix still does exactly once.

## Recomputed `what_changed_he` (id=2 → id=3, read-only, no rerun)

```
python -c "from eoa.dossier.diff import compute_diff; ..." over product_dossiers.data for id in (2, 3)
```

**7** sentences (was 12 in `id=3`'s own persisted, pre-fix `what_changed_he` — the 3 fabricated
"עסקה חדשה" deal entries are gone entirely; 2 of the false "new parameter"/"new metric" entries
correctly collapse into a single genuine value-change sentence about `'מספר חיישנים דיגיטליים'`
instead — net −5):

1. נתון מחיר חדש שפורסם: מעל 90 מיליון דולר (היקף חוזה (לא מחיר ליחידה)).
2. נתון מחיר חדש שפורסם: 270 מיליון דולר (היקף חוזה (לא מחיר ליחידה)).
3. שינוי בערך המפרט 'מספר חיישנים דיגיטליים': עד 9 חיישנים דיגיטליים המשלבים MWIR, VNIR ו-SWIR ->
   עד 9 חיישנים דיגיטליים.
4. פרמטר מפרט חדש שפורסם: כיסוי ספקטרלי = VNIR, SWIR, MWIR.
5. פרמטר מפרט חדש שפורסם: טווח תפעולי = Ultra-Long-Range (טווח ארוך במיוחד).
6. פרמטר מפרט חדש שפורסם: תנאי תפעול = יום, לילה וכל תנאי מזג אוויר (All Weather).
7. מדד ביצועים חדש שפורסם: טווח תפעולי = Ultra-Long-Range (טווח ארוך במיוחד).

The old (buggy) `what_changed_he` stored on `id=3`, for the record (12 items) — the three now-gone
fabricated deal entries:

```
עסקה חדשה מאז הסקירה הקודמת: — בהיקף כ-80 מיליון דולר (contract_award).
עסקה חדשה מאז הסקירה הקודמת: — בהיקף מעל 90 מיליון דולר (contract_award).
עסקה חדשה מאז הסקירה הקודמת: — בהיקף 270 מיליון דולר (contract_award).
```

— and the two now-collapsed false "new parameter"/"new metric" entries (both about the same
underlying fact, `id=2`'s `'מספר חיישנים דיגיטליים'` row, renamed twice over):

```
פרמטר מפרט חדש שפורסם: ביצועי עומס אופטי במארז קומפקטי = ביצועי מטע״ד בגודל 20 אינץ׳ בתוך מארז של 15 אינץ׳.
מדד ביצועים חדש שפורסם: יחס גודל-לביצועים (עומס אופטי) = ביצועי מטע״ד בגודל 20 אינץ׳ בתוך מארז 15 אינץ׳.
```

Confirmed against live DB inspection (read-only): `id=3`'s 8 patent rows all have `assignees = []`
and a `relevance_he` already admitting no confirmed link (item 1) — every one of the 8 would be
dropped by the new `patent_relevance_he` gate; `id=3`'s 3 deal rows all have `customer = ""` (item
2/4) where `id=2`'s matching rows had real (if unidentified) customer text.

## Tests

`tests/unit/test_product_dossier_corpus.py`: **33** (was 27, +6) — patent relevance gate
(`TestPatentRelevanceGate`).
`tests/unit/test_product_dossier_extract.py`: **29** (was 23, +6) — patent grounding relevance_he,
deal customer placeholder normalization.
`tests/unit/test_product_dossier_diff.py`: **29** (was 20, +9) — deal-identity placeholder
fallback, fuzzy spec/performance name matching.
`tests/unit/test_product_dossier_report.py`: **29** (was 26, +3) — deal customer cell.
`tests/unit/test_product_dossier_*.py` total: **169 passed**
(`pytest tests/unit/test_product_dossier_*.py -q`).

`tests/unit/test_deep_search_budget.py`: **14** (was 13, +1) — `_act` deadline enforcement.
Regression sweep, every `test_deep_search_*.py` file (anchors, answer_format, blocked_round5,
cloud_batch, mcp_tools, outcomes, provenance_links, reconcile_round4, round7, round8, round9,
budget): **240 passed** (226 + 14), confirming the `_act` fix does not change behavior for any
already-passing deep-search scenario.

`ruff check agent/eoa/dossier/corpus.py agent/eoa/dossier/diff.py agent/eoa/dossier/extract.py
agent/eoa/dossier/report.py agent/eoa/llm/schemas/product_dossier.py
agent/eoa/search/deep_search.py` — clean.

The live dossier (`product_dossiers.id=3`) was **not** rerun, per the task brief — every fix above
is verified either against unit fixtures reproducing the exact live shapes, or (items 1/2/3, deals/
patents/specs) by recomputing `compute_diff`/inspecting `id=2`/`id=3`'s real `data` JSON directly
(read-only, `DATABASE_URL`, port 5432 verified never 5433) — see the recomputed list above.
