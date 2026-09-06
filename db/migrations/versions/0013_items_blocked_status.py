"""Q4-1 (docs/qa/findings_Q4_r1.md): add 'blocked' to items.security_status.

A fetched page that is actually a Cloudflare/WAF/anti-bot challenge interstitial (not the
article) is detected by `eoa.fetch.sanitize.detect_block_page` and stored with
`security_status='blocked'`, `clean_text=NULL`, and a neutral Hebrew title -- see
`eoa.fetch.service._store_item`. This just widens the CHECK constraint; no existing row is
touched (the backfill for already-stored blocked items is `scripts/repair_blocked_items.py`,
run separately).

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE items DROP CONSTRAINT items_security_status_check")
    op.execute(
        "ALTER TABLE items ADD CONSTRAINT items_security_status_check "
        "CHECK (security_status IN ('clean', 'flagged', 'quarantined', 'blocked'))"
    )


def downgrade() -> None:
    op.execute("UPDATE items SET security_status = 'flagged' WHERE security_status = 'blocked'")
    op.execute("ALTER TABLE items DROP CONSTRAINT items_security_status_check")
    op.execute(
        "ALTER TABLE items ADD CONSTRAINT items_security_status_check "
        "CHECK (security_status IN ('clean', 'flagged', 'quarantined'))"
    )
