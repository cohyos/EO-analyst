# CR Monthly — Unsupported-Claims Content Review

Date of review: 2026-09-08. Trigger: direct user feedback on
`output/reports/monthly_2026-09-30.md` — "ניכר זינוק", "ניכרת התפתחות", superlatives supported by
nothing, "מגמה חדשה" everywhere, items assigned to trends they do not fit. Quote from the feedback:
"there is a gap in the inference ability of whoever edits the report." This document records the
root causes, the fix per root cause, before/after sentence pairs, and the verification counts from
the rebuilt monthly/weekly reports.

Ownership boundary (per the task brief): `agent/eoa/report/trends.py`, `agent/eoa/report/deltas.py`,
the narrative post-processing in `agent/eoa/report/monthly.py`/`weekly.py` (not their table
builders), the new `agent/eoa/report/claims_gate.py`, `agent/eoa/llm/prompts/report_monthly.md` /
`report_weekly.md`, the monthly/weekly checks in `agent/eoa/qa/d6_daily_report.py`, and tests. Not
touched: `web/**`, `agent/eoa/pipeline/**`, `agent/eoa/patents/**`, `agent/eoa/search/**`,
`docx_builder.py`.

---

## 1. Root causes and fixes

| # | Root cause | File(s) | Fix |
|---|---|---|---|
| 1a | `_domain_surges_from_counts` labelled ANY domain with >=3 items "זינוק בכמות הפריטים" whenever there was no baseline (`avg == 0`, e.g. every domain on the very first monthly ever built, since a 4-week-baseline lookup naturally has nothing to compare a first corpus to) — the bare item-count floor was the *entire* gate, so 10/10 "trends" fired off a ~38-item corpus with zero comparison basis | `agent/eoa/report/trends.py` | Split into two honestly-distinct outcomes: **no baseline** → new `kind="domain_active"`, title "תחום פעיל החודש: {domain} — N פריטים מ-M מקורות (אין בסיס השוואה מחודש קודם)", gated only by the plain item floor. **real baseline** → `kind="domain_surge"` fires only when baseline avg >= 2/week **AND** ratio >= 2x **AND** N >= 5; title "עלייה בפעילות בתחום {domain}: N פריטים לעומת ממוצע K". A thin baseline/ratio/count below those three thresholds is dropped, not relabelled. |
| 1b | `_entity_clusters_from_rows` titled any 3 items about the same entity+domain "פעילות מוגברת סביב Entity" regardless of how many distinct outlets reported it — 3 items from one wire-service rewrite read exactly like 3 independent confirmations | `agent/eoa/report/trends.py` | Requires >=3 items **from >=2 distinct sources** (new `count(DISTINCT source_id)` in the SQL). Title now states the real counts: "ריכוז דיווחים: {entity} בתחום {domain} (N פריטים, M מקורות)". |
| 1c | Every trend on the monthly's very first-ever build was labelled "מגמה חדשה החודש" ("new trend this month") — technically true (`change="new"` fires whenever `strength_prev` has no match) but misleading: it reads as a claim about *this* month vs. a *previous* one, when there was no previous monthly report to compare against at all | `agent/eoa/report/monthly.py` | New `_has_previous_monthly_report(period_start)` (a plain existence check, independent of `collect_previous_monthly_trends`, which already returns `[]` in both the "no report" and "report exists but lacked this trend" cases and therefore can't disambiguate them on its own). `_render_trend_body` now takes `has_previous_report` and renders "לא נמדדה בחודש הקודם (אין דוח קודם)" instead of "מגמה חדשה החודש" when no previous report exists at all. |
| 1d | `market_convergence` fired on >=2 M&A/partnership events touching the same subdomain, with no check that they were actually *different* deals — 2-3 follow-up articles about the same acquisition (same two parties) read as "market-wide convergence" | `agent/eoa/report/trends.py` | Raised to >=3 events **and** requires >=2 distinct party sets among them (`_distinct_party_sets`, computed from each event's own `parties`, not the old flattened/deduped array). Three deals all involving the same two companies no longer qualifies. |
| 1e | No trend dict carried the numbers that justified its own claim — the drafting model had a Hebrew title but no explicit item/source count, baseline, or ratio to *write from*, so it had nothing to put in a sentence except its own inference | `agent/eoa/report/trends.py`, `agent/eoa/report/weekly.py` (`_format_trend_numbers_he`, reused by `monthly.py`) | Every trend dict (all 5 kinds) now carries `n_items`, `n_sources`, `baseline_avg`, `ratio` (`None` where not applicable, always present as keys). Surfaced in both `format_trends_block` (weekly) and `format_monthly_trends_block` (monthly) as a "נתונים: N פריטים, M מקורות, ..." field the model reads directly. |
| 2 | Trend narrative sentences cited items that did not belong to that trend's own evidence (an item that fit trend B ended up cited inside trend A's section) — membership was never enforced after the model wrote its own `cites` | `agent/eoa/report/trends.py` (SQL-level: domain queries restricted to `level IN ('red','orange','yellow')` and `item.domain == domain`; evidence capped at 8 most-relevant items per trend), `agent/eoa/report/weekly.py` (`strip_trend_out_of_scope_sentences`, reused by `monthly.py`) | Deterministic post-generation guard: matches each drafted trend section back to its `detect_trends` source entry by normalized title, then drops (not just flags) any sentence whose `cites` are not a subset of that trend's own evidence-item registry numbers. Logged as `{report_kind}.trend_sentence_out_of_scope`; the dropped count is persisted onto `reports.qa_report` for the new D6 check (see §3). |
| 3 | The model routinely wrote magnitude/novelty/drama words with **no supporting number anywhere in the sentence** — "ניכרת התעצמות דרמטית ברכש", "מסמנות קפיצת מדרגה בשוק ה-EO/IR", "מצביע בבירור על העדפה הולכת וגוברת" | New `agent/eoa/report/claims_gate.py` | Deterministic sentence-level gate applied to `bluf`, `exec_summary`, `analyst_note_he`, every trend section's `sentences`, and every domain section's `sentences` (never to deterministic code-authored scaffolding text, and never to `cites`): a sentence carrying a trigger word (ניכר/ניכרת, זינוק, דרמטי, משמעותי/ת/ים, תאוצה, עלייה חדה, קפיצת מדרגה, מצביע/ה בבירור, מסמן/ת, התעצמות, הולכת וגוברת, מגמה, יוצא/ת דופן, היסטורי/ת, ענק) with no quantity (digit, %, מיליון/מיליארד/אלף/אחוז, or an explicit "לעומת ממוצע" comparison) in the same sentence has the trigger phrase removed; if nothing evidentiary survives, the whole sentence is dropped. Citations are never touched. |
| 4 | Prompts never told the model to lead with numbers, restricted "מגמה" to real supplied trends, or to only cite a trend's own evidence | `agent/eoa/llm/prompts/report_monthly.md`, `report_weekly.md` | New iron rule: quantity before meaning, hedged language, the full forbidden-intensifier list, "מגמה" restricted to supplied trends described by their counts, trend citations restricted to that trend's own evidence, and the BLUF restricted to one fact + its number (not a market verdict). |
| 5 | No QA gate existed to catch either defect (3) or (2) after the fact | `agent/eoa/qa/d6_daily_report.py` | Two new monthly/weekly-only D6 checks: `unsupported_intensifier_count_zero` (rendered-Markdown backstop scanning every non-appendix sentence for a trigger word with no supporting quantity, citation markers stripped first so a `[3]` reference is never mistaken for a supporting number) and `{report_kind}_trend_sections_cite_only_member_items` (reads the persisted `trend_sentences_out_of_scope` count from `reports.qa_report`; 0 = clean). Both gated to monthly/weekly report files only (a daily report was never in scope for this feedback). |

---

## 2. Before / after — the three sentences named in the task

All three ran through `eoa.report.claims_gate.gate_text` directly (unit-tested in
`tests/unit/test_claims_gate.py`):

| Before | After | Outcome |
|---|---|---|
| "החודש ניכרת התעצמות דרמטית ברכש מערכות הגנה אווירית" | "החודש נרשמו דיווחים על רכש מערכות הגנה אווירית" | softened (exact rewrite specified by the task) |
| "מסמנות קפיצת מדרגה בשוק ה-EO/IR" | "בשוק ה-EO/IR" | softened (both trigger phrases removed; the remaining fragment still names the market segment, so it is not vacuous under the 3-content-token floor) |
| "החודש מצביע בבירור על העדפה הולכת וגוברת" | *(dropped)* | dropped — after removing "מצביע בבירור על"→"קשור ל" and "הולכת וגוברת", the residual ("החודש קשור להעדפה") falls under the content-token floor |

## 3. Before / after — trend titles (synthetic, matching the live corpus shape)

| Before (root cause 1a/1b) | After |
|---|---|
| "זינוק בכמות הפריטים בתחום מערכות ראייה ממוחשבת" (fired off a bare 3-item floor, no baseline at all — the live 2026-09-30 monthly's actual failure mode, ~38-item corpus, first-ever monthly, 10/10 trends fired this way) | "תחום פעיל החודש: מערכות ראייה ממוחשבת — 3 פריטים מ-2 מקורות (אין בסיס השוואה מחודש קודם)" |
| "מגמה: פעילות מוגברת סביב Elbit Systems בתחום פודים אוויריים" (3 items, source count never checked) | "ריכוז דיווחים: Elbit Systems בתחום פודים אוויריים (3 פריטים, 2 מקורות)" — or dropped entirely if the 3 items trace to a single outlet |
| "מגמה חדשה החודש." (rendered for every trend on the corpus's first-ever monthly report) | "לא נמדדה בחודש הקודם (אין דוח קודם)." |

## 4. Rule table (claims_gate trigger phrases → quantity requirement)

| Trigger phrase(s) | Requires in the SAME sentence | If missing |
|---|---|---|
| ניכר/ניכרת, זינוק, דרמטי, משמעותי/ת/ים, תאוצה, עלייה חדה, קפיצת מדרגה, מצביע/ה בבירור, מסמן/ת, התעצמות, הולכת וגוברת, מגמה, יוצא/ת דופן, היסטורי/ת, ענק | a digit, `%`, מיליון/מיליארד/אלף/אחוז(ים), "פי N", or an explicit "לעומת (ה)ממוצע" comparison | phrase removed (specific neutral rewrite for the "ניכר.. התעצמות.. ב" pattern, deletion otherwise); sentence dropped entirely if nothing evidentiary survives the removal |

## 5. Verification

### 5.1 Unit tests (all new/updated; run with `runtime/eoa.env` loaded)

| Suite | Result |
|---|---|
| `tests/unit/test_claims_gate.py` | 13 passed — the three named fixtures, quantity-present/no-trigger cases, `Sentence`/`AnalystNote` object-level wiring, `apply_claims_gate` end-to-end |
| `tests/unit/test_d6_cr_monthly.py` | 8 passed — the two new D6 checks (pass/fail/not-checked/monthly-vs-weekly naming), including the citation-marker-digit false-positive fix (a rendered `[1]` must never count as a "supporting quantity") |
| `tests/unit/test_report_weekly_monthly.py` | 27 passed (309s — real, unmocked optional sections in `build_weekly`/`build_monthly` hit the live DB; confirmed identical runtime on an unmodified-HEAD baseline worktree, i.e. pre-existing test cost, not a regression) — rewritten `trends.py` detector tests (source-diversity floor, baseline/ratio/count triple gate, `domain_active`/`domain_surge` split, convergence party-set diversity), new `strip_trend_out_of_scope_sentences` tests |
| `tests/unit/test_monthly_round5.py` | 28 passed — new `_render_trend_body(has_previous_report=False)` case plus updated `gone`/`stronger`/`new` call sites |
| `test_qa_round5.py` + `test_qa_score.py` + `test_report_round3_d6.py` + `test_claims_gate.py` + `test_d6_cr_monthly.py` + `test_report_editing_round14.py` + `test_round6_entities.py` (combined) | 211 passed, 1 failed — the failure (`TestD1.test_all_clean_items_score_100`, a `populated_for_recent_in_scope` count assertion) is in `eoa/qa/d1_classify.py` (not owned by this task) and reproduces identically on an unmodified-HEAD baseline worktree — pre-existing, unrelated |

Ruff: `agent/eoa/report/` and `agent/eoa/qa/` — clean (`All checks passed!`).

### 5.2 EOA_PIPELINE=1 live rebuild (`build_monthly(period_end=date(2026,9,30))` + `build_weekly()`)

The first live attempt caught a real bug this task's unit tests couldn't (by design — `detect_trends`'s SQL helpers are intentionally untested against a real DB, only their pure-Python `_*_from_rows` counterparts are): `_convergence_rows`'s new per-event `array_agg(parties)` raised `psycopg.errors.ArraySubscriptError: cannot accumulate arrays of different dimensionality` — Postgres reports a zero-element `text[]` as 0-dimensional, which cannot be `array_agg`'d alongside a populated 1-D array. Fixed by aggregating via `jsonb_agg(coalesce(to_jsonb(parties), '[]'::jsonb))` instead (no dimension constraint; psycopg decodes each element back into a plain Python list). Confirmed against live data post-fix: `_convergence_rows` returned 3 real subdomains, correctly kept one 3-distinct-party-set "atr" convergence and correctly dropped a 3-events-same-two-parties "uav_gimbals" group (exactly the item-1(d) root cause).

<!-- FILL-IN once the live rebuild (running as of this edit) completes: report ids, unsupported-
intensifier count, trend-title-carries-counts check, "מגמה חדשה החודש" absence check, and
trend_sentences_out_of_scope count for both the monthly and weekly reports. -->
