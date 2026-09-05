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


def _binary_setting(kind: str) -> str:
    cli = settings().llm_providers.cli.get(kind)
    return cli.binary if cli else kind


def _resolve_binary(kind: str) -> str | None:
    return shutil.which(_binary_setting(kind))


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

    def __init__(self, kind: str, model: str | None = None) -> None:
        if kind not in _STATIC_MODELS:
            raise ValueError(f"unknown CLI provider kind: {kind!r}")
        self.kind = kind
        self.model = model
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
    ) -> ProviderResult:
        binary = _resolve_binary(self.kind)
        if not binary:
            raise ProviderUnavailable(
                f"CLI provider '{self.kind}' not found on PATH (looked for "
                f"'{_binary_setting(self.kind)}'; install/authenticate it or pick another provider)"
            )
        mdl = model or self.model
        prompt = _flatten_messages(messages)
        if json_schema:
            prompt += _JSON_INSTRUCTION.format(schema=json.dumps(json_schema, ensure_ascii=False))
        timeout = timeout_s or float(settings().llm_providers.timeout_s)

        args, stdin_data, tmp_out = self._build_args(binary, mdl, prompt)
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
        self, binary: str, model: str | None, prompt: str
    ) -> tuple[list[str], str | None, Path | None]:
        real_model = None if model == "default" else model
        if self.kind == "agy":
            # No stdin support (see docs/adr/005-cloud-llm-cli.md); the prompt is a plain argv
            # element (no shell involved), reliable up to ~32KB per the field notes.
            args = [binary, "-p", prompt, "--output-format", "json"]
            if real_model:
                args += ["--model", real_model]
            return args, None, None
        if self.kind == "claude":
            # `-p` with no attached value reads the prompt from stdin.
            args = [binary, "-p", "--output-format", "json", "--restricted"]
            if real_model:
                args += ["--model", real_model]
            return args, prompt, None
        # codex: final message is written to -o/--output-last-message rather than parsed out of
        # the NDJSON --json stream, which also carries hook/skill noise on this machine.
        fd, tmp_name = tempfile.mkstemp(prefix="eoa_codex_", suffix=".txt")
        os.close(fd)
        tmp_out = Path(tmp_name)
        args = [binary, "exec", "-s", "read-only", "--json", "-o", str(tmp_out)]
        if real_model:
            args += ["-m", real_model]
        return args, prompt, tmp_out

    @staticmethod
    def _cleanup(tmp_out: Path | None) -> None:
        if tmp_out is not None:
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
    return CliProviderError(f"{kind} CLI {reason} (exit {proc.returncode}): {stderr or '(no output)'}")


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
