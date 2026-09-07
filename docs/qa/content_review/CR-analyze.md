# CR Analyze — Analysis-Stage Grounding Guard (Round 14)

Date: 2026-09-07. Trigger: user report (severe) — item 39 (edrmagazine.eu, "The all-new 15-300 mm
f/4 MWIR zoom engineered for 10 µm SXGA detectors") had `summary_he` claim "Ophir Optronics, חברה
בת של תעשייה אווירית (תע״א)" (Ophir Optronics, a subsidiary of Israel Aerospace Industries) —
false, and `so_what_he` invented two non-existent competitors, "פלנטריוניקס (Planar Optics)"
ו"טלסקופיקס (Telescopeics)". Both fabrications reached `output/reports/weekly_2026-09-07.md` and
`output/reports/bd_il_2026-09-07.md`.

## 1. Root cause

`agent/eoa/pipeline/analyze.py` had **no grounding check of any kind** on `summary_he`/`so_what_he`
/`key_facts`/`entities_mentioned` before this round — the resident/cloud LLM's structured output
(`AnalyzeOut`) was persisted (`persist_analysis`) verbatim. The chat path
(`agent/eoa/api/ask_grounding.py`, wired into `eoa.api.routes.ask`) already has an extensive,
independently-evolved grounding guard (entity grounding, cross-source conflation, entity
equivalence, number grounding, ...), but it was never applied to the analysis pipeline, and its own
design (comparing an answer against *multiple retrieved sources*) doesn't map directly onto
analyze's single-source, FACT/ASSESSMENT-mode shape.

Item 39's own source text (`edrmagazine.eu`) is entirely about the Ophir(R) SupIR-X lens product
and never mentions Israel Aerospace Industries at all — its only hint of a parent company is the
trailing photo credit "photo courtesy MKS" (Ophir Optronics is in fact a subsidiary of MKS
Instruments, already recorded as the watchlist alias "MKS Ophir" in `config/watchlist.yaml`). The
"תעשייה אווירית (תע״א)" (IAI) claim and the two invented competitor names were produced by the
analyze-stage LLM call with nothing in the source text to ground any of them, and nothing in the
pipeline caught it.

## 2. Rules added

New module `agent/eoa/pipeline/analysis_grounding.py` (self-contained; small number-grounding
primitives are a duplicate, narrower port of `eoa.api.ask_grounding`'s own — reused in spirit, not
imported, per this codebase's own stated convention for a cross-module/cross-layer boundary helper,
see `eoa.pipeline.analyze._event_dedup_key`'s docstring — importing `eoa.api` from `eoa.pipeline`
would also be a layering inversion). Single entry point: `ground_analysis_fields(record, *,
summary_he, so_what_he, key_facts, entities_mentioned) -> GroundingResult`.

Four rules, each additive, operating at sentence/clause granularity (a violation is excised, the
rest of the field is kept):

- **(a) Entity grounding** — every Latin multi-word proper noun or Hebrew head-noun-shaped
  institution phrase in the output must appear literally in the item's own source corpus
  (title + clean/raw text + url + url domain), OR resolve to a watchlist/curated-org record at
  least one of whose *other* aliases the corpus actually mentions — being on the watchlist alone is
  never enough (item 39: "תעשייה אווירית"/"תע״א" resolves straight to IAI, but IAI is never
  mentioned in this item's own source text under any alias, so it is still stripped). A registry
  record's own alias list is also not always a verbatim match for how a specific source phrases the
  same entity — a word-by-word paraphrase fallback (mirroring
  `ask_grounding._proper_noun_grounded`'s own step 3) is applied to each of a resolved record's
  Latin surface forms too, not only the original candidate (found live on item 290: the curated "UK
  Ministry of Defence" record's own aliases — "MoD UK"/"British Ministry of Defence"/"משרד ההגנה
  הבריטי" — are none of them a literal substring of that source's own "the Ministry of Defence
  (MoD)", which very nearly stripped every mention of the article's own central, legitimate
  subject).
- **(b) Affiliation/ownership claims** (חברה בת של / בבעלות / חטיבה של / זרוע של / נרכשה על ידי /
  חברה אם / subsidiary of / owned by / division of / part of) are excised at clause granularity (a
  clean regex boundary, keeping the rest of the sentence — usually genuine FACT content — intact)
  when invalid. Valid means either: (i) `config/company_facts.yaml` has a curated record for either
  side and the *other* side matches one of its recorded `parents` — a claim contradicting a fact
  this registry is confident in is hard-rejected even when the source text happens to also mention
  the wrong parent somewhere (regression-tested: item 1002 fixture, Ophir Optronics + IAI
  co-occurring in the same source sentence is still rejected); or (ii), absent any registry record,
  both organisations must co-occur within the *same* sentence of the item's own source text.
- **(c) Competitor/peer lists** (so_what_he only, per the task brief) — every name following a
  "מתחרים/מתחרותיה/competitors/rivals ... כמו/כגון/such as/including" cue must ground exactly like
  rule (a); an ungrounded name is dropped from the list (the rest of the list is kept, e.g. a real
  grounded competitor survives alongside a stripped fabricated one), and the whole cue+list clause
  is dropped only when *no* listed name grounds. Runs *before* the generic entity check (a) for
  so_what_he specifically — otherwise (a)'s sentence-granularity strip usually beats it to the same
  ungrounded name and drops the whole sentence first, leaving nothing for (c) to trim surgically.
- **(d) Numbers/units** (summary_he only, per the task brief) — every money figure and year must
  appear in the source corpus, reusing `ask_grounding`'s digit-boundary-safe matching
  (`_digits_grounded`) and Hebrew/English spelled-number normalization
  (`_normalize_spelled_numbers`), duplicated here in a deliberately narrower scope (no
  magnitude-aware money-scale comparison, no plain-count mismatch guard — FACT-mode summary_he is
  meant to copy a number verbatim from a *single* source per `prompts/analyze.md`, not reconcile
  several retrieved sources the way a chat answer must).

`config/company_facts.yaml` — a small, curated Israeli-EO/IR corporate-affiliation registry:

| Company | Parent(s) | Confidence | Note |
|---|---|---|---|
| Ophir Optronics | MKS Instruments | high | Root-cause fixture — confirmed by item 39's own "photo courtesy MKS" credit and public record; NOT IAI. |
| Elop | Elbit Systems | high | Merged into Elbit Systems (2000), operates as Elbit Systems Electro-Optics. |
| Elta | IAI | high | ELTA Systems is a wholly-owned IAI subsidiary. |
| Opgal | Rafael, Elbit Systems | medium | Joint venture (1998) — either co-owner alone is not hard-rejected, but a third-party claim is. |

Only `high`/`medium`-confidence entries exist in the file at all (an unconfirmed guess is left out
entirely rather than risking a false hard-reject of a claim that might be true).

### Wiring

- `eoa.pipeline.analyze.persist_analysis`: `ground_analysis_fields` runs on `out.summary_he`/
  `out.so_what_he`/deduped `key_facts`/`entities_mentioned` immediately before `update_item_fields`
  — every future analyze run (and every `_repair_generic_so_what` corrective pass, since that
  happens upstream inside `analyze_item` and its output flows through this same call) is grounded
  automatically. `is_too_thin(...)` (empty/near-empty, or `so_what_he` missing its required
  "להערכתנו" opening because the sentence carrying it was itself stripped) is logged as
  `analyze_grounding_left_too_thin` but never blocks persistence — a thin field is written as-is and
  picked up by the repair script's own sweep.
- `eoa.pipeline.analyze.repair_so_what_text` (the round-6/13-era so_what-only repair entry point):
  its freshly-generated text is now also run through `ground_analysis_fields` before being
  returned/accepted, since its caller persists that string directly, bypassing `persist_analysis`.
- `entities_mentioned` grounding inside `persist_analysis` is gated on
  `_entity_persistence_allowed(item)` (the same pre-existing round-6 D3/D9 guard that already
  protects `items.entities_mentioned`/`entities`/`graph_edges` writes for an out-of-scope/archived
  item), and only ever changes `entities_mentioned` relative to what was already going to be
  written (existing value, or a fresh watchlist-backfill union) -- never introduces a *new* write
  purely because grounding found something to strip. Caught by the pre-existing regression suite
  (`tests/unit/test_round6_entities.py`) during this round's own broader-sweep verification: an
  early version applied grounding unconditionally and (a) broke the archived-item invariant
  (`entities_mentioned` must never be touched at all for `level='archive'`) and (b) caused a
  spurious `entities_mentioned` write on an item where nothing else warranted one, which the
  events-fallback-skip test reads as "the fallback fired" even though it hadn't. `entities_mentioned`
  is still fully ground-checked for an *already-persisted* row by the repair script below (called
  directly, not through this gate) -- this restriction only narrows the *live pipeline*'s own
  write path, not the guard's own audit capability.

## 3. Repair script

`scripts/repair_round14_grounding.py` — dry-run by default, `--apply` to write, `--llm-budget`
(default 15) caps re-analysis calls. Backs up every touched row's pre-change field values to
`runtime/backups/repair_round14_grounding_<UTC timestamp>.json` before writing. Prints the DB
target and refuses port 5433; `--apply` re-verifies its own writes from a separate connection.

- `items` subcommand — every item with `domain <> 'out_of_scope'` AND `level <> 'archive'` (this
  round's own "in-scope" definition) and a non-null `summary_he`/`so_what_he`: runs the guard over
  summary_he/so_what_he/key_facts/entities_mentioned. A row left "too thin" by stripping gets one
  fresh re-analysis via `eoa.pipeline.analyze.analyze_item` on the cloud chain (script forces
  `EOA_PIPELINE=1`), re-grounded before being accepted; a dry run only *reports* what would need
  re-analysis (never spends a real LLM call).
- `patents` subcommand — every `patents` row with a non-null `claims_summary_he`/`so_what_he`:
  same guard, mapped onto `claims_summary_he`/`so_what_he`. `agent/eoa/patents/**` is out of scope
  for this round (owned by another agent), so patents has no re-analysis path — a too-thin patent
  row is reported under `needs_manual_review` instead.

### Item 39 — before / after (fixed first, per instruction)

**`summary_he`:**

Before:
> חברת Ophir Optronics, **חברה בת של תעשייה אווירית (תע״א)**, השיקה עדשה חדשה למטע״דים אוויריים
> ויבשתיים: עדשת זום MWIR (Mid-Wave Infrared) דו-צירית 15-300 מ״מ f/4, המיועדת לגלאי 10 מיקרון
> SXGA. העדשה, Ophir® SupIR-X, מציעה שדה ראייה של 45°-2.4° אופקי, משקל של כ-1 ק״ג, וניתנת להרחבה עד
> 1200 מ״מ באמצעות מתאמי Ophir. היא מיועדת למשימות ISR, הגנה על גבולות, אבטחת חופים ומתקנים
> קריטיים.

After (affiliation clause excised at clause granularity — real FACT content, including the
legitimate "MWIR (Mid-Wave Infrared)" technical-term gloss, kept):
> חברת Ophir Optronics, השיקה עדשה חדשה למטע״דים אוויריים ויבשתיים: עדשת זום MWIR (Mid-Wave
> Infrared) דו-צירית 15-300 מ״מ f/4, המיועדת לגלאי 10 מיקרון SXGA. העדשה, Ophir® SupIR-X, מציעה שדה
> ראייה של 45°-2.4° אופקי, משקל של כ-1 ק״ג, וניתנת להרחבה עד 1200 מ״מ באמצעות מתאמי Ophir. היא
> מיועדת למשימות ISR, הגנה על גבולות, אבטחת חופים ומתקנים קריטיים.

**`so_what_he`:**

Before:
> להערכתנו, השקת העדשה החדשה Ophir® SupIR-X מעניקה יתרון תחרותי **לתעשייה אווירית (תע״א)** על פני
> מתחרותיה **כמו פלנטריוניקס (Planar Optics) וטלסקופיקס (Telescopeics)**, במיוחד בתחום המטע״דים
> האוויריים והיבשתיים. העדשה, המיועדת לגלאי 10 מיקרון SXGA, מציעה פתרון קל משקל (כ-1 ק״ג) עם שדה
> ראייה רחב (45°-2.4° אופקי) ויכולת הרחבה עד 1200 מ״מ, מה שמשפר באופן דרמטי את יכולות התצפית והזיהוי
> לטווח ארוך. **הדבר מאפשר לתעשייה אווירית לספק פתרונות מתקדמים יותר ללקוחות ביטחוניים, כולל צה״ל**,
> ולשפר את יכולתם לבצע משימות ISR, הגנה על גבולות, אבטחת חופים ומתקנים קריטיים, ובכך להרחיב את נתח
> השוק שלה בתחום האופטיקה החישובית.

After grounding, both sentences naming IAI and both fabricated competitors were dropped whole,
leaving the field without its required "להערכתנו" opening — flagged `too_thin` and re-analyzed on
the cloud chain (`claude-sonnet-5`, `--apply` run 2026-09-07). The re-analyzed, re-grounded
`so_what_he` actually persisted:

> להערכתנו המוצר ממצב את Ophir בפלח עדשות הזום ה-MWIR ארוכות הטווח לגלאי 10 µm SXGA, שדה שבו הביקוש
> גדל ככל שמערכות ISR ומגדלי תצפית גבוליים עוברים לגלאי רזולוציה גבוהה יותר; יכולת ההרחבה עד 1200
> מ״מ תוך שמירה על f/4 קבוע נותנת ל-Ophir יתרון הצעה גמישה מול מתחרות בתחום שמציעות בדרך כלל עדשות
> זום עם טווח מוקד קבוע או צמצם משתנה. עם זאת, מדובר בדף מוצר שיווקי ולא בדיווח על עסקה או פריסה
> מבצעית בפועל, כך שההשפעה בפועל על נתחי שוק תלויה באימוץ בפועל על ידי יצרני מערכות EO/IR.

No IAI/"תעשייה אווירית" mention, no invented competitor names — and, read qualitatively, a *more*
useful, better-hedged analyst assessment than the fabricated original (it explicitly flags that
the source is a marketing product page, not a reported deal).

`summary_he` (after, clause excised, everything else — including the legitimate "MWIR (Mid-Wave
Infrared)" gloss — kept, no re-analysis needed, not flagged `too_thin`):
> חברת Ophir Optronics, השיקה עדשה חדשה למטע״דים אוויריים ויבשתיים: עדשת זום MWIR (Mid-Wave
> Infrared) דו-צירית 15-300 מ״מ f/4, המיועדת לגלאי 10 מיקרון SXGA. העדשה, Ophir® SupIR-X, מציעה שדה
> ראייה של 45°-2.4° אופקי, משקל של כ-1 ק״ג, וניתנת להרחבה עד 1200 מ״מ באמצעות מתאמי Ophir. היא
> מיועדת למשימות ISR, הגנה על גבולות, אבטחת חופים ומתקנים קריטיים.

`entities_mentioned`: unchanged (`['Ophir Optronics']` was already correct — grounded via its own
"Ophir"/"Ophir®" mentions in the source text, no IAI/"תעשייה אווירית" was ever stored there).

### Repair counts

Live `--apply` run, 2026-09-07, verified from a fresh connection (port 5432) immediately after
each subcommand, both `items_still_ungrounded`/`patents_still_ungrounded` empty:

**`items` subcommand** — scope: 57 in-scope items (`domain <> 'out_of_scope'` AND
`level <> 'archive'`, non-null `summary_he`/`so_what_he`).

| | Count |
|---|---|
| Items checked | 57 |
| Items changed | 17 |
| Clauses/entries removed — entity | 17 |
| Clauses/entries removed — number | 6 |
| Clauses/entries removed — affiliation | 1 (item 39) |
| Clauses/entries removed — competitor | 2 (item 39, both fabricated names) |
| Items re-analyzed (too-thin after stripping) | 4 — ids 39, 129, 5122, 6872 |
| LLM calls spent | 4 (well under the 15-call budget) |
| `needs_manual_review` after re-analysis | 0 — every re-analysis succeeded and re-grounded clean |

Other items changed by the sweep (item ids, all 17): 3, 10, 12, 39, 47, 58, 90, 114, 126, 129, 257,
290, 1352, 5122, 6163, 6166, 6872 — a mix of ungrounded money figures (10, 47, 90, 114, 126, 257 —
`prompts/analyze.md` asks the model to copy every figure verbatim from the source, but these six
had drifted), ungrounded `entities_mentioned`/`key_facts` entries (a specific aircraft/platform
variant name or foreign-ministry mention not literally present in the item's own title/text —
e.g. "MQ-9B SkyGuardian" on item 3, "Taiwan Navy" on item 6166, "Volkswagen" on item 6872), and one
further genuine competitor fabrication beyond item 39 (item 129, "IAI equips Singapore warship
with Blue Spear missiles": "BAE Systems" named as a competitor with nothing in that item's own
source text to support it — item 129 was also one of the four re-analyzed too-thin rows, its
fresh `so_what_he` grounding clean).

**`patents` subcommand** — scope: 86 patents rows with non-null `claims_summary_he`/`so_what_he`.

| | Count |
|---|---|
| Patents checked | 86 |
| Patents changed | 1 |
| Clauses/entries removed — entity | 1 |
| `needs_manual_review` (too thin, no re-analysis path in this round's scope) | 1 — patent 86 (WO2017087031A1) |

Patent 86's `so_what_he` named "BAE Systems" as a competitor alongside the actual assignee
(Raytheon) with nothing in the patent's own abstract to support it; the claim was its own entire
`so_what_he` (one long clause with no sentence-ending period until the very end), so removing it
correctly leaves the field empty rather than a partially-true fragment — flagged for the patents
owner to re-generate (`agent/eoa/patents/**` is out of scope for this round).

## 4. Tests

`tests/unit/test_analysis_grounding.py` (21 tests, all passing) — item 39's exact text as a
fixture (affiliation clause removed and rest kept coherent, competitors removed, `too_thin`
correctly flagged), the "Elop, part of Elbit Systems"/"Elta, subsidiary of IAI" grounded-affiliation
regression (must survive), the registry-overrides-co-occurrence case (Ophir/IAI co-occurring in a
*source* sentence still rejected), entity grounding for key_facts/entities_mentioned, competitor-list
partial vs. whole-clause stripping, and number grounding (ungrounded money figure stripped, a
grounded one survives).

`tests/unit/test_analyze_key_facts_entities.py` (22 tests, all passing) — one pre-existing test
(`test_persist_analysis_unions_additional_entity_into_non_empty_list`) had an unrealistic `""`
`clean_text` fixture that made the new guard's (correct) stripping of a genuinely-ungrounded
pre-existing `entities_mentioned` entry indistinguishable from a bug in the watchlist-union logic
that test actually exercises; updated the fixture's `clean_text` to actually mention both names
(matching realistic production data, where `clean_text` is never empty for an item that has already
been analyzed) rather than weakening the new guard.

`tests/unit/test_round6_entities.py` (this round's own broader-sweep verification, beyond the
`--collect`-targeted files above, surfaced 2 real regressions from an earlier, unconditional version
of the `persist_analysis` wiring — see the "Wiring" section's own note above for the fix
(`entities_mentioned` grounding gated on `_entity_persistence_allowed`) and the one further test
fixture updated to a realistic, non-empty `clean_text`
(`test_persist_analysis_events_fallback_skipped_when_entities_already_present`) for the same
reason as the `test_analyze_key_facts_entities.py` fix above. All tests in this file pass after
both fixes.

`ruff check` clean on every file touched (`agent/eoa/pipeline/analysis_grounding.py`,
`agent/eoa/pipeline/analyze.py`, `agent/eoa/config.py`, `scripts/repair_round14_grounding.py`,
`tests/unit/test_analysis_grounding.py`, `tests/unit/test_analyze_key_facts_entities.py`,
`tests/unit/test_round6_entities.py`).

## 5. Known limitations (measured, not yet tightened further — precision-first per this codebase's
own stated convention: "measure live false positives before tightening further")

- **Country-name coverage.** `_country_grounded` (a reverse lookup over
  `eoa.pipeline.entity_normalize`'s own private `_COUNTRY_NAMES` table) lets a Hebrew-only source
  ground an `entities_mentioned` entry stored in English (e.g. a Hebrew article about Germany
  grounds an English "Germany" entry) — but only for the ~15 countries already in that table.
  Live-found gap: item 6163 (Estonia) — "Estonia" is not in `_COUNTRY_NAMES` at all, so it was
  stripped from `entities_mentioned` even though the (Hebrew) source is entirely about Estonia.
  Not fixed here — `entity_normalize.py` is a shared, heavily-used module outside this round's own
  file list; flagged as a follow-up for whoever owns country-table coverage.
- **Missing Hebrew watchlist aliases.** Item 6163 also lost "RTX" from `entities_mentioned`: the
  Hebrew source names Raytheon as "ריית'יון", but `config/watchlist.yaml`'s RTX entry has no
  Hebrew alias registered at all (only "Raytheon"/"Raytheon Technologies"/"Collins Aerospace").
  Same "not this round's file to expand" reasoning as above.
- **Parenthetical technical-gloss exemption is a coverage trade, not free.** Rule (a) never treats
  a Latin phrase *entirely inside parentheses* as an organisation-name candidate (see
  `_org_candidates`'s own docstring) — this is required to avoid flagging every legitimate
  "MWIR (Mid-Wave Infrared)"-style technical-term gloss `prompts/analyze.md` explicitly asks the
  model to write, but it means a fabricated company mentioned *only* parenthetically and never as
  a sentence's own subject would not be caught by rule (a) alone (rule (c)'s competitor-list check
  is unaffected — it has its own, narrower detection that explicitly expects this exact shape).
- **Trailing-token over-capture on a Hebrew head-noun match.** `_HEBREW_ORG_RE` captures up to 3
  tokens after a head noun as a shape heuristic (mirroring `ask_grounding._HEBREW_INSTITUTION_RE`'s
  own, documented version of the same trade-off) — it sometimes swallows a following verb
  ("משרד ההגנה מתכנן להעניק" — "the MoD plans to grant" — captured as one candidate). A verb-glued
  variant of an otherwise-grounded entity can still fail to ground even when the bare entity name
  would have; live-found on item 290 (two of seven "משרד ההגנה" mentions there were dropped this
  way, down from seven before an earlier iteration of this round's own tuning — see the module's
  own head-noun-list comment for the larger false-positive classes ("תעשייה"/"יחידת"/"קבוצת" as
  ordinary vocabulary, not organisation names) that tuning pass already removed).
- **`entities_mentioned` re-grounding is retroactive.** A name that was correctly grounded when
  `classify.py` (an earlier stage) first extracted it can still be re-stripped here if the
  analyze-stage view of the source text (title/clean_text/raw_text/url) doesn't happen to contain
  it either — in production this is the same text classify itself saw, so this is expected to be a
  rare, genuine defense-in-depth catch (a classify-stage hallucination), not a systematic
  regression; no live instance of this specifically was found in the round-14 sweep beyond the
  country/alias gaps above.

## 6. Reports that must be rebuilt

Data-layer fix only — every report/export that read the fabricated `summary_he`/`so_what_he` needs
a fresh build to show the corrected text (report builders read `items` live at build time, no
caching, so this is a rebuild, not a further code change):

- `output/reports/weekly_2026-09-07.md` / `.html` / `.docx` — lines 111, 133, 175, 438, 506, 525
  (the item-39 "תעשייה אווירית" / Ophir Optronics table row and its fabricated competitors, plus
  three narrative mentions of "תעשייה אווירית" as a market leader that trace back to the same
  fabrication).
- `output/reports/bd_il_2026-09-07.md` / `.html` / `.docx` — lines 23, 68, 139, 144 (the item's
  event-table row and BD action-item rows attributing the SupIR-X launch to "תעשייה אווירית").
- Any `daily_*`/`monthly_*` report covering item 39's publish date should also be checked for the
  same lines and rebuilt if present (not found in this round's own grep of the currently-generated
  report set, which only covers `weekly_2026-09-07`/`bd_il_2026-09-07`).
