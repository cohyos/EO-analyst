# Round 6 Judge Report (J6, independent, read-only)

## Method

Read-only, no pipeline runs, no fixes, no OMC skills/agents/tools, no DB writes, no git commands of
any kind, no process starts/stops. DATABASE_URL was loaded exactly once per shell via
`set -a; . runtime/eoa.env; set +a` and passed straight into short-lived python/psycopg scripts
(`os.environ["DATABASE_URL"]`) — the value itself was never `cat`/`grep`/`echo`ed or printed at any
point, and every query used port 5432 (confirmed via `SELECT version_num FROM alembic_version` =
`'0025'`, matching the brief). Live chat was tested against `POST http://127.0.0.1:8765/api/ask`
(SSE), all 8 golden questions asked exactly once, sequentially, last — total wall clock under 7
minutes of the 45-minute budget, no timeouts, no retries needed (each answer came back in 30–70s).

Every claim in `docs/qa/loop/round_6_fixes.md` was independently re-verified against a live `SELECT`
and/or the actual bytes of the rebuilt report files and the underlying items' `clean_text` — never
against the fixes doc's own prose. Golden sample: all 40 items in `docs/qa/loop/golden_items.json`
(one consistency-check query), the 6 golden investigation job ids, all 8 golden chat questions, the
freshest daily/weekly/monthly reports, all 8 BD territory files (newest per territory per the
brief's round-6-specifics override), and both named patent surveys.

## Per-domain findings

### D1 — סיווג ומיון (score 80, n=40, was 70)

The two defects that had been open for 3–4 rounds running are both genuinely resolved. Item 5604's
score/level mismatch is gone (score=1, level='archive', reason sum=3, `level_for(1)`='archive' —
fully consistent). Items 22/153/290, flagged since round 1 for empty `entities_mentioned`, are all
now populated — items 153/290 with real names correctly unioned in from their own `events`/
`graph_edges` (TC-Next/GraphCast/WeatherNext/Pangu-Weather/IFS HRES for 153; three UK MoD/UKDI
entities for 290).

The one real defect found: item 22's own `entities_mentioned` array still literally contains
`'F-16s'` and `'Western Partners'` — the exact two junk-name shapes this round's own new
`is_junk_candidate_entity_name()` guard was built to reject. The guard runs on new writes and on the
standalone `entities` table's cleanup, but nothing re-ran it against this item's own
already-persisted array, so the item that motivated the guard is the one item still visibly
violating it. All 40 golden items were checked for score↔level consistency against
`config/config.yaml`'s thresholds (red:8/orange:6/yellow:4) — zero mismatches found, a durable clean
result.

### D2 — סיכום ו-so-what (score 88, n=46, was 48)

The longest-standing open D2 defect — the `SO_WHAT_TEMPLATE_PHRASES_HE` family ("מחזק את מעמדה",
etc.) stuck at exactly the same 16 item ids for three straight rounds — is genuinely gone. A fresh
DB-wide `ILIKE` scan across all 11 banned phrases against `items.so_what_he` returns zero rows.
Round 5's own new finding — the same phrase leaking into a live chat answer (Q8) — also does not
reproduce: this round's Q8 (SPECTRO ISR) is clean, phrase-free, and well-grounded (see D5).

### D3 — אירועים וישויות (score 90, n=45, was 74)

Three real fixes, all independently verified: (1) zero events now belong to an
out-of-scope-domain or archive-level item (was 51–53, including the owl-leak event) — the new
analyze-stage scope filter plus the one-time cleanup both hold; (2) the Kongsberg
StrikeMaster/Operation Atlantic City events no longer carry the invalid `kind='ניסוי'` string (0
such events DB-wide; item 3702 now has properly-enum'd 'test'/'deployment' events); (3) the daily
report's empty-vs-populated duplicate business-event row (the MMA/US Air Force pair) is gone. One
minor, unconfirmed observation: the same table still lists 3 rows for "Operation Atlantic City"
(שותפות/פעילות מבצעית/פריסה) — not exact duplicates, but a reader sees the same story three times.

### D4 — חקירות עומק (score 78, n=15, was 42)

Round 5's #1 worst-list item and this round's explicit brief requirement are both directly
satisfied: job 113 now renders as "**נחסם** (לא נחקר בפועל)" as the primary line in both
`daily_2026-09-07.md` and `weekly_2026-09-07.md`, with the rerun history correctly demoted to a
parenthetical note — "blocked" is no longer buried behind "לא נמצא". Unchanged: the golden
investigation jobs (91/86/70/48/47/46) remain not_found/off_topic with confidence 0.0–0.3 on the
live DB, identical to round 5's characterisation.

### D5 — צ'אט "שאל את האנליסט" (score 45, n=8, was 35)

Mixed, and this is where round 6's most severe residual problems live.

**Genuinely fixed:** Q2 (Iron Beam) no longer fabricates a Rafael/AMPS-NG/$270M narrative — it
honestly states the sources don't detail a recent Iron Beam contract and lists real gaps. Q8
(SPECTRO ISR) correctly attributes the real AMPS NG sub-system and $270M contract to **Elbit**
(verified directly against items 93/321's actual clean_text, which really does name "AMPS NG" —
round 5's characterisation of AMPS NG itself as "fictitious" was an overstatement; the actual round-5
defect was misattributing it to Rafael/Iron Beam, and that misattribution does not recur). No
malformed `[9]]`-style citation markers found anywhere in this round's 8 answers.

**A new instance of the banned fabrication class:** Q1 (XM30) confidently states, as fact #4 sourced
to `[1]` (item 257), that both prototypes carry "טכנולוגיית EO רב-מודלית: MWIR, SWIR ו-VIS." Item
257's full `clean_text` (all 4511 characters, directly read) never mentions MWIR, SWIR, EO/IR,
"electro-optic," or "infrared" anywhere — it is exclusively about vehicle delivery, program value,
hybrid-electric powertrain and the Team Lynx supplier roster. This is a confidently-numbered,
fully-invented EO/IR technical claim — the round-6 brief explicitly named this fabrication class as
one that "must not recur," and it has, just with different fabricated content.

**A new, severe protocol-level problem, found on 3 of 8 questions:** Q6 (AUSA) received **zero**
`answer_final` SSE events — only raw `token`/`sources`/`done` events; the content, reconstructed from
the token stream, is actually coherent, but any consumer following the documented "collect
`answer_final`" contract gets nothing. Q4 (DROIC) emits **two** `answer_final` events: the first
correctly opens with the retrieval-relevance caveat, the second drops the caveat and is truncated
mid-sentence. Q3 (Greece/LORA) also emits two events, with the second gluing `## תשובה ישירה`
directly onto `### הקשר קרוב (לא התשובה)` with no separating sentence, duplicating the whole answer.
None of this occurred in round 5's sample — it is a new regression class, orthogonal to the
fabrication-content axis. Additionally, Q5 (Skyranger) reads as confident analysis (asserting an HEL
capability, EMCON parallels) with almost no inline citations, then admits in a footer that 6 of 8
retrieved sources have no direct connection to the subject.

Per this round's own scoring rule, one confirmed fabrication in a sample of 8 caps this domain at
60; the additional protocol-level answer-delivery failures on 3 of 8 questions justify scoring below
that cap.

### D6 — דוח יומי/שבועי/חודשי (score 78, n=96, was 60)

The shared D4/D6 flagship fix (job 113 rendering) holds in both reports. The weekly heading-budget
fix is real and verified: `weekly_2026-09-07.md` has exactly 15 H2 headings (≤16 budget, down from
33). Two things the round-6 brief didn't flag but are real: `daily_2026-09-07.md` itself has 14 H2
headings against its own ≤12 budget (the grouping fix wasn't extended to daily), and the daily
report's "מעקב אינדיקטורים" table is heavily duplicated — 10 rows cover only 3 distinct underlying
indicators, each restated 3+ times, uncaught by any existing dedup check. BLUF, exec summary, "מה
השתנה," outlook likelihood/confidence separation and the unified Israel-industry table all verified
present and substantive, durable from round 5. The tender-forecast near-duplicate rows round 5 found
are gone (see D9).

### D7 — דוח פיתוח עסקי (score 72, n=8, was 50)

The specific W18 cross-territory leak round 5 reconfirmed as still-broken (Elbit/Anduril appearing
unscoped in DE/EU/GB) is fixed in the rebuilt files — those territories now correctly show no
acquisition-watch activity for that story, and bd_us's own Elbit/Anduril row is legitimately
in-scope under the brief's "any party in-territory" rule (Anduril is US). The one residual defect:
`bd_il_2026-09-06.md` — which the brief's "newest file per territory" instruction still names, since
IL wasn't rebuilt this round — still has its round-5 internal duplicate acquisition-watch row
(`Elbit | שותפות | — | — | [11]`, listed twice with different dates). BLUF length, buyer-pipeline
tables, tier ranking and assumptions/falsifiers sections all verified present and populated across
all 7 non-empty territory files.

### D8 — סקר פטנטים (score 90, n=43, was 82)

All three round-5 D8 findings are substantively fixed, verified live on both fresh surveys.
Assignee-data coverage: 0%→100% (DROIC, 10/10) and 17%→100% (Anduril, 6/6). The literal "לא מסווג"
string is gone from the Anduril survey's cluster heading and matrix, replaced with "אשכול נושאי:
anduril / inc / industries" — this closes the deterministic checker, though the replacement label is
itself a content-quality nit: its "terms" are fragments of the assignee's own company name rather
than a real technology term, so it reads as a restated assignee name dressed up as a cluster label.
Both the CPC×assignee and cluster×assignee matrices render with real, populated data. No bogus
generic assignees found in either survey — every profile is a real, named entity (Raytheon Co,
Sensors Unlimited Inc, Sabanci Universitesi, Anduril Industries Inc, Raytheon BBN Technologies Corp,
Individual).

### D9 — מכרזים/תחזיות/כנסים (score 85, n=89, was 76)

Both round-5 findings are fixed and verified live: the tenders table's junk duplicate candidates (5
near-identical Northrop Grumman/Counter-UAS rows) are gone (13→8 rows; the 5 accepted/archived rows
and the 1 genuine candidate are all preserved), and `tender_forecasts` (10 live rows) has zero
near-duplicate groups — every (platform, buyer_country, payload_need) triple is unique. One
previously-undisclosed, low-severity finding: one of the 3 remaining candidate tenders (id 34) is a
Dutch legal/HR consulting contract entirely unrelated to EO/IR defense — a pre-existing
intake-relevance gap, not part of this round's claims. The 5 accepted tenders backing the reports
still all have deadlines already in the past (unchanged since round 1). Source-reliability column
remains present in the appendix but its cells are still blank.

## Worst 10 (most severe first)

1. **D5** — Golden Q1 (XM30): a new, fully-invented "EO רב-מודלית: MWIR, SWIR ו-VIS" claim attributed
   to item 257, whose actual text never mentions any EO/IR technology — reproduces the exact
   fabrication class this round's brief said must not recur.
2. **D5** — Golden Q6 (AUSA 2026): zero `answer_final` SSE events emitted; the documented
   final-answer contract silently fails even though the underlying content is coherent.
3. **D5** — Golden Q3 and Q4: duplicate/malformed `answer_final` events in the same stream — Q4's
   second event drops the retrieval-relevance caveat and truncates mid-sentence; Q3's second event
   glues a heading directly onto another heading while duplicating the whole answer.
4. **D1** — Item 22's `entities_mentioned` still contains "F-16s"/"Western Partners," the exact
   junk-name shapes this same round's own guard was built to reject, because the guard was never run
   against this item's own already-persisted array.
5. **D1/D9** — 245 of 365 entities (67%) are now fully orphaned post-cleanup, including 2 of the 4
   entities ("Rees Training Center," "Global Owl Project") the brief names as "gone" — the cleanup's
   EXISTS-based query structurally can't catch a zero-mention entity, a gap disclosed for only one
   name.
6. **D7** — `bd_il_2026-09-06.md`'s internal acquisition-watch duplicate row, unchanged from round 5
   because the file was never rebuilt this round.
7. **D6** — Daily report's indicator-watchlist table: 10 rows, only 3 distinct indicators, each
   restated 3+ times — no existing check catches within-table duplication like this.
8. **D6** — `daily_2026-09-07.md`'s own H2 count (14) exceeds its ≤12 budget; the weekly grouping fix
   wasn't extended to daily.
9. **D5** — Golden Q5 (Skyranger): confident technical claims (HEL capability, EMCON parallels) with
   almost no inline citations, followed by a footer admitting 6 of 8 sources are unrelated.
10. **D8** — The "לא מסווג" fix on the Anduril survey produces a low-quality label built from
    assignee-name fragments ("anduril / inc / industries") rather than a real technology term.

## Comparison with round 5 (what moved and why)

Round 5's two most severe findings — job 113 rendering as "לא נמצא" instead of "נחסם," and the
16-item so_what template-phrase family stuck for 3 rounds — are **both genuinely closed** this
round, verified independently against the live DB and the actual rendered report files, not just the
fixes doc's claims. The D7 territory-scoping leak and the D8 assignee-coverage/unclassified-cluster
findings are likewise substantively fixed. D9's tender/tenders duplication and D3's owl-leak/
'ניסוי'-mistyping findings are all closed. This produced large score gains in D2 (+40), D4 (+36),
D8 (+8), D9 (+9), D6 (+18), D3 (+16), D7 (+22), D1 (+10).

D5 moved the least (+10) despite real, verified content fixes (Q2, Q8) — because a *different*
fabrication in the same class round 5 flagged reappeared on Q1, and a wholly new protocol-level
defect (missing/duplicated/truncated final-answer events) showed up on 3 of 8 questions, a failure
mode round 5's sample never exhibited. This is the one domain where round 6's fixes, whatever they
targeted, left the net user-facing risk about the same as before — a different fabrication and a new
delivery-reliability problem replacing the ones that were closed.

## Recommended round-7 packages, ordered by expected score gain

1. **Fix the `answer_final` SSE delivery bug** (missing on Q6, duplicated on Q3/Q4, truncated on
   Q4) — this is an infrastructure-level defect affecting 3/8 of this round's sample; if it affects a
   similar fraction of live production traffic it silently degrades or breaks the core chat feature
   for a large share of real questions, independent of any content-fabrication work.
2. **Add a source-grounding check for numeric/technical "key facts" in chat answers** (the same class
   that produced round 5's HEL/ATR fabrication and this round's MWIR/SWIR/VIS fabrication on the
   identical question/item) — a keyword-presence check between a claimed technical fact and the
   cited item's `clean_text` would have caught both instances.
3. **Extend the weekly heading-grouping fix to the daily template** and add a within-table
   duplicate-content check for the indicator-watchlist table (not just the existing cross-table
   citation-set dedup) — both are cheap, mechanical fixes with a clear, already-demonstrated pattern
   to follow from the weekly work.
4. **Retroactively re-run the junk-entity-name filter against already-persisted
   `items.entities_mentioned` arrays** (not just new writes and the standalone `entities` table), and
   add a "zero-mention entity" population query to `entities_cleanup` alongside the existing
   out-of-scope-only one, to close the entity-orphan blind spot.
5. **Rebuild `bd_il`** so its BD report reflects the same acquisition-watch dedup fix already applied
   elsewhere this round.
