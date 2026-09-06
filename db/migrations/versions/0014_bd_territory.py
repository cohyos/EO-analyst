"""add reports.territory column; widen reports.kind CHECK for 'bd_territory' (A11)

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-06

A11's "דוח מיקוד לפיתוח עסקי, מכירה ושיווק לפי טריטוריה" (``eoa.report.bd_territory``) persists
into the existing ``reports`` table (``kind='bd_territory'``) exactly like daily/weekly/monthly,
plus one additive column identifying which territory (ISO-2/region code, normalized via
``eoa.report.geography.normalize_country``) the report covers -- needed so `GET
/api/bd/reports?territory=` and `GET /api/bd/territories` can filter/aggregate without parsing it
back out of ``qa_report`` or a report path.
"""

from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE reports ADD COLUMN territory TEXT")
    op.execute("CREATE INDEX ix_reports_territory ON reports (territory)")

    op.execute("ALTER TABLE reports DROP CONSTRAINT reports_kind_check")
    op.execute(
        "ALTER TABLE reports ADD CONSTRAINT reports_kind_check "
        "CHECK (kind IN ('daily', 'weekly', 'monthly', 'adhoc', 'bd_territory'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE reports DROP CONSTRAINT reports_kind_check")
    op.execute(
        "ALTER TABLE reports ADD CONSTRAINT reports_kind_check "
        "CHECK (kind IN ('daily', 'weekly', 'monthly', 'adhoc'))"
    )
    op.execute("DROP INDEX IF EXISTS ix_reports_territory")
    op.execute("ALTER TABLE reports DROP COLUMN territory")
