"""Fallback chain execution (U8-ה, docs/adr/005-cloud-llm-cli.md Revision 2026-09-06).

``eoa.config.Settings.llm_providers.effective_chain(role)`` resolves a role to an ordered list of
``ChainEntryCfg`` (provider/model/power) that always ends in a local Ollama entry. This module
tries each entry in turn, falling back to the next one when: the provider is unavailable (CLI
binary missing from PATH, or an API key missing), an HTTP 401/403/429/5xx survives the
provider's own retries, a timeout, a schema-validation failure survives ``chat_structured``'s own
corrective retry, or a CLI process exits non-zero. Every attempt -- successful or not -- is
recorded in ``llm_calls`` (migration 0009: ``attempt_no``, ``fell_back_from``, ``prompt_tokens``,
``completion_tokens``, ``est_cost_usd``, ``batch_size``, plus ``role``/``error`` for the summary
endpoint).

The local leg is never re-implemented here: the caller (``eoa.llm.ollama_client``) passes in a
zero-argument ``call_ollama`` callable that runs its own resource-gate/num_ctx path and adapts the
result into a ``ProviderResult`` -- this module only ever calls it, never Ollama's HTTP API
directly (``docs/CONVENTIONS.md`` rule #1).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from eoa.config import ChainEntryCfg
from eoa.errors import CliProviderError, LLMOutputError, ProviderUnavailable
from eoa.llm.cost import estimate_cost_usd
from eoa.llm.providers.base import ProviderResult

log = structlog.get_logger(__name__)

#: exceptions that mean "move on to the next chain entry" rather than "the whole call failed".
#: ``ApiProviderError``/``CliProviderError`` cover HTTP failures-after-retry, non-zero CLI exits
#: and timeouts (both subprocess and httpx timeouts are wrapped into one of these by the provider
#: classes); ``ProviderUnavailable`` covers a missing binary/key; ``LLMOutputError`` covers a
#: schema-validation failure that survived ``chat_structured``'s own corrective retry.
FALLBACK_EXCEPTIONS: tuple[type[Exception], ...] = (ProviderUnavailable, CliProviderError, LLMOutputError)


@dataclass
class ChainAttempt:
    provider: str
    model: str
    power: str | None
    ok: bool
    error: str | None = None
    duration_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    fell_back_from: str | None = None
    attempt_no: int = 1


class ChainExhausted(LLMOutputError):
    """Every entry in the chain failed, including the local terminal one."""


def _build_provider(entry: ChainEntryCfg) -> Any:
    """One provider instance for a non-``"ollama"`` chain entry (CLI or direct-API)."""
    if entry.provider in ("agy", "claude", "codex"):
        from eoa.llm.providers.cli import CliProvider

        return CliProvider(entry.provider, entry.model, entry.power)
    if entry.provider in ("anthropic", "gemini", "openai"):
        from eoa.llm.providers.api import get_api_provider

        return get_api_provider(entry.provider, entry.model, entry.power)
    raise ValueError(f"unknown chain provider: {entry.provider!r}")


def run_chain(
    role: str,
    chain: list[ChainEntryCfg],
    call_ollama: Callable[[], ProviderResult],
    *,
    messages: list[dict[str, Any]],
    json_schema: dict[str, Any] | None = None,
    batch_size: int = 1,
) -> tuple[ProviderResult, list[ChainAttempt]]:
    """Try ``chain`` in order; return the first successful ``ProviderResult`` plus every attempt
    made (in order), for the caller to fold into its own result/logging. Raises
    :class:`ChainExhausted` only if the local terminal entry itself fails (there is nothing left
    to fall back to at that point)."""
    attempts: list[ChainAttempt] = []
    fell_back_from: str | None = None

    for i, entry in enumerate(chain):
        attempt_no = i + 1
        if entry.provider == "ollama":
            try:
                result = call_ollama()
            except Exception as exc:  # the local leg failing is a hard failure -- nothing left
                attempt = ChainAttempt(
                    provider="ollama",
                    model="",
                    power=None,
                    ok=False,
                    error=str(exc)[:300],
                    fell_back_from=fell_back_from,
                    attempt_no=attempt_no,
                )
                attempts.append(attempt)
                _record(role, attempt, batch_size)
                raise ChainExhausted(f"llm chain for role={role!r}: local terminal entry failed: {exc}") from exc
            attempt = ChainAttempt(
                provider="ollama",
                model=result.model,
                power=None,
                ok=True,
                duration_ms=result.duration_ms,
                prompt_tokens=int(result.usage.get("prompt_tokens") or result.usage.get("input_tokens") or 0),
                completion_tokens=int(result.usage.get("eval_tokens") or result.usage.get("output_tokens") or 0),
                fell_back_from=fell_back_from,
                attempt_no=attempt_no,
            )
            attempts.append(attempt)
            _record(role, attempt, batch_size)
            return result, attempts

        try:
            provider = _build_provider(entry)
            if not provider.is_available():
                raise ProviderUnavailable(f"{entry.provider} provider unavailable (binary/key missing)")
            result = provider.chat(messages, model=entry.model, json_schema=json_schema)
        except FALLBACK_EXCEPTIONS as exc:
            attempt = ChainAttempt(
                provider=entry.provider,
                model=entry.model or "",
                power=entry.power,
                ok=False,
                error=str(exc)[:300],
                fell_back_from=fell_back_from,
                attempt_no=attempt_no,
            )
            attempts.append(attempt)
            _record(role, attempt, batch_size)
            fell_back_from = entry.provider
            log.warning("llm_chain_fallback", role=role, provider=entry.provider, error=str(exc)[:200])
            continue

        attempt = ChainAttempt(
            provider=entry.provider,
            model=result.model,
            power=entry.power,
            ok=True,
            duration_ms=result.duration_ms,
            prompt_tokens=int(result.usage.get("input_tokens") or result.usage.get("prompt_tokens") or 0),
            completion_tokens=int(result.usage.get("output_tokens") or result.usage.get("eval_tokens") or 0),
            fell_back_from=fell_back_from,
            attempt_no=attempt_no,
        )
        attempts.append(attempt)
        _record(role, attempt, batch_size)
        return result, attempts

    # Unreachable when `chain` came from `effective_chain()` (always ollama-terminated) -- kept
    # as a defensive floor for a hand-built chain (e.g. a unit test) that omits one.
    raise ChainExhausted(f"llm chain for role={role!r} exhausted with no local terminal entry")


def _record(role: str, attempt: ChainAttempt, batch_size: int) -> None:
    """Best-effort ``llm_calls`` row for one chain attempt -- must never break the actual call."""
    try:
        from eoa.memory.relational import log_llm_call

        cost = estimate_cost_usd(attempt.provider, attempt.model, attempt.prompt_tokens, attempt.completion_tokens)
        log_llm_call(
            provider=attempt.provider,
            model=attempt.model or "(unavailable)",
            prompt_chars=0,
            duration_ms=attempt.duration_ms,
            attempt_no=attempt.attempt_no,
            fell_back_from=attempt.fell_back_from,
            prompt_tokens=attempt.prompt_tokens,
            completion_tokens=attempt.completion_tokens,
            est_cost_usd=cost,
            batch_size=batch_size,
            role=role,
            error=attempt.error,
        )
    except Exception as exc:
        log.warning("llm_call_log_failed", provider=attempt.provider, error=str(exc)[:200])
