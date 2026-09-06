"""Round-3 (job 97): the structured BD draft ran away to 54k-char JSON -- input-side item
reduction + a lower token budget + invalid output routed to the deterministic substitute."""

from __future__ import annotations

import datetime as dt

from test_bd_structured_round3 import patch_bd_collectors  # noqa: F401 -- fixture reuse

from eoa.errors import LLMOutputError
from eoa.report import bd_territory as bdt


def _items(n: int, domain: str = "airborne_pods", level: str = "orange") -> list[dict]:
    return [
        {
            "id": i,
            "n": i,
            "title": f"item {i}",
            "domain": domain,
            "level": "red" if i == n else level,
            "score": 10 - (i % 5),
            "summary_he": "x" * 900,
            "so_what_he": "y" * 50,
            "published_at": dt.datetime(2026, 9, 1),
        }
        for i in range(1, n + 1)
    ]


def test_select_bd_items_for_prompt_caps_per_domain_keeps_red_and_truncates() -> None:
    items = _items(12)
    sel = bdt.select_bd_items_for_prompt(items, per_domain=4)
    ids = [it["id"] for it in sel]
    assert len(ids) == 5  # top 4 of the single domain + the red item 12
    assert 12 in ids
    assert all(len(it["summary_he"]) <= bdt._BD_PROMPT_ITEM_TEXT_CHARS for it in sel)
    # the registry itself is untouched
    assert len(items[0]["summary_he"]) == 900


def test_bd_num_predict_is_bounded() -> None:
    assert bdt._BD_NUM_PREDICT <= 9000


def test_invalid_model_output_yields_deterministic_substitute(patch_bd_collectors, monkeypatch):  # noqa: F811
    def boom(role, schema, messages, **kw):
        raise LLMOutputError("schema validation failed: Invalid JSON: EOF while parsing a string")

    monkeypatch.setattr(bdt, "chat_structured", boom)
    paths = bdt.build_bd_territory("US", 90, period_end=dt.date(2026, 9, 6))
    md_text = paths.md.read_text(encoding="utf-8")
    assert "תקציר מובנה אוטומטית" in md_text
    assert "[1]" in md_text
    assert "אזהרה: הדוח לא עבר" not in md_text
