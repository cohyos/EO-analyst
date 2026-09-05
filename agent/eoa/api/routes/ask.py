"""`POST /api/ask` -- RAG chat: retrieves relevant items and streams a cited Hebrew answer over SSE."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from eoa.api import services
from eoa.llm import ollama_client

log = structlog.get_logger(__name__)

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    # Q2-9: bounded so an oversized question can't be used to force an
    # unreasonably large retrieval/LLM-context payload.
    question: str = Field(..., max_length=4000)
    context_item_ids: list[int] = []
    context_entity_ids: list[int] = []
    history: list[dict[str, str]] = []
    provider: str | None = None  # U8: "ollama" | "agy[:<model>]" | "claude[:<model>]" | "codex[:<model>]"


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


@router.post("/ask")
async def ask(body: AskRequest) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        try:
            retrieved = await run_in_threadpool(
                services.ask_retrieve, body.question, body.context_item_ids, body.context_entity_ids
            )
            messages, citations = services.ask_build_messages(body.question, body.history, retrieved)
            yield _sse({"type": "citations", "items": citations})
            # U8: tell the UI up front which provider/model will answer (badge chip), before
            # the (possibly slow, for a cloud CLI) call even starts.
            provider_kind, provider_model = ollama_client.resolve_provider_info(body.provider)
            yield _sse({"type": "meta", "provider": provider_kind, "model": provider_model})
            # `chat_stream` is a synchronous generator over blocking HTTP reads; this is a
            # local, single-user deployment (see docs/CONVENTIONS.md), so driving it directly
            # inside the async generator (rather than off-loading to a thread) is an accepted
            # trade-off -- it blocks the event loop only for the duration of this one request.
            for chunk in ollama_client.chat_stream(
                "resident", messages, task="react", interactive=True, provider=body.provider
            ):
                yield _sse({"type": "token", "text": chunk})
        except Exception as exc:
            log.warning("ask.stream_failed", error=str(exc))
            yield _sse({"type": "error", "message": str(exc)})
        finally:
            yield _sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")
