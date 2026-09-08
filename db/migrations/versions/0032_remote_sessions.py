"""remote_sessions: persist ADR-008 remote-access sessions across API restarts (user decision
2026-09-08: the phone was logged out by every restart; sessions now live in the DB with a 30-day TTL).

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-08

``eoa.api.auth`` kept its sessions in a process-local dict (12 h TTL). Ten restarts in one night
of fixes meant ten passcode prompts on the iPhone. The token itself is never stored -- only its
SHA-256 (``token_hash``), so a DB read cannot be replayed as a cookie. Expired rows are pruned
lazily on login.
"""

from __future__ import annotations

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE remote_sessions (
            token_hash   TEXT PRIMARY KEY,
            expires_at   TIMESTAMPTZ NOT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX remote_sessions_expires_at_idx ON remote_sessions (expires_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS remote_sessions")
