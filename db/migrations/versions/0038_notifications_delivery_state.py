"""notifications_sent delivery state: pending/sent/failed + attempts (R02/N03,
SOL-REVIEW2-2026-09-24: jobs/orchestrator/LLM audit round 3).

Migration 0037's ``notifications_sent`` only ever recorded "already sent" -- inserted via
``INSERT ... ON CONFLICT DO NOTHING`` immediately BEFORE the actual ``ntfy.send`` call. The
review's own finding (R02/N03): ``eoa.notify.ntfy.send`` can return ``Sent(ok=False)`` (an HTTP
error, a timeout, an unreachable server -- none of those raise) without ever raising an exception,
so the marker had already committed "sent" by the time the send outcome was known, permanently
suppressing any retry of a delivery that never actually went out.

This migration adds the columns ``eoa.memory.relational.claim_notification_pending`` /
``mark_notification_result`` need for a proper ``pending -> sent | failed`` state machine per
``(kind, key)``:

* ``status`` -- ``pending`` (claimed, delivery in progress), ``sent`` (confirmed OK, terminal),
  or ``failed`` (delivery failed/raised, eligible for a bounded retry). Existing rows (all of them
  under the old semantics represent a confirmed-delivered notification, since the old code only
  ever inserted right before a send whose result was simply never checked) default to ``sent``.
* ``attempts`` -- claim count, bounds the retry loop (``NOTIFICATION_MAX_ATTEMPTS``).
* ``updated_at`` -- when ``status``/``attempts`` last changed; a ``pending`` row older than
  ``NOTIFICATION_PENDING_STALE_MINUTES`` is assumed to be a crashed claim (the worker died before
  ever recording a result) and becomes eligible for reclaim by a later replay.

NOT YET APPLIED against the live database as of this commit (task brief: do not run migrations
against the shared dev DB out of band) -- write-only, reviewed alongside the code that needs it.

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-24
"""

from __future__ import annotations

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE notifications_sent
            ADD COLUMN status TEXT NOT NULL DEFAULT 'sent'
                CHECK (status IN ('pending', 'sent', 'failed')),
            ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1,
            ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        """
    )
    # Backfill: every pre-existing row's `updated_at` should reflect when it was actually
    # recorded, not the moment this migration ran.
    op.execute("UPDATE notifications_sent SET updated_at = sent_at")
    # claim_notification_pending's WHERE clause filters on (kind, key, status, updated_at) for
    # every claim attempt -- the existing UNIQUE (kind, key) already indexes (kind, key); this
    # covers the status/updated_at half of that filter for a `pending`/`failed` row specifically
    # (a `sent` row, the overwhelming majority once the system has been running a while, is never
    # matched by either branch of that WHERE clause and so never needs this index).
    op.execute(
        "CREATE INDEX ix_notifications_sent_pending_failed ON notifications_sent (status, updated_at) "
        "WHERE status IN ('pending', 'failed')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notifications_sent_pending_failed")
    op.execute(
        """
        ALTER TABLE notifications_sent
            DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS attempts,
            DROP COLUMN IF EXISTS updated_at
        """
    )
