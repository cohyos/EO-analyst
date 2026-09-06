"""`POST /api/ask` -- RAG chat: retrieves relevant items and streams a cited Hebrew answer over SSE."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from eoa.api import services
from eoa.llm import ollama_client

log = structlog.get_logger(__name__)

router = APIRouter(tags=["ask"])

# U11 (2026-09-06 answer-format rewrite): `ask_answer_format.md` instructs the model to emit this
# exact sentinel, on its own line, after the full analyst answer and before an optional
# `{"source_notes": [...]}` JSON block -- the model's own per-source relevance notes, which belong
# in the UI's "מקורות" footer, never inline in the answer body. This is never shown to the user as
# text: the streaming loop below holds back only as much of the tail as could still be the start
# of this sentinel, so ordinary answer text keeps streaming token-by-token with no added latency,
# and only text at/after a confirmed sentinel match is diverted into `sources_buf` instead of a
# `token` event.
_SOURCES_SENTINEL = "===SOURCES_JSON==="


def _parse_source_notes(buf: str) -> dict[int, str]:
    """Best-effort ``{n: note}`` from the JSON blob the model wrote after the sentinel.

    Never raises -- a model that gets the format wrong (missing block, malformed JSON, wrong
    shape) just means the sources footer has no notes; it must never break the answer itself.
    """
    match = re.search(r"\{.*\}", buf, re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return {}
    notes: dict[int, str] = {}
    for entry in data.get("source_notes") or []:
        if not isinstance(entry, dict) or "n" not in entry:
            continue
        try:
            n = int(entry["n"])
        except (TypeError, ValueError):
            continue
        note = entry.get("note")
        if isinstance(note, str) and note.strip():
            notes[n] = note.strip()
    return notes


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
            # U11: hold back at most `len(_SOURCES_SENTINEL) - 1` trailing characters of `pending`
            # at any time -- that's the most that could still turn into the sentinel once the next
            # chunk arrives, so ordinary text streams through with no perceptible delay. Once the
            # sentinel is confirmed, everything from there on is the (never streamed to the
            # client) source-notes JSON block instead of answer text.
            pending = ""
            in_sources = False
            sources_buf = ""
            for chunk in ollama_client.chat_stream(
                "resident", messages, task="react", interactive=True, provider=body.provider
            ):
                if in_sources:
                    sources_buf += chunk
                    continue
                pending += chunk
                idx = pending.find(_SOURCES_SENTINEL)
                if idx != -1:
                    if idx > 0:
                        yield _sse({"type": "token", "text": pending[:idx]})
                    in_sources = True
                    sources_buf = pending[idx + len(_SOURCES_SENTINEL) :]
                    pending = ""
                    continue
                safe_len = max(0, len(pending) - (len(_SOURCES_SENTINEL) - 1))
                if safe_len > 0:
                    yield _sse({"type": "token", "text": pending[:safe_len]})
                    pending = pending[safe_len:]
            if pending:
                yield _sse({"type": "token", "text": pending})

            notes = _parse_source_notes(sources_buf) if in_sources else {}
            sources: list[dict[str, Any]] = [{**c, "note": notes.get(c["n"])} for c in citations]
            yield _sse({"type": "sources", "items": sources})
        except Exception as exc:
            log.warning("ask.stream_failed", error=str(exc))
            yield _sse({"type": "error", "message": str(exc)})
        finally:
            yield _sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")
