"""Tests for eoa.llm.cost -- USD-per-million-token estimation (U8-ו/5, Revision 2026-09-06)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from eoa.llm.cost import estimate_cost_usd, format_daily_report_footer


def _fake_settings(pricing):
    return SimpleNamespace(llm_providers=SimpleNamespace(pricing=pricing))


class TestEstimateCostUsd:
    def test_known_model_computes_expected_cost(self, monkeypatch):
        pricing = {"anthropic:claude-sonnet-5": SimpleNamespace(input_per_mtok=3.0, output_per_mtok=15.0)}
        monkeypatch.setattr("eoa.llm.cost.settings", lambda: _fake_settings(pricing))
        cost = estimate_cost_usd("anthropic", "claude-sonnet-5", 1_000_000, 1_000_000)
        assert cost == pytest.approx(18.0)

    def test_partial_usage_scales_linearly(self, monkeypatch):
        pricing = {"gemini:gemini-3.5-flash": SimpleNamespace(input_per_mtok=0.3, output_per_mtok=2.5)}
        monkeypatch.setattr("eoa.llm.cost.settings", lambda: _fake_settings(pricing))
        cost = estimate_cost_usd("gemini", "gemini-3.5-flash", 500_000, 200_000)
        assert cost == pytest.approx(0.15 + 0.5)

    def test_unpriced_model_is_zero(self, monkeypatch):
        monkeypatch.setattr("eoa.llm.cost.settings", lambda: _fake_settings({}))
        assert estimate_cost_usd("agy", "gemini-3.8-flash-medium", 10_000, 10_000) == 0.0

    def test_cli_provider_always_zero_even_if_priced_by_mistake(self, monkeypatch):
        # CLI providers should never appear in the pricing table, but if one did, the math still
        # just works out to whatever the table says -- there is no special-casing by provider id.
        pricing = {"claude:claude-opus-5": SimpleNamespace(input_per_mtok=0.0, output_per_mtok=0.0)}
        monkeypatch.setattr("eoa.llm.cost.settings", lambda: _fake_settings(pricing))
        assert estimate_cost_usd("claude", "claude-opus-5", 1_000_000, 1_000_000) == 0.0


class TestFormatDailyReportFooter:
    def test_no_cloud_activity_returns_empty(self):
        assert format_daily_report_footer({"cloud_calls": 0, "fallbacks": 0, "est_cost_usd": 0.0}) == ""

    def test_formats_hebrew_summary_line(self):
        line = format_daily_report_footer({"cloud_calls": 12, "fallbacks": 2, "est_cost_usd": 0.1234})
        assert "מודלים:" in line
        assert "12" in line
        assert "2" in line
        assert "0.1234" in line
