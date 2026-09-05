"""F24 (docs/QA_PROGRAM.md section 4, 2026-09-06): add 'archived' to tenders.status.

A closed tender whose deadline passed more than 30 days ago is archived by
eoa.tenders.scan._archive_stale_closed (the nightly run) -- kept in the DB (never
deleted) but excluded from the tenders board's default view. This just widens the
CHECK constraint; no existing row is touched.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tenders DROP CONSTRAINT tenders_status_check")
    op.execute(
        "ALTER TABLE tenders ADD CONSTRAINT tenders_status_check "
        "CHECK (status IN ('open', 'closed', 'awarded', 'unknown', 'archived'))"
    )


def downgrade() -> None:
    op.execute("UPDATE tenders SET status = 'closed' WHERE status = 'archived'")
    op.execute("ALTER TABLE tenders DROP CONSTRAINT tenders_status_check")
    op.execute(
        "ALTER TABLE tenders ADD CONSTRAINT tenders_status_check "
        "CHECK (status IN ('open', 'closed', 'awarded', 'unknown'))"
    )
