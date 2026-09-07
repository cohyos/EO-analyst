"""product_lines tagging column (PL-backend, user request 2026-09-07): product-line status &
business-development reporting for six EO/IR product lines.

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-07

Adds a ``product_lines TEXT[] NOT NULL DEFAULT '{}'`` column (with a GIN index for the
``@> ARRAY[...]`` containment queries ``eoa.product_lines.stats``/``eoa.report.product_line`` run)
to every table a product line's report/stats need to scope: ``items``, ``events``, ``tenders``,
``tender_forecasts``, ``patents``. Values are the six frozen ids from ``config/product_lines.yaml``
(``targeting_pods``, ``mws_eo``, ``lorop_pods``, ``eo_air_defense_warning``, ``ball_gimbals_16in``,
``border_long_range_eo``) -- never enforced by a DB-level CHECK (the id set is config-driven, not a
DB enum, matching how ``domain``/``subdomain`` are validated in Python against
``config/taxonomy.yaml`` rather than a SQL CHECK).

Tagged by ``eoa.product_lines.tagging.tag_product_lines`` (deterministic, no LLM) via a hook in
``eoa.pipeline.analyze.persist_analysis`` (items + their own newly-inserted events) and
``scripts/backfill_product_lines.py`` (everything already in the DB, plus tenders/tender_forecasts/
patents, which have no equivalent per-item pipeline hook of their own).
"""

from __future__ import annotations

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_TABLES = ("items", "events", "tenders", "tender_forecasts", "patents")


def upgrade() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN product_lines TEXT[] NOT NULL DEFAULT '{{}}'")
        op.execute(f"CREATE INDEX ix_{table}_product_lines ON {table} USING GIN (product_lines)")


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP INDEX IF EXISTS ix_{table}_product_lines")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS product_lines")
