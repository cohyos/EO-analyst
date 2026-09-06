"""Q3-5/Q3-10/Q3-11 (docs/qa/findings_Q3_r1.md): investigation_log url/title, items.content_status,
tender_forecasts.needs_regen.

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-06

Three independent, additive changes bundled into one migration (all owned by the same fix pass):

* ``investigation_log.url``/``.title`` (Q3-5): a successfully-read page's URL/title, recorded by
  ``eoa.search.deep_search._log_read_url`` right after the row is inserted -- previously
  ``investigation_log`` had no column to hold the URL a `read` tool call actually fetched, which
  is why the historical repair script (``scripts/repair_investigation_sources.py``) cannot fully
  reconstruct ``sources`` for jobs that predate this migration (it can only tell *that* a page was
  read that round, never *which* URL). Both columns are nullable -- most existing rows (searches,
  non-`fetch` engines, and every row before this migration) legitimately have neither.

* ``items.content_status`` (Q3-10): 'full' | 'partial' | 'stub', set by
  ``eoa.fetch.content_quality.assess`` and consumed by ``eoa.pipeline.analyze``'s pre-check.
  Defaults to 'full' so every existing row (and every column-unaware writer) keeps today's
  behaviour -- full analysis -- until backfilled.

* ``tender_forecasts.needs_regen`` (Q3-11): true for a forecast row produced by the deterministic
  fallback rationale generator rather than the LLM; the nightly forecast step re-generates these
  when the LLM is available instead of leaving a generic fallback rationale in place forever.
  Defaults to false so a hand-written/LLM-authored row is never flagged for regen by accident.
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE investigation_log ADD COLUMN url TEXT")
    op.execute("ALTER TABLE investigation_log ADD COLUMN title TEXT")

    op.execute("ALTER TABLE items ADD COLUMN content_status TEXT NOT NULL DEFAULT 'full'")
    op.execute(
        "ALTER TABLE items ADD CONSTRAINT items_content_status_check "
        "CHECK (content_status IN ('full', 'partial', 'stub'))"
    )

    op.execute("ALTER TABLE tender_forecasts ADD COLUMN needs_regen BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE tender_forecasts DROP COLUMN needs_regen")

    op.execute("ALTER TABLE items DROP CONSTRAINT items_content_status_check")
    op.execute("ALTER TABLE items DROP COLUMN content_status")

    op.execute("ALTER TABLE investigation_log DROP COLUMN title")
    op.execute("ALTER TABLE investigation_log DROP COLUMN url")
