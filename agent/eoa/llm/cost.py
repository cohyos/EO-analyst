"""Cost estimation for cloud LLM calls (U8-ו/5, docs/adr/005-cloud-llm-cli.md Revision
2026-09-06). Pricing comes from ``config.yaml``'s ``llm_providers.pricing`` table, keyed
``"<provider>:<model>"``, USD per million tokens. CLI/subscription providers (agy/claude/codex)
are intentionally absent from that table -- they cost $0 by design, even though their token
usage (when the CLI reports it) is still counted in ``llm_calls``.
"""

from __future__ import annotations

from typing import Any

from eoa.config import settings


def estimate_cost_usd(provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """USD estimate for one call; ``0.0`` for any provider/model not in the pricing table (every
    CLI provider, or an API model the user hasn't priced yet)."""
    entry = settings().llm_providers.pricing.get(f"{provider}:{model}")
    if not entry:
        return 0.0
    return round(
        (prompt_tokens / 1_000_000) * entry.input_per_mtok + (completion_tokens / 1_000_000) * entry.output_per_mtok,
        6,
    )


def format_daily_report_footer(summary: dict[str, Any]) -> str:
    """U8-4 one-line footer for the daily report: "מודלים: X קריאות ענן, Y נפלו למקומי, עלות
    משוערת $Z". ``summary`` is the ``totals`` sub-dict ``eoa.memory.relational.summarize_llm_calls``
    returns. Called only from ``eoa.report.daily``'s LLM call site (per this change's ownership
    split)."""
    cloud_calls = int(summary.get("cloud_calls") or 0)
    fallbacks = int(summary.get("fallbacks") or 0)
    cost = float(summary.get("est_cost_usd") or 0.0)
    if cloud_calls == 0 and fallbacks == 0 and cost == 0.0:
        return ""
    return f"מודלים: {cloud_calls} קריאות ענן, {fallbacks} נפלו למקומי, עלות משוערת ${cost:.4f}"
