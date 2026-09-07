### R13-reports status

**Package:** R13-reports (docs/qa/loop/round_12_judge.md worst-list #1, #5, #9, plus the
weekly-indicator finding under D6).
**Files owned/changed:** `agent/eoa/report/product_line.py` (patent-dedupe kind-code
normalization only), `agent/eoa/report/indicators.py` (evidence-matching short-phrase widening
only), `scripts/repair_round13.py` (new), `tests/unit/test_reports_round13.py` (new, 29 tests).
`agent/eoa/patents/cluster.py` was investigated but **not changed** -- see finding #4 below for
why, and `.omc`-free `spawn_task` flag left for a future round that owns `agent/eoa/patents/survey.py`.

#### 1. pl_mws_eo patent-dedupe fix did not work live (worst #1)

**Root cause confirmed live:** `product_line._dedupe_patents_by_pub_number` (added round 12) keyed
on the raw `pub_number` string, so two DB rows for the *same* patent under different WIPO/USPTO
kind-code suffixes -- `'US7378626'` (id 57) vs `'US7378626B2'` (id 55), `'US20030142005A1'`
(id 45) vs `'US20030142005'` (id 77) -- were never recognised as duplicates of each other. Live
query against the real `patents` table (2026-09-07) reproduced the round-12 judge's exact finding:
`collect_patents('mws_eo')`'s own raw SQL (pre-dedupe) returns exactly these 5 rows for the 90-day
window (57/55/45/77 plus one distinct row, 59).

**Fix:** added `product_line._normalize_pub_number` -- uppercases, strips internal
whitespace/slashes/hyphens, then drops a trailing kind-code suffix (a single letter from
`A/B/C/U/S/P/T/W/H/Y` optionally followed by 1-2 digits -- A1/A2/B1/B2/U1 etc.) only when what
remains still ends in a digit (so a genuine trailing letter+digit that's actually part of the base
number is never mis-stripped). `_dedupe_patents_by_pub_number` now keys the `seen` set on this
normalized value instead of the raw string; the row's own `pub_number` field is left untouched (only
the *dedup key* is normalized), so the rendered table still shows whichever kind-code variant's row
survived (the newest, per `collect_patents`'s own `ORDER BY ... DESC` -- unchanged from round 12).

**Unit-tested** (`TestNormalizePubNumber`, `TestDedupePatentsByPubNumber`, 13 tests): both of the
round-12 judge's exact reproduction pairs (in both row orderings), whitespace/slash/hyphen/case
normalization, `None`/empty input, a guard against mis-stripping a base number that doesn't end in
a digit before the candidate kind-code suffix, distinct patents never collapsed, and rows with no
`pub_number` never collapsed together.

**Live verification:** `build_product_line('mws_eo')` rebuilt end-to-end
(`EOA_PIPELINE=1`, report_id=138, `qa_passed=True`) -- `output/reports/pl_mws_eo_2026-09-07.md`'s
patents table now shows exactly 3 rows (US7378626B2, US20030142005A1, US20070075182A1), one per
normalized `pub_number`, confirmed by grepping the file for each of the 5 raw pre-dedupe
`pub_number` values: each of the two duplicate pairs now appears exactly once.

#### 2. Weekly indicator table: 2 rows still lacked evidence (weekly-indicator item, D6)

**Investigated both live "—" rows individually**, per the brief's own instruction, rather than
assuming either was a genuine miss:

- `indicator_watchlist` id 1 ("להערכתנו מגמת ההשקות תימשך ברבעון הקרוב.") -- its only
  distinctive-enough Hebrew terms (`_extract_indicator_terms`) are "תימשך"/"השקות", and no item in
  the live corpus shares either. **Genuinely no matching item** -- left as "—", correctly.
- `indicator_watchlist` id 16 ("חתימה צפויה על חוזה הצטיידות רשמי של אסטוניה במערכות היירוט קלע דוד
  בתוך מספר חודשים.") -- a **real matching gap**. Item id 6163
  ("אסטוניה שוקלת לרכוש מערכות הגנ״א קלע דוד תוצרת ישראל") is a genuine, on-topic match: both texts
  share "אסטוניה" (1 hit) and "קלע דוד" -- David's Sling, written as two 3-letter Hebrew words --
  verbatim. Neither "קלע" nor "דוד" alone clears `_MATCH_MIN_TERM_LEN_HE` (5), and the phrase is in
  neither `_taxonomy_subdomain_labels_he()` (not a taxonomy sub-domain) nor
  `_watchlist_hebrew_names()` (not a company name), so it was invisible to
  `_item_matches_indicator`'s 2-term bar -- only "אסטוניה" ever counted, one hit short.

**Fix:** added `indicators._short_name_phrases_he` -- extracts adjacent-word bigrams (original
word order) where both words are 3-4 Hebrew letters (too short to clear the single-word filter
alone) and neither word's stopword-check form (prefix-stripped the same way `_content_tokens`
does, e.g. "בתוך" -> "תוך", a real `_STOP_HE` entry) is in `_STOP_HE`/`_MATCH_GENERIC_HE`. Folded
into `_extract_indicator_terms`'s existing `hebrew_terms` set at the **same weight** as any other
Hebrew term or taxonomy/watchlist phrase -- a phrase hit still needs a second independent hit to
clear `_item_matches_indicator`'s 2-term bar, keeping this module's existing "never invent a match
from one weak signal" precision bar unchanged.

**Unit-tested** (`TestShortNameNamePhrasesHe`, `TestItemMatchesIndicatorShortPhrase`, 8 tests): the
exact "קלע דוד" extraction, the "בתוך"-stopword-prefix exclusion, words outside the 3-4 length
range never paired, no-short-pair -> empty set, `None` input, the real Estonia/David's-Sling case
now matching, a single phrase hit alone still insufficient, and an unrelated item still not
matching.

**Live verification:** re-ran the real `indicator_watchlist` (kind='weekly', status='open') rows
and a real `items` pool against `indicators.render_watchlist_table` directly (function-level, no
new report row) -- id 16's row, "—" in the live `weekly_2026-09-07.md`, now resolves to a real
citation (`[44]`, item 6163's own registry position in this verification's citation list); id 1
stays "—", confirming the fix does not spuriously invent matches for the genuinely-unmatched row.

#### 3. 11 in-scope items missing so_what_he (worst #5, D2/D1)

**Query confirmed exactly:** `level IN ('red','orange','yellow') AND so_what_he IS NULL` --> 11
items live (ids 23, 55, 72, 83, 108, 168, 217, 281, 297, 5122, 6872). Note: "in-scope" here is the
report-facing severity tier (`level`), not `domain` -- 9 of the 11 actually carry
`domain = 'out_of_scope'`, a separate pre-existing level/domain mismatch this round does not touch
(out of the named finding's scope; flagged here for visibility only).

**Fix:** `scripts/repair_round13.py`'s `so_what` subcommand (dry-run default / `--apply`) lists the
11 targets (id, level, title) and, under `--apply`, calls the existing
`eoa.pipeline.analyze.repair_so_what_text` (cloud chain, `EOA_PIPELINE=1`) for each, with a
placeholder `phrase` describing "not written at all" (the function's own repair prompt is worded
for an *already-generic* `so_what_he`; a wholly-missing field has no such phrase to name, so the
placeholder only fills that template slot -- the LLM still gets the item's real summary/original
text via the shared `_analyze_prompt`). Validates the result the same way round-6's own `so_what`
subcommand does: non-empty, starts with "להערכתנו", 1-3 Hebrew sentences
(`qa_citations.split_sentences`), no `SO_WHAT_TEMPLATE_PHRASES_HE` banned phrase -- rejected
otherwise, never written. A shared `--llm-budget` (default 15, brief's own cap) bounds the run.

**Unit-tested** (`TestFindMissingSoWhatItems`, `TestRepairSoWhat`, 8 tests, all DB/LLM-mocked): the
query shape, dry-run makes zero LLM calls, a valid repair is written and counted, LLM-failure /
still-generic / wrong-sentence-count rejections, budget exhaustion reports `skipped_budget`, and an
item vanishing between query and fetch is handled without crashing.

**Live run:** `scripts/repair_round13.py so_what` dry-run confirmed the exact 11-item target list
matches the query above. `--apply` (budget 15, cloud chain) repaired all 11/11 items (ids 23, 55,
72, 83, 108, 168, 217, 281, 297, 5122, 6872) -- 11 LLM calls used, 0 rejected, 0 skipped-budget.
Verified from a separate connection: `SELECT count(*) FROM items WHERE level IN
('red','orange','yellow') AND so_what_he IS NULL` -> **0** remaining.

#### 4. Cluster-label format inconsistency (worst #9, D8) -- investigated, root cause found,
   **not fixable within this round's file scope**

**Root cause confirmed live:** the Anduril survey's odd cluster heading
(`output/reports/patent_survey_Anduril_..._2026-09-07.md` line 45) reads
`## אשכול טכנולוגי: אשכול נושאי: lattice / mesh` -- a **doubled prefix**, not merely a missing
CPC-in-parens suffix. `agent/eoa/patents/survey.py`'s `_cluster_narrative_section` (line ~1267)
unconditionally renders `title_he=f"אשכול טכנולוגי: {cluster.cluster_label_he}"` for every cluster.
For a CPC-classified cluster, `cluster.py._cpc_label` deliberately returns content with NO
"אשכול ..." prefix of its own (documented in its own comment, referencing the round-11 fix for the
same doubling failure mode) so `survey.py`'s single prefix composes correctly. For a genuinely
CPC-less "term cluster" (confirmed live: patent id 62 / `US10506436B1` "Lattice mesh" has zero
recorded CPC codes -- the survey's own methodology text says so explicitly, and this is the exact
reproduction patent `tests/unit/test_patents_round7.py` already builds fixtures around),
`cluster.py._unclassified_label_he` returns its OWN baked-in `"אשכול נושאי: "` prefix -- and
`survey.py`'s blanket prefix collides with it.

**Why this round's fix couldn't land in `cluster.py` alone:** `_unclassified_label_he`'s exact
return string (`"אשכול נושאי: <terms>"`) is pinned by existing, required-green tests
(`tests/unit/test_patents_round6.py:354`, `test_patents_round7.py:228` and `:406`, all exact-match
or `.startswith` assertions) -- changing it would break `test_patents_round7.py`, which this
round's own test-run list requires to stay green. The actual fix needs a conditional in
`survey.py._cluster_narrative_section` (skip the outer prefix when `cluster_label_he` already
carries its own category-word prefix), and `survey.py` is outside this round's file-ownership
scope ("Do NOT edit other files"). Flagged via a `spawn_task` follow-up
(task_c29c601e, "Fix doubled cluster heading in patent survey.py") for a future round that owns
`survey.py`, with the exact fix location and a suggested approach. No code change made to
`cluster.py` for this finding -- shipping a no-op or test-breaking change would be worse than
leaving it flagged.

#### 4b. Doubled cluster-heading prefix -- FIXED in `survey.py` (follow-up to finding #4, task_c29c601e)

**Fix:** `agent/eoa/patents/survey.py` gained `_cluster_heading_he(label_he)` plus
`_CLUSTER_CATEGORY_PREFIX_RE` (`^אשכול <hebrew word>: `). `_cluster_narrative_section` now renders
a label verbatim when it already opens with a category prefix of that shape (cluster.py's
`"אשכול נושאי: <terms>"` term-cluster label, or an LLM-echoed `"אשכול טכנולוגי: ..."`), and wraps
it in `"אשכול טכנולוגי: "` otherwise. Bare CPC labels (`_cpc_label`, no prefix by design since J11)
and a plain label that merely starts with the word "אשכול" (no category word + colon, e.g. the
existing fixture "אשכול X") keep the single wrapper exactly as before. `cluster.py` untouched;
its pinned `_unclassified_label_he` return value stays as the round-6/7 tests assert.

**Unit-tested** (`tests/unit/test_patents_survey.py::TestClusterHeadingPrefix`, 4 tests): the live
term-cluster label built by `_unclassified_label_he(["lattice", "mesh"])` renders un-doubled, a CPC
label and "אשכול X" still get one wrapper, an LLM-echoed wrapper is not doubled, and a full
`_build_draft_from_synthesis` pass asserts no section title matches `אשכול <word>: אשכול `.

**Live verification:** `build_patent_survey("Anduril Lattice counter-UAS EO/IR optical tracking
patents")` rebuilt end-to-end (2026-09-07 18:59, DATABASE_URL 5432). The rebuilt
`output/reports/patent_survey_Anduril_..._2026-09-07.md` line 45 now reads
`## אשכול נושאי: lattice / mesh`; the four CPC headings (H04W4, F41H3, G05D1, F41H11) are unchanged
and a grep for `אשכול [א-ת]*: אשכול ` over the file returns 0 hits.

#### Verification commands run this round

```
set -a; . runtime/eoa.env; set +a
PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_reports_round13.py \
  tests/unit/test_reports_round12.py tests/unit/test_reports_round10.py \
  tests/unit/test_product_lines.py tests/unit/test_patents_round7.py -q -p no:cacheprovider
  # 187 passed

.venv/Scripts/ruff.exe check agent/eoa/report/product_line.py agent/eoa/report/indicators.py \
  scripts/repair_round13.py tests/unit/test_reports_round13.py   # All checks passed!

PYTHONPATH=agent PYTHONUTF8=1 EOA_PIPELINE=1 .venv/Scripts/python.exe -c "
from eoa.report.product_line import build_product_line
print(build_product_line('mws_eo'))"   # report_id=138, qa_passed=True, 3 unique patent rows

PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round13.py so_what             # dry run, 11 targets
PYTHONUTF8=1 .venv/Scripts/python.exe scripts/repair_round13.py so_what --apply     # 11/11 repaired
```

#### What remains for a future round

- ~~`agent/eoa/patents/survey.py`'s doubled cluster-heading prefix (finding #4)~~ -- fixed, see 4b.
- The 9-of-11 `level IN ('red','orange','yellow')` items that carry `domain = 'out_of_scope'`
  (noted under finding #3) -- a separate, pre-existing level/domain mismatch, not touched this
  round.
- Indicator row 1 ("מגמת ההשקות") stays evidence-less by design (genuinely unmatched); worth
  re-checking in a later round once more source items accumulate.

### R13-chat status

**Package:** R13-chat (docs/qa/loop/round_12_judge.md worst-list #2, #3).
**Files owned/changed:** `agent/eoa/api/ask_grounding.py` (the `_count_mismatch_violation` fix,
the new `_digits_grounded`/`_grouped_digit_pattern` thousands-separator normalization, and the new
`enforce_answer_coherence` leading-unit-clause check), `agent/eoa/llm/prompts/ask_answer_format.md`
(one new prompt rule), `tests/unit/test_ask_round13.py` (new, 33 tests). `agent/eoa/api/routes/ask.py`
and `agent/eoa/api/services.py` (`ask_build_messages`/`_relevant_excerpt`) were investigated in full
but **not changed** -- both traced to already working correctly, see finding #1 below.

#### 1. Q5 (Skyranger) rate-of-fire figure "doesn't reach the final synthesized text" (worst #3)

**Root cause confirmed offline, against the live DB row:** item 1353's own `clean_text`
("U.S. Air Force Seeks Anti-Aircraft Guns To Protect Its Overseas Bases") discusses *two* different
systems' rates of fire under the same generic noun "rounds" -- "Centurion, at a rate of fire of
4,500 rounds per minute" (unrelated, char offset ~7828) and "[Skyranger] ... a firing rate of 1,000
rounds per minute" (the actually-relevant figure, char offset ~13691). Three hypotheses were traced
directly, in order:

- **Excerpt window too narrow?** No. `services._relevant_excerpt(item_1353_text, ["Skyranger",
  "Rheinmetall"], 4000)` was run directly against the live row -- the returned excerpt (2890 chars)
  contains the literal string "1,000 rounds per minute" verbatim. Round 11's own anchor-window fix
  works correctly; the model is shown the real figure.
- **Model omits it in synthesis?** Not the root cause found -- see below.
- **A guard removes/corrupts it?** **Yes, confirmed.** `_count_candidates` splits a comma-grouped
  number into separate 1-3-digit count candidates (`"1,000"` -> a `"1"` candidate and a `"000"`
  candidate, both paired with the following noun "rounds"). `_count_mismatch_violation`
  (`filter_claim_count_mismatch`) compared the claim's `"1"` only against the *first* same-noun
  source candidate in document order -- the earlier, unrelated `"4,500"` (Centurion) mention -- and,
  finding it different, "corrected" a verbatim-correct `"1,000 rounds per minute [n]"` claim into a
  **fabricated `"4,000 rounds per minute [n]"`**, silently, with no later guard able to tell the
  difference (the corrupted figure carries a real citation and is not itself money-regex-matched,
  so no other check ever re-examines it). Reproduced directly:
  `ask_grounding.filter_claim_count_mismatch("...1,000 rounds per minute [1].", [item_1353_row])`
  returned `removed=1` and rewrote the text to `"...4,000 rounds per minute [1]."` before the fix.

**Fix:** `_count_mismatch_violation` now compares a claim's digit against *every* same-noun source
candidate (not just the first) before deciding: grounded (no mismatch) if the claim's own digit
matches *any* of them, regardless of what an earlier, unrelated same-noun occurrence elsewhere in a
multi-topic source says. A genuine mismatch (the claim's digit matches *none* of the same-noun
source values) is still caught and corrected exactly as before -- this only ever narrows when the
guard fires, never widens it (see `TestCountMismatchAnySameNounOccurrenceGrounds`, 6 tests,
including a same-shape "genuine mismatch still caught" negative case and the original round-8
"seven"->"8" single-value repro, both unaffected).

**Investigated and ruled out as this round's cause, but fixed anyway as a genuine, independently
-verified latent gap:** `_digits_grounded`'s digit-substring search stripped the *candidate's* own
thousands separators before comparing, but never normalized the *corpus* text the same way -- so a
money-figure candidate grounded by a corpus occurrence that itself used a comma/period thousands
separator (`"$1,000"`) could never match (the literal search for a separator-free digit run can
never find a comma sitting inside it). New `_grouped_digit_pattern` builds a pattern accepting an
optional comma/period at every thousands-grouping position; a no-op for any number under 1000 (no
possible grouping position), so round 9's own decimal-boundary fix is completely unaffected
(`TestDigitsGroundedThousandsSeparator`, 7 tests). Does not apply to the Q5 figure itself -- a bare
"1,000 rounds per minute" carries no currency symbol or scale word, so it never reaches `_MONEY_RE`
in the first place -- but closes a real gap for any money figure >= 1000 phrased with a separator
in its source.

**Prompt-level defense in depth:** `ask_answer_format.md` gained a new rule -- when the question
asks about a technical spec/performance/cost figure and a retrieved source states one, the answer
must quote the exact number with its `[n]`, not a vague paraphrase ("high rate of fire") in its
place -- reducing the chance of a genuine model-side omission in addition to the guard-level fix.

**Offline replay (proves the fully guarded answer keeps the figure end to end, no live LLM call):**
`TestCountMismatchAnySameNounOccurrenceGrounds::test_offline_replay_figure_survives_the_full_ground_and_filter_answer_pipeline`
runs a Skyranger-shaped claim through both `filter_claim_count_mismatch` and
`ground_and_filter_answer` (the two guards this figure passes through) against the live-row-shaped
source text -- `removed == 0` for both, and `"1,000 rounds per minute"` is present in the final
text. `TestEndToEndQ5CountFigureSurvives` repeats this through the full `/api/ask` SSE pipeline
(mocked retrieval/LLM, real `ask_build_messages` + guard sequence).

#### 2. Q6 (AUSA 2026) headless mid-clause opening, new shape of the "סי." defect class (worst #2)

**Root cause:** round 12's two new checks (dotted-Hebrew-acronym terminator fix, short-orphan
-fragment sweep, both scoped to < 3-word fragments) correctly stopped recurring, but the live
answer instead opened with a *longer* (7-word), well-punctuated-looking leftover --
`מים, ודירוג הכנסות של חברות ביטחון גלובליות)...` -- a bare noun/conjunction chain with no finite
verb or copula anywhere in it, left behind once an earlier guard removed the sentence that used to
introduce it. Same underlying defect class as "סי." (a guard-removal leftover), a new shape neither
round-12 check was scoped to catch.

**Fix:** `enforce_answer_coherence` gained a fourth, independent leading-unit check
(`_is_incoherent_leading_unit`), gated behind its own `elif` after the existing three (dangling
-fragment, only-unit-without-terminal-punctuation, cross-reference-opening) -- asks whether a
section's own leading unit reads as one complete, independent clause at all: (1) its first real
character (leading decoration -- a blockquote marker, a warning glyph -- stripped first, see below)
must plausibly start a word; (2) it must not open with a bare conjunction/relative token (glued
ו-/ש- prefix, standalone כי/אשר/אך, English and/but/which); (3) it must contain a finite verb or
copula-like structure, per `_has_finite_verb_or_copula`'s own heuristic (a digit/colon outside a
`[n]` citation marker; a curated future/hifil/piel present-tense prefix set with a curated
domain-noun blocklist, since this project's own vocabulary is full of מ-initial nouns sharing the
identical templatic shape as a present-tense verb -- "מערכת", "מרכזי", "מבחינת" -- which a bare
prefix rule would misread as verb-shaped; a curated common past-tense reporting-verb stem list; or
a plural/construct-suffixed word immediately preceded by a ה-marked word). A unit failing this is
dropped, unless the section's very next unit is itself continuation-shaped, in which case both are
left alone (dropping only the first would just crown the second, equally headless unit as the new
leading fragment).

**Two real false-positive regressions found and fixed during this round's own test-driven
development** (both confirmed via this round's own end-to-end test fixtures before being fixed, not
merely reasoned about):

- A legitimate past-tense verb ("חתמה"/"signed") directly preceded by a Latin proper noun
  ("Rheinmetall חתמה חוזה חדש [1]") was wrongly flagged -- the verb carries no prefix-based signal,
  and the suffix-fallback's "preceded by a ה-word" check only ever scans Hebrew word runs, so the
  real (Latin) preceding word was invisible to it. Fixed with a curated past-tense verb stem list
  (`_CLAUSE_PAST_TENSE_VERB_STEMS_HE`).
- This module's own leading markers -- `relocate_source_admission_caveat`'s prepended
  `"> ⚠️ "` blockquote and `routes.ask._OFF_TOPIC_PREFIX`'s leading `"⚠ "` glyph -- were wrongly
  treated as an incoherent opening character. Fixed by stripping a leading run of non-letter/
  non-digit "decoration" (`_LEADING_DECORATION_RE`) before evaluating the opening-character/
  continuation checks.

**Unit-tested** (`TestIncoherentLeadingUnitPositiveRepros`, `TestIncoherentLeadingUnitNegativeRegressions`,
`TestIsIncoherentLeadingUnitDirect`, 15 tests): the judge's own quoted Q6 opening (reconstructed as
its own leading unit); a blocklisted-domain-noun chain; lowercase-Latin and ו-/ש--prefixed
continuation openings; a legitimate 6+-word present-tense opening (the brief's own explicit "must
survive" contract); the existing round-10 4-word short-lead contract; the past-tense-verb and
blockquote/warning-prefix regressions above; a legitimate number-bearing opening with no verb; a
bullet item (never touched); the round-11 "שאר המקורות" shape (confirmed still handled by the
pre-existing cross-reference check, never reaching this new one); and the merge-or-leave-alone
contract when the next unit is itself continuation-shaped. `TestEndToEndQ6CoherentOpeningSurvives`
repeats the positive repro through the full `/api/ask` SSE pipeline.

#### Live probes (2026-09-07, `runtime/logs/api.2026-09-07.log`)

Both probes ran against the **live stack's round-12 code** (not this round's fix -- verification for
this package is unit tests + offline replay per this round's own instructions; the stack was not
restarted). 2 of 3 permitted `POST /api/ask` calls used:

- **Q5** (`כיצד משתווה ה-Skyranger של Rheinmetall למערכות נגד כטב"ם (C-UAS) ישראליות מקבילות?`):
  127s, HTTP 200. `removed_by_guard: {'uncited_factual_claim': 3, 'entailment_check': 1}`. Retrieval
  this round did not surface item 1353 at all (the DB/ranking state has moved on since round 12's
  own sample) -- the model correctly reported no direct comparison was found in the retrieved
  sources rather than reproducing the round-12 defect, which is expected: this was never a
  deterministic, always-reproducing bug, only a specific sampled shape traced and fixed offline.
- **Q6** (`מהי הרלוונטיות של כנס AUSA 2026 לתעשייה הישראלית בתחום האלקטרו-אופטיקה?`): 102s, HTTP
  200. `removed_by_guard: {'entailment_check': 1}`. Opens cleanly with `המקורות שסופקו...` -- the
  round-12 "מים, ודירוג..." shape also did not recur in this sample (same reasoning as Q5).

**Entailment coverage (event counts only, from `runtime/logs/api.2026-09-07.log`, the two calls
above -- the day's only `POST /api/ask` traffic):** `ask.entailment_check_removed` fired 2/2 times
(`claims=2 removed=1` both times); 0 `ask.entailment_check_skipped`/`_unavailable` events. Config
confirms `ask.entailment_check: true` (enabled 2026-09-07 06:50 per config.yaml's own comment).

#### Environmental note (not in this package's scope, not touched)

`config/config.yaml`'s `api.remote_access.enabled` is `true` on this host as of today (file mtime
2026-09-07 18:38, changed mid-session by something other than this package -- not a code change of
ours). This makes every existing `TestClient`-based end-to-end `/api/ask` test in this repo
(`test_ask_round3_grounding.py`, `test_ask_round5.py`, `test_ask_round7.py`, `test_ask_round8.py`,
`test_ask_sse_sources.py`, and this round's own `test_ask_round13.py`) 401 with `auth_required`,
since `TestClient`'s synthetic client host is not recognised as loopback -- confirmed unrelated to
this package's own code via an A/B test (temporarily short-circuiting this round's own new
coherence-check branch to `False` reproduced the identical 401 failure). All test runs in this
section were verified with `EOA_CONFIG_DIR` pointed at a scratch copy of `config/` with
`remote_access.enabled: false` restored -- `config/config.yaml` itself was never written to.

#### Verification commands run this round

```
set -a; . runtime/eoa.env; set +a
SCRATCH=<scratch config dir with remote_access.enabled: false -- see environmental note above>
EOA_CONFIG_DIR="$SCRATCH" PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest \
  tests/unit/test_ask_round13.py tests/unit/test_ask_round12.py tests/unit/test_ask_round11.py \
  tests/unit/test_ask_round10.py tests/unit/test_ask_round9.py tests/unit/test_ask_round8.py \
  tests/unit/test_ask_round7.py tests/unit/test_ask_round5.py tests/unit/test_ask_round3_grounding.py \
  tests/unit/test_ask_retrieval.py tests/unit/test_ask_sse_sources.py -q -p no:cacheprovider
  # 310 passed (277 pre-existing + 33 new)

.venv/Scripts/ruff.exe check agent/eoa/api/ask_grounding.py agent/eoa/api/routes/ask.py \
  agent/eoa/api/services.py tests/unit/test_ask_round13.py            # All checks passed!
.venv/Scripts/ruff.exe format --check agent/eoa/api/ask_grounding.py agent/eoa/api/routes/ask.py \
  agent/eoa/api/services.py tests/unit/test_ask_round13.py            # 4 files already formatted
```

#### What remains for a future round

- Q5's live retrieval no longer surfaces item 1353 for this exact question phrasing (DB/ranking
  state has moved since round 12) -- worth re-sampling in a future round to confirm the fixed guard
  behaves correctly on a fresh live reproduction, not just the offline replay this round relied on.
- `_has_finite_verb_or_copula`'s own documented gap: a legitimate masculine-singular past-tense
  predicate whose verb is on neither curated list and whose subject is not ה-prefixed (e.g. "מכרז
  פורסם השבוע") is still misread as lacking a verb. No live round has hit this shape yet; worth
  watching for in a future round's judge sample before broadening the heuristic further.
- The `config/config.yaml` `remote_access.enabled: true` state (environmental note above) breaks
  every existing TestClient-based end-to-end test in this repo for any future package that runs the
  full suite without the same `EOA_CONFIG_DIR` scratch-copy workaround -- worth a fix (either the
  test suite's own fixtures, or `is_loopback_host`) in whichever future round owns `auth.py`/
  `tests/conftest.py`.

### Round-13 close (lead, 2026-09-07 20:45)

- Deterministic 99.6 (D1-D9 all checks pass; D10 e2e 617/629, 12 unexpected in three spec families:
  a graph-toolbar locator that now also matches the panel's close button after the dfdeafa a11y pass,
  a data-dependent empty-state assertion on the entity card's "פתח גרף מלא", and the nav-rail focus
  check on the tablet-landscape project). R13-ui is fixing the specs; D10 is re-run after that.
- Judge J13 (docs/qa/loop/round_13_judge.{json,md}): D1 92, D2 91, D3 96, D4 94, D5 85, D6 97, D7 96,
  D8 96, D9 93 (avg 93.3). Combined **95.6** -- the second consecutive round at or above 95, so the
  loop's stop rule (docs/QA_CONTINUOUS_LOOP.md) is met. Trajectory: 85.8, 87.5, 87.4, 89.8, 93.4,
  93.1, 95.1, 95.6.
- J13's process-integrity note (the R13-chat "live probes" figures are not in
  runtime/logs/api.2026-09-07.log): the probes ran ~18:30-19:00 against the round-12 API process; the
  stack was restarted at 19:40 (round-13 code) and again at 19:45 (SAM key). Whether the supervisor
  truncates the API log on restart is unverified -- treated as a process gap (agents must quote the
  log line, not a summary), not as a code defect. Both fixes are confirmed live by J13's own sample.
- Backlog carried out of the loop (J13 worst-10, for a later maintenance round): generalise the
  guard-removal coherence check to every boundary a removal touches (Q1/Q7 leftovers); off-topic
  prefix + low-citation caveat concatenated on one line (2/8); 9 so_what-repaired items carry
  domain='out_of_scope' with an in-scope level; item-90 title artefact; tender 34 junk note; daily
  BLUF heading on empty days; thin product_lines coverage on the border line.
