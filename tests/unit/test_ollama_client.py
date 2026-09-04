"""Tests for eoa.llm.ollama_client — Ollama integration and LLM calls."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import respx

from eoa.llm.ollama_client import (
    _strip_fences,
    chat,
    chat_structured,
    embed,
    wrap_data,
)
from eoa.llm.schemas.analysis import TriageOut
from eoa.errors import LLMOutputError


class TestWrapData:
    """Test the wrap_data function for neutralizing delimiter characters."""

    def test_wrap_data_adds_delimiters(self):
        """wrap_data() wraps text with DATA markers."""
        text = "Some fetched content"
        wrapped = wrap_data(text, item_id=123, src="https://example.com")
        assert "<<<DATA id=123" in wrapped
        assert "Some fetched content" in wrapped
        assert "<<<END DATA>>>" in wrapped

    def test_wrap_data_neutralizes_open_fence(self):
        """wrap_data() replaces <<< with safe variant."""
        text = "This has <<< inside it"
        wrapped = wrap_data(text, item_id=1)
        # Should have zero-width space (or similar) to break the pattern
        assert "<<<" not in wrapped or "<<​<" in wrapped

    def test_wrap_data_neutralizes_close_fence(self):
        """wrap_data() replaces >>> with safe variant."""
        text = "This has >>> inside it"
        wrapped = wrap_data(text, item_id=1)
        assert ">>>" not in wrapped or ">​>>" in wrapped

    def test_wrap_data_round_trip(self):
        """Wrapped data can be safely embedded in prompts."""
        original = "Attack with <<< injection >>> payload"
        wrapped = wrap_data(original, item_id=999)
        # The wrapped version should NOT contain the dangerous patterns
        # (They should be neutralized)
        dangerous_patterns = [
            s for s in [wrapped]
            if "<<<" in s and "injection" in s.split("<<<")[1]
        ]
        # If dangerous_patterns is non-empty, the injection is NOT neutralized
        # But our wrap_data should neutralize it
        assert wrapped.count("<<<") == 1  # Only the DATA opener


class TestStripFences:
    """Test the _strip_fences function."""

    def test_strip_fences_removes_markdown_code_blocks(self):
        """_strip_fences() removes ```json fences."""
        text = "```json\n{\"key\": \"value\"}\n```"
        stripped = _strip_fences(text)
        assert stripped == '{"key": "value"}'

    def test_strip_fences_handles_no_language(self):
        """_strip_fences() handles ``` without language."""
        text = "```\n{\"data\": true}\n```"
        stripped = _strip_fences(text)
        assert stripped == '{"data": true}'

    def test_strip_fences_handles_incomplete_fences(self):
        """_strip_fences() handles missing closing fence."""
        text = "```json\n{\"incomplete\": true}"
        stripped = _strip_fences(text)
        assert "{" in stripped

    def test_strip_fences_no_fences_returns_unchanged(self):
        """_strip_fences() returns text unchanged if no fences."""
        text = '{"already": "json"}'
        stripped = _strip_fences(text)
        assert stripped == text

    def test_strip_fences_whitespace_handling(self):
        """_strip_fences() trims surrounding whitespace."""
        text = "  ```\nvalue\n```  \n"
        stripped = _strip_fences(text)
        assert stripped == "value"


class TestChat:
    """Test the chat() function with mocked HTTP."""

    @respx.mock
    def test_chat_parses_response(self, monkeypatch):
        """chat() parses response and returns ChatResult."""
        monkeypatch.setattr("eoa.config.settings().ollama_url", "http://localhost:11434")
        monkeypatch.setattr("eoa.resources.gate.gate")

        # Mock the gate
        mock_gate = MagicMock()
        mock_gate.acquire.return_value = MagicMock(
            ollama="gemma4:12b",
            est_vram_mb=8200,
            key="resident",
        )
        monkeypatch.setattr("eoa.resources.gate.gate", lambda: mock_gate)

        respx.post("http://localhost:11434/api/chat").mock(
            return_value=respx.Response(
                200,
                json={
                    "message": {"content": "Hello there", "tool_calls": []},
                    "eval_count": 10,
                    "prompt_eval_count": 20,
                },
            )
        )

        result = chat("resident", [{"role": "user", "content": "Hi"}])
        assert result.content == "Hello there"
        assert result.eval_tokens == 10
        assert result.prompt_tokens == 20

    @respx.mock
    def test_chat_parses_tool_calls(self, monkeypatch):
        """chat() extracts tool_calls from response."""
        mock_gate = MagicMock()
        mock_gate.acquire.return_value = MagicMock(ollama="gemma4:12b", est_vram_mb=8200)
        monkeypatch.setattr("eoa.resources.gate.gate", lambda: mock_gate)

        tool_call = {
            "function": {"name": "search", "arguments": '{"query": "test"}'},
        }
        respx.post("http://localhost:11434/api/chat").mock(
            return_value=respx.Response(
                200,
                json={
                    "message": {"content": "", "tool_calls": [tool_call]},
                    "eval_count": 5,
                    "prompt_eval_count": 15,
                },
            )
        )

        result = chat("resident", [{"role": "user", "content": "call search"}])
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["function"]["name"] == "search"


class TestChatStructured:
    """Test the chat_structured() function with schema validation."""

    @respx.mock
    def test_chat_structured_validates_and_returns_model(self, monkeypatch):
        """chat_structured() validates response against schema and returns model instance."""
        mock_gate = MagicMock()
        mock_gate.acquire.return_value = MagicMock(ollama="gemma4:12b", est_vram_mb=8200)
        monkeypatch.setattr("eoa.resources.gate.gate", lambda: mock_gate)

        json_response = '{"score": 7, "level": "red", "reason_he": "סיבה", "needs_deep_search": false, "deep_search_question": null}'
        respx.post("http://localhost:11434/api/chat").mock(
            return_value=respx.Response(
                200,
                json={
                    "message": {"content": json_response, "tool_calls": []},
                    "eval_count": 10,
                    "prompt_eval_count": 20,
                },
            )
        )

        result = chat_structured("resident", TriageOut, [{"role": "user", "content": "triage"}])
        assert isinstance(result, TriageOut)
        assert result.score == 7

    @respx.mock
    def test_chat_structured_retries_on_invalid_json(self, monkeypatch):
        """chat_structured() retries once on invalid JSON."""
        mock_gate = MagicMock()
        mock_gate.acquire.return_value = MagicMock(ollama="gemma4:12b", est_vram_mb=8200)
        monkeypatch.setattr("eoa.resources.gate.gate", lambda: mock_gate)

        valid_response = '{"score": 8, "level": "red", "reason_he": "טוב", "needs_deep_search": false}'
        respx.post("http://localhost:11434/api/chat").mock(
            side_effect=[
                respx.Response(
                    200,
                    json={
                        "message": {"content": "{invalid json", "tool_calls": []},
                        "eval_count": 5,
                        "prompt_eval_count": 15,
                    },
                ),
                respx.Response(
                    200,
                    json={
                        "message": {"content": valid_response, "tool_calls": []},
                        "eval_count": 10,
                        "prompt_eval_count": 20,
                    },
                ),
            ]
        )

        result = chat_structured("resident", TriageOut, [{"role": "user", "content": "triage"}])
        assert result.score == 8

    @respx.mock
    def test_chat_structured_raises_after_retry_fails(self, monkeypatch):
        """chat_structured() raises LLMOutputError if validation fails twice."""
        mock_gate = MagicMock()
        mock_gate.acquire.return_value = MagicMock(ollama="gemma4:12b", est_vram_mb=8200)
        monkeypatch.setattr("eoa.resources.gate.gate", lambda: mock_gate)

        respx.post("http://localhost:11434/api/chat").mock(
            return_value=respx.Response(
                200,
                json={
                    "message": {"content": '{"invalid": "schema"}', "tool_calls": []},
                    "eval_count": 5,
                    "prompt_eval_count": 15,
                },
            )
        )

        with pytest.raises(LLMOutputError, match="schema validation failed"):
            chat_structured("resident", TriageOut, [{"role": "user", "content": "triage"}])

    @respx.mock
    def test_chat_structured_strips_code_fences(self, monkeypatch):
        """chat_structured() strips markdown fences before validation."""
        mock_gate = MagicMock()
        mock_gate.acquire.return_value = MagicMock(ollama="gemma4:12b", est_vram_mb=8200)
        monkeypatch.setattr("eoa.resources.gate.gate", lambda: mock_gate)

        json_response = '```json\n{"score": 9, "level": "red", "reason_he": "excellent", "needs_deep_search": true, "deep_search_question": "why"}\n```'
        respx.post("http://localhost:11434/api/chat").mock(
            return_value=respx.Response(
                200,
                json={
                    "message": {"content": json_response, "tool_calls": []},
                    "eval_count": 10,
                    "prompt_eval_count": 20,
                },
            )
        )

        result = chat_structured("resident", TriageOut, [{"role": "user", "content": "triage"}])
        assert result.score == 9


class TestEmbed:
    """Test the embed() function."""

    @respx.mock
    def test_embed_returns_vectors(self, monkeypatch):
        """embed() returns list of embedding vectors."""
        mock_gate = MagicMock()
        mock_gate.acquire.return_value = MagicMock(
            ollama="multilingual-e5-large",
            est_vram_mb=1300,
        )
        monkeypatch.setattr("eoa.resources.gate.gate", lambda: mock_gate)

        embeddings = [
            [0.1, 0.2, 0.3],
            [0.4, 0.5, 0.6],
        ]
        respx.post("http://localhost:11434/api/embed").mock(
            return_value=respx.Response(
                200,
                json={"embeddings": embeddings},
            )
        )

        result = embed(["text1", "text2"])
        assert len(result) == 2
        assert result[0] == [0.1, 0.2, 0.3]

    @respx.mock
    def test_embed_handles_empty_texts(self, monkeypatch):
        """embed() returns empty list for empty input."""
        mock_gate = MagicMock()
        mock_gate.acquire.return_value = MagicMock(ollama="multilingual-e5-large")
        monkeypatch.setattr("eoa.resources.gate.gate", lambda: mock_gate)

        result = embed([])
        assert result == []

    @respx.mock
    def test_embed_replaces_empty_with_space(self, monkeypatch):
        """embed() replaces empty strings with single space."""
        mock_gate = MagicMock()
        mock_gate.acquire.return_value = MagicMock(ollama="multilingual-e5-large")
        monkeypatch.setattr("eoa.resources.gate.gate", lambda: mock_gate)

        call_count = [0]

        def check_and_respond(request):
            call_count[0] += 1
            body = json.loads(request.content)
            # Should have replaced empty strings with space
            assert all(len(t) > 0 for t in body["input"])
            return respx.Response(200, json={"embeddings": [[0.1] * 1024] * len(body["input"])})

        respx.post("http://localhost:11434/api/embed").mock(side_effect=check_and_respond)

        result = embed(["", "text", ""])
        assert len(result) == 3

    @respx.mock
    def test_embed_raises_on_count_mismatch(self, monkeypatch):
        """embed() raises LLMOutputError if response vector count != input count."""
        mock_gate = MagicMock()
        mock_gate.acquire.return_value = MagicMock(ollama="multilingual-e5-large")
        monkeypatch.setattr("eoa.resources.gate.gate", lambda: mock_gate)

        respx.post("http://localhost:11434/api/embed").mock(
            return_value=respx.Response(
                200,
                json={"embeddings": [[0.1]]},  # Only 1 vector
            )
        )

        with pytest.raises(LLMOutputError, match="embed returned"):
            embed(["text1", "text2", "text3"])


class TestChatIntegration:
    """Integration tests for chat with realistic payloads."""

    @respx.mock
    def test_chat_with_format_schema(self, monkeypatch):
        """chat() passes format schema to Ollama."""
        mock_gate = MagicMock()
        mock_gate.acquire.return_value = MagicMock(ollama="gemma4:12b", est_vram_mb=8200)
        monkeypatch.setattr("eoa.resources.gate.gate", lambda: mock_gate)

        request_body = {}

        def capture_request(request):
            nonlocal request_body
            request_body = json.loads(request.content)
            return respx.Response(
                200,
                json={
                    "message": {"content": '{"field": "value"}', "tool_calls": []},
                    "eval_count": 5,
                    "prompt_eval_count": 10,
                },
            )

        respx.post("http://localhost:11434/api/chat").mock(side_effect=capture_request)

        schema = {"type": "object", "properties": {"field": {"type": "string"}}}
        chat("resident", [{"role": "user", "content": "test"}], format_schema=schema)
        assert "format" in request_body

    @respx.mock
    def test_chat_with_tools(self, monkeypatch):
        """chat() passes tools list to Ollama."""
        mock_gate = MagicMock()
        mock_gate.acquire.return_value = MagicMock(ollama="gemma4:12b", est_vram_mb=8200)
        monkeypatch.setattr("eoa.resources.gate.gate", lambda: mock_gate)

        request_body = {}

        def capture_request(request):
            nonlocal request_body
            request_body = json.loads(request.content)
            return respx.Response(
                200,
                json={
                    "message": {"content": "", "tool_calls": []},
                    "eval_count": 5,
                    "prompt_eval_count": 10,
                },
            )

        respx.post("http://localhost:11434/api/chat").mock(side_effect=capture_request)

        tools = [{"type": "function", "function": {"name": "test"}}]
        chat("resident", [{"role": "user", "content": "call tool"}], tools=tools)
        assert "tools" in request_body
