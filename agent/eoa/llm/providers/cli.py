"""Cloud LLM access via a CLI already installed and authenticated on this machine (U8).

Three kinds, each a thin subprocess wrapper -- no HTTP client, no API key ever touches this
project (``docs/CONVENTIONS.md`` rule #12: no secrets in repo; these CLIs hold their own auth):

* ``agy``    -- Gemini via the Antigravity CLI (``agy -p "<prompt>" --output-format json``).
* ``claude`` -- Claude Code CLI (``claude -p --output-format json --restricted``, prompt on stdin;
  ``--restricted`` strips the tool-use built-ins so a headless call can only ever return text).
* ``codex``  -- Codex CLi (``codex exec -s read-only --json -o <tmpfile>``, prompt on stdin; the
  final agent message is read back from ``-o`` rather than parsed out of the NDJSON stream).

All three are run with the prompt flattened from the chat ``messages`` (system + turns,
DATA-framing already embedded by the caller -- nothing here re-wraps or re-validates it) and,
for a structured (``json_schema``) request, an explicit "return ONLY JSON matching this schema"
instruction appended -- the schema *validation* and corrective retry live in
``eoa.llm.ollama_client.chat_structured`` exactly as they do for Ollama, by calling ``chat()``
again; this class only ever makes one subprocess call per ``chat()`` invocation.

Subprocess mechanics: ``subprocess.run(..., timeout=...)`` with UTF-8 text I/O and
``CREATE_NO_WINDOW`` on Windows (no flashing console). Tool permissions are effectively denied
in every case above (headless mode has nothing to approve against, or is explicitly sandboxed)
-- that is intentional: this project only ever wants text back, never side effects on disk.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog

from eoa.config import settings
from eoa.errors import CliProviderError, ProviderUnavailable
from eoa.execution import checkpoint, timeout_seconds
from eoa.llm.providers.base import ProviderResult, strip_code_fences
from eoa.processes import run_process

log = structlog.get_logger(__name__)

_STATIC_MODELS: dict[str, list[str]] = {
    # `agy models` (verified live, 2026-09-05) -- the id column, not the display name; the
    # catalog moves under the CLI's own updates, so this list (and config.yaml's) may drift
    # from what `agy models` reports later. `--model <id>` rejects anything else with a
    # "not recognized" error that lists the current catalog, which is how this was caught.
    "agy": ["gemini-3.8-flash-medium", "gemini-3.8-flash-high", "gemini-3.1-pro-high"],
    "claude": ["claude-opus-5-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
    "codex": ["default"],
}

_JSON_INSTRUCTION = (
    "\n\n--- STRUCTURED OUTPUT REQUIRED ---\n"
    "Return ONLY valid JSON matching this JSON Schema. No prose, no markdown code fences, "
    "no explanation before or after the JSON:\n{schema}"
)

# --------------------------------------------------------------------------------------------
# Cloud tools (2026-09-09): text-protocol tool calling for CLI legs.
#
# `eoa.llm.chain.run_chain` skipped every CLI leg for a tool-calling turn (the deep-search ReAct
# loop's search/read/finish tools) because no CLI provider accepted a caller-supplied tools
# schema -- every tool-calling turn therefore ran on the local Ollama leg even in `mode: cloud`,
# defeating the point of the cloud chain for `investigate()`'s own rounds. This section renders
# the tool list into the prompt with a strict output contract (one JSON object, either
# `{"tool": "<name>", "args": {...}}` or `{"final": {...}}` for the "finish" tool) and parses the
# reply back into the same `tool_calls` shape Ollama's native tool-calling API produces
# (`[{"function": {"name": ..., "arguments": {...}}}]`) -- `eoa.search.deep_search._act` reads
# only that shape and needs no change at all. A malformed/unparseable reply gets exactly one
# repair prompt ("reply with only the JSON object"); if that also fails, `CliProviderError` is
# raised so `eoa.llm.chain.run_chain`'s existing fallback machinery moves on to the next leg --
# no new fallback mechanism needed, this is the same `FALLBACK_EXCEPTIONS` path a bad HTTP
# response or a non-zero CLI exit already takes.
# --------------------------------------------------------------------------------------------

_TOOL_PROTOCOL_INSTRUCTION = (
    "\n\n--- TOOL CALLING PROTOCOL ---\n"
    "You have access to the following tools. To use one, reply with EXACTLY ONE JSON object and "
    "nothing else -- no prose, no markdown code fences, no explanation before or after it -- in "
    "one of these two forms:\n"
    '  {{"tool": "<tool_name>", "args": {{...}}}}   -- to call a tool\n'
    '  {{"final": {{...}}}}                         -- to call the "finish" tool (its args go '
    'directly under "final", not wrapped in a nested "args")\n'
    "Available tools:\n{tool_list}\n"
    "Reply with the JSON object only."
)

_TOOL_REPAIR_INSTRUCTION = (
    "\n\nAssistant: {prior_reply}\n\n"
    "User: התשובה הקודמת אינה תואמת לפרוטוקול הכלים ({reason}). הגב אך ורק עם אובייקט JSON יחיד "
    'כמפורט לעיל -- {{"tool": "<name>", "args": {{...}}}} או {{"final": {{...}}}} -- בלי שום טקסט '
    "נוסף, בלי markdown, בלי הסבר."
)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _render_tool_list(tools: list[dict[str, Any]]) -> str:
    lines = []
    for t in tools:
        fn = t.get("function") or {}
        name = fn.get("name", "?")
        desc = fn.get("description", "")
        params = fn.get("parameters") or {}
        lines.append(f"- {name}: {desc}\n  args schema: {json.dumps(params, ensure_ascii=False)}")
    return "\n".join(lines)


def _merge_usage(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Sum matching numeric fields of two usage dicts (a corrective-repair call's usage stacked
    onto the original call's, not replacing it -- see `CliProvider._chat_with_tools`). A key
    present in only one dict, or whose value isn't numeric in both, is taken from whichever dict
    has it (preferring ``second`` so a corrected/more complete field still wins).

    F26 follow-up (SOL-REVIEW-2026-09-24): defensive against a non-dict ``first``/``second`` --
    `_parse_agy`/`_parse_claude` already sanitize a malformed ``usage`` field to ``{}`` at the
    parse boundary, so this should never actually receive one in practice, but a bare `.items()`
    call on a non-dict would otherwise raise `AttributeError` outside every error boundary."""
    out = dict(first) if isinstance(first, dict) else {}
    for key, value in (second or {}).items() if isinstance(second, dict) else ():
        prior = out.get(key)
        if isinstance(value, int | float) and isinstance(prior, int | float):
            out[key] = prior + value
        else:
            out[key] = value
    return out


#: Usage counters downstream code reads as numbers (`eoa.llm.chain`, `eoa.llm.ollama_client`).
_USAGE_COUNT_KEYS = frozenset({"input_tokens", "output_tokens", "prompt_tokens", "eval_tokens"})


def _validate_usage(kind: str, usage: dict[str, Any]) -> dict[str, Any]:
    """Every present usage value must be a non-negative number (``None``/missing is fine -- not
    every CLI reports every field). F26 follow-up (SOL-REVIEW2-2026-09-24): the parse functions
    below already sanitize a wrong-shaped ``usage`` *container* to ``{}``, but a malformed
    individual value inside an otherwise-dict ``usage`` (a string, a negative number) passed
    straight through into ``ProviderResult.usage`` unvalidated -- it only surfaced later, outside
    every provider boundary, when ``eoa.llm.chain.run_chain`` ran its own
    ``int(result.usage.get(...) or 0)`` conversion and raised bare. Validating here, inside
    ``CliProvider.chat()``'s own try/except, turns a bad value into ``CliProviderError`` so the
    chain falls through to the next leg instead.

    Only the token counters downstream code converts with ``int(...)`` are validated
    (:data:`_USAGE_COUNT_KEYS`). 2026-09-26: claude CLI 2.1.280 added a nested
    ``output_tokens_details: {"thinking_tokens": N}`` breakdown (and Anthropic usage has always
    carried ``service_tier`` text and a ``cache_creation`` object); validating EVERY key rejected
    each Claude response as malformed, so every Claude leg failed and the chain fell through to
    Gemini/Ollama for the whole night of 26.9. Other keys are informational and pass through."""
    for key, value in usage.items():
        if key not in _USAGE_COUNT_KEYS or value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{kind} CLI usage[{key!r}] is a {type(value).__name__}, not a number: {value!r}")
        # F26 (SOL-REVIEW3-2026-09-24): `json.loads` happily parses the bare `NaN`/`Infinity`/
        # `-Infinity` literals some CLIs emit into a real `float`, which passed the isinstance
        # check above and then sailed through unrejected -- `nan < 0` and `inf < 0` are both
        # `False`, so the negative check below never caught them either. Reject nonfinite values
        # explicitly, same `ValueError` the negative check already raises, so it takes the same
        # caught-by-`chat()` path into `CliProviderError` instead of reaching `ProviderResult`.
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{kind} CLI usage[{key!r}] is not finite: {value!r}")
        if value < 0:
            raise ValueError(f"{kind} CLI usage[{key!r}] is negative: {value!r}")
    return usage


def _find_tool_spec(tools: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for t in tools:
        fn = t.get("function") or {}
        if fn.get("name") == name:
            return fn
    return None


def _validate_tool_args(spec: dict[str, Any], args: dict[str, Any]) -> str | None:
    """Lightweight validation: every ``required`` property from the tool's JSON Schema must be
    present. No deeper type-checking -- this project has no ``jsonschema`` dependency and the
    downstream tool implementations (``eoa.search.deep_search``'s ``_tool_search``/``_tool_read``/
    the ``finish`` handling in ``_act``) already validate/coerce values themselves (e.g. via
    ``InvestigationOut.model_validate``); this check only needs to catch a reply that omits an
    argument outright, so a repair prompt can ask for it rather than crashing deeper in the loop.
    """
    required = (spec.get("parameters") or {}).get("required") or []
    missing = [r for r in required if r not in args]
    if missing:
        return f"missing required args: {missing}"
    return None


def _parse_tool_reply(
    raw: str, tools: list[dict[str, Any]]
) -> tuple[tuple[str, dict[str, Any]] | None, str | None]:
    """``((tool_name, args), None)`` on success, or ``(None, reason)`` on a malformed/invalid
    reply -- tolerates a fenced ```json block and, failing a direct parse, the first ``{...}``
    substring in the reply (some CLIs prepend a stray word or two despite the instruction)."""
    text = strip_code_fences(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_OBJECT_RE.search(text)
        if not m:
            return None, "no JSON object found in reply"
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as exc:
            return None, f"invalid JSON: {str(exc)[:120]}"
    if not isinstance(data, dict):
        return None, "reply JSON is not an object"

    if "final" in data:
        name = "finish"
        args = data["final"] if isinstance(data["final"], dict) else None
        if args is None:
            return None, '"final" value is not a JSON object'
    elif "tool" in data:
        name = str(data["tool"])
        args = data.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return None, '"args" is not a JSON object'
    else:
        return None, 'reply JSON is missing a "tool" or "final" key'

    spec = _find_tool_spec(tools, name)
    if spec is None:
        return None, f"unknown tool {name!r}"
    err = _validate_tool_args(spec, args)
    if err:
        return None, err
    return (name, args), None

_CREATE_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW, inlined so this imports on non-Windows too


# Windows caps the full command line at 32767 chars; leave headroom for the binary path, flags
# and the multi-byte expansion of Hebrew text.
_AGY_ARGV_PROMPT_MAX_CHARS = 24_000


def _binary_setting(kind: str) -> str:
    cli = settings().llm_providers.cli.get(kind)
    return cli.binary if cli else kind


def _resolve_binary(kind: str) -> str | None:
    return shutil.which(_binary_setting(kind))


def _mcp_config_path(cli_kind: str) -> Path | None:
    """A8 (docs/adr/006-mcp-sources.md, point 4): build a temporary ``--mcp-config`` JSON file
    (``{"mcpServers": {...}}``, the same shape ``claude mcp add-json`` writes) from this project's
    own enabled stdio MCP servers -- ``None`` when MCP is disabled, ``cli_kind`` isn't opted in via
    ``mcp.inherit_cli_mcp``, or there are no stdio servers to hand over. The caller is responsible
    for cleaning the returned path up (``CliProvider._cleanup``, same as codex's output tmpfile)."""
    try:
        cfg = settings().mcp
    except Exception:  # config not loadable (e.g. a unit test with a minimal fixture) -> no MCP
        return None
    if not cfg.enabled or not cfg.inherit_cli_mcp.get(cli_kind, False):
        return None
    servers = cfg.stdio_servers_for_cli()
    if not servers:
        return None

    mcp_servers: dict[str, Any] = {}
    for server in servers:
        command = sys.executable if server.command == "{python}" else server.command
        entry: dict[str, Any] = {"command": command, "args": list(server.args)}
        env = {name: os.environ[name] for name in server.env if name in os.environ}
        if env:
            entry["env"] = env
        mcp_servers[server.id] = entry

    fd, tmp_name = tempfile.mkstemp(prefix="eoa_mcp_config_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump({"mcpServers": mcp_servers}, fh)
    return Path(tmp_name)


def _flatten_messages(messages: list[dict[str, Any]]) -> str:
    """System + turns -> one prompt string. DATA framing in ``content`` is passed through as-is."""
    parts: list[str] = []
    for m in messages:
        role = m.get("role", "user")
        content = str(m.get("content", "") or "")
        if not content:
            continue
        if role == "system":
            parts.append(content)
        elif role == "assistant":
            parts.append(f"Assistant: {content}")
        else:
            parts.append(f"User: {content}")
    return "\n\n".join(parts)


class CliProvider:
    """One cloud CLI (``kind`` in ``agy``/``claude``/``codex``), optionally pinned to ``model``."""

    def __init__(self, kind: str, model: str | None = None, power: str | None = None) -> None:
        if kind not in _STATIC_MODELS:
            raise ValueError(f"unknown CLI provider kind: {kind!r}")
        self.kind = kind
        self.model = model
        self.power = power
        self.name = kind

    def is_available(self) -> bool:
        return _resolve_binary(self.kind) is not None

    @property
    def supports_tools(self) -> bool:
        """2026-09-09 (cloud tool-calling): gated behind ``llm_providers.cli_text_tools``
        (default ``true``) so the text-protocol dispatch below can be switched off without a code
        change if a CLI's real-world reliability on the protocol turns out to be poor. Every CLI
        kind opts in identically -- the protocol is provider-agnostic (plain text in, plain text
        out), unlike the native web-tools delegation in ``eoa.search.deep_search.
        investigate_batch_cloud`` (codex excluded there for lacking a search/web flag; that
        constraint doesn't apply here, this project's own tools are being described in the
        prompt, not the CLI's own browsing capability)."""
        try:
            return bool(settings().llm_providers.cli_text_tools)
        except Exception:
            return True

    def list_models(self) -> list[str]:
        cli = settings().llm_providers.cli.get(self.kind)
        return list(cli.models) if cli and cli.models else list(_STATIC_MODELS[self.kind])

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        power: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> ProviderResult:
        binary = _resolve_binary(self.kind)
        if not binary:
            raise ProviderUnavailable(
                f"CLI provider '{self.kind}' not found on PATH (looked for "
                f"'{_binary_setting(self.kind)}'; install/authenticate it or pick another provider)"
            )
        mdl = model or self.model
        pwr = power or self.power
        prompt = _flatten_messages(messages)
        timeout = timeout_s or float(settings().llm_providers.timeout_s)

        if tools:
            return self._chat_with_tools(binary, mdl, prompt, pwr, tools, timeout)

        if json_schema:
            prompt += _JSON_INSTRUCTION.format(schema=json.dumps(json_schema, ensure_ascii=False))

        content, usage, duration_ms = self._run_once(binary, mdl, prompt, pwr, timeout)
        log.info(
            "cli_provider_call",
            provider=self.kind,
            model=mdl or "default",
            prompt_chars=len(prompt),
            duration_ms=duration_ms,
        )
        # F26: validate + construct the full result inside a CliProviderError boundary -- see
        # `_validate_usage` above for why.
        try:
            return ProviderResult(
                content=content,
                model=mdl or "default",
                provider=self.kind,
                duration_ms=duration_ms,
                prompt_chars=len(prompt),
                usage=_validate_usage(self.kind, usage),
            )
        except (TypeError, ValueError) as exc:
            raise CliProviderError(f"{self.kind} CLI returned malformed usage data: {exc}") from exc

    def _run_once(
        self, binary: str, model: str | None, prompt: str, power: str | None, timeout: float
    ) -> tuple[str, dict[str, Any], int]:
        """One subprocess call for ``prompt`` -> ``(content, usage, duration_ms)``. Factored out
        of ``chat()`` so the tool-calling path (:meth:`_chat_with_tools`, up to two calls: the
        turn itself plus one repair attempt) shares exactly the same argv/stdin/parse/cleanup
        mechanics as the plain-text and structured-output paths."""
        args, stdin_data, tmp_out = self._build_args(binary, model, prompt, power)
        creationflags = _CREATE_NO_WINDOW if os.name == "nt" else 0

        t0 = time.monotonic()
        try:
            # `agy` (no stdin support -- see _build_args) gets no `input=`, which leaves
            # subprocess.run's stdin at its default of "inherit the parent's handle". That is
            # fine for a foreground/interactive process but breaks under a detached,
            # backgrounded server (the API process this eventually runs in): observed live
            # (2026-09-05) -- `agy` exits 1 with empty stdout/stderr in under a second when
            # spawned from inside a `nohup`'d uvicorn, while the identical call succeeds from a
            # normal foreground shell. Explicitly closing stdin for that case (nothing needs it)
            # avoids inheriting a handle that may not be valid in that context.
            proc = run_process(
                args,
                input=stdin_data,
                stdin=subprocess.DEVNULL if stdin_data is None else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds(timeout),
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            self._cleanup(tmp_out)
            checkpoint()
            raise CliProviderError(f"{self.kind} CLI timed out after {timeout:.0f}s") from exc
        except OSError as exc:
            self._cleanup(tmp_out)
            raise ProviderUnavailable(f"failed to launch {self.kind} CLI ({binary}): {exc}") from exc

        duration_ms = int((time.monotonic() - t0) * 1000)
        try:
            checkpoint()
            content, usage = self._parse_output(proc, tmp_out)
        finally:
            self._cleanup(tmp_out)
        return content, usage, duration_ms

    def _chat_with_tools(
        self,
        binary: str,
        model: str | None,
        prompt: str,
        power: str | None,
        tools: list[dict[str, Any]],
        timeout: float,
    ) -> ProviderResult:
        """Text-protocol tool calling (2026-09-09): render ``tools`` into the prompt with a strict
        output contract, call once, parse; on a malformed/invalid reply give the model exactly one
        repair prompt, then raise ``CliProviderError`` (picked up by ``eoa.llm.chain.run_chain``'s
        existing fallback machinery -- see module docstring above) rather than retrying further.
        Returns a ``ProviderResult`` whose ``tool_calls`` carries the same
        ``[{"function": {"name": ..., "arguments": {...}}}]`` shape Ollama's native tool-calling
        API produces, so ``eoa.search.deep_search._act`` needs no change at all."""
        tool_prompt = prompt + _TOOL_PROTOCOL_INSTRUCTION.format(tool_list=_render_tool_list(tools))

        content, usage, duration_ms = self._run_once(binary, model, tool_prompt, power, timeout)
        parsed, reason = _parse_tool_reply(content, tools)

        if parsed is None:
            repair_prompt = tool_prompt + _TOOL_REPAIR_INSTRUCTION.format(
                prior_reply=content[:2000], reason=reason or "malformed reply"
            )
            content2, usage2, duration_ms2 = self._run_once(binary, model, repair_prompt, power, timeout)
            duration_ms += duration_ms2
            # Efficiency (audit 2026-09-24): `usage = usage2 or usage` discarded the first call's
            # token usage outright instead of accounting for it -- the repair call is a genuine
            # SECOND LLM call on top of the first, not a replacement of it, so cost/token totals
            # under-reported every triggered repair. Sum matching numeric fields across both calls.
            usage = _merge_usage(usage, usage2)
            parsed, reason = _parse_tool_reply(content2, tools)
            if parsed is None:
                raise CliProviderError(
                    f"{self.kind} CLI text-tools reply still malformed after one repair attempt: {reason}"
                )

        name, args = parsed
        log.info("llm_chain_text_tools", provider=self.kind, model=model or "default", tool=name)
        try:
            return ProviderResult(
                content="",
                model=model or "default",
                provider=self.kind,
                duration_ms=duration_ms,
                prompt_chars=len(prompt),
                usage=_validate_usage(self.kind, usage),
                tool_calls=[{"function": {"name": name, "arguments": args}}],
            )
        except (TypeError, ValueError) as exc:
            raise CliProviderError(f"{self.kind} CLI returned malformed usage data: {exc}") from exc

    # -- per-kind argv/stdin construction --------------------------------------------------

    def _build_args(
        self, binary: str, model: str | None, prompt: str, power: str | None = None
    ) -> tuple[list[str], str | None, Path | None]:
        """U8-ג (Revision 2026-09-06): ``power`` ("low"/"medium"/"high", ...) is appended as a
        bare ``--effort <level>`` for ``agy``/``claude`` -- both accept that exact flag name,
        confirmed live 2026-09-06 against `agy --help`/`claude --help` on this machine (`claude`'s
        `--effort` also accepts "xhigh"/"max", not offered in this project's config default);
        `codex` has no such flag (`codex exec --help`, same check) and gets
        ``-c model_reasoning_effort=<level>`` instead, via its generic config-override mechanism.
        """
        real_model = None if model == "default" else model
        if self.kind == "agy":
            # No stdin support (see docs/adr/005-cloud-llm-cli.md); the prompt is a plain argv
            # element (no shell involved) up to _AGY_ARGV_PROMPT_MAX_CHARS, an @file include above.
            # A8/point 4 (docs/adr/006-mcp-sources.md): `agy --help` (checked 2026-09-06) has only
            # a persistent `agy mcp add/remove/list/enable/disable` server registry, no per-call
            # config flag equivalent to claude's `--mcp-config` -- not wired here; documented gap,
            # `mcp.inherit_cli_mcp.agy` defaults to `false` in config/mcp.yaml accordingly.
            tmp_prompt: Path | None = None
            if len(prompt) > _AGY_ARGV_PROMPT_MAX_CHARS:
                # 2026-09-07 (live weekly rebuild): a 74k-char report prompt as one argv element
                # fails with WinError 206 ("filename or extension is too long") -- Windows caps the
                # whole command line at ~32k chars -- so every agy fallback leg silently died and
                # the chain fell through to the local model. `agy` has no plain-stdin mode, but
                # its `@<path>` include (verified live: `agy -p "... @file"` reads the file) does
                # the job: spill the prompt to a UTF-8 temp file and hand agy a short pointer.
                fd, tmp_name = tempfile.mkstemp(prefix="eoa_agy_prompt_", suffix=".md")
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(prompt)
                tmp_prompt = Path(tmp_name)
                argv_prompt = (
                    "The complete prompt (system instructions, task, input material and the required "
                    f"output format) is in the attached file @{tmp_prompt.as_posix()} -- read it in "
                    "full and respond exactly as that file instructs, with no preamble."
                )
            else:
                argv_prompt = prompt
            args = [binary, "-p", argv_prompt, "--output-format", "json"]
            if real_model:
                args += ["--model", real_model]
            if power:
                args += ["--effort", power]
            return args, None, tmp_prompt
        if self.kind == "claude":
            # `-p` with no attached value reads the prompt from stdin.
            args = [binary, "-p", "--output-format", "json", "--restricted"]
            if real_model:
                args += ["--model", real_model]
            if power:
                args += ["--effort", power]
            mcp_config_path = _mcp_config_path("claude")
            if mcp_config_path:
                # A8 (docs/adr/006-mcp-sources.md): hand this project's own stdio MCP servers to
                # the CLI, same shape `claude mcp add-json` writes. NOTE (known limitation, see the
                # ADR): `--restricted` still blocks every tool -- including one loaded this way --
                # unless it is also named in `--allowedTools` (verified live for WebSearch/WebFetch
                # in `eoa.search.deep_search._run_claude_with_tools`; not re-verified here for MCP
                # tool names, so this flag alone loads the servers without yet granting access to
                # them in THIS text-only call path). Left honestly incomplete rather than guessing
                # an unverified `--allowedTools` pattern; the tool-granting call path (deep search's
                # cloud-batch delegation) is a separate function this change does not touch.
                args += ["--mcp-config", str(mcp_config_path)]
            return args, prompt, mcp_config_path
        # codex: final message is written to -o/--output-last-message rather than parsed out of
        # the NDJSON --json stream, which also carries hook/skill noise on this machine. No
        # dedicated effort/power flag exists (`codex exec --help`, checked 2026-09-06) -- routed
        # through the generic `-c key=value` config override instead, per the ADR's design.
        # A8/point 4 (docs/adr/006-mcp-sources.md): `codex exec --help` has no per-call MCP flag
        # either -- only a persistent `codex mcp` server registry (config.toml), same shape as
        # agy's. Its generic `-c key=value` override could in principle inject
        # `mcp_servers.<id>.command=...` entries, but that was judged too speculative to ship
        # unverified (TOML value/array quoting through `-c` is unconfirmed for this shape) --
        # documented gap, `mcp.inherit_cli_mcp.codex` defaults to `false`.
        fd, tmp_name = tempfile.mkstemp(prefix="eoa_codex_", suffix=".txt")
        os.close(fd)
        tmp_out = Path(tmp_name)
        args = [binary, "exec", "-s", "read-only", "--json", "-o", str(tmp_out)]
        if real_model:
            args += ["-m", real_model]
        if power:
            args += ["-c", f"model_reasoning_effort={power}"]
        return args, prompt, tmp_out

    @staticmethod
    def _cleanup(tmp_out: Path | None) -> None:
        if tmp_out is not None:
            if os.environ.get("EOA_CLI_KEEP_PROMPT") == "1" and tmp_out.name.startswith("eoa_agy_prompt_"):
                # debugging aid (2026-09-07): keep the spilled prompt so a failing agy call can be
                # replayed by hand; never set in production runs.
                log.warning("cli_prompt_file_kept", path=str(tmp_out))
                return
            try:
                tmp_out.unlink(missing_ok=True)
            except OSError:
                pass

    def _parse_output(
        self, proc: subprocess.CompletedProcess[str], tmp_out: Path | None
    ) -> tuple[str, dict[str, Any]]:
        if self.kind == "agy":
            return _parse_agy(proc)
        if self.kind == "claude":
            return _parse_claude(proc)
        return _parse_codex(proc, tmp_out)


def _fail(kind: str, proc: subprocess.CompletedProcess[str], reason: str) -> CliProviderError:
    """Build the exception for a non-zero exit. Prefers stderr; several of these CLIs (observed
    live with `agy`, e.g. an invalid `--model` id) put the actual error message in a JSON body
    on stdout instead, with stderr empty -- fall back to that (a top-level "error" or "message"
    string field) before giving up and saying so.
    """
    stderr = (proc.stderr or "").strip()[-500:]
    if not stderr:
        try:
            data = json.loads(proc.stdout)
            stderr = str(data.get("error") or data.get("message") or data.get("result") or "")[:500]
        except (json.JSONDecodeError, AttributeError):
            pass
    # 2026-09-07: agy's stderr on a mid-run failure is the generic "Agent execution terminated due
    # to error." while the JSON body on stdout carries the real cause -- always append its tail.
    stdout_tail = (proc.stdout or "").strip()[:400]
    detail = stderr or "(no output)"
    if stdout_tail and stdout_tail not in detail:
        detail = f"{detail} | stdout: {stdout_tail}"
    return CliProviderError(f"{kind} CLI {reason} (exit {proc.returncode}): {detail}")


def _parse_agy(proc: subprocess.CompletedProcess[str]) -> tuple[str, dict[str, Any]]:
    if proc.returncode != 0:
        raise _fail("agy", proc, "failed")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CliProviderError(f"agy CLI returned non-JSON output: {proc.stdout[:300]!r}") from exc
    # F26 (audit 2026-09-24): a syntactically valid but wrong-shaped body (e.g. a JSON list/string
    # instead of an object) used to raise a bare `AttributeError` from `data.get(...)` below --
    # not `CliProviderError`, so `eoa.llm.chain.FALLBACK_EXCEPTIONS` never caught it and the whole
    # chain aborted instead of falling through to the next leg.
    if not isinstance(data, dict):
        raise CliProviderError(f"agy CLI returned a JSON {type(data).__name__}, not an object: {proc.stdout[:300]!r}")
    content = str(data.get("response", "")).strip()
    if data.get("status") and data["status"] != "SUCCESS":
        # 2026-09-22: agy sometimes reports status=ERROR while still carrying a complete, usable
        # `response` (e.g. a fenced JSON object). Rejecting those outright stranded whole stages on
        # the night of 2026-09-21: the `light` chain (agy -> ollama) and the `resident` chain both
        # exhausted into the paused local leg and deferred deep_search/analyze. The schema
        # validation downstream is the real gate -- unusable content still fails there and falls
        # through the chain exactly as before -- so a non-empty response is worth using.
        if not content:
            raise CliProviderError(f"agy CLI status={data.get('status')}: {str(data)[:300]}")
        log.warning("agy_status_error_with_response", status=str(data.get("status")), chars=len(content))
    if not content or data.get("response") is None:
        raise CliProviderError("agy CLI returned an empty response")
    usage = data.get("usage") or {}
    # F26 follow-up (SOL-REVIEW-2026-09-24): a syntactically valid, object-shaped body whose
    # `usage` field itself is the WRONG shape (e.g. a list/string) used to be returned as-is --
    # every later consumer (`ProviderResult.usage`, `eoa.llm.chain._record`, `_merge_usage`'s own
    # `.items()` call) assumes a dict and raises an uncaught `AttributeError` outside this
    # function's own error boundary, aborting the whole fallback chain instead of falling through.
    # Sanitizing here, at the parse boundary, means every downstream consumer can keep assuming a
    # dict without its own defensive check.
    if not isinstance(usage, dict):
        log.warning("agy_usage_field_malformed", usage_type=type(usage).__name__)
        usage = {}
    return content, usage


def _parse_claude(proc: subprocess.CompletedProcess[str]) -> tuple[str, dict[str, Any]]:
    if proc.returncode != 0:
        raise _fail("claude", proc, "failed")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CliProviderError(f"claude CLI returned non-JSON output: {proc.stdout[:300]!r}") from exc
    # F26 (audit 2026-09-24): see the identical comment in `_parse_agy` above.
    if not isinstance(data, dict):
        raise CliProviderError(
            f"claude CLI returned a JSON {type(data).__name__}, not an object: {proc.stdout[:300]!r}"
        )
    if data.get("is_error"):
        raise CliProviderError(f"claude CLI reported an error: {str(data.get('result'))[:300]}")
    content = str(data.get("result", "")).strip()
    if not content or data.get("result") is None:
        raise CliProviderError("claude CLI returned an empty response")
    usage = data.get("usage") or {}
    # F26 follow-up (SOL-REVIEW-2026-09-24): see the identical comment in `_parse_agy` above.
    if not isinstance(usage, dict):
        log.warning("claude_usage_field_malformed", usage_type=type(usage).__name__)
        usage = {}
    return content, usage


def _parse_codex(proc: subprocess.CompletedProcess[str], tmp_out: Path | None) -> tuple[str, dict[str, Any]]:
    if proc.returncode != 0:
        raise _fail("codex", proc, "failed")
    if tmp_out is None or not tmp_out.exists():
        raise CliProviderError("codex CLI did not write an output file")
    content = tmp_out.read_text(encoding="utf-8", errors="replace").strip()
    if not content:
        raise CliProviderError("codex CLI returned an empty response")
    usage: dict[str, Any] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        # F26 (audit 2026-09-24): a well-formed-JSON but non-object NDJSON line (e.g. a bare
        # number/string) would otherwise raise `AttributeError` on `.get` here -- best-effort
        # usage extraction, so just skip it rather than let it escape uncaught.
        if isinstance(evt, dict) and evt.get("type") == "turn.completed" and isinstance(evt.get("usage"), dict):
            usage = evt["usage"]
    return content, usage
