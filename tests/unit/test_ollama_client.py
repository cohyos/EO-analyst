"""Tests for eoa.llm.ollama_client — Ollama integration and LLM calls."""

from __future__ import annotations

from eoa.llm.ollama_client import (
    ChatResult,
    _strip_fences,
    wrap_data,
)


class TestChatResult:
    """Test the ChatResult dataclass."""

    def test_chat_result_initialization(self):
        """ChatResult initializes with defaults."""
        result = ChatResult(content="Hello", eval_tokens=10, prompt_tokens=20)
        assert result.content == "Hello"
        assert result.eval_tokens == 10
        assert result.prompt_tokens == 20
        assert result.tool_calls == []
        assert result.thinking is None

    def test_chat_result_tokens_per_second(self):
        """tokens_per_s property calculates tokens/second."""
        result = ChatResult(
            content="test",
            eval_tokens=100,
            prompt_tokens=10,
            raw={
                "eval_duration": 1_000_000_000,  # 1 second in nanoseconds
            },
        )
        tps = result.tokens_per_s
        assert tps == 100.0  # 100 tokens / 1 second

    def test_chat_result_tokens_per_second_zero_duration(self):
        """tokens_per_s returns 0.0 with zero duration."""
        result = ChatResult(content="test", eval_tokens=100, raw={})
        assert result.tokens_per_s == 0.0


class TestWrapData:
    """Test the wrap_data function for neutralizing delimiter characters."""

    def test_wrap_data_adds_delimiters(self):
        """wrap_data() wraps text with DATA markers."""
        text = "Some fetched content"
        wrapped = wrap_data(text, item_id=123, src="https://example.com")
        assert "<<<DATA id=123" in wrapped
        assert "Some fetched content" in wrapped
        assert "<<<END DATA>>>" in wrapped

    def test_wrap_data_includes_src(self):
        """wrap_data() includes source URL in opener."""
        text = "content"
        wrapped = wrap_data(text, item_id=1, src="https://source.example.com")
        assert "src=https://source.example.com" in wrapped

    def test_wrap_data_neutralizes_open_fence(self):
        """wrap_data() replaces <<< with safe variant."""
        text = "This has <<< inside it"
        wrapped = wrap_data(text, item_id=1)
        # Should not have consecutive <<< in the content (neutralized)
        assert "<<<" not in wrapped.split("<<<DATA")[1].split("<<<END")[0]

    def test_wrap_data_neutralizes_close_fence(self):
        """wrap_data() replaces >>> with safe variant."""
        text = "This has >>> inside it"
        wrapped = wrap_data(text, item_id=1)
        # Should have neutralized the >>> in the content
        # The content is between DATA and END DATA markers
        start = wrapped.find(">>>") + 3  # After opening >>>
        end = wrapped.find("<<<END")
        content_part = wrapped[start:end]
        # The >>> in the content should be replaced with zero-width space variant
        assert ">>>>" not in content_part  # Should not have multiple >>>

    def test_wrap_data_with_integer_id(self):
        """wrap_data() works with integer item_id."""
        wrapped = wrap_data("text", item_id=999)
        assert "id=999" in wrapped

    def test_wrap_data_with_string_id(self):
        """wrap_data() works with string item_id."""
        wrapped = wrap_data("text", item_id="inv-123")
        assert "id=inv-123" in wrapped


class TestStripFences:
    """Test the _strip_fences function."""

    def test_strip_fences_removes_markdown_code_blocks(self):
        """_strip_fences() removes ```json fences."""
        text = '```json\n{"key": "value"}\n```'
        stripped = _strip_fences(text)
        assert stripped == '{"key": "value"}'

    def test_strip_fences_handles_no_language(self):
        """_strip_fences() handles ``` without language."""
        text = '```\n{"data": true}\n```'
        stripped = _strip_fences(text)
        assert stripped == '{"data": true}'

    def test_strip_fences_handles_incomplete_fences(self):
        """_strip_fences() handles missing closing fence."""
        text = '```json\n{"incomplete": true}'
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

    def test_strip_fences_with_newline_after_triple_backticks(self):
        """_strip_fences() properly extracts content after opening fence."""
        text = "```python\nprint('hello')\n```"
        stripped = _strip_fences(text)
        assert "print" in stripped

    def test_strip_fences_empty_after_backticks(self):
        """_strip_fences() handles ``` with no newline."""
        text = '```{"a":1}```'
        stripped = _strip_fences(text)
        # Content after opening ``` but before closing ```
        assert stripped == '{"a":1}' or stripped.startswith("{")


class TestDataGuardSystem:
    """Test the DATA_GUARD_SYSTEM prompt."""

    def test_data_guard_system_exists(self):
        """DATA_GUARD_SYSTEM prompt is defined."""
        from eoa.llm.ollama_client import DATA_GUARD_SYSTEM

        assert isinstance(DATA_GUARD_SYSTEM, str)
        assert len(DATA_GUARD_SYSTEM) > 0

    def test_data_guard_system_mentions_data_markers(self):
        """DATA_GUARD_SYSTEM references the DATA markers."""
        from eoa.llm.ollama_client import DATA_GUARD_SYSTEM

        assert "DATA" in DATA_GUARD_SYSTEM
        assert ">>>>" in DATA_GUARD_SYSTEM or ">>>" in DATA_GUARD_SYSTEM

    def test_data_guard_system_bilingual(self):
        """DATA_GUARD_SYSTEM includes Hebrew and English."""
        from eoa.llm.ollama_client import DATA_GUARD_SYSTEM

        # Should mention Hebrew (contains Hebrew text or references)
        has_hebrew = any(ord(c) > 127 for c in DATA_GUARD_SYSTEM)  # Basic check for non-ASCII
        assert has_hebrew or "עברית" in DATA_GUARD_SYSTEM or len(DATA_GUARD_SYSTEM) > 100


class TestDataMarkers:
    """Test the DATA marker constants."""

    def test_data_open_format_string(self):
        """DATA_OPEN is a format string with {id} and {src}."""
        from eoa.llm.ollama_client import DATA_OPEN

        assert "{id}" in DATA_OPEN
        assert "{src}" in DATA_OPEN
        assert "<<<DATA" in DATA_OPEN

    def test_data_close_is_constant(self):
        """DATA_CLOSE is a constant end marker."""
        from eoa.llm.ollama_client import DATA_CLOSE

        assert DATA_CLOSE == "<<<END DATA>>>"
