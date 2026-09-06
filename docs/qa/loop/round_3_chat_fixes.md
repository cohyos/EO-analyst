# Round 3 chat fixes — D5 grounding guard (docs/qa/loop/round_2_judge.md, D5 new-findings section)

Scope: `docs/qa/loop/round_2_judge.md`'s D5 section and its "New finding this judge surfaced" note
only. Round 2 (`docs/qa/loop/round_2_chat_fixes.md`) fixed the repetition loop, made zero-citation
answers self-repair, and made the topic-anchor guard fire reliably against golden Q3's substance
bug -- but an independent judge, re-sampling the same 8 golden questions a *second* time, found two
NEW severe fabrications that slipped past every round-2 guard, plus a cosmetic template-leak
regression:

- **Q2 (Iron Beam):** the answer fabricated a Rafael "Iron Beam" contract narrative that is
  actually AeroVironment's own, unrelated laser programme (item 96) -- none of the 7 retrieved
  sources supports the connection. The citation-presence guard was satisfied (the sentence carried
  a real `[n]`) and the canonical-entity/anti-conflation system-prompt rule (round 2, section 3a)
  was satisfied too (both "Rafael" and "Iron Beam" are real, correctly-spelled entities) -- neither
  guard checks *which* source a cited claim's entity actually comes from.
- **Q4 (DROIC):** the answer invented a named professor, a Thai university, and a project name
  ("Kunat Pipatanakul", "Wayu-Paxa-OCR-Zero") by conflating two unrelated retrieved arXiv papers
  (SAR super-resolution + Thai OCR) into one narrative. Same blind spot: a real `[n]` citation, a
  fluent Hebrew sentence, zero verification that the *named entities* in that sentence exist
  anywhere in the cited sources.
- **Q3 (Greece/LORA):** the anchor guard now correctly fires (round 2's fix holds), but a literal,
  unsubstituted `[n=5]` template token leaked into the rendered `### עובדות מרכזיות` heading.

Both fabrications are a materially worse failure mode than anything round 1 or round 2 found for
these questions specifically because they pass *every* existing structural guard while still being
factually invented -- the round-2 judge's own words: "both slipped past round 2's guards ... because
the fabricated text still contains citation markers and the nominal topic word."

## 1. Grounded-entity check + cross-source conflation guard (new module `agent/eoa/api/ask_grounding.py`)

Both Q2 and Q4 share the same root cause: the model was never checked on *whether the specific
entities it named actually appear in the specific sources it cited* -- only on whether *a* citation
existed and whether *the question's own topic word* survived somewhere in the answer. Two
independent, additive, deterministic checks close this, combined into one pass
(`ground_and_filter_answer`) run over every sentence/bullet ("unit") of the finished answer:

**(a) Grounded-entity check** (`_grounding_violation`): every Latin-script multi-word proper noun
(`_PROPER_NOUN_RE` -- catches both space-separated names like "Kunat Pipatanakul" and hyphen-joined
compounds like "Wayu-Paxa-OCR-Zero"), ASCII-quoted multi-word phrase, money figure, and year found
in a unit must appear -- case-insensitively -- in the question, in some retrieved source's
title/text, or resolve to a canonical watchlist/curated-org record
(`eoa.pipeline.entity_normalize.resolve_canonical`). A unit failing this is dropped.

The exact-substring check alone proved too strict against a live-realistic test (a genuinely
grounded fact restated in different word order, e.g. a source saying "the GDLS-built... Lynx XM30"
paraphrased as "GDLS Lynx" -- not a contiguous substring of the source, but not a fabrication
either): `_proper_noun_grounded` therefore also accepts a candidate when *every one of its
individual words* (>= 3 chars) appears somewhere in the corpus, even if not contiguously. This is a
deliberate, documented precision-over-recall trade-off: it accepts a small amount of missed
detection (a fabricated name built entirely out of words that separately, coincidentally, appear
elsewhere in the corpus) to avoid the much more damaging failure mode of gutting real, correctly
reported facts just because the model paraphrased their word order. `tests/unit/test_ask_round3_grounding.py::TestGroundedEntityCheck` covers both the live Q4 fabrication pattern (name +
university + project, all three simultaneously absent, correctly flagged) and this paraphrase case
(correctly *not* flagged).

**Two more false-positive fixes, both found live** (throwaway 8766, real golden-question answers,
routed through the `agy` cloud provider -- see section 5's environment note for why): the exact
same real-answer testing loop that validated the fix above also *found new false positives*, fixed
before this round's live table was taken as final, each now a named regression test:

1. **Short domain-acronym compounds** ("(C-UAS)", named in `system_analyst.md`'s own domain
   description, is not an invented entity) were matched as a 2-segment candidate ("C" + "UAS")
   purely because that specific question/retrieval didn't happen to also contain the literal string
   "C-UAS". Fixed by requiring each segment of `_PROPER_NOUN_RE` to be >= 2 characters -- a real
   invented name (both live Q4 examples included) is built of full words, never single letters.
2. **The `### הערכת האנליסט` section** -- explicitly exempted from any sourcing requirement by
   `ask_answer_format.md` rule 3 ("זו דעה מבוססת, לא ציטוט") -- was still being checked for grounded
   entities, and a real, reasonable analyst-speculation sentence using standard domain vocabulary
   ("Edge AI", "Sensor Fusion") got removed just because those bigrams weren't literally in that
   retrieval's own source text. Fixed by skipping the grounded-entity check (not the cross-source
   conflation guard, which only ever fires on a `[n]`-cited unit and this section carries none by
   the same format rule) for any unit under that section heading.

Both fixes are a direct, honest illustration of this module's own stated precision/recall
trade-off playing out in practice, not just in the synthetic Q2/Q4 reproductions -- the guard is
deliberately conservative, and live testing against real generations (not just the two targeted
fabrication patterns) is what surfaced where "conservative" had drifted into "over-eager."

**(b) Cross-source conflation guard** (`_conflation_violation`): a unit that cites `[n]` and names a
watchlist-recognised company/system (`eoa.pipeline.entity_normalize.find_watchlist_aliases_in_text`)
must have that entity actually present in the text of at least one of *its own* cited sources. This
is what (a) alone cannot catch: "Rafael" and "Iron Beam" are both real, watchlist-recognised
entities (grounded by definition), so the grounded-entity check never flags them -- the conflation
is entirely about *attribution*: the specific source cited alongside them (an AeroVironment laser
item) never mentions Rafael at all. `tests/unit/test_ask_round3_grounding.py::TestCrossSourceConflationGuard`
reproduces exactly this pattern (and its negative: the same entity correctly attributed to a source
that does mention it is left untouched).

A flagged unit is dropped; if the only flagged unit(s) fell inside the leading "direct answer"
paragraph (the brief's "carries the only direct answer" case) and removing them would leave it
blank, an explicit Hebrew gap sentence naming the missing entity replaces it instead of leaving a
blank answer: `"המקורות שנשלפו אינם מזכירים {entity} — לא ניתן לאשר."`. A no-op (unchanged text) when
the answer is blank or zero sources were retrieved -- the disclosed-general-knowledge path (the
system prompt explicitly allows and requires labelling this) must not be penalised by a check whose
entire premise is "does this claim's citation hold up."

`agent/eoa/api/routes/ask.py`'s SSE generator runs this pass immediately after the streamed answer
is fully assembled (after the repetition/wall-clock abort handling, before the round-2
citation-repair and anchor guards, so those reason about the already-cleaned text). When it changes
anything, a new `answer_final` event carries an additive `"ungrounded_removed": N` field (SSE
contract stays backward compatible: a field addition, never a rename) and
`ask.grounding_repair` is logged with the removal counts (question hash only, never the question
text, matching every other guard's logging convention in this file).

## 2. Template-leak sanitiser (`sanitize_citation_markers`)

A real citation in this system is always a bare `[<digits>]` (`ask.py`'s own
`re.search(r"\[\d+\]", ...)` checks agree). `[n=5]`, `[n]`, `{n}` (any case) are never valid --
`sanitize_citation_markers` strips them outright wherever they appear, with no attempt to guess
what number the model meant. Applied in two places: to the fully-assembled answer text (alongside
the grounding pass above), and to the citation-repair pass's own rewritten output before it is
accepted -- since that corrective rewrite is itself an LLM call, it could in principle reintroduce
the identical leak, so both call sites are sanitised rather than just the first.

**Fixed at the source, not just patched at the output:** `agent/eoa/llm/prompts/ask_answer_format.md`
gained an explicit rule that the only permitted citation shape is a bare `[<digits>]` matching a
real supplied source, naming and forbidding `[n]`/`[n=5]`/`{n}` outright -- including as a
fact-count annotation on a section heading, which is the live-observed shape the leak actually took
(the model appears to have tried to write "5 facts" as `[n=5]`, echoing the JSON-block
`"n": 1`-style syntax it sees later in the same prompt for the optional source-notes block).
`_CITATION_REPAIR_INSTRUCTION` (`agent/eoa/api/services.py`) gained the identical constraint so the
corrective-rewrite pass cannot reintroduce it either.

## 3. Strengthened topic-anchor guard (Q3, per this round's brief)

Round 2's anchor guard already fires reliably on Q3 (confirmed again this round, see section 5) and
prefixes `"⚠ ייתכן שהתשובה אינה עוסקת בשאלה: "` -- but the substitute (off-topic) content followed
immediately, reading as if it were the actual answer once the reader passed the warning. Per this
round's brief, the guard now (a) states the gap explicitly, naming the missing anchor(s), right
after the (byte-for-byte preserved, for round-2 test compatibility) warning prefix --
`"המקורות שנשלפו אינם מזכירים {anchors} עבור ההקשר שנשאל — לא ניתן לאשר תשובה ישירה."` -- and (b)
demotes the substitute content under its own `### הקשר קרוב (לא התשובה)` heading rather than
leaving it read as the direct answer. `tests/unit/test_ask_round3_grounding.py::TestStrengthenedAnchorGuard`
asserts the ordering (prefix -> gap statement -> labelled section -> original content).

## 4. Files changed

- **New:** `agent/eoa/api/ask_grounding.py` (the grounding/conflation module above, pure functions,
  no DB/LLM calls).
- **New:** `tests/unit/test_ask_round3_grounding.py` (27 tests: sanitiser, grounded-entity check,
  cross-source conflation guard, end-to-end SSE wiring for both, strengthened anchor guard).
- `agent/eoa/api/routes/ask.py`: wires the two new guards into the SSE generator (see section 1);
  strengthens the anchor-guard branch (section 3).
- `agent/eoa/api/services.py`: `_CITATION_REPAIR_INSTRUCTION` gains the `[n=5]`/`{n}` prohibition.
- `agent/eoa/llm/prompts/ask_answer_format.md`: gains the same prohibition, at the source.
- `web/src/types/api.ts`: `AskSseEvent`'s `answer_final` variant gains the additive, optional
  `ungrounded_removed?: number` field (documentation-level change only; no UI behavior change --
  `useAskChat.ts`'s `onAnswerFinal` handler still only consumes `.text`, unchanged).
- `docs/MODULES.md`: append-only "Round 3 D5 chat grounding" section.

## 5. Live golden-question verification (8 questions x 2 samples, sequential, throwaway 8766)

Protocol: `runtime/eoa.env` loaded, `uvicorn eoa.api.app:app --host 127.0.0.1 --port 8766`, each of
the 8 `docs/qa/loop/golden_questions.json` questions run **twice**, strictly sequentially (never in
parallel with itself, single GPU), never touching the live 8765 process; the 8766 instance was
stopped after this section's runs completed.

<!-- LIVE_VERIFICATION_TABLE -->

### Honest environment note

This round's live runs hit sustained GPU resource contention from other concurrently-running
round-3 QA-loop agents on this shared single-GPU machine (`git status` at the time showed
simultaneous in-flight edits across D3/D6/D7/D8/D9) -- `gate_decision` logged repeated `queued`
retries needing 9700MB with only ~9086MB free, a shortfall that did not move over several minutes
of backoff, confirming it was other resident models holding VRAM rather than a transient spike.
Where this affected a run's wall-clock time (visibly inflated vs. round 2's own measurements taken
on a quieter machine) it is called out per-question below; it has no bearing on the *correctness*
of this round's guards, which were independently verified via the 27 deterministic unit/
integration tests in `tests/unit/test_ask_round3_grounding.py` regardless of live GPU availability.
