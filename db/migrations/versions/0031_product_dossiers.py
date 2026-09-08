"""product_dossiers table; widen reports.kind CHECK for 'product_dossier' (PD-backend, user request
2026-09-08 -- "סקירת שוק עמוקה למוצר", docs/PLAN_PRODUCT_DOSSIER.md).

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-08

``product_dossiers``: one row per completed (or partial/not_found) run of the on-demand deep
product survey (``eoa.dossier.report.build_product_dossier``), keyed by ``product_key`` (a slug of
vendor+name, e.g. ``elbit-spectro-xr``). Mirrors ``patent_surveys``' own shape (a small side-table
linking to the ``reports`` row that carries the actual rendered docx/md/html) but additionally
stores the full structured ``data`` (``ProductDossierOut``, every fact with its own ``cites``) and
``sources`` (the citation registry) inline as JSONB -- unlike a patent survey, the dossier detail
API (``GET /api/dossiers/{key}``) needs to serve the whole structured record back to the UI
verbatim, not just a report card. ``UNIQUE (product_key, created_at)`` allows multiple runs per
product (reruns) while still giving ``GET /api/dossiers`` a cheap "latest per product_key" lookup
via ``ORDER BY created_at DESC LIMIT 1``.

Also widens ``reports.kind``'s CHECK constraint (widened by 0014/0018/0028 for
'bd_territory'/'patent_survey'/'product_line') to add 'product_dossier' -- same pattern as those.
"""

from __future__ import annotations

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_OLD_KINDS = "'daily', 'weekly', 'monthly', 'adhoc', 'bd_territory', 'patent_survey', 'product_line'"
_NEW_KINDS = f"{_OLD_KINDS}, 'product_dossier'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE product_dossiers (
            id            BIGSERIAL PRIMARY KEY,
            product_key   TEXT NOT NULL,
            product_name  TEXT NOT NULL,
            vendor        TEXT,
            aliases       TEXT[] NOT NULL DEFAULT '{}',
            product_line  TEXT,
            job_id        BIGINT,
            report_id     BIGINT REFERENCES reports(id) ON DELETE SET NULL,
            data          JSONB NOT NULL DEFAULT '{}',
            sources       JSONB NOT NULL DEFAULT '[]',
            outcome       TEXT NOT NULL DEFAULT 'not_found'
                          CHECK (outcome IN ('found', 'partial', 'not_found')),
            confidence    REAL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (product_key, created_at)
        )
        """
    )
    op.execute("CREATE INDEX ix_product_dossiers_product_key ON product_dossiers (product_key)")
    op.execute("CREATE INDEX ix_product_dossiers_created_at ON product_dossiers (created_at)")

    op.execute("ALTER TABLE reports DROP CONSTRAINT reports_kind_check")
    op.execute(f"ALTER TABLE reports ADD CONSTRAINT reports_kind_check CHECK (kind IN ({_NEW_KINDS}))")


def downgrade() -> None:
    op.execute("ALTER TABLE reports DROP CONSTRAINT reports_kind_check")
    op.execute(f"ALTER TABLE reports ADD CONSTRAINT reports_kind_check CHECK (kind IN ({_OLD_KINDS}))")
    op.execute("DROP TABLE IF EXISTS product_dossiers")
