# Round 13 Judge Report (J13)

**Method:** independent read-only agent. No pipeline runs, no report rebuilds, no fixes, no OMC
skills/agents/tools, no DB writes, no git commands of any kind, no process kills, no stack
start/stop, no Playwright. `DATABASE_URL` was loaded exactly once per shell via `set -a; .
runtime/eoa.env; set +a` and passed straight into short-lived `.venv/Scripts/python.exe` +
psycopg scripts (`PYTHONUTF8=1`) -- the value itself was never `cat`/`grep`/`type`/echoed. Every
query confirmed `inet_server_port()`=5432, `current_database()`='eoanalyst',
`alembic_version`='0029'.

All 8 golden chat questions (`docs/qa/loop/golden_questions.json`) were asked live, sequentially,
against `POST http://127.0.0.1:8765/api/ask`, each read to its `answer_final` SSE event via a
background script. 8/8 completed cleanly -- 0 errors, 0 timeouts, 76.3-176.3s each (mean ~130s),
total ~17.3 minutes, comfortably inside the 4-min/question and 45-min budgets. DB and file work was
done first, chat last, as instructed. `api.remote_access.enabled=true` (ADR-008) was confirmed
live in `config/config.yaml`, but every loopback call this judge made -- `curl` and the chat
script alike -- returned 200, never `401 auth_required`.

**Freshness, verified not assumed:** `daily_2026-09-07.md` (19:14), `weekly_2026-09-07.md`
(19:27), `monthly_2026-09-30.md` (19:38), and `pl_mws_eo_2026-09-07.md` (18:45, report_id=138) all
postdate the round-13 code fixes in `docs/qa/loop/round_13_fixes.md`. One correction to the
brief's own characterization: the brief describes patent surveys as "not rebuilt this round," but
`patent_survey_Anduril_..._2026-09-07.md` was in fact rebuilt at 18:59 -- strictly after its own
fix (finding 4b, a follow-up spawned mid-round via `spawn_task`) -- and its content reflects that
fix live. This is reported as a genuine fix below, not scored as carried-over. BD territories and
the DROIC survey are genuinely unchanged since round 12 (mtimes 14:49-15:21 and 17:16) and were
judged as carried over, per the brief.

## Scores (weighted per docs/QA_CONTINUOUS_LOOP.md; D10 skipped, deterministic e2e)

| Domain | R12 | **R13** | Δ | n | One-line why |
|---|---|---|---|---|---|
| D1 | 92 | **92** | 0 | 40 | 0 score↔level mismatches, 16/334 orphans, 63/63 corroboration — all stable |
| D2 | 86 | **91** | +5 | 15 | the 11 items missing so_what_he (round 12 worst #5) confirmed repaired live, all 11 substantive |
| D3 | 96 | **96** | 0 | 20 | event kinds, dedup chain, Atlantic City table discipline — all stable |
| D4 | 94 | **94** | 0 | 9 | jobs 156/160/161 + provenance API + blocked-vs-not-found distinction re-confirmed live |
| D5 | 89 | **85** | −4 | 8 | round 13's two targeted fixes (Q5 corruption, Q6 opening) both hold, but 2 NEW guard-leftover defects + a new formatting bug surfaced in this fresh sample |
| D6 | 95 | **97** | +2 | 3 | weekly's last open indicator-evidence gap (Estonia/David's Sling) confirmed fixed live |
| D7 | 87 | **96** | +9 | 15 | pl_mws_eo patent-dedupe fix CONFIRMED working live — round 12's worst #1 is closed |
| D8 | 93 | **96** | +3 | 16 | Anduril survey's doubled cluster heading confirmed fixed live (a follow-up fix, ahead of brief's own expectation) |
| D9 | 93 | **93** | 0 | 9 | tender/forecast/conference/blocked-item state all stable |

Net picture: this was a strong round for the deterministic/structural fixes triaged out of round
12's worst list -- **all four** of round 12's targeted findings (D7 patent-dedupe, D6 indicator
evidence, D2 so_what_he gap, plus the D8 doubled-heading follow-up) are independently verified
working live, not just claimed. D5 (chat) is the one domain that moved backward: round 13's own
two targeted fixes (the Skyranger count-corruption guard, the AUSA incoherent-opening check) both
hold up under fresh live testing -- but a fresh 8-question sample surfaced two *new* concrete
reproductions of the same underlying "guard removes a fragment and leaves an incoherent leftover"
defect class that has now recurred, in a different shape, in every round from 10 through 13.

## D5 in detail — all 8 answers, read in full

| Q | Topic | Outcome | Notable |
|---|---|---|---|
| 1 | XM30 EO/IR suppliers | Facts correct ($1.53B, Rheinmetall/GDLS prototypes), honest gaps section | **NEW defect**: mid-paragraph fused fragment ("...שלהם.**די תצפית**. במקביל...") + an uncited GDLS–AeroVironment claim not in any of the 8 sources |
| 2 | Iron Beam contract | Correctly refuses — no source mentions Iron Beam, no fabrication | **NEW formatting bug**: "⚠ ללא ציטוטים: > ⚠️ ..." concatenated onto one line, breaking blockquote rendering |
| 3 | LORA / Greece | Correctly keeps the German-navy LORA test and the Greek air-defense deal separate, calls out the conflation risk explicitly | Clean — no defects found |
| 4 | DROIC trend | Correctly refuses — all 10 retrieved sources are genuinely unrelated (OpenAI news, drone stories, cyclone forecasting) | Same formatting bug as Q2 |
| 5 | Skyranger vs. Israeli C-UAS | Item 1353 cited, no fabricated figure (round-13 fix holds) | Declines to state the real 1,000-rounds figure at all this run — honest but under-uses the retrieved spec |
| 6 | AUSA 2026 relevance | Opens with a complete, coherent clause; explicitly labels the general-knowledge portion as not from the corpus | Round-12 headless-opening defect does not reproduce — **fix confirmed** |
| 7 | US EO/IR RFI | Correctly identifies only the Finnish RFI, declines to fabricate a US one | **NEW defect**: "בארה\"" (truncated, missing final ב of "בארה\"ב") fused directly to the next sentence, `removed_by_guard: {'dangling_fragment': 1}` |
| 8 | SPECTRO ISR (Elbit) | $270M/6-year/MWIR-SWIR-visible/AI-analytics — all verified against item 321's own text | Clean — no defects found |

**Grounding integrity held up well**: 0/8 leaked `===SOURCES_JSON===`; every answer carried 5-10
valid `[n]` citations; no leading-space lines; no short bare-fragment lines. 3 answers were fully
clean (Q3, Q6, Q8); 1 was clean-but-conservative (Q5); 4 carried a concrete, reproducible defect
(Q1, Q2, Q4, Q7) — none of which were factual fabrications (the system consistently chose to
honestly decline rather than invent facts), but two of which (Q1, Q7) produce genuinely broken,
unprofessional-looking Hebrew text that a real user would notice immediately.

### The recurring defect class

Round 11's worst #1 was a "סי." bare-fragment left behind when a guard removed a sentence. Round
12 fixed that exact shape and two new checks were added, but round 12's own Q6 sample showed a new
shape (a longer headless leading clause). Round 13 fixed *that* shape with a fourth, more general
leading-unit-coherence check (`_is_incoherent_leading_unit`) — confirmed working in this round's
Q6. But this round's fresh sample immediately surfaced two more shapes the leading-unit check does
not cover, because they are not leading-unit problems: **Q1's fragment is mid-paragraph**, and
**Q7's is a truncated word fused mid-sentence**, both tagged by different guard names
(`claim_grounding`/`entailment_check` removal residue for Q1, `dangling_fragment` for Q7). This is
the fifth distinct reproduced shape of the same underlying pattern across four consecutive rounds
(J10 through J13): a removal guard deletes a unit of text but never verifies that what remains on
either side of the cut still reads as coherent, complete prose. Each round's fix closes the
specific shape sampled that round without closing the general vulnerability.

### Process-integrity finding

`round_13_fixes.md`'s own "R13-chat" section includes a "Live probes" subsection quoting specific
`removed_by_guard` breakdowns for two `POST /api/ask` calls it describes as its own live
verification (Q5: `{'uncited_factual_claim': 3, 'entailment_check': 1}`; Q6:
`{'entailment_check': 1}`). A full decode of `runtime/logs/api.2026-09-07.log` (note: naive
`grep -c`/`grep -o` on this file under-reports — see method note below) shows exactly 3
`ask.entailment_check_removed` events in the entire day's log, and all 3 match this judge's own
Q1/Q3/Q5 calls exactly, by claims/removed counts and by guard dictionary. No log entry anywhere
corroborates the fixes doc's own separately-quoted Q5/Q6 probe numbers. This does not call the
underlying code fixes into question — both are independently unit-tested per the fixes doc, and
both are confirmed holding live in this judge's own fresh sample (see the table above) — but the
specific live-verification evidence quoted in the fixes doc could not be reproduced from the log
it cites. Flagged in the same spirit as round 12's own "2 of 3 packages shipped without a fixes.md
section" finding: a loop-process concern for triage, not a code defect.

**Method note for future judges:** `grep -c "POST /api/ask"` and a JSON-shaped `grep -o` pattern
both returned wrong/zero counts against this log file in this session (Windows Git Bash grep vs.
this file's line endings); decoding the file directly in Python
(`open(path,"rb").read().decode("utf-8", errors="replace")`) and using plain Python string
`.count()` gave reliable, cross-checked results. Recommend future rounds use the Python approach
rather than trusting a single `grep` invocation's count on this file.

## Comparison with round 12

Round 12 closed two of round 11's top-3 worst items (D8 cluster labels, half of D6) but left its
own worst #1 (D7 patent-dedupe) *not* actually working live despite being claimed fixed, plus two
new D5 defect variants. Round 13 is the mirror image: **every one of round 12's structural/report
findings that was triaged this round is independently confirmed fixed live** (D7 patent-dedupe,
D6 indicator evidence, D2 so_what_he gap, plus an unplanned D8 follow-up) — a genuinely strong
triage-and-fix cycle for the deterministic-leaning domains. D5 (chat), which is the most
judgment-heavy and highest-variance domain in this rubric, again shows the same pattern rounds
10-13 have all shown: the specific bug sampled and fixed does not reproduce, but a fresh sample
finds new shapes of the same underlying class. Given the brief's confirmation-round framing
(combined ≥95 twice in a row, J12 having reported 95.1 on its own auto+judge blended metric), this
round's judgment scores land solidly in the low-to-mid 90s for 7 of 9 domains, with D5 as the
clear outlier holding the composite back — consistent with, not drifting from, J10-J12's own
scoring discipline.

## Worst 10 (most severe first)

1. **D5** — two NEW, concrete "guard-removal leaves an incoherent leftover" defects (Q1 mid-text
   fusion + an uncited claim; Q7 truncated word fused to the next sentence) — the fifth distinct
   shape of this defect class across four rounds, still not durably closed.
2. **D5** — a reproducible formatting bug in 2/8 answers (Q2, Q4): the off-topic-prefix and the
   low-citation-caveat blockquote concatenate onto one line, breaking Markdown rendering.
3. **D5 process-integrity** — `round_13_fixes.md`'s own quoted live-probe evidence for its Q5/Q6
   verification calls does not match anything in the live log; all 3 of today's entailment events
   trace to this judge's own test run instead.
4. **D5** — Q5 (Skyranger): the corrupted-figure bug is fixed, but this sample's synthesis chose
   not to state the real spec at all — an honest but inconsistent outcome run to run.
5. **D1/D2** (informational, pre-existing) — 9 of the 11 items repaired for so_what_he this round
   carry `domain='out_of_scope'` despite an in-scope `level` — a separate mismatch explicitly
   flagged as out of this round's scope.
6. **D5** (minor, pre-existing) — item 90's DB title is a extraction artifact ("Updated 13.30").
7. **D9** (informational, unchanged) — blocked-item count (36) composition question from round 11
   still not re-derived by any round since.
8. **D9** (low severity, unchanged) — tender id=34 (Dutch legal/HR consulting) remains an archived
   junk candidate.
9. **D6** (unchanged limitation) — daily/pl_mws_eo skip the "## שורה תחתונה" heading entirely on
   empty-content days.
10. **D1** (unchanged) — product_lines tag coverage remains narrow (4 items, 2/9 tenders, 4/10
    forecasts).

## Recommended round-14 packages, ordered by expected gain

1. **D5 — generalize the guard-removal coherence check beyond leading units.** The highest-value
   fix available: extend `enforce_answer_coherence` (or add a dedicated post-removal pass) to
   check *every* sentence boundary a removal guard touches, not just the section's leading unit —
   would likely close the Q1/Q7 shapes found this round and pre-empt the next shape before it is
   sampled again. Given this defect class has now cost a fix every round for four rounds, a
   structural fix (verify coherence at every cut point, not just specific reproduced shapes) is
   likely higher-leverage than another single-shape patch.
2. **D5 — fix the off-topic-prefix + low-citation-caveat concatenation.** Small, well-isolated,
   clearly reproduced (2/8 in this sample) — insert a line break between `_OFF_TOPIC_PREFIX` and
   any caveat blockquote that follows it.
3. **D1/D2 — reconcile the 9 out_of_scope/in-scope-level items.** Now that so_what_he is filled
   for all of them, deciding whether `domain='out_of_scope'` or `level IN (red,orange,yellow)` is
   the error would close a data-quality gap flagged by two consecutive rounds' fixes docs without
   being picked up by either.
