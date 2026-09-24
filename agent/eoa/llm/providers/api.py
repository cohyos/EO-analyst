"""Direct-API cloud LLM providers (U8-ו, docs/adr/005-cloud-llm-cli.md Revision 2026-09-06):
Anthropic Messages API, Google Gemini ``generateContent``, OpenAI Chat Completions.

Keys come from the environment only (``ANTHROPIC_API_KEY`` / ``GEMINI_API_KEY`` /
``OPENAI_API_KEY``, populated from ``.env``) -- never from config.yaml, never logged, never
surfaced to the UI beyond an ``is_available()`` boolean ("מוגדר / לא מוגדר").

Each provider is a thin ``httpx`` wrapper (120s timeout) with ``tenacity`` retry on 429/5xx/
timeout and a "power" level (low/medium/high) mapped onto that API's own effort/thinking knob.
Structured output uses each API's native JSON-schema mechanism (Anthropic tool-use,
Gemini ``responseSchema``, OpenAI ``response_format``); ``eoa.llm.ollama_client.chat_structured``
still owns the validate-and-one-corrective-retry contract on top of whatever comes back here --
these classes only ever make one HTTP call per ``chat()`` invocation, exactly like ``CliProvider``.
"""

from __future__ import annotations

import copy
import json
import math
import os
import time
from typing import Any

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from eoa.config import settings
from eoa.errors import CliProviderError, ProviderUnavailable
from eoa.execution import checkpoint, timeout_seconds
from eoa.llm.providers.base import ProviderResult, strip_code_fences

# Q2-15 (2026-09-06): moved to `eoa.security.redact` so `eoa.mcp_servers.*` / `eoa.mcp.client` /
# `eoa.mcp.registry` share the exact same patterns instead of a second copy. Imported (not
# redefined) here so `from eoa.llm.providers.api import redact_secrets` keeps working.
from eoa.security.redact import redact_secrets

log = structlog.get_logger(__name__)

_TIMEOUT_S = 120.0


class ApiProviderError(CliProviderError):
    """A direct-API provider call failed after retries.

    Subclasses ``CliProviderError`` deliberately so ``eoa.llm.chain.run_chain``'s single
    fallback-worthy exception tuple treats a failed CLI subprocess and a failed API call
    identically -- both just mean "move on to the next chain entry".
    """


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return False


def _retrying() -> Any:
    return retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception(_retryable),
    )


def _flatten_system_and_turns(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Split chat ``messages`` into ``(system_text, turns)`` -- every API here wants the system
    prompt passed separately from the conversation turns."""
    system_parts: list[str] = []
    turns: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role", "user")
        content = str(m.get("content", "") or "")
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
        else:
            turns.append({"role": role, "content": content})
    return "\n\n".join(system_parts), turns


def _finalize_result(
    *,
    provider: str,
    content: Any,
    model: str,
    duration_ms: int,
    prompt_chars: int,
    input_tokens: Any,
    output_tokens: Any,
) -> ProviderResult:
    """Validate and build the full ``ProviderResult`` for a provider ``chat()`` call.

    F26 (SOL-REVIEW2-2026-09-24 remaining gap): usage *containers* were already checked
    (``isinstance(usage, dict)``), but the actual token-count conversion
    (``int(usage.get("input_tokens") or 0)``) and ``strip_code_fences(content)`` happened in the
    bare ``return ProviderResult(...)`` expression -- OUTSIDE the surrounding ``try/except``
    boundary in each provider's ``chat()``. A malformed token value (a string, a negative number)
    or a non-string ``content`` therefore raised a bare ``TypeError``/``ValueError`` straight out
    of ``chat()`` instead of the caught ``ApiProviderError`` -- not one of
    ``eoa.llm.chain.FALLBACK_EXCEPTIONS``, so it aborted the whole fallback chain instead of
    falling through to the next leg. Callers now call this *inside* their existing ``try`` block,
    so any failure here is caught by the same ``except`` that already wraps a malformed envelope.

    A missing/``None`` token count is not malformed -- some provider responses omit a count
    entirely (e.g. no thinking tokens spent) -- and coerces to ``0``, matching prior behavior.
    """
    if not isinstance(content, str):
        raise TypeError(f"{provider} response content is a {type(content).__name__}, not text")

    def _count(value: Any, field: str) -> int:
        if value is None:
            return 0
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{provider} {field} is a {type(value).__name__}, not a number: {value!r}")
        # F26 (SOL-REVIEW3-2026-09-24): a nonfinite float (NaN/Infinity -- json.loads happily
        # parses the bare `NaN`/`Infinity`/`-Infinity` literals some providers emit) passed the
        # isinstance check above and reached `int(value)` below, where `int(float("inf"))` raises
        # `OverflowError` -- not one of the `(ValueError, TypeError, AttributeError, KeyError,
        # IndexError)` types every caller's `except` around `_finalize_result` catches, so it
        # escaped the malformed-envelope boundary and aborted the whole fallback chain instead of
        # falling through to the next leg. Rejecting it here, as the same `ValueError` the
        # negative-count check below already uses, keeps it on the existing caught path.
        # F26 (SOL-REVIEW4-2026-09-24): `math.isfinite` itself raises `OverflowError` when handed
        # an `int` too large for a C double (e.g. a malformed token count like `10**400`) -- ints
        # are always mathematically finite, so this check only applies to `float`. The `int(value)`
        # conversion below can *also* raise `OverflowError` for a huge float-like input in other
        # runtimes; catching it here keeps that on the same caught `ValueError` path too.
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{provider} {field} is not finite: {value!r}")
        try:
            n = int(value)
        except OverflowError as exc:
            raise ValueError(f"{provider} {field} is not finite: {value!r}") from exc
        if n < 0:
            raise ValueError(f"{provider} {field} is negative: {n}")
        return n

    return ProviderResult(
        content=strip_code_fences(content),
        model=model,
        provider=provider,
        duration_ms=duration_ms,
        prompt_chars=prompt_chars,
        usage={
            "input_tokens": _count(input_tokens, "input_tokens"),
            "output_tokens": _count(output_tokens, "output_tokens"),
        },
    )


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Best-effort adaptation of a pydantic ``model_json_schema()`` output into the subset of
    JSON Schema Gemini's ``responseSchema`` accepts: inline ``$defs``/``$ref`` (Gemini does not
    resolve external refs) and drop keys it does not recognise (``title``,
    ``additionalProperties``, ``$schema``, ``default``)."""
    defs = schema.get("$defs", {})

    def _resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].rsplit("/", 1)[-1]
                return _resolve(copy.deepcopy(defs.get(ref, {})))
            out = {}
            for k, v in node.items():
                if k in ("title", "additionalProperties", "$schema", "default", "$defs"):
                    continue
                out[k] = _resolve(v)
            return out
        if isinstance(node, list):
            return [_resolve(v) for v in node]
        return node

    return _resolve(schema)


# ------------------------------------------------------------------------------------- Anthropic

_ANTHROPIC_POWER_BUDGET = {"low": 1024, "medium": 4096, "high": 16000}


class AnthropicProvider:
    """Anthropic Messages API (https://api.anthropic.com/v1/messages)."""

    name = "anthropic"

    def __init__(self, model: str | None = None, power: str | None = None) -> None:
        self.model = model
        self.power = power

    def is_available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def list_models(self) -> list[str]:
        cfg = settings().llm_providers.api.get("anthropic")
        return list(cfg.models) if cfg else []

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> ProviderResult:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderUnavailable("ANTHROPIC_API_KEY not set in the environment (.env)")
        models = self.list_models()
        mdl = model or self.model or (models[0] if models else "claude-sonnet-5")
        system, turns = _flatten_system_and_turns(messages)
        api_messages = [
            {"role": t["role"] if t["role"] == "assistant" else "user", "content": t["content"]}
            for t in turns
        ] or [{"role": "user", "content": " "}]
        max_tokens = 4096
        body: dict[str, Any] = {"model": mdl, "max_tokens": max_tokens, "messages": api_messages}
        if system:
            body["system"] = system
        budget = _ANTHROPIC_POWER_BUDGET.get(self.power or "")
        if budget:
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            body["max_tokens"] = max(max_tokens, budget + 1024)
        tool_name: str | None = None
        if json_schema:
            tool_name = "emit_result"
            body["tools"] = [
                {"name": tool_name, "description": "Emit the structured result.", "input_schema": json_schema}
            ]
            body["tool_choice"] = {"type": "tool", "name": tool_name}

        @_retrying()
        def _call() -> httpx.Response:
            with httpx.Client(timeout=timeout_seconds(timeout_s or _TIMEOUT_S)) as c:
                r = c.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=body,
                )
                checkpoint()
                r.raise_for_status()
                return r

        t0 = time.monotonic()
        try:
            r = _call()
        except httpx.HTTPStatusError as exc:
            raise ApiProviderError(
                f"anthropic API error {exc.response.status_code}: {redact_secrets(exc.response.text[:300])}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiProviderError(f"anthropic API request failed: {redact_secrets(str(exc))}") from exc
        duration_ms = int((time.monotonic() - t0) * 1000)
        # F26 (audit 2026-09-24): a 200 response with a non-JSON or wrong-shaped body used to raise
        # a bare `json.JSONDecodeError`/`AttributeError`/... straight out of this method -- not one
        # of `eoa.llm.chain.FALLBACK_EXCEPTIONS` -- which aborted the whole fallback chain instead
        # of moving on to the next leg. Converting any malformed-envelope failure into
        # `ApiProviderError` here lets it fall through exactly like an HTTP error already does.
        try:
            data = r.json()
            content = ""
            if tool_name:
                for block in data.get("content", []) or []:
                    if block.get("type") == "tool_use" and block.get("name") == tool_name:
                        content = json.dumps(block.get("input", {}), ensure_ascii=False)
                        break
            if not content:
                for block in data.get("content", []) or []:
                    if block.get("type") == "text":
                        content += block.get("text", "")
            usage = data.get("usage") or {}
            # F26 follow-up (SOL-REVIEW-2026-09-24): a syntactically valid response whose `usage`
            # field is present but the WRONG shape (e.g. a list/string instead of an object) used
            # to sail through this `try` (`data.get("usage")` never raises) and only blow up later,
            # OUTSIDE this boundary, when the `usage.get(...)` calls below built the `ProviderResult`
            # -- an uncaught `AttributeError` there is not one of `FALLBACK_EXCEPTIONS`, so it
            # aborted the whole chain instead of falling through. Validating the shape here, still
            # inside the `try`, converts it into the same `ApiProviderError` as every other
            # malformed-envelope case.
            if not isinstance(usage, dict):
                raise TypeError(f"usage field is a {type(usage).__name__}, not an object")
            # F26: full result construction (content + token-count validation) now happens
            # inside this try, so a malformed value here is caught below like any other
            # malformed-envelope failure, instead of raising past this boundary.
            result = _finalize_result(
                provider="anthropic",
                content=content,
                model=mdl,
                duration_ms=duration_ms,
                prompt_chars=sum(len(t["content"]) for t in turns) + len(system),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
            )
        except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
            raise ApiProviderError(
                f"anthropic API returned a malformed response body: {redact_secrets(str(exc))[:200]}"
            ) from exc
        log.info("api_provider_call", provider="anthropic", model=mdl, duration_ms=duration_ms)
        return result


# ---------------------------------------------------------------------------------------- Gemini

_GEMINI_POWER_BUDGET = {"low": 512, "medium": 4096, "high": 16000}


class GeminiProvider:
    """Google Generative Language API v1beta ``generateContent``."""

    name = "gemini"

    def __init__(self, model: str | None = None, power: str | None = None) -> None:
        self.model = model
        self.power = power

    def is_available(self) -> bool:
        return bool(os.environ.get("GEMINI_API_KEY"))

    def list_models(self) -> list[str]:
        """Static config default; refreshed live from ``/v1beta/models`` when the key is present
        (point 3: "list models via /v1beta/models when the key exists")."""
        cfg = settings().llm_providers.api.get("gemini")
        static = list(cfg.models) if cfg else []
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            return static
        try:
            with httpx.Client(timeout=10.0) as c:
                # Q2-3: key goes in the `x-goog-api-key` header, never a `?key=` query
                # param -- a query param is far more likely to end up copied into logs,
                # proxies, or browser history than a header.
                r = c.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    headers={"x-goog-api-key": key},
                )
                checkpoint()
                r.raise_for_status()
                names = [
                    m["name"].removeprefix("models/")
                    for m in r.json().get("models", [])
                    if "generateContent" in (m.get("supportedGenerationMethods") or [])
                ]
                return names or static
        # Q2-3: narrowed from a bare `except Exception` -- only httpx transport/status
        # errors, a non-JSON body, and an unexpected response shape (missing "name")
        # are expected failure modes here; anything else should surface, not be
        # swallowed as "best effort".
        except (
            httpx.HTTPError,
            ValueError,
            KeyError,
        ) as exc:  # live listing is best-effort; never break the picker
            log.warning("gemini_list_models_failed", error=redact_secrets(str(exc))[:200])
            return static

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> ProviderResult:
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise ProviderUnavailable("GEMINI_API_KEY not set in the environment (.env)")
        cfg = settings().llm_providers.api.get("gemini")
        static_models = list(cfg.models) if cfg else []
        mdl = model or self.model or (static_models[0] if static_models else "gemini-3.5-flash")
        system, turns = _flatten_system_and_turns(messages)
        contents = [
            {"role": "model" if t["role"] == "assistant" else "user", "parts": [{"text": t["content"]}]}
            for t in turns
        ] or [{"role": "user", "parts": [{"text": " "}]}]
        gen_cfg: dict[str, Any] = {}
        budget = _GEMINI_POWER_BUDGET.get(self.power or "")
        if budget:
            gen_cfg["thinkingConfig"] = {"thinkingBudget": budget}
        if json_schema:
            gen_cfg["responseMimeType"] = "application/json"
            gen_cfg["responseSchema"] = _gemini_schema(json_schema)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if gen_cfg:
            body["generationConfig"] = gen_cfg

        @_retrying()
        def _call() -> httpx.Response:
            with httpx.Client(timeout=timeout_seconds(timeout_s or _TIMEOUT_S)) as c:
                # Q2-3: `x-goog-api-key` header, not a `?key=` query param -- see
                # `list_models` above for why.
                r = c.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent",
                    headers={"x-goog-api-key": key},
                    json=body,
                )
                checkpoint()
                r.raise_for_status()
                return r

        t0 = time.monotonic()
        try:
            r = _call()
        except httpx.HTTPStatusError as exc:
            raise ApiProviderError(
                f"gemini API error {exc.response.status_code}: {redact_secrets(exc.response.text[:300])}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiProviderError(f"gemini API request failed: {redact_secrets(str(exc))}") from exc
        duration_ms = int((time.monotonic() - t0) * 1000)
        # F26 (audit 2026-09-24): see the identical comment in AnthropicProvider.chat above.
        try:
            data = r.json()
            content = ""
            for cand in data.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    content += part.get("text", "")
                if content:
                    break
            usage = data.get("usageMetadata") or {}
            # F26 follow-up (SOL-REVIEW-2026-09-24): see the identical comment in
            # AnthropicProvider.chat above -- validated inside the boundary, not after it.
            if not isinstance(usage, dict):
                raise TypeError(f"usageMetadata field is a {type(usage).__name__}, not an object")
            # F26: see the identical comment in AnthropicProvider.chat above.
            result = _finalize_result(
                provider="gemini",
                content=content,
                model=mdl,
                duration_ms=duration_ms,
                prompt_chars=sum(len(t["content"]) for t in turns) + len(system),
                input_tokens=usage.get("promptTokenCount"),
                output_tokens=usage.get("candidatesTokenCount"),
            )
        except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
            raise ApiProviderError(
                f"gemini API returned a malformed response body: {redact_secrets(str(exc))[:200]}"
            ) from exc
        log.info("api_provider_call", provider="gemini", model=mdl, duration_ms=duration_ms)
        return result


# ---------------------------------------------------------------------------------------- OpenAI


class OpenAIProvider:
    """OpenAI Chat Completions API."""

    name = "openai"

    def __init__(self, model: str | None = None, power: str | None = None) -> None:
        self.model = model
        self.power = power

    def is_available(self) -> bool:
        return bool(os.environ.get("OPENAI_API_KEY"))

    def list_models(self) -> list[str]:
        cfg = settings().llm_providers.api.get("openai")
        return list(cfg.models) if cfg else []

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> ProviderResult:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ProviderUnavailable("OPENAI_API_KEY not set in the environment (.env)")
        models = self.list_models()
        mdl = model or self.model or (models[0] if models else "gpt-5.1-mini")
        system, turns = _flatten_system_and_turns(messages)
        api_messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": t["role"], "content": t["content"]} for t in turns
        ]
        if not api_messages:
            api_messages = [{"role": "user", "content": " "}]
        body: dict[str, Any] = {"model": mdl, "messages": api_messages}
        if self.power in ("low", "medium", "high"):
            body["reasoning_effort"] = self.power
        if json_schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "eoa_output", "schema": json_schema, "strict": False},
            }

        @_retrying()
        def _call() -> httpx.Response:
            with httpx.Client(timeout=timeout_seconds(timeout_s or _TIMEOUT_S)) as c:
                r = c.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
                    json=body,
                )
                checkpoint()
                r.raise_for_status()
                return r

        t0 = time.monotonic()
        try:
            r = _call()
        except httpx.HTTPStatusError as exc:
            raise ApiProviderError(
                f"openai API error {exc.response.status_code}: {redact_secrets(exc.response.text[:300])}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiProviderError(f"openai API request failed: {redact_secrets(str(exc))}") from exc
        duration_ms = int((time.monotonic() - t0) * 1000)
        # F26 (audit 2026-09-24): see the identical comment in AnthropicProvider.chat above.
        try:
            data = r.json()
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "") or ""
            usage = data.get("usage") or {}
            # F26 follow-up (SOL-REVIEW-2026-09-24): see the identical comment in
            # AnthropicProvider.chat above -- validated inside the boundary, not after it.
            if not isinstance(usage, dict):
                raise TypeError(f"usage field is a {type(usage).__name__}, not an object")
            # F26: see the identical comment in AnthropicProvider.chat above -- this is also the
            # provider whose `content` is a single dict lookup (`message.content`) with no
            # construction-side type check, so a non-string message content (e.g. a list of
            # multimodal blocks) used to reach `strip_code_fences()` unvalidated and raise
            # straight out of `chat()`.
            result = _finalize_result(
                provider="openai",
                content=content,
                model=mdl,
                duration_ms=duration_ms,
                prompt_chars=sum(len(m["content"]) for m in api_messages),
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
            )
        except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
            raise ApiProviderError(
                f"openai API returned a malformed response body: {redact_secrets(str(exc))[:200]}"
            ) from exc
        log.info("api_provider_call", provider="openai", model=mdl, duration_ms=duration_ms)
        return result


_API_PROVIDER_CLASSES: dict[str, type] = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "openai": OpenAIProvider,
}


def get_api_provider(kind: str, model: str | None = None, power: str | None = None) -> Any:
    """Factory: one of the three classes above, keyed by ``kind`` ("anthropic"/"gemini"/"openai")."""
    cls = _API_PROVIDER_CLASSES.get(kind)
    if cls is None:
        raise ValueError(f"unknown API provider kind: {kind!r}")
    return cls(model, power)
