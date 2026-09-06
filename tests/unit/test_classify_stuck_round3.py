"""Round-3 D1 (docs/qa/loop/round_2_judge.md, items 52/56/57): items whose classify stage is
marked done but whose domain is NULL are re-classified instead of being stranded forever."""

from __future__ import annotations

from eoa.pipeline import classify as mod


def test_run_classify_appends_stuck_unclassified_items(monkeypatch) -> None:
    normal = [{"id": 1, "security_status": "clean", "dedup_of": None}]
    stuck = [
        {"id": 52, "security_status": "clean", "dedup_of": None},
        {"id": 1, "security_status": "clean", "dedup_of": None},  # already in the normal batch
    ]
    monkeypatch.setattr(mod, "get_items_for_stage", lambda stage, limit, item_ids=None: list(normal))
    monkeypatch.setattr(mod, "get_items_stuck_unclassified", lambda limit: list(stuck))
    monkeypatch.setattr(mod, "is_cloud_batch_mode", lambda: False)
    classified: list[int] = []

    def _classify_item(it, role="resident"):
        classified.append(it["id"])
        raise mod.LLMOutputError("stop here")

    monkeypatch.setattr(mod, "classify_item", _classify_item)
    monkeypatch.setattr(mod, "mark_stage", lambda *a, **k: None)
    mod.run_classify(limit=10)
    assert classified == [1, 52]


def test_scoped_run_does_not_pull_stuck_items(monkeypatch) -> None:
    monkeypatch.setattr(mod, "get_items_for_stage", lambda stage, limit, item_ids=None: [])
    called = []
    monkeypatch.setattr(mod, "get_items_stuck_unclassified", lambda limit: called.append(limit) or [])
    monkeypatch.setattr(mod, "is_cloud_batch_mode", lambda: False)
    mod.run_classify(limit=10, item_ids=[5])
    assert called == []
