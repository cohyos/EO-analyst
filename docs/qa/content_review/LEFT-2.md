# LEFT-2: two long-failing unit-test classes (pre-existing on 937c59d)

Both classes failed since before the 2026-09-07 content-review round (confirmed failing on
commit `937c59d` too, per the assignment brief). Investigated and closed 2026-09-08.

## 1. `tests/unit/test_discovery_round4.py::TestToolReadUsesL2Arbitration` (2 tests)

**Root cause: stale test fixtures, not a product regression.**

`_tool_read` (`agent/eoa/search/deep_search.py:950`) still calls `screen(..., use_l2=True)` at
line 1005 -- unchanged since the round-4 commit (`7d3ef0b`) that introduced it, confirmed via
`git log -S "use_l2=True"`. So the L2-arbitration wiring these tests exist to guard is intact.

What broke the tests: round 8 (commit `f4e0d3c`, R8-investigations-b) added
`_low_quality_page_reason` -- an interstitial/short-body gate (`_MIN_BODY_CHARS = 400`) that now
runs in `_tool_read` *before* the `screen()` security call these tests exercise. Both tests'
fixture bodies ("hello world", "bad content") are far under 400 chars, so `_tool_read` now
discards them at the new low-quality gate and returns before `screen()` is ever reached --
`captured` (the `fake_screen` kwargs spy) stayed empty, and `inv.security_flagged_pages` stayed
empty, for a reason unrelated to L2 arbitration.

**Fix applied:** `tests/unit/test_discovery_round4.py` -- added a shared `_REAL_BODY_TEXT`
fixture constant (padded past 400 chars, no `_LOW_QUALITY_PAGE_SIGNATURES` phrase) and swapped
both tests' mocked `fetch_remote` bodies to use it, so the fixtures clear the round-8 gate and
reach `screen()` as the tests intend. No production code changed.

## 2. `tests/unit/test_ask_round6_grounding.py::TestAnchorMissDemotedSectionEndToEnd` (was: 1 of 3 failing)

**Root cause: product behaviour improved across rounds 7-13; the round-6 test's assertion no
longer matches the new (more honest) contract.**

Only `test_citation_repair_rewrite_is_reguarded_before_reaching_the_demoted_section` failed. Live
behaviour: the final answer became `"⚠ ייתכן שהתשובה אינה עוסקת בשאלה: המקורות שנשלפו אינם
מזכירים Iron, Beam עבור ההקשר שנשאל — לא ניתן לאשר תשובה ישירה.\n\n"` -- the off-topic caveat
only, no `"### הקשר קרוב (לא התשובה)"` demoted section, though the test still asserted the
heading must be present.

Traced step by step (`ask_route._run_removal_guards` + `ask_grounding.enforce_answer_coherence`
called directly against the test's own fixture text):

1. Considered, then ruled out, the `_rare_tokens`/anchor-splitting hypothesis named in the
   assignment brief: `extract_anchors`/`_primary_anchors`
   (`agent/eoa/api/routes/ask.py:188-225`) splits the question's `"(Iron Beam)"` gloss into two
   independent anchors, `["Iron", "Beam"]`, producing the "Iron, Beam" (comma-joined) caveat
   wording. This is **deliberate, documented, live-verified behaviour** -- `_primary_anchors`'s
   own docstring names this exact `"מגן אור (Iron Beam)"` shape as the case where the subject
   itself is glossed and every strong anchor is kept (round-2/round-3, live-verified against
   `docs/qa/loop/golden_questions.json`). No `_rare_tokens` function exists in the current
   codebase (it does not appear in `ask_grounding.py`); the brief's naming likely refers to this
   `extract_anchors` mechanism. Not a defect, and not the actual cause of the assertion failure
   (confirmed by reproducing the failure with the anchor list held constant).
2. Actual cause: rounds 7-13 added guards that, when a demoted section's content is **wholly**
   fabricated (not just partially), now remove it entirely rather than leaving a stripped-but-
   still-headed section behind:
   - `filter_claim_grounding`'s tightened per-citation scoping (round 6/7) strips the fabricated
     sentence's words but -- in the direct-answer-paragraph shape this test uses -- leaves a bare
     `" [1]."` citation-marker residue behind as the section's one remaining "unit".
   - `enforce_answer_coherence` (round 10, `agent/eoa/api/ask_grounding.py:2730`) treats that
     residue as a dangling leading fragment (`_word_count(" [1].") < _MIN_LEADING_FRAGMENT_WORDS`)
     and drops it.
   - `_drop_empty_headings` (same round, called from within `enforce_answer_coherence` once a unit
     is actually dropped) then removes the now-empty `"### הקשר קרוב (לא התשובה)"` heading itself.
   Net result: a 100%-fabricated demoted section disappears completely, leaving only the honest
   anchor-miss caveat -- arguably a better outcome than round 6's original contract (an empty
   "context" section with nothing under it, or a bare citation-marker fragment).
3. Sanity-checked against the sibling test in the same class
   (`test_demoted_section_conflation_is_scrubbed_even_when_the_original_answer_already_cited`,
   still passing): there the fabricated bullet's guard-removal leaves *no* residual unit at all
   (not even a marker) under either heading, so `enforce_answer_coherence`'s dangling-fragment
   branch never fires and the (also content-less) headings survive untouched. This asymmetry
   between "direct-answer paragraph" and "bullet" residue shapes is a pre-existing, deeper
   emergent interaction between several rounds' guards, out of this task's minimal-fix scope
   (would require touching shared removal-guard internals well beyond `ask_grounding.py`'s
   `filter_claim_grounding`/`enforce_answer_coherence` boundary, with a real risk of destabilising
   the other 20 passing tests in this file and the 421 across the neighbouring `test_ask_*.py`
   suite) and not something either failing test here actually exercises.

**Decision:** product behaviour is right (per the brief's own framing: "a caveat is more honest
than a demoted section"). Updated the test rather than the product code.

**Fix applied:** `tests/unit/test_ask_round6_grounding.py`,
`test_citation_repair_rewrite_is_reguarded_before_reaching_the_demoted_section` --
- Extended the docstring with the contract-update note above (dated, root-caused).
- Replaced `assert "### הקשר קרוב" in final_text` with
  `assert "### הקשר קרוב" not in final_text`.
- Added `assert "Iron" in final_text and "Beam" in final_text` -- the caveat still names the
  missed anchors, so the test still guards that the off-topic reason stays informative even
  though the demoted section itself is gone.
- `assert final_text.startswith(ask_route._OFF_TOPIC_PREFIX)` and `assert "AMPSNG" not in
  final_text` (the actual fabrication-must-never-survive guarantee this test exists to protect)
  are unchanged.

No production code changed for either class.

## Verification

- `tests/unit/test_discovery_round4.py` alone: 37 passed.
- `tests/unit/test_ask_round6_grounding.py` alone: 31 passed.
- `tests/unit/test_discovery_round4.py` together with every neighbouring `test_deep_search_*.py`,
  `test_tenders_scan.py`, `test_conferences.py`: 492 passed.
- `tests/unit/test_ask_round6_grounding.py` together with every neighbouring `test_ask_*.py`
  (round2 through round13, retrieval, sse_sources, investigations_max_length): 421 passed.
- `ruff check` on both edited files: clean.
- Run with `DATABASE_URL` unset (no `runtime/eoa.env` sourced), `test_discovery_round4.py`'s
  unrelated `TestScanTendersEndToEndRescue` class times out against Postgres (pre-existing
  environment requirement, not introduced by this change) -- confirmed resolved by sourcing
  `runtime/eoa.env` per this project's standard test invocation.
