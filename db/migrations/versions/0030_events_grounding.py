"""Widen events.kind CHECK to add 'appointment'/'financial_results'; add events.source_item_ids
(CR-events, round-14 grounding repair, 2026-09-07).

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-07

Two changes, both required by ``eoa.pipeline.event_grounding`` (docs/qa/content_review/
CR-factcheck.md; the independent fact-check pass that found events.id 22/23 -- a personnel
appointment article turned into a fabricated $10B "m_and_a" event against "Israel Ministry of
Defense and IDF" -- plus the Elbit-Anduril Sigma-155 co-marketing partnership mislabeled
'm_and_a', and Elbit's $32B backlog disclosure (item 114) mislabeled 'regulation'):

1. ``events.kind`` CHECK (0001_core.py: contract_award/m_and_a/partnership/investment/launch/test/
   deployment/regulation/other) widened with two more real business-event shapes that the existing
   9-value enum has no honest slot for -- an executive/personnel appointment (previously forced
   into 'm_and_a' or 'other', the direct root cause of the events-22/23 fabrication above) and an
   earnings/backlog/financial-results disclosure (previously forced into 'regulation', the item-114
   root cause above). Same widen-the-CHECK pattern as 0014/0018/0028/0029.

2. ``events.source_item_ids BIGINT[] NOT NULL DEFAULT '{}'`` -- ``events`` has always had exactly
   one ``item_id`` FK (0001_core.py), so a business event independently reported by two or more
   items (e.g. events 54/107, the same $270M Elbit SPECTRO/ISR contract reported by
   airforce-technology.com on 2026-09-01 and israeldefense.co.il on 2026-09-02 one day apart --
   CR-factcheck.md monthly lines 120/138) had no schema-level place to record that it is one real
   event, not two -- previously always inserted as two separate rows with no cross-reference to
   each other. ``eoa.pipeline.event_grounding``'s cross-source reconciliation helper merges such
   siblings into the earlier row's ``item_id`` and records every *other* contributing item id here
   (the kept row's own ``item_id`` is deliberately never duplicated into this array). A GIN index
   mirrors the ``product_lines`` array column's own indexing choice (0027) for the same
   ``@> ARRAY[...]``-shaped lookup this array will need (e.g. "which events cite item N as a
   secondary source").
"""

from __future__ import annotations

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_OLD_KINDS = (
    "contract_award", "m_and_a", "partnership", "investment", "launch", "test",
    "deployment", "regulation", "other",
)  # fmt: skip
_NEW_KINDS = (*_OLD_KINDS, "appointment", "financial_results")


def _kind_check_sql(kinds: tuple[str, ...]) -> str:
    values = ", ".join(f"'{k}'" for k in kinds)
    return f"CHECK (kind IN ({values}))"


def upgrade() -> None:
    op.execute("ALTER TABLE events DROP CONSTRAINT events_kind_check")
    op.execute(f"ALTER TABLE events ADD CONSTRAINT events_kind_check {_kind_check_sql(_NEW_KINDS)}")
    op.execute("ALTER TABLE events ADD COLUMN source_item_ids BIGINT[] NOT NULL DEFAULT '{}'")
    op.execute("CREATE INDEX ix_events_source_item_ids ON events USING GIN (source_item_ids)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_events_source_item_ids")
    op.execute("ALTER TABLE events DROP COLUMN IF EXISTS source_item_ids")
    op.execute("ALTER TABLE events DROP CONSTRAINT events_kind_check")
    op.execute(f"ALTER TABLE events ADD CONSTRAINT events_kind_check {_kind_check_sql(_OLD_KINDS)}")
