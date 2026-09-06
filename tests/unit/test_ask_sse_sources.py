"""U11 (2026-09-06 answer-format rewrite): `/api/ask` now peels an optional trailing
`===SOURCES_JSON===\n{"source_notes": [...]}` block off the model's stream -- never forwarding it
to the client as `token` text -- and always emits one `sources` SSE event (citations enriched with
whatever per-source notes the model produced, or none) after the answer finishes streaming.

Run with: ``PYTHONPATH=agent .venv\\Scripts\\python -m pytest tests/unit/test_ask_sse_sources.py -q``
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from eoa.api.routes.ask import _parse_source_notes


class TestParseSourceNotes:
    def test_well_formed_block(self) -> None:
        buf = '\n{"source_notes": [{"n": 1, "note": "רלוונטי ישירות"}, {"n": 2, "note": "רקע כללי"}]}'
        assert _parse_source_notes(buf) == {1: "רלוונטי ישירות", 2: "רקע כללי"}

    def test_extra_prose_around_the_json_is_ignored(self) -> None:
        buf = 'הערה: \n{"source_notes": [{"n": 3, "note": "טוב"}]}\nסוף.'
        assert _parse_source_notes(buf) == {3: "טוב"}

    def test_malformed_json_returns_empty_without_raising(self) -> None:
        assert _parse_source_notes("{not json at all") == {}

    def test_no_json_at_all_returns_empty(self) -> None:
        assert _parse_source_notes("") == {}
        assert _parse_source_notes("   \n  ") == {}

    def test_entries_missing_n_or_with_blank_note_are_skipped(self) -> None:
        buf = json.dumps(
            {
                "source_notes": [
                    {"note": "אין n"},
                    {"n": 1, "note": "   "},
                    {"n": 2, "note": "תקין"},
                ]
            }
        )
        assert _parse_source_notes(buf) == {2: "תקין"}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _sse_events(body: str) -> list[dict]:
    events = []
    for chunk in body.split("\n\n"):
        line = next((ln for ln in chunk.split("\n") if ln.startswith("data:")), None)
        if not line:
            continue
        events.append(json.loads(line[len("data:") :].strip()))
    return events


CITATIONS = [
    {"n": 1, "item_id": 101, "title": "מקור א", "url": "https://a.test", "level": "red", "source_name": "Globes"},
    {"n": 2, "item_id": 102, "title": "מקור ב", "url": "https://b.test", "level": "yellow", "source_name": "Ynet"},
]


class _FakeChatResult:
    """Minimal stand-in for `ollama_client.ChatResult` -- only `.content` is read by the round-2
    citation-repair pass (`eoa.api.routes.ask._run_citation_repair`)."""

    def __init__(self, content: str = "") -> None:
        self.content = content


def _mock_ask(
    monkeypatch: pytest.MonkeyPatch, chunks: list[str], *, repair_content: str = ""
) -> None:
    from eoa.api import services
    from eoa.llm import ollama_client

    monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(services, "ask_build_messages", lambda *a, **k: ([], CITATIONS))
    monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
    monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: iter(chunks))
    # Round 2 (docs/qa/loop/round_2_chat_fixes.md): most of this file's fixture answers carry no
    # `[n]` and CITATIONS is non-empty, which now triggers a one-shot corrective `chat()` call
    # (`ask._run_citation_repair`) -- stub it so these tests never hit a real Ollama server.
    monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: _FakeChatResult(repair_content))


class TestAskSseSources:
    def test_sentinel_and_json_never_leak_into_token_events(self, client: TestClient, monkeypatch) -> None:
        _mock_ask(
            monkeypatch,
            [
                "תשובה קצרה [1].",
                "===SOURCES_JSON===",
                '{"source_notes": [{"n": 1, "note": "מדויק"}]}',
            ],
        )
        r = client.post("/api/ask", json={"question": "שאלה"})
        events = _sse_events(r.text)
        token_text = "".join(e["text"] for e in events if e["type"] == "token")
        assert token_text == "תשובה קצרה [1]."
        assert "SOURCES_JSON" not in token_text
        assert "source_notes" not in token_text

    def test_sources_event_carries_the_parsed_note_merged_onto_the_citation(
        self, client: TestClient, monkeypatch
    ) -> None:
        _mock_ask(
            monkeypatch,
            ["תשובה [1].", "===SOURCES_JSON===", '{"source_notes": [{"n": 1, "note": "רלוונטי"}]}'],
        )
        r = client.post("/api/ask", json={"question": "שאלה"})
        events = _sse_events(r.text)
        sources_events = [e for e in events if e["type"] == "sources"]
        assert len(sources_events) == 1
        items = sources_events[0]["items"]
        assert items[0]["item_id"] == 101
        assert items[0]["note"] == "רלוונטי"
        assert items[0]["level"] == "red"
        assert items[0]["source_name"] == "Globes"
        assert items[1]["note"] is None  # citation 2 had no matching source_note

    def test_sentinel_split_across_chunk_boundaries_is_still_detected(
        self, client: TestClient, monkeypatch
    ) -> None:
        # split the sentinel itself mid-token, across two separate stream chunks
        _mock_ask(
            monkeypatch,
            ["תשובה.", "===SOURCES_J", 'SON===\n{"source_notes": [{"n": 2, "note": "משני"}]}'],
        )
        r = client.post("/api/ask", json={"question": "שאלה"})
        events = _sse_events(r.text)
        token_text = "".join(e["text"] for e in events if e["type"] == "token")
        assert token_text == "תשובה."
        sources_items = next(e for e in events if e["type"] == "sources")["items"]
        assert sources_items[1]["note"] == "משני"

    def test_a_model_that_never_emits_the_sentinel_still_gets_a_sources_event(
        self, client: TestClient, monkeypatch
    ) -> None:
        _mock_ask(monkeypatch, ["תשובה רגילה בלי שום דבר מיוחד."])
        r = client.post("/api/ask", json={"question": "שאלה"})
        events = _sse_events(r.text)
        token_text = "".join(e["text"] for e in events if e["type"] == "token")
        assert token_text == "תשובה רגילה בלי שום דבר מיוחד."
        sources_events = [e for e in events if e["type"] == "sources"]
        assert len(sources_events) == 1
        assert all(item["note"] is None for item in sources_events[0]["items"])

    def test_done_event_always_sent_last(self, client: TestClient, monkeypatch) -> None:
        _mock_ask(monkeypatch, ["תשובה."])
        r = client.post("/api/ask", json={"question": "שאלה"})
        events = _sse_events(r.text)
        assert events[-1]["type"] == "done"
