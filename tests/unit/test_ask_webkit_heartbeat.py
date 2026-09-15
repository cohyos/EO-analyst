"""iPhone/WebKit "Load failed" investigation (2026-09-15).

Live WebKit repro (Playwright `devices["iPhone 14"]`, `e2e/scripts/ask-repro.spec.ts`, throwaway)
against the running `/api/ask` on port 8765: response headers land fine (200, `text/event-stream`,
chunked, ~9s), tokens stream, then WebKit's own network stack reports `errorText: "Timeout was
reached"` at t=74895ms -- ~66s after the response started, with zero bytes in between. Safari
surfaces the identical failure to `fetch()` as a bare `TypeError: Load failed` (matches the user's
screenshot). A same-question Chromium run over the identical live backend finished cleanly
(`requestfinished`) at t=67305ms with no error at all -- proving the answer itself is not slow or
broken, only WebKit's own ~60s idle-network timeout, unrelated to the client's own
`AbortController` (`web/src/api/real.ts`'s `askStream` never sets one; it only aborts on the user's
"עצור" button -- see `web/src/hooks/useAskChat.ts`'s `stop`).

Root cause: after the last `token` event, `routes.ask.ask`'s `gen()` runs the citation-repair pass
and (config-gated, `ask.entailment_check`, default `true` in `config/config.yaml`) the
entailment-filter pass, each a single blocking LLM call -- `entailment_filter`'s own
`fast_chain_timeout_s=60.0` alone can eat WebKit's entire idle budget -- with no SSE bytes emitted
meanwhile. Fixed in `routes.ask` by running each of those two calls as a background `asyncio.Task`
via the new `_stream_heartbeats_while` helper, which yields a `: heartbeat\n\n` SSE comment frame
(ignored by the client's own `askStream` parser -- it only looks for lines starting with `data:`,
see `web/src/api/real.ts`) every `_HEARTBEAT_INTERVAL_S` seconds the task is still pending, keeping
bytes flowing over the wire so WebKit's idle timer never expires.

NEEDS AN API RESTART to take effect (the live uvicorn process on 8765 was not restarted by this
investigation, per the task's hard rules) -- verified here by unit test only. To confirm on a real
iPhone after the restart: open the app over the Tailscale HTTPS URL, ask a question in "שאל את
האנליסט" whose retrieval yields at least one citation, and confirm the answer completes instead of
showing "Load failed" (watch Settings > Safari > Advanced > Web Inspector on a connected Mac for
the exact same `requestfailed`/timing signal this file's `e2e/scripts/ask-repro.spec.ts` captured,
or just time it against the historical ~65-75s failure point).

Run with:
``set -a; . runtime/eoa.env; set +a; .venv/Scripts/python.exe -m pytest tests/unit/test_ask_webkit_heartbeat.py -q -p no:cacheprovider``
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from eoa.api import ask_grounding, services
from eoa.api.routes import ask as ask_route

# ---------------------------------------------------------------------------------------------
# 1. `_stream_heartbeats_while` in isolation -- fast, no server, no real 15s wait (interval patched)
# ---------------------------------------------------------------------------------------------


class TestStreamHeartbeatsWhile:
    async def _collect(self, task: asyncio.Task[str]) -> list[str]:
        return [frame async for frame in ask_route._stream_heartbeats_while(task)]

    def test_yields_heartbeat_frames_while_the_task_is_pending_then_stops(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ask_route, "_HEARTBEAT_INTERVAL_S", 0.05)

        async def _slow() -> str:
            await asyncio.sleep(0.22)
            return "done"

        async def _run() -> tuple[list[str], str]:
            task = asyncio.ensure_future(_slow())
            frames = await self._collect(task)
            return frames, task.result()

        frames, result = asyncio.run(_run())
        assert result == "done"
        # ~0.22s of pending time at a 0.05s interval -> at least a couple of heartbeats, and every
        # frame emitted must be the exact ignorable SSE comment frame, nothing else.
        assert len(frames) >= 2
        assert all(f == ": heartbeat\n\n" for f in frames)

    def test_yields_nothing_when_the_task_is_already_done(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ask_route, "_HEARTBEAT_INTERVAL_S", 5.0)

        async def _run() -> list[str]:
            task = asyncio.ensure_future(asyncio.sleep(0, result="x"))
            await task  # already finished before the generator even starts polling
            return await self._collect(task)

        assert asyncio.run(_run()) == []


# ---------------------------------------------------------------------------------------------
# 2. End-to-end through the live `/api/ask` route: heartbeats actually reach the SSE body during a
#    slow entailment-filter pass, in between the last `token` and the final `answer_final`.
# ---------------------------------------------------------------------------------------------


class _FakeChatResult:
    def __init__(self, content: str = "") -> None:
        self.content = content


class _FakeAskCfg:
    entailment_check = True
    entailment_max_claims = 6


class _FakeSettings:
    ask = _FakeAskCfg()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _sse_raw_events(body: str) -> list[str]:
    """Every `\\n\\n`-delimited SSE frame's raw text, unparsed -- unlike the other round tests'
    `_sse_events` (which drops any frame with no `data:` line), this keeps `: heartbeat` comment
    frames visible so the test can assert on them directly."""
    return [chunk for chunk in body.split("\n\n") if chunk]


def _row(id: int, **kw: Any) -> dict[str, Any]:
    base = {
        "id": id,
        "title": "כותרת",
        "url": "https://example.com",
        "clean_text": "טקסט",
        "summary_he": "תקציר",
        "key_facts": [],
        "level": "yellow",
        "source_name": "מקור",
        "report_kind": None,
        "entities_mentioned": [],
        "_is_context": False,
    }
    base.update(kw)
    return base


class TestEndToEndHeartbeatsDuringSlowEntailmentPass:
    def test_heartbeat_frames_appear_between_the_last_token_and_answer_final(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eoa.llm import ollama_client

        monkeypatch.setattr(ask_route, "_HEARTBEAT_INTERVAL_S", 0.03)
        monkeypatch.setattr(ask_route, "settings", lambda: _FakeSettings())

        rows = [
            _row(
                1,
                title="XM30 program funding",
                clean_text="The XM30 program is valued at $1.53bn, involving Rheinmetall.",
                entities_mentioned=["Rheinmetall"],
            )
        ]
        chunk = "התוכנית של XM30 מוערכת בכ-1.53 מיליארד דולר [1]."

        monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: rows)
        monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
        monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: iter([chunk]))
        monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: _FakeChatResult(""))

        def _fake_structured(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            # The slow call this fix targets -- sleeps long enough, at the patched 0.03s heartbeat
            # interval, for several heartbeat frames to land while it is still pending.
            time.sleep(0.25)
            return ask_grounding._EntailmentResponse(verdicts=[])

        monkeypatch.setattr(ollama_client, "chat_structured", _fake_structured)

        r = client.post("/api/ask", json={"question": "כמה שווה תוכנית XM30?"})
        raw_frames = _sse_raw_events(r.text)
        heartbeat_idxs = [i for i, f in enumerate(raw_frames) if f == ": heartbeat"]
        final_idxs = [
            i
            for i, f in enumerate(raw_frames)
            if f.startswith("data:") and json.loads(f[len("data:") :].strip()).get("type") == "answer_final"
        ]

        assert heartbeat_idxs, "expected at least one heartbeat frame during the slow entailment pass"
        assert final_idxs, "answer_final must still be emitted exactly once"
        # Heartbeats belong strictly before the final answer -- proves they filled the real silent
        # gap identified live, not some unrelated point in the stream.
        assert max(heartbeat_idxs) < min(final_idxs)
