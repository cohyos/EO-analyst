# REPORT-REDUNDANCY — Cross-Section Restatement Review

Date of review: 2026-09-08. Trigger: direct user feedback on the daily report as shown on the
morning page: "the report repeats the information overview needlessly; the repetition does not
advance the consumer of the information."

Ownership boundary (per the task brief): new `agent/eoa/report/redundancy.py`; its wiring in
`agent/eoa/report/daily.py` / `weekly.py` / `monthly.py` (narrative assembly only — no table
builders touched); `agent/eoa/llm/prompts/report_daily.md` / `report_weekly.md` /
`report_monthly.md` (additive rules only); `agent/eoa/report/deltas.py`'s "new items" list; and
tests. Not touched: `docx_builder.py`, `textnorm.py`, `product_line.py`, `bd_territory.py`,
`claims_gate.py`, `tenders/**`, `patents/**`, `web/**`.

---

## 1. Measurement — before

Method: `eoa.report.redundancy.is_redundant_text`'s own similarity test (token-set Jaccard >= 0.6
OR shared distinctive number + shared organisation token, distinctive = not a bare calendar year
and not a generic single-digit counter) run pairwise across every sentence extracted from
different labeled sections of the three live reports (BLUF, exec summary, analyst note, domain
review, "מה השתנה", deep-search context, and the "so-what" column of every table — "מה זה אומר" /
"נימוק" / "ראיה" / "אינדיקטור"), excluding pairs within the same section. A one-off measurement
script (not shipped — pure counting, no fixes) was used to produce these counts; the shipped
`is_redundant_text`/`apply_redundancy_pass` unit tests in
`tests/unit/test_report_redundancy.py` reuse the exact sentence pairs found here as fixtures.

| Report | Sentences analyzed | Jaccard >= 0.6 pairs | Number+org pairs (any Jaccard) | Total flagged (union, excl. exact structural boilerplate) |
|---|---|---|---|---|
| `output/reports/daily_2026-09-08.md` | 73 | 0 | 1 | 1 |
| `output/reports/weekly_2026-09-07.md` | 203 | 4 | 17 | 20 |
| `output/reports/monthly_2026-09-30.md` | 235 | 51 | 40 | 44 (+45 exact-identical structural boilerplate — "לא נמדדה בחודש הקודם (אין דוח קודם)" repeated once per trend subsection, a distinct phenomenon from narrative restatement, counted separately) |

The daily report was already close to clean: `eoa.report.style.dedupe_exact_sentences_across_sections`
(landed round-14, before this review) already drops byte-identical repeats across
bluf/exec_summary/sections/trends/outlook, which is why the daily's Jaccard>=0.6 count is 0 — the
near-duplicate (sub-1.0 Jaccard, same-fact-different-wording) case that pass does not cover is
exactly what this review's redundancy pass adds. Weekly and monthly, with far more sections per
report (trend paragraphs *and* domain-review sections *and* deep-search *and* several table
"so-what" columns all narrating overlapping facts), show the restatement pattern clearly.

### Worst 10 (by similarity), across all three reports

1. **weekly, Jaccard=1.00** — "מבט קדימה" vs. "מעקב אינדיקטורים" `[אינדיקטור]`: byte-identical
   sentence ("נראה שחדירת אנדוריל לשוק הישראלי תאלץ תעשיות מקומיות להאיץ שיתופי פעולה בתחום הפיקוד
   המבוזר כדי למנוע זליגת פרויקטים אסטרטגיים לחברות זרות.") in both. *(Structural: the same
   `OutlookIndicator` is rendered once as prose and once as a freshly-opened tracking row — out of
   this review's scope, see below.)*
2. **weekly, Jaccard=1.00** — same pattern, קלע דוד/אסטוניה indicator.
3. **weekly, Jaccard=1.00** — same pattern, אמריקן ריינמטל/נגמ"ש indicator.
4. **weekly, Jaccard=0.68** — "חקירות עומק" vs. "תעשייה ישראלית" `[מה זה אומר]`: the Ophir
   MWIR-zoom so-what sentence, near-verbatim in both (trimmed differently at the end).
5. **monthly, Jaccard=0.68** — "מגמות החודש" (בינה חזותית) vs. "סקירה לפי תחום" (בינה חזותית): the
   InfraPatch 86%-100% vulnerability fact, reworded.
6. **monthly, Jaccard=0.68** — same Ophir pair as #4 (monthly reuses the same investigation).
7. **monthly, Jaccard=0.62** — "מגמות החודש" (C-UAS) vs. "סקירה לפי תחום" (C-UAS): the Dutch
   ReDrone order, restated with the supplier named as "אלביט" in one place and "אלישרא" (its
   EW/SIGINT division that actually built ReDrone) in the other — same fact, same 6-unit order.
8. **daily, number+org match (Jaccard=0.24)** — "חקירות עומק" investigation question vs. "תחזיות
   מכרזים" `[נימוק]`: both anchor on the same $464.8M AeroVironment LOCUST X3 figure.
9. **monthly, Jaccard=0.54, number+org** — Saab Giraffe 1X / UVision $50M pair, "מגמות החודש" vs.
   "סקירה לפי תחום" (C-UAS).
10. **monthly, Jaccard=0.46, number+org** — Elbit/Serbia 51% joint-plant fact, "ריכוז דיווחים:
    Elbit" vs. "סקירה לפי תחום" (Airborne Pods & Payloads).

---

## 2. Fix

| # | What | File(s) |
|---|---|---|
| 1 | New deterministic (no LLM) cross-section pass: word-level Jaccard >= 0.6 OR shared distinctive number + shared org — same test used for the measurement above. Walks a draft in priority order **BLUF > exec summary > trends (weekly/monthly) > domain review (`sections`) > analyst note**; a lower-priority `Sentence` that restates one already kept in a higher-priority section is dropped, its `cites` merged into the surviving sentence. A `StructuredSection`/`WeeklyTrendSection`/`MonthlyTrendSection` emptied out entirely gets one deterministic pointer sentence ("פורט בתקציר המנהלים.") carrying the union of its own original citations (never an empty section, never breaks `MonthlyTrendSection`'s ">=1 sentence unless change='gone'" validator). Every drop is logged as `report.redundant_sentence_dropped` with the dropped/kept section pair. | new `agent/eoa/report/redundancy.py` (`apply_redundancy_pass`) |
| 2 | Wired in after drafting AND after the claims gate (weekly/monthly: right after `apply_claims_gate`; daily: right after `dedupe_exact_sentences_across_sections`, since the daily report has no claims gate). Deep-search `key_facts` bullets are filtered against the pass's final kept-sentence pool (`filter_facts_against_narrative`) right after, before rendering — "render only facts absent from the narrative." | `agent/eoa/report/daily.py`, `weekly.py`, `monthly.py` |
| 3 | "מה השתנה"'s new-items bullet list excludes an item already cited in BLUF/exec summary (by citation-number overlap — `eoa.report.redundancy.narrative_citation_numbers`, computed from the post-redundancy-pass draft), replacing it with a trailing "(עוד N פריטים חדשים כבר מוזכרים בתקציר המנהלים/שורה תחתונה.)" count line. Monthly has no "מה השתנה" section (it tracks change at the trend level instead), so this wiring is daily+weekly only. | `agent/eoa/report/deltas.py` (`render_delta_section_he`, `delta_extra_section` — new optional `narrative_cites` parameter, backward compatible) |
| 4 | Additive prompt rule (new bullet/item in "כללי כתיבה"): "כל עובדה מופיעה פעם אחת בדוח" — `exec_summary` adds the so-what across items, a domain-review/trend section adds detail the summary doesn't have, `analyst_note_he` adds judgement only; never restate a BLUF sentence in any other field; never open a section by re-summarizing the whole report. | `agent/eoa/llm/prompts/report_daily.md`, `report_weekly.md`, `report_monthly.md` |
| 5 | Unit tests using real sentence pairs pulled from the three live reports measured above, plus full-draft integration tests. | new `tests/unit/test_report_redundancy.py`; additive cases in `tests/unit/test_report_deltas_round5.py` |

### Not fixed (out of scope, flagged for the lead)

The weekly's #1-3 above (Jaccard=1.00, "מבט קדימה" vs. "מעקב אינדיקטורים") is a *structural*
duplication, not a drafting one: `eoa.report.indicators.build_indicator_watchlist_section` turns
each of this issue's own `draft.outlook` items into a freshly-opened tracked indicator row in the
SAME report that first wrote it as outlook prose — two code paths reading the same underlying
`OutlookIndicator.text_he`, not two independently-drafted restatements of one fact. The task's fix
scope (BLUF/exec summary/domain review/analyst note/"מה השתנה"/deep-search) does not name
`outlook`/indicators, and `eoa.report.indicators` is not in this review's ownership boundary —
left as-is, worth a follow-up ticket if the lead wants it addressed.

---

## 3. Rebuild — before / after

`EOA_PIPELINE=1`, `build_daily(force=True)` (via `eo run report`), daily report only, per
instructions (weekly/monthly rebuild left to the lead).

**Lead completion (2026-09-08 21:40):** the agent's daily rebuild never ran; the lead rebuilt `daily_2026-09-08` with `EOA_PIPELINE=1 build_daily(force=True)` (149 s). Measurement on the new file with the same method: 148 sentences, **0 cross-section pairs at Jaccard >= 0.6** (was 1 flagged pair before), and "מה השתנה" now lists only the new item not already cited above with the note "(עוד 1 פריטים חדשים כבר מוזכרים בתקציר המנהלים/שורה תחתונה.)". Weekly/monthly rebuilt by the lead in the next lane.
