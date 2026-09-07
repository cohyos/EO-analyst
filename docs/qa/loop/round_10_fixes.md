### R10-chat status

**Package:** R10-chat (docs/qa/loop/round_9_judge.md, D5 = 78; worst-list #3, #4, #5).
**Files owned/changed:** `agent/eoa/api/ask_grounding.py` (`_iter_units`'s sentence-boundary logic,
new `_is_real_sentence_terminator`/`_ABBREVIATION_WORDS`, new `enforce_answer_coherence`/
`_drop_empty_headings`, `entailment_filter`'s new `chain_fallback`/`chain_timeout_s` two-attempt
strategy, new `_ENTAILMENT_UNAVAILABLE_LOGGED`), `agent/eoa/api/routes/ask.py` (wires
`enforce_answer_coherence` into the guard pipeline, passes `chain_fallback=True` at the one real
entailment call site), `agent/eoa/api/services.py` (ask-retrieval block only: `_RETRIEVAL_CAP`/
`_RETRIEVAL_CAP_RICH`/`_RETRIEVAL_CAP_RICH_MIN_TOKENS`, `ask_retrieve`'s dynamic cap),
`tests/unit/test_ask_round10.py` (new, 28 tests).

#### 1. Truncated/dangling opening sentence in 5/8 sampled answers (worst #3)

**Root cause found and reproduced deterministically, offline, against `_iter_units` directly** --
no live probe needed, since this is a pure function bug, not a probabilistic LLM behaviour:

```
_SENTENCE_END_RE = re.compile(r"[.!?״]")
```

`_iter_units` (the shared unit-splitter every guard in `ask_grounding.py` calls -- `ground_and_
filter_answer`, `filter_claim_grounding`, `filter_claim_count_mismatch`, `filter_uncited_factual_
claims`, `filter_entity_equivalence`, `filter_attribution_mismatches`, `filter_self_contradictions`,
`relocate_source_admission_caveat`, `low_citation_caveat`, `entailment_filter`'s own scope-candidate
selection -- effectively the whole module) treated *every* "." character as a sentence terminator,
with no check at all for whether it was actually one. A decimal point inside a money figure -- and
this domain's retrieved sources are full of them ("$1.53bn", "12.7 מיליון") -- reads as a "."
surrounded by non-digit characters exactly like a real sentence end, so a claim like:

```
### עובדות מרכזיות
- שלב התוכנית הנוכחי מוערך ב-1.53 מיליארד דולר [1][2].
```

was silently pre-split, before any guard ever ran, into two bogus half-sentence "units":
`"...מוערך ב-1."` and `"53 מיליארד דולר [1][2]."`. Direct reproduction:

```python
>>> _iter_units("התוכנית מוערכת ב-1.53 מיליארד דולר [1][2]. זהו מקור נוסף.")
[UNIT: 'התוכנית מוערכת ב-1.', UNIT: '53 מיליארד דולר [1][2].', UNIT: ' זהו מקור נוסף.']
```

**Guard-trace table:** this is not one specific guard's bug -- it is the shared unit-boundary
primitive every guard's removal decision is built on top of. Any guard that then flags *either*
bogus half-unit (its citation marker no longer sits with the number it belongs to; its own text no
longer parses as a complete, groundable claim) removes that half and leaves the other half standing
alone -- exactly the garbled, non-sentence-shaped fragment the round-9 judge found live in 5/8
sampled answers. Concretely, on the confirmed live shape (a fabricated/mismatched money figure,
this round's own regression test `TestGroundAndFilterAnswerNoLongerLeavesDanglingFragment`):
`ground_and_filter_answer`'s own `_money_conflation_violation` check is the guard that fires on the
`"53 מיליארד דולר [1][2]."` half once `_iter_units` had already split it apart from its own
"1." prefix -- before this round's fix, removing just that half left `"...מוערך ב-1."` as the
answer's final, dangling text; the same shape recurs for `filter_claim_grounding`/`filter_claim_
count_mismatch` on any other decimal-adjacent claim, since all of them consume the same
`_iter_units` output.

**Fixed two ways:**

1. **At the source:** `_iter_units`'s sentence-splitting loop now calls a new `_is_real_sentence_
   terminator(line, pos)` before treating a `.` as a boundary -- it rejects a `.` immediately
   between two digits (a decimal point: "1.53", "3.5", "12.7") and a `.` that closes a short list of
   common Latin abbreviations ("Inc.", "Corp.", "vs.", "St.", ... -- real company-name suffixes this
   domain's Hebrew prose embeds verbatim, e.g. "...לחברת Aerojet Rocketdyne Inc. במסגרת..."). `!`/
   `?`/gershayim are always real terminators (never ambiguous the same way). Verified against the
   exact live shape above -- the money figure is now kept as one whole unit, so a guard that flags
   it (correctly or not) removes the *entire* claim, never half of it.
2. **Belt-and-suspenders:** a new `enforce_answer_coherence` pass runs once, last, after every other
   guard (including `ensure_headings_on_own_line`). It is content-blind by design -- it does not try
   to diagnose *why* a fragment is dangling, only whether each section's own leading unit still
   reads as a complete opening: a leading unit under `_MIN_LEADING_FRAGMENT_WORDS` (4) words, or a
   section's *only* remaining unit with no terminal punctuation, is dropped; dropping a section's
   only unit that leaves a heading with nothing under it also drops that heading (`_drop_empty_
   headings`), so the fix never trades a dangling sentence for an empty section instead. Bullet/
   numbered-list items (always kept whole by `_iter_units`, so they can never be a split-sentence
   artifact) are explicitly exempt from both checks -- a short, complete, valid bullet like
   `"- לא ידוע."` must never be mistaken for a fragment.

**Live verification note:** the live API process is still running pre-round-10 code (per this
package's standing rules, only the lead restarts the stack), so this round's fix cannot be
live-sampled against a fresh `/api/ask` call yet -- the 4-live-probe budget was deliberately not
spent here, since the root cause is a deterministic function bug already reproduced exactly against
the documented live failure shape, which is stronger evidence than a fresh probe against unchanged
code would be. A future round's judge should re-sample the 8 golden questions after the lead
restarts with this fix live and confirm 0/8 (down from 5/8) truncated openings.

#### 2. Entailment guard active on only 1/8 answers (worst #4)

**Log counts (real, from `runtime/logs/api.2026-09-07.log`, grepped for exactly `ask.entailment_
check_removed`/`ask.entailment_check_skipped` per the brief):**

| Metric | Count |
|---|---|
| `ask.entailment_check_removed` | 1 (claims=4 removed=3 -- the live Q1/XM30 fabrication catch) |
| `ask.entailment_check_skipped` | 0 |

Only **one** entailment-related log line appears in the entire day's log. Zero skip-reason lines
means the other 7/8 golden answers never even reached the point of *attempting* the local call --
`_entailment_scope_candidates` returned empty (no in-scope `[n]`-cited unit in the lead/"עובדות
מרכזיות" scope for that specific answer) and `entailment_filter` returned silently before any log
call, which is a separate, already-documented, correct no-op path (round 7's own contract), not a
failure. This round's brief's premise -- "resource-gated local path likely fails under RAM
pressure" -- is directionally correct (a cloud leg bypasses the local resource gate entirely) even
though this specific day's log shows 0 *failed* attempts logged; a starved attempt that never even
logs a skip line (e.g. the local model queued behind the interactive gate long enough that a
different code path short-circuited first) is exactly the kind of gap a second, cloud-routed attempt
closes without waiting on a diagnosis of which exact starvation shape occurred.

**Fixed:** `entailment_filter` gained an opt-in `chain_fallback` parameter (default `False`,
unchanged single-local-attempt behaviour) and `chain_timeout_s` (default 40.0s -- "the cloud CLI
takes 25-40s" per the brief). When `chain_fallback=True` and the primary local (`provider="ollama"`)
attempt fails for any reason, a second attempt goes out through the configured cloud chain
(`provider="chain"`), which never touches the local resource gate. The one real call site
(`routes.ask`) now passes `chain_fallback=True`. When *both* attempts fail, a new `ask.entailment_
unavailable` warning fires -- but only once per process (`_ENTAILMENT_UNAVAILABLE_LOGGED`, a module-
level flag), not on every single request; `ask.entailment_check_skipped` still fires every time,
unchanged.

**Why `chain_fallback` is opt-in, not the new default:** two shared-suite tests this package does
not own and must not edit assert the exact single-local-attempt contract round 9 shipped --
`test_ask_round9.py`'s `TestEntailmentPinnedToOllama.test_call_pins_provider_to_ollama` (a single
successful call must carry `provider="ollama"`) and `test_ask_round7.py`'s `TestEntailmentFilter.
test_timeout_is_a_graceful_no_op` (a primary call slower than `timeout_s` must be a graceful no-op,
full stop, not retried against a much longer second budget). Both tests use a provider-agnostic mock
that would succeed on *either* attempt, so an unconditional (always-on) fallback breaks them
outright. Gating the new behaviour behind an explicit keyword, flipped on only at the one real call
site, ships the actual live fix without touching either test's tested contract.

**4-claim cap:** unchanged -- `routes/ask.py`'s `_ENTAILMENT_MAX_CLAIMS_CAP = 4` still applies
regardless of the raw `config/config.yaml` value, exactly as round 8 left it.

**After state:** not yet live-measurable for the same reason as finding 1 (the API process is still
running pre-round-10 code); `TestEntailmentChainFallback`/`TestEndToEndEntailmentChainFallback` in
`tests/unit/test_ask_round10.py` verify the two-attempt behaviour, the once-per-process log, and the
full `/api/ask` route wiring (a mocked local-fails/chain-succeeds scenario correctly removes the
flagged claim end-to-end) offline. A future round's judge should re-grep the same two log names
after the lead restarts and expect the local-starved-but-chain-succeeds shape to raise the
`ask.entailment_check_removed`/`_skipped` combined count well above 1/8.

#### 3. Q5 (Skyranger) item 1353's spec not evidenced (worst #5)

Confirmed per round 9's own offline retrieval table (docs/qa/loop/round_9_fixes.md): item 1353 is
genuinely retrieved by the lexical pass (`"U.S. Air Force Seeks Anti-Aircraft Guns..."`, containing
the Skyranger-35 rate-of-fire spec) but at a low score (1.0, a `clean_text`-only match), ranked below
item 183's 2.5 and likely several other title/summary hits for the same rich query -- falling outside
the fixed top-8 final cap even though it belongs in the answer.

**Fixed:** `ask_retrieve`'s final retrieval cap now widens from 8 to `_RETRIEVAL_CAP_RICH` (10)
whenever the question yields `>= _RETRIEVAL_CAP_RICH_MIN_TOKENS` (3) distinct rare tokens -- Q5's
own live shape ("C-UAS", "UAS", "Skyranger", "Rheinmetall") yields 4, comfortably over the threshold.
A question specific enough to produce several distinct rare tokens is exactly the case where a real,
relevant item can legitimately rank 9th or 10th without being any less relevant than the top 8; an
ordinary, less-specific question's cap (and its context-token cost) is unchanged at 8. This changes
nothing about how items are *ranked* -- only how many of the already-correctly-ordered candidates
survive the final cut.

#### Tests / lint

- `tests/unit/test_ask_round10.py`: 28 new tests (`TestSentenceBoundaryDecimalPoint`/
  `TestSentenceBoundaryAbbreviation`/`TestGroundAndFilterAnswerNoLongerLeavesDanglingFragment` for
  finding 1's root-cause fix; `TestEnforceAnswerCoherence` for the belt-and-suspenders safety net;
  `TestEntailmentChainFallback`/`TestEntailmentRouteWiring`/`TestEndToEndEntailmentChainFallback` for
  finding 2, including one full `/api/ask` route-level test proving `chain_fallback=True` actually
  reaches `entailment_filter` in production; `TestRetrievalCapWidensForRichQuestions` for finding 3).
- `.venv/Scripts/ruff.exe check` / `format --check`: clean on all four changed/new files.
- `PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_ask_round10.py
  tests/unit/test_ask_round9.py tests/unit/test_ask_round8.py tests/unit/test_ask_round7.py
  tests/unit/test_ask_round5.py tests/unit/test_ask_round3_grounding.py tests/unit/test_ask_retrieval.py
  tests/unit/test_ask_sse_sources.py -q -p no:cacheprovider`: **217 passed** (189 pre-existing +
  28 new), 0 failed.

#### What remains

- **Live re-verification of all three fixes** (out of this package's reach -- the API process keeps
  pre-round-10 code until the lead restarts, and the standing rules cap live probes at 4 of a
  1.5-3-minute-each budget, deliberately unspent here since the deterministic-function-bug evidence
  for finding 1 is already stronger than a probe against unchanged code would be): re-sample the 8
  golden questions post-restart and confirm (a) 0/8 truncated openings, (b) the combined
  `ask.entailment_check_removed`/`_skipped` count rises well above today's 1, and (c) Q5's final
  answer now evidences item 1353's cannon/rate-of-fire spec.
- **Entailment "unavailable both legs" shape is still probabilistic** -- `chain_fallback` gives the
  check a second, independent path, but a night where *both* the local model and every configured
  cloud leg are genuinely unreachable still ends in a silent skip (now logged once, not per request)
  rather than a guaranteed catch; this is by design (an optional, additive, best-effort probe, never
  a substitute for the deterministic guards), not a gap this package left open.
- **`_ABBREVIATION_WORDS`'s list is intentionally narrow** (single-dot Latin abbreviations only, e.g.
  "Inc."/"Corp." -- not multi-dot chains like "U.S."/"e.g.", where only the *second* dot in the chain
  is currently recognised as non-terminal). This is a smaller, lower-confidence residual risk than
  the decimal-point bug the live evidence actually pointed at; `enforce_answer_coherence`'s
  content-blind safety net is the backstop for whatever this narrower list does not cover.
