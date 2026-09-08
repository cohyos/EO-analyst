# Cloud tool-calling for deep-search ReAct turns, and a codex-leg dossier run (CLOUD-TOOLS)

Date: 2026-09-09
Scope: the missing piece `agent/eoa/llm/chain.py`'s own docstring named -- text-protocol tool
calling for CLI legs, so `eoa.search.deep_search.investigate()`'s ReAct rounds (search/read/finish)
can actually run on a cloud CLI leg instead of always falling back to the local Ollama model the
moment `tools` are supplied; verification that `codex exec` works headlessly on this machine; a
per-run `llm_leg` override threaded through the product-dossier pipeline and its API/UI; and the
fourth live SPECTRO XR dossier, run on `codex:gpt-6-astra`.

Per this task's file ownership: `agent/eoa/llm/chain.py`, `agent/eoa/llm/providers/cli.py`,
`agent/eoa/search/deep_search.py` (ReAct dispatch only), `agent/eoa/dossier/plan.py` + `report.py`
(llm-leg override plumbing), `agent/eoa/orchestrator/jobs.py`'s `run_product_dossier`, the dossier
parts of `agent/eoa/api/services.py` + `routes/dossiers.py`, `web/src/components/dossiers/
NewDossierForm.tsx` + one new API type field, `config/config.yaml`'s `llm_providers.cli.codex`
models, and tests. Two adjacent files (`agent/eoa/llm/ollama_client.py`, `agent/eoa/dossier/
extract.py`) were touched with small, additive-only changes because the feature is structurally
impossible without them (the `chat()`/`chat_structured()` dispatch functions live there, and the
extraction call the task explicitly asks to route through the leg lives there) -- every change to
either file is backward compatible (a new optional parameter, default `None`, unused by any
existing call site).

**Concurrent work note:** this round ran while another in-flight session was actively adding a
"PD-vocab" spec/performance vocabulary feature to several of the same dossier files (`agent/eoa/
dossier/extract.py`, `report.py`, `corpus.py`, `agent/eoa/api/services.py`, `routes/dossiers.py`,
several `web/src/` files). Those changes landed on disk mid-session, additively, and did not
conflict with anything in this round's diff (confirmed by re-reading each touched file after every
external change notification and re-running the full scoped test suite). One pre-existing test,
`tests/unit/test_product_dossier_extract.py::test_spec_value_number_grounded_is_kept`, is currently
red because of that other session's vocabulary-key demotion logic (`dossier.field_dropped
field=specifications.key reason=missing_key_demoted_to_other`) -- unrelated to this round's diff
(never touches `_ground_spec_row`/vocabulary matching) and left for that lane to fix. That other
session also committed several times mid-round (`git log`, commits up to `7220b1d`), sweeping the
whole working tree each time -- `agent/eoa/config.py` and a batch of `web/src/` files this round
also touched (`api/real.ts`, `types/api.ts`, the i18n dictionaries, `mocks/data/dossiers.ts`) ended
up committed as part of that lane's own commits rather than staying uncommitted; this round's other
files (`agent/eoa/llm/*`, `agent/eoa/dossier/plan.py`/`report.py`, `agent/eoa/orchestrator/jobs.py`,
`agent/eoa/api/services.py`/`routes/dossiers.py`, `config/config.yaml`, `NewDossierForm.tsx`, every
test file) remain as uncommitted working-tree changes -- left for the lead per this project's "only
commit when explicitly asked" rule.

## 1. Text-protocol tool calling for CLI legs (`agent/eoa/llm/providers/cli.py`)

`eoa.llm.chain.run_chain` already had the *plumbing* to route a tool-calling turn to any leg
reporting `supports_tools = True` (round 7, 2026-09-07) -- what it never had was a leg that
actually reported it. `CliProvider` now does:

- `CliProvider.supports_tools` is a property, `True` whenever `llm_providers.cli_text_tools`
  (new config flag, `config/config.yaml` + `agent/eoa/config.py`'s `LlmProvidersCfg`, default
  `true`) is true -- an escape hatch, no code change needed, if a CLI's real-world reliability on
  the protocol turns out to be poor for a specific deployment.
- `CliProvider.chat(..., tools=...)` renders the tool list into the prompt (name, description,
  JSON args schema, one line per tool) with a strict output contract appended:
  ```
  {"tool": "<tool_name>", "args": {...}}     -- to call a tool
  {"final": {...}}                            -- to call the "finish" tool (args go directly under "final")
  ```
  and instructs the model to reply with exactly that one JSON object, nothing else. The reply is
  parsed tolerantly (`_parse_tool_reply`): a fenced ` ```json ` block is stripped first; failing a
  direct `json.loads`, the first `{...}` substring in the reply is tried (a CLI that prepends a
  stray word or two despite the instruction is still salvaged). `args` is validated against the
  tool's own JSON Schema `required` list (`_validate_tool_args` -- presence only, no deep type
  checking; this project carries no `jsonschema` dependency and the downstream tool
  implementations already validate/coerce their own arguments, e.g. `InvestigationOut.
  model_validate` for `finish`).
- A malformed/invalid reply gets **exactly one** repair prompt ("the previous reply did not match
  the protocol (`<reason>`); reply with only the JSON object as specified above"); if the repair
  also fails to parse, `CliProviderError` is raised -- picked up by `eoa.llm.chain.run_chain`'s
  existing `FALLBACK_EXCEPTIONS` handling, so the turn simply falls through to the next chain
  entry exactly like a bad HTTP response or a non-zero CLI exit already does. No new fallback
  mechanism was needed.
- A successful parse is adapted into `ProviderResult(tool_calls=[{"function": {"name": ...,
  "arguments": {...}}}], content="")` -- **the exact shape Ollama's native tool-calling API
  produces** -- so `eoa.search.deep_search._act` (the ReAct loop that reads `res.tool_calls`)
  needed **zero changes** to consume a CLI-served tool call.
- Every successful turn logs `llm_chain_text_tools` (`provider`, `model`, `tool`) -- confirmed
  live below.
- Mechanically: `chat()`'s single-subprocess-call body was factored into `_run_once` (unchanged
  behavior for the plain-text and JSON-schema paths) so the new `_chat_with_tools` path (up to two
  subprocess calls: the turn itself, plus one repair) reuses exactly the same argv/stdin/parse/
  cleanup mechanics rather than duplicating them.

`eoa.llm.chain.run_chain` itself needed **no changes** -- it already checked
`getattr(provider, "supports_tools", False)` before handing a leg a tool-calling turn (round 7);
that check now resolves `True` for a CLI leg. One adjacent fix: `eoa.llm.ollama_client.
_dispatch_chain`'s "cloud legs do not accept tools" warning log was stale after this change (it
unconditionally called every non-ollama chain entry "skipped" whenever a tool-calling turn ran) --
now it actually checks each entry's `supports_tools` before logging it as skipped, so the log line
stays accurate for a mixed chain (e.g. a CLI leg that handles the turn, an API leg after it that
still can't).

Tests: `tests/unit/test_cli_text_tools.py` (new, 22 tests) -- tool-list rendering, arg validation,
reply parsing (plain, fenced, stray-prose-wrapped, malformed, unknown tool, missing required arg),
`CliProvider.supports_tools` under the config flag (on/off/missing-attr-defaults-true), and
`CliProvider.chat(..., tools=...)` end to end per CLI kind (agy/claude/codex, `subprocess.run`
mocked) including the one-repair-then-succeed and one-repair-then-raise paths.
`tests/unit/test_chain_tool_turns.py` (extended, +3 tests, `TestRunChainWithTextToolsCliLeg`) --
a REAL `CliProvider` (only `subprocess.run` mocked, not `_build_provider`) routed through
`run_chain`: a tool turn served entirely by a CLI leg before the local terminal entry ever runs, a
malformed CLI reply falling back to local, and `cli_text_tools: false` skipping the CLI leg
entirely for a tool turn.

## 2. Codex CLI: verified headless, live

`codex exec --help` (this machine, `codex-cli 0.153.2`) confirmed the ADR's assumed invocation
shape still holds: `codex exec [OPTIONS] [PROMPT]`, `-s read-only|workspace-write|danger-full-
access`, `-c key=value` generic config override, `-m <model>`, `--json` (NDJSON event stream),
`-o/--output-last-message <file>`, `--skip-git-repo-check`. `~/.codex/config.toml`'s `model =
"gpt-6-astra"` is this machine's actual default (not one of the three static placeholder ids
previously in `_STATIC_MODELS`/`config.yaml`) -- confirmed live both via the implicit default and
`-m gpt-6-astra` explicitly:

```
$ echo "What is 2+2? Answer with just the number, one word." \
  | codex exec -s read-only --json -o /tmp/out --skip-git-repo-check
...
{"type":"item.completed","item":{"id":"item_5","type":"agent_message","text":"4"}}
{"type":"turn.completed","usage":{"input_tokens":23443,"cached_input_tokens":13056,...,"output_tokens":5}}
$ cat /tmp/out
4
```

`config/config.yaml`'s `llm_providers.cli.codex.models` now lists `[default, gpt-6-astra]` (was
`[default]`) -- `"default"` still means "whatever `~/.codex/config.toml`'s `model =` is", and
`gpt-6-astra` is now also selectable explicitly. A manual dry run of the text-tools protocol itself
(before wiring it into `CliProvider`, to de-risk the design) confirmed codex replies with exactly
the requested single JSON object when given the tool list + contract:

```
$ codex exec -s read-only --json -o /tmp/out --skip-git-repo-check -m gpt-6-astra < prompt_with_tool_protocol.txt
$ cat /tmp/out
{"final":{"outcome":"success","answer_he":"בירת צרפת היא פריז.","confidence":1,"sources":[]}}
```

`codex exec`'s read-only sandbox, non-interactive `--json` mode, and `-o` output-file capture were
already exactly how `eoa.llm.providers.cli.CliProvider` runs codex per ADR-005 -- nothing new
needed there; the 360s `llm_providers.timeout_s` (`config/config.yaml`) already bounds every call.
One environmental note worth flagging for whoever tunes this next: every `codex exec` call on this
machine carries roughly 20-23k input tokens of overhead from this operator's own OMC/skills
configuration being loaded into the codex session (`Skill descriptions were shortened to fit the
skills context budget` -- visible in every raw `--json` stream), which is project-external
(`~/.codex/`, `~/.agents/`) and adds real latency (each codex call below took 8-27s) but not
correctness risk -- the final answer/tool-call text itself is unaffected.

## 3. Dossier `llm_leg` override

`POST /api/dossiers` and `POST /api/dossiers/{key}/rerun` accept an optional `llm_leg` field
(`"codex:<model>"` / `"claude:<model>"` / `"agy:<model>"` / `"local"`/`null`, the last two meaning
"use the configured chain, unchanged"). The job payload carries it through unmodified; `eoa.
orchestrator.jobs.run_product_dossier` forwards it into `eoa.dossier.report.build_product_dossier`,
which forwards it into:

- `eoa.dossier.plan.run_plan` -> every topic's own `eoa.search.deep_search.investigate(...,
  llm_leg=...)` -> `_act()`'s ReAct tool-calling turns (`eoa.llm.chain.
  build_chain_with_leg_override("investigator", llm_leg)`, one entry prepended ahead of the
  `investigator` role's normally-configured chain -- never replacing it, so a temporarily
  unavailable leg still degrades to the existing chain instead of leaving a topic with nothing).
- `eoa.dossier.extract.extract_dossier`'s structured-extraction call (same
  `build_chain_with_leg_override`, `resident` role by default).

`eoa.llm.chain.parse_leg`/`build_chain_with_leg_override` (new) parse the `"<provider>[:<model>]
[@<power>]"` string into a `ChainEntryCfg`, mirroring the `model@power` split `eoa.llm.
ollama_client._dispatch_explicit_provider` already used for the interactive chat's own per-question
override. `eoa.llm.ollama_client.chat`/`chat_structured` each gained an additive `chain_override`
parameter (default `None`, meaning "resolve `effective_chain(role)` exactly as before") --
`_dispatch_chain` and `_chat_structured_chain` use it in place of the role's configured chain when
given.

The leg actually used is persisted into `product_dossiers.data.meta.llm_leg` (`"local"` when no
override was given, including every run made before this field existed) -- added at the dict level
after `dossier.model_dump()` in `eoa.dossier.report._persist`, not as a schema field (`
ProductDossierOut` is the frozen, out-of-ownership schema). `eoa.api.services._dossier_run_card`
surfaces it as `llm_leg` on every run-history row (`GET /api/dossiers/{key}`'s `dossiers` array and
the list endpoint's `latest`), defaulting `"local"` when `data.meta` is absent.

`web/src/components/dossiers/NewDossierForm.tsx` gained a third select, "מודל" (`dossiers.form.
modelLabel`), options: ברירת מחדל / Codex / Claude Sonnet / Gemini (agy) -- empty string (default)
means no override, sent as `llm_leg: null`. `web/src/types/api.ts`'s `DossierCreateBody` gained
`llm_leg?: string | null`, and `DossierRunRef` gained `llm_leg: string` (surfaced in the run
history the detail page already renders per run). Mock fixtures (`web/src/mocks/data/dossiers.ts`)
and existing page tests (`DossiersPage.test.tsx`, `DossierDetailPage.test.tsx`) updated for the new
required field; `npx tsc --noEmit` and `npm run build` both clean.

Tests: `tests/unit/test_llm_chain.py` (+8, `TestParseLeg`/`TestBuildChainWithLegOverride`),
`tests/unit/test_deep_search_budget.py` (+2, `TestActLlmLegOverride` -- `_act` builds and forwards
`chain_override` only when `llm_leg` is given), `tests/unit/test_product_dossier_plan.py` (+2,
`run_plan` forwards `llm_leg` to every topic), `tests/unit/test_product_dossier_services.py` (+5,
enqueue/rerun payload + `_dossier_run_card`'s `data.meta.llm_leg` read), `tests/unit/
test_product_dossier_api.py` (+2, the request field reaches the service call; the file's four
pre-existing tests needed their mocked-lambda arity widened for the new positional argument --
fixed, not a regression in behavior).

## 4. Bug found and fixed live: `chain_override` was silently dropped inside `_chat_structured_chain`

The first live run (section 6) exposed a real defect: every one of the dossier's 9 research
topics genuinely used codex for their ReAct tool-calling turns (confirmed by `llm_chain_text_tools`
log lines throughout), but the final structured-extraction call -- the one `eoa.dossier.extract.
extract_dossier` makes with `llm_leg="codex:gpt-6-astra"` -- was actually served by **claude**, not
codex. No fallback warning was logged either, which was the tell that something was silently wrong
rather than codex having failed and correctly falling back.

Root cause, in `agent/eoa/llm/ollama_client.py`: inside the pipeline/worker process, `chat()`
**ignores its own `provider` argument entirely** by design (ADR-005's defense-in-depth -- only the
configured/overridden *chain* ever applies there, never an ad-hoc provider string, so a pipeline
call's `provider` argument can never leak a cloud choice around the audited chain mechanism).
`_chat_structured_chain`'s per-chain-entry loop, however, was built entirely around passing
`provider=_provider_string(entry)` to `_structured_once` -> `chat()` for each entry -- a mechanism
that only works OUTSIDE the pipeline process (where `chat()`'s `else` branch does resolve
`provider` explicitly). Inside the pipeline process, every iteration's nested `chat()` call
therefore silently ignored `provider` and fell back to `settings().llm_providers.effective_chain
(role)` -- the SAME plain default chain, every single time, regardless of which `entry` the outer
loop thought it was trying. Since that nested call's own `_dispatch_chain`/`run_chain` already
retries through the whole default chain internally (with its own guaranteed-safe local-ollama
floor), it always resolved successfully on the very first outer-loop iteration -- which is why this
was never previously exercised as broken: for the *plain* `mode: cloud` case (no `llm_leg`
override), the outer loop's `chain` parameter already equals `effective_chain(role)`, so getting the
"wrong" (but identical) chain on iteration 0 produces the *same correct answer* as intended, purely
by coincidence. The moment `chain[0]` differs from `effective_chain(role)`'s own first entry (i.e.
the moment `llm_leg` prepends anything), the coincidence breaks and the override is silently
dropped -- exactly what happened live.

Fix: `_structured_once` gained a `chain_override` parameter, forwarded into its own `chat()` call.
`_chat_structured_chain` now passes `chain_override=[entry]` (a single-entry chain) for each
iteration, pinning that one call to exactly that provider rather than re-deriving the default chain
inside the nested call; `_guard_hebrew_truncation`'s own corrective-retry call (a second, later
`_structured_once` call against whatever chain produced the validated result) also now receives
`chain_override` from `chat_structured`'s top-level `chain`, for the same reason.

**Verified live** with a standalone check (not a full dossier rerun -- `scratchpad/
verify_extract_leg.py`, `EOA_PIPELINE=1`, `chain_override = build_chain_with_leg_override
("resident", "codex:gpt-6-astra")` against a tiny one-field schema):
```
chain_override: [('codex', 'gpt-6-astra'), ('claude', 'claude-sonnet-5'), ('agy', 'gemini-3.1-pro-high'), ('ollama', None)]
{"provider": "codex", "model": "gpt-6-astra", "prompt_chars": 352, "duration_ms": 7581, "event": "cli_provider_call", ...}
RESULT: answer='42'
```
`cli_provider_call`'s `provider: codex` confirms the fix: the exact same call shape that silently
fell back to claude in the live dossier run now genuinely reaches codex first, as designed. The
existing `tests/unit/test_hebrew_truncation_guard.py` suite (29 tests, exercises `_structured_once`/
`_guard_hebrew_truncation`) still passes unchanged after this fix.

## 5. Test/lint sweep

`ruff check` on every touched file: clean.
`PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_chain_tool_turns.py tests/unit/
test_deep_search_budget.py tests/unit/test_deep_search_anchors.py tests/unit/
test_deep_search_answer_format.py tests/unit/test_deep_search_blocked_round5.py tests/unit/
test_deep_search_cloud_batch.py tests/unit/test_deep_search_mcp_tools.py tests/unit/
test_deep_search_outcomes.py tests/unit/test_deep_search_provenance_links.py tests/unit/
test_deep_search_reconcile_round4.py tests/unit/test_deep_search_round7.py tests/unit/
test_deep_search_round8.py tests/unit/test_deep_search_round9.py tests/unit/test_product_dossier_api.py
tests/unit/test_product_dossier_corpus.py tests/unit/test_product_dossier_diff.py tests/unit/
test_product_dossier_extract.py tests/unit/test_product_dossier_plan.py tests/unit/
test_product_dossier_report.py tests/unit/test_product_dossier_schema.py tests/unit/
test_product_dossier_services.py tests/unit/test_llm_chain.py tests/unit/test_llm_providers.py
tests/unit/test_cli_text_tools.py tests/unit/test_cli_mcp_config.py tests/unit/
test_llm_settings_api.py tests/unit/test_ollama_client_provider_dispatch.py tests/unit/
test_hebrew_truncation_guard.py tests/unit/test_jobs_product_dossier.py -q`:
**621 passed, 0 failed** (final sweep, after the section 4 fix and the section below's test-
isolation fix). Along the way: the section 4 bug was caught BY this sweep (`test_ollama_client_
provider_dispatch.py::TestChatStructuredProviderThreading::test_provider_passed_through_to_chat`
failed once `test_jobs_product_dossier.py` -- new this round -- was added to the same run, because
importing `eoa.orchestrator.jobs` sets `os.environ.setdefault("EOA_PIPELINE", "1")` at import time
(ADR-005) and pytest collects/imports every specified file before running any test, so that side
effect leaked into an unrelated, earlier-running test that assumes `EOA_PIPELINE` is unset; fixed by
having `test_jobs_product_dossier.py` record whether the flag was already set before its own import
of `jobs` and undo the `setdefault` if it wasn't (this file's own tests never depend on
`EOA_PIPELINE`). Also fixed, not left red: `tests/unit/test_deep_search_round8.py`'s `fake_act` mock
needed the new `llm_leg` keyword argument added to its signature, and `tests/unit/
test_product_dossier_api.py`'s four mocked-service lambdas needed their arity widened for the new
`llm_leg` positional argument `routes/dossiers.py` now passes. The one previously-red, unrelated
concurrent-session test (`test_spec_value_number_grounded_is_kept`) is green again in this final
sweep -- fixed by that other lane in the meantime, not by anything in this round's diff.
`web`: `npx tsc --noEmit` clean, `npx vitest run src/pages/DossiersPage.test.tsx src/pages/
DossierDetailPage.test.tsx` (23 tests, grown from 16 by the concurrent PD-vocab-ui lane's own
additions) clean, `npm run build` clean.

## 6. Live run #4 -- SPECTRO XR on `codex:gpt-6-astra`

Run in-process (the API/orchestrator run old code until the lead restarts, per the task brief),
`EOA_PIPELINE=1 PYTHONUTF8=1 PYTHONPATH=agent .venv/Scripts/python.exe scratchpad/run_dossier4.py`
calling `eoa.dossier.report.build_product_dossier("SPECTRO XR", "Elbit Systems", ["Spectro",
"SPECTRO XR", "ספקטרו", "Spectro XR"], product_line="targeting_pods", llm_leg="codex:gpt-6-astra")`
-- the same call shape `eoa.orchestrator.jobs.run_product_dossier` makes from a real enqueued job.
This run predates the section 4 fix (it was what exposed the bug); the extraction call therefore
fell back to claude, documented honestly below rather than rerun (the fix is separately verified
live in section 4, and every one of the 9 research topics' tool-calling turns -- the actual point
of this task -- is unaffected by that bug and ran on codex as intended).

**Result:** `product_dossiers.id=4`, `reports.id=195`, outcome `found`, confidence **0.63** (run 3:
0.68). Wall time **2072.0 s (34.5 min)** total -- 1771.6 s (29.5 min) across the 9 research topics,
~300 s for structured extraction (two attempts: the Hebrew-truncation guard's one corrective retry
fired, see below) plus render/persist.

**Per-topic wall time and provider (from the live log):**

| topic | seconds | sources found | tool turns served by |
|---|---|---|---|
| specifications | 196.0 | 1 | codex |
| versions | 193.1 | 2 | codex |
| performance | 209.0 | 2 | codex |
| maturity | 178.7 | 2 | codex |
| deals | 345.1 | 2 | codex |
| pricing | 202.5 | 3 | codex |
| partnerships | 173.0 | 1 | codex |
| competitors | 158.8 | 2 | codex |
| regulatory | 115.4 | 2 | codex |

**Which provider actually served the tool turns:** every single one. Counting `llm_chain_text_tools`
log lines across the whole run: **42 tool-calling turns, all 42 served by `codex:gpt-6-astra`** (33
`read` + 9 `finish`, exactly one `finish` per topic) -- zero fallback to claude/agy for any ReAct
turn. The only calls claude/agy served were the *other*, never-overridden pipeline stages this
round's scope deliberately left alone (query planning via the `light` role's own chain -- 6 agy
calls -- and the relevance-gate/reconciliation checks via the `resident` role's own chain -- 26
claude calls, 2 of which were the extraction call itself and its Hebrew-truncation retry, both
affected by the section 4 bug).

**Quality vs. run 3** (`product_dossiers.id=3`, `2026-09-08`, pre-dating this round and PD-vocab):

| field | run 3 | run 4 (this round) |
|---|---|---|
| outcome | found | found |
| confidence | 0.68 | 0.63 |
| sources | 18 | 15 |
| specifications | 5 | 15 |
| performance | 2 | 2 |
| variants/versions | (not recorded) | 1 |
| deals | 3 | 3 |
| pricing | 3 | 2 |
| patents | 8 (all later shown ungrounded, PD-fix-3) | **0** |
| partnerships | 0 | 0 |
| competitors | 0 | 0 |
| what_changed_he | 12 (pre-PD-fix-3, since corrected to 7 offline) | 8 |

Read: the **patents drop from 8 to 0 is a quality improvement, not a regression** -- PD-fix-3
(2026-09-08, same day) added a hard patent-relevance gate (`corpus.patent_relevance_he`: an empty
`assignees` list is an absolute drop, no title/abstract fallback) specifically because run 3's 8
patent rows were confirmed, via manual DB inspection, to be generic keyword hits with no real
assignee link to Elbit/SPECTRO XR; run 4 correctly found zero patents that pass that gate rather
than reintroducing ungrounded ones. Specifications going from 5 to 15 rows reflects the concurrent
PD-vocab-extract lane's keyed-vocabulary extraction (a different, unrelated feature that landed
mid-session) rather than anything in this round's own diff -- flagged here only so the count jump
isn't misread as this round's own doing. The confidence dip (0.68 -> 0.63) and pricing/what_changed
counts are within the normal run-to-run variance this kind of live web research already showed
across runs 1-3 (0.74 -> 0.78 -> 0.68); nothing here points at codex specifically producing a worse
result than the local/claude-led chain did on runs 1-3.

**Files:** `docs/qa/content_review/CLOUD-TOOLS.md` (this file), rendered dossier at
`output/reports/dossier_elbit-systems-spectro-xr_2026-09-09.{docx,md,html}`.

**Restart needed:** yes, for this to work through the real UI/API -- `agent/eoa/api/routes/
dossiers.py`, `agent/eoa/api/services.py`, `agent/eoa/orchestrator/jobs.py`, `agent/eoa/dossier/
plan.py`/`report.py`/`extract.py`, `agent/eoa/llm/chain.py`/`ollama_client.py`/`providers/cli.py`,
and `agent/eoa/search/deep_search.py` are all still running as the OLD code in the live API/worker
processes until the lead restarts them; the `web` build (`npm run build`, already run) IS live
immediately per this project's own convention (no server restart needed for UI-only changes). Until
restarted, `POST /api/dossiers`/`rerun` will accept an `llm_leg` field in the request body (FastAPI
silently ignores unknown fields on a Pydantic model only if the route itself doesn't declare it --
here it WOULD be silently dropped by the old `DossierCreateRequest`/`DossierRerunRequest` classes,
which don't have the field yet) and no tool-calling turn will route to a CLI leg at all (the old
`CliProvider` has no `tools` parameter, `chain.py`/`run_chain`'s existing `supports_tools` check
already skips a leg lacking that attribute gracefully, so cloud-mode dossier/deep-search runs keep
working exactly as before, just without ever reaching a CLI leg for a tool-calling turn, matching
the state before this round).
