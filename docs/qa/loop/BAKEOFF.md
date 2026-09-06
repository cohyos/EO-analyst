# Bake-off: how much of the D1/D2 gap is the model?

**Question:** `docs/qa/loop/round_1_judge.md` scored the resident model's classification/triage
output (D1) at **40/100** and its summary/so-what output (D2) at **55/100** on the JUDGE
component — despite the DETERMINISTIC/mechanical component for the same two domains already
sitting at 87.5/85.7 by round 2 (`docs/qa/loop/SCORES.md`). In other words: schema validity,
taxonomy validity, Hebrew truncation/gershayim hygiene, and non-empty entities are essentially
solved — but an independent judge reading the actual content (faithfulness to source, correct
domain/level reasoning, real analyst-grade inference, Hebrew fluency, entity completeness) still
finds it weak. `docs/adr/001-model-selection.md` names this directly: "the LLM-quality ceiling of
the resident model DictaLM-3 12B Q4 is now the main limiter." This bake-off measures how much of
that ceiling is actually the *model*, holding the prompts/schemas fixed, and at what cost.

## Methodology

Built as a reusable harness (`agent/eoa/qa/bakeoff.py`, `scripts/bakeoff_golden.py`,
`tests/unit/test_bakeoff.py` — see `docs/MODULES.md`'s "Model bake-off harness" section for the
file-level design), not a one-off script:

1. **Same prompts, same schemas, swapped model.** Every candidate runs through the exact,
   unmodified `classify_item` → `triage_item` → `analyze_item` stage functions
   (`agent/eoa/pipeline/{classify,triage,analyze}.py`) and the exact `ask_build_messages` chat
   builder (`agent/eoa/api/services.py`). `candidate_context()` swaps only *which model answers*
   — an in-process-only `settings().models["resident"]` remap for local Ollama candidates, or a
   `provider=` injection into `chat_structured` for cloud CLI candidates — never a config-file
   edit, never a DB write, never `EOA_PIPELINE=1`.
2. **Golden items, stratified.** `docs/qa/loop/golden_items.json`'s 40 frozen ids, narrowed to a
   run-sized, stratified subset (all `red`, up to 6 `orange`, up to 4 `yellow`, up to 4
   archive/out_of_scope, backfilled to guarantee ≥3 Israel-relevant items) via one read-only
   `SELECT` against the live DB.
3. **Deterministic checks, reused not reimplemented.** D1/D2 scored with the exact same
   `eoa.qa.d1_classify.score_D1` / `d2_summary.score_D2` the live QA loop uses.
4. **Blind rubric judge.** `blind_judge_item` shuffles each item's candidate outputs behind
   anonymous `CANDIDATE_n` labels (no candidate name or model id ever appears in the judge's
   prompt) and scores 0-100 against the Q3 rubric (`docs/QA_CONTINUOUS_LOOP.md` sec 1):
   faithfulness, domain/level correctness, analyst-grade inference, Hebrew quality, entity
   completeness.
5. **Cost, latency, VRAM.** Every candidate here is $0 marginal cost (already-downloaded local
   weights, or an existing `agy`/`claude` CLI subscription — never a paid per-token API key).
   Latency is measured per stage; VRAM is sampled via `nvidia-smi` polling during local candidates.

### Candidates run

<!-- BAKEOFF_TABLE_START -->
_(filled in below once the sweep completes)_
<!-- BAKEOFF_TABLE_END -->

## Methodology note / limitations

- **Scope for this pass.** Time- and GPU-budgeted to a subset of the task brief's full matrix
  (all candidates × 20 items × 4 chat questions): see the exact `--items-limit`/`--candidates`
  invocation recorded below. The harness (`scripts/bakeoff_golden.py --items-limit 20
  --candidates all --questions-limit 4`) is fully capable of the complete sweep; running the
  remaining candidates/items is a mechanical re-run, not new engineering.
- **DB access.** `eoa-postgres` (the Docker container named in `docker-compose.yml`) was stopped
  at the start of this task; the actual live Postgres this run reads from is a native Windows
  `postgres.exe` process already listening on `127.0.0.1:5432` (matching `runtime/eoa.env`'s
  `DATABASE_URL`) — i.e. the Docker Postgres service appears to have been superseded by a
  native-Windows install (consistent with `docs/PLAN_WINDOWS_NATIVE.md`), and the stopped
  container is not this project's actual datastore. No container was started or restarted by
  this task; the harness only ever issued read-only `SELECT`s against the already-running native
  instance.
- **Judge provider.** `agy:gemini-3.1-pro-high`, not `claude:claude-sonnet-5`, specifically
  *because* `claude_sonnet5` is itself a bake-off candidate — grading a candidate with itself (or
  a close sibling) risks self-preference bias. This is a deliberate deviation from the task
  brief's "claude, or agy if claude unavailable" ordering, in the interest of a fairer judge.
- **Chat/D5.** `ask_build_messages` was called with an empty `retrieved` list for candidates other
  than what the live corpus retrieval would supply in production, since this run's purpose is
  measuring each *model's* answer quality/Hebrew fluency/hallucination-avoidance on a genuinely
  hard, under-specified question, not re-testing the retrieval layer (which is unchanged
  regardless of which model answers). Read the D5 latency/completion numbers as "cold, ungrounded"
  — they are a lower bound, not what a user would see in the real, retrieval-backed chat.

## Results

<!-- RESULTS_START -->
_(filled in once the sweep completes)_
<!-- RESULTS_END -->

## Example items (blind judge)

<!-- EXAMPLES_START -->
_(filled in once the sweep completes)_
<!-- EXAMPLES_END -->

## Recommendation

<!-- RECOMMENDATION_START -->
_(filled in once the sweep completes)_
<!-- RECOMMENDATION_END -->
