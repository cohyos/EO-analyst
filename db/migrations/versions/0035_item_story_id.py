"""items.story_id: persisted same-story cluster key (2026-09-17, "improve same-story grouping"
task).

A story is a connected component over four edge kinds computed by
``eoa.pipeline.story_clustering.assign_story_ids``: (a) ``dedup_of`` links, (b) cross-source
corroboration links (``item_corroboration.sources[].item_id``), (c) embedding cosine similarity
>= ``clustering.story_embedding_threshold`` within a +/-``clustering.story_window_days`` window,
and (d) cross-language title/entity matching within the same window. ``story_id`` is the minimum
item id in its component -- a stable key that survives re-runs (idempotent) and, unlike
``dedup_of``, never hides an item: it is purely a grouping key for report/API rendering, read by
``eoa.report.clustering.cluster_items`` and ``eoa.api.services.list_items``.

Nullable (never backfilled for an item that hasn't gone through the ``stories`` stage yet, or on
a DB where the migration just landed) and unindexed-by-FK (the id it points at need not itself
still exist as a distinct concept -- it is a component key, not a real reference target -- so this
is a plain ``BIGINT``, not ``REFERENCES items(id)``, mirroring how ``items.dedup_of`` is the only
column here that *is* a real FK).

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-17
"""

from __future__ import annotations

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE items ADD COLUMN story_id BIGINT")
    op.execute("CREATE INDEX ix_items_story_id ON items (story_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_items_story_id")
    op.execute("ALTER TABLE items DROP COLUMN IF EXISTS story_id")
