"""sources.kind: add 'sitemap' (Task B item 1, 2026-09-16) -- several `*_press` sources
(anduril/rafael/iai/thales/rheinmetall/saab/controp, config/sources.yaml) are `verified: false`
for `kind: html` because their listing page renders client-side or sits behind a WAF challenge;
several publish a static `sitemap.xml`/news-sitemap instead (see
`agent/eoa/fetch/sitemap.py` and `docs/qa/content_review/CR-platform-opportunity.md` section 6/7).
This widens the existing `sources_kind_check` CHECK constraint (already covering
'rss'/'html'/'api'/'search') to also allow 'sitemap' so `eoa.fetch.sources_loader.upsert_sources_to_db`
can upsert a `kind: sitemap` entry without violating it.

Revision ID: 0033
Revises: 0032
Create Date: 2026-09-16
"""

from __future__ import annotations

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

_OLD_KINDS = "'rss', 'html', 'api', 'search'"
_NEW_KINDS = "'rss', 'html', 'api', 'search', 'sitemap'"


def upgrade() -> None:
    op.execute("ALTER TABLE sources DROP CONSTRAINT sources_kind_check")
    op.execute(f"ALTER TABLE sources ADD CONSTRAINT sources_kind_check CHECK (kind IN ({_NEW_KINDS}))")


def downgrade() -> None:
    op.execute("ALTER TABLE sources DROP CONSTRAINT sources_kind_check")
    op.execute(f"ALTER TABLE sources ADD CONSTRAINT sources_kind_check CHECK (kind IN ({_OLD_KINDS}))")
