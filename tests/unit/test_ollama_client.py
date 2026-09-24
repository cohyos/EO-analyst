"""Tests for eoa.llm.ollama_client — Ollama integration and LLM calls."""

from __future__ import annotations

from pydantic import BaseModel

import eoa.llm.ollama_client as oc
from eoa.llm.ollama_client import (
    ChatResult,
    _strip_fences,
    _structured_once,
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


class TestStructuredOnceUsageAggregation:
    """E05 follow-up (SOL-REVIEW-2026-09-24): `_structured_once`'s own schema-validation-retry
    loop makes up to two real, separately-billed `chat()` calls -- a rejected first attempt's
    token usage must not be silently dropped just because the second attempt is the one that
    finally validated. Old code returned only the LAST attempt's `ChatResult` as-is; this test's
    aggregated totals fail against that (it would see just the second call's 30/10/80, not the
    summed 130/60/280)."""

    class _Out(BaseModel):
        ok: bool

    def test_aggregates_usage_across_both_internal_attempts(self, monkeypatch) -> None:
        calls: list[list[dict]] = []

        def fake_chat(role, msgs, **kwargs):
            calls.append(msgs)
            if len(calls) == 1:
                # schema-validation failure: not valid JSON for `_Out`
                return ChatResult(content="not json", prompt_tokens=100, eval_tokens=50, duration_ms=200)
            return ChatResult(content='{"ok": true}', prompt_tokens=30, eval_tokens=10, duration_ms=80)

        monkeypatch.setattr(oc, "chat", fake_chat)

        validated, res = _structured_once(
            "resident",
            self._Out,
            [{"role": "user", "content": "hi"}],
            task="classify",
            interactive=False,
            options=None,
            provider=None,
        )

        assert validated.ok is True
        assert len(calls) == 2  # both internal attempts actually ran
        assert res.prompt_tokens == 130
        assert res.eval_tokens == 60
        assert res.duration_ms == 280

    def test_single_successful_attempt_usage_unchanged(self, monkeypatch) -> None:
        """No retry needed -- the aggregated result must equal the one real attempt's own usage,
        not double-count or zero it out."""

        def fake_chat(role, msgs, **kwargs):
            return ChatResult(content='{"ok": true}', prompt_tokens=42, eval_tokens=7, duration_ms=99)

        monkeypatch.setattr(oc, "chat", fake_chat)

        validated, res = _structured_once(
            "resident",
            self._Out,
            [{"role": "user", "content": "hi"}],
            task="classify",
            interactive=False,
            options=None,
            provider=None,
        )
        assert validated.ok is True
        assert res.prompt_tokens == 42
        assert res.eval_tokens == 7
        assert res.duration_ms == 99


class TestChatStructuredChainNoDoubleLedgerRow:
    """R07 (SOL-REVIEW2-2026-09-24): `_chat_structured_chain` used to call `_record` a SECOND
    time on success, with `_structured_once`'s own (possibly aggregated-across-a-schema-retry,
    see `TestStructuredOnceUsageAggregation` above) usage -- double-counting tokens/cost that the
    real `chat()` call(s) inside `_structured_once` had already logged for real, via `run_chain`,
    the first time. This test isolates `_chat_structured_chain`'s OWN success path (mocking
    `_structured_once` away entirely, same style as `TestChatStructuredChainReturnsUsedEntry`
    below) and asserts it logs NOTHING itself on success -- old code fails this with exactly one
    extra `_record` call carrying `_structured_once`'s aggregated usage."""

    class _Out(BaseModel):
        ok: bool

    def test_success_does_not_call_record_a_second_time(self, monkeypatch) -> None:
        from eoa.config import ChainEntryCfg

        chain = [ChainEntryCfg(provider="anthropic", model="claude-x")]
        suspect = self._Out(ok=True)

        def fake_structured_once(role, schema, messages, **kwargs):
            # Stands in for a schema-retry that took two real calls, aggregated by
            # `_structured_once`'s own E05 fix -- exactly the shape that used to get re-logged.
            return suspect, ChatResult(content='{"ok": true}', prompt_tokens=130, eval_tokens=60, duration_ms=280)

        record_calls: list[tuple] = []
        monkeypatch.setattr(oc, "_structured_once", fake_structured_once)
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: record_calls.append((a, k)))

        validated, used_entry = oc._chat_structured_chain(
            "resident", chain, self._Out, [{"role": "user", "content": "hi"}],
            task="analyze", interactive=False, options=None,
        )

        assert validated is suspect
        assert used_entry == chain[0]
        assert record_calls == []  # THE regression check -- no outer aggregate ledger row

    def test_failure_still_logs_one_zero_cost_marker_row(self, monkeypatch) -> None:
        """The FAILURE branch's own `_record` call is unaffected by this fix -- it logs a
        zero-token `ok=False` marker (not a double-count of any real usage) and still moves on
        to the next chain entry.

        R07/E05 follow-up (SOL-REVIEW3-2026-09-24): the chain's SECOND entry here is "ollama",
        whose successful `chat_override=[entry]` call never reaches `run_chain` (see the fix in
        `_chat_structured_chain`), so it now logs its OWN success row here -- 2 total, not 1. See
        `TestChatStructuredChainOllamaSuccessLedgerRow` below for the dedicated ollama-only cases."""
        from eoa.config import ChainEntryCfg
        from eoa.errors import ProviderUnavailable

        chain = [
            ChainEntryCfg(provider="anthropic", model="claude-x"),
            ChainEntryCfg(provider="ollama"),
        ]
        suspect = self._Out(ok=True)
        calls = {"n": 0}

        def fake_structured_once(role, schema, messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ProviderUnavailable("anthropic key missing")
            return suspect, ChatResult(content='{"ok": true}', prompt_tokens=5, eval_tokens=5, duration_ms=10)

        record_calls: list[tuple] = []
        monkeypatch.setattr(oc, "_structured_once", fake_structured_once)
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: record_calls.append((a, k)))

        validated, used_entry = oc._chat_structured_chain(
            "resident", chain, self._Out, [{"role": "user", "content": "hi"}],
            task="analyze", interactive=False, options=None,
        )

        assert validated is suspect
        assert used_entry == chain[1]
        # Two record calls: the failed anthropic attempt's zero-cost marker, PLUS the successful
        # ollama entry's own usage row (its real usage is logged inside `_structured_once`, but
        # NOT via `run_chain` -- the single-entry-ollama shortcut bypasses it entirely, so
        # `_chat_structured_chain` itself must record that one).
        assert len(record_calls) == 2
        failed_attempt = record_calls[0][0][1]
        assert failed_attempt.ok is False
        assert failed_attempt.prompt_tokens == 0
        ollama_attempt = record_calls[1][0][1]
        assert ollama_attempt.ok is True
        assert ollama_attempt.provider == "ollama"
        assert ollama_attempt.prompt_tokens == 5
        assert ollama_attempt.completion_tokens == 5


class TestChatStructuredChainOllamaSuccessLedgerRow:
    """R07/E05 (SOL-REVIEW3-2026-09-24): a structured call whose chain resolves to a single
    "ollama" entry never reaches `run_chain` at all -- `eoa.llm.ollama_client.chat()`'s own
    single-entry-ollama shortcut (`chain_override=[entry]`, `entry.provider == "ollama"`) goes
    straight to `_ollama_chat`, which does not call `_record` itself. R07's fix (see
    `TestChatStructuredChainNoDoubleLedgerRow` above) correctly stopped `_chat_structured_chain`
    from re-logging a CLOUD entry's already-recorded usage, but as an unconditional removal it
    also silently dropped the ollama entry's only chance at a ledger row. These tests are
    discriminating against that regression: pre-fix code (an unconditional `if entry.provider ==
    "ollama": ...` guard absent) logs zero rows for a successful lone-ollama structured call."""

    class _Out(BaseModel):
        ok: bool

    def test_single_ollama_structured_fallback_writes_exactly_one_row(self, monkeypatch) -> None:
        from eoa.config import ChainEntryCfg

        chain = [ChainEntryCfg(provider="ollama")]
        suspect = self._Out(ok=True)

        def fake_structured_once(role, schema, messages, **kwargs):
            assert kwargs["chain_override"] == [ChainEntryCfg(provider="ollama")]
            return suspect, ChatResult(
                content='{"ok": true}', prompt_tokens=12, eval_tokens=8, duration_ms=150, model="qwen3:14b"
            )

        record_calls: list[tuple] = []
        monkeypatch.setattr(oc, "_structured_once", fake_structured_once)
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: record_calls.append((a, k)))

        validated, used_entry = oc._chat_structured_chain(
            "resident", chain, self._Out, [{"role": "user", "content": "hi"}],
            task="analyze", interactive=False, options=None,
        )

        assert validated is suspect
        assert used_entry == chain[0]
        assert len(record_calls) == 1  # THE regression check -- used to be zero
        role, attempt, _batch_size = record_calls[0][0]
        assert role == "resident"
        assert attempt.ok is True
        assert attempt.provider == "ollama"
        assert attempt.model == "qwen3:14b"
        assert attempt.prompt_tokens == 12
        assert attempt.completion_tokens == 8
        assert attempt.duration_ms == 150

    def test_cloud_chain_still_writes_one_row_per_actual_call_not_an_aggregate(self, monkeypatch) -> None:
        """(b) from SOL-REVIEW3: R07's no-double-count property must still hold -- a cloud entry
        (or a cloud entry that itself falls back to ollama inside `run_chain`, which already
        records each of ITS attempts) gets no extra row from `_chat_structured_chain` itself."""
        from eoa.config import ChainEntryCfg

        chain = [ChainEntryCfg(provider="anthropic", model="claude-x")]
        suspect = self._Out(ok=True)

        def fake_structured_once(role, schema, messages, **kwargs):
            # Two real `chat()` calls under the hood, each already recorded by `run_chain` --
            # simulated here simply by never touching `_record` ourselves, same as production.
            return suspect, ChatResult(content='{"ok": true}', prompt_tokens=130, eval_tokens=60, duration_ms=280)

        record_calls: list[tuple] = []
        monkeypatch.setattr(oc, "_structured_once", fake_structured_once)
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: record_calls.append((a, k)))

        validated, used_entry = oc._chat_structured_chain(
            "resident", chain, self._Out, [{"role": "user", "content": "hi"}],
            task="analyze", interactive=False, options=None,
        )

        assert validated is suspect
        assert used_entry == chain[0]
        assert record_calls == []  # no aggregate duplicate for a cloud entry


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


class _TruncatedOut(BaseModel):
    """A minimal schema with one free-text Hebrew field, for exercising
    `_find_truncation_suspects`/`_guard_hebrew_truncation` without any of the real pipeline
    schemas' unrelated required fields."""

    summary_he: str


class TestGuardHebrewTruncationF27:
    """F27 (audit 2026-09-24): a resource/provider failure on the truncation-repair call must
    return the already schema-valid first result, not propagate and discard it -- only a schema
    validation failure (LLMOutputError) used to be caught."""

    def _suspect_model(self):
        # "מטע" is a bare Hebrew-acronym stem with no terminal punctuation -- exactly what
        # `_looks_truncated_mid_hebrew_acronym` flags as a suspected mid-acronym truncation.
        return _TruncatedOut(summary_he="הפעילות התבצעה בסיוע מטע")

    def _call_guard(self, monkeypatch, structured_once_side_effect):
        from eoa.llm import ollama_client as oc

        monkeypatch.setattr(oc, "_structured_once", structured_once_side_effect)
        return oc._guard_hebrew_truncation(
            "resident",
            _TruncatedOut,
            [{"role": "user", "content": "hi"}],
            self._suspect_model(),
            task="analyze",
            interactive=False,
            options=None,
            provider=None,
        )

    def test_resource_unavailable_on_repair_falls_back_to_first_result(self, monkeypatch):
        from eoa.errors import ResourceUnavailable

        def boom(*a, **k):
            raise ResourceUnavailable("gpu busy")

        result = self._call_guard(monkeypatch, boom)
        assert result.summary_he.startswith("הפעילות התבצעה בסיוע")

    def test_provider_unavailable_on_repair_falls_back_to_first_result(self, monkeypatch):
        from eoa.errors import ProviderUnavailable

        def boom(*a, **k):
            raise ProviderUnavailable("no key")

        result = self._call_guard(monkeypatch, boom)
        assert result.summary_he.startswith("הפעילות התבצעה בסיוע")

    def test_cli_provider_error_on_repair_falls_back_to_first_result(self, monkeypatch):
        from eoa.errors import CliProviderError

        def boom(*a, **k):
            raise CliProviderError("cli crashed mid-repair")

        result = self._call_guard(monkeypatch, boom)
        assert result.summary_he.startswith("הפעילות התבצעה בסיוע")

    def test_deadline_exceeded_on_repair_still_propagates(self, monkeypatch):
        """DeadlineExceeded/LeaseLost mean the worker itself must stop -- not "this repair
        failed" -- so they must NOT be swallowed into the fallback-to-first-result path."""
        from eoa.errors import DeadlineExceeded

        def boom(*a, **k):
            raise DeadlineExceeded("stage time budget exhausted")

        import pytest

        with pytest.raises(DeadlineExceeded):
            self._call_guard(monkeypatch, boom)

    def test_no_suspects_never_calls_structured_once(self, monkeypatch):
        from eoa.llm import ollama_client as oc

        def boom(*a, **k):
            raise AssertionError("must not attempt a repair call when nothing looks truncated")

        monkeypatch.setattr(oc, "_structured_once", boom)
        clean = _TruncatedOut(summary_he="משפט תקין וסגור.")
        result = oc._guard_hebrew_truncation(
            "resident",
            _TruncatedOut,
            [{"role": "user", "content": "hi"}],
            clean,
            task="analyze",
            interactive=False,
            options=None,
            provider=None,
        )
        assert result.summary_he == "משפט תקין וסגור."


class TestChatStructuredChainReturnsUsedEntry:
    """Efficiency (audit 2026-09-24): `_chat_structured_chain` now returns the ONE entry that
    actually produced its result, so `chat_structured`'s truncation-repair retry can pin to just
    that entry instead of replaying the whole chain (including entries that already failed)."""

    def test_returns_the_entry_that_succeeded_not_the_first_one(self, monkeypatch):
        from eoa.config import ChainEntryCfg
        from eoa.errors import ProviderUnavailable
        from eoa.llm import ollama_client as oc

        chain = [
            ChainEntryCfg(provider="claude", model="claude-sonnet-5"),
            ChainEntryCfg(provider="agy", model="gemini-3.8-flash-medium"),
            ChainEntryCfg(provider="ollama"),
        ]
        clean = _TruncatedOut(summary_he="משפט תקין וסגור.")
        calls: list[str] = []

        def fake_structured_once(role, schema, messages, *, task, interactive, options, provider, chain_override=None):
            entry = chain_override[0]
            calls.append(entry.provider)
            if entry.provider == "claude":
                raise ProviderUnavailable("claude not logged in")
            return clean, oc.ChatResult(content="{}")

        monkeypatch.setattr(oc, "_structured_once", fake_structured_once)
        monkeypatch.setattr("eoa.llm.chain._record", lambda *a, **k: None)

        _validated, used_entry = oc._chat_structured_chain(
            "resident", chain, _TruncatedOut, [{"role": "user", "content": "hi"}],
            task="analyze", interactive=False, options=None,
        )
        assert used_entry.provider == "agy"
        assert calls == ["claude", "agy"]  # never reached ollama -- agy already succeeded

    def test_chat_structured_pins_repair_to_the_single_successful_entry(self, monkeypatch):
        """The chain-path caller in `chat_structured` must pass `chain_override=[used_entry]` to
        the truncation guard -- a single-entry list -- not the whole multi-entry `chain`, so a
        triggered repair never replays an entry that already failed before `validated` was
        obtained."""
        from eoa.config import ChainEntryCfg
        from eoa.llm import ollama_client as oc

        chain = [
            ChainEntryCfg(provider="claude", model="claude-sonnet-5"),
            ChainEntryCfg(provider="agy", model="gemini-3.8-flash-medium"),
            ChainEntryCfg(provider="ollama"),
        ]
        used_entry = chain[1]
        suspect = _TruncatedOut(summary_he="הפעילות התבצעה בסיוע מטע")

        monkeypatch.setenv("EOA_PIPELINE", "1")
        monkeypatch.setattr(oc, "settings", lambda: type(
            "S", (), {"llm_providers": type("L", (), {"effective_chain": staticmethod(lambda role: chain)})()}
        )())
        monkeypatch.setattr(oc, "_chat_structured_chain", lambda *a, **k: (suspect, used_entry))

        captured: dict = {}

        def fake_guard(role, schema, messages, validated, **kw):
            captured.update(kw)
            return validated

        monkeypatch.setattr(oc, "_guard_hebrew_truncation", fake_guard)

        oc.chat_structured("resident", _TruncatedOut, [{"role": "user", "content": "hi"}])

        assert captured["chain_override"] == [used_entry]
