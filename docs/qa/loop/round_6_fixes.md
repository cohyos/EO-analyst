# Round 6 fixes

## R6-forecast package

### R6-forecast status

Scope: `docs/qa/loop/round_5_judge.md` findings D6/D9 (tender-forecast near-duplicates) and D3
(event-kind mismatch), as assigned in the R6-forecast task package. Files touched: only
`agent/eoa/tenders/forecast.py`, `agent/eoa/tenders/report_section.py`,
`agent/eoa/report/daily.py` (`_dedup_events`/`_event_has_signal`/`_sanitize_event_kind` and the
forecast-table builder only), `agent/eoa/pipeline/analyze.py` (event-kind classification for
exercises only), and `tests/unit/test_round6_forecast.py` (new). Did not touch
`agent/eoa/memory/relational.py` or `agent/eoa/tenders/scan.py` (R6-data's files).

#### Finding 1 — tender-forecast near-duplicate rows

**Root cause found**: not buyer_country volatility as initially hypothesised, but a Hebrew
punctuation drift. `platform_payloads.yaml` today uses a plain ASCII quote/apostrophe for Hebrew
abbreviations (`כטב"ם`, `רק"ם`, `מטע"ד`, `ג'ימבלי`), but a number of `tender_forecasts` rows were
written earlier with the Hebrew gershayim/geresh marks (`כטב״ם`, `רק״ם`, `מטע״ד`, `ג׳ימבלי`) — the
exact same platform, but different bytes. `tender_forecasts` has `UNIQUE (platform, buyer_country,
payload_need)` (db/migrations/versions/0004_tenders.py) on that literal text, so the punctuation
mismatch alone makes `ON CONFLICT` miss and insert a new row (with the window recomputed from
today) instead of updating the old one — exactly the "same platform/payload/reasoning text, window
shifted by a day or two" pattern in the finding. Confirmed by reading the live `tender_forecasts`
rows (id 5 vs 53, id 1 vs 59, id 2 vs 54, id 9 vs 57): each pair's `rationale_he` is about the same
underlying subject; the only reliable signal distinguishing genuine near-duplicates from real
distinct-buyer forecasts (id 6 vs 55, id 7 vs 56 — same platform text, genuinely different
`buyer_country`, kept separate by design) was the punctuation.

Fixes:
1. **Generation time** (`forecast.py`): new `normalize_hebrew_punctuation()` — canonicalises
   gershayim (`״`) → `"` and geresh (`׳`) → `'`. Applied in `PlatformSpec.__init__` so
   every candidate built from `platform_payloads.yaml` always writes the same canonical text
   regardless of which punctuation mark a future yaml edit uses, keeping `ON CONFLICT` working
   correctly going forward.
2. **Existing-row repair (dry-run + apply)** (`forecast.py`): `_forecast_stable_key()` — a
   normalised `(platform, buyer_country, payload_need)` identity ignoring window/likelihood/
   rationale/sources/dates. `find_duplicate_forecast_groups(rows)` — pure grouping function (no DB),
   returns `ForecastDuplicateGroup(key, ids, kept_id, dropped_ids)` for every group with >1 member
   (kept = most recently updated). `dedupe_existing_forecasts(conn, *, apply=False)` — reads all
   `tender_forecasts` rows, reports duplicate groups; with `apply=True`, merges each dropped row's
   `sources` into the kept row (order-preserving, deduped), stamps `updated_at`, and deletes the
   dropped rows. Does not commit — the caller's `with connection() as conn:` (or explicit
   `conn.commit()`) controls the transaction, same convention as every other `conn`-taking helper
   in this codebase.
3. **Report-table collection time** (`report_section.py`): `_forecast_topic_key()` now runs through
   `normalize_hebrew_punctuation()` before casefold, so a punctuation-drift pair collapses under the
   existing `dedupe_forecasts_by_topic` (W1) topic grouping even before the DB repair runs. Also
   added `_windows_close()`/`_cluster_by_window_proximity()`: within one topic group, rows are only
   merged if their windows overlap or are within `_FORECAST_WINDOW_MERGE_GAP_DAYS` (3) days of each
   other — two forecasts sharing a topic but describing genuinely distant windows now render as
   separate rows instead of being silently collapsed.

**Dry-run duplicate-group listing (live DB, 2026-09-07, `dedupe_existing_forecasts(conn,
apply=False)` — no writes)**:

```
3 duplicate group(s) found:
key=('כטב"ם טקטי/זעיר', 'EU', 'מטע"ד זעיר (micro-gimbal)')
  ids=[1, 59]  kept_id=59  dropped_ids=[1]
key=('תוכנית נגד כטב"ם (c-uas)', 'US', 'עוקבים eo/ir לגילוי-סיווג-עקיבה ואפקטורים')
  ids=[9, 57]  kept_id=57  dropped_ids=[9]
key=('רק"ם (apc/ifv/טנק)', 'EU', 'מטע"ד מפקד/תותחן (commander/gunner sights)')
  ids=[2, 54]  kept_id=54  dropped_ids=[2]
```

These three groups are the ones the DB-level (platform, buyer_country, payload_need)-exact key
flags for repair. The rendered report's third named pair (MALE UAV, `כטב"ם MALE`) is *not* in this
DB-level list because its two rows (id 5 buyer=GR, id 53 buyer=US) carry genuinely different
`buyer_country` values — kept as distinct DB rows by design (finding 1's spec: the stable key
includes buyer). It still rendered as a visible duplicate in `daily_2026-09-06.md`/`_07.md` purely
because the report table's own topic-key grouping (W1) ignores `buyer_country`; fix #3 above (the
`normalize_hebrew_punctuation` call in `_forecast_topic_key`) resolves that at render time
independently of the DB-level buyer-country difference.

**For the lead to apply the DB repair**:
```python
from eoa.db import connection
from eoa.tenders.forecast import dedupe_existing_forecasts

with connection() as conn:
    groups = dedupe_existing_forecasts(conn, apply=True)
    # conn.commit() happens automatically on successful exit of `connection()`
print(groups)  # ForecastDuplicateGroup list — same 3 groups as the dry-run above
```

#### Finding 2 — business-events near-duplicate (empty-fields copy)

`agent/eoa/report/daily.py`'s `_dedup_events` collapse key (`_normalize_event_key`: kind + parties +
customer + program) never caught a pair where one copy has every descriptive field empty and the
other has them populated (the finding's example: `2026-09-05 | אחר | — | — | 10,000,000 USD | [4]`
immediately followed by the same date/amount/kind with `US Air Force` / `Massed Modular Aircraft`
populated) — the two rows produce different content keys. Added a second dedup pass,
`_item_amount_kind_key()` / extended `_dedup_events`: groups by `(item_id, kind, date, amount_usd)`
after the existing content-key pass, keeping the richer (more populated fields) row. Deliberately
narrow — requires a concrete `item_id` *and* a non-null `amount_usd` on both sides, so two distinct
events sharing an item/date/kind but no (or different) monetary figures are never merged by this
pass; a row missing either field passes through unaffected, so the existing F9/F16 regression tests
(`test_report_daily.py::test_dedup_events_keeps_richest_of_duplicate_group` /
`test_dedup_events_keeps_distinct_kinds_separate`) still pass unchanged.

#### Finding 3 — event-kind mismatch (Kongsberg StrikeMaster / Operation Atlantic City)

Confirmed against the live DB: item 3702 produced three `events` rows sharing
`program='Operation Atlantic City'` — id 173 `kind='deployment'`, id 174 `kind='test'`
(`title='ניסוי מערכת ה-StrikeMaster בתנאים ארקטיים'`), id 175 `kind='partnership'`. Row 174 is not a
standalone weapon test; it is one facet of the same NATO Arctic exercise the sibling rows describe.
`events.kind` carries a DB CHECK constraint to the 9 `EventOut` literals (no `'exercise'` value
exists to write), so:

1. **Ingestion time** (`analyze.py`): new `_reclassify_exercise_kind()`, wired into
   `persist_analysis`'s events loop right before the narrative-title check / `insert_event` — a
   `kind='test'` event whose own title/summary/program also names an exercise/deployment/named
   operation (`_EXERCISE_VOCAB_RE`: `תרגיל`, `exercise`, `deployment`, `Operation <Name>`) is
   reclassified onto the existing `'deployment'` literal before it is ever written. Conservative:
   only ever touches `kind='test'` events carrying that vocabulary; a genuine test with none of it
   (e.g. "ירי ניסיוני של הטיל בוצע בהצלחה") is untouched, and every other kind is untouched.
2. **Report render time, existing rows** (`daily.py`): `_sanitize_event_kind` checked first for the
   same exercise vocabulary (via `_looks_like_exercise`) — a `kind='test'` row already persisted
   with the stale kind (like the live id 174) is relabeled directly to the display string
   `"פעילות מבצעית"` rather than either the misleading `"ניסוי"` or the existing W5 `'other'`
   downgrade. This label is never written back to the DB, so it isn't limited to the 9-literal CHECK
   constraint: both `docx_builder._EVENT_KIND_LABELS_HE` and this module's own
   `_EVENT_KIND_LABELS_HE_FALLBACK` render an unrecognised kind via `.get(kind, kind or "—")`, so the
   raw string renders correctly with no label-map edit needed (`docx_builder.py` is out of this
   package's scope). Pre-existing W5 behaviour (`'test'` with no exercise vocab and no test vocab
   either → `'other'`; `'test'` with only test vocab → stays `'test'`) is unchanged.

#### Tests

`tests/unit/test_round6_forecast.py` — 42 new tests, all pure logic (no DB/LLM/network; DB
interaction tested only via fake `conn`/`cursor` doubles matching this codebase's existing
convention in `test_tenders_forecast.py`). Covers: `normalize_hebrew_punctuation` (5),
`PlatformSpec` load-time normalization (2), `_forecast_stable_key` (4), `find_duplicate_forecast_groups`
(4), `dedupe_existing_forecasts` dry-run/apply (3), `report_section` topic-key normalization +
window-proximity clustering (6), `daily._dedup_events` item/amount/kind pass (6),
`analyze._reclassify_exercise_kind` + `persist_analysis` wiring (6), `daily._sanitize_event_kind`
exercise relabeling (6).

Full run (`PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest
tests/unit/test_round6_forecast.py tests/unit/test_report_daily.py tests/unit/test_tenders_forecast.py
tests/unit/test_events_dedup.py tests/unit/test_analyze_key_facts_entities.py -q -p no:cacheprovider`):
**164 passed**. `ruff check`/`ruff format --check` clean on every touched file.

## R6-chat package

### R6-chat status

Scope: `docs/qa/loop/round_5_judge.md` D5 findings (chat scored 35/100 on this round's live
8-question sample) — worst-list #2 (Q2/Iron Beam, the anchor-miss "demoted" section leaking a
fabricated Rafael/SPECTRO/AMPS-NG narrative) and #3 (Q1/XM30, invented "HEL laser"/"ATR/GPS-denied
nav" key facts attributed to a source that never mentions either), plus D2's so_what-phrase leak
into chat (Q8) and a malformed-citation-marker finding (Q5, `[9]]`/`[6]]`). Files touched: only
`agent/eoa/api/ask_grounding.py`, `agent/eoa/api/routes/ask.py`,
`agent/eoa/llm/prompts/ask_answer_format.md` (unchanged — see note below), and
`tests/unit/test_ask_round6_grounding.py` (new).

#### Finding 1 — fabricated "key facts" citing a real-but-unrelated source (Q1/XM30)

**Root cause**: the item's own distinctive tokens ("HEL", "ATR", "GPS") were each independently
"real" by the existing guards' own standards — "HEL" sits on `_COMMON_DEFENSE_ACRONYMS` (ordinary
domain vocabulary, not a claim about a specific source) and "ATR"/"GPS" are each genuinely present
*somewhere* in the wider retrieval corpus — so `ground_and_filter_answer`'s corpus-wide,
allowlist-aware check waves the whole bullet through even though the specific *source it cites*
never mentions any of them. **Fix**: new `ask_grounding.filter_claim_grounding` — scoped to the
"עובדות מרכזיות" section and the direct-answer paragraph only (the two places
`ask_answer_format.md` already requires a claim to trace to a specific source), it checks a unit's
distinctive tokens (Latin words/acronyms ≥ 3 chars, hyphen-split; digit runs ≥ 2 digits; Hebrew
technical terms from `config/taxonomy.yaml`'s own `domains.*.sub` labels) against *that unit's own
`[n]`-cited source(s) specifically*, with no common-acronym/taxonomy-vocabulary allowlist exemption.
A unit is dropped only when *none* of its tokens grounds this way (nor in the question) —
deliberately weak (one genuinely grounded token in an otherwise-fabricated bullet still saves it),
matching this module's own precision-first design. Verified against the live golden-sample
false-positive risk the brief asked for: re-ran the existing round-3/5 fixture suite (112 tests)
unchanged, plus 11 new `TestFilterClaimGrounding` cases covering the exempt sections
(`### פערים`, `### הערכת האנליסט`), uncited units, question-grounded terms, taxonomy-phrase
grounding, and the "one real token saves the bullet" trade-off explicitly — zero new false
positives on any existing fixture.

#### Finding 2 — anchor-miss "demoted" fallback section leaks fabrications (Q2/Iron Beam)

**Root cause, traced**: `routes/ask.py`'s guard order already ran every grounding guard *before*
the anchor-miss check, so the "### הקשר קרוב (לא התשובה)" section was, in principle, built from
already-guarded text — but one call site never re-passed through any guard at all: the
zero-citation corrective pass (`_run_citation_repair`, triggered when the first generation carries
no `[n]` marker) produces a brand-new LLM rewrite that became the new `answer_text` *completely
unguarded* (only `sanitize_citation_markers` ran on it), then flowed straight into the anchor-miss
demotion unexamined. Live-reproduced: a fabricated "AMPSNG" jargon term with no citation survives
the first pass's own guard (which drops the whole lead paragraph, leaving a gap sentence and still
no `[n]`), triggers the repair pass, and the repair rewrite reattaches the *same* fabrication with
a newly-added `[1]` — previously reaching the demoted section verbatim. **Fix**: factored the
five removal guards (`ground_and_filter_answer`, `filter_claim_grounding`,
`filter_entity_equivalence`, `filter_attribution_mismatches`, `filter_self_contradictions`) into a
shared `_run_removal_guards` helper in `routes/ask.py`, now called three times: the original
post-generation pass (unchanged), again immediately after a citation-repair rewrite is adopted, and
once more on the fully-assembled demoted text (belt-and-suspenders, per the brief). Each re-run
only emits a new `answer_final` SSE event when it actually removes something, so no existing SSE
event shape/count changed for any clean-answer test. Verified end-to-end
(`TestAnchorMissDemotedSectionEndToEnd`, 3 tests): one reproduces the exact repair-pass-reintroduces
-the-fabrication shape traced above (confirmed, by tracing the emitted `removed_by_guard` events,
that the second pass — not the first — is what actually removes the reintroduced "AMPSNG"), one is
the brief's own literally-requested "anchor miss + ungrounded entity in the body → entity absent
from the demoted section" case, and one confirms a clean on-topic answer is never touched.

#### Finding 3 — so_what template-phrase leak into chat (D2/Q8)

**Fix**: new `ask_grounding.strip_template_phrases`, reusing (importing, not copying)
`eoa.report.qa_citations.strip_so_what_phrases` and `eoa.report.style.strip_filler_phrases` so any
future addition to either banned-phrase list closes the chat gap automatically. Runs unit-by-unit,
unconditional on citations (pure style cleanup): a unit that is *only* the banned phrase is dropped
outright (e.g. `"- מהווה צעד משמעותי."` → removed entirely); a unit with other content keeps that
content with just the phrase clause deleted. One subtlety found and fixed while testing: both
reused strippers trim their own leading whitespace/junk (built for standalone sentences), which
silently ate the leading space that separates a non-first unit from the previous one's own trailing
punctuation — fixed by re-attaching the unit's original leading whitespace when the strippers ate
it, verified via `test_multiple_phrases_across_units_are_all_counted`. Wired into `routes/ask.py`
unconditionally (before the `if citations:` guard block) and folded into the existing
`ask.grounding_repair` log line as `template_phrases_removed`.

#### Finding 4 — malformed citation markers `[9]]`/`[6]]` (Q5)

**Root cause**: the web UI's citation renderer (`web/src/components/CitationText.tsx`, read-only)
splits strictly on `/(\[\d+\])/g` — only an exact `[<digits>]` run becomes a citation chip, so a
stray extra bracket glued onto an otherwise-real citation (`[9]]`, `[[6]`, `[9]]]`) leaves an
orphaned literal `[`/`]` character rendered to the user. **Fix**: extended
`sanitize_citation_markers` with a second pass (`_fix_malformed_citations`) that collapses any run
of extra brackets touching a `[<digits>]` core down to the bare form — implemented as a manual
match-by-match rewrite (not a blind `re.subn`) specifically so an *already*-well-formed `[7]`
matches the detection regex but contributes zero to the count, keeping every existing
`test_no_leak_returns_text_unchanged_and_zero_count`/`test_valid_numeric_citations_of_any_length_are
_never_touched`-style round-3 assertion passing byte-for-byte. `[1][2]`-style adjacent-but-distinct
citations are verified untouched (6 new `TestMalformedCitationMarkers` tests).

#### Finding 5 — a `###` heading glued to the preceding line (iPhone Safari e2e)

**Fix**: new `ask_grounding.ensure_headings_on_own_line`, a final, order-independent normalisation
pass (never removes/rewrites content, only re-inserts a line break) called once at the very end of
`routes/ask.py`'s SSE generator, after every other guard — deliberately scoped to `#{2,6}` (this
project's format only ever emits `###`) so a stray single `#` in ordinary Hebrew prose (e.g. "מחיר #
יחידה") is never mistaken for a heading (verified explicitly,
`test_single_hash_is_not_treated_as_a_heading`).

#### `ask_answer_format.md`

Read in full; already carries explicit rules (added in an earlier round) against fabricated
acronyms/jargon, false equivalence claims, and attribution/self-contradiction errors. No prompt
change made this round — all five findings above are closed at the deterministic-guard layer, which
is this package's own file scope; a prompt-level reinforcement (e.g. an explicit "every key-fact
bullet's distinctive terms must appear in its own cited source, not just anywhere in the corpus"
rule) is a reasonable future addition but out of scope for a guard-only round.

#### Tests

`tests/unit/test_ask_round6_grounding.py` — 31 new tests (target was ≥ 20): 11
`TestFilterClaimGrounding`, 6 `TestMalformedCitationMarkers`, 6 `TestStripTemplatePhrases`, 5
`TestEnsureHeadingsOwnLine`, 3 `TestAnchorMissDemotedSectionEndToEnd` (full SSE route, real
`retrieved` rows, mocked `chat_stream`/`chat`).

Required run (`PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest
tests/unit/test_ask_round3_grounding.py tests/unit/test_ask_round5_grounding.py
tests/unit/test_ask_round5.py tests/unit/test_ask_round6_grounding.py -q -p no:cacheprovider`):
**112 passed**, zero regressions on every existing round-3/5 fixture (including the round-2 citation
-repair/anchor-guard suite, `test_ask_round2_chat_fixes.py`, which the guard-order refactor in
finding 2 touches directly — also re-run separately, 49/49 green). Broader sweep
(`pytest tests/unit -q -k "ask or ground or chat"`): **239 passed, 1 failed** — the one failure
(`test_ollama_client_provider_dispatch.py::TestChatStructuredProviderThreading::
test_provider_passed_through_to_chat`) is pre-existing/unrelated: it imports neither
`ask_grounding` nor `routes.ask`, fails on a provider-routing default mismatch plus a Postgres
connection error unrelated to this package's files, and was not touched. `ruff check`/
`ruff format --check` clean on every touched file.

#### Live probes (2 of the allowed 6, read-only, no restart)

Per standing orders the running API (`http://127.0.0.1:8765`, native stack) was **not restarted**,
so these two probes (golden Q1/XM30, Q2/Iron Beam) document current live behaviour only — they do
not confirm whether the live process is running this package's code (no restart means no
before/after comparison was possible). Both came back honest and well-cited: Q1's answer correctly
attributes the real Rheinmetall/GDLS XM30 prototype-delivery facts to source [1] with real dates/
counts, one unit was removed by the pre-existing `grounded_entity` guard, and the analyst section
explicitly discloses that specific IR-sensor details are not yet public rather than inventing any
(no HEL/ATR/GPS-style fabrication reproduced this run). Q2's retrieval happened to include the same
structural trap as the original live finding — item "Elbit Systems Lands $270M ISR Deal" sitting
alongside an Iron Beam question — but the model correctly declined to conflate it, explicitly listed
the unrelated sources it found instead, and stated plainly that no source confirms a specific new
Iron Beam contract; `grounded_entity` again removed exactly one unit. Neither probe reproduced the
original fabrication verbatim (expected — a live model call is not deterministic and the corpus has
moved on since round 5), so these two probes are evidence of current health, not a controlled
before/after of this package's fix; the fix itself is verified by the unit/route-level test suite
above instead.

#### Not closed / out of scope

- The demoted-section re-run (finding 2) is verified to catch the *reintroduced-by-repair* shape
  live-traced above; it does not add full sentence-level claim-vs-source entailment beyond what
  `filter_claim_grounding` (finding 1) already provides, so a fabrication built entirely from tokens
  that are each grounded in the *same* wrongly-cited source (not just present in the corpus
  generally) would still need the entailment-level guard `docs/qa/loop/round_5_judge.md`'s own
  "ranked by expected gain" item 7 describes — flagged there, not attempted here (would require
  either an LLM-judge pass or much deeper NLP than this module's deterministic-token design).
- D2's 16 legacy `so_what_he` backfill rows and the nightly-backfill mechanism are report-pipeline
  files outside this package's chat-only scope (`eoa.report.*`, `scripts/`) — not touched.

## R6-ui package

### R6-ui status

Scope: the 4 round-5 Playwright failures assigned to the R6-ui package (`e2e/test-results/qa_d10_report.json`)
— `07-conferences.spec.ts` expand/collapse on `mobile-390x844` + `iphone-safari`, `09-reports.spec.ts`
citation-hover on `desktop-1440x900`, `06-ask.spec.ts` literal `###` on `iphone-safari`. Files touched:
`web/src/pages/ConferencesPage.tsx`, `web/src/pages/ConferencesPage.test.tsx`,
`web/src/components/reports/ReportBody.tsx`, `web/src/components/reports/ReportBody.test.tsx`,
`web/src/lib/askMarkdown.ts`, `web/src/lib/askMarkdown.test.ts`. No `agent/**` changes were needed —
all three root causes were frontend-only.

#### Finding 1 — conference row expand/collapse dead on mobile-390x844 + iphone-safari

**Root cause**: the conference name was the *entire* outbound-link `<a>` (name text + external-link
icon), inside a `<tr>` whose whole area is meant to toggle `aria-expanded` on click (guarded by
`stopPropagation` on the link so clicking it navigates instead of toggling). The table carries
`min-w-[720px]` inside an `overflow-x-auto` strip, so on any viewport narrower than that the table
renders at a fixed 720px regardless of the actual viewport width. Measured live (Chrome DevTools
protocol, 375px viewport, first row): the `<tr>`'s own bounding rect is `x:-426, width:720` (RTL —
the table's "start" columns render on the right, so the *unscrolled* visible slice is exactly the
leading chevron+name portion). A click at the row's geometric center — which is what a bare tap and
Playwright's `.click()` both target — lands at the intersection of that bounding rect and the
viewport, i.e. inside the outbound `<a>` (measured `x:109.9` to `x:256`, dead center of the visible
280px-ish slice). The link's `stopPropagation` then does exactly what it's supposed to: block the
toggle — but also fires its own navigation, so a plain center-tap meant to expand the row instead
opened the external conference site in a new tab (matches `test-failed-2.png` in
`07-conferences...mobile-390x844`, which shows the SPIE website, not the conferences table). This
never reproduced on desktop/tablet because those viewports are wide enough that the table isn't
clamped to 720px and the row's visible center falls in an empty/non-interactive column instead.

**Fix** (`ConferencesPage.tsx`): the name cell no longer wraps the whole name in the outbound `<a>`.
The name (`<bdi>`) is now plain, non-navigating text — part of the row's click-to-expand surface —
and only a small icon-only button (the existing `ExternalLink` icon, `aria-label`'d) carries the
`href`/`target="_blank"`/`stopPropagation`. This shrinks the link's footprint from "most of the
visible row" down to a ~16px icon, so a center-tap on the row lands on plain text and toggles
correctly in every viewport tested, while the icon itself still opens the registration/source URL
without toggling. Added a vitest regression
(`ConferencesPage.test.tsx`: "clicking the outbound-link icon does not toggle the row, but clicking
the name text does") encoding both halves of that contract; jsdom can't reproduce the real
geometric/viewport-clipping bug itself, so the actual fix was verified live via Playwright against
the rebuilt bundle (see Verification below).

#### Finding 2 — citation marker un-hoverable after click, desktop-1440x900

**Root cause**: `ReportBody.tsx` renders `linkifyReportCitations(html, citations)` output via
`dangerouslySetInnerHTML`. React's DOM renderer diffs that prop by the *object reference* of the
`{ __html }` wrapper — `lastProps.dangerouslySetInnerHTML !== nextProps.dangerouslySetInnerHTML` —
before it ever inspects the string inside it, and JSX allocates a brand-new wrapper object literal
every render regardless of whether the string itself changed. `handleMouseOver` calls `setHover` on
essentially every pointer move over a citation chip — a purely local state update with zero effect
on the rendered markup — but because the wrapper object's reference always changes, React tore down
and recreated the *entire* article subtree on every such re-render, detaching the very `<a>` the
pointer was on. A real `.hover()` (or Playwright's retry loop) would see its target vanish mid-
gesture and retry indefinitely against a page that keeps regenerating the same-looking node, which
matches the observed `"19 × element was detached from the DOM, retrying"`. Confirmed live via CDP:
dispatching a single `mouseover` on the resolved citation anchor flipped its `isConnected` to
`false` and swapped in a new node at the same query, on the pre-fix build. Also reproduced and
proved in isolation with a minimal scratch React component (memoized string, unmemoized `{ __html
}` wrapper, unrelated `useState` update) before touching the real component, to rule out any
`reportHtml.ts`/citations-shape explanation.

**Fix** (`ReportBody.tsx`): two layers of memoization, both required — `useMemo` on the `linked`
string (keyed on `html` + the citations map, so it isn't recomputed on every render) *and* a second
`useMemo` wrapping `{ __html: linked }` itself (keyed on `linked`), so the actual prop object React
diffs is reference-stable across renders that don't change the content. Memoizing only the string
(the more "obvious" fix) is *not* sufficient — verified this empirically before landing the final
fix, since JSX still re-allocates the wrapper object every render regardless. Added a vitest
regression (`ReportBody.test.tsx`: "does not detach/recreate the citation node across a
hover-triggered re-render") that fires `mouseOver` on a resolved marker and asserts
`isConnected`/node identity are preserved; this test reliably reproduced the bug against the
pre-fix component and now passes. The `<strong>`-wrapped-BLUF-marker case the task flagged does not
change anything here — `linkifyReportCitations`'s regex operates on the raw HTML string
independent of ancestor tags, and the specific report reproduced against (`id=72`,
`GET /api/reports?limit=1`) already had its `[n]` markers server-rendered as proper `<a href="#src-n"
class="cite">[n]</a>` anchors, not raw unconverted markdown — no `reportHtml.ts` change was needed
for that shape.

#### Finding 3 — literal `###` in rendered chat answer, iphone-safari

**Root cause**: `askMarkdown.ts` hands the raw model answer straight to `marked.parse()`. Per
CommonMark (which `marked` follows), a `#`..`######` ATX heading is only recognized when the marker
starts its own line. The failing run's actual received answer text (from the JSON report) shows a
heading run glued directly onto the end of the previous sentence with no line break —
`...NH90 הטקטיים של הצבא הפיני...[^2].### עובדות מרכזיות` — so `marked` correctly (per spec) parsed
the whole thing as one ordinary paragraph, and the literal `###` leaked into the rendered
`.eo-ask-answer` text. This is a property of the raw text the chat backend produced for that
specific (non-deterministic, live-LLM) answer, not a mobile-vs-desktop rendering difference —
`AskAnswer.tsx`/`askMarkdown.ts` is the single renderer for every viewport, already memoized via
`useMemo`, with no per-viewport branching.

**Fix** (`askMarkdown.ts`): a new `normalizeGluedHeadings()` preprocessing pass, run on the raw
markdown before `marked.parse()`. It inserts the newline a glued heading marker is missing
(`/([^\n#])(#{1,6}[ \t]+\S)/g` → `"$1\n\n$2"`), applied regardless of which viewport or request
happens to trigger it — a defensive normalization of the input, not a renderer-side special case.
The preceding-character class excludes `#` itself (not just `\n`): an earlier version used `[^\n]`,
which also matched on the *first* `#` of an already-correctly-placed multi-# heading (e.g. `"##
text"`) and incorrectly split it into an empty heading + a smaller one — caught by running the
existing "renders headings..." vitest before landing the fix, fixed, and covered by 3 new tests
(glued heading gets its own line and renders as `<h3>`; an already-correct heading is left alone;
a bare `#123`-style token with no space is left alone, matching the pre-existing hashtag-safety
intent). No backend change is needed to fix the symptom, though the backend team may still want to
know it's occasionally emitting glued headings.

#### Verification

- `npx vitest run` (full suite): **272 passed** (0 failed), including 3 new tests in
  `askMarkdown.test.ts`, 1 new test in `ConferencesPage.test.tsx`, 1 new test in
  `ReportBody.test.tsx`.
- `npm run lint`: 0 errors, 12 pre-existing warnings (all in files this package didn't touch).
- `npm run build`: succeeds (`tsc -b && vite build`), output written to `web/dist` (the path the
  live API at `127.0.0.1:8765` serves).
- Playwright against the rebuilt live app, all 5 projects (desktop-1440x900, mobile-390x844,
  tablet-820x1180, tablet-landscape-1180x820, iphone-safari):
  - `npx playwright test tests/07-conferences.spec.ts tests/09-reports.spec.ts --reporter=line` →
    **55 passed** (0 failed).
  - `npx playwright test tests/06-ask.spec.ts --reporter=line` → **10 passed** (0 failed), run once
    (of the 2 allotted, since it hits the live cloud LLM).

No `agent/**` files were touched and no backend change is required for any of the three findings.
