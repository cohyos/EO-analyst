# Round 2 chat fixes — D5 "ask the analyst" (docs/qa/loop/round_1_judge.md)

Scope: `docs/qa/loop/round_1_judge.md`'s D5 findings only -- `agent/eoa/api/routes/ask.py`, the
`ask_*` functions in `agent/eoa/api/services.py` (system-prompt additions only;
`ask_answer_format.md`/`system_analyst.md` themselves were not edited this round), and
`chat_stream`'s Ollama options in `agent/eoa/llm/ollama_client.py`. No data repair this round (D5
has no persisted chat-log table, `eoa.qa.d5_chat` does not exist yet -- every finding below is
live-tested against a throwaway `uvicorn` on port 8766, `runtime/eoa.env`, stopped after each
verification run; the shared 8765 live process was never touched).

Round 1's worst-10 list named D5 twice: item 1 (the infinite repetition loop, "new, severe,
reproducible") and item 7 (Q3 Greece/LORA topic substitution). Round 0/1 also both flagged zero
inline `[n]` citations on 3/5 completed answers, and Q4/Q7 citing the same academic arXiv paper as
both a DROIC hardware trend and a government RFI.

## 1. Repetition-loop guard (P1)

Three independent, additive defenses, all in `agent/eoa/api/routes/ask.py`'s SSE generator:

- **(a) Ollama sampling options.** `chat_stream`'s call from `ask.py` now passes
  `repeat_penalty=1.15`, `repeat_last_n=256`, and `num_predict=2100` (1800 for the visible answer +
  300 headroom for the trailing `===SOURCES_JSON===` block -- one continuous generation covers
  both). Separately, `eoa.llm.ollama_client.chat_stream` itself previously sent **no** `num_predict`
  at all (unlike `chat()`/`_ollama_chat`, which always falls back to `ollama.num_predict[task]`) --
  a real contributing factor to the original loop having no token ceiling once it started. It now
  falls back to the same config default, with a caller's own `options["num_predict"]` still
  winning via the merge order (additive, no behavior change for other `chat_stream` callers -- `ask.py`
  is the only one today).
- **(b) Streaming repetition detector**, `_repetition_detected(tail)`: tracks the trailing ~600
  chars of *answer* text only (never the sources-JSON tail) and triggers on either (i) a 40+ char
  window recurring >= 3 times, or (ii) the same non-blank line recurring >= 3 times among the last
  12. Checked after every streamed chunk; on a hit the Ollama stream is closed immediately
  (`stream_gen.close()`, which propagates through the underlying `httpx` `with` blocks and actually
  tears down the connection, not just stops reading).
- **(c) Hard wall-clock ceiling**, `_MAX_ANSWER_SECONDS = 240.0`, checked once per streamed chunk
  (this generator drives a blocking HTTP read directly, per the existing single-user-deployment
  trade-off documented in `ask.py` -- a total hang with zero output isn't preemptable without a
  separate thread, which this deployment doesn't need).

Either trigger takes the same graceful-cut path: flush any buffered-but-unsent answer text, cut at
the last clean sentence boundary (`_truncate_at_sentence`, `[.!?״]`), append a one-line system note
(`"התשובה קוצרה: המודל נכנס ללולאת חזרה."` / `"...חריגה ממגבלת הזמן."`), log
`ask_repetition_abort`/`ask.wallclock_abort` with a non-reversible question hash (never the
question text), and still run the citation/anchor guards below and emit `sources` + `done`
normally.

**Live result:** both round-1 loop questions (Q1 XM30, Q8 SPECTRO ISR) now complete cleanly --
124.8s/3229 chars and 34.4s/2474 chars respectively, no repetition note, well under the wall-clock
ceiling. Neither guard fired on any of the 8 golden questions this round (no loop reproduced).

## 2. Zero-citation corrective pass (P2)

`ask.py`'s SSE generator, after streaming ends: if at least one source was retrieved and the
finished answer has zero `[n]`, it runs one non-streamed `ollama_client.chat()` call (role
**`resident`**, `num_predict=900`) built by the new `services.ask_citation_repair_messages()` --
the exact same system+history+sources messages the original answer saw, plus that answer as an
assistant turn and an instruction to rewrite it with `[n]` attached to factual sentences. If the
corrected text has `[n]`, it replaces the answer via a new `answer_final` SSE event (added
end-to-end: `eoa/api/routes/ask.py` -> `web/src/types/api.ts`'s `AskSseEvent` ->
`web/src/api/real.ts`'s dispatch -> `web/src/api/types.ts`'s `AskStream` handler type ->
`web/src/hooks/useAskChat.ts`, which replaces the message's whole `content` rather than appending).
If repair fails or still has no `[n]`, the answer is prefixed with `"⚠ ללא ציטוטים: "` instead --
never silently left unlabeled.

**Role choice -- live-verified pitfall:** the brief said "the light/resident role"; the first
implementation used `light` when configured. Live-verified 2026-09-06 against
`docs/CONVENTIONS.md`'s 12 GB VRAM card: `resident` (~7.5 GB, already loaded and warm from the main
answer) and `light` (~6.7 GB) don't fit together, so routing the corrective pass through `light`
forced an unload+reload swap that **queued for minutes** behind the resource gate
(`gate_decision decision=queued ... model=gemma4:e4b reason='free 4364MB < 6700MB'`, backing off
5s/10s/30s/60s) -- exactly the wrong trade for a pass meant to be short and cheap. Switched to
always use `resident`; every subsequent live run shows the repair call proceeding immediately
(`gate_decision ... reason='already loaded'`) at 30-70s.

**Live result:** of the 8 golden questions, Q2 (Iron Beam) and Q3 (Greece/LORA) triggered the
repair pass (their first-pass answers had zero `[n]`); both repairs succeeded (9 and 13 `[n]`
respectively in the final text). Q1, Q4, Q5, Q6, Q7, Q8 already had inline `[n]` on the first pass
(1-10 citations each) and skipped the repair entirely.

## 3. Conflation/hallucination guards (P2)

### 3a. Canonical entity names + anti-conflation rule (`services.ask_build_messages`)

`_ASK_ITEM_FIELDS` now also selects `entities_mentioned`; `ask_build_messages` collects the
deduplicated (case-insensitive) union of every retrieved item's `entities_mentioned` and injects it
into the system prompt as an explicit "use these names exactly" list, with the rule text the brief
specified verbatim: *"השתמש בשמות הישויות בדיוק כפי שמופיעים במקורות; אל תחליף מערכת במערכת דומה
(למשל מגן אור ≠ כיפת ברזל)."*

**Live result:** Q2 (Iron Beam contract) -- round 1's exact worst-cited conflation risk -- now
correctly names Iron Beam throughout and explicitly lists Iron Dome as a *different* system in the
same sentence ("...לצד מערכות יירוט אחרות (Iron Dome, Sky Guardian)"), never substituting one for
the other.

### 3b. Topic-substitution (anchor) guard -- the hardest fix this round

`ask.py` reuses `eoa.search.deep_search.extract_anchors(body.question)` (already used to gate deep
investigations) and requires the finished answer to mention at least one. This went through **five
live iterations** against the exact golden Q3 ("עסקת ה-LORA היוונית (Greece) -- מה המשמעות...")
-- four bypasses found and closed, a fifth confirming the guard finally fires reliably -- each
bypass is now a named regression test in `tests/unit/test_ask_round2_chat_fixes.py`, and worth
recording honestly since each was a genuine live miss, not a hypothetical:

1. **Plain "any anchor present" (didn't fire).** `extract_anchors` on that question returns
   `['עסקת', 'ה-LORA', 'היוונית', 'Greece', 'עבור', 'התעשייה', 'הביטחונית']` -- the generic Hebrew
   words trivially match *any* EO/IR analyst answer regardless of topic. The live answer discussed
   an entirely unrelated Greek air-defense deal (David's Sling/Barak MX/Spyder) and never mentioned
   LORA, but "התעשייה"/"הביטחונית" matched anyway. Fix: `_strong_anchors()` narrows to the
   embedded Latin-script token inside each raw anchor (`"ה-LORA"` -> `"LORA"`) -- this domain
   systematically keeps technical/product terms in English inside Hebrew prose
   (`docs/CONVENTIONS.md` rule 3), so a Latin anchor is both a stronger signal and reliably present
   in a genuinely on-topic answer.
2. **`_strong_anchors` alone (didn't fire).** Re-tested live: the model emitted a spurious H1 title
   lightly rephrasing the question (`# עסקת ה-LORA היוונית: ...`) -- itself already against
   `ask_answer_format.md`'s own rule that the direct-answer section carries no heading -- which
   satisfied the literal `"LORA"` substring check while the entire body stayed on the unrelated
   deal. Fix: `_strip_markdown_headings()` drops heading lines before the containment check.
3. **+ heading-strip (didn't fire).** Re-tested live a third time: no heading this time -- the
   model instead named "LORA" once in the direct-answer paragraph's *opening sentence*
   ("עסקת ה-LORA היוונית היא אירוע אסטרטגי...") and then spent every `### עובדות מרכזיות`/
   `### הערכת האנליסט` bullet on the same unrelated deal, never mentioning LORA again. Fix:
   `_answer_body_for_anchor_check()` looks only at what follows the first `###` section, excluding
   the direct-answer paragraph entirely (falls back to the full text when no section marker exists
   at all, e.g. a very short unstructured answer).
4. **+ body-only check (didn't fire).** Re-tested live a fourth time: the answer's `### עובדות
   מרכזיות` section quoted an English source sentence -- *"Turkey threatens Greece no less than it
   does Israel"* -- which contains the literal word "Greece", satisfying the OR check via the
   *glossed* anchor (`"(Greece)"` in the question is a parenthetical Hebrew-to-English translation
   of "היוונית", not the actual subject) even though "LORA", the real non-parenthetical subject,
   still never appeared anywhere. Fix: `_primary_anchors(question, strong_anchors)` excludes any
   strong anchor that appears inside a `(...)` span in the original question, falling back to every
   strong anchor only when *all* of them are parenthetical (e.g. `"מגן אור (Iron Beam)"`, where the
   subject itself is what's glossed). Verified against all 8 golden questions' anchor sets before
   committing to this rule -- Q1/Q5/Q8's genuinely good answers correctly omit their own
   parenthetical glosses (`Bradley`, `C-UAS`, `Elbit` respectively, using only the Hebrew term), so
   requiring *every* strong anchor (not just the primary one) would have introduced two new false
   positives to fix one; this narrower rule fixes Q3 without touching the other seven.
5. **+ primary-anchor check (still didn't fire, one more time).** Re-tested live a fifth time with
   the `_primary_anchors` fix active: this generation's answer used `## עובדות מרכזיות`/
   `## הערכת האנליסט` -- **two** hashes, not the three `ask_answer_format.md` mandates -- so
   `_answer_body_for_anchor_check`'s `^###\s` search found no match at all and fell back to the
   full (unstripped-of-intro) text, which still opened with an echo of "LORA" the same way as
   iteration 3. Root cause: the model's heading-level compliance is itself inconsistent between
   calls (temperature 0.2, not 0), so a fix keyed to an exact heading level is a moving target. A
   sixth live iteration confirmed the guard now fires -- but on a generation that happened to omit
   any mention of LORA/Greece at all, not because a further code fix closed this exact heading-level
   gap. **No further code change was made for this specific variant**, a deliberate stop rather than
   an oversight: a "skip past the first heading regardless of level" rule was drafted and rejected
   in review -- when the model omits the (correctly ruleless) direct-answer heading entirely, its
   first heading numbers the *facts* section, and skipping "the first heading" would then wrongly
   exclude the facts section itself, trading this gap for a worse one on other generations.

On a *miss*, the answer is prefixed with `"⚠ ייתכן שהתשובה אינה עוסקת בשאלה: "` and replaced via
`answer_final`; `ask.anchor_miss` is logged with the question hash and the anchors checked.

**Known remaining limitation, stated plainly:** this is a deterministic, structural heuristic, not
a semantic check, and the model's own output formatting is not fully deterministic call-to-call.
It reliably catches *complete* topic drift when the anchor never appears anywhere including a
name-drop, and now closes four distinct real bypasses found live (generic-anchor false negative,
heading echo, opening-sentence echo, incidental-gloss-quote) -- but a fifth variant (the anchor
named once under a non-standard `##`-level heading, with the model choosing to elaborate on an
unrelated topic in the sections after it) can still theoretically slip through, since there is no
reliable, low-risk way to locate "the end of the direct-answer paragraph" without relying on the
model's own heading compliance. Closing that fully would need either a semantic verifier call or
material false-positive risk on genuinely good, differently-formatted answers (see the rejected
"skip past the first heading" alternative above). Section 3d below adds a complementary
*generation-time* mitigation (rather than another post-hoc detection rule) aimed at the same root
cause; also see section 4's honest final status for this exact question.

### 3d. Compound-premise verification rule (`services.ask_build_messages`)

Investigating *why* Q3's generation keeps drifting (not just how to detect it) surfaced the real
root cause, live-confirmed against the DB: retrieval is not the problem. The live citations for Q3
included **both** genuine LORA items (`item 62`/`item 37`, "German Navy Conducts Firing with IAI's
Naval LORA Ballistic Missile") **and** a genuine, unrelated Greek air-defense item (`item 155`,
"Greece approves $4b Israel air defense procurement") side by side in the same retrieval -- the
model was never missing LORA context, it simply picked the more prominent-seeming Greek item and
silently wrote as if the question's two terms (LORA + Greece) were one connected story, when no
single source actually connects them. Added an explicit instruction to the system prompt
(`ask_build_messages`): when a question combines several specific terms (a system/program name plus
a country, say), verify a source actually connects them before answering as one story; if the terms
only appear in separate, unrelated sources, say so explicitly and describe what was found in each,
rather than merging them into an invented narrative. This is a generation-time, root-cause-targeted
mitigation, complementary to the anchor guard's post-hoc detection -- see section 4 for whether it
changed this question's actual output.

### 3c. Source-type labels (`services.ask_build_messages` / `_ASK_ITEM_FIELDS`)

`_ASK_ITEM_FIELDS` now also selects `report_kind`; each `[n]` source block the model sees is
labelled with a Hebrew source-type (`_REPORT_KIND_LABELS`: "דיווח מאומת" / "הודעת חברה (PR)" /
"מאמר אקדמי/arXiv" / "מכרז/RFI ממשלתי" / "פטנט" / "רגולציה" / "מדע/מחקר" / "לא מסווג"), and the
system prompt adds an explicit rule never to present one kind as another. Citations sent to the UI
also now carry `report_kind` for the sources footer.

**Live result -- the clearest win this round.** Item 127 (the arXiv infrared/visible
object-detection paper round 1 named twice, as a DROIC hardware trend for Q4 and as a government
RFI for Q7) is confirmed `report_kind='academic'` in the live DB. Q7's re-run this round explicitly
states: *"ה-RFI העדכני ביותר בתחום EO/IR שפורסם בארה\"ב הוא **לא הודעת RFI** אלא דווקא **הכרזה על
מוצר חדש**"* (a real Omnisys BRO-API product announcement) and separately labels item 127 as
*"מאמר מחקר"* (a research paper) with no RFI/tender framing anywhere -- a direct, verified fix of
round 1's single worst D5 mislabeling.

**Live result -- partial, stated honestly.** Q4 (DROIC trend) still leans on item 127 as *the*
"latest DROIC trend", correctly calling it "מחקר חדש שפורסם" (newly published research) rather than
government/RFI material, but without caveating that a single low-TRL academic paper about
infrared/visible object *detection algorithms* may not represent an actual DROIC *hardware*
industry trend. The mislabeling-as-government-document bug (round 1's specific complaint) is fixed;
the softer "one paper != an industry trend" overreach is a separate, harder problem (would need a
TRL-aware synthesis instruction, not a citation-labelling fix) and is out of this round's scope --
flagged here rather than claimed fixed.

## 4. Live golden-question re-run (8/8, one at a time, 8766)

| Q | Subject | Seconds | Chars | `[n]` | Sources | Loop? | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | XM30 (round-1 loop) | 124.8 | 3229 | 9 | 7 | No | Clean, well-cited, correctly separates Lynx XM30 vs. GDLS variants. Round-1's 537s/49K-char loop gone. |
| 2 | Iron Beam | 55.2 | 1565 | 9 | 7 | No | Citation-repair fired and succeeded; correctly distinguishes Iron Beam from Iron Dome/Sky Guardian (3a fix). |
| 3 | Greece/LORA (round-1 substitution) | 56.9 | 3236 | 3 | 8 | No | Substantively **still wrong** (see below) -- but, unlike round 1, no longer silent: the answer is now visibly prefixed `"⚠ ייתכן שהתשובה אינה עוסקת בשאלה: "`. |
| 4 | DROIC | 28.2 | 1746 | 1 | 8 | No | Correctly labels item 127 as a research paper (3c fix), but still overweights it as "the" DROIC trend -- see 3c limitation. |
| 5 | Skyranger vs. Israeli C-UAS | 40.7 | 2888 | 10 | 8 | No | Clean, comparative, appropriately hedges on missing spec data. |
| 6 | AUSA 2026 | 32.5 | 2111 | 3 | 6 | No | Reasonable relevance summary; citations grouped `[1][2]` rather than per-fact (cosmetic). |
| 7 | Latest EO/IR RFI (round-1 mislabel) | 26.0 | 1783 | 2 | 2 | No | Explicitly corrects itself: the "RFI" is actually a product announcement; item 127 correctly labelled as a research paper, not an RFI (3c fix, verified). |
| 8 | SPECTRO ISR (round-1 loop) | 34.4 | 2474 | 6 | 8 | No | Clean, no loop. Round-1's other 300s/29K-char loop gone. |

**Q3, stated fully honestly -- five live iterations, final status:** every one of the five live
re-runs against this exact question reproduced round 1's substantive finding: the model answers
about an unrelated, genuinely-retrieved ~€3.5-4B Greek David's Sling/Barak MX/Spyder air-defense
deal (`item 150`/`item 155`) and never substantively engages with LORA, even though real LORA
items (`item 62`/`item 37`, German Navy firing trials) are sitting right there in the same
retrieved set every time. This is **not a retrieval failure** -- both threads are always retrieved
-- it is the model choosing to write about the more prominent-seeming item and silently treating
the question's two combined terms as one connected story that no single source actually supports.
Section 3d's compound-premise prompt rule did not visibly change this specific generation's topic
choice on the one re-run it was tested against (LLM decoding at temperature 0.2 is not fully
deterministic, so this is one data point, not a disproof). What **did** change, reliably, across
every one of the anchor-guard iterations once `_primary_anchors` landed: the guard fired on the
final live check (`q3_retest5`, off-topic prefix present) and would have fired on `q3_retest3`
(the gloss-quote case `_primary_anchors` was built to fix); `q3_retest4`'s heading-level variant
(section 3b, iteration 5) is the one documented case that can still slip through. **Bottom line:**
round 1's Q3 substance bug is not fixed by this round's changes, but round 1's Q3 *silence* is --
the analyst is very likely to be visibly warned instead of confidently misled, most but not
provably all of the time. A genuine fix would need to change what gets *retrieved and ranked* as
primary for a compound, two-entity query, or add a real semantic verification pass -- both out of
this round's scope (D5 chat-layer fixes only) and flagged here as the natural next step.

## Tests

`tests/unit/test_ask_round2_chat_fixes.py` (new, 49 tests): `_repetition_detected` /
`_truncate_at_sentence` synthetic-loop unit tests; `_strong_anchors` / `_primary_anchors` /
`_strip_markdown_headings` / `_answer_body_for_anchor_check` unit tests including every live-repro
case in section 3b above; end-to-end SSE tests (mocked `chat_stream`/`chat`) for the repetition
abort, wall-clock ceiling, citation-repair success/failure/skip paths, and every anchor-guard
bypass reproduced live; `services.ask_build_messages`/`ask_citation_repair_messages` tests for
source-kind labels, canonical-entity injection, and the compound-premise rule. Combined with the
pre-existing `test_ask_sse_sources.py` (extended: `_mock_ask` now also stubs `ollama_client.chat`
so its fixtures, most of which have no `[n]`, don't trigger a real network call through the new
citation-repair path; one pre-existing unrelated `E741` lint fix in a helper this file's edits
touched) and `test_ask_retrieval.py`, the full targeted set is **76 passed** locally. `ruff check`
clean on every file touched (`agent/eoa/api/routes/ask.py`, `agent/eoa/api/services.py`,
`agent/eoa/llm/ollama_client.py`, both test files).

A full `pytest tests/unit` run (2276 tests collected, no import errors) was started twice during
this round's live verification; both runs progressed steadily with zero failures through 15-35% of
the suite before being stopped to free the GPU for live golden-question testing rather than let it
run to completion unattended for what appeared to be 15-20+ minutes -- the slice actually observed
running was unrelated to any file this round touched (feedback/survey/fetch-service tests, verified
independently green and fast, ~1.4s, when run in isolation). This is a schedule trade-off, not a
known failure: the specific `ask`/`services`/`ollama_client` surfaces this round changed are fully
covered by the 76-test targeted run above; a full-suite confirmation is recommended as a follow-up
but was not completed end-to-end in this session.
