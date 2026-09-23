"""DB-backed behavioral test for eoa.pipeline.dedup.link_cross_language (F14, SOL-AUDIT-2026-09-24
/ SOL-REVIEW-2026-09-24: "Add the old-undated-URL refetch test specified by the audit").

The audit's own spec: "Re-fetch an old undated item with two shared entities; assert no link to a
recent cross-language item." `tests/unit/test_dedup_xlang.py` already covers this function's SQL
shape (domain-equality removal) via a fake connection -- this file adds the one behavioral case
that needs a real date comparison, which only means something against actual PostgreSQL
`extract(epoch FROM ...)`/`coalesce(...)` arithmetic, not a fake cursor.

Same never-committed-transaction pattern as test_insert_item_upsert_postgres.py: everything runs
inside one connection that is rolled back and closed in a fixture teardown -- nothing is left in
the shared DB. Skips automatically when `DATABASE_URL` isn't set.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

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
        conn.rollback()
        conn.close()


@pytest.fixture
def dedup_module(pg_conn, monkeypatch):
    from eoa import db as _db

    @contextmanager
    def _reuse(timeout=None):
        yield pg_conn

    monkeypatch.setattr(_db, "connection", _reuse)
    from eoa.pipeline import dedup as _dedup

    return _dedup


def _insert_item(pg_conn, *, url, entities, domain, lang, published_at, created_at) -> int:
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO items (url, entities_mentioned, domain, lang, security_status,
                                published_at, created_at, content_status)
            VALUES (%(url)s, %(entities)s, %(domain)s, %(lang)s, 'clean',
                    %(published_at)s, %(created_at)s, 'full')
            RETURNING id
            """,
            {
                "url": url,
                "entities": entities,
                "domain": domain,
                "lang": lang,
                "published_at": published_at,
                "created_at": created_at,
            },
        )
        return cur.fetchone()["id"]


class TestLinkCrossLanguageOldUndatedRefetch:
    def test_refetched_old_undated_item_does_not_link_to_a_recent_item(self, dedup_module, pg_conn):
        """F14: item A has NO `published_at` (undated) and a `created_at` from long ago -- it is
        genuinely old. It shares >=2 entities with a genuinely RECENT item B in a different
        language. `insert_item`'s `ON CONFLICT (url) DO UPDATE` only ever bumps `fetched_at`, never
        `created_at` -- so simulating "item A was just re-fetched" here (an explicit `fetched_at`
        bump, `created_at`/`published_at` left alone) must NOT make the pair look recent enough to
        link. On the pre-F14 query (which used `i.fetched_at` for this comparison), this WOULD
        link."""
        import uuid

        now = datetime.now(UTC)
        old = now - timedelta(days=400)
        suffix = uuid.uuid4().hex[:8]

        item_a = _insert_item(
            pg_conn,
            url=f"https://sol-review-2026-09-24-test.invalid/xlang-a-{suffix}",
            entities=["Elbit Systems", "Rafael"],
            domain="airborne_pods",
            lang="he",
            published_at=None,  # undated
            created_at=old,
        )
        # Simulate a re-fetch bumping `fetched_at` to "now" WITHOUT touching created_at/published_at
        # -- exactly what insert_item's ON CONFLICT path does.
        with pg_conn.cursor() as cur:
            cur.execute(
                "UPDATE items SET fetched_at = %(now)s WHERE id = %(id)s", {"now": now, "id": item_a}
            )

        item_b = _insert_item(
            pg_conn,
            url=f"https://sol-review-2026-09-24-test.invalid/xlang-b-{suffix}",
            entities=["Elbit Systems", "Rafael"],
            domain="airborne_pods",
            lang="en",
            published_at=now,  # genuinely recent
            created_at=now,
        )

        linked = dedup_module.link_cross_language(lookback_days=30)

        with pg_conn.cursor() as cur:
            cur.execute("SELECT dedup_of FROM items WHERE id = %(id)s", {"id": item_b})
            row = cur.fetchone()
        assert row["dedup_of"] is None
        # Sanity: the function ran against real rows (not erroring out silently) -- it just
        # correctly found nothing linkable among OUR two rows. `linked` may be nonzero from
        # unrelated pre-existing rows sharing this same transaction's snapshot, so this only
        # asserts on the specific pair under test above, not the aggregate count.
        del linked


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
