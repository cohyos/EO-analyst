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
import os
import time
from typing import Any

import httpx
import structlog
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from eoa.config import settings
from eoa.errors import CliProviderError, ProviderUnavailable
from eoa.llm.providers.base import ProviderResult, strip_code_fences

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
            {"role": t["role"] if t["role"] == "assistant" else "user", "content": t["content"]} for t in turns
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
            with httpx.Client(timeout=timeout_s or _TIMEOUT_S) as c:
                r = c.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json=body,
                )
                r.raise_for_status()
                return r

        t0 = time.monotonic()
        try:
            r = _call()
        except httpx.HTTPStatusError as exc:
            raise ApiProviderError(
                f"anthropic API error {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiProviderError(f"anthropic API request failed: {exc}") from exc
        duration_ms = int((time.monotonic() - t0) * 1000)
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
        log.info("api_provider_call", provider="anthropic", model=mdl, duration_ms=duration_ms)
        return ProviderResult(
            content=strip_code_fences(content),
            model=mdl,
            provider="anthropic",
            duration_ms=duration_ms,
            prompt_chars=sum(len(t["content"]) for t in turns) + len(system),
            usage={
                "input_tokens": int(usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("output_tokens") or 0),
            },
        )


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
                r = c.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key})
                r.raise_for_status()
                names = [
                    m["name"].removeprefix("models/")
                    for m in r.json().get("models", [])
                    if "generateContent" in (m.get("supportedGenerationMethods") or [])
                ]
                return names or static
        except Exception as exc:  # live listing is best-effort; never break the picker
            log.warning("gemini_list_models_failed", error=str(exc)[:200])
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
            with httpx.Client(timeout=timeout_s or _TIMEOUT_S) as c:
                r = c.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent",
                    params={"key": key},
                    json=body,
                )
                r.raise_for_status()
                return r

        t0 = time.monotonic()
        try:
            r = _call()
        except httpx.HTTPStatusError as exc:
            raise ApiProviderError(f"gemini API error {exc.response.status_code}: {exc.response.text[:300]}") from exc
        except httpx.HTTPError as exc:
            raise ApiProviderError(f"gemini API request failed: {exc}") from exc
        duration_ms = int((time.monotonic() - t0) * 1000)
        data = r.json()
        content = ""
        for cand in data.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                content += part.get("text", "")
            if content:
                break
        usage = data.get("usageMetadata") or {}
        log.info("api_provider_call", provider="gemini", model=mdl, duration_ms=duration_ms)
        return ProviderResult(
            content=strip_code_fences(content),
            model=mdl,
            provider="gemini",
            duration_ms=duration_ms,
            prompt_chars=sum(len(t["content"]) for t in turns) + len(system),
            usage={
                "input_tokens": int(usage.get("promptTokenCount") or 0),
                "output_tokens": int(usage.get("candidatesTokenCount") or 0),
            },
        )


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
            with httpx.Client(timeout=timeout_s or _TIMEOUT_S) as c:
                r = c.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
                    json=body,
                )
                r.raise_for_status()
                return r

        t0 = time.monotonic()
        try:
            r = _call()
        except httpx.HTTPStatusError as exc:
            raise ApiProviderError(f"openai API error {exc.response.status_code}: {exc.response.text[:300]}") from exc
        except httpx.HTTPError as exc:
            raise ApiProviderError(f"openai API request failed: {exc}") from exc
        duration_ms = int((time.monotonic() - t0) * 1000)
        data = r.json()
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "") or ""
        usage = data.get("usage") or {}
        log.info("api_provider_call", provider="openai", model=mdl, duration_ms=duration_ms)
        return ProviderResult(
            content=strip_code_fences(content),
            model=mdl,
            provider="openai",
            duration_ms=duration_ms,
            prompt_chars=sum(len(m["content"]) for m in api_messages),
            usage={
                "input_tokens": int(usage.get("prompt_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or 0),
            },
        )


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
