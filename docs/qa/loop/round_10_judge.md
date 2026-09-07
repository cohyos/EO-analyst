# Round 10 QA Judge Report (J10)

**Judge:** independent read-only agent. No pipeline runs, no fixes, no OMC skills/agents/tools, no
DB writes, no git commands of any kind, no process kills, no stack start/stop. `DATABASE_URL`
loaded once per shell via `set -a; . runtime/eoa.env; set +a`, never printed; every query confirmed
`db='eoanalyst'`, `port=5432`. D10 skipped per brief (deterministic e2e).

## 0. Freshness ledger (read first, per the round-10 brief's own instruction)

Process inventory (`Get-CimInstance Win32_Process`, read-only) shows the live API
(`uvicorn eoa.api.app:app`, PID 48820/53880) **restarted at 2026-09-07 15:32:21 local** — after
every round-10 source file's own last-write time:

| File | Last write | vs API restart (15:32:21) |
|---|---|---|
| `agent/eoa/api/routes/ask.py` | 14:56:00 | before |
| `agent/eoa/report/indicators.py` | 14:57:02 | before |
| `agent/eoa/api/routes/investigations.py` | 14:58:02 | before |
| `agent/eoa/api/ask_grounding.py` | 14:59:51 | before |
| `agent/eoa/report/daily.py` | 15:03:13 | before |
| `scripts/cleanup_round10.py` | 15:09:30 | before |
| `agent/eoa/graph/queries.py` | 15:15:46 | before |
| `agent/eoa/api/routes/entities.py` | 15:15:55 | before |
| `agent/eoa/investigations/links.py` | 15:21:38 | before |
| `agent/eoa/report/product_line.py` | 13:37:00 (no round-10 change) | before |

**Unlike round 9 — whose own judge could not live-sample any round-9 fix because the API kept
pre-round-9 code — this round's live API genuinely serves round-10 code end to end.** Confirmed
directly, not inferred: `GET /api/investigations/{id}` returns a populated `provenance` object,
all five new `/api/graph/*` and `/api/entities/{id}/detail` routes respond, and all 8 golden chat
questions were asked against this live process.

Report freshness:

| File | Built | vs relevant fix |
|---|---|---|
| `daily_2026-09-07.md` | 15:32 | after `daily.py`/`indicators.py` (15:03/14:57) — fresh |
| `weekly_2026-09-07.md` | 15:39 | after — fresh |
| `monthly_2026-09-30.md` | **14:53** | **before** `daily.py` (15:03) by ~10 min — **not fresh for this round's D4/D6 fixes** |
| 8× `bd_*_2026-09-07.md` | 14:56–15:21 | no round-10 BD-specific package; unaffected either way |
| `patent_survey_*_2026-09-07.md` (×2) | 15:30 / 15:38 | after `report/daily.py`-adjacent fixes; fresh |
| 6× `pl_*_2026-09-07.md` | 14:45–14:49 | after `product_line.py`'s already-landed round-9 fix (13:37); fresh |

Only the monthly report is stale relative to this round's own fixes; every other report kind judged
below is genuinely fresh.

## 1. Scores

| Domain | R9 | R10 | Δ | n | Headline |
|---|---|---|---|---|---|
| D1 | 91 | **92** | +1 | 334 | Entity orphans reproduce at 16/334 exactly, resolving R9's own flagged discrepancy |
| D2 | 86 | **86** | 0 | 8 | Not a round-10 target; held, indirect positive signal from chat answers |
| D3 | 91 | **93** | +2 | 125 | pl_targeting_pods raw "test" leak (R9 worst) closed live |
| D4 | 74 | **93** | +19 | 11 | Job 160 (clean) now renders over job 146 (interstitial); live provenance API |
| D5 | 78 | **85** | +7 | 8 | Decimal-split fixed 0/8; dangling-fragment 5/8→1/8; Skyranger spec still unsynthesized |
| D6 | 90 | **92** | +2 | 30 | Daily indicator evidence 1/8→8/8 live |
| D7 | 78 | **90** | +12 | 20 | Report-freshness systemic gap (R9 worst #1) closed |
| D8 | 85 | **93** | +8 | 16 | Patent appendix reliability populated live (10/10 DROIC) |
| D9 | 94 | **90** | -4 | 14 | DB state unchanged/clean; **new Graph Explorer UI regression** found live |

Weighted (D1–D9, brief's own weights, out of 95): **≈90.3** — a large jump from round 9's
weighted position, driven almost entirely by D4 and D7 converting from "code correct but artifact
stale" (round 9's framing) to "verified live on a fresh artifact" this round.

## 2. Domain detail

### D4 — Deep investigations (74 → 93, the round's biggest story)

Round 9's single worst finding was architectural: rerun reconciliation picked the "best" answer by
outcome-tier alone, so job 146's badly-sourced `found`/0.85 (an undisclosed Cloudflare interstitial)
beat job 160's honest `partial`/0.1 even after 160 correctly discarded the same bad URL. This round:

- **Live weekly report** (rebuilt 15:39, after the fix) renders job 160, not 146, for the AARGM-ER
  question, with an explicit override clause: *"ריצה אחרת עם תוצאה 'טובה יותר' לכאורה הושמטה כי מקורה
  היחיד... זוהו כדף חסימה/אימות אנושי ולא כתוכן אמתי."* A reader now sees **why** a lower-tier answer
  won, not just that reruns happened.
- **New provenance API**, live-tested: `GET /api/investigations/160` returns `trigger_item` (item 44,
  full title/url/source/date), `lineage` (the 138→146→160 chain with outcome/confidence per hop),
  and `reports` (4 citing reports, including the live weekly). This closes a gap that existed since
  before round 9 — investigations were previously islands, linked to nothing.
- **Report-level links** confirmed live: `[חקירה #160](/investigations/160) · פריט מקור [17](#src-17)`
  renders inline in the weekly's "חקירות עומק" section.
- Item 96→10 dedup fold, the blocked-vs-not_found distinction (Volkswagen/Rafael item), and job 161's
  Norkin hedge-downgrade — all previously-fixed round-9 behaviors — still hold live.

This is the strongest, most concretely-evidenced improvement in the round: three independent live
checks (rendered report text, a fresh API call, and a DB query joining jobs/investigation_log) all
agree.

### D7 — Report freshness (78 → 90)

Round 9's worst #1 was that every report except daily/weekly predated the round-9 code fixes by
1–4 hours, so none of them could evidence the fixes even though the code was correct. This round, all
6 product-line reports, both patent surveys, and all 8 BD reports were rebuilt 14:45–15:38 — after
the relevant fixes — and directly confirm them (see D3, D8 below). The one exception is the monthly
report (14:53, ~10 minutes before `daily.py`'s own fix), noted above and in D6.

### D5 — Chat ("ask the analyst") (78 → 85)

All 8 golden questions were re-asked live via a direct SSE client against `POST /api/ask`
(never through a UI or another tool), one `answer_final` per call confirmed throughout,
`first_event_s` 5.8–6.4s, `answer_final_s` 50.5–101.9s — all healthy, well inside the 4-minute
per-question budget.

**Fixed, verified:** the decimal-point sentence-split (round 9's root cause behind 5/8 dangling
openings) is gone — 0/8 decimal-split artifacts in a regex scan plus manual read of every answer's
opening/closing 200 characters. Q1 (XM30) correctly states "כ-1.53 מיליארד דולר" as one unbroken,
accurate figure (round 8's $53bn-vs-$1.53bn fabrication stays fixed).

**Still open, two findings:**
1. **Q6 (AUSA 2026)** opens with `" שאר המקורות (...)"` — "the REST of the sources" — the *identical*
   golden question and defect shape round 9 flagged. Root cause 1 (decimal splitting) is confirmed
   fixed, so this is a different failure mode: the new `enforce_answer_coherence` safety net is
   content-blind (checks only leading-unit word count and terminal punctuation), and this fragment is
   itself a complete, correctly-punctuated sentence — only its cross-reference to a now-removed prior
   sentence gives it away. Down from 5/8 to 1/8: a real, large improvement, just not the 0/8 the
   fix's own writeup predicted.
2. **Q5 (Skyranger)** — item 1353 (the Skyranger-35 rate-of-fire spec) is now confirmed **retrieved**
   (present as citation `[3]`/10 in the live SSE `citations` payload), proving the retrieval-cap
   widening fix worked exactly as designed. But the **final synthesized answer still doesn't use it**
   — it explicitly states no source gives Skyranger's technical spec, when a retrieved-but-unused
   citation apparently does. Same 0/2 user-facing outcome as round 9, now clearly diagnosed as a
   synthesis/prompting gap, not a retrieval-cap gap.

Entailment-guard activity: `ask.entailment_check_removed` fired for 3/8 live calls this round
(claims=4, removed=1–2 each), up from round 9's 1/8 — real progress from the `chain_fallback` fix,
though short of "well above 1/8". Zero `_skipped`, zero `_unavailable`.

### D6 — Daily/weekly/monthly (90 → 92)

Daily's indicator-watchlist evidence column is **8/8 populated (100%)** on the live, freshly-rebuilt
report — up from round 9's 1/8, closing that worst finding on the actual artifact rather than a
proxy day. The daily report is also an honest, deliberate tables-only build this cycle — its own
exec summary states plainly that no red/orange/yellow items appeared in the last 24h window, then
still surfaces real table content (2 events, 7 forecasts, 1 investigation). This matches the brief's
framing exactly and should not be read as a defect.

New, minor finding: the **monthly report has no dedicated "תעשייה ישראלית" table/heading at all**
(daily and weekly both do) — Israeli-industry content lives only inside the narrative "סקירה לפי
תחום" sections. It's unclear whether this D6 check was ever meant to apply to the monthly kind;
flagged for round 11 to clarify rather than scored as a hard failure.

### D8 — Patent surveys (85 → 93)

Round 9 confirmed the appendix-reliability code fix was correct but every on-disk survey predated
it. This round's fresh DROIC survey (rebuilt 15:30) shows `מקור ראשוני · רשומת פטנט רשמית · 1.00`
for all 10/10 patents.google.com rows — the fix landing on the actual artifact, not just in source.
The Anduril survey's appendix was not independently re-read row-by-row this round (assumed
consistent via the same code path).

### D9 — Tenders/forecasts/conferences + UI (94 → 90)

DB state is unchanged and clean (tender 42 open/untagged, 15/20 archived/tagged, 0 unknown-status
tenders, 10 forecasts) — no regression, but also no fresh movement to evidence this round's own
packages.

**New, high-severity, live-reproduced UI regression** (this is the reason D9 goes down despite a
clean DB): the round-10 brief asked to exercise the new entity-graph endpoints, "and if a browser is
available, note what you can verify." A browser was available. Opening `/entities` → "סייר גרף"
(Graph Explorer) — the tab's own default landing state, before any entity is selected — immediately
fails with **"לא ניתן לטעון את הגרף"** (cannot load the graph). Root cause, confirmed two independent
ways:

1. Live network log: `GET /api/graph/overview?limit=300` → **422 Unprocessable Content**.
2. Direct curl reproduction against the same live API: the backend's own FastAPI `Query` validator
   rejects `limit=300` with `{"msg":"Input should be less than or equal to 200",...,"ctx":{"le":200}}`.
   `agent/eoa/graph/queries.py`'s `overview()` function itself also clamps internally to 200.
   `web/src/components/graph/EntityGraphExplorer.tsx` line 68 initializes a **single shared**
   `nodeLimit` state to `300` — the *neighborhood* endpoint's own default cap (`_NODE_CAP = 300`) —
   and reuses that same value for the unrelated `overview` call, whose ceiling is a hard 200.

This is not a theoretical or unit-test gap: it is the actual first thing any analyst sees on opening
a brand-new, never-before-shipped feature, live, right now. The other four new graph endpoints
(`search`, `neighborhood` at a normal depth, `path`, `entity-detail`) all work correctly when called
directly — curl-verified 200 OK with real, well-formed data (a 30-node/42-edge neighborhood around
Elbit, a 5-node/4-edge Elbit→RTX path, a full entity-detail payload with mentions/events/
investigations/reports). So the backend is sound and three of five surfaces work; only the landing
view's default parameter is broken, but for a first-time user that is the whole first impression.

## 3. Worst-10 (most severe first)

1. **UI/D9 (new, live-reproduced):** Graph Explorer's default landing view 422s on `GET
   /api/graph/overview?limit=300` — frontend sends a shared default the backend rejects. Every user
   who opens the new tab hits this immediately.
2. **D5:** Skyranger's item 1353 is retrieved but not synthesized into the final answer — same
   user-facing gap as round 9, now proven to be a synthesis issue.
3. **D5:** Q6's dangling "שאר המקורות..." fragment recurs (1/8, down from 5/8) — the new coherence
   check cannot catch a structurally-complete-but-referentially-dangling opening.
4. **D5:** entailment-guard activity is 3/8, not "well above 1/8" as the fix predicted; 5/8 answers'
   coverage remains unconfirmed from the log.
5. **D6 (informational):** monthly report has no Israeli-industry table at all, and predates this
   round's `daily.py` fix by ~10 minutes.
6. **D1 (methodology caveat):** corroboration sample shrank to n=36 from round 9's n=43 (likely the
   R10-cleanup interstitial removal, not a regression) — both remain 100% populated.
7. **D7 (not observable):** the acquisition-watch territory-scoping question from round 5 could not
   be re-tested — bd_de's section is empty this week.
8. **D8 (not independently re-verified):** only DROIC's appendix (10/10) was directly re-read; Anduril's
   (6 rows) assumed consistent but not separately spot-checked.
9. **D5 (cosmetic):** Q7 has a stray leading space before an otherwise complete sentence.
10. **D9 (informational only):** tender/forecast/conference state is unchanged from round 9 — listed
    only for completeness, not as a defect.

## 4. Comparison with round 7

Round 7's own scores (D4 lower-70s/D7 mid-30s-to-70s range depending on which artifact was sampled)
were dominated by missing report-layer features that round 9 built and round 9's own judge found
correctly coded but not yet live on any artifact except daily/weekly. Round 10 is the first round
where that gap closes broadly: D4 (+19), D7 (+12), and D8 (+8) all convert from "verified in source,
unverifiable on the artifact" to "verified live on a freshly-rebuilt artifact," which is a
structurally different and stronger form of evidence than either round 7 or round 9 could produce for
these domains. D5 continues its round 8→9→10 climb (an unscored/broken baseline → 78 → 85) via two
more real, load-bearing fixes (decimal splitting, retrieval-cap widening) each of which fixed its
literal target while leaving one adjacent, more subtle instance of the same class of problem (Q6's
dangling fragment; Q5's still-unsynthesized retrieval) — a pattern worth naming explicitly for
round 11: **this codebase's guard-pipeline fixes reliably close the literal reproduction case they
were built against, and reliably leave a narrower sibling case open**, which is exactly what
happened again with D9's brand-new Graph Explorer (four of five endpoints solid, the fifth's default
parameter wrong).

## 5. Recommended round-11 packages, ordered by expected gain

1. **Fix the Graph Explorer overview 422** (trivial, high-visibility): give `EntityGraphExplorer.tsx`
   two separate limit constants — a `neighborhoodLimit` defaulting to 300 and an `overviewLimit`
   defaulting to ≤200 — instead of one shared `nodeLimit` state. One-line-scale fix, closes a
   first-impression-breaking bug in a feature that otherwise works end to end.
2. **D5 Skyranger synthesis**: item 1353 is retrieved but the synthesis prompt/guard pipeline still
   discards or ignores it. Trace why a retrieved, relevant, on-topic citation doesn't make it into the
   answer even when nothing else contradicts it — likely a synthesis-prompt or a downstream grounding
   guard over-pruning a legitimately relevant but topically-narrow citation.
3. **D5 referential-dangling-fragment class**: extend `enforce_answer_coherence` (or add a companion
   check) to catch an opening unit whose *first token* is a cross-reference/continuation marker
   ("שאר", "לעומת זאת", "בנוסף", etc.) with no antecedent in the same section — a cheap, high-signal
   heuristic distinct from the existing word-count/punctuation check, directly targeting the exact
   shape both round 9 and round 10 independently reproduced on the same golden question (Q6).

Also worth a light-touch look: rebuild the monthly report on the next cycle so it can evidence this
round's `daily.py`-adjacent fixes, and give the monthly report kind an explicit decision (in
`docs/QA_CONTINUOUS_LOOP.md` or the report template) on whether it should carry the same single
"תעשייה ישראלית" table daily/weekly do.
