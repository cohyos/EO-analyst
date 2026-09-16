"""Unit test for CR-platform-opportunity (2026-09-16): ``eoa.pipeline.analyze.persist_analysis``
must UNION with, not overwrite, a ``items.product_lines`` value already set by
``eoa.pipeline.classify.persist_classification`` (the deterministic platform-integration-
opportunity pre-check, ``eoa.pipeline.opportunity_signals``) -- this stage's own
``tag_product_lines``/``llm_tag_batch`` match against ``summary_he``/``so_what_he``/``subdomain``
and may legitimately find nothing (or a non-overlapping set) for an item whose classify-stage tag
was derived from title/clean_text alone; overwriting would silently drop it.

Run with:
``PYTHONPATH=agent python -m pytest tests/unit/test_analyze_platform_opportunity_merge.py -q``
"""

from __future__ import annotations

import sys
import types

import pytest

if "eoa.db" not in sys.modules:
    try:
        import eoa.db  # noqa: F401
    except ImportError:
        fake_db = types.ModuleType("eoa.db")
        fake_db.connection = lambda: None  # type: ignore[attr-defined]
        fake_db.get_pool = lambda: None  # type: ignore[attr-defined]
        sys.modules["eoa.db"] = fake_db

from eoa.llm.schemas.analysis import AnalyzeOut
from eoa.pipeline.analyze import persist_analysis
from eoa.pipeline.opportunity_signals import TAG


class RecordingStub:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs) -> int:
        self.calls.append((args, kwargs))
        return len(self.calls)


def _out(**overrides) -> AnalyzeOut:
    base = dict(
        summary_he="תקציר על נושא כללי בלי מילות מפתח של קווי מוצר",
        so_what_he="להערכתנו זהו עדכון שגרתי.",
        key_facts=[],
        events=[],
        edges=[],
    )
    base.update(overrides)
    return AnalyzeOut(**base)


class TestProductLinesUnionMerge:
    def test_existing_classify_stage_tag_survives_when_analyze_finds_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        update_stub = RecordingStub()
        monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", update_stub)
        monkeypatch.setattr("eoa.pipeline.analyze.insert_event", RecordingStub())
        monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", RecordingStub())
        # analyze's own product-line LLM fallback must not fire in this test (keeps it hermetic).
        monkeypatch.setattr(
            "eoa.product_lines.registry.llm_tagging_enabled", lambda: False
        )

        item = {
            "id": 22760,
            "title": "YFQ-44A Fury Has Been Fit Checked With Air-To-Ground Munitions",
            "url": "https://example.com/22760",
            "domain": "airborne_pods",
            "subdomain": "targeting_pods",
            "level": "yellow",
            "tags": [TAG],
            # Set at classify time by persist_classification's own opportunity-signals hint --
            # deliberately a line the generic keyword text below will NOT independently re-derive.
            "product_lines": ["targeting_pods"],
            "entities_mentioned": [],
        }

        persist_analysis(item, _out())

        product_line_calls = [
            kwargs["product_lines"]
            for _, kwargs in update_stub.calls
            if "product_lines" in kwargs
        ]
        assert product_line_calls, "expected at least one update_item_fields(product_lines=...) call"
        # The classify-stage tag must still be present in whatever was ultimately written.
        assert "targeting_pods" in product_line_calls[-1]

    def test_analyze_stage_additions_are_unioned_not_overwritten(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        update_stub = RecordingStub()
        monkeypatch.setattr("eoa.pipeline.analyze.update_item_fields", update_stub)
        monkeypatch.setattr("eoa.pipeline.analyze.insert_event", RecordingStub())
        monkeypatch.setattr("eoa.pipeline.analyze.upsert_entity", RecordingStub())
        monkeypatch.setattr("eoa.product_lines.registry.llm_tagging_enabled", lambda: False)

        item = {
            "id": 22760,
            "title": "Fury CCA update",
            "url": "https://example.com/22760",
            "domain": "airborne_pods",
            "subdomain": "targeting_pods",
            "level": "yellow",
            "tags": [TAG],
            "product_lines": ["targeting_pods"],
            "entities_mentioned": [],
        }
        # so_what_he text that DOES independently hit lorop_pods' own configured keyword, so the
        # analyze-stage deterministic tagger finds a second, different line on its own merits.
        out = _out(so_what_he="להערכתנו מדובר בפוד LOROP חדש לפלטפורמה.")

        persist_analysis(item, out)

        product_line_calls = [
            kwargs["product_lines"]
            for _, kwargs in update_stub.calls
            if "product_lines" in kwargs
        ]
        assert product_line_calls
        final = product_line_calls[-1]
        assert "targeting_pods" in final  # classify-stage tag preserved
        assert "lorop_pods" in final  # analyze-stage's own independent match added
