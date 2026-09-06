"""indicator_watchlist table + reports.report_state column (Round 5 P2: D4/D5/W3/M2/B4)

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-06

docs/PLAN_ROUND5_REPORTS.md package P2 / docs/REPORT_TEMPLATE_BENCHMARK.md D4 ("מה השתנה מאז
אתמול"), D5 ("I&W עם סטטוס לאורך זמן"), W3 ("מה השתנה מהשבוע הקודם"), B4 (BD-territory delta,
data side only -- the BD report wiring itself is a later wave per the plan doc).

Two additive, nullable changes -- no backfill, no existing column touched, no existing row
affected (both are entirely new):

1. ``reports.report_state JSONB`` -- the raw material the *next* same-``kind`` (and, for
   ``bd_territory``, same-``territory``) report's delta is computed against
   (``eoa.report.deltas.build_report_state``/``previous_report_state``): ``{item_ids,
   item_levels, trend_titles, indicator_ids}``. ``NULL`` for every report built before this
   migration and for any report kind that never calls ``deltas`` -- a report with no
   ``report_state`` is simply invisible to ``previous_report_state``'s lookup (never a false
   "no items changed" delta).

2. ``indicator_watchlist`` -- one row per forward-looking outlook indicator tracked across
   issues (``eoa.report.indicators``): ``open`` while still unconfirmed, ``matured`` once a
   later issue's own items textually confirm it (cites ``matured_evidence_item_id``), ``dropped``
   once it ages out (30 days, see ``eoa.report.indicators._DROP_AFTER_DAYS``) without a match.
   ``kind`` scopes tracking separately per report cadence (an indicator raised in a daily report
   is not conflated with one raised in a weekly report, even if their text is similar).
"""

from __future__ import annotations

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE reports ADD COLUMN IF NOT EXISTS report_state JSONB")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS indicator_watchlist (
            id                       BIGSERIAL PRIMARY KEY,
            text_he                  TEXT NOT NULL,
            source_report_id         BIGINT REFERENCES reports(id) ON DELETE SET NULL,
            first_seen               TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen                TIMESTAMPTZ NOT NULL DEFAULT now(),
            status                   TEXT NOT NULL DEFAULT 'open'
                                     CHECK (status IN ('open', 'matured', 'dropped')),
            matured_evidence_item_id BIGINT REFERENCES items(id) ON DELETE SET NULL,
            kind                     TEXT NOT NULL CHECK (kind IN ('daily', 'weekly'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_indicator_watchlist_kind_status ON indicator_watchlist (kind, status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_indicator_watchlist_kind_status")
    op.execute("DROP TABLE IF EXISTS indicator_watchlist")
    op.execute("ALTER TABLE reports DROP COLUMN IF EXISTS report_state")
