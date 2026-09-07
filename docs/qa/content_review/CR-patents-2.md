# CR Patents 2 — Description Fabrication & Survey Table Readability (Round 14 continued)

Date: 2026-09-07. Trigger: two confirmed content defects from the independent fact-check
(`docs/qa/content_review/CR-factcheck.md`, `patent_survey` sections) and the editing review
(`docs/qa/content_review/CR-editing.md` §6), both left explicitly out-of-scope by the earlier
Round 14 pass (`docs/qa/content_review/CR-patents.md`, which fixed the *assignee*-misattribution
bug and does not touch either defect below).

1. **Fabricated patent descriptions.** US10506436B1 ("Lattice mesh", a real Anduril patent) has a
   DB record whose `abstract` is only a USPTO assignment-transfer notice ("2019-03-07 Assigned to
   Anduril Industries Inc...", 18 words, zero technical content), yet its stored
   `claims_summary_he`/`so_what_he` (and the Anduril survey's per-patent "התקדמות" column)
   described a fully invented optical lens/mirror/fiber array.
2. **Survey table readability.** `agent/eoa/patents/survey.py`'s patent-appendix and timeline
   tables both rendered at 7 columns, every title/CPC/assignee cell was wrapped in LRI/PDI bidi
   isolates that leaked into the markdown as visible glyphs (`⁦...⁩`), and a hollow "מקצה"/"הענקה"
   column carried no explanatory note.

## 1. Root cause

**Defect 1** had two independent contributing failures, neither previously guarded:

- `eoa.patents.analyze.analyze_patents` sent *every* row missing `claims_summary_he` straight to
  the LLM, with no check on whether the record's `abstract` actually carried any real technical
  content. The prompt's own "iron rule" ("אסור להמציא עובדה שאינה בטקסט") is advisory only — a
  small/instruction-tuned local model reading an 18-word assignment notice still produced a
  confident, detailed, plausible-sounding *invented* description rather than declining outright.
- Nothing checked the *generated* Hebrew text against the source afterward, so even a model that
  did comply with the prompt's instruction had no independent verification step.

**Defect 2**: `eoa.patents.render.ltr_isolate` (added round 6 for a genuine problem — a bare Latin
value in a plain-Markdown table cell can visually fragment against surrounding RTL text) wraps a
cell value in Unicode LRI/PDI isolate control characters. Its own docstring claimed this is
"harmless" everywhere because the marks are zero-width — true inside `docx_builder`'s own
run-level bidi splitting (docx/html), but **not** true for a bare `.md` file read in a real
Markdown viewer or plain-text context, where the marks render as visible placeholder glyphs
(confirmed live 2026-09-07 in `output/reports/patent_survey_Anduril_Lattice_..._2026-09-07.md`:
`⁦WO2023041813A1 - Counter-unmanned aerial system (c-uas ...⁩`). The patent-appendix table also
carried a redundant 7th "קישור" column duplicating information better folded into the title cell,
and the timeline table separately carried its own 7th column ("עדיפות"/priority date). Neither
table flagged a column that was mostly/entirely empty (`"מקצה"`/assignee for a keyless-search
sample with no confirmed owner, `"הענקה"`/grant date for a mostly-still-pending sample).

## 2. Code fix

### (a) Guard 1 — a text-less record never reaches the LLM

`agent/eoa/patents/analyze.py`:

- `_technical_word_count`/`_has_sufficient_technical_text`: word count of `abstract` (+ any future
  `raw.claims_text`) against a `_MIN_TECHNICAL_WORDS = 40` floor. US10506436B1's real stored
  abstract is 18 words (pure boilerplate); the threshold was picked so CN112074705A's genuinely
  fragmentary-but-real 40-word technical snippet still clears it.
- `analyze_one_patent_row` (the per-row body of `analyze_patents`, extracted this round so
  `scripts/repair_round14_patent_text.py` can reuse the identical pipeline): when guard 1 fails,
  the LLM is never called — `_persist_no_text_analysis` writes the fixed placeholders
  (`claims_summary_he = "אין תקציר או תביעות זמינים למסמך זה; לא ניתן לתאר את הטכנולוגיה."`,
  `so_what_he` analogous) and stamps `raw.text_available = false`.

### (b) Guard 2 — a generated sentence must be verifiable against the source

- `_grounding_source_pool(row)`: title + abstract + any `raw` claims text + the row's own
  already-verified `assignees`/`cpc`/`pub_number` (these three are given facts, not something the
  model could fabricate, so they're included to avoid false-positive strips of a sentence that
  simply names the patent's own known assignee/CPC class).
- `ground_generated_text(text, source_pool, ...)`: splits `text` into sentences; a sentence naming
  a technical token (`_TECH_TOKEN_RE` — an English word/acronym or a >=2-digit number/model code)
  that cannot be found (case-insensitively) in the source pool, and is not in the small standing
  `_DOMAIN_VOCAB` allowlist (`EO`, `IR`, `ISR`, `UAS`, `C-UAS`, `SNR`, `HEL`, `DEW` — terms the
  `so_what_he` prompt field itself instructs the model to use generically), is dropped and logged
  as `patent.ungrounded_description_removed` (`pub_number`, `field`, `token`, `sentence[:200]`). A
  field left with nothing grounded falls back to
  `"התקציר אינו מספק מספיק מידע לניתוח תביעות מלא (פרטים שנוצרו לא אומתו מול המקור והוסרו)."`
- Applied to both `claims_summary_he` and `so_what_he` in `analyze_one_patent_row`, after the LLM
  call, before persist.

**Known, documented limitation** (see `ground_generated_text`'s own docstring): this is a lexical
substring check, not semantic grounding. A sentence that introduces its *own* abbreviation of a
term the source abstract only ever spells out in full (e.g. writing "RIC" for a "Readout
Integrated Circuit" the English abstract text spells out) is still flagged even though the
underlying fact is real — the check trades recall for precision. The `_DOMAIN_VOCAB` allowlist was
added specifically because the first full-corpus dry run (before it existed) stripped "EO"/"IR"
out of the overwhelming majority of stored `so_what_he` sentences — a false positive, not a real
catch, since the `so_what_he` prompt field literally instructs the model to frame every answer in
terms of "מוצרי EO/IR". Guard 1 (the pre-call word-count gate) is the actual primary defense for
the flagship US10506436B1 case; guard 2 is defense-in-depth for a record whose abstract *is*
substantive but whose generated text still pads in an unverifiable specific.

### (c) Real-abstract recovery via the existing detail-page enrichment

`agent/eoa/patents/scan.py`:

- `_parse_abstract_from_detail_html(html)`: extracts the patent's real abstract from its own
  Google Patents detail page's `<meta name="DC.description" content="...">` tag (confirmed live
  2026-09-07: US10506436B1's own detail page carries a genuine ~120-word "lattice mesh"
  networking/asset-registration abstract — nothing to do with optics, matching CR-factcheck's own
  prediction that the real patent "almost certainly concerns networked sensor/command-and-control
  mesh, not optics"). Folded into `_parse_google_patent_detail_html`'s existing return dict.
- `_backfill_patent_fields(..., overwrite_abstract=True)`: a new sibling to the existing
  `overwrite_assignees` path (round 14's earlier assignee fix) — unconditionally replaces a
  low-quality search-snippet `abstract` with the real detail-page one, stamping
  `raw.abstract_source = 'detail_page'`.

### (d) Survey table readability (`agent/eoa/patents/survey.py`, `render.py`, `report_section.py`)

- `eoa.report.textnorm.strip_bidi_isolates` **imported** (not copied, per instruction) and applied
  via a new `_strip_bidi_isolates_from_tables(tables)` to every table's `title_he`/`note_he`/
  `headers`/`rows` right before they reach any of the three renderers (docx/md/html share the same
  `tables` list) — harmless for docx/html either way, since that pair's own bidi handling never
  needed the isolate marks in the first place (this module's own pre-existing docstring already
  said so).
- `_title_link_cell(title, url)`: the patent-appendix table's old "קישור" column merged into the
  title cell as a plain Markdown link `[title](url)`, bringing that table from 7 to 6 columns
  (`["מספר", "כותרת EN", "מקצה", "CPC", "ציון ערך", "התקדמות"]`). **Trade-off, documented in the
  function's own docstring**: `docx_builder.py` stays untouched per this task's ownership
  boundary, so the docx/html paths show the literal `[title](url)` bracket syntax as plain text
  rather than a live hyperlink there — the markdown report (the format the content-review pass
  actually reads) renders it as a real clickable link, per the CR's own instruction.
- The timeline table ("ציר זמן פטנטים") dropped its "עדיפות" (priority date) column — the least
  informative of the seven, since `eoa.patents.cluster.expiry_estimate` already falls back to
  priority date internally whenever filing date (the table's own next column) is missing — down to
  6 columns (`["מספר", "הגשה", "פרסום", "הענקה", "תפוגה משוערת (20 שנה)", "סטטוס"]`).
- `eoa.patents.render.sparse_column_note_he(headers, rows, column_he)`: `None` unless >=60% of a
  table's rows carry an empty/placeholder value in `column_he`, in which case a one-line Hebrew
  note is folded into the table's `note_he` (`eoa.report.docx_builder._table_caption_text` already
  renders that field identically across all three formats — untouched). Wired into the
  patent-appendix table's "מקצה" column and the timeline table's "הענקה" column in `survey.py`,
  and into `report_section.py`'s `patents_table`/`patents_bd_table` "בעלים" column (the same defect
  pattern, confirmed live in `pl_mws_eo_2026-09-07.md`'s three DIRCM patents all showing owner
  "—").
- **Whole-document safety net.** The first rebuild (before this addition) still showed 8 leaked
  isolate marks in the Anduril survey's `.md` — all in the "שאילתת חיפוש"/"טווח תאריכים"
  methodology lines and the generic sources-appendix table built from `items_for_appendix`, neither
  of which is part of the `tables` list `_strip_bidi_isolates_from_tables` cleans. Rather than chase
  every individual `ltr_isolate`/`ltr_isolate_if_latin` call site across the module, `survey.py` now
  applies `strip_bidi_isolates` once more to the fully-assembled `md_content`/`html_content` strings
  right before each is written to disk — a single, comprehensive, idempotent safety net (harmless
  no-op wherever no isolate mark is present) that also covers any future call site this round didn't
  anticipate.

## 3. Audit/repair script

`scripts/repair_round14_patent_text.py` (dry-run by default, `--apply` to write, `--pub-number` to
scope to one patent, backs up every touched row's pre-change state to
`runtime/backups/repair_round14_patent_text_<UTC timestamp>.json` before writing, re-verifies its
own writes from a separate connection). Scope: every `patents` row with `claims_summary_he IS NOT
NULL` (86 of 86 — every row had already been analyzed at least once before this round's guards
existed). For each: classifies as `text_less` (guard-1 gate now fails against the current
abstract), `grounding_check` (text was/is sufficient — re-run guard 2 against the *stored*
description), or `already_placeholder`. A `text_less` row first tries the real-abstract-recovery
path (§2c) before ever falling back to the placeholder, and — when a real abstract is recovered —
re-runs the exact same `analyze_one_patent_row` pipeline `analyze_patents` uses, so the row's fresh
description goes through guard 2 as well.

### US10506436B1 — before / after (fixed first, per instruction)

Verified live 2026-09-07 against `https://patents.google.com/patent/US10506436B1/en`: the page's
own `DC.description` meta tag is a genuine ~120-word "lattice mesh" abstract about a distributed
asset-registration/authentication mechanism — no sensor, optics, electro-optics, infrared, laser,
or computer-vision content whatsoever.

| Field | Before | After |
|---|---|---|
| `claims_summary_he` | "הפטנט מתאר מערכת 'רשת רשתית' (Lattice Mesh) שמטרתה לשפר את ביצועי חיישנים אלקטרואופטיים... המערכת כוללת רשת של אלמנטים אופטיים (כגון עדשות, מראות או סיבים אופטיים) המסודרים באופן גיאומטרי מדויק. החידוש הטכני טמון בסידור זה, המאפשר שיפור באיכות התמונה, בהפחתת עיוותים ובגמישות המערכת..." (fully invented optical lens/mirror/fiber array) | "החידוש הטכני, ככל שניתן להסיק, הוא במנגנון אימות והפצה מבוסס-אישורים לרישום נכסים ברשת מבוזרת. התקציר עוסק בתשתית תקשורת/אבטחת מידע גנרית ואינו מזכיר כלל חיישן, אופטיקה, אלקטרואופטיקה, אינפרה-אדום, לייזר או בינה חזותית -- אין בו מספיק מידע כדי לשייכו לתחום ה-EO/IR או לנתח ממנו תביעות רלוונטיות לתחום זה." (grounded, correctly identifies the patent as *not* an EO/IR technology) |
| `abstract` | "2019-03-07 Assigned to Anduril Industries Inc. reassignment Anduril Industries Inc. ASSIGNMENT OF ASSIGNORS INTEREST (SEE DOCUMENT FOR DETAILS)." (18-word assignment notice, `raw.assignee_source` only) | The real ~120-word detail-page abstract (asset registration/RA-certificate mechanism) |
| `raw.abstract_source` | *(not set)* | `"detail_page"` |

### Full sweep — results (all 86 candidate rows audited)

| Outcome | Count | Meaning |
|---|---|---|
| `already_grounded` | 40 | Stored description already passes both guards unchanged — no write |
| `abstract_recovered_needs_reanalysis` | 29 (1 fixed first via `--pub-number` + 28 in the full sweep) | Text-less by guard 1; a real detail-page abstract was recovered and the row re-analyzed through the full guarded pipeline |
| `ungrounded_sentences_stripped` | 14 | Text was/is sufficient, but the *stored* description (generated before guard 2 existed) still named an unverifiable technical detail — offending sentence(s) removed |
| `fallback_placeholder` | 3 | Text-less by guard 1, and no real abstract was recoverable from the detail page either (dead/thin page) — written to the fixed, honest placeholder |

**46 of 86 rows (53%) changed.** The 3 `fallback_placeholder` rows: id 29 (JP6771616B2), id 35
(US1 — a malformed `pub_number`, no real page to fetch), id 54 (US20220324572A1). The 29
`abstract_recovered_needs_reanalysis` rows include several patents already publicly surfaced in
the two on-demand surveys (US20230082239A1, US11385659B2, US20200363824A1 — the Anduril C-UAS
cluster; the full pub_number list is in the script's own audit log).

Backups: `runtime/backups/repair_round14_patent_text_20260907T201254Z.json` (US10506436B1 alone,
applied first) and `runtime/backups/repair_round14_patent_text_20260907T201503Z.json` (the
remaining 45-row full sweep).

Verified independently from a fresh connection:

```
placeholder rows (raw.text_available = false): 3
abstract recovered from detail page (raw.abstract_source = 'detail_page'): 29
total patents: 86
```

## 4. Tests

`tests/unit/test_patents_text_grounding_round14.py` (24 tests) — covers `_technical_word_count`/
`_has_sufficient_technical_text` (including the real 18-word US10506436B1 and 40-word
CN112074705A abstracts as literal fixtures), `_grounding_source_pool`, `ground_generated_text`
(grounded survival, ungrounded strip + log assertion, all-stripped fallback, the real
CN112074705A component/number case, the documented Hebrew-only-sentence limitation), the
`_rows_missing_analysis`/`_persist_no_text_analysis` DB-call shapes (mocked connection, no live DB
call), and two `analyze_patents` integration tests (mocked LLM/DB/entity-resolution boundaries)
proving guard 1 never calls the LLM for a text-less row and guard 2 strips an ungrounded sentence
from a real LLM response.

`tests/unit/test_patents_survey_round14.py` (15 tests) — `_title_link_cell`,
`_strip_bidi_isolates_from_tables` (removal, non-string passthrough, `note_he` cleaned,
non-mutation, empty-list), the two tables' 6-column shape contracts, and `sparse_column_note_he`
(note/no-note thresholds, missing column, no rows, short-row-as-empty).

`tests/unit/test_patents_round6.py` — one existing exact-dict-equality assertion
(`test_missing_markup_yields_empty_not_guessed`) extended with the new `"abstract": None` key
`_parse_google_patent_detail_html` now always returns; its original assertions are otherwise
unchanged.

`tests/unit/test_patents_*.py` (the full pre-existing suite, 415 tests across all files, including
round 14's own assignee-misattribution tests and the round 7/survey cluster-heading contract tests
— `cluster.py` was not touched this round) — all green.

`ruff check` clean on every file touched (`agent/eoa/patents/analyze.py`, `scan.py`, `survey.py`,
`render.py`, `report_section.py`, `scripts/repair_round14_patent_text.py`, and the two new test
files).

## 5. Reports rebuilt

Both on-demand patent surveys rebuilt live 2026-09-07 with `EOA_PIPELINE=1` (rebuilt *twice*: an
initial pass exposed the sources-appendix isolate leak described in §2d's last bullet, then rebuilt
again after that whole-document safety net was added — report ids below are the final,
post-fix build):

- Topic `"Anduril Lattice counter-UAS EO/IR optical tracking patents"` → `patent_surveys.id=35`,
  `reports.id=167`, 6 patents.
- Topic `"FPA עם פיקסל דיגיטלי DROIC"` → `patent_surveys.id=36`, `reports.id=168`, 10 patents.

Weekly/monthly/bd_territory rebuilds (which also surface `patents` data via
`agent/eoa/patents/report_section.py`) are out of this round's ownership boundary
(`agent/eoa/report/**`) — flagged to that owner as a follow-up, same as round 14's own note.

## 6. Verification run

Live checks against the final rebuilt `.md` files:

```
$ grep -c '⁦\|⁩' patent_survey_Anduril_Lattice_..._2026-09-07.md
0
$ grep -c '⁦\|⁩' patent_survey_FPA_עם_פיקסל_דיגיטלי_DROIC_2026-09-07.md
0

$ grep "US10506436B1" patent_survey_Anduril_Lattice_..._2026-09-07.md   (appendix row)
| 5 | [US10506436B1 - Lattice mesh](https://patents.google.com/patent/US10506436B1/en) |
Anduril Industries Inc | — | 12 | החידוש הטכני, ככל שניתן להסיק, הוא במנגנון אימות והפצה
מבוסס-אישורים לרישום נכסים ברשת מבוזרת. התקציר עוסק בתשתית תקשורת/אבטחת מידע גנרית ואינו
מזכיר כלל חיישן, אופטיקה, אלקטרואופטיקה, אינפרה-אדום, לייזר או בינה חזותית … (פירוט במקורות)

$ head -1 (appendix table header, Anduril survey)
| מספר | כותרת EN | מקצה | CPC | ציון ערך | התקדמות |          -- 6 columns

$ head -1 (timeline table header, both surveys)
| מספר | הגשה | פרסום | הענקה | תפוגה משוערת (20 שנה) | סטטוס |  -- 6 columns
*לרוב הרשומות בטבלה זו אין נתון בעמודה "הענקה" -- ... (N שורות)*  -- sparse-column note present
```

All four required checks pass: **no invented optical-lens/mirror/fiber description for
US10506436B1** (replaced with the grounded networking/asset-registration description), **0 isolate
characters** in either rendered `.md` file, **<=6 columns** on both the patent-appendix and
timeline tables in both surveys, and the sparse-column note renders where expected.
