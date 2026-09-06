"""payloads: variant column (W19b -- family/variant drill-down)

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-06

User requirement (2026-09-06 21:20, verbatim): "group the manufacturers' products by families
and allow drill-down, not flooding the operator." ``family`` already existed (migration 0020);
this migration adds the missing third leg, ``variant`` (e.g. "MX-15" for the "WESCAM MX-15" row
whose ``family`` is "MX") -- additive, nullable, no backfill in the migration itself. The actual
values are filled in deterministically by ``eoa.payloads.models.parse_family_variant`` via
``db/seed/seed_payloads.py``'s one-off ``backfill_family_variant()`` pass, same division of
labour as migration 0022's ``image_url``/``spec_url``/``spec_source`` (schema here, data via the
seed script) -- never invented in the migration.

An index on ``family`` (there was none before -- 0020 only indexed ``category``/
``vendor_entity_name``) supports the new ``GET /api/payloads/tree`` grouping and the list
endpoint's new ``family`` filter.
"""

from __future__ import annotations

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE payloads ADD COLUMN IF NOT EXISTS variant TEXT")
    op.execute("CREATE INDEX IF NOT EXISTS ix_payloads_family ON payloads (family)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_payloads_family")
    op.execute("ALTER TABLE payloads DROP COLUMN IF EXISTS variant")
