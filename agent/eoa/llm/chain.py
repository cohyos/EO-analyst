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

from eoa.config import ChainEntryCfg, settings
from eoa.errors import (
    CliProviderError,
    DeadlineExceeded,
    LeaseLost,
    LLMOutputError,
    ProviderUnavailable,
    ResourceUnavailable,
)
from eoa.execution import checkpoint
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
    tools: list[dict[str, Any]] | None = None,
) -> tuple[ProviderResult, list[ChainAttempt]]:
    """Try ``chain`` in order; return the first successful ``ProviderResult`` plus every attempt
    made (in order), for the caller to fold into its own result/logging. Raises
    :class:`ChainExhausted` only if the local terminal entry itself fails (there is nothing left
    to fall back to at that point).

    ``tools`` (round 7, 2026-09-07): a *tool-calling* turn (the deep-search ReAct loop's
    search/read/finish tools) can only run on a leg that honours a caller-supplied tools schema.
    No CLI provider (claude/agy/codex `-p` calls) and no API provider in this codebase does --
    the API providers use tools internally for structured output only -- so such legs are
    skipped with a warning (a provider may opt in by exposing ``supports_tools = True``) and the
    turn runs on the local leg. Before this, `_dispatch_chain` dropped ``tools`` and returned
    ``tool_calls=[]`` from a prose answer, so every cloud-mode investigation looped without a
    single page read until its budget expired (golden jobs 47/48/70/86/91, round 7)."""
    attempts: list[ChainAttempt] = []
    fell_back_from: str | None = None
    # F02 (audit 2026-09-24): this is the single choke point every chain -- the role's own
    # configured chain, a dossier's `llm_leg`/`build_chain_with_leg_override` prepend, or any
    # other caller-supplied ``chain`` -- actually executes through, so it is where
    # ``allow_cloud=False`` must be enforced absolutely regardless of how ``chain`` was built.
    # ``effective_chain`` already does this when it is the one resolving the chain, but a
    # prepended override bypassed it entirely; filtering here closes that gap centrally instead
    # of chasing every call site that can hand in an explicit chain.
    if not settings().llm_providers.allow_cloud:
        chain = [e for e in chain if e.provider == "ollama"] or [ChainEntryCfg(provider="ollama")]
    if tools and not any(e.provider == "ollama" for e in chain):
        raise ProviderUnavailable(
            f"llm chain for role={role!r} has no tool-capable leg for a tool-calling turn "
            f"(legs: {[e.provider for e in chain]}); add the local 'ollama' terminal entry"
        )

    for i, entry in enumerate(chain):
        checkpoint()
        attempt_no = i + 1
        if entry.provider == "ollama":
            try:
                result = call_ollama()
            except (DeadlineExceeded, LeaseLost):
                raise
            except ResourceUnavailable as exc:
                if not any(e.provider != "ollama" for e in chain[i + 1 :]):
                    raise
                attempt = ChainAttempt(provider="ollama", model="", power=None, ok=False,
                                       error=str(exc)[:300], attempt_no=attempt_no,
                                       fell_back_from=fell_back_from)
                attempts.append(attempt)
                _record(role, attempt, batch_size)
                fell_back_from = "ollama"
                log.info("local_unavailable_using_cloud", role=role, reason=str(exc)[:200])
                continue
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
                if any(e.provider != "ollama" for e in chain[i + 1 :]):
                    fell_back_from = "ollama"
                    continue
                raise ChainExhausted(
                    f"llm chain for role={role!r}: local terminal entry failed: {exc}"
                ) from exc
            attempt = ChainAttempt(
                provider="ollama",
                model=result.model,
                power=None,
                ok=True,
                duration_ms=result.duration_ms,
                prompt_tokens=int(result.usage.get("prompt_tokens") or result.usage.get("input_tokens") or 0),
                completion_tokens=int(
                    result.usage.get("eval_tokens") or result.usage.get("output_tokens") or 0
                ),
                fell_back_from=fell_back_from,
                attempt_no=attempt_no,
            )
            attempts.append(attempt)
            _record(role, attempt, batch_size)
            return result, attempts

        try:
            provider = _build_provider(entry)
            if tools and not getattr(provider, "supports_tools", False):
                raise ProviderUnavailable(
                    f"{entry.provider} provider cannot run a tool-calling turn (no tools-schema support)"
                )
            if not provider.is_available():
                raise ProviderUnavailable(f"{entry.provider} provider unavailable (binary/key missing)")
            if tools:
                result = provider.chat(messages, model=entry.model, json_schema=json_schema, tools=tools)
            else:
                result = provider.chat(messages, model=entry.model, json_schema=json_schema)
            checkpoint()
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

    raise ChainExhausted(f"llm chain for role={role!r} exhausted: all configured providers failed")


def parse_leg(leg: str | None) -> ChainEntryCfg | None:
    """One ``"<provider>[:<model>][@<power>]"`` string -> a single :class:`ChainEntryCfg`, or
    ``None`` for ``"local"``/blank/``None`` (no override -- use the role's normal chain as-is).
    Mirrors the ``model@power`` split ``eoa.llm.ollama_client._dispatch_explicit_provider`` already
    uses for the interactive chat's per-question override, so the two "pick one cloud provider by
    string" entry points in this codebase (chat's picker, a dossier run's ``llm_leg``) parse
    identically. Used by :func:`build_chain_with_leg_override` (PD-cloud-tools, 2026-09-09)."""
    if not leg or leg == "local":
        return None
    provider, _, rest = leg.partition(":")
    model, _, power = rest.partition("@") if "@" in rest else (rest, "", "")
    return ChainEntryCfg(provider=provider, model=model or None, power=power or None)


def build_chain_with_leg_override(role: str, leg: str | None) -> list[ChainEntryCfg]:
    """PD-cloud-tools (2026-09-09): a dossier run's explicit ``llm_leg`` override (``POST
    /api/dossiers``'s optional field, threaded through ``eoa.dossier.plan``/``eoa.dossier.report``
    into ``eoa.search.deep_search.investigate()``'s ReAct turns and ``eoa.dossier.extract``'s
    structured extraction call) prepends ONE chain entry ahead of the role's normally-configured
    chain -- "try this leg first, then fall back to the configured chain (which itself always ends
    in local ollama, enforced by ``effective_chain``)" -- rather than replacing the chain outright,
    so a leg that is temporarily unavailable (CLI not logged in, a typo'd model id) still degrades
    to the existing chain instead of leaving the run with nothing. ``leg`` of ``None``/``""``/
    ``"local"`` returns the role's configured chain unchanged (:func:`parse_leg` returns ``None``
    for all three)."""
    default_chain = settings().llm_providers.effective_chain(role)
    entry = parse_leg(leg)
    if entry is None:
        return default_chain
    return [entry, *default_chain]


def _record(role: str, attempt: ChainAttempt, batch_size: int) -> None:
    """Best-effort ``llm_calls`` row for one chain attempt -- must never break the actual call."""
    try:
        from eoa.memory.relational import log_llm_call

        cost = estimate_cost_usd(
            attempt.provider, attempt.model, attempt.prompt_tokens, attempt.completion_tokens
        )
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
