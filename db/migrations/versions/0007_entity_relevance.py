"""entity relevance scoring + watchlist flag (F15); widen entities.kind to allow 'country'

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-05

Part of the Entities & Graph redesign (docs/REVIEW_2026-09-05.md U10/F15): irrelevant
entities picked up by NER (e.g. "Zipline", a delivery-drone company mentioned in an
unrelated Houston-highway news item) currently enter the graph with the same visual
weight as a tracked watchlist company. `eoa.pipeline.entity_relevance` scores every
entity 0..1 from watchlist match / in-scope mention fraction / kind / mention count;
this migration adds the two columns it persists to, plus a supporting index so the
Entities list's default `relevance >= 0.4` filter (and the watchlist-only toggle) can
use an index scan instead of a full table scan.

**kind CHECK widened to include 'country'.** `eoa.llm.schemas.analysis.EntityMention.kind`
has allowed `"country"` since it was introduced, but `entities_kind_check` (migration
0001) never did -- so any entity the LLM classified as a country has always been
silently dropped by `upsert_entity`'s broad `except Exception` in
`eoa.pipeline.classify.persist_classification` (never invented, but also never
persisted; discovered while wiring up the U10 kind facet, which explicitly lists
`company/program/agency/system/person/country`). This migration fixes that by widening
the constraint; it does not backfill anything (there is nothing to backfill -- rows
with kind='country' were never written). Existing kind values are untouched (`org` is
kept as the underlying value for org/agency entities; the UI is free to label it
"סוכנות/ארגון").
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE entities ADD COLUMN relevance REAL NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE entities ADD COLUMN is_watchlist BOOLEAN NOT NULL DEFAULT false")
    op.execute("CREATE INDEX ix_entities_relevance ON entities (relevance)")
    op.execute("CREATE INDEX ix_entities_is_watchlist ON entities (is_watchlist)")

    op.execute("ALTER TABLE entities DROP CONSTRAINT entities_kind_check")
    op.execute(
        "ALTER TABLE entities ADD CONSTRAINT entities_kind_check "
        "CHECK (kind IN ('company', 'program', 'system', 'person', 'org', 'country'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE entities DROP CONSTRAINT entities_kind_check")
    op.execute(
        "ALTER TABLE entities ADD CONSTRAINT entities_kind_check "
        "CHECK (kind IN ('company', 'program', 'system', 'person', 'org'))"
    )
    op.execute("DROP INDEX IF EXISTS ix_entities_is_watchlist")
    op.execute("DROP INDEX IF EXISTS ix_entities_relevance")
    op.execute("ALTER TABLE entities DROP COLUMN is_watchlist")
    op.execute("ALTER TABLE entities DROP COLUMN relevance")
