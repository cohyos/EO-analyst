"""Shared types for interactive LLM providers -- see ``eoa.llm.providers.__init__`` for context."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ProviderResult:
    """One provider chat response, before it is adapted into ``eoa.llm.ollama_client.ChatResult``."""

    content: str
    model: str
    provider: str
    duration_ms: int = 0
    prompt_chars: int = 0
    usage: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Provider(Protocol):
    """A backend that can answer a chat request. Implemented by ``OllamaProvider`` (listing only
    -- the actual Ollama call path stays in ``ollama_client.chat``) and ``CliProvider``."""

    name: str

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> ProviderResult: ...

    def list_models(self) -> list[str]: ...

    def is_available(self) -> bool: ...


def strip_code_fences(text: str) -> str:
    """Remove a leading/trailing ``` fenced block, if any (cloud CLIs often wrap JSON in one)."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
    return t.strip()
