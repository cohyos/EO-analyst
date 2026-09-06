"""sources.last_ok_at (D9 round-1 fix, docs/qa/loop/round_1_fixes.md, sources_recently_fetched)

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-06

``sources.last_fetched_at``/``fail_count`` already existed (migration 0001) but nothing in the
codebase ever wrote them on a successful fetch -- only ``fail_count`` on failure, keyed by source
``name`` (``eoa.fetch.service._bump_fail_count``). D9's ``sources_enabled_fetched_recently`` check
(``eoa.qa.d9_tenders_conferences``) found 0/60 active sources fetched within 7 days at round 0
because of exactly this gap. ``eoa.memory.relational.touch_source_fetched`` (called once per
source per ``eoa.fetch.service._ingest_one_source`` attempt, success or failure) now bumps
``last_fetched_at`` always, ``fail_count`` (reset on success / incremented on failure), and --
this migration -- ``last_ok_at``, a *separate* timestamp for the last successful attempt, so a
source that keeps getting attempted but keeps failing (``last_fetched_at`` recent) can still be
told apart from one that is actually delivering content (``last_ok_at`` recent).

Nullable, no default -- ``NULL`` means "never successfully fetched since this column existed" or
"never fetched at all", both correctly falsy for any freshness check.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sources", sa.Column("last_ok_at", sa.TIMESTAMP(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("sources", "last_ok_at")
