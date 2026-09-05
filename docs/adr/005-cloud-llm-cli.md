# ADR-005: Cloud LLM access for the interactive analyst, via CLI subprocess, gated off the pipeline

**Status:** accepted (2026-09-05)

## Context

`docs/CONVENTIONS.md`'s hard rule #1 is "Ollama access only via `eoa.llm.ollama_client`" and rule
#6 is "Config, not code" -- both written for a fully offline, zero-paid-API, Western-open-weight
pipeline (ADR-001). That rule protects the night pipeline: unattended, unsupervised, running
against fetched OSINT content that is explicitly untrusted (`wrap_data`, `DATA_GUARD_SYSTEM`).
There is no reason to relax it there.

The interactive surfaces are a different situation. U8 (`docs/REVIEW_2026-09-05.md`) asks for the
user to be able to pick, per question and as a default, whether "שאל את האנליסט" answers from the
local resident model or from a cloud model -- because sometimes the local 12B model's answer
quality on a hard question isn't good enough, and the user already has three CLIs installed and
authenticated on this machine (`~/.claude/gemini-channel-brief.md`'s field notes: `agy` for
Gemini via the Antigravity CLI since the old `gemini` CLI's Google-account auth path died
2026-06-18; `claude`; `codex`) at effectively zero marginal cost. Calling a cloud API directly
would mean holding an API key in this project (`docs/CONVENTIONS.md` rule #12: no secrets in
repo, no API keys exist in this project at all) -- but a CLI subprocess needs no key here at all;
the CLI holds its own auth outside this project entirely.

The risk this ADR has to manage is not "should the analyst ever see a cloud model" -- the user
asked for exactly that -- it's "how do we guarantee this opt-in choice can never silently leak
into the one context where it must never apply": an autonomous 5-hour night run, unattended,
against a firehose of untrusted fetched content, where nobody is present to notice a cloud call
happened, notice a cost, or notice a change in output that a hallucination-prone or
prompt-injected cloud response might cause.

## Decision

### 1. Three CLI kinds, one subprocess-based `Provider`

`agent/eoa/llm/providers/cli.py`'s `CliProvider(kind, model)`, `kind` in `agy`/`claude`/`codex`.
Each `chat()` call is exactly one `subprocess.run(timeout=llm_providers.timeout_s)`:

| kind | invocation | prompt delivery | notes |
|---|---|---|---|
| `agy` | `agy -p "<prompt>" --output-format json [--model <m>]` | argv element | no stdin support; reliable to ~32KB per the field notes |
| `claude` | `claude -p --output-format json --restricted [--model <m>]` | stdin | `--restricted` strips the tool-use built-ins, so a headless call can only ever return text |
| `codex` | `codex exec -s read-only --json -o <tmpfile> [-m <m>]` | stdin | final answer read from `-o`, not parsed out of the noisier `--json` NDJSON stream |

Tool permissions are effectively denied in every case above (headless has nothing to approve
against, or is explicitly sandboxed/restricted) -- intentional: this project only ever wants text
back from these CLIs, never side effects on disk or in another service.

Structured output (`json_schema`) is an explicit "return ONLY JSON matching this schema"
instruction appended to the flattened prompt -- no CLI-native schema flag is used, so the same
validate-and-one-corrective-retry contract `chat_structured` already had for Ollama applies
unchanged to a cloud provider too (the retry is `chat_structured`'s loop calling `chat()` again;
`CliProvider` itself is stateless, one call in, one answer out).

### 2. Dispatch lives entirely inside `ollama_client.chat`/`chat_structured`/`chat_stream`

No other module gained a new way to reach an LLM. The three public functions everything already
calls gained one optional `provider: str | None` parameter each
(`"ollama"` | `"agy[:<model>]"` | `"claude[:<model>]"` | `"codex[:<model>]"`); a non-"ollama"
resolution returns early, before the resource gate, from a small dispatch block at the top of
`chat()` (`chat_structured`/`chat_stream` funnel through it too). Every existing call site that
does not pass `provider` is completely unaffected -- `provider=None` resolves to
`llm_providers.interactive_default`, which itself defaults to `"ollama"`.

### 3. The pipeline gate: an env flag set once, checked once, at the choke point

This is the part the risk in Context is actually about. `eoa.orchestrator.jobs` -- the module
that both the cron-scheduled night pipeline (`build_scheduler`'s jobs) and every job the API
enqueues onto the same queue (`POST /api/run`, `POST /api/items/{id}/investigate`) run inside --
sets `os.environ.setdefault("EOA_PIPELINE", "1")` at import time. `ollama_client._resolve_provider`
checks that env var *first*, ahead of both the explicit `provider` argument and the configured
default, and forces `"ollama"` whenever it is set. This means:

- The night pipeline can never use a cloud provider, full stop, regardless of what
  `llm_providers.interactive_default` is set to at the time it runs.
- A user-triggered "run now" or "investigate" also can't -- **known limitation, accepted for this
  change**: both are queued jobs, executed by the same orchestrator/worker process as the
  automated pipeline, so the same gate that must protect the automated case also catches these
  manual-but-queued ones. Giving them cloud access would need a `provider` field threaded through
  the job payload and into every `deep_search`/`analyze` call site, with the *scheduler's own*
  enqueues still hard-forced to "ollama" regardless -- judged out of scope here. Only the chat
  endpoint (`POST /api/ask`, served synchronously inside the separate API/uvicorn process, never
  through the job queue) gets the picker in this change.
- Alternatives considered and rejected: passing `provider="ollama"` explicitly at every job
  enqueue site is more call sites to keep in sync and silently wrong the moment one is missed;
  a settings-level "pipeline mode" flag checked by the caller has the same problem one level up.
  A single `os.environ` check inside the one function everything already funnels through is the
  smallest number of places this can go wrong.

### 4. Visible, auditable, and killable

- Every cloud call is logged to `llm_calls` (migration `0008`: provider, model, prompt size in
  characters, duration -- never the prompt or response text) via `eoa.memory.relational.
  log_llm_call`, called from `ollama_client._log_cloud_call` right after the subprocess returns,
  wrapped so a logging failure can never break the actual answer.
- `llm_providers.allow_cloud` (default `true`, since the user asked for this feature) is a
  config-level kill switch checked before any CLI dispatch; `false` makes every cloud provider
  raise `ProviderUnavailable` immediately, and `GET /api/llm/providers` omits the cloud entries
  from the list entirely (not just marks them unavailable) so the Settings "מודלים" card and the
  chat's `ModelPicker` both reflect it.
- Every assistant chat message carries a `provider`/`model` badge (`{"type": "meta", ...}` SSE
  event, computed by `resolve_provider_info` before the call starts) -- "מקומי" vs. "ענן · <model>"
  -- so cloud use is never invisible to the person reading the answer.
- The `ModelPicker` cloud options carry a tooltip ("הטקסט של השיחה יישלח לשירות ענן חיצוני") and
  the last choice is remembered per-browser (`localStorage`), not silently defaulted.

## Consequences

**Gained:** the interactive analyst can use a materially stronger model on a hard question, at
effectively zero marginal cost (existing subscriptions/CLI auth), with no API key ever entering
this project and no change at all to the offline, zero-paid-API guarantee for the actual pipeline.

**Given up / accepted:**
- Manual "investigate" and "run now" don't get cloud access in this change (see 3. above) --
  flagged as follow-up work, not silently dropped.
- A cloud CLI's own auth/session health is outside this project's control; `is_available()` only
  checks the binary is on `PATH`, so an expired login surfaces as a `CliProviderError` from the
  first real call rather than proactively in the picker.
- No batching, streaming, or context-caching of these CLIs -- each call is one fresh, independent
  subprocess (fine for a single-user interactive chat; would need revisiting for volume use).
- The flattened-prompt format sent to each CLI is plain "System: ...\n\nUser: ...\n\nAssistant:
  ..." text, not each CLI's own native message/session API -- simplest thing that works given all
  three CLIs are being driven identically through one abstraction; a CLI's own multi-turn session
  features (`--continue`, `--conversation`, etc.) are intentionally not used, since this project
  manages conversation history itself (`AskRequest.history`) and wants no state living in an
  external CLI's session store between calls.

See also `docs/MODULES.md`'s "Cloud LLM providers via CLI (U8, docs/adr/005-cloud-llm-cli.md)"
section for the concrete module/file map, the exact live-verification commands and output, and
the test inventory.
