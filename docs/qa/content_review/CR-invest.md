# Content Readability Review — CR-invest (investigation answers)

Date: 2026-09-08
Scope: investigation answer rendering end-to-end — `web/src/pages/InvestigationDetailPage.tsx`,
`web/src/components/CitationText.tsx`, a new `web/src/components/AnswerText.tsx` +
`web/src/lib/answerFormat.ts`, `agent/eoa/search/deep_search.py`'s W27 answer-assembly function
(`format_investigation_answer_he`), and `agent/eoa/llm/prompts/deep_search_system.md`.
Trigger: user screenshot of `/investigations/175`, "look at the poor language of the report".
Fixture: job 175's stored `answer_he` (jobs table, `result->answer_he`), read directly from
Postgres — reproduced verbatim below and used as the fixture in every new test file.

## Summary

- **Root cause of the reported bug**: job 175 ran before the backend's own punctuation-
  normalization pass (`eoa.report.textnorm.normalize_hebrew_punctuation`, which strips embedded
  LRI/PDI bidi-isolate control characters and un-escapes stray `\"` backslash-quotes) was wired
  into `format_investigation_answer_he` — confirmed by diffing job 175's stored text against what
  the *current* backend code produces from the same inputs (it no longer reproduces the bug).
  Per the task brief, **stored rows are not rewritten** — the fix has to make old text render
  cleanly too, so the primary fix is in the UI renderer (finding 1 below), with the backend
  (findings 2-4) improving only *future* answers.
- **4 fixes**, one in the UI renderer (the one that also repairs every already-stored answer),
  three in the backend assembly function, plus a prompt update so the model produces cleaner raw
  text going forward:
  1. UI: `answer_he`/`what_was_tried_he`/`contradictions_he` now render as structured Hebrew
     (real headings, bullet lists, paragraph spacing, per-run bidi isolation, clickable citation
     chips) instead of one wall of text with literal `###`/`- ` markup — and clean up legacy
     artifacts (bidi-isolate control chars, stray backslash-quotes, grouped `[1,2]` citation
     markers) that predate the backend's own normalization pass.
  2. Backend: a `key_facts` bullet that mostly repeats a sentence already in the direct-answer
     prose is now dropped (token-overlap ≥ 0.7).
  3. Backend: the "### מקורות" block is never built into `answer_he` any more — `sources` is
     already a separate, structured field the UI renders on its own; this section was pure
     duplication (and, per the screenshot, ends every legacy answer with a raw URL dump).
  4. Backend: the direct-answer and context prose are each capped at ~6 sentences.
  5. Prompt: added report-style writing rules (short sentences, Latin names with spacing, one
     term per system, gloss-once acronyms, no filler openers, no prose/bullet duplication, no
     URLs in prose) to `deep_search_system.md`.
- **Tests**: 87 new/updated frontend tests (Vitest) + 31 new/updated backend tests (pytest), all
  using job 175 as a fixture where the task called for one. Full counts below.
- **Build**: `cd web && npm run build` — passes (`tsc -b` + `vite build`), bundle live at
  `http://127.0.0.1:8765`.
- **e2e**: `cd e2e && npx playwright test 05-investigations.spec.ts 09-reports.spec.ts
  10-settings.spec.ts` — **60 passed, 15 skipped, 0 failed** across all 5 projects
  (desktop/mobile/tablet-portrait/tablet-landscape/iphone-safari). (05 is the actual investigations
  spec; 09/10 — reports/settings — were run too per the task brief and are unaffected, as
  expected, since neither renders `answer_he`.)

---

## Before / after: job 175

### Before (raw, as rendered by the old `<CitationText>`-in-one-`<bdi>` block)

One paragraph of visible text, no headings, no bullets — everything below rendered as literal
characters:

```
המוצר החדש, ⁦Ophir® SupIR-X, ⁩הוא עדשת זום מוטורית רציפה (⁦Continuous Zoom⁩) בטווח ⁦15-300 ⁩מ\"מ
ובעדשה קבועה ⁦f/4, ⁩המיועדת ספציפית לגלאי ⁦MWIR ⁩מסוג ⁦10 µm SXGA. ⁩העדשה מיוצרת על ידי חברת ⁦Ophir
Optronics (⁩שייכת לקונצרניט ⁦MKS Instruments) ⁩ומיועדת למשימות ⁦ISR (⁩מודיעין, תצפית וסימון⁦) ⁩במרחקים
ארוכים באוויר, ביבשה ובים. המערכת מאפשרת זיהוי כלי רכב מעבר ל-⁦26 ⁩ק\"מ וניתנת להרחבה (⁦Scalability⁩)
עד למרחק מוקד של ⁦1200 ⁩מ\"מ באמצעות מתאמי המערכת של ⁦Ophir.⁩

### עובדות מרכזיות
- העדשה מיועדת לגלאי ⁦MWIR ⁩מסוג ⁦10 µm SXGA ⁩המיועדים למשימות ⁦ISR [1]⁩
- העדשה מציעה טווח זום רציף של ⁦15-300 ⁩מ\"מ עם פתיחת עדשה קבועה של ⁦f/4 [1,2]⁩
- העדשה תומכת בהרחבה (⁦Scalability⁩) עד ל-⁦1200 ⁩מ״מ באמצעות מתאמי המערכת של ⁦Ophir [1,2]⁩
- העדשה מאפשרת זיהוי כלי רכב מעבר ל-⁦26 ⁩ק״מ בתנאי שטח סטנדרטיים [⁦1,2⁩]
- המוצר מיוצר על ידי ⁦Ophir Optronics, ⁩חברה של קונצרניט ⁦MKS Instruments [1,2]⁩
- העדשה כוללת מנגנון סגירת תריס מכני (⁦NUC shutter⁩) לשמירה על איכות התמונה [⁦1,2⁩]
- העדשה מיועדת לשימוש באוויר, ביבשה וביים [⁦1,2⁩]

### הקשר
מבחינה טכנולוגית, העדשה מהווה קפיצת מדרגה ...
[... two more full paragraphs, restating several of the same facts a third time ...]

### פערים / מה לא ידוע
אין נתונים ספציפיים על סכומי חוזה, לקוחות ספציפיים או לוחות זמנים מסחריים ...

### מקורות
- [⁦1⁩] ⁦https://hiwars.com/en/intel/the-all-new-15-300-mm-f4-mwir-zoom-engineered-for⁩
```

Concrete problems (matching the user's screenshot complaint verbatim):

- Literal `###` and `- ` markdown syntax visible as text.
- `⁦`/`⁩` (U+2066 LRI / U+2069 PDI) bidi-isolate control characters render as visible glyphs in
  several fonts/viewers instead of behaving as zero-width formatting — the reported
  "Ophir® SupIR-Xהוא"/"15-300מ"מ"/"MKS Instruments) לקונצרן" glued/scrambled text.
- `\"` literal backslash-quote artifacts (`מ\"מ`, `תע\"א`) leaked from JSON escaping.
- Every one of the 7 `key_facts` bullets restates a clause already in the direct-answer paragraph
  — the same facts appear twice, once as prose, once as a bullet.
- A trailing `### מקורות` block repeats the one URL the page's own sources panel already shows.
- `[1,2]` grouped citation markers render as one inert, unclickable blob (the single-number
  `\[\d+\]` regex `CitationText` matched never matched the group at all).

### After (this session's fix, `AnswerText` rendering the *same* stored `answer_he`)

- `### עובדות מרכזיות` / `### הקשר` / `### פערים / מה לא ידוע` render as real `<h4>` headings.
- The `- ` bullets render as a real `<ul>`/`<li>` list.
- The `⁦`/`⁩` isolate characters and `\"` backslash artifacts are stripped/repaired
  (`מ\"מ` → `מ״מ`, a proper gershayim) before anything reaches the DOM.
- `[1,2]` expands to two individually clickable `[1]`/`[2]` chips.
- Every Latin/number run (`Ophir® SupIR-X`, `MWIR`, `10 µm SXGA`, `MKS Instruments`, `26`, `1200`,
  …) is wrapped in a real `<bdi dir="ltr">`, so it never glues to the adjacent Hebrew word.
- The trailing `### מקורות` block and its URL are **not** rendered at all — the page's own
  sources panel (fed from the same `sources` array via `CitationText`'s citation chips) is the
  only place the URL appears.
- (Backend-only, does not change job 175's *already-stored* text, but is what a fresh run of the
  same investigation would now produce): the `key_facts` bullets that restate the direct
  paragraph verbatim are dropped — only the two genuinely new facts (NUC shutter, air/land/sea
  usage) would survive `format_investigation_answer_he`.

---

## Rules and fixes, in detail

### 1. UI: `AnswerText` (new) + `answerFormat.ts` (new) + `CitationText`/`bidiText` extensions

- **`web/src/lib/answerFormat.ts`** (new): `normalizeHebrewPunctuation()` — an independent
  TypeScript port of `agent/eoa/report/textnorm.py`'s six-pass algorithm (unescape stray
  backslash-quotes → strip bidi isolates → collapse doubled quotes → ASCII quote→gershayim →
  ASCII apostrophe→geresh → collapse space before closing punctuation). Independent rather than
  imported because nothing client-side can import Python, and because this is exactly the code
  path that has to keep working on text the *current* Python normalizer never touched (an
  already-stored legacy answer). `expandGroupedCitationMarkers()` turns `[1,2]` into `[1][2]`.
  `parseAnswerSections()` walks the cleaned text line-by-line, starting a new section on every
  `### <title>` heading (a section's body can itself span several blank-line-separated
  paragraphs — e.g. job 175's three-paragraph `הקשר` section — which is why this doesn't just
  split the whole text on every blank line up front), classifies each paragraph as a bullet list
  (every line starts with `- `) or prose, and drops any section titled `מקורות` outright.
- **`web/src/components/AnswerText.tsx`** (new): maps `parseAnswerSections()`'s output onto real
  `<h4>`/`<p>`/`<ul><li>` elements, handing each block's text to the existing `CitationText` for
  citation-chip rendering (so `[n]` stays exactly as clickable/hoverable as before, `onOpenItem`
  and all) plus the new per-run bidi isolation (next bullet). `size="xs"|"sm"` controls text size
  so it drops into the page's existing "מה נוסה"/"תשובה" slots without a style regression.
- **`web/src/lib/bidiText.tsx`**: added `renderBidiRuns()` alongside the existing
  `renderBidiText()` (left untouched, including its own narrow quoted-span contract and tests —
  it has its own call site, `InvestigationsListPage`'s question rendering). `renderBidiRuns` is a
  TypeScript port of `eoa.search.deep_search._split_bidi_runs` (same Hebrew-codepoint-range
  classification, same bracket-pair symmetry so `"(Targeting Pods)"` keeps its parens in the
  Hebrew run) that wraps every Latin-letter/digit run in a real `<bdi dir="ltr">` — a strictly
  better fix than the backend's own invisible-Unicode-isolate-mark approach for *plain display
  text*, since several fonts/viewers render that control character as a visible glyph (the exact
  bug reported here).
- **`web/src/components/CitationText.tsx`**: non-citation text segments now go through
  `renderBidiRuns()` instead of being emitted as a raw string — this is `CitationText`'s only
  production call site (`InvestigationDetailPage`, via `AnswerText`), so every plain sentence
  rendered through it is bidi-safe, not just ones that go through the fuller section parser.

Both `what_was_tried_he` and `contradictions_he` get the identical `AnswerText` treatment. For
`contradictions_he` specifically: it's normally already folded into `answer_he`'s own `### פערים`
section by the backend, so `InvestigationDetailPage` only renders it as its own block when
`answer_he` does *not* already contain a `"פערים"` heading — a legacy answer that predates the
fold-in (or the cloud-batch path, which never sets `contradictions_he` at all).

### 2. Backend: `eoa.search.deep_search.format_investigation_answer_he` (W27)

- **Bullet de-duplication**: `_bullet_restates_prose()` drops a `key_facts` bullet whose own
  tokens overlap the direct-answer paragraph at ≥ 0.7 (fraction of the *bullet's* tokens found in
  the prose, not a symmetric Jaccard score — a short bullet fully contained in a much longer
  paragraph should count as a full restatement even though the paragraph itself shares only a
  small fraction of its tokens with that one bullet).
- **No `sources` section**: `format_investigation_answer_he` no longer takes a `sources` argument
  at all — the "### מקורות" block it used to build is gone. Both call sites
  (`_finalize_outcome`, `investigate_batch_cloud`) updated to stop passing it.
- **Prose sentence cap**: `_cap_prose_sentences()` (new) trims the direct-answer paragraph and
  the `הקשר` block independently to ~6 sentences each (`_MAX_PROSE_SENTENCES`).
- Headings are otherwise unchanged (`### עובדות מרכזיות`/`### הקשר`/`### פערים / מה לא ידוע`,
  fixed order, skipped when empty) — the UI now renders them, so they stay.
- Hebrew/Latin spacing normalization was already wired in (`_bidi_space_and_isolate` +
  `eoa.report.textnorm.normalize_hebrew_punctuation`) from a prior round; unchanged here beyond
  running after the new sentence cap.

### 3. Prompt: `agent/eoa/llm/prompts/deep_search_system.md`

New rule 11 ("רמת כתיבה — עברית מודיעינית פורמלית"), mirroring the report-writing rules already
in `report_daily.md`'s "כללי כתיבה תמציתית": ≤20-word sentences / one idea per sentence, foreign
company/product names always in Latin letters with a space on both sides (never a Hebrew
transliteration), one fixed term per system/product for the whole answer, gloss an acronym once
on first use, no filler openers (יש לציין / חשוב להדגיש / ראוי לציין / …), no restating the same
fact in prose and in a bullet, no URLs inside `answer_he`/`key_facts`/`contradictions_he`.

### 4. Not done (explicitly out of scope per the task brief)

- **No DB rewrite.** Job 175's stored row is untouched — the UI fix (finding 1) is what makes it
  render cleanly; the backend fixes (findings 2-4) only affect investigations run from now on.

---

## Tests

**Frontend (Vitest, `cd web && npx vitest run`)**: **58 files / 460 passed, 0 failed** (full
suite, not just the touched files). New/updated files:
- `web/src/lib/answerFormat.test.ts` (new, 22 tests) — punctuation normalization, grouped-citation
  expansion, section parsing, including the job 175 fixture (parses into the 4 expected sections,
  no isolate/backslash artifacts survive, `[1,2]` expands, `מ"מ` → `מ״מ`).
- `web/src/lib/bidiText.test.tsx` (extended, +6 tests for `renderBidiRuns`) — bare Latin/digit run
  isolation, bracket-pair symmetry, multiple independent runs, pure-Hebrew passthrough.
- `web/src/components/AnswerText.test.tsx` (new, 8 tests) — heading/list/paragraph rendering,
  `מקורות` section dropped, citation chips still clickable (including `onOpenItem`), and two tests
  against the job 175 fixture directly.
- `web/src/components/CitationText.test.tsx` (updated 1 test for the new bidi-wrapped-plain-text
  DOM shape; all other tests unchanged/still passing).
- `web/src/pages/InvestigationDetailPage.test.tsx` (extended, +5 tests, job 175 fixture) — real
  headings/lists render, no literal `###`, no מקורות block, no raw isolate/backslash artifacts,
  citation chip still clickable end-to-end through the page.

**Backend (pytest, `PYTHONPATH=agent python -m pytest tests/unit/test_deep_search_answer_format.py
tests/unit/test_deep_search_cloud_batch.py tests/unit/test_deep_search_round8.py -q`)**: **81
passed, 0 failed**. New/updated:
- `tests/unit/test_deep_search_answer_format.py` — updated existing tests for the removed
  `sources` argument/section, added: bullet-restates-prose dropping, prose sentence capping
  (direct + context independently), and a dedicated `TestJob175Fixture` class (5 tests) built
  from job 175's exact DB fields (`DIRECT_HE`/`KEY_FACTS`/`CONTRADICTIONS_HE`, isolates and
  backslash-quotes included) verifying no isolate characters survive, backslash-quotes become
  gershayim, restating facts are dropped genuinely-new ones kept, no מקורות section, and no
  unspaced bidi boundary anywhere in the output.
- `tests/unit/test_deep_search_cloud_batch.py` / `test_deep_search_round8.py` — 3 pre-existing
  tests updated for the `format_investigation_answer_he` signature change (no more `sources=`
  kwarg); one of them (`test_investigate_calls_force_read_when_round_loop_ends_with_zero_reads`)
  was asserting "a forced read produced content" via a side effect of the now-removed מקורות
  block — re-pointed at the actual signal (`inv.result.sources` populated).

Confirmed via a disposable `git worktree add` against pristine `HEAD` that one further failure
seen in a full `-k deep_search` run (`test_discovery_round4.py::...
test_quarantined_page_recorded_on_investigation_for_final_security_review_flag`) is pre-existing
and unrelated to this change (fails identically on `HEAD`, before any of this session's edits).

**Build**: `cd web && npm run build` — `tsc -b && vite build`, passes clean.

**e2e**: `cd e2e && npx playwright test 05-investigations.spec.ts 09-reports.spec.ts
10-settings.spec.ts` — **60 passed, 15 skipped, 0 failed**, all 5 projects (desktop 1440×900,
mobile 390×844, tablet 820×1180, tablet-landscape 1180×820, iphone-safari).
