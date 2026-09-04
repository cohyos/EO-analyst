"""tenders + tender_forecasts (section 5.2 / FR-5.2 tender & RFI/RFP tracking)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-04

``tenders``: one row per ingested tender/RFI/RFP notice (eoa.tenders.scan), keyed by a
globally-unique ``external_ref`` (``"<source_id>:<notice id>"``). Each tender links to the
``items`` row created alongside it (``item_id``) so the normal classify/triage/analyze pipeline
covers it like any other item.

``tender_forecasts``: deterministic + LLM-rationale forecasts of future tender likelihood
(eoa.tenders.forecast), derived from recent platform-related ``events`` and
``eoa/tenders/platform_payloads.yaml``. Unique on ``(platform, buyer_country, payload_need)`` so a
forecast for the same platform/buyer/payload combination is upserted rather than duplicated as new
events accumulate.
"""

from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tenders (
            id              BIGSERIAL PRIMARY KEY,
            source          TEXT NOT NULL,
            external_ref    TEXT NOT NULL UNIQUE,
            title           TEXT,
            agency          TEXT,
            country         TEXT,
            published_at    TIMESTAMPTZ,
            deadline        DATE,
            url             TEXT,
            cpv_naics       TEXT[],
            summary_he      TEXT,
            relevance       SMALLINT,
            matched_terms   TEXT[],
            entities        TEXT[],
            status          TEXT NOT NULL DEFAULT 'open'
                             CHECK (status IN ('open', 'closed', 'awarded', 'unknown')),
            item_id         BIGINT REFERENCES items(id) ON DELETE SET NULL,
            raw             JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_tenders_status ON tenders (status)")
    op.execute("CREATE INDEX ix_tenders_deadline ON tenders (deadline)")
    op.execute("CREATE INDEX ix_tenders_country ON tenders (country)")
    op.execute("CREATE INDEX ix_tenders_item_id ON tenders (item_id)")
    op.execute(
        "CREATE TRIGGER trg_tenders_updated_at BEFORE UPDATE ON tenders "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    op.execute(
        """
        CREATE TABLE tender_forecasts (
            id                  BIGSERIAL PRIMARY KEY,
            platform            TEXT NOT NULL,
            buyer_country       TEXT,
            trigger_event_id    BIGINT REFERENCES events(id) ON DELETE SET NULL,
            trigger_item_id     BIGINT REFERENCES items(id) ON DELETE SET NULL,
            payload_need        TEXT NOT NULL,
            candidate_vendors   TEXT[],
            likelihood          REAL,
            window_from         DATE,
            window_to           DATE,
            rationale_he        TEXT,
            sources             TEXT[],
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (platform, buyer_country, payload_need)
        )
        """
    )
    op.execute("CREATE INDEX ix_tender_forecasts_likelihood ON tender_forecasts (likelihood)")
    op.execute("CREATE INDEX ix_tender_forecasts_platform ON tender_forecasts (platform)")
    op.execute(
        "CREATE TRIGGER trg_tender_forecasts_updated_at BEFORE UPDATE ON tender_forecasts "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tender_forecasts")
    op.execute("DROP TABLE IF EXISTS tenders")
