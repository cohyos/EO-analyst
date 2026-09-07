# Round 7 Judge B Report (J7b, independent re-judge of D4/D5, read-only)

## Scope and method

J7 scored D4 (30) and D5 (40) against process artefacts it could not see through: the re-run
investigation jobs 131-140 failed on a worker running pre-08:55 code (`ImportError: cannot import
name 'FallbackSynthesisOut'`), and all 8 golden chat questions hit `gate_busy` because interactive
chat was queued behind the local Ollama model, contended by a concurrent Playwright e2e run. Since
then: the stack was restarted at 11:10 on current code, `config/config.yaml`'s
`llm_providers.interactive_default` was set to `"chain"` (chat now follows the Claude→Gemini→local
role chain instead of always queueing behind Ollama), and the four golden investigation questions
were re-queued as jobs 145-148 through the real `investigate()` production path (not the
`investigate_batch_cloud` bypass J7 caught as a dead-end).

Read-only throughout: `DATABASE_URL` loaded once via `set -a; . runtime/eoa.env; set +a` into
`.venv/Scripts/python.exe` + `psycopg` (`PYTHONUTF8=1`), never echoed; confirmed `inet_server_port()`
= 5432, `current_database()` = 'eoanalyst' on every query. No pipeline runs, no DB writes beyond
these two output files, no process kills, no git commands, no OMC tools. The 8 golden chat questions
were asked once each via `POST http://127.0.0.1:8765/api/ask`, parsing the `data: `-prefixed
JSON-per-line SSE stream for an `answer_final` event (4-minute per-question timeout; total wall
clock well inside the 45-minute budget). Two cited source URLs were independently re-fetched live
(WebFetch) to check citation integrity, and DB `items.clean_text` was read in full for every item
underlying both D4's rerun questions and D5's chat answers.

## D4 — חקירות עומק (score 45, n=12, was 30)

### What is genuinely better

Jobs 145-148 (reruns of golden questions 47/48/70/86-91) were queued and ran through
`investigate()` directly — the real production path, using `ddgs`/`ddgs-news`/`fetch` engines, not
the one-off `claude` CLI batch call (`investigate_batch_cloud`) that produced round 7's showcased
"successes" (jobs 137-140) but never persisted anything (`ImportError`, `result=NULL`). Three of
four succeeded on this real path:

| Job | Item / question | Pages read | Outcome / confidence | Notable honesty signal |
|---|---|---|---|---|
| 146 | 44 — AARGM-ER unit price | 1 | found / 0.85 | Correctly states no unit price was published (Japan's FY2027 request is an unpriced "item request"); does not invent a number |
| 147 | 81 — Norkin/Anduril candidates | 3 | found / 0.9 | Names the real competing candidate (Amir Abulafia) accurately |
| 148 | 1352 — Reaper successor (MMA) | 4 | partial / 0.5 | Explicitly flags one of its 3 sources as Cloudflare-blocked and unusable, and flags a rank-title discrepancy between two other sources instead of silently picking one |
| 130 | 117 — degenerate question | 0 | insufficient_context / 0.0 | Correctly recognizes the "question" is leftover meta-commentary, not a real question, in 0.1s |

Reading `agent/eoa/llm/chain.py:100-170` directly (not just trusting the fixes doc's prose)
confirms the "critical cross-cutting finding" the fixes doc raised — that the cloud-chain dispatch
path can never carry a `tools` schema and always returns `tool_calls=[]` — does not match today's
live code. `run_chain` explicitly checks each non-`ollama` leg for `supports_tools` and raises
`ProviderUnavailable` to fall through to the local `ollama` leg (which does support tools) whenever
a tool-calling turn is required. This is exactly the mechanism that let jobs 146-148 actually call
`search`/`read`/`finish` and produce real, well-sourced answers — whether this is a fix that landed
after 07:23 (when jobs 137-140 failed) or a mischaracterization in the fixes doc's own account, the
live behavior today is materially better for 3 of 4 golden questions than either document claimed.

### What is not better — reproduced live, right now

**Job 145 (item 10/47, AeroVironment $465M laser contract — the exact question the fixes doc
showcased as job 137's star "found/0.9" success) fails on current code, via the real production
path, with the identical historical symptom.** `investigation_log` shows 16 rows, 56 cumulative
search hits across 4 full rounds, 15/15 query budget consumed — and **zero** `read`/`fetch`
attempts logged at any point, unlike its three sibling jobs, each of which shows explicit `fetch`
rows every round. The job ends with `outcome=not_found`, `confidence=0.0`, `pages_read=0`, and the
same blank generic message ("לא נמצא מידע מספק במסגרת התקציב") this exact question has returned
since round 3.

This is explainable directly from the code, not just from the symptom: `MIN_PAGES_BEFORE_NOT_FOUND`
(`agent/eoa/search/deep_search.py:163,1738-1757`) only fires when the model explicitly calls
`finish(outcome=not_found)` — it has nothing to say about a loop that simply exhausts its query
budget without ever attempting a `read`. And `_synthesize_from_reads`
(`deep_search.py:1197-1210`), this round's own new fallback for exactly this failure class, returns
`None` immediately when `inv.read_summaries` is empty — its own docstring says it activates "when
... at least one page WAS successfully read." Job 145 read none, so neither guard the round-7 fixes
doc built for this exact failure mode actually catches it. This is a live, reproducible gap, not a
historical artefact.

**The single most severe finding from J7 — that the reader-facing reports do not reflect any of
this — is directly reconfirmed today, after the fact, not just historically.** `weekly_2026-09-07.md`
was rebuilt at **11:14**, after all four of jobs 145-148 finished (the last, 148, at 11:02);
`monthly_2026-09-30.md` was rebuilt at 10:52, after 145/146/147 finished. Both freshly-rebuilt files
still render the byte-identical years-old text for these exact questions: job 46's "לא בוצעה חקירה"
and job 86's off-topic MOSP 5000 answer, word-for-word matching what rounds 3-6 already showed. None
of jobs 130/145/146/147/148's substantially better content — including two `found`/0.85-0.9 answers
— reaches either report. Because this was checked against a fresh rebuild that post-dates job
completion, this rules out a mere timing race: the report-generation layer's logic for selecting
which investigation job to render per item/question is a real, structural gap independent of the
persistence bug J7 found.

### Citation-integrity spot checks (2 of 2 sampled found a real issue)

1. **Job 146** cites `https://easternherald.com/2026/09/05/japan-fy2027-defense-budget-...` as its
   sole source [1] for specific facts (Japan's ¥8.89T/$55.6B FY2027 budget, Northrop Grumman, the
   Navy's "strategic pause"). `investigation_log`'s own fetch row for this exact URL logged
   `title='Just a moment...'` — Cloudflare's bot-challenge interstitial page, not the article — yet
   this was silently counted as `pages_read=1` and cited with no disclosure. (Re-fetching the URL
   independently now also returns HTTP 403.) Job 148, in the same batch, correctly disclosed an
   equivalent block on one of its own sources — this asymmetry shows the disclosure behavior is
   real but inconsistent. Separately, the underlying facts were independently verified via a live
   web search (breakingdefense.com, navalnews.com, twz.com all corroborate the budget figure and
   the "strategic pause") and are accurate — this is a sourcing-transparency gap, not a content
   fabrication.
2. **Job 147** states as settled fact, at confidence 0.9, "בחירת נורקין על פני אבולעפיה" (the
   choice of Norkin over Abulafia), citing a Hebrew Globes article (`did=1001546987`). Independently
   re-fetching that exact URL shows it describes an **unresolved** three-candidate process, with all
   three candidates "expected to meet next week" with Anduril's founder/CEO to determine who gets
   the role — not a completed choice. The item this question was originally seeded from (item 81)
   carries a *different*, later English Globes URL (`did=1001553989`) that does confirm Norkin was
   chosen — job 147 never fetched it. The real-world answer (Norkin was in fact chosen) happens to
   be correct, but job 147's own cited evidence does not establish it; its own buried "gaps" section
   admits "no final official appointment was stated in the source articles," directly contradicting
   its confident headline framing.

### Verdict on J7's artefact diagnosis: **confirmed**

J7's core claim — that D4's problem was a persistence/reporting artefact, not fundamentally a
content-quality failure — holds up under re-judging. The underlying investigation logic, when it
actually runs through the intended path, now regularly produces good, honest, appropriately-hedged
content (146, 147, 148 all real, useful answers). But the score cannot move far above J7's 30
because the two problems that made D4's headline finding severe are *both* still live today: (1) a
real, reproducible instance of the original "zero-page-read → blank not_found" bug survives on
exactly the showcased question, and (2) the reports that are the system's actual deliverable still
show nothing of any of this, confirmed after a fresh rebuild. Score: 45 (up from 30, reflecting that
3/4 fresh reruns now genuinely succeed end-to-end into `jobs.result` via the real path — a
meaningfully different and better state than round 7's all-failed/fabricated-success finding — but
capped by the confirmed-live report gap and the two sourcing-integrity issues found on spot check).

## D5 — צ'אט "שאל את האנליסט" (score 80, n=8, was 40)

### Delivery: completely fixed

All 8 golden questions returned a real `answer_final` SSE event on the first attempt — zero
`gate_busy`, despite a genuinely concurrent `deep_search` pipeline job (id 149, running
10:41-11:12) overlapping the entire test window. This is a full reversal of J7's 14/14 `gate_busy`
result and directly confirms the fix: `llm_providers.interactive_default: "chain"` routes
interactive chat through the Claude→Gemini→local role chain instead of unconditionally queueing
behind the local Ollama model, and `llm_calls` shows `role='resident', provider='claude',
model='claude-sonnet-5'` actively serving these requests in real time. Response times ranged 61-91s
per question.

### Content quality: strong, with real recurring gaps

Four of eight questions (Q2/Iron Beam, Q3/LORA-Greece, Q6/AUSA 2026, Q7/US EO-IR RFI) correctly
identified and refused a false or unsupported premise embedded in the question, rather than
fabricating a bridging answer:

- **Q3** explicitly separates the real Greece air-defense deal (David's Sling/Barak MX/Spyder) from
  the LORA missile (which the DB only links to a German Navy trial), rather than conflating the two
  as the question's framing invites.
- **Q7** correctly reports that the only EO/IR RFI in the DB is Finnish (FDFLOGCOM, for NH90
  helicopters), not American, and declines to answer the US-framed question as literally asked.
- **Q2** and **Q6** both explicitly state no source covers the topic and refuse to fill the gap from
  general knowledge without clearly labeling it as such.

Round 6's three specific fabrication/delivery findings do **not** reproduce in the same form this
round: Q1 (XM30) no longer invents the MWIR/SWIR/VIS multi-sensor claim on item 257; Q5 (Skyranger)
is now well-cited throughout and explicitly declines an unsupported technical comparison (round 6's
near-citation-free confident claims are gone); Q6 (AUSA) now delivers a real `answer_final` at all
(round 6's zero-event bug is fixed). Q8 (SPECTRO ISR) remains clean — verified word-for-word against
items 93/321's actual `clean_text`: the $270M figure, 6-year term, MWIR/visible/SWIR channels, AMPS
NG, and the CEO's name are all correct.

Two new, real defects were found on spot-checking:

1. **A small numeric inaccuracy in Q1**: the answer states Rheinmetall "plans to deliver up to eight
   additional prototypes this year," citing item 257 — but item 257's own `clean_text` explicitly
   says "The company plans to deliver **seven** additional prototypes." This is a smaller instance
   of the same fabrication class round 6 flagged on this identical item (there it was a wholesale
   invented MWIR/SWIR/VIS claim; here it is a single digit changed from a real, cited fact).
2. **A live, reproducible protocol-formatting bug**: the `answer_final` event's own `text` field
   leaks a raw internal delimiter into the user-facing answer in 2 of 8 sampled questions. Both Q4
   (DROIC) and Q6 (AUSA) end with the literal string `===SOURCES_JSON===\n"}]}` appended after the
   real content — an artifact of some unstripped internal prompt/response boundary. Q6 additionally
   renders a corrupted, meaningless one-word fragment ("סי.") as its own paragraph where a section
   heading should be. This is a new instance of the general failure class round 6 flagged
   (missing/duplicated/malformed `answer_final` content) — the specific symptom changed from
   missing/duplicated events to a leaked internal marker, but the underlying "the delivery contract
   for `answer_final` is not fully reliable" problem persists, now visible in roughly a quarter of a
   small sample.

The active content-safety guard is real and working, not just claimed: `removed_by_guard` counters
on every response (e.g. Q4: 9 items removed across `grounded_entity`/`retrieval_relevance_caveat`/
`low_citation_caveat`/`uncited_factual_claim` categories) show genuine ungrounded content being
intercepted before reaching the user — though what exactly was removed is not visible to the end
user or to this judge in reviewable form, so its own precision/recall cannot be independently
assessed this round.

### Verdict on J7's artefact diagnosis: **confirmed**

J7's diagnosis — that D5 was blocked by resource contention (chat queued behind the local model,
worsened by concurrent e2e traffic), not a content defect — is strongly confirmed. The fix
(bypassing the local-model gate via the chain config) produced 8/8 successful, prompt answers even
with genuine concurrent pipeline load, and the content that was finally visible for the first time
in three rounds is substantially better than round 5/6's content-fabrication baseline. Score: 80 (up
from 40 — the largest single-domain move in this re-judge — reflecting that the domain went from
completely unobservable to reliably delivering honest, well-grounded answers on 6-7 of 8 questions,
capped below "excellent" by the recurring leaked-delimiter formatting bug and one small numeric
inaccuracy).

## Summary

| Domain | J7 score | J7b score | Artefact diagnosis confirmed? |
|---|---|---|---|
| D4 | 30 | 45 | Yes — persistence/reporting gap, not fundamental content failure; but the report gap and one instance of the original zero-read bug both reproduce live today |
| D5 | 40 | 80 | Yes — resource contention, not content defect; delivery is now fully fixed, content is honest with two new minor/moderate defects |

## Top 3 remaining defects (most severe first)

1. **D4 — reports don't render fixed investigations, confirmed live after a fresh rebuild.**
   `weekly_2026-09-07.md` (rebuilt 11:14) and `monthly_2026-09-30.md` (rebuilt 10:52) both post-date
   the successful completion of jobs 145-148, yet both still show the years-old stale not_found/
   off-topic text for these exact questions. This is the single highest-leverage fix remaining: the
   underlying investigation content is now frequently good; almost none of it reaches the reader.
2. **D4 — the original "zero-page-read → blank not_found" bug still reproduces on live code.** Job
   145, run today via the real production path on the exact question round 7's fixes doc showcased
   as fixed, spent its entire budget searching (15/15 queries, 56 hits) without ever calling `read`
   once. Neither `MIN_PAGES_BEFORE_NOT_FOUND` nor `_synthesize_from_reads` catches a loop that never
   attempts a read at all — both guards assume at least one read attempt or an explicit
   `finish(not_found)` call.
3. **D5 — a leaked internal delimiter reaches the user-facing chat answer.** `===SOURCES_JSON===\n"}]}`
   appears verbatim at the end of 2 of 8 sampled `answer_final` texts (Q4, Q6), and Q6 separately
   renders a corrupted one-word fragment in place of a section. This is cosmetically minor but
   visible to every real user who asks a question the guard has to trim heavily, and it is the same
   general class of `answer_final` delivery-reliability defect round 6 flagged as its #2 worst-list
   item.
