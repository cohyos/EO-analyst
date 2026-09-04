"""job leases: worker_id + lease_expires_at on jobs (FR-14 / #15)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-04

Adds a lease owner (`worker_id`) and lease expiry (`lease_expires_at`) to `jobs` so a crashed
worker's `running` job can be detected by `eoa.memory.relational.reap_stale_jobs` instead of
sitting `running` forever. `claim_next_job` sets both at claim time; `heartbeat` extends the
lease while a job is actively progressing; `finish_job` only writes to a row that is still
`running`/`deferred`/`queued`, so a late write from a reaped/stale worker cannot clobber a
newer attempt's result.

Numbered 0005 (not 0004) because revision 0004 was concurrently claimed by
``0004_tenders.py`` (tenders + tender_forecasts); this migration chains after it.
"""

from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE jobs ADD COLUMN worker_id TEXT")
    op.execute("ALTER TABLE jobs ADD COLUMN lease_expires_at TIMESTAMPTZ")
    op.execute("CREATE INDEX ix_jobs_state_lease_expires_at ON jobs (state, lease_expires_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_state_lease_expires_at")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS lease_expires_at")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS worker_id")
