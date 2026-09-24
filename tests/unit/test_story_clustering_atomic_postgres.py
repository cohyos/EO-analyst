"""R05/F20 (SOL-REVIEW2-2026-09-24): `bulk_set_story_ids` + `remap_story_roots` against a REAL
PostgreSQL connection, proving `apply_story_clustering_updates` is atomic.

Old code: `story_clustering.assign_story_ids` called `bulk_set_story_ids(changed)` (its own
`db.connection()`, committed immediately) and THEN, separately, `remap_story_roots(root_remap)`
(a second, separate `db.connection()`). A crash/exception between the two left the in-pool items
already rewritten to the NEW story_id while historical, out-of-pool members stayed on the OLD,
now-abandoned root -- an unrecoverable split. New code runs both UPDATEs on one caller-owned
`conn` inside a single `with connection() as conn:` block (`apply_story_clustering_updates`) -- a
failure partway through rolls the whole transaction back, so NEITHER update commits.

This test forces the second UPDATE (`remap_story_roots`) to raise and asserts the first UPDATE's
effect (the in-pool item's new `story_id`) was rolled back too -- fails against the old,
two-separate-transactions code (the first UPDATE would already be committed by the time the
second one raises).

Skips (does not fail) when DATABASE_URL / a local Postgres is unreachable. Explicitly deletes
every row it creates.

DB-safety note: `remap_story_roots` is a set-based `UPDATE items SET story_id = new WHERE
story_id = old` with NO scoping to this test's own rows -- against the shared, LIVE dev database
(other rows may legitimately already carry any given `story_id`, itself just another item's own
id per `eoa.pipeline.story_clustering`'s docstring), a small/plausible `old`/`new` constant (e.g.
10, 50) can silently re-cluster unrelated real items. Every `story_id` used here is instead drawn
from a `_fresh_story_id()` range far above any real item id (`items.id` is a plain `BIGSERIAL`
currently in the tens of thousands) to make a collision with real data effectively impossible.

Run with:
``DATABASE_URL=... PYTHONPATH=agent python -m pytest tests/unit/test_story_clustering_atomic_postgres.py -q``
"""

from __future__ import annotations

import random
import uuid

import pytest


def _pg_available() -> bool:
    try:
        from eoa.db import ping

        return ping()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="no live Postgres available")


def _fresh_story_id() -> int:
    """A `story_id` value that cannot plausibly collide with any real item id (see the module
    docstring's DB-safety note) -- `items.id` is a `BIGSERIAL` currently in the tens of
    thousands; this draws from a fixed band 100 million above that, randomized per call so two
    values in the same test are also guaranteed distinct."""
    return 100_000_000 + random.randint(0, 50_000_000)


@pytest.fixture()
def two_items():
    """Two rows: `in_pool` (about to be reassigned to a new story root by `bulk_set_story_ids`)
    and `historical` (out of the clustering pool, still on the OLD root -- what
    `remap_story_roots` is supposed to propagate the new root to). Both the "old" and "new" root
    values are fresh, collision-safe `_fresh_story_id()`s -- see the module docstring."""
    from eoa.db import connection

    tag = uuid.uuid4().hex[:8]
    old_root = _fresh_story_id()
    new_root = _fresh_story_id()
    own_prior_story_id = _fresh_story_id()  # in_pool's OWN starting story_id, distinct from both
    with connection() as conn:
        in_pool = conn.execute(
            "INSERT INTO items (url, story_id) VALUES (%s, %s) RETURNING id",
            (f"https://sol-review2-test.invalid/in-pool-{tag}", own_prior_story_id),
        ).fetchone()["id"]
        historical = conn.execute(
            "INSERT INTO items (url, story_id) VALUES (%s, %s) RETURNING id",
            (f"https://sol-review2-test.invalid/historical-{tag}", old_root),
        ).fetchone()["id"]
    try:
        yield {
            "in_pool": in_pool,
            "historical": historical,
            "own_prior_story_id": own_prior_story_id,
            "old_root": old_root,
            "new_root": new_root,
        }
    finally:
        with connection() as conn:
            conn.execute("DELETE FROM items WHERE id = ANY(%s)", ([in_pool, historical],))


class TestApplyStoryClusteringUpdatesAtomicity:
    def test_remap_failure_rolls_back_the_reassignment_too(
        self, two_items: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eoa.db import connection
        from eoa.memory import relational

        in_pool = two_items["in_pool"]
        historical = two_items["historical"]
        own_prior_story_id = two_items["own_prior_story_id"]
        old_root = two_items["old_root"]
        new_root = two_items["new_root"]

        def _boom(root_remap, *, conn=None):
            raise RuntimeError("simulated remap failure")

        monkeypatch.setattr(relational, "remap_story_roots", _boom)

        with pytest.raises(RuntimeError, match="simulated remap failure"):
            relational.apply_story_clustering_updates({in_pool: new_root}, {old_root: new_root})

        # THE regression check: `in_pool`'s reassignment (the first UPDATE) must NOT have
        # committed just because the second UPDATE after it failed -- old code's two separate
        # transactions would already have persisted it here.
        with connection() as conn:
            row = conn.execute("SELECT story_id FROM items WHERE id = %s", (in_pool,)).fetchone()
        assert row["story_id"] == own_prior_story_id, "the reassignment must have been rolled back"

        # And the historical row is untouched either way (remap never ran for real).
        with connection() as conn:
            hist_row = conn.execute("SELECT story_id FROM items WHERE id = %s", (historical,)).fetchone()
        assert hist_row["story_id"] == old_root

    def test_successful_call_commits_both_updates_together(self, two_items: dict) -> None:
        from eoa.db import connection
        from eoa.memory import relational

        in_pool = two_items["in_pool"]
        historical = two_items["historical"]
        old_root = two_items["old_root"]
        new_root = two_items["new_root"]

        n_reassigned, n_remapped = relational.apply_story_clustering_updates(
            {in_pool: new_root}, {old_root: new_root}
        )

        assert n_reassigned == 1
        assert n_remapped == 1
        with connection() as conn:
            in_pool_row = conn.execute("SELECT story_id FROM items WHERE id = %s", (in_pool,)).fetchone()
            hist_row = conn.execute("SELECT story_id FROM items WHERE id = %s", (historical,)).fetchone()
        assert in_pool_row["story_id"] == new_root
        assert hist_row["story_id"] == new_root  # remapped from the old root to the new one
