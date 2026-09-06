"""Round 5 P7: allow the new `blocked` investigation outcome in investigation_log.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-06 23:05

`eoa.search.deep_search` now finishes an investigation as `blocked` (every page quarantined /
search-gate stop / fully redacted delegated answer) instead of `not_found`; the CHECK constraint
from 0001 only knew found/partial/not_found/stopped_budget/stopped_timeout.
"""

from __future__ import annotations

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_OLD = "outcome IN ('found','partial','not_found','stopped_budget','stopped_timeout')"
_NEW = "outcome IN ('found','partial','not_found','stopped_budget','stopped_timeout','blocked')"


def upgrade() -> None:
    op.execute("ALTER TABLE investigation_log DROP CONSTRAINT IF EXISTS investigation_log_outcome_check")
    op.execute(f"ALTER TABLE investigation_log ADD CONSTRAINT investigation_log_outcome_check CHECK ({_NEW})")


def downgrade() -> None:
    op.execute("UPDATE investigation_log SET outcome = 'not_found' WHERE outcome = 'blocked'")
    op.execute("ALTER TABLE investigation_log DROP CONSTRAINT IF EXISTS investigation_log_outcome_check")
    op.execute(f"ALTER TABLE investigation_log ADD CONSTRAINT investigation_log_outcome_check CHECK ({_OLD})")
