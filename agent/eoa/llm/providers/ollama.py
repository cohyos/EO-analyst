"""Listing/availability adapter for the local Ollama backend.

The real interactive Ollama call path stays exactly where it always was --
``eoa.llm.ollama_client.chat`` -- because it resolves a config *role*
(resident/light/hebrew_editor/...) through the resource gate, not a raw model
id. This class exists only so ``GET /api/llm/providers`` can list Ollama
alongside the cloud CLIs with the same shape.
"""

from __future__ import annotations

from typing import Any

from eoa.config import settings
from eoa.llm.providers.base import ProviderResult


class OllamaProvider:
    name = "ollama"

    def is_available(self) -> bool:
        from eoa.llm.ollama_client import ping

        return ping()

    def list_models(self) -> list[str]:
        """Config roles that resolve to an Ollama model (the picker shows roles, not raw model ids)."""
        s = settings()
        return [
            role
            for role, key in s.models.items()
            if key and s.registry.models.get(key, None) and s.registry.models[key].runtime == "ollama"
        ]

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        json_schema: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> ProviderResult:
        """Not used by the dispatcher (see module docstring) -- provided only for Protocol conformance."""
        from eoa.llm.ollama_client import chat as ollama_chat

        role = model or "light"
        res = ollama_chat(role, messages, format_schema=json_schema, interactive=True)
        return ProviderResult(
            content=res.content,
            model=res.model,
            provider="ollama",
            duration_ms=res.duration_ms,
            prompt_chars=sum(len(m.get("content", "")) for m in messages),
            usage={"prompt_tokens": res.prompt_tokens, "eval_tokens": res.eval_tokens},
        )
