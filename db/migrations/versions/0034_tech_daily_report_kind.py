"""Widen reports.kind CHECK to add 'tech_daily' (daily EO/IR supply-chain technology-watch
report, user request 2026-09-17 -- "דוח על התפתחויות טכנולוגיות ברמה היומית").

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-17

Same widen-the-CHECK pattern as 0014/0018/0028/0031: ``reports.kind`` was 'daily', 'weekly',
'monthly', 'adhoc', 'bd_territory', 'patent_survey', 'product_line', 'product_dossier' as of
0031 (the latest migration to touch this constraint) -- this adds 'tech_daily' for
``eoa.report.tech_daily.build_tech_daily``.
"""

from __future__ import annotations

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_OLD_KINDS = (
    "'daily', 'weekly', 'monthly', 'adhoc', 'bd_territory', 'patent_survey', 'product_line', "
    "'product_dossier'"
)
_NEW_KINDS = f"{_OLD_KINDS}, 'tech_daily'"


def upgrade() -> None:
    op.execute("ALTER TABLE reports DROP CONSTRAINT reports_kind_check")
    op.execute(f"ALTER TABLE reports ADD CONSTRAINT reports_kind_check CHECK (kind IN ({_NEW_KINDS}))")


def downgrade() -> None:
    op.execute("ALTER TABLE reports DROP CONSTRAINT reports_kind_check")
    op.execute(f"ALTER TABLE reports ADD CONSTRAINT reports_kind_check CHECK (kind IN ({_OLD_KINDS}))")
