"""items.canonical_url index (F36/N10, SOL-REVIEW-2026-09-24: fetch/ingest audit round 2).

``eoa.memory.relational.insert_item`` looks up an existing row by
``canonical_url`` (alias/redirect/tracking-variant identity, F36) on every
quality-aware upsert -- a full sequential scan without an index. A plain
(non-unique) btree index: a read-only check against the live DB on
2026-09-24 found zero populated ``canonical_url`` values yet (F36 has not
processed any live rows), so there is no live-data basis for asserting a
UNIQUE constraint would hold once it does. Uniqueness is enforced instead at
the application layer -- ``insert_item`` now takes a ``pg_advisory_xact_lock``
keyed by the canonical identity before its existing-row lookup, so two
concurrent fetches of two different aliases of the same new article can no
longer both miss the lookup and insert two rows (N10's "concurrent aliases
lack unique canonical identity"). Revisit a UNIQUE index once live data
confirms no duplicates.

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-24
"""

from __future__ import annotations

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain (transactional) CREATE INDEX, matching every other migration in this repo (no
    # `transaction_per_migration`/autocommit env is configured for CONCURRENTLY to be safe here).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_items_canonical_url "
        "ON items (canonical_url) WHERE canonical_url IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_items_canonical_url")
