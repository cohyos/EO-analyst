"""Unit tests for eoa.orchestrator.jobs's F22 post_tenders_catchup mini-stage
(docs/REVIEW_2026-09-05.md): tender-derived items created by the `tenders` stage run after
embed_dedup/classify/triage already ran this night, so without this they'd sit unprocessed until
the next night's stages happen to sweep them up.

No DB, no Ollama: `_tender_items_needing_pipeline`'s own DB probe is exercised separately (with a
fake `eoa.db.connection`); `_post_tenders_catchup` itself is tested with that selection
monkeypatched directly, and the three pipeline stage functions it calls stubbed out.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_jobs_post_tenders_catchup.py -q``
"""

from __future__ import annotations

import pytest

from eoa.orchestrator import jobs


class _FakeCursor:
    def __init__(self, rows: list[dict] | None = None, *, raises: bool = False):
        self._rows = rows or []
        self._raises = raises

    def execute(self, sql, params=None):
        if self._raises:
            raise RuntimeError("db unreachable")

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self, row_factory=None):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --------------------------------------------------------------------------
# _tender_items_needing_pipeline
# --------------------------------------------------------------------------


class TestTenderItemsNeedingPipeline:
    def test_returns_ids_from_query(self, monkeypatch):
        cursor = _FakeCursor([{"id": 11}, {"id": 12}])
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConn(cursor))
        assert jobs._tender_items_needing_pipeline() == [11, 12]

    def test_empty_when_no_rows(self, monkeypatch):
        cursor = _FakeCursor([])
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConn(cursor))
        assert jobs._tender_items_needing_pipeline() == []

    def test_empty_on_db_error_best_effort(self, monkeypatch):
        cursor = _FakeCursor(raises=True)
        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConn(cursor))
        assert jobs._tender_items_needing_pipeline() == []


# --------------------------------------------------------------------------
# _post_tenders_catchup
# --------------------------------------------------------------------------


class TestPostTendersCatchup:
    def test_no_items_short_circuits_without_running_any_stage(self, monkeypatch):
        monkeypatch.setattr(jobs, "_tender_items_needing_pipeline", lambda: [])

        def _fail(*a, **kw):
            raise AssertionError("no stage should run when there are no candidate items")

        monkeypatch.setattr("eoa.pipeline.dedup.run_dedup", _fail)
        monkeypatch.setattr("eoa.pipeline.classify.run_classify", _fail)
        monkeypatch.setattr("eoa.pipeline.triage.run_triage", _fail)

        assert jobs._post_tenders_catchup() == {"items": 0}

    def test_runs_all_three_stages_scoped_to_the_selected_ids(self, monkeypatch):
        monkeypatch.setattr(jobs, "_tender_items_needing_pipeline", lambda: [201, 202])

        calls: dict[str, dict] = {}

        class _Stats:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        def _fake_dedup(*, item_ids):
            calls["dedup"] = {"item_ids": item_ids}
            return _Stats(embedded=2, duplicates=0, failed=0)

        def _fake_classify(*, role, item_ids):
            calls["classify"] = {"role": role, "item_ids": item_ids}
            return _Stats(done=2, out_of_scope=0, failed=0)

        def _fake_triage(*, role, item_ids):
            calls["triage"] = {"role": role, "item_ids": item_ids}
            return _Stats(done=2, red=0, orange=1, failed=0)

        monkeypatch.setattr("eoa.pipeline.dedup.run_dedup", _fake_dedup)
        monkeypatch.setattr("eoa.pipeline.classify.run_classify", _fake_classify)
        monkeypatch.setattr("eoa.pipeline.triage.run_triage", _fake_triage)

        out = jobs._post_tenders_catchup(role="resident")

        assert out["items"] == 2
        assert calls["dedup"]["item_ids"] == [201, 202]
        assert calls["classify"] == {"role": "resident", "item_ids": [201, 202]}
        assert calls["triage"] == {"role": "resident", "item_ids": [201, 202]}
        assert out["embed_dedup"]["embedded"] == 2
        assert out["classify"]["done"] == 2
        assert out["triage"]["orange"] == 1

    def test_one_stage_failing_does_not_block_the_others(self, monkeypatch):
        monkeypatch.setattr(jobs, "_tender_items_needing_pipeline", lambda: [301])

        class _Stats:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        def _boom(*, item_ids):
            raise RuntimeError("embed backend unavailable")

        monkeypatch.setattr("eoa.pipeline.dedup.run_dedup", _boom)
        monkeypatch.setattr(
            "eoa.pipeline.classify.run_classify",
            lambda *, role, item_ids: _Stats(done=1, out_of_scope=0, failed=0),
        )
        monkeypatch.setattr(
            "eoa.pipeline.triage.run_triage",
            lambda *, role, item_ids: _Stats(done=1, red=0, orange=0, failed=0),
        )

        out = jobs._post_tenders_catchup()

        assert "embed_dedup_error" in out
        assert out["classify"]["done"] == 1
        assert out["triage"]["done"] == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
