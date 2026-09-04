"""`POST /api/ask` -- RAG chat: retrieves relevant items and streams a cited Hebrew answer over SSE."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import structlog
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from eoa.api import services
from eoa.llm import ollama_client

log = structlog.get_logger(__name__)

router = APIRouter(tags=["ask"])


class AskRequest(BaseModel):
    question: str
    context_item_ids: list[int] = []
    context_entity_ids: list[int] = []
    history: list[dict[str, str]] = []


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/ask")
async def ask(body: AskRequest) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        try:
            retrieved = await run_in_threadpool(
                services.ask_retrieve, body.question, body.context_item_ids, body.context_entity_ids
            )
            messages, citations = services.ask_build_messages(body.question, body.history, retrieved)
            yield _sse({"type": "citations", "items": citations})
            # `chat_stream` is a synchronous generator over blocking HTTP reads; this is a
            # local, single-user deployment (see docs/CONVENTIONS.md), so driving it directly
            # inside the async generator (rather than off-loading to a thread) is an accepted
            # trade-off -- it blocks the event loop only for the duration of this one request.
            for chunk in ollama_client.chat_stream("resident", messages, task="react", interactive=True):
                yield _sse({"type": "token", "text": chunk})
        except Exception as exc:
            log.warning("ask.stream_failed", error=str(exc))
            yield _sse({"type": "error", "message": str(exc)})
        finally:
            yield _sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")
