### R11-chat status

**Package:** R11-chat (docs/qa/loop/round_10_judge.md, D5 = 85; worst-list #2, #3, #4).
**Files owned/changed:** `agent/eoa/api/ask_grounding.py` (`enforce_answer_coherence`'s new
cross-reference-opening check: `_CROSS_REF_OPENING_TOKENS`/`_CROSS_REF_OPENING_RE`/
`_MIN_CROSS_REF_REMAINDER_WORDS`; `entailment_filter`'s smaller probe payload
(`_ENTAILMENT_SOURCE_EXCERPT_CHARS` 1500 -> 800), new `fast_chain_timeout_s`/`answer_elapsed_s`
parameters, new resident-chain third-tier fallback), `agent/eoa/api/routes/ask.py` (passes
`answer_elapsed_s=time.monotonic() - t_answer_start` at the one real entailment call site),
`agent/eoa/api/services.py` (new `_relevant_excerpt`, wired into `ask_build_messages`'s per-item
body construction for both the context and retrieved branches), `agent/eoa/llm/prompts/
ask_answer_format.md` (unchanged this round -- see finding 1's own note on why), `tests/unit/
test_ask_round11.py` (new, 32 tests).

#### 1. Q5 (Skyranger) item 1353's spec retrieved but never synthesized (worst #2)

**Root cause found and reproduced deterministically, offline, against the real DB row.** Traced
the whole path per the brief:

`ask_retrieve` (real DB, golden Q5 -- `"כיצד משתווה ה-Skyranger של Rheinmetall למערכות נגד כטב"ם
(C-UAS) ישראליות מקבילות?"`) genuinely returns item 1353, ranked **3rd of 10** (round 10's own
cap-widening fix holds), and `ask_build_messages` cites it as **`[3]`**. So far this matches round
10's own finding exactly -- retrieval is not the gap. The gap is one layer further down, inside
`ask_build_messages` itself: item 1353's `clean_text` is 15,223 characters, and the retrieved-item
branch built its body as a flat `clean_text[:4000]` prefix. Querying the real row directly:

| Term | Character offset in `clean_text` |
|---|---|
| First `"UAS"` (generic, matches part of `"GBADs"`/`"drones"` context) | 1083 |
| First `"Rheinmetall"` | 13463 |
| First `"Skyranger"` | 13493 |
| The actual spec sentence (`"...firing rate of 1,000 rounds per minute..."`) | ~13600 |

The one paragraph that actually names Skyranger and states its rate-of-fire sits **9500+ characters
past the naive 4000-char cut**. The model was shown source `[3]`'s title (`"U.S. Air Force Seeks
Anti-Aircraft Guns To Protect Its Overseas Bases"` -- itself never mentions Skyranger) plus 4000
characters of body that stop entirely before the Skyranger paragraph begins. The model's own
live answer (round 10's judge: *"the final synthesized answer still doesn't use it... explicitly
states no source gives Skyranger's technical spec"*) was **correct given what it was actually
shown** -- it never fabricated or mis-cited anything; the slice of source `[3]` it saw genuinely
said nothing about Skyranger. This is why the prompt itself (`ask_answer_format.md`) was left
unchanged this round: the defect was never a synthesis/prompting gap as the brief's fallback
hypothesis suggested, it was a data-layer truncation gap.

**Fixed:** `eoa.api.services._relevant_excerpt(text, anchors, budget)` replaces the flat prefix
slice for both the retrieved-item body (4000-char budget) and the context-item "full text" tail
(1500-char budget). `anchors` is the question's own `_rare_tokens(question)` (the same rare/salient
token extractor `ask_retrieve`'s own lexical pass already uses -- no new extraction logic, no
extra DB call in the common case). Instead of always keeping only the document's head, the excerpt
keeps a small head window (so it still opens sensibly) plus a window around anchor matches,
**rarest-anchor-in-this-document first** -- not document order, and not first-match-wins.

That rarity ordering was not a hypothetical caution -- an offline replay against the real row
caught the naive (document-order) version of this fix *still* failing to surface "Skyranger" for
item 1353 specifically, because `"UAS"` (a generic token matched 4x, starting at char 1083) ate the
whole budget before the fill ever reached the specific `"Rheinmetall"`/`"Skyranger"` pair (2 and 4
matches respectively, first occurrence at char 13463/13493). Reprocessed rarest-anchor-first (one
window per anchor before any anchor gets a second), the same real row now produces:

```
BEFORE (text[:4000]):            "Skyranger" in body -> False
AFTER  (_relevant_excerpt):      "Skyranger" in body -> True
AFTER:  "firing rate of 1,000 rounds per minute" in body -> True
AFTER excerpt length: 2890 / 4000 chars
```

-- proven directly against the live DB row (read-only, via `DATABASE_URL` from `runtime/eoa.env`,
never printed), not a synthetic fixture. `tests/unit/test_ask_round11.py`'s
`TestRelevantExcerpt.test_a_rare_late_anchor_is_not_starved_by_a_common_early_one` pins this exact
shape as a permanent regression test with a synthetic (DB-independent) fixture, and
`TestAskBuildMessagesSurfacesBuriedSpec.test_retrieved_item_body_carries_the_buried_anchor_match`
proves the fix reaches `ask_build_messages`'s actual output end to end. Falls back to the prior
`text[:budget]` behavior byte-for-byte whenever `anchors` is empty or matches nothing in a
document -- every existing test's fixture text (and the common case of an ordinary, anchor-less
item) is unaffected; `TestAskBuildMessagesSurfacesBuriedSpec.test_anchor_less_question_keeps_
previous_prefix_behavior` and the full `test_ask_retrieval.py`/`test_ask_round2_chat_fixes.py`
suites (unedited, still passing) confirm this.

**Not fixed this round, by design:** the model's own downstream synthesis behavior (whether it
actually *uses* the now-visible Skyranger paragraph in its final prose) was not re-verified against
a live model call this round -- the live API process still serves pre-round-11 code (only the lead
restarts it), so a live `/api/ask` probe right now would test old code, not this fix, exactly as
round 9's and round 10's own status docs noted for their own findings. The offline evidence above
proves the *input* the model receives now contains the spec; a future round's judge re-asking Q5
live, after a restart, is the right place to confirm the *output* changed too.

#### 2. Q6 (AUSA 2026) referentially dangling opening recurs, 1/8 (worst #3)

`enforce_answer_coherence` already had two content-blind leading-unit checks (round 10): too few
words, or the section's only unit with no terminal punctuation. Neither catches a **structurally
complete** opening that is only dangling *referentially* -- it opens with a cross-reference/
continuation token whose antecedent was a sentence some earlier guard already removed. The live
repro (round 10's judge, verbatim): `" שאר המקורות (...)"` -- grammatically a complete, correctly
punctuated fragment; only the word "שאר" ("the rest [of]") gives away that it once followed a
sentence naming which sources it meant, and that sentence is gone.

**Fixed:** a third, independent check on the same per-section leading unit (only reached when the
first two checks did *not* already flag it): if the unit starts with one of
`_CROSS_REF_OPENING_TOKENS` (שאר, לעומת זאת, כמו כן, עם זאת, בנוסף, גם, מנגד, לכן, לפיכך, אולם, אך,
"the other", "in addition", "however"), the token (plus a trailing comma/colon) is stripped and the
remainder is measured: `>= 8` words and it replaces the unit in place (`"בנוסף, X" -> "X"` --
per the brief's own example); fewer than 8 words and the whole unit is dropped, same as the
existing dangling-fragment case. A bullet/numbered-list item is exempt (same reasoning as the
existing checks: `_iter_units` always keeps it whole, so it can never be this pass's target
shape), and the token is only ever matched at the very start of the unit (`^`) -- a cross-reference
word appearing mid-sentence is ordinary prose, not this defect.

**Offline replay** against the judge's own quoted fragment, reconstructed into a full answer shape
(the literal captured Q6 answer text is not persisted anywhere -- `golden_questions.json`'s own
note: *"D5 has no deterministic scoring today (no chat-log persistence table)"* -- so this replay
uses the judge's verbatim quote, embedded in a representative full-answer skeleton):

```
BEFORE:
שאר המקורות עוסקים בכנסים אחרים לגמרי ואינם קשורים ישירות לכנס AUSA 2026 הנדון כאן [4].

### עובדות מרכזיות
- כנס AUSA 2026 יתקיים בוושינגטון ויכלול תצוגת ציוד EO/IR [1].
- חברות ישראליות צפויות להשתתף בתערוכה הנלווית [2].

AFTER (removed=1, the dangling "שאר " token stripped, remainder kept -- it read as a
grammatical, self-contained sentence once the antecedent-less reference was gone):
המקורות עוסקים בכנסים אחרים לגמרי ואינם קשורים ישירות לכנס AUSA 2026 הנדון כאן [4].

### עובדות מרכזיות
- כנס AUSA 2026 יתקיים בוושינגטון ויכלול תצוגת ציוד EO/IR [1].
- חברות ישראליות צפויות להשתתף בתערוכה הנלווית [2].
```

`tests/unit/test_ask_round11.py`'s `TestCrossReferenceOpeningFragment` (11 tests) covers: the
literal short-fragment shape (removed entirely, `test_short_cross_ref_opening_is_dropped_
entirely`), the long-remainder strip-in-place shape, both English tokens ("however"/"the other"),
a mid-sentence token (never flagged), a bullet-leading token (never flagged, existing exemption
holds), a non-lead section heading (`### הערכת האנליסט`), and the exact 8-word/7-word remainder
boundary computed via `ask_grounding._word_count` rather than hand-counted (so the test itself
can't silently drift from the real threshold).

**Regression safety:** every existing `enforce_answer_coherence` test in the shared-suite
`test_ask_round10.py` (which this package does not own and must not edit) still passes unedited --
none of its fixtures open with a cross-reference token, so the new check is purely additive there.

#### 3. Entailment coverage only 3/8, not "well above 1/8" (worst #4)

This session's own `runtime/logs/api.*.log` files carry **zero** `ask.entailment_*` log lines (the
API process restarted at 14:10:01 this session, after round 10's own live-sampling window, and has
served no chat traffic with entailment logging since) -- so the round-10 judge's own "3/8, up from
1/8" figure could not be re-derived from this session's logs; the fix below follows the brief's own
prescribed mitigation directly instead of a fresh log-mined root cause.

**Fixed, three changes, all reachable only once the primary local (`ollama`) attempt has already
failed -- `chain_fallback=False`'s single-attempt contract (`test_ask_round9.py`'s
`TestEntailmentPinnedToOllama`, `test_ask_round7.py`'s `test_timeout_is_a_graceful_no_op`) stays
byte-for-byte unedited either way:**

1. **Smaller probe payload:** `_ENTAILMENT_SOURCE_EXCERPT_CHARS` 1500 -> 800 (the 4-claim cap was
   already in place from round 8, `routes/ask.py`'s `_ENTAILMENT_MAX_CLAIMS_CAP`; the whole batch
   was already always one call, never per-claim, since round 7).
2. **Elapsed-aware chain timeout:** the caller (`routes.ask`) now passes `answer_elapsed_s=
   time.monotonic() - t_answer_start` -- the same wall clock its own `_MAX_ANSWER_SECONDS` abort
   check already uses. `entailment_filter`'s chain attempt gets `fast_chain_timeout_s` (60s) when
   `answer_elapsed_s < 90.0` (the main answer itself came back quickly, real budget left to spend
   on this optional pass) and the unchanged `chain_timeout_s` (40s) otherwise.
3. **Resident-chain fallback for "the light role is unavailable," not merely slow:** `config/
   config.yaml`'s `llm_providers.chains.light` is `agy(gemini-3.8-flash-medium) -> ollama` -- **no
   Claude entry at all** (a deliberate 2026-09-06 decision to stop chat freezing behind a slow
   local queue). So when *both* the direct-`ollama` attempt and the light-role-chain attempt fail,
   that is the entire light role unavailable, not one slow leg -- a third, explicitly paid attempt
   now goes out against the **resident** role's own chain (`chat_structured("resident", ...,
   provider="chain")`, which does carry a Claude entry first per `llm_providers.chains.resident`),
   logged as `ask.entailment_resident_fallback_used` when it is the attempt that actually produces
   a result. **Documented cost:** this is a real Claude call instead of the light role's usual
   free/cheap flash-tier or local one -- acceptable because it only ever fires as a last resort
   after two cheaper legs already failed, on a pass that costs nothing at all when the caller does
   not opt into `chain_fallback` (only `routes.ask`'s own real call site does).

**Offline replay, real light-role calls (not mocked), against real retrieved context for all 8
golden questions** (one `entailment_filter(..., chain_fallback=True)` call per question, a short
synthetic single-claim probe citing the real `[1]` source `ask_retrieve` returned for that
question -- the literal captured answers are not persisted anywhere, see finding 2's own note, so
this measures the same probe pipeline against the same real retrieval each question would actually
use):

| Q | Retrieved items | Outcome | Resident fallback used | Time |
|---|---|---|---|---|
| 1 (XM30) | 8 | completed | no | 11.2s |
| 2 (Iron Beam) | 8 | completed | no | 15.2s |
| 3 (LORA/Greece) | 8 | completed | no | 14.1s |
| 4 (DROIC) | 10 | completed | no | 14.5s |
| 5 (Skyranger) | 10 | completed | no | 14.5s |
| 6 (AUSA 2026) | 5 | completed | no | 14.9s |
| 7 (RFI) | 7 | completed | no | 14.6s |
| 8 (SPECTRO ISR) | 10 | completed | no | 15.9s |

**8/8 completed** on the first (local `ollama`) attempt -- no chain or resident-chain attempt was
even needed this run (RAM was not under pressure this session: ~28GB free at the time of this
replay, per `Get-CimInstance Win32_OperatingSystem`). This is not, on its own, proof that the
round-10 judge's specific starvation shape (a real RAM shortage) is fixed -- it is proof the
pipeline itself (real retrieval -> real probe -> real local light-model call, chain_fallback wired
identically to production) completes cleanly end to end when the resource gate is not contended,
which round 10's own "3/8" figure suggests was not reliably true even then. The three code changes
above target specifically the *contended* case (smaller payload = faster once admitted; elapsed-
aware longer chain budget; resident fallback when the light role's whole chain, not just one leg,
is unavailable) -- a future round's judge sampling live traffic during real RAM pressure is the
right way to confirm those specifically. Target from the brief was `>= 6/8` completing (i.e. not
ending in `ask.entailment_check_skipped`); this replay reached **8/8**.

#### Tests / lint

- `tests/unit/test_ask_round11.py`: 32 new tests, all green.
- Full owned + shared regression set green (249 passed, 0 failed):
  `PYTHONPATH=agent PYTHONUTF8=1 .venv\Scripts\python.exe -m pytest tests/unit/test_ask_round11.py
  tests/unit/test_ask_round10.py tests/unit/test_ask_round9.py tests/unit/test_ask_round8.py
  tests/unit/test_ask_round7.py tests/unit/test_ask_round5.py tests/unit/test_ask_round3_grounding.py
  tests/unit/test_ask_retrieval.py tests/unit/test_ask_sse_sources.py -q -p no:cacheprovider`
- `.venv\Scripts\ruff.exe check` and `.venv\Scripts\ruff.exe format --check` both clean on every
  owned file touched this round.

#### What remains

- Finding 1's downstream synthesis behavior (does the model's own final prose actually *use* the
  now-visible Skyranger paragraph) is not live-verifiable until the lead restarts the API process --
  offline evidence proves the input changed; a future round's judge should re-ask Q5 live and
  confirm the output changed too, per that finding's own note above.
- Finding 3's real-world coverage number (this round's replay measured the probe pipeline against
  real retrieval + real light/chain calls, not against the judge's own original 8 live chat
  answers, which are not persisted anywhere) should be re-measured by a future judge sampling
  live `/api/ask` traffic after the restart, the same way round 10's own "3/8" figure was obtained.
- The monthly report / "תעשייה ישראלית" table question (round 10's worst #5, D6) is out of this
  package's file ownership and untouched.
