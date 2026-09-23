"""Unit tests for eoa.pipeline.dedup.run_dedup / link_cross_language (no DB, no LLM: every
DB-touching or embedding call is monkeypatched; `find_duplicate_in_memory` runs for real, since
it's a pure numpy function).

F08 (crash/retry self-dedup) and E01/N06 (in-memory candidate pool lookback cutoff + replace-on-
re-embed) -- SOL-AUDIT-2026-09-24 / SOL-REVIEW-2026-09-24 round 2 -- were fixed in an earlier round
but had no dedicated test exercising the failure mode; these do.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from eoa.pipeline import dedup


def _cfg(lookback_days: int = 7, cosine_threshold: float = 0.92) -> SimpleNamespace:
    return SimpleNamespace(dedup=SimpleNamespace(lookback_days=lookback_days, cosine_threshold=cosine_threshold))


@pytest.fixture(autouse=True)
def _no_checkpoint(monkeypatch):
    monkeypatch.setattr(dedup, "checkpoint", lambda: None)


# --------------------------------------------------------------------------
# F08: a retry must never match an item against its own just-committed vector.
# --------------------------------------------------------------------------


class TestRetrySelfDedup:
    def test_item_already_in_candidate_pool_never_matches_itself(self, monkeypatch):
        """Simulates the exact F08 crash window: item 5's embedding was already committed by a
        prior (crashed-before-marking-stage-done) run, so it's already present in
        `load_candidate_vectors`'s result (`embedding IS NOT NULL`) -- AND it's re-selected by
        `get_items_for_stage` because its stage marker never got written. A retry re-embeds it to
        the exact same vector. On code that doesn't exclude the item from its own candidate pool
        (or doesn't replace its stale pool entry), this scores a perfect cosine-1.0 self-match and
        wrongly sets `dedup_of = 5` on item 5 itself, hiding its own canonical row."""
        monkeypatch.setattr(dedup, "settings", lambda: _cfg())
        vec = [1.0, 0.0, 0.0]
        monkeypatch.setattr(
            dedup,
            "get_items_for_stage",
            lambda stage, limit, item_ids=None: [
                {"id": 5, "title": "t", "clean_text": "body", "security_status": "clean"}
            ],
        )
        monkeypatch.setattr(
            dedup, "load_candidate_vectors", lambda days: [{"id": 5, "embedding": vec}]
        )
        monkeypatch.setattr(dedup, "embed", lambda texts: [vec])

        committed = {}

        def _fake_commit(item_id, v, dedup_of, stage):
            committed[item_id] = dedup_of

        monkeypatch.setattr(dedup, "commit_dedup_result", _fake_commit)

        stats = dedup.run_dedup(limit=10)

        assert committed[5] != 5
        assert committed[5] is None
        assert stats.embedded == 1


# --------------------------------------------------------------------------
# E01/N06: the in-memory candidate pool enforces the same lookback cutoff as the DB query, and
# replaces (not duplicates) a re-embedded item's stale pool entry.
# --------------------------------------------------------------------------


class TestCandidatePoolLookbackAndReplace:
    def test_out_of_lookback_item_processed_this_run_is_not_added_as_a_candidate(self, monkeypatch):
        """`get_items_for_stage` has no date filter -- an old item (outside `lookback_days`) can
        still be re-selected (e.g. F09's quality-upgrade reprocessing resets its stage). It must
        NOT be appended to the in-memory pool afterward: a later, genuinely recent item in the same
        batch must not be able to match against it just because it happened to be processed first
        in this run. This fails against code that appends every processed item unconditionally."""
        monkeypatch.setattr(dedup, "settings", lambda: _cfg(lookback_days=7))
        now = datetime(2026, 9, 24, tzinfo=UTC)
        old_date = now - timedelta(days=400)
        vec_old = [1.0, 0.0, 0.0]
        vec_recent = [1.0, 0.0, 0.0]  # deliberately identical -- would match at cosine 1.0

        items = [
            {
                "id": 1,
                "title": "old",
                "clean_text": "body",
                "security_status": "clean",
                "published_at": old_date,
                "created_at": old_date,
            },
            {
                "id": 2,
                "title": "recent",
                "clean_text": "body",
                "security_status": "clean",
                "published_at": now,
                "created_at": now,
            },
        ]
        monkeypatch.setattr(dedup, "get_items_for_stage", lambda stage, limit, item_ids=None: items)
        monkeypatch.setattr(dedup, "load_candidate_vectors", lambda days: [])
        vecs_by_call = iter([[vec_old], [vec_recent]])  # batch_size=1 -- one embed() call per item
        monkeypatch.setattr(dedup, "embed", lambda texts: next(vecs_by_call))

        committed = {}
        monkeypatch.setattr(
            dedup, "commit_dedup_result", lambda item_id, v, dedup_of, stage: committed.update({item_id: dedup_of})
        )

        # Process one at a time (batch_size=1) so item 1 is fully committed -- and would be added
        # to the pool if the cutoff weren't enforced -- before item 2 is scored.
        dedup.run_dedup(limit=10, batch_size=1)

        assert committed[1] is None  # nothing to match against yet
        assert committed[2] is None  # must NOT match item 1 -- item 1 is outside the lookback

    def test_reembedded_item_replaces_its_stale_pool_entry(self, monkeypatch):
        """Item 3 already has an OLD vector in the initial DB-loaded pool (simulating a prior embed
        of its now-superseded content, F09's reprocessing case) and is re-embedded this run to a
        NEW, very different vector (its content genuinely changed). A later item 4 in the same
        batch is similar to item 3's OLD content, not its new content. With the fix, only item 3's
        fresh vector remains in the pool, so item 4 does NOT match it. On code that merely appends
        the fresh vector alongside the stale one (instead of replacing it), item 4 would wrongly
        match the leftover stale entry and get linked to item 3 as a duplicate of content item 3
        no longer actually has (N06's "false duplicate links")."""
        monkeypatch.setattr(dedup, "settings", lambda: _cfg(lookback_days=7))
        now = datetime(2026, 9, 24, tzinfo=UTC)
        stale_vec = [0.0, 1.0, 0.0]
        new_vec = [1.0, 0.0, 0.0]  # orthogonal to stale_vec -- item 3's content genuinely changed
        item4_vec = [0.0, 0.99, 0.01]  # near-identical to the STALE vector, not the new one

        items = [
            {
                "id": 3,
                "title": "reembedded",
                "clean_text": "body",
                "security_status": "clean",
                "published_at": now,
                "created_at": now,
            },
            {
                "id": 4,
                "title": "matches item 3's OLD content only",
                "clean_text": "body",
                "security_status": "clean",
                "published_at": now,
                "created_at": now,
            },
        ]
        monkeypatch.setattr(dedup, "get_items_for_stage", lambda stage, limit, item_ids=None: items)
        # Item 3's stale entry is already in the DB-loaded pool under the SAME id.
        monkeypatch.setattr(
            dedup, "load_candidate_vectors", lambda days: [{"id": 3, "embedding": stale_vec}]
        )
        vecs_by_call = iter([[new_vec], [item4_vec]])  # batch_size=1 -- one embed() call per item
        monkeypatch.setattr(dedup, "embed", lambda texts: next(vecs_by_call))

        committed = {}
        monkeypatch.setattr(
            dedup, "commit_dedup_result", lambda item_id, v, dedup_of, stage: committed.update({item_id: dedup_of})
        )

        dedup.run_dedup(limit=10, batch_size=1)

        # Item 4 must NOT match item 3 -- item 3's CURRENT content (new_vec) is dissimilar; only a
        # leftover stale duplicate entry for id 3 would produce a match here.
        assert committed[4] is None


# --------------------------------------------------------------------------
# F14 (missing test, SOL-REVIEW-2026-09-24): cross-language pair dating uses immutable
# published_at/created_at, never the `fetched_at` a same-URL re-fetch bumps.
# --------------------------------------------------------------------------


class _FakeCur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.executed: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, query, params=None):
        self.executed.append(query)
        return _FakeCur(self._rows if "SELECT" in query else [])


class TestLinkCrossLanguageDating:
    def test_query_never_references_fetched_at(self, monkeypatch):
        """F14: `fetched_at` is bumped by `insert_item` on every same-URL re-fetch (including of
        an old, undated URL) -- if the cross-language matcher's date window used it, re-seeing an
        old undated item could make it look newly published and wrongly dedup-link it to a
        genuinely recent item in a different language. This fails against the old (pre-F14) query,
        which referenced `i.fetched_at`."""
        fake_conn = _FakeConn([])
        monkeypatch.setattr("eoa.db.connection", lambda: fake_conn)

        dedup.link_cross_language(lookback_days=30)

        assert fake_conn.executed  # sanity: the SELECT actually ran
        select_sql = fake_conn.executed[0].lower()
        assert "fetched_at" not in select_sql
        assert "coalesce(a.published_at, a.created_at)" in select_sql
        assert "coalesce(b.published_at, b.created_at)" in select_sql


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
