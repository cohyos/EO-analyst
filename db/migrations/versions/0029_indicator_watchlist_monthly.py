"""Widen indicator_watchlist.kind CHECK to add 'monthly' (R12-reports #1, round-11 judge D6
worst #3, 2026-09-07).

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-07

Migration 0023 created ``indicator_watchlist`` with ``kind TEXT NOT NULL CHECK (kind IN ('daily',
'weekly'))`` -- correct at the time, since only ``eoa.report.daily``/``eoa.report.weekly`` called
into ``eoa.report.indicators`` at all. Round 12 wires the same "מעקב אינדיקטורים" watchlist section
into ``eoa.report.monthly.build_monthly`` (the round-11 judge's D6 worst #3: the monthly report had
no indicator-tracking section whatsoever) -- ``eoa.report.indicators.process_indicator_watchlist``
inserts a new open row with ``kind='monthly'`` the first time a monthly outlook raises an indicator
that isn't already tracked, which would violate the pre-widen CHECK exactly the way 0028's own
docstring describes for ``reports.kind``/'product_line'. Same widen-the-CHECK pattern, caught before
a live build rather than after.
"""

from __future__ import annotations

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_OLD_KINDS = "'daily', 'weekly'"
_NEW_KINDS = f"{_OLD_KINDS}, 'monthly'"


def upgrade() -> None:
    op.execute("ALTER TABLE indicator_watchlist DROP CONSTRAINT indicator_watchlist_kind_check")
    op.execute(
        f"ALTER TABLE indicator_watchlist ADD CONSTRAINT indicator_watchlist_kind_check "
        f"CHECK (kind IN ({_NEW_KINDS}))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE indicator_watchlist DROP CONSTRAINT indicator_watchlist_kind_check")
    op.execute(
        f"ALTER TABLE indicator_watchlist ADD CONSTRAINT indicator_watchlist_kind_check "
        f"CHECK (kind IN ({_OLD_KINDS}))"
    )
