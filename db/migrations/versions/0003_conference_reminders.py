"""conference_reminders dedupe table (FR-12.4)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-04

One row per (conference, reminder kind) actually sent, so ``eoa.conferences.reminders.send_reminders``
never re-fires the same reminder for the same conference occurrence. ``kind`` is one of
``registration_opens`` / ``early_bird`` / ``cfp`` / ``major_conference`` (see reminders.py); each
year's occurrence of a recurring conference is its own row in ``conferences`` (e.g. "AUSA 2026",
"AUSA 2027"), so the unique constraint naturally allows the same kind to fire again next year.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE conference_reminders (
            id          BIGSERIAL PRIMARY KEY,
            conf_id     BIGINT NOT NULL REFERENCES conferences(id) ON DELETE CASCADE,
            kind        TEXT NOT NULL,
            sent_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (conf_id, kind)
        )
        """
    )
    op.execute("CREATE INDEX ix_conference_reminders_conf_id ON conference_reminders (conf_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS conference_reminders")
