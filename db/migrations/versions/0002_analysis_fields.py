"""Add analysis detail fields to items and a stop flag helper index on jobs.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("items", sa.Column("key_facts", sa.ARRAY(sa.Text()), nullable=True))
    op.add_column("items", sa.Column("uncertainty_he", sa.Text(), nullable=True))
    op.add_column("items", sa.Column("source_name", sa.Text(), nullable=True))
    op.execute(
        "CREATE OR REPLACE FUNCTION items_fill_source_name() RETURNS trigger AS $$ "
        "BEGIN IF NEW.source_name IS NULL AND NEW.source_id IS NOT NULL THEN "
        "SELECT name INTO NEW.source_name FROM sources WHERE id = NEW.source_id; END IF; RETURN NEW; END; "
        "$$ LANGUAGE plpgsql"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_items_source_name ON items; "
        "CREATE TRIGGER trg_items_source_name BEFORE INSERT ON items "
        "FOR EACH ROW EXECUTE FUNCTION items_fill_source_name()"
    )
    op.create_index("ix_jobs_kind_state", "jobs", ["kind", "state"])


def downgrade() -> None:
    op.drop_index("ix_jobs_kind_state", table_name="jobs")
    op.execute("DROP TRIGGER IF EXISTS trg_items_source_name ON items")
    op.execute("DROP FUNCTION IF EXISTS items_fill_source_name()")
    op.drop_column("items", "source_name")
    op.drop_column("items", "uncertainty_he")
    op.drop_column("items", "key_facts")
