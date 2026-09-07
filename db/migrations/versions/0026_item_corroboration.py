"""cross-source corroboration: item_corroboration table (2026-09-07 user requirement).

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-07

New feature (user requirement, verbatim intent, 2026-09-07): when an item is brought in,
determine whether other independent sources corroborate it or it is a single-source report, and
surface that everywhere the item appears. This migration adds the one table the deterministic
core (``eoa.pipeline.corroboration``, same PR) reads/writes; it does not touch ``items`` or
``events`` at all -- every corroboration fact for an item lives in exactly one row here, upserted
by item id.

``status`` is one of:
  - ``single_source``    -- no independent corroborating source found (yet).
  - ``corroborated``      -- at least one independent duplicate/same-event source found
                              (``count`` >= 1, ``sources`` lists each one).
  - ``official_primary``  -- the item's own source is itself an official/primary outlet (a
                              government portal, SAM.gov/TED, a company press-release/wire page)
                              -- corroboration from a second outlet is not the relevant question
                              for a primary release.
  - ``unknown``           -- not yet computed, or computed but the item itself lacked enough data
                              (no ``published_at``/domain) to run the check at all.

``sources`` is a JSONB array of ``{item_id, source_name, url, published_at, kind}`` describing
every corroborating item found (``kind`` in 'duplicate' | 'same_event' | 'official') -- empty for
``single_source``/``unknown``, and for ``official_primary`` when the primary-source finding alone
(no second item) is what drove the status.
"""

from __future__ import annotations

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_STATUS_CHECK = "status IN ('single_source', 'corroborated', 'official_primary', 'unknown')"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE item_corroboration (
            item_id     BIGINT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
            status      TEXT NOT NULL DEFAULT 'unknown' CHECK ({_STATUS_CHECK}),
            count       INTEGER NOT NULL DEFAULT 0,
            sources     JSONB NOT NULL DEFAULT '[]',
            method      TEXT,
            checked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_item_corroboration_status ON item_corroboration (status)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_item_corroboration_status")
    op.execute("DROP TABLE IF EXISTS item_corroboration")
