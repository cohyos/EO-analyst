"""DB-backed tests for eoa.memory.relational.insert_item against a REAL PostgreSQL
(SOL-REVIEW-2026-09-24 round 2, E02/F09): the review specifically criticised the round-1 tests
("F09/E02") for asserting fake-SQL/fragment shape rather than actual PostgreSQL upsert/conflict
behavior. These run every scenario inside one never-committed transaction on the live
`DATABASE_URL`: `eoa.memory.relational.connection` is monkeypatched to hand back that SAME open
psycopg connection (a plain passthrough, no commit/rollback of its own) so every call within one
test chains into the same transaction; a fixture rolls it back and closes it at teardown, so
nothing this file writes is ever left in the shared DB -- no manual cleanup needed, matching this
project's "never leave rows behind" DB-test rule.

Skips automatically when `DATABASE_URL` isn't set. Run with:
``PYTHONPATH=agent python -m pytest tests/unit/test_insert_item_upsert_postgres.py -q``
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set")


@pytest.fixture
def pg_conn():
    import psycopg
    from psycopg.rows import dict_row

    conn = psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row, autocommit=False)
    try:
        yield conn
    finally:
        conn.rollback()  # discard every write this test made -- nothing is ever kept
        conn.close()


@pytest.fixture
def relational(pg_conn, monkeypatch):
    """`eoa.memory.relational`, with its own `connection()` replaced by a passthrough over
    `pg_conn` -- every call in a test runs in the SAME open (never-committed) transaction, so a
    later call in the same test sees an earlier call's writes (real `ON CONFLICT`/upsert
    behavior), and nothing is visible to any other connection until (never) committed."""
    from eoa.memory import relational as _relational

    @contextmanager
    def _reuse(timeout=None):
        yield pg_conn

    monkeypatch.setattr(_relational, "connection", _reuse)
    return _relational


def _url(name: str) -> str:
    return f"https://sol-review-2026-09-24-test.invalid/{name}-{uuid.uuid4().hex[:8]}"


def _row(pg_conn, item_id: int) -> dict:
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT id, url, canonical_url, content_status, text_hash, processed_stages, dedup_of "
            "FROM items WHERE id = %(id)s",
            {"id": item_id},
        )
        return cur.fetchone()


# --------------------------------------------------------------------------
# E02: insert vs. conflict flag against real PostgreSQL (the `xmax = 0` upsert idiom).
# --------------------------------------------------------------------------


class TestInsertConflictFlagPostgres:
    def test_first_call_inserts_second_call_conflicts(self, relational, pg_conn):
        url = _url("e02")

        first = relational.insert_item(source_id=None, url=url, title="first", text_hash="h1")
        assert first.inserted is True

        second = relational.insert_item(source_id=None, url=url, title="second", text_hash="h1")
        assert second.inserted is False
        assert second == first  # same row, not a new one

        with pg_conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM items WHERE url = %(url)s", {"url": url})
            assert cur.fetchone()["n"] == 1


# --------------------------------------------------------------------------
# F09/N04: a same-URL refresh only replaces content when BOTH the quality rank improves AND the
# text_hash actually changed -- an identical re-fetch must never reset processed_stages.
# --------------------------------------------------------------------------


class TestQualityRefreshRequiresHashChangePostgres:
    def test_identical_text_higher_offered_rank_does_not_refresh(self, relational, pg_conn):
        """The N04 regression itself: content unchanged (same text_hash) but this call offers a
        'full' status where the stored row is 'partial' (e.g. analysis downgraded the stored row
        after ingest, and an unpaywalled page's identical re-fetch is unconditionally 'full' at
        ingest) -- with only the round-1 fix (rank-only), this WOULD refresh and reset
        processed_stages every single re-fetch. It must not."""
        url = _url("n04")
        first = relational.insert_item(
            source_id=None,
            url=url,
            text_hash="same-hash",
            content_status="partial",
            security_status="clean",
        )
        relational.mark_stage(int(first), "embed_dedup")

        second = relational.insert_item(
            source_id=None,
            url=url,
            text_hash="same-hash",  # UNCHANGED
            content_status="full",  # offers a higher rank than stored 'partial'
            security_status="clean",
        )
        assert second == first

        row = _row(pg_conn, int(first))
        assert row["content_status"] == "partial"  # unchanged -- refresh did not apply
        assert row["processed_stages"] == ["embed_dedup"]  # NOT reset

    def test_changed_text_and_higher_rank_does_refresh_and_resets_stages(self, relational, pg_conn):
        url = _url("n04b")
        first = relational.insert_item(
            source_id=None,
            url=url,
            text_hash="hash-a",
            content_status="stub",
            security_status="blocked",
        )
        relational.mark_stage(int(first), "embed_dedup")

        second = relational.insert_item(
            source_id=None,
            url=url,
            text_hash="hash-b",  # CHANGED
            content_status="full",  # higher rank than stored 'stub'/'blocked'
            security_status="clean",
        )
        assert second == first

        row = _row(pg_conn, int(first))
        assert row["content_status"] == "full"
        assert row["text_hash"] == "hash-b"
        assert row["processed_stages"] == []  # reset for reprocessing


# --------------------------------------------------------------------------
# N09: canonical_url only changes together with an accepted (quality-better) content update.
# --------------------------------------------------------------------------


class TestCanonicalUrlGuardedByAcceptedContentPostgres:
    def test_worse_refetch_with_a_canonical_url_does_not_rewrite_identity(self, relational, pg_conn):
        url = _url("n09")
        first = relational.insert_item(
            source_id=None,
            url=url,
            text_hash="good-hash",
            content_status="full",
            security_status="clean",
        )
        assert _row(pg_conn, int(first))["canonical_url"] is None

        # A later, WORSE (blocked) re-fetch happens to also carry a canonical_url -- must not be
        # accepted, so the good row's identity must not change.
        second = relational.insert_item(
            source_id=None,
            url=url,
            canonical_url="https://sol-review-2026-09-24-test.invalid/should-not-apply",
            text_hash="blocked-hash",
            content_status="stub",
            security_status="blocked",
        )
        assert second == first

        row = _row(pg_conn, int(first))
        assert row["canonical_url"] is None  # NOT rewritten by the rejected (worse) fetch
        assert row["content_status"] == "full"  # original good content untouched


# --------------------------------------------------------------------------
# R04 (SOL-REVIEW2-2026-09-24, round 3, must-fix): a same-quality redirect must still update
# canonical identity -- N09's fix made canonical_url changes require STRICTLY better rank AND a
# changed text_hash, which also blocked this legitimate case. The review's own required test:
# "full/clean A redirecting to full/clean B, then a direct fetch of B -> one row."
# --------------------------------------------------------------------------


class TestSameQualityRedirectUpdatesCanonicalIdentityPostgres:
    def test_same_quality_redirect_then_direct_fetch_of_target_is_one_row(self, relational, pg_conn):
        suffix = uuid.uuid4().hex[:8]
        url_a = f"https://sol-review2-2026-09-24-test.invalid/a-{suffix}"
        url_b = f"https://sol-review2-2026-09-24-test.invalid/b-{suffix}"

        # A is fetched first (no redirect known yet) -- full/clean, some content.
        first = relational.insert_item(
            source_id=None,
            url=url_a,
            text_hash="hash-same",
            content_status="full",
            security_status="clean",
        )
        assert _row(pg_conn, int(first))["canonical_url"] is None

        # A is fetched again; this time it redirects to B. SAME quality rank (full/clean) and, in
        # the realistic case, unchanged text_hash (it's the same article) -- neither condition the
        # old `excluded_better` gate required (strictly-higher rank, changed hash) is met, yet
        # identity must still move to B.
        second = relational.insert_item(
            source_id=None,
            url=url_a,
            canonical_url=url_b,
            text_hash="hash-same",  # UNCHANGED
            content_status="full",  # SAME rank, not higher
            security_status="clean",
        )
        assert second == first
        assert _row(pg_conn, int(second))["canonical_url"] == url_b

        # A LATER direct fetch of B itself (no redirect: canonical_url == url, so
        # `eoa.fetch.service._store_item` would pass `canonical_url=None` here) must converge on
        # the SAME row, not insert a second one.
        third = relational.insert_item(
            source_id=None,
            url=url_b,
            canonical_url=None,
            text_hash="hash-same",
            content_status="full",
            security_status="clean",
        )
        assert third == first

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM items WHERE url IN (%(a)s, %(b)s) OR canonical_url = %(b)s",
                {"a": url_a, "b": url_b},
            )
            assert cur.fetchone()["n"] == 1

    def test_worse_redirect_still_does_not_update_canonical_identity(self, relational, pg_conn):
        """N09 stays protected under R04's looser (>=) identity gate: a WORSE (blocked) re-fetch's
        canonical_url is still rejected, since a blocked fetch always ranks 0 -- never `>=` a real
        row's rank."""
        url_a = f"https://sol-review2-2026-09-24-test.invalid/worse-a-{uuid.uuid4().hex[:8]}"
        url_b = f"https://sol-review2-2026-09-24-test.invalid/worse-b-{uuid.uuid4().hex[:8]}"

        first = relational.insert_item(
            source_id=None,
            url=url_a,
            text_hash="good-hash",
            content_status="full",
            security_status="clean",
        )

        second = relational.insert_item(
            source_id=None,
            url=url_a,
            canonical_url=url_b,
            text_hash="blocked-hash",
            content_status="stub",
            security_status="blocked",
        )
        assert second == first

        row = _row(pg_conn, int(first))
        assert row["canonical_url"] is None  # NOT rewritten by the rejected (worse) redirect
        assert row["content_status"] == "full"  # original good content untouched


# --------------------------------------------------------------------------
# N05: an accepted content update clears a stale dedup_of link.
# --------------------------------------------------------------------------


class TestDedupOfClearedOnAcceptedUpdatePostgres:
    def test_accepted_refresh_clears_stale_dedup_of(self, relational, pg_conn):
        canonical_target = relational.insert_item(
            source_id=None, url=_url("n05-target"), text_hash="target-hash", content_status="full"
        )
        item = relational.insert_item(
            source_id=None, url=_url("n05-item"), text_hash="hash-a", content_status="stub"
        )
        with pg_conn.cursor() as cur:
            cur.execute(
                "UPDATE items SET dedup_of = %(dedup_of)s WHERE id = %(id)s",
                {"dedup_of": int(canonical_target), "id": int(item)},
            )
        assert _row(pg_conn, int(item))["dedup_of"] == int(canonical_target)

        refreshed = relational.insert_item(
            source_id=None,
            url=_row(pg_conn, int(item))["url"],
            text_hash="hash-b",  # changed
            content_status="full",  # higher rank -- accepted
        )
        assert refreshed == item
        assert _row(pg_conn, int(item))["dedup_of"] is None


# --------------------------------------------------------------------------
# F36/N10: alias-first then a later direct canonical fetch converges on the SAME row -- the core
# real-DB proof that the round-2 fix actually prevents a duplicate row.
# --------------------------------------------------------------------------


class TestAliasThenCanonicalConvergesOnOneRowPostgres:
    def test_alias_first_then_direct_canonical_fetch_does_not_duplicate(self, relational, pg_conn):
        suffix = uuid.uuid4().hex[:8]
        canonical_url = f"https://sol-review-2026-09-24-test.invalid/canon-{suffix}"
        alias_url = f"{canonical_url}?utm_source=newsletter"

        via_alias = relational.insert_item(
            source_id=None,
            url=alias_url,
            canonical_url=canonical_url,
            text_hash="hash-a",
            content_status="full",
        )
        assert via_alias.inserted is True

        # A LATER direct fetch of the canonical URL itself -- `eoa.fetch.service._store_item`
        # passes `canonical_url=None` in exactly this case (identity equals `url`). On the old
        # (round-1) code this fell straight through to a plain `ON CONFLICT (url)` insert and
        # created a SECOND row.
        via_direct = relational.insert_item(
            source_id=None,
            url=canonical_url,
            canonical_url=None,
            text_hash="hash-a",
            content_status="full",
        )

        assert via_direct == via_alias
        assert via_direct.inserted is False

        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM items WHERE url IN (%(a)s, %(c)s) OR canonical_url = %(c)s",
                {"a": alias_url, "c": canonical_url},
            )
            assert cur.fetchone()["n"] == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
