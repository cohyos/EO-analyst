# Round 8 — Judge J8b re-judge of D5 (chat) after the retrieval-gate fix

**Context.** J8 (`docs/qa/loop/round_8_judge.md` §D5) scored D5 at 45/100 with n=1: 7 of 8 golden
questions streamed zero bytes and hung past 240s; only Q1 (XM30) completed. The lead traced this to
the retrieval-time embedding call queuing 15+ minutes behind the host's resource gate under memory
pressure, and shipped a fix (commit e16e3e4, stack restarted) that bounds the interactive embed call
to a ~20s budget instead of letting it queue indefinitely. This re-judge asks all 8 golden questions
fresh, verifies every cited claim against the DB, and checks whether the fix actually restored D5 to
health or merely changed its failure mode.

**Score: 48/100, n=8.** **Artefact diagnosis: CONFIRMED** — the specific hang J8 found is fixed and
verified live. But a full n=8 sample surfaces a different, still-live defect (retrieval blindness to
proper-noun-only queries) severe enough that the domain does not recover to round 7's 80.

## Timing — the fix holds

All 8 questions were asked sequentially via `POST /api/ask`, `{"question": "..."}`, SSE, within a
45-minute budget and a 5-minute per-question timeout.

| Q | Topic | first_event_s | answer_final_s | events seen |
|---|---|---|---|---|
| 1 | XM30 / Bradley EO-IR | 48.48 | 159.47 | citations, meta, ~180×token, answer_final, sources, done |
| 2 | Iron Beam / מגן אור contract | 48.74 | 72.53 | citations, meta, ~48×token, answer_final, sources, done |
| 3 | LORA / Greece | 48.40 | 84.65 | citations, meta, ~58×token, answer_final, sources, done |
| 4 | DROIC trend | 48.64 | 84.84 | citations, meta, ~76×token, answer_final, sources, done |
| 5 | Skyranger vs Israeli C-UAS | 48.60 | 94.47 | citations, meta, ~93×token, answer_final, sources, done |
| 6 | AUSA 2026 relevance | 48.47 | 73.98 | citations, meta, ~49×token, answer_final, sources, done |
| 7 | Latest EO/IR RFI (US) | 48.67 | 75.09 | citations, meta, ~50×token, answer_final, sources, done |
| 8 | SPECTRO ISR (Elbit) | 48.46 | 78.77 | citations, meta, ~44×token, answer_final, sources, done |

8/8 completed with exactly one `answer_final` each, no `gate_busy`, no hangs, no exceptions. Every
question's `first_event_s` (the time to the `citations` event, i.e. retrieval completion) landed in
a tight 48.40–48.74s band regardless of question content — a suspiciously *uniform* delay across
eight structurally different questions, which turns out to matter (see below). Average time to
`answer_final` was ~89s, comfortably inside the lead's "~2.5 minutes" estimate and the 5-minute
timeout. **J8's sample-limited diagnosis held**: the hang is gone, the fix works as designed for the
specific failure mode it targeted.

## Content quality — a different failure mode surfaces at n=8

### Positives that hold up
- **Zero `===SOURCES_JSON===` leaks** in any of the 8 raw `answer_text` values.
- **No malformed `[n]` citation markers** — Q1's `[1][2]` markers both resolve to real entries in
  its `sources` event; no other question emits bracket markers without a matching citation.
- **No empty "עובדות מרכזיות" heading with zero bullets** — the round-7/8 defect J8 flagged as new
  did not recur. Q1 has 7 substantive bullets under that heading; Q2–Q8 skip the heading entirely in
  favor of a narrative refusal, which is a reasonable structural choice, not a bug.
- **Honestly-earned refusals for 3 of 8**: DB checks confirm Q4 (DROIC — 0 matches anywhere in the
  corpus), Q6 (AUSA 2026 — 0 matches), and Q2 (Iron Beam / מגן אור — 0 Hebrew-term matches) are
  refusing correctly; there is genuinely nothing in the corpus to cite for these three.

### The new defect: retrieval is structurally blind to most of this domain's vocabulary
7 of 8 questions returned **zero** citations — not "the corpus doesn't cover this," but a retrieval
step that never even tried the right items. Cross-checking the DB directly finds real, in-scope,
fully retrievable content for at least three of the seven "empty" questions:

- **Q8 (SPECTRO ISR)** — the chat answers *"אינני מכיר את המונח... ייתכן שמדובר בשם שגוי"* ("I don't
  recognize this term, may be a wrong name"). Item **321**, `Elbit Systems wins $270m contract for
  SPECTRO ISR & targeting payloads` (level=orange, domain=airborne_pods, security_status=clean,
  has summary_he), is an **exact title match** sitting in the DB, fully eligible for retrieval. This
  is the single worst finding of this pass: a confident "I've never heard of this" for a system the
  corpus already has a $270M-contract article about.
- **Q5 (Skyranger)** — the chat says *"לא נמצאו פריטים רלוונטיים במאגר"* ("no relevant items found").
  Item **1353** contains literal Skyranger-35 spec detail (35mm cannon, firing rate) directly
  answering the requested comparison; item **183** (level=orange, in-scope) is a live Rheinmetall/
  HENSOLDT release explicitly naming "mobile Skyranger solutions." Neither reached the model.
- **Q3 (LORA/Greece)** — the chat correctly finds no Greece-specific LORA item (verified: 0 DB rows
  join LORA + Greece/Greek), but it also surfaces **zero** retrieved items at all, rather than the
  real German-navy LORA firing-trial items (ids 62/278/37/84, one at level=yellow) as disambiguating
  "found separately" context. This is a regression from round 2's own documented fix for this exact
  golden question (`round_2_chat_fixes.md`): the anti-conflation logic depends on those items
  actually reaching the model so it can say "found separately, not the same story" — now they never
  arrive, so the model can't even attempt that disambiguation.

**Root cause, traced in code:** `agent/eoa/api/services.py`'s `_rare_tokens()` (line 1613) uses the
regex `\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z][A-Za-z0-9-]{2,}\b`, which **requires a
digit inside the token**. That correctly catches model numbers like `XM30` (which is exactly why Q1,
alone among the 8, got real lexical hits) but structurally can **never** match pure-letter proper
nouns — `SPECTRO`, `Skyranger`, `LORA`, `DROIC`, `AUSA` — which is most of this domain's vocabulary
and 7 of 8 golden questions' key terms. The vector-embedding fallback that's supposed to cover this
gap is itself failing almost every call tonight: grepping `runtime/logs/api.2026-09-07.log` for
`ask.retrieve_embedding_failed` returns **9** hits, essentially one per question asked in this test.
The uniform ~48.5s `first_event_s` across all 8 questions is consistent with every single embed call
hitting its interactive timeout rather than succeeding. In other words: **the round-8 fix traded an
infinite hang for a near-universal fast failure** of the exact same embedding call — retrieval no
longer hangs, but for anything not shaped like a model number, it now silently returns nothing, and
the model (correctly, given empty input) reports "not in the corpus" for content that is, in fact,
in the corpus.

### A genuine new defect in the one grounded answer
Q1 is the only question with real citations, and the one sample round 8's judge scored clean. Under
DB cross-check it is not: the answer states the current XM30 program phase is valued at **"53
מיליארד דולר"** ($53B), flatly cited `[1][2]` with no hedge. Its own cited source, item 257
(`army-technology.com`), states **"$1.53bn split equally between the two firms"** — the rendered
answer is ~34× the sourced figure. The answer text also opens abruptly mid-sentence directly on this
dollar figure, with no direct-answer lead-in sentence before it — consistent with a truncation at the
very start of the stream (possibly the "1." of "1.53" and its preceding clause being clipped) rather
than, or in addition to, a pure model transcription error. Either way, a materially wrong monetary
figure reached the end user as a cited fact. Grepping the log confirms why no guard caught it:
`ask.entailment_check_skipped`=2, `ask.entailment_check_removed`=0 today — the entailment guard that
would nominally flag an unsupported/contradicted numeric claim did not remove anything during this
test window, consistent with prior rounds' finding that this guard is not functioning live.
`ask.grounding_repair`=2 fired somewhere in the window but had no visible effect on Q1's
`ungrounded_removed` counter, which stayed 0 across all 8 answers.

### Q7 (RFI) — minor completeness gap, mostly defensible
The chat reports no corpus coverage and no reliable general knowledge for "the most recent EO/IR RFI
published in the US." DB check: most RFI-tagged rows are either `out_of_scope`/unsummarized or have
no `published_at`, so there genuinely isn't a dated, in-scope "most recent RFI" to point to. One
technically-retrievable row (id 5721, an undated generic EO/IR RFI template) exists and wasn't
surfaced — a minor gap, but the ultimate refusal is largely correct on the merits.

## Log grep (event counts only, per instructions — no lines dumped)

```
ask.entailment_check_removed:   0
ask.entailment_check_skipped:   2
ask.retrieve_embedding_failed:  9
ask.grounding_repair:           2
```

## Bottom line

The artefact J8 diagnosed — the ask pipeline hanging with zero streamed bytes because the retrieval
embedding call queued 15+ minutes behind the resource gate — is **genuinely fixed**: 8/8 golden
questions completed today, fast and uniformly (~48.5s to first event, ~89s average to
`answer_final`), with none of round 8's hang symptoms. J8's own diagnosis that this was a
sample-size/availability artifact, not a content-quality regression, **held up** for the specific
defect it named.

But testing the full sample instead of n=1 shows the fix did not restore D5 to round 7's health. It
changed retrieval's failure mode from "hangs forever" to "fails silently and fast" for any question
whose key terms are pure-letter proper nouns — which is most of this domain's vocabulary — while the
one question that still gets real citations (Q1, because "XM30" happens to satisfy a digit-requiring
lexical-match regex) delivers a ~34× numeric error with no hedge and no guard catching it. Score: 48,
down from round 7's 80, for reasons genuinely different from — and, on the evidence gathered here,
more structurally serious than — round 8's own hang-driven 45.

## Files referenced
- `agent/eoa/api/routes/ask.py` — SSE event contract (`citations`, `meta`, `token`, `answer_final`,
  `sources`, `done`), guard pipeline, `_strip_residual_sources_block`.
- `agent/eoa/api/services.py` — `_rare_tokens` (line 1613, digit-requiring regex), `ask_retrieve`
  (line 1658, hybrid lexical+vector retrieval with the 20s interactive embed budget and its
  `ask.retrieve_embedding_failed` log line), `_ask_item_retrievable` (line 1639), `ask_build_messages`
  (line 1728, citation dict shape: `n`, `item_id`, `level`, `source_name`, `report_kind`).
- `docs/qa/loop/golden_questions.json` — the 8 fixed questions asked this round.
- `runtime/logs/api.2026-09-07.log` — grepped for event counts only.
- Items cross-checked in `items` table: 257, 61 (Q1); 62, 278, 37, 84, 6285 (Q3, LORA); 183, 1353, 8
  (Q5, Skyranger); 93, 321 (Q8, SPECTRO ISR); 5721, 5121, 5604, 1864, 7954 (Q7, RFI).
