"""notifications_sent payload + next_attempt_at: bounded scheduled retries for failed
notification deliveries (R02, SOL-REVIEW3-2026-09-24 audit round 3, blocker 3).

Migration 0038 gave ``notifications_sent`` a ``pending -> sent | failed`` state machine, so a
``Sent(ok=False)`` delivery (``eoa.notify.ntfy.send`` returning an HTTP error/timeout/unreachable
server without raising) now correctly lands on ``failed`` and flags the run ``partial``. The
review's own finding for this round: landing on ``failed`` was only half the fix -- nothing ever
scheduled another attempt. The job worker (``eoa.orchestrator.jobs.Worker``) only ever claims
``queued``/``deferred`` rows from the ``jobs`` table; a failed notification is not a job, it is a
row in this table, and the job it was sent from (``daily_run``) is typically already terminal
(``partial``) by the time delivery fails, so nothing revisits ``notifications_sent`` again until --
if ever -- that same job happens to be reaped and replayed.

This migration adds the two columns ``eoa.memory.relational.mark_notification_result`` /
``eoa.notify.retry.retry_failed_notifications`` need to close that gap:

* ``payload`` -- ``JSONB``, the exact ``eoa.notify.ntfy.send()`` keyword arguments needed to
  resend this specific message (title/body/priority/tags/click), built once via
  ``eoa.notify.ntfy.build_report_ready``/``build_failure`` at result time and stored alongside the
  outcome, so a later retry replays literally the same notification instead of reconstructing it
  from whatever state happens to exist at retry time (which may have moved on, e.g. different
  headlines). ``NULL`` for every row that predates this migration (and, in practice, for a row
  that never even reaches ``mark_notification_result`` -- caller failure before that point) --
  ``eoa.notify.retry.retry_failed_notifications`` explicitly skips ``payload IS NULL`` rows: there
  is nothing to resend.
* ``next_attempt_at`` -- ``TIMESTAMPTZ``, when a ``failed`` row becomes eligible for the retry
  sweep. Set to ``now() + backoff(attempts)`` on failure (10 min / 30 min / 2 h, see
  ``mark_notification_result``), ``NULL`` on success (a ``sent`` row is terminal) or for a row that
  has never yet failed.

NOT YET APPLIED against the live database as of this commit (task brief: do not run migrations
against the shared dev DB out of band) -- write-only, reviewed alongside the code that needs it.

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-24
"""

from __future__ import annotations

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE notifications_sent
            ADD COLUMN payload JSONB NULL,
            ADD COLUMN next_attempt_at TIMESTAMPTZ NULL
        """
    )
    # eoa.notify.retry.retry_failed_notifications' selection query filters on
    # (status = 'failed' AND payload IS NOT NULL AND (next_attempt_at IS NULL OR next_attempt_at
    # <= now())) every sweep tick (~15 minutes, eoa.orchestrator.main.build_scheduler) -- a
    # partial index on exactly that predicate keeps the sweep cheap even once `sent` rows (the
    # overwhelming majority long-term) pile up, mirroring migration 0038's
    # ix_notifications_sent_pending_failed for the claim path.
    op.execute(
        "CREATE INDEX ix_notifications_sent_retry_due ON notifications_sent (next_attempt_at) "
        "WHERE status = 'failed' AND payload IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notifications_sent_retry_due")
    op.execute(
        """
        ALTER TABLE notifications_sent
            DROP COLUMN IF EXISTS payload,
            DROP COLUMN IF EXISTS next_attempt_at
        """
    )
