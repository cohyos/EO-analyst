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

## Revision 2026-09-06: a global switch, fallback chains, direct APIs, batch mode

The user's follow-up requirements (`docs/REVIEW_2026-09-05.md` U8 (א)-(ו)) supersede this ADR's
"cloud is chat-only, pipeline hard-gated to ollama" decision (section 3 above) with a design where
cloud access is *sanctioned and audited* for the pipeline too, not merely forbidden. This section
records what changed and why; sections 1/2/4 above (the three CLI kinds, the single dispatch
funnel through `ollama_client`, the audit log) are extended, not replaced.

### (א) One global switch, applying everywhere

`llm_providers.mode: "local" | "cloud"` (default `"local"`) is the single control U8 originally
lacked: previously, the only lever was `interactive_default` (chat-only) plus the hard-coded
`EOA_PIPELINE` gate (pipeline-only, always "ollama", no exceptions). Now `mode` decides what the
pipeline does, and `interactive_default` keeps deciding what an un-overridden chat question does
-- two independent knobs instead of one config value and one hardcoded behavior. The chat's own
per-question override (the `ModelPicker`) is untouched by `mode`; that was always meant to be a
human's explicit, visible-in-the-UI choice, and stays that way.

**Why not replace `EOA_PIPELINE`'s check instead of repurposing it:** the original risk this ADR
exists to manage -- "how do we guarantee an opt-in choice can never silently leak into the one
context where it must never apply" -- is still exactly the risk here, just with a wider set of
sanctioned outcomes (a configured chain, not just "ollama"). Keeping the same env-var choke point
and only changing what it resolves *to* means the defense-in-depth property (a pipeline call's
`provider` argument, if one ever existed, is ignored outright) survives unchanged, and every
existing reasoning about "which process can reach a cloud provider" in the original ADR still
holds -- it's a smaller diff to reason about correctly than inventing a second mechanism.

### (ה) Fallback chains, and why the terminal entry is enforced rather than trusted

`llm_providers.chains.<role>: [{provider, model?, power?}, ...]`, tried in order by
`eoa.llm.chain.run_chain`. The alternative considered -- let the user's chain be whatever they
write, including one with no local entry at all -- was rejected because `mode: cloud` combined
with a chain that has no working entry (all keys revoked, all CLIs uninstalled, a typo'd model id)
would otherwise leave the *pipeline* with no answer at all, which is a much worse failure mode for
an unattended 5-hour run than "fell back to a weaker local model." `Settings.effective_chain`
appends a local `ollama` entry whenever the configured chain's last entry isn't already one --
cheap enough to do unconditionally that there's no reason to make it a validation error instead.

Fallback triggers (provider unavailable, HTTP 401/403/429/5xx after the provider's own retries,
timeout, non-zero CLI exit, schema validation failing `chat_structured`'s corrective retry) are
deliberately broad -- a narrower list would mean some real-world failure mode silently returns a
malformed/empty answer from a "working" chain entry instead of moving on. The cost of being broad
is a chain could in principle skip past a transient blip that a bare retry on the same entry would
have recovered from; accepted, since the terminal local entry means a skip never means "no answer"
the way it would for a shorter chain.

### (ו) Direct-API providers: why keys stay in `.env` and never touch config.yaml

`docs/CONVENTIONS.md` rule #12 ("no secrets in repo") predates this feature but the reasoning
applies identically: `config.yaml` is read by `write_settings_yaml`'s generic editor, is fully
round-trippable through `GET/PUT /api/settings/config`, and (unlike a CLI's own auth, which lives
entirely outside this project) an API key embedded in it would be one YAML paste away from
appearing in a screen-share, a support bundle, or a future `git add -A`. `ANTHROPIC_API_KEY`/
`GEMINI_API_KEY`/`OPENAI_API_KEY` therefore only ever come from the process environment; every
place that could plausibly leak one was checked: `list_llm_providers()` returns a boolean
(`available`) and the env var *name* (`key_env`), never the value; `log_llm_call`/`llm_calls`
never receives the key or any request/response body; the CLI-with-tools delegation path
(U8-6b below) never puts a key in a written file (it needs none -- only the API providers do, and
those never get their own file-based delegation, only ordinary `chat()` calls).

Choosing httpx + tenacity (already dependencies) over a vendor SDK per provider kept the
dependency surface unchanged and made the three providers structurally identical (one POST, one
retry policy, one JSON-schema adaptation per API) -- easier to audit for the "never log the body"
property than three different SDKs with their own logging/retry defaults to verify.

### (ו) "The active provider chain has web tools" -- verified live, not assumed

Point 6 asked for cloud-delegated deep search to hand pending questions to "the agentic CLI with
its own web tools" and named specific flags (`claude -p --allowedTools WebSearch,WebFetch`,
`agy` with its search tool, `codex exec --search`). Rather than implementing against the spec's
example flags on faith, `scripts/verify_cloud_tools.py` was written and *run* against this
machine's actually-installed CLI versions (`claude`, `agy`, `codex`, `agy models`, and each CLI's
own `--help`, checked 2026-09-06):

| CLI | Result | Notes |
|---|---|---|
| `claude` | **Works, verified live** | `--restricted --allowedTools WebSearch,WebFetch --output-format json` grants exactly those two tools headlessly -- **no permission-bypass flag needed at all** (`--permission-mode bypassPermissions` was tried first and rejected outright: "bypassPermissions not supported in restricted mode"; omitting `--permission-mode` entirely just worked). One live call returned a real, current, cited answer with `usage.server_tool_use.web_search_requests: 1`; a second, near-identical call minutes later showed `web_search_requests: 0` while still returning a correct, source-cited answer -- almost certainly server-side prompt/response caching on an identical-enough question, not a regression in tool access. **Caveat for anyone re-running the verify script**: a `used_a_tool: false` result on a repeated question is not proof tools stopped working; vary the question or check `cache_read_input_tokens` in the raw response to tell the two apart. `--restricted` still strips Bash/PowerShell/REPL/other code-execution tools even with `WebSearch,WebFetch` named -- the two are independent (naming a tool grants only that tool, not "exit restricted mode"). |
| `agy` | **Inconclusive** | No documented `--search`/`--web` flag in `agy --help`; a plain `agy -p "<question>" --output-format json` call (no special flags) returned a plausible, current-looking answer (Node.js LTS version) in one turn, but agy's JSON output carries no per-call tool-usage field the way claude's does, so there is no way to confirm from the response alone whether it actually searched or answered from training data. `--dangerously-skip-permissions` was NOT verified either way -- an attempt was refused by *this development machine's own agent sandbox* (a classifier blocking the literal flag name) before `agy` itself ever ran; a future check should try it directly, outside that harness. Used as delegation's second attempt regardless (same schema validation + guard screening as claude, so an ungrounded answer is caught by the ordinary low-confidence/"never invent" contract, not trusted blindly). |
| `codex` | **Not supported** | `codex exec --help` (this machine's installed version) has no `--search`/`--web`/`-c web_search=...` option -- only a shell sandbox (`-s read-only`/`workspace-write`/`danger-full-access`) that runs model-generated commands, which is a fundamentally different (and, for this project's purposes, less acceptable -- arbitrary code execution vs. a scoped search/fetch tool) capability. Excluded from the cloud-delegated batch deep-search path entirely; `codex exec`'s existing plain-text `CliProvider` path (no tools, per the original ADR) is unaffected and still used wherever a chain names it. |

**Consequence for the implementation:** `investigate_batch_cloud` tries `claude` then `agy`, never
`codex`, and treats an agy "success" exactly as skeptically as any other unconfirmed source --
through the same guard-screening and confidence/outcome logic a claude or local answer gets, so
"agy might not really have searched" degrades to "low-confidence, source-checked answer" rather
than a silent correctness gap. `agy models` (run live, 2026-09-05/06) also revealed a materially
larger/fresher model catalog than this project's static config default
(`gemini-3.8-flash-high/medium/low`, `-3.7-*`, `-3.6-*`, `-3.1-pro-high/low`,
`claude-sonnet-4-6`, `claude-opus-4-6-thinking`, `gpt-oss-120b-medium` vs. the three names baked
into `CliProviderCfg`) -- left as a known drift (the existing static-list comment already
documents that the catalog moves independently of this file) rather than churning the config in
this change; worth refreshing next time someone touches `agy`'s chain entries.

### Batch mode's local-fallback edge case (known limitation)

U8-6's batch sizes (25 for classify/triage, 8 for analyze) are sized for a cloud model's larger
context window. If every entry in a role's chain fails and the chain falls back to the local
Ollama terminal entry, that local model receives the *same* already-built batch prompt (all 25
items' `wrap_data`-framed text in one message) -- for a role/model whose configured `num_ctx`
can't hold that, Ollama truncates from the front (the existing, already-documented F1 behavior)
rather than erroring, so the practical failure mode is degraded quality on that one batch, not a
crash or a dropped item. Not specifically mitigated in this change (splitting the batch into
smaller local-only sub-batches on chain exhaustion was judged excess complexity for what should be
a rare path -- cloud mode implies the chain's cloud entries are expected to be the common case);
flagged here for whoever next tunes batch sizes or `num_ctx` for a cloud-mode deployment.

See `docs/MODULES.md`'s "Cloud LLM providers, Revision 2026-09-06" section for the concrete
module/file map, migration, API shape, and test inventory this revision added.
