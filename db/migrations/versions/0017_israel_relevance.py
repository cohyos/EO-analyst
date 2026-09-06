"""A13 (מיקוד תעשייה ישראלית, 2026-09-06): Israeli-industry focus -- deterministic relevance
scoring for the Israeli EO/IR/defense industry, per docs/PLAN_WINDOWS_NATIVE.md row A13.

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-06

Adds:
  - ``items.israel_relevance`` (REAL, 0..1) -- ``eoa.pipeline.israel_focus.israel_relevance()``'s
    deterministic score (no LLM), computed in ``classify.py`` post-processing and refreshed in
    ``analyze.py``.
  - ``items.israel_reasons`` (TEXT[]) -- the short reason codes behind that score (alias hit,
    export-market signal, Hebrew-language source, etc.), for the report section and UI badge.
  - ``entities.is_israeli`` (BOOLEAN, default false) -- true for a watchlist-Israeli company/
    program or a curated Israeli government/military body, set alongside ``is_watchlist``/
    ``relevance`` by ``eoa.pipeline.israel_focus.score_and_persist_entity_israeli``.

All three are additive/nullable-or-defaulted columns; no existing row shape changes.
"""

from __future__ import annotations

from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE items ADD COLUMN israel_relevance REAL")
    op.execute("ALTER TABLE items ADD COLUMN israel_reasons TEXT[]")
    op.execute("CREATE INDEX ix_items_israel_relevance ON items (israel_relevance)")

    op.execute("ALTER TABLE entities ADD COLUMN is_israeli BOOLEAN NOT NULL DEFAULT false")
    op.execute("CREATE INDEX ix_entities_is_israeli ON entities (is_israeli)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_entities_is_israeli")
    op.execute("ALTER TABLE entities DROP COLUMN is_israeli")

    op.execute("DROP INDEX IF EXISTS ix_items_israel_relevance")
    op.execute("ALTER TABLE items DROP COLUMN israel_reasons")
    op.execute("ALTER TABLE items DROP COLUMN israel_relevance")
