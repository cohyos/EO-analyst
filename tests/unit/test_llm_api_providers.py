"""Tests for eoa.llm.providers.api -- direct-API cloud providers (U8-ו, Revision 2026-09-06).

All HTTP is mocked with `respx`; no real network call, no real key is ever needed. Retry timing
(tenacity's exponential backoff) is neutralised so a 429/5xx test doesn't actually sleep.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from eoa.errors import ProviderUnavailable
from eoa.llm.providers.api import (
    AnthropicProvider,
    ApiProviderError,
    GeminiProvider,
    OpenAIProvider,
    _gemini_schema,
    get_api_provider,
    redact_secrets,
)


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """Tenacity's wait_exponential really sleeps between attempts -- for a unit test we want the
    retry *logic* exercised (attempt count, eventual failure) without the real backoff delay."""
    monkeypatch.setattr("eoa.llm.providers.api.wait_exponential", lambda **kw: lambda *a, **k: 0)


@pytest.fixture(autouse=True)
def _fake_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


class TestAvailability:
    def test_unavailable_without_key(self):
        assert AnthropicProvider().is_available() is False
        assert GeminiProvider().is_available() is False
        assert OpenAIProvider().is_available() is False

    def test_available_with_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
        assert AnthropicProvider().is_available() is True

    def test_chat_raises_provider_unavailable_without_key(self):
        with pytest.raises(ProviderUnavailable):
            AnthropicProvider().chat([{"role": "user", "content": "hi"}])
        with pytest.raises(ProviderUnavailable):
            GeminiProvider().chat([{"role": "user", "content": "hi"}])
        with pytest.raises(ProviderUnavailable):
            OpenAIProvider().chat([{"role": "user", "content": "hi"}])


class TestGetApiProvider:
    def test_factory_returns_expected_classes(self):
        assert isinstance(get_api_provider("anthropic"), AnthropicProvider)
        assert isinstance(get_api_provider("gemini"), GeminiProvider)
        assert isinstance(get_api_provider("openai"), OpenAIProvider)

    def test_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            get_api_provider("bogus")


class TestAnthropicChat:
    @respx.mock
    def test_plain_text_response(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": [{"type": "text", "text": "PONG"}],
                    "usage": {"input_tokens": 10, "output_tokens": 4},
                },
            )
        )
        result = AnthropicProvider("claude-sonnet-5").chat([{"role": "user", "content": "ping"}])
        assert route.called
        assert result.content == "PONG"
        assert result.provider == "anthropic"
        assert result.usage == {"input_tokens": 10, "output_tokens": 4}
        sent = json.loads(route.calls[0].request.content)
        assert sent["model"] == "claude-sonnet-5"

    @respx.mock
    def test_sends_api_key_header_not_bearer(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json={"content": [{"type": "text", "text": "x"}], "usage": {}})
        )
        AnthropicProvider("claude-sonnet-5").chat([{"role": "user", "content": "hi"}])
        headers = route.calls[0].request.headers
        assert headers["x-api-key"] == "sk-ant-secret"
        assert "authorization" not in headers

    @respx.mock
    def test_structured_output_uses_tool_use(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": [{"type": "tool_use", "name": "emit_result", "input": {"ok": True}}],
                    "usage": {"input_tokens": 5, "output_tokens": 2},
                },
            )
        )
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
        result = AnthropicProvider("claude-sonnet-5").chat(
            [{"role": "user", "content": "hi"}], json_schema=schema
        )
        assert json.loads(result.content) == {"ok": True}
        sent = json.loads(route.calls[0].request.content)
        assert sent["tool_choice"] == {"type": "tool", "name": "emit_result"}

    @respx.mock
    def test_power_level_sets_thinking_budget(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(200, json={"content": [{"type": "text", "text": "x"}], "usage": {}})
        )
        AnthropicProvider("claude-opus-5", power="high").chat([{"role": "user", "content": "hi"}])
        sent = json.loads(route.calls[0].request.content)
        assert sent["thinking"] == {"type": "enabled", "budget_tokens": 16000}

    @respx.mock
    def test_4xx_error_raises_without_retry(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-bad")
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        with pytest.raises(ApiProviderError, match="401"):
            AnthropicProvider("claude-sonnet-5").chat([{"role": "user", "content": "hi"}])
        assert route.call_count == 1  # 401 is not retryable

    @respx.mock
    def test_5xx_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
        route = respx.post("https://api.anthropic.com/v1/messages").mock(
            side_effect=[
                httpx.Response(500, json={"error": "boom"}),
                httpx.Response(200, json={"content": [{"type": "text", "text": "recovered"}], "usage": {}}),
            ]
        )
        result = AnthropicProvider("claude-sonnet-5").chat([{"role": "user", "content": "hi"}])
        assert result.content == "recovered"
        assert route.call_count == 2

    @respx.mock
    def test_429_exhausts_retries_and_raises(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
        respx.post("https://api.anthropic.com/v1/messages").mock(return_value=httpx.Response(429, json={}))
        with pytest.raises(ApiProviderError):
            AnthropicProvider("claude-sonnet-5").chat([{"role": "user", "content": "hi"}])


class TestGeminiChat:
    @respx.mock
    def test_plain_text_response(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gk-x")
        respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "candidates": [{"content": {"parts": [{"text": "PONG"}]}}],
                    "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3},
                },
            )
        )
        result = GeminiProvider("gemini-3.5-flash").chat([{"role": "user", "content": "ping"}])
        assert result.content == "PONG"
        assert result.usage == {"input_tokens": 7, "output_tokens": 3}

    @respx.mock
    def test_structured_output_sets_response_schema(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gk-x")
        route = respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
        ).mock(
            return_value=httpx.Response(
                200, json={"candidates": [{"content": {"parts": [{"text": '{"ok":true}'}]}}]}
            )
        )
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "title": "Out"}
        GeminiProvider("gemini-3.5-flash").chat([{"role": "user", "content": "hi"}], json_schema=schema)
        sent = json.loads(route.calls[0].request.content)
        assert sent["generationConfig"]["responseMimeType"] == "application/json"
        assert "title" not in sent["generationConfig"]["responseSchema"]

    @respx.mock
    def test_list_models_falls_back_to_static_on_error(self, monkeypatch):
        from eoa.config import ApiProviderCfg

        monkeypatch.setenv("GEMINI_API_KEY", "gk-x")
        monkeypatch.setattr(
            "eoa.llm.providers.api.settings",
            lambda: type(
                "S",
                (),
                {
                    "llm_providers": type(
                        "L", (), {"api": {"gemini": ApiProviderCfg(models=["static-model"])}}
                    )()
                },
            )(),
        )
        respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
            return_value=httpx.Response(500)
        )
        assert GeminiProvider().list_models() == ["static-model"]


class TestOpenAIChat:
    @respx.mock
    def test_plain_text_response(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "ok-x")
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "PONG"}}],
                    "usage": {"prompt_tokens": 6, "completion_tokens": 2},
                },
            )
        )
        result = OpenAIProvider("gpt-5.1-mini").chat([{"role": "user", "content": "ping"}])
        assert result.content == "PONG"
        assert result.usage == {"input_tokens": 6, "output_tokens": 2}
        sent = json.loads(route.calls[0].request.content)
        assert sent["messages"][-1] == {"role": "user", "content": "ping"}

    @respx.mock
    def test_reasoning_effort_passed_through(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "ok-x")
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "x"}}], "usage": {}})
        )
        OpenAIProvider("gpt-5.1", power="low").chat([{"role": "user", "content": "hi"}])
        sent = json.loads(route.calls[0].request.content)
        assert sent["reasoning_effort"] == "low"

    @respx.mock
    def test_structured_output_uses_json_schema_response_format(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "ok-x")
        route = respx.post("https://api.openai.com/v1/chat/completions").mock(
            return_value=httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})
        )
        schema = {"type": "object"}
        OpenAIProvider("gpt-5.1-mini").chat([{"role": "user", "content": "hi"}], json_schema=schema)
        sent = json.loads(route.calls[0].request.content)
        assert sent["response_format"]["type"] == "json_schema"
        assert sent["response_format"]["json_schema"]["schema"] == schema


class TestGeminiKeyHandling:
    """Q2-3: the Gemini key travels only in the `x-goog-api-key` header, never a
    `?key=` query param, and never leaks into a raised/logged message on failure."""

    @respx.mock
    def test_chat_sends_key_as_header_not_query_param(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gk-secret-value")
        route = respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
        ).mock(
            return_value=httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "x"}]}}]})
        )
        GeminiProvider("gemini-3.5-flash").chat([{"role": "user", "content": "hi"}])
        sent = route.calls[0].request
        assert sent.headers["x-goog-api-key"] == "gk-secret-value"
        assert "key=" not in str(sent.url)

    @respx.mock
    def test_list_models_sends_key_as_header_not_query_param(self, monkeypatch):
        monkeypatch.setenv("GEMINI_API_KEY", "gk-secret-value")
        route = respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
            return_value=httpx.Response(200, json={"models": []})
        )
        GeminiProvider().list_models()
        sent = route.calls[0].request
        assert sent.headers["x-goog-api-key"] == "gk-secret-value"
        assert "key=" not in str(sent.url)

    @respx.mock
    def test_500_error_does_not_leak_key_in_raised_message(self, monkeypatch):
        secret = "AIzaSuperSecretGeminiKey1234567890"
        monkeypatch.setenv("GEMINI_API_KEY", secret)
        respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
        ).mock(return_value=httpx.Response(500, text=f"upstream failure, echoing ?key={secret} back"))
        with pytest.raises(ApiProviderError) as exc_info:
            GeminiProvider("gemini-3.5-flash").chat([{"role": "user", "content": "hi"}])
        assert secret not in str(exc_info.value)

    @respx.mock
    def test_500_error_does_not_leak_key_in_logged_message(self, monkeypatch, caplog):
        import logging

        secret = "AIzaSuperSecretGeminiKey1234567890"
        monkeypatch.setenv("GEMINI_API_KEY", secret)
        respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
        ).mock(return_value=httpx.Response(500, text=f"upstream failure, echoing ?key={secret} back"))
        with caplog.at_level(logging.ERROR):
            with pytest.raises(ApiProviderError):
                GeminiProvider("gemini-3.5-flash").chat([{"role": "user", "content": "hi"}])
        assert secret not in caplog.text

    @respx.mock
    def test_list_models_failure_does_not_leak_key(self, monkeypatch, caplog):
        import logging

        secret = "AIzaSuperSecretGeminiKey1234567890"
        monkeypatch.setenv("GEMINI_API_KEY", secret)
        respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
            return_value=httpx.Response(500, text=f"boom key={secret}")
        )
        with caplog.at_level(logging.WARNING):
            GeminiProvider().list_models()
        assert secret not in caplog.text


class TestRedactSecrets:
    def test_redacts_key_query_param(self):
        assert "AIzaXYZ" not in redact_secrets("https://x.example/foo?key=AIzaXYZ123456789&other=1")

    def test_redacts_aiza_key_literal(self):
        out = redact_secrets("token is AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234")
        assert "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234" not in out

    def test_redacts_sk_prefixed_key(self):
        out = redact_secrets("using sk-abcdefghijklmnopqrstuvwx now")
        assert "sk-abcdefghijklmnopqrstuvwx" not in out

    def test_redacts_bearer_token(self):
        out = redact_secrets("Authorization: Bearer abc123.def456-ghi789")
        assert "abc123.def456-ghi789" not in out
        assert "Bearer" in out

    def test_empty_and_none_like_input_untouched(self):
        assert redact_secrets("") == ""

    def test_leaves_ordinary_text_untouched(self):
        text = "gemini API error 429: rate limited, try again later"
        assert redact_secrets(text) == text


class TestGeminiSchemaAdaptation:
    def test_inlines_refs_and_strips_unsupported_keys(self):
        schema = {
            "title": "Out",
            "type": "object",
            "properties": {"item": {"$ref": "#/$defs/Item"}},
            "$defs": {
                "Item": {
                    "title": "Item",
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "additionalProperties": False,
                }
            },
        }
        cleaned = _gemini_schema(schema)
        assert "title" not in cleaned
        assert "$defs" not in cleaned
        assert cleaned["properties"]["item"] == {"type": "object", "properties": {"name": {"type": "string"}}}
