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
import os
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
from eoa.llm.providers.base import ProviderResult

log = structlog.get_logger(__name__)

_STATIC_MODELS: dict[str, list[str]] = {
    # `agy models` (verified live, 2026-09-05) -- the id column, not the display name; the
    # catalog moves under the CLI's own updates, so this list (and config.yaml's) may drift
    # from what `agy models` reports later. `--model <id>` rejects anything else with a
    # "not recognized" error that lists the current catalog, which is how this was caught.
    "agy": ["gemini-3.8-flash-medium", "gemini-3.8-flash-high", "gemini-3.1-pro-high"],
    "claude": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
    "codex": ["default"],
}

_JSON_INSTRUCTION = (
    "\n\n--- STRUCTURED OUTPUT REQUIRED ---\n"
    "Return ONLY valid JSON matching this JSON Schema. No prose, no markdown code fences, "
    "no explanation before or after the JSON:\n{schema}"
)

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
        if json_schema:
            prompt += _JSON_INSTRUCTION.format(schema=json.dumps(json_schema, ensure_ascii=False))
        timeout = timeout_s or float(settings().llm_providers.timeout_s)

        args, stdin_data, tmp_out = self._build_args(binary, mdl, prompt, pwr)
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
            proc = subprocess.run(
                args,
                input=stdin_data,
                stdin=subprocess.DEVNULL if stdin_data is None else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            self._cleanup(tmp_out)
            raise CliProviderError(f"{self.kind} CLI timed out after {timeout:.0f}s") from exc
        except OSError as exc:
            self._cleanup(tmp_out)
            raise ProviderUnavailable(f"failed to launch {self.kind} CLI ({binary}): {exc}") from exc

        duration_ms = int((time.monotonic() - t0) * 1000)
        try:
            content, usage = self._parse_output(proc, tmp_out)
        finally:
            self._cleanup(tmp_out)

        log.info(
            "cli_provider_call",
            provider=self.kind,
            model=mdl or "default",
            prompt_chars=len(prompt),
            duration_ms=duration_ms,
        )
        return ProviderResult(
            content=content,
            model=mdl or "default",
            provider=self.kind,
            duration_ms=duration_ms,
            prompt_chars=len(prompt),
            usage=usage,
        )

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
            stderr = str(data.get("error") or data.get("message") or "")[:500]
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
    if data.get("status") and data["status"] != "SUCCESS":
        raise CliProviderError(f"agy CLI status={data.get('status')}: {str(data)[:300]}")
    content = str(data.get("response", "")).strip()
    usage = data.get("usage") or {}
    return content, usage


def _parse_claude(proc: subprocess.CompletedProcess[str]) -> tuple[str, dict[str, Any]]:
    if proc.returncode != 0:
        raise _fail("claude", proc, "failed")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise CliProviderError(f"claude CLI returned non-JSON output: {proc.stdout[:300]!r}") from exc
    if data.get("is_error"):
        raise CliProviderError(f"claude CLI reported an error: {str(data.get('result'))[:300]}")
    content = str(data.get("result", "")).strip()
    usage = data.get("usage") or {}
    return content, usage


def _parse_codex(proc: subprocess.CompletedProcess[str], tmp_out: Path | None) -> tuple[str, dict[str, Any]]:
    if proc.returncode != 0:
        raise _fail("codex", proc, "failed")
    if tmp_out is None or not tmp_out.exists():
        raise CliProviderError("codex CLI did not write an output file")
    content = tmp_out.read_text(encoding="utf-8", errors="replace").strip()
    usage: dict[str, Any] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") == "turn.completed" and isinstance(evt.get("usage"), dict):
            usage = evt["usage"]
    return content, usage
