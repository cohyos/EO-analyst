"""A12 (מעקב טכנולוגי, 2026-09-06): additive tech-watch columns on items.

`trl` already existed (0001_core) and is reused as-is for tech_dev items too;
these three columns are new and only ever populated for domain == "tech_dev".

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("tech_maturity", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("tech_actor_kind", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("tech_readiness_note_he", sa.Text(), nullable=True))
    op.execute(
        "ALTER TABLE items ADD CONSTRAINT items_tech_maturity_check "
        "CHECK (tech_maturity IS NULL OR tech_maturity IN ('lab', 'prototype', 'qualified', 'fielded'))"
    )
    op.execute(
        "ALTER TABLE items ADD CONSTRAINT items_tech_actor_kind_check "
        "CHECK (tech_actor_kind IS NULL OR tech_actor_kind IN "
        "('academia', 'lab', 'startup', 'prime', 'government'))"
    )
    # Speeds up the tech radar (subdomain x maturity matrix) and item-list filters.
    op.execute(
        "CREATE INDEX ix_items_domain_subdomain ON items (domain, subdomain) "
        "WHERE domain = 'tech_dev'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_items_domain_subdomain")
    op.execute("ALTER TABLE items DROP CONSTRAINT IF EXISTS items_tech_actor_kind_check")
    op.execute("ALTER TABLE items DROP CONSTRAINT IF EXISTS items_tech_maturity_check")
    op.drop_column("items", "tech_readiness_note_he")
    op.drop_column("items", "tech_actor_kind")
    op.drop_column("items", "tech_maturity")
