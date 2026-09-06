"""patents + patent_watch_topics + patent_surveys (A14: patent landscape/IP tracking)

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-06

NOTE (IL1, 2026-09-06): renumbered from 0017 -> 0018 (down_revision 0016 -> 0017) -- this file
and 0017_israel_relevance.py (A13) were both authored concurrently as "the next migration after
0016", so both originally claimed revision id "0017". 0017_israel_relevance.py was applied to the
live DB first; this file is renumbered to keep a single linear history rather than two competing
heads. No SQL below changed, only the revision/down_revision identifiers and this note.

``patents``: one row per tracked patent/publication (eoa.patents.scan), keyed by ``pub_number``
(the publication/document number as reported by the source -- EPO OPS/PatentsView when configured,
else a Google Patents URL-derived number for the keyless search fallback). ``raw`` keeps the
source payload/snippet for audit; ``claims_summary_he``/``so_what_he``/``value_score``/
``value_reasons``/``israel_relevance`` are filled in by ``eoa.patents.analyze``/``valuation``
(nullable until analyzed -- a freshly-scanned row is a bare record until that runs).

``patent_watch_topics``: user-editable watch topics (``config/patents.yaml`` seeds the defaults;
this table lets the UI/API add ad-hoc ones without a config edit/redeploy).

``patent_surveys``: one row per on-demand "סקר פטנטים" run (``eoa.patents.survey``), linking to the
``reports`` row (``kind='patent_survey'``) that carries the actual rendered docx/md/html -- mirrors
how ``tender_forecasts``/``conferences`` sit alongside their own report integration.

Also widens ``reports.kind``'s CHECK constraint (originally 'daily'/'weekly'/'monthly'/'adhoc',
widened by 0014 to add 'bd_territory') to add 'patent_survey'.
"""

from __future__ import annotations

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE patents (
            id                  BIGSERIAL PRIMARY KEY,
            pub_number          TEXT NOT NULL,
            kind                TEXT,
            title               TEXT,
            abstract            TEXT,
            assignees           TEXT[],
            inventors           TEXT[],
            cpc                 TEXT[],
            priority_date       DATE,
            filing_date         DATE,
            publication_date    DATE,
            grant_date          DATE,
            family_id           TEXT,
            jurisdictions       TEXT[],
            forward_citations   INTEGER,
            backward_citations  INTEGER,
            url                 TEXT,
            source              TEXT NOT NULL DEFAULT 'unknown',
            raw                 JSONB,
            subdomain           TEXT,
            claims_summary_he   TEXT,
            so_what_he          TEXT,
            israel_relevance    REAL,
            value_score         SMALLINT,
            value_reasons       TEXT[],
            entity_ids          BIGINT[],
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (pub_number)
        )
        """
    )
    op.execute("CREATE INDEX ix_patents_publication_date ON patents (publication_date)")
    op.execute("CREATE INDEX ix_patents_subdomain ON patents (subdomain)")
    op.execute("CREATE INDEX ix_patents_value_score ON patents (value_score)")
    op.execute("CREATE INDEX ix_patents_family_id ON patents (family_id)")
    op.execute("CREATE INDEX ix_patents_assignees ON patents USING GIN (assignees)")
    op.execute("CREATE INDEX ix_patents_cpc ON patents USING GIN (cpc)")
    op.execute(
        "CREATE TRIGGER trg_patents_updated_at BEFORE UPDATE ON patents "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    op.execute(
        """
        CREATE TABLE patent_watch_topics (
            id          BIGSERIAL PRIMARY KEY,
            name_he     TEXT NOT NULL,
            query       TEXT NOT NULL,
            cpc         TEXT[],
            enabled     BOOLEAN NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE TRIGGER trg_patent_watch_topics_updated_at BEFORE UPDATE ON patent_watch_topics "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    op.execute(
        """
        CREATE TABLE patent_surveys (
            id          BIGSERIAL PRIMARY KEY,
            topic       TEXT NOT NULL,
            report_id   BIGINT REFERENCES reports(id) ON DELETE SET NULL,
            status      TEXT NOT NULL DEFAULT 'running'
                         CHECK (status IN ('running', 'done', 'failed')),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_patent_surveys_created_at ON patent_surveys (created_at)")

    op.execute("ALTER TABLE reports DROP CONSTRAINT reports_kind_check")
    op.execute(
        "ALTER TABLE reports ADD CONSTRAINT reports_kind_check "
        "CHECK (kind IN ('daily', 'weekly', 'monthly', 'adhoc', 'bd_territory', 'patent_survey'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE reports DROP CONSTRAINT reports_kind_check")
    op.execute(
        "ALTER TABLE reports ADD CONSTRAINT reports_kind_check "
        "CHECK (kind IN ('daily', 'weekly', 'monthly', 'adhoc', 'bd_territory'))"
    )
    op.execute("DROP TABLE IF EXISTS patent_surveys")
    op.execute("DROP TABLE IF EXISTS patent_watch_topics")
    op.execute("DROP TABLE IF EXISTS patents")
