# CR Editing — Line-by-Line Hebrew Copy-Edit Review

Date of review: 2026-09-07. Reviewer: independent editing/readability pass (Claude, general-purpose
agent), read as a Hebrew copy editor for a defence-industry BD executive. Complements
`docs/qa/content_review/CR-factcheck.md` (a prior, separate pass on factual accuracy) — this
document is about **editing and presentation quality**, not whether a claim is true.

Scope read line-by-line: `output/reports/daily_2026-09-07.md`, `weekly_2026-09-07.md`,
`monthly_2026-09-30.md`, `bd_il_2026-09-07.md`, `bd_us_2026-09-07.md`, both
`patent_survey_*_2026-09-07.md`, `pl_mws_eo_2026-09-07.md`, `pl_targeting_pods_2026-09-07.md`, plus
one rendered HTML/DOCX pair (weekly) to judge presentation. Other `bd_<territory>` reports and
`pl_<line>` reports sampled via targeted grep for the same defect signatures found in the read-in-
full reports (counts below cover the full `output/reports/*2026-09-0[6-7]*`/`*2026-09-30*` corpus,
not only the exhaustively-read subset).

Ownership boundary (per the task brief): fixes below only ever touch
`agent/eoa/report/docx_builder.py`, other `agent/eoa/report/*.py` (never `product_line.py`'s patent
dedupe), and the wording rules in `agent/eoa/llm/prompts/report_*.md` /
`agent/eoa/llm/prompts/analyze.md` / `agent/eoa/llm/prompts/tech_watch_so_what.md`. Defects whose
fix lives in `agent/eoa/pipeline/analyze.py`, `agent/eoa/patents/**`, `agent/eoa/tenders/**`,
`agent/eoa/payloads/**`, `agent/eoa/mcp_servers/**`, or `web/**` are catalogued but **not** fixed —
listed under "Remaining" with the owning module named. No data rows were touched in the DB.

---

## 1. Defect catalogue, by category

Counts are exact (`re`/`str.count` scans) across the 9 named reports plus the wider corpus where
noted; "sampled" line numbers are one representative instance, not every occurrence.

| # | Category | Count | Representative example (file:line) |
|---|---|---|---|
| 1 | Bidi-isolate control characters (U+2066/U+2069, "LRI…PDI") rendered as visible glyphs around every English/number token in investigation text | 751 chars across daily/weekly/monthly + 180 in the two patent surveys (patents out of scope, see §3) | `daily_2026-09-07.md:26` — "⁦90 ⁩הימים האחרונים (נכון ליוני ⁦2026⁩)" |
| 2 | Stray backslash before a quote (`\"`), an unescaped JSON/string-escape leak, most often inside a Hebrew acronym (כטב\"מים instead of כטב"מים) | 21 (daily ×5, weekly ×8, monthly ×8) | `daily_2026-09-07.md:29` — "לנטרול כטב\\"מים" |
| 3 | Stray space before closing sentence punctuation after a citation bracket (`[1, 5, 6] .`) | pervasive wherever #1/#2 occur (same root: LRI/PDI padding a citation list) | `daily_2026-09-07.md:29` — "[⁦1, 5, 6⁩] ." |
| 4 | English taxonomy slug leaked into a Hebrew heading/cell ("...בתחום out_of_scope", raw `domain`/`level` key in a table cell) | 23 in weekly's own trend-delta list + 2 more table cells (product-line "תחום"/"רמה" columns) | `weekly_2026-09-07.md:38` — "פעילות מוגברת סביב Europe בתחום out_of_scope"; `pl_targeting_pods_2026-09-07.md:42` — `תחום` cell literally `airborne_pods`, `רמה` cell literally `orange` |
| 5 | Internal "[item N]" data-labelling artifact leaking into reader-facing table text | 9 (all in daily's tender-forecast "נימוק" column) | `daily_2026-09-07.md:66` — "...הוענק לחברת AeroVironment עבור מערכת Locust X3 [item 10][item 47]" |
| 6 | Table cell hard-truncated mid-sentence at a fixed char count, ending in a bare "…" with no pointer to more detail | every row of the tenders-forecast table >200 chars (7 of 7 rows in daily) | `daily_2026-09-07.md:70` — "...מטוסי הקרב…" (cut off mid-clause) |
| 7 | Table >6 columns | 1 in `bd_territory.platform_events_table` (7 cols, all bd_\* reports) confirmed fixable; 1 more in `payloads.report_section` (מחירי ייחוס, 7 cols, out of scope); patent-survey tables (7 cols ×3 tables, out of scope) | `bd_il_2026-09-07.md:97` — "תאריך \| פלטפורמה/תוכנית \| רוכש \| ספק \| סכום \| צורך EO/IR נגזר \| מקור" |
| 8 | Number/gender-agreement error: "1 שורות כבר הופיעו" (שורה is feminine singular; template only ever produced the plural form) | 4 occurrences (bd_il ×1, monthly ×2, one patent survey ×1) | `bd_il_2026-09-07.md:95` — "1 שורות כבר הופיעו בטבלאות קודמות בדוח ולא חזרו כאן." |
| 9 | Unescaped literal `\|` in a scraped page title breaking a markdown table row into extra, misaligned columns | confirmed in the sources appendix of every markdown report (any title of the common "Headline \| Site Name" shape) | `bd_il_2026-09-07.md:152` — title "…מפעל פולקסווגן נמכר לרפאל" row shows an extra stray "היום" cell before the real source-name cell |
| 10 | Exec-summary placeholder ("אין תקציר לתקופה זו.") shown directly above a system note that *does* have real content — self-contradicting | pl_mws_eo, pl_targeting_pods (both first-run product-line reports) | `pl_mws_eo_2026-09-07.md:7-9` — "אין תקציר לתקופה זו." immediately followed by "...קיים תוכן רלוונטי בטבלאות הדוח: 3 פטנטים" |
| 11 | Invented/hallucinated Hebrew transliteration of a foreign company name not present in the source (and a second, misspelled invented English name) | 1 confirmed instance, reused verbatim across 3 report dates (monthly, weekly ×2 dates) | `monthly_2026-09-30.md:668` — "פלנטריוניקס (Planar Optics) וטלסקופיקס (Telescopeics)" — the transliteration doesn't even phonetically match "Planar Optics", and "Telescopeics" is not a real word |
| 12 | Inconsistent system/entity naming (underscore-style internal codename vs. natural-language name for the same system) | recurring across weekly/monthly/bd_us | `weekly_2026-09-07.md:119,198,201` — "Halo_Shield" vs. "AV_Halo COMMAND" (never "Halo Shield") |
| 13 | Mixed-language / duplicated-in-two-languages table cell (buyer or customer field carrying both the Hebrew translation and the original English side by side) | several in the "רכש ופלטפורמות" tables (bd_il, weekly) | `bd_il_2026-09-07.md:99` — רוכש/ספק cell literally "defense ministry of a NATO member state" next to "Israeli Ministry of Defense" |
| 14 | Verbosely long, low-signal "מה השתנה" trend-delta dump (52 "נעלמה" lines in one weekly report, no prioritization, BLUF-violating order) | 1 report section, 52 lines | `weekly_2026-09-07.md:17-89` |
| 15 | Empty appendix table (headers with zero data rows, no "no sources" fallback text) | pl_mws_eo (first-run product-line report with items but zero appendix-registered sources) | `pl_mws_eo_2026-09-07.md:33-35` |
| 16 | Hollow table column (every row "—" for an entire column, no note explaining why) | patent survey "בעלים"/"תאריך פרסום" columns (out of scope, `agent/eoa/patents/survey.py`) | `pl_mws_eo_2026-09-07.md:23-25` |

---

## 2. Fixes made (deterministic, renderer/builder layer)

All in `agent/eoa/report/` (mine per the task's ownership list). Each entry: defect # from §1 →
file:function → what changed.

- **#1, #2, #3 → `agent/eoa/report/textnorm.py`** (new functions `strip_bidi_isolates`,
  `unescape_stray_backslash_quotes`, `collapse_space_before_closing_punctuation`, plus the reusable
  `trim_at_word_boundary`), wired into `normalize_hebrew_punctuation`'s pipeline (now 6 passes, was
  3). Applied at the point where investigation text enters the report:
  `agent/eoa/report/daily.py::collect_deep_search` now normalizes `answer_he`, `contradictions_he`,
  `question`, and every `key_facts` entry — one fix point, inherited automatically by
  daily/weekly/monthly's docx/md/html renderers (all three read the same `entry["answer_he"]`).
  - Before: `לנטרול כטב\"מים [⁦1, 5, 6⁩] .`
  - After: `לנטרול כטב״מים [1, 5, 6].`
- **#4 → `agent/eoa/report/weekly.py`, `trends.py`, `bd_territory.py` (`_domain_label`/
  `_subdomain_label`)**: these three modules each carried a stale, pre-Q3-15 copy of
  `_domain_label` that fell back to the raw `domain` string when the taxonomy had no entry for it
  (`eoa.report.daily`'s own copy was already fixed for this in an earlier round — Q3-15 — but the
  duplicate copies in the other three modules were never updated). Fixed the same way: a real
  Hebrew fallback (`"תחומים נוספים"`), never the raw slug. `trends._subdomain_label` had the
  identical bug in its own final fallback line despite its docstring already naming the defect.
  - Before: "פעילות מוגברת סביב Europe בתחום out_of_scope"
  - After: "פעילות מוגברת סביב Europe בתחום תחומים נוספים"
  - **`agent/eoa/report/product_line.py::market_items_table`**: added local `_domain_label`/
    `_level_label` (same pattern) — this table rendered `it.get("domain")`/`it.get("level")` raw,
    with no translation attempted at all (not even a stale copy of the helper).
  - **`agent/eoa/report/deltas.py::_label_raw_subdomain_keys`** (found via the rebuild proof in §5,
    below — see that section): the "מה השתנה מאז הדוח הקודם" trend-delta list re-displays a
    *previous* report's already-persisted `title_he` verbatim (`compute_trend_deltas`'s
    `prev_by_title`), so the `_domain_label` fix above only prevents the leak in a trend title
    generated *after* the fix — a title stored before it still needs repairing at render time. This
    module already had exactly that repair mechanism for the analogous raw-*subdomain*-key bug
    (`בתת-התחום "atr"`); extended the same function to also repair an unquoted raw *domain* slug
    (`בתחום out_of_scope`), which is why the domain-slug case wasn't caught by grep alone until the
    rebuild surfaced it live.
- **#5, #6 → `agent/eoa/report/daily.py::_tenders_forecast_table`**: strips `[item N]` markers
  (and an enclosing empty `(...)` if the marker sat alone in one) via regex before display; replaced
  the `rationale[:199] + "…"` hard character-count cut with `textnorm.trim_at_word_boundary(...,
  200, suffix=" … (פירוט במקורות)")` — cuts at the nearest word boundary, never mid-word/mid-marker,
  and points the reader at the row's own "מקורות" citation column instead of a bare ellipsis.
  - Before: "...שמטוסי הקרב…" / "...הוענק לחברת AeroVironment [item 10][item 47]"
  - After: "...שמטוסי הקרב יזדקקו לפוד תצפית ... (פירוט במקורות)" / "...הוענק לחברת AeroVironment"
    (marker gone, no dangling parens)
- **#6 (general case), missing captions → `agent/eoa/report/docx_builder.py`**: new
  `_trim_cell_text`/`_trim_row_cells` (same word-boundary trim, 220-char cap) wired into the
  **shared** table-row path all three renderers use (`_add_generic_table_body` for docx,
  `_render_one_table_md`, `_render_one_table_html`) — covers every generic table project-wide
  (tenders, tech-watch, bd_territory, monthly/weekly deterministic tables), not only the one table
  fixed by name above. New `_table_caption_text`: a short caption line before every table — the
  author-provided `note_he` when present, otherwise a synthesized "(N שורות)" row-count line, so a
  reader always sees a table's size at a glance (defect: "missing table captions").
- **#7 → `agent/eoa/report/bd_territory.py::platform_events_table`**: merged the separate
  "רוכש"/"ספק" columns into one "רוכש / ספק" cell (7 → 6 columns), the same buyer/vendor-in-one-cell
  convention the events table already uses elsewhere in this report family.
- **#8 → `agent/eoa/report/docx_builder.py::dedupe_rows_across_tables`**: the "N שורות כבר הופיעו"
  note now uses correct singular Hebrew phrasing ("שורה אחת כבר הופיעה בטבלה קודמת...") when exactly
  one row was dropped, the existing plural form otherwise.
- **#9 → `agent/eoa/report/docx_builder.py`**: new `_escape_md_table_cell` (escapes a literal `|`
  to `\|`, collapses an embedded newline to a space), applied everywhere a markdown table cell is
  built from scraped/free text: `_md_cell` (the shared generic-table cell renderer), the sources
  appendix row builder, and the business-events table row builder.
- **#10 → `agent/eoa/report/docx_builder.py`**: new `_exec_summary_display_text(draft)` — the
  "אין תקציר לתקופה זו." placeholder now renders only when there is truly nothing to say in that
  slot (no exec-summary sentence **and** no system note either); when a system note exists, it
  alone carries the slot's content, so the two can no longer contradict each other. Applied
  identically in the docx/md/html renderers.
- **Sentence-level dedupe across sections → `agent/eoa/report/style.py::dedupe_exact_sentences_across_sections`**
  (new, additive function, wired into `daily.py`/`weekly.py`/`monthly.py` right after the existing
  `apply_style_guard` call): drops a later **exact**-duplicate `Sentence` (byte-identical
  normalized `text_he`) that repeats one already kept earlier, in reading order `bluf` →
  `exec_summary` → `sections` → `trends` → `outlook`. Deliberately narrower than
  `apply_style_guard`'s existing detection-only duplicate check (which the module's own docstring
  explains was left detection-only over citation-orphaning risk): this only ever *drops* a whole
  `Sentence` object with byte-identical text, never rewrites/splits/merges one, so a claim's
  `cites` can never be orphaned. Never empties a section/trend/outlook down to zero sentences (the
  first sentence of any single collection is always kept even if it repeats an earlier one) — this
  explicitly avoids trading defect #14-adjacent "repeated sentence" for the worse "empty section"
  defect.

## 3. Fixed via prompt wording (not code)

- **`agent/eoa/llm/prompts/analyze.md`** (`so_what_he` rule, root cause of defect #11): added an
  explicit ban on inventing a competitor/company/product name not present in the source item, and a
  rule that any foreign company/product name is always kept in Latin script — never transliterated
  to Hebrew, invented or otherwise. The existing rule ("name the specific competitor/program/tech
  that wins or loses") was, on the evidence, exactly what pushed the model to invent "פלנטריוניקס"
  and "טלסקופיקס" when no real competitor was named in its source.
- **`agent/eoa/llm/prompts/tech_watch_so_what.md`**: same two rules added (no invented
  company/competitor names; any real company name stays in Latin script) for the tech-watch
  so-what field, which shares the same failure mode.
- `report_daily.md`/`report_weekly.md` were checked against `report_monthly.md`/
  `report_bd_territory.md`'s explicit filler-phrase list (the task named "no filler openers" as a
  wording rule to add) — both already carry the identical list (verified, no change needed there).

## 4. Unit tests

`tests/unit/test_report_editing_round14.py` (36 tests, all passing) — one test per fix above:
`textnorm` pass functions individually and composed (matching the exact live daily-report defect
string), `daily.collect_deep_search` normalization (DB mocked), `_tenders_forecast_table`
marker-stripping and word-boundary trim, `_domain_label`/`_subdomain_label` never-leak-raw-slug
across all four modules, `platform_events_table`'s 6-column cap, `docx_builder`'s cell trim/caption/
pipe-escape/singular-plural note, `style.dedupe_exact_sentences_across_sections`'s three behaviors
(drops a real repeat, protects a single-sentence collection from emptying, no-ops when nothing
repeats), and `deltas._label_raw_subdomain_keys`'s domain-slug repair (added after the rebuild in
§5 surfaced the gap, reproducing the exact live string found there). Also updated 5 pre-existing
tests that asserted the **old, buggy** behavior as correct (now asserting the fixed behavior):
`test_report_daily.py::test_tenders_forecast_table_shape_and_rationale_cap`
(+ a new `..._strips_internal_item_markers` test), `test_report_bd_territory.py::test_build_bd_territory_renders_expected_tables`
(6-column header), `test_renderer_round5.py::test_dedupe_rows_across_tables_treats_dict_rows_like_list_rows`
and `test_table_dedupe_round4.py::test_repeated_rows_dropped_with_note` (both singular note),
`test_product_lines.py::test_market_items_table_renders_rows` (translated domain/level).

`ruff check` clean on every changed file. Full existing suites re-run and green after the changes:
`test_report_daily.py` (29), `test_report_bd_territory.py` (49), `test_report_weekly_monthly.py` +
`test_weekly_meta_round4.py` + `test_reports_round4.py` + `test_reports_round9.py` (combined),
`test_docx_builder.py`, `test_report_style_round4b.py`, `test_table_dedupe_round4.py`,
`test_renderer_round5.py`, `test_md_body_blocks_round4.py`, `test_bluf_round5.py`,
`test_product_lines.py`, `test_reports_round10/11/12/13.py`, `test_monthly_round5.py`,
`test_weekly_round6.py`, `test_round6_forecast.py`, `test_report_round3_d6.py`,
`test_reports_list_round4.py`.

## 5. Rebuild proof

`build_weekly(period_end=2026-09-07)` re-run (`EOA_PIPELINE=1`) against the live DB after every
fix in §2 landed. Result: QA passed (`qa_passed=True`, 0 errors), `report_id=146`, all three
outputs (`.md`/`.html`/`.docx`) regenerated. Original file backed up before the rebuild; grep-based
before/after counts on the same defect signatures used in §1:

| Metric | Before | After |
|---|---|---|
| Bidi-isolate chars (LRI/PDI) | 266 | **0** |
| Stray `\"` backslash-quote | 8 | **0** |
| Stray space before closing punctuation (`] .`) | 4 | **0** |
| `[item N]` leak | 0 (weekly's own forecast table was already clean) | 0 |
| Table >6 columns (unescaped-pipe-aware count) | 0 | 0 |
| Raw `domain` slug in "מה השתנה" delta list ("בתחום out_of_scope") | 23 | **23 (unchanged by the first rebuild — see below)** |

The first rebuild's `_domain_label` fix (§2) only prevents the leak in a **newly generated** trend
title — it re-confirmed 23 unchanged occurrences of "בתחום out_of_scope" in the "מה השתנה" section,
because that section re-displays a **previous** report's already-persisted `title_he` string
verbatim (`eoa.report.deltas.compute_trend_deltas`), not a freshly generated one. This is exactly
why a rebuild-to-prove-the-effect step is valuable: it caught a gap the static grep-based catalogue
in §1 could not (grep on the *already-published* file only shows what a **past** bug produced, not
whether a **live** code path still reproduces it going forward). Traced to
`eoa.report.deltas._label_raw_subdomain_keys`, which already had this exact repair mechanism for
the analogous raw-subdomain-slug case (`בתת-התחום "atr"`) but not for the unquoted raw-domain-slug
case; extended it the same way (§2) and added
`tests/unit/test_report_editing_round14.py::test_label_raw_subdomain_keys_repairs_a_persisted_raw_domain_slug`,
which reproduces the exact live string found in the rebuilt file
("מגמה: פעילות מוגברת סביב Europe בתחום out_of_scope") and asserts it repairs correctly. A second
full rebuild to re-confirm end-to-end was not run (each rebuild is a ~12-minute LLM generation
call, and the delta list's "previous report" comparison target is itself old, pre-fix stored state,
so a second rebuild would mostly re-exercise the same already-unit-tested code path rather than
prove something new) — the fix is render-time and general (applies to daily/weekly/monthly alike,
not just weekly), verified directly against the live defect string instead.

The escaped-`|` fix (§2, defect #9) is independently visible in the rebuilt file itself: row 612 of
the new `weekly_2026-09-07.md`'s sources appendix carries a title with a literal `|`
("...Valuation **\|** Special Interview...", escaped, not a bare pipe) — the exact defect shape
found in `bd_il_2026-09-07.md`, now rendered safely instead of corrupting the row.

## 6. Remaining (not fixed this round)

- **Out of ownership, confirmed live defects, need their own owner:**
  - `agent/eoa/patents/survey.py` — both patent-survey tables at 7 columns; every patent title/CPC
    cell wrapped in the same LRI/PDI bidi isolates (defect #1, same root cause, same fix pattern —
    `textnorm.strip_bidi_isolates` — would apply directly once ported there); "בעלים"/"תאריך פרסום"
    hollow columns (defect #16) with no explanatory note.
  - `agent/eoa/payloads/report_section.py` — "מחירי ייחוס למטע\"דים" table at 7 columns.
  - `agent/eoa/tenders/report_section.py` — the `[item N]` marker (defect #5) originates in this
    module's own forecast-drafting text; the report-layer fix above strips it at display time, but
    the marker's root cause is upstream of the report layer.
  - `agent/eoa/pipeline/analyze.py` (or wherever `entities_mentioned`/event extraction lives) —
    defect #12 (Halo_Shield/AV_Halo COMMAND underscore-style naming) and #13 (buyer/vendor field
    sometimes carrying the same fact in two languages side by side) are both upstream data-quality
    issues, not renderer bugs; the report layer can display what it's given more cleanly but cannot
    invent a normalized system name or pick one language.
- **Needs an LLM re-generation to actually change wording** (the prompt-file fixes in §3 only take
  effect on a report's *next* draft — nothing retroactively rewrites the already-generated,
  already-published `output/reports/*.md` text beyond what the deterministic fixes in §2 touch):
  - The "פלנטריוניקס"/"טלסקופיקס" invented transliteration itself (defect #11) — persists verbatim
    in the existing weekly/monthly files until those `so_what_he` fields are regenerated.
  - Defect #14 (the 52-line low-signal "מה השתנה" trend-delta dump) — this is a data-volume/
    prioritization problem in what `eoa.report.trends`/`deltas.py` surfaces as "disappeared" trends
    period-over-period, not a text-formatting bug; a real fix needs a product decision (cap the
    list length? only show the top-N by prior strength? drop "disappeared" entirely and keep only
    "new"/"strengthened"/"weakened"?) that is out of scope for a deterministic renderer change.
  - Defect #15 (pl_mws_eo's empty sources appendix) — the report has real citable content (3
    patents) but zero items registered in the citation registry; a renderer-side "no sources cited
    this run" fallback line would paper over what looks like an upstream citation-registration gap
    worth its own investigation, so left uncaptioned rather than deterministically patched here.

## 7. Counts by category (summary)

| Category | Instances found | Fixed (renderer) | Fixed (prompt) | Remaining |
|---|---|---|---|---|
| Bidi-isolate leakage | 751 (in-scope reports) + 180 (patent surveys) | 751 | — | 180 (patents/survey.py) |
| Stray `\"` backslash | 21 | 21 | — | 0 |
| Space-before-punctuation | pervasive w/ above | fixed w/ above | — | 0 |
| Raw taxonomy slug in heading/cell | 25 | 25 | — | 0 |
| `[item N]` leak | 9 | 9 | — | 0 (root cause upstream, noted) |
| Mid-word/abrupt table truncation | 7+ (systemic via shared renderer) | all (systemic fix) | — | 0 |
| >6-column table | 3 tables | 1 table | — | 2 tables (patents/payloads, out of scope) |
| "1 שורות" grammar bug | 4 | 4 | — | 0 |
| Unescaped `\|` breaking a table row | systemic (any title with "\|") | systemic fix | — | 0 |
| Contradictory empty-summary placeholder | 2 | 2 | — | 0 |
| Invented transliteration | 1 (persists in 3 files) | — | 1 (prevents recurrence) | 3 existing occurrences need regen |
| Underscore-style system naming | recurring | — | — | upstream (pipeline) |
| Mixed-language cell | several | partially (buyer/vendor merged into one cell) | — | root data still mixed-language |
| Low-signal trend-delta dump | 1 (52 lines) | — | — | needs product decision |
| Empty appendix table | 1 | — | — | needs investigation |
| Hollow column | 2 (patent surveys) | — | — | out of scope |

**Totals: 16 defect categories catalogued; 10 fixed deterministically in the renderer, 2 categories
addressed via prompt wording rules (prevents recurrence, does not retroactively fix already-
published text), 6 categories documented as remaining (4 out of ownership scope, 2 needing a
product decision or further investigation before a safe fix).**
