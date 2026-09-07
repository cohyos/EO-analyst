"""Widen reports.kind CHECK to add 'product_line' (PL-backend follow-up, 2026-09-07).

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-07

Migration 0027 added the ``product_lines`` tagging column but did not widen ``reports.kind``'s own
CHECK constraint (originally 'daily'/'weekly'/'monthly'/'adhoc', widened by 0014 to add
'bd_territory' and by 0018 to add 'patent_survey') -- caught live: the first verification build of
``eoa.report.product_line.build_product_line`` failed with ``psycopg.errors.CheckViolation: new row
for relation "reports" violates check constraint "reports_kind_check"`` when persisting a
``kind='product_line'`` row. Same widen-the-CHECK pattern as 0014/0018, in its own migration (not
folded into 0027, which was already applied to the live DB by the time this was caught).
"""

from __future__ import annotations

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_OLD_KINDS = "'daily', 'weekly', 'monthly', 'adhoc', 'bd_territory', 'patent_survey'"
_NEW_KINDS = f"{_OLD_KINDS}, 'product_line'"


def upgrade() -> None:
    op.execute("ALTER TABLE reports DROP CONSTRAINT reports_kind_check")
    op.execute(f"ALTER TABLE reports ADD CONSTRAINT reports_kind_check CHECK (kind IN ({_NEW_KINDS}))")


def downgrade() -> None:
    op.execute("ALTER TABLE reports DROP CONSTRAINT reports_kind_check")
    op.execute(f"ALTER TABLE reports ADD CONSTRAINT reports_kind_check CHECK (kind IN ({_OLD_KINDS}))")
