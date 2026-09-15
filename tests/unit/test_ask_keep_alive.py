"""`routes.ask._keep_alive`: the whole-stream SSE keep-alive wrapper (2026-09-15, iPhone/WebKit
"Load failed"). Complements `test_ask_webkit_heartbeat.py`, which covers the per-pass helper.
"""

from __future__ import annotations

import asyncio

import pytest

from eoa.api.routes import ask as ask_route


async def _collect(agen, *, until_done: bool = True) -> list[str]:
    out: list[str] = []
    async for chunk in agen:
        out.append(chunk)
    return out


@pytest.mark.asyncio
async def test_keep_alive_passes_items_through_unchanged_and_ends_cleanly(monkeypatch):
    async def src():
        yield "data: a\n\n"
        yield "data: b\n\n"

    assert await _collect(ask_route._keep_alive(src())) == ["data: a\n\n", "data: b\n\n"]


@pytest.mark.asyncio
async def test_keep_alive_emits_heartbeat_comment_during_a_silent_gap(monkeypatch):
    monkeypatch.setattr(ask_route, "_HEARTBEAT_INTERVAL_S", 0.02)

    async def src():
        yield "data: first\n\n"
        await asyncio.sleep(0.09)  # silent gap of ~4 intervals
        yield "data: last\n\n"

    chunks = await _collect(ask_route._keep_alive(src()))
    assert chunks[0] == "data: first\n\n"
    assert chunks[-1] == "data: last\n\n"
    heartbeats = [c for c in chunks if c == ": heartbeat\n\n"]
    assert 2 <= len(heartbeats) <= 6
    # Every heartbeat is a bare SSE comment line: never a `data:` frame the client would parse.
    assert all(not c.startswith("data:") for c in heartbeats)


@pytest.mark.asyncio
async def test_keep_alive_propagates_source_exceptions(monkeypatch):
    async def src():
        yield "data: x\n\n"
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await _collect(ask_route._keep_alive(src()))
