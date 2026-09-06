"""Tests for U8-6 batch mode (Revision 2026-09-06): the generic
``eoa.llm.ollama_client.chat_structured_batch`` helper, and the classify/triage/analyze batch
call sites that use it only when the global mode is "cloud".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from eoa.llm import ollama_client as oc


class _Out(BaseModel):
    domain: str
    score: int = 0


class TestBatchWrapperSchema:
    def test_wrapper_cached_per_schema(self):
        w1 = oc._batch_wrapper_schema(_Out)
        w2 = oc._batch_wrapper_schema(_Out)
        assert w1 is w2

    def test_wrapper_schema_has_items_array(self):
        schema = oc._batch_wrapper_schema(_Out).model_json_schema()
        assert schema["properties"]["items"]["type"] == "array"


class TestChatStructuredBatch:
    def test_maps_results_by_item_id(self, monkeypatch: pytest.MonkeyPatch):
        def fake_chat_structured(role, schema, messages, **kw):
            return schema.model_validate(
                {
                    "items": [
                        {"item_id": 1, "domain": "a", "score": 3},
                        {"item_id": 2, "domain": "b", "score": 7},
                    ]
                }
            )

        monkeypatch.setattr(oc, "chat_structured", fake_chat_structured)
        result = oc.chat_structured_batch("resident", _Out, [(1, "p1"), (2, "p2")], system="sys")
        assert result == {1: _Out(domain="a", score=3), 2: _Out(domain="b", score=7)}

    def test_missing_item_id_simply_absent(self, monkeypatch: pytest.MonkeyPatch):
        def fake_chat_structured(role, schema, messages, **kw):
            return schema.model_validate({"items": [{"item_id": 1, "domain": "a"}]})

        monkeypatch.setattr(oc, "chat_structured", fake_chat_structured)
        result = oc.chat_structured_batch("resident", _Out, [(1, "p1"), (2, "p2")], system="sys")
        assert set(result) == {1}

    def test_prompt_includes_item_id_markers(self, monkeypatch: pytest.MonkeyPatch):
        captured = {}

        def fake_chat_structured(role, schema, messages, **kw):
            captured["messages"] = messages
            return schema.model_validate({"items": []})

        monkeypatch.setattr(oc, "chat_structured", fake_chat_structured)
        oc.chat_structured_batch("light", _Out, [(42, "hello world")], system="sys")
        user_msg = captured["messages"][1]["content"]
        assert "item_id=42" in user_msg
        assert "hello world" in user_msg


class TestIsCloudBatchMode:
    def test_local_mode_false(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            oc, "settings", lambda: SimpleNamespace(llm_providers=SimpleNamespace(mode="local"))
        )
        assert oc.is_cloud_batch_mode() is False

    def test_cloud_mode_true(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            oc, "settings", lambda: SimpleNamespace(llm_providers=SimpleNamespace(mode="cloud"))
        )
        assert oc.is_cloud_batch_mode() is True


class TestClassifyBatchWiring:
    def test_local_mode_run_classify_never_calls_batch(self, monkeypatch: pytest.MonkeyPatch):
        from eoa.pipeline import classify

        monkeypatch.setattr(classify, "is_cloud_batch_mode", lambda: False)
        monkeypatch.setattr(classify, "get_items_for_stage", lambda stage, limit, item_ids=None: [])

        def boom(*a, **k):
            raise AssertionError("classify_batch must not run in local mode")

        monkeypatch.setattr(classify, "classify_batch", boom)
        stats = classify.run_classify()
        assert stats.done == 0

    def test_cloud_mode_run_classify_uses_batch_and_persists(self, monkeypatch: pytest.MonkeyPatch):
        from eoa.llm.schemas.analysis import ClassifyOut
        from eoa.pipeline import classify

        items = [
            {"id": 1, "title": "a", "clean_text": "x", "url": "http://a"},
            {"id": 2, "title": "b", "clean_text": "y", "url": "http://b"},
        ]
        monkeypatch.setattr(classify, "is_cloud_batch_mode", lambda: True)
        monkeypatch.setattr(classify, "get_items_for_stage", lambda stage, limit, item_ids=None: items)
        marked = []
        monkeypatch.setattr(classify, "mark_stage", lambda item_id, stage: marked.append(item_id))
        persisted = []
        monkeypatch.setattr(
            classify,
            "persist_classification",
            lambda item, out: persisted.append(item["id"] if isinstance(item, dict) else item),
        )
        monkeypatch.setattr(classify, "update_item_fields", lambda *a, **k: None)

        def fake_batch(chunk, *, role):
            return {
                it["id"]: ClassifyOut(
                    domain="airborne_pods",
                    subdomain="",
                    dimensions=[],
                    tags=[],
                    report_kind="verified_report",
                    trl="unknown",
                    geography="US",
                    entities=[],
                    one_line_he="x",
                    relevance_note="x",
                )
                for it in chunk
            }

        monkeypatch.setattr(classify, "classify_batch", fake_batch)
        stats = classify.run_classify()
        assert stats.done == 2
        assert set(persisted) == {1, 2}
        assert set(marked) == {1, 2}

    def test_cloud_mode_batch_failure_counts_as_failed_and_continues(self, monkeypatch: pytest.MonkeyPatch):
        from eoa.errors import LLMOutputError
        from eoa.pipeline import classify

        items = [{"id": i, "title": "x", "clean_text": "y", "url": "http://x"} for i in range(1, 3)]
        monkeypatch.setattr(classify, "is_cloud_batch_mode", lambda: True)
        monkeypatch.setattr(classify, "get_items_for_stage", lambda stage, limit, item_ids=None: items)
        monkeypatch.setattr(classify, "mark_stage", lambda *a, **k: None)

        def fail_batch(chunk, *, role):
            raise LLMOutputError("schema exhausted")

        monkeypatch.setattr(classify, "classify_batch", fail_batch)
        stats = classify.run_classify()
        assert stats.failed == 2
        assert stats.done == 0


class TestTriageBatchWiring:
    def test_local_mode_never_calls_batch(self, monkeypatch: pytest.MonkeyPatch):
        from eoa.pipeline import triage

        monkeypatch.setattr(triage, "is_cloud_batch_mode", lambda: False)
        monkeypatch.setattr(triage, "get_items_for_stage", lambda stage, limit, item_ids=None: [])

        def boom(*a, **k):
            raise AssertionError("triage_batch must not run in local mode")

        monkeypatch.setattr(triage, "triage_batch", boom)
        stats = triage.run_triage()
        assert stats.done == 0

    def test_cloud_mode_persists_and_enqueues_deep_search_for_red(self, monkeypatch: pytest.MonkeyPatch):
        from eoa.llm.schemas.analysis import TriageOut
        from eoa.pipeline import triage

        items = [{"id": 1, "domain": "airborne_pods", "title": "t", "clean_text": "x", "url": "http://x"}]
        monkeypatch.setattr(triage, "is_cloud_batch_mode", lambda: True)
        monkeypatch.setattr(triage, "get_items_for_stage", lambda stage, limit, item_ids=None: items)
        monkeypatch.setattr(triage, "mark_stage", lambda *a, **k: None)
        monkeypatch.setattr(triage, "update_item_fields", lambda *a, **k: None)
        enqueued = []
        monkeypatch.setattr(triage, "_enqueue_deep_search", lambda item, out: enqueued.append(item["id"]))

        def fake_batch(chunk, *, role):
            return {
                it["id"]: TriageOut(
                    score=9,
                    level="red",
                    novelty=4,
                    magnitude=4,
                    core_relevance=5,
                    reason_he="x",
                    needs_deep_search=False,
                )
                for it in chunk
            }

        monkeypatch.setattr(triage, "triage_batch", fake_batch)
        stats = triage.run_triage()
        assert stats.done == 1
        assert stats.red == 1
        assert enqueued == [1]


class TestAnalyzeBatchWiring:
    def test_local_mode_never_calls_batch(self, monkeypatch: pytest.MonkeyPatch):
        from eoa.pipeline import analyze

        monkeypatch.setattr(analyze, "is_cloud_batch_mode", lambda: False)
        monkeypatch.setattr(analyze, "get_items_for_stage", lambda stage, limit: [])

        def boom(*a, **k):
            raise AssertionError("analyze_batch must not run in local mode")

        monkeypatch.setattr(analyze, "analyze_batch", boom)
        stats = analyze.run_analyze()
        assert stats.done == 0

    def test_cloud_mode_persists_events_and_edges(self, monkeypatch: pytest.MonkeyPatch):
        from eoa.llm.schemas.analysis import AnalyzeOut
        from eoa.pipeline import analyze

        # clean_text must be long enough to clear Q3-10's content-quality precheck (see
        # eoa.fetch.content_quality.STUB_MAX_CHARS=400) -- a one-character body is classified
        # 'stub' and skipped by run_analyze() before it ever reaches the batch path this test
        # exercises.
        items = [
            {
                "id": 1,
                "level": "yellow",
                "title": "t",
                "clean_text": "x" * 500,
                "url": "http://x",
            }
        ]
        monkeypatch.setattr(analyze, "is_cloud_batch_mode", lambda: True)
        monkeypatch.setattr(analyze, "get_items_for_stage", lambda stage, limit: items)
        monkeypatch.setattr(analyze, "mark_stage", lambda *a, **k: None)
        monkeypatch.setattr(analyze, "update_item_fields", lambda *a, **k: None)
        monkeypatch.setattr(analyze, "persist_analysis", lambda it, out: (2, 1))

        def fake_batch(chunk, *, role):
            return {
                it["id"]: AnalyzeOut(summary_he="s", so_what_he="w", key_facts=[], events=[], edges=[])
                for it in chunk
            }

        monkeypatch.setattr(analyze, "analyze_batch", fake_batch)
        stats = analyze.run_analyze()
        assert stats.done == 1
        assert stats.events == 2
        assert stats.edges == 1
