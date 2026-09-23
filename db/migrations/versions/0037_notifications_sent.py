"""notifications_sent: durable idempotency marker for external notifications (N03,
SOL-REVIEW-2026-09-24: jobs/orchestrator/LLM audit round 2).

A worker crash between `eoa.orchestrator.jobs._notify` actually delivering the nightly ntfy push
and the outer `finish_job` call recording the job as `done` leaves the job `running` past its
lease; `reap_stale_jobs` requeues it `deferred` and a later worker reruns the *same* job (same
`job_id`) from `ingest` through `notify` again, including `_notify` itself -- nothing in the job's
own (about to be overwritten) `state`/`result` records that the push already went out. This table
is a small, durable, out-of-band marker keyed `(kind, key)`, checked with an
`INSERT ... ON CONFLICT (kind, key) DO NOTHING` (`eoa.memory.relational.mark_notification_sent`)
*before* a notification is actually sent -- the first caller to insert a given `(kind, key)` pair
sends; every later caller for the same pair sees the conflict and skips.

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-24
"""

from __future__ import annotations

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notifications_sent (
            id          BIGSERIAL PRIMARY KEY,
            kind        TEXT NOT NULL,
            key         TEXT NOT NULL,
            sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (kind, key)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notifications_sent")
