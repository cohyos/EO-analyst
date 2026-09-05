"""Interactive-only LLM providers (U8, docs/adr/005-cloud-llm-cli.md).

``eoa.llm.ollama_client.chat`` / ``chat_structured`` are still the only functions the rest of
the codebase calls -- this package is dispatched to *from inside* them (never imported at
their module top-level, to avoid a cycle) when the resolved ``provider`` is not ``"ollama"``.

A provider string looks like ``"ollama"``, ``"agy:gemini-3.8-flash-medium"``, ``"claude:claude-sonnet-5"``
or ``"codex"`` (bare -- uses the provider's default model). ``parse_provider`` splits that; the
CLI kinds (``agy`` / ``claude`` / ``codex``) are implemented by :class:`eoa.llm.providers.cli.CliProvider`.
"""

from __future__ import annotations

CLOUD_KINDS = ("agy", "claude", "codex")


def parse_provider(provider: str) -> tuple[str, str | None]:
    """Split ``"kind[:model]"`` into ``(kind, model_or_None)``."""
    kind, sep, model = provider.partition(":")
    return kind, (model or None) if sep else None
