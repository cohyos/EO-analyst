"""core schema

Revision ID: 0001
Revises:
Create Date: 2026-09-04

Creates the full relational schema for EO-Analyst: sources/items/entities/events/
contracts/conferences/reports/jobs/run_log/resource_log/model_registry/security_log/
triage_feedback/source_reliability/search_playbook/lessons/investigation_log/
clarifications/feedback_surveys.

2026-09-05 (ADR-004, docs/PLAN_WINDOWS_NATIVE.md step 1a): ``items.embedding`` is a
plain ``REAL[]`` column, not a pgvector ``vector(N)`` column -- no PostgreSQL
extension is created by this migration at all. This lets a fresh
``alembic upgrade head`` succeed against plain PostgreSQL 17 with zero extensions
(the Windows-native target). Cosine similarity is computed in Python/numpy by
``eoa.memory.vector`` instead of via a pgvector operator/HNSW index. Apache AGE
setup (``db/graph_init.sql``) is deprecated for the same reason -- see migration
0006, which also drops the ``vector`` extension on any DB that still has it
(the docker-era DB this migration originally ran against did create it; editing
this already-applied revision only affects *new* databases replaying the chain
from scratch, never a DB that already recorded 0001 in ``alembic_version``).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )

    # -- sources -----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE sources (
            id              BIGSERIAL PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            url             TEXT,
            kind            TEXT NOT NULL CHECK (kind IN ('rss', 'html', 'api', 'search')),
            lang            TEXT,
            reliability     SMALLINT NOT NULL DEFAULT 3,
            active          BOOLEAN NOT NULL DEFAULT true,
            last_fetched_at TIMESTAMPTZ,
            fail_count      INTEGER NOT NULL DEFAULT 0,
            blocklisted     BOOLEAN NOT NULL DEFAULT false,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_sources_kind ON sources (kind)")
    op.execute(
        "CREATE TRIGGER trg_sources_updated_at BEFORE UPDATE ON sources "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # -- items ---------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE items (
            id                  BIGSERIAL PRIMARY KEY,
            source_id           BIGINT REFERENCES sources(id) ON DELETE SET NULL,
            url                 TEXT NOT NULL UNIQUE,
            canonical_url       TEXT,
            title               TEXT,
            lang                TEXT,
            published_at        TIMESTAMPTZ,
            fetched_at          TIMESTAMPTZ,
            raw_text            TEXT,
            clean_text          TEXT,
            text_hash           TEXT,
            summary_he          TEXT,
            so_what_he          TEXT,
            domain              TEXT,
            subdomain           TEXT,
            dimensions          TEXT[],
            tags                TEXT[],
            geography           TEXT,
            report_kind         TEXT,
            trl                 TEXT,
            entities_mentioned  TEXT[],
            score               SMALLINT,
            level               TEXT CHECK (level IN ('red', 'orange', 'yellow', 'archive')),
            triage_reason       TEXT,
            dedup_of            BIGINT REFERENCES items(id) ON DELETE SET NULL,
            embedding           REAL[],
            security_status     TEXT NOT NULL DEFAULT 'clean'
                                 CHECK (security_status IN ('clean', 'flagged', 'quarantined')),
            classification      TEXT NOT NULL DEFAULT 'OSINT',
            processed_stages    TEXT[],
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_items_level_published_at ON items (level, published_at)")
    op.execute("CREATE INDEX ix_items_text_hash ON items (text_hash)")
    op.execute("CREATE INDEX ix_items_source_id ON items (source_id)")
    op.execute("CREATE INDEX ix_items_dedup_of ON items (dedup_of)")
    op.execute("CREATE INDEX ix_items_security_status ON items (security_status)")
    # No index on `embedding` -- it is a plain REAL[] column, scanned/scored in numpy by
    # `eoa.memory.vector` rather than through a pgvector ANN index. See migration 0006.
    op.execute(
        "CREATE TRIGGER trg_items_updated_at BEFORE UPDATE ON items "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # -- entities --------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE entities (
            id              BIGSERIAL PRIMARY KEY,
            name            TEXT NOT NULL UNIQUE,
            kind            TEXT NOT NULL
                             CHECK (kind IN ('company', 'program', 'system', 'person', 'org')),
            country         TEXT,
            aliases         TEXT[],
            focus           TEXT[],
            notes           TEXT,
            first_seen_item BIGINT REFERENCES items(id) ON DELETE SET NULL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_entities_kind ON entities (kind)")
    op.execute(
        "CREATE TRIGGER trg_entities_updated_at BEFORE UPDATE ON entities "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # -- events ------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE events (
            id          BIGSERIAL PRIMARY KEY,
            item_id     BIGINT REFERENCES items(id) ON DELETE CASCADE,
            kind        TEXT CHECK (kind IN (
                            'contract_award', 'm_and_a', 'partnership', 'investment',
                            'launch', 'test', 'deployment', 'regulation', 'other'
                        )),
            title       TEXT,
            date        DATE,
            amount_usd  NUMERIC,
            currency    TEXT,
            parties     TEXT[],
            customer    TEXT,
            program     TEXT,
            summary_he  TEXT,
            confidence  REAL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_events_item_id ON events (item_id)")
    op.execute("CREATE INDEX ix_events_date ON events (date)")
    op.execute("CREATE INDEX ix_events_kind ON events (kind)")
    op.execute(
        "CREATE TRIGGER trg_events_updated_at BEFORE UPDATE ON events "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # -- contracts ---------------------------------------------------------
    op.execute(
        """
        CREATE TABLE contracts (
            id                BIGSERIAL PRIMARY KEY,
            event_id          BIGINT REFERENCES events(id) ON DELETE CASCADE,
            awarder           TEXT,
            awardee           TEXT,
            value_usd         NUMERIC,
            ceiling_usd       NUMERIC,
            period_start      DATE,
            period_end        DATE,
            contract_number   TEXT,
            competitors_lost  TEXT[],
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_contracts_event_id ON contracts (event_id)")
    op.execute(
        "CREATE TRIGGER trg_contracts_updated_at BEFORE UPDATE ON contracts "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # -- conferences ---------------------------------------------------------
    op.execute(
        """
        CREATE TABLE conferences (
            id                  BIGSERIAL PRIMARY KEY,
            name                TEXT NOT NULL UNIQUE,
            organizer           TEXT,
            start_date          DATE,
            end_date            DATE,
            city                TEXT,
            venue               TEXT,
            cadence             TEXT,
            relevance           SMALLINT,
            rationale           TEXT,
            registration_opens  DATE,
            early_bird_deadline DATE,
            cfp_deadline        DATE,
            cost_range          TEXT,
            registration_url    TEXT,
            entry_conditions    TEXT,
            status              TEXT NOT NULL DEFAULT 'estimated'
                                 CHECK (status IN ('confirmed', 'estimated', 'cancelled', 'past')),
            last_verified_at    TIMESTAMPTZ,
            prev_snapshot       JSONB,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_conferences_start_date ON conferences (start_date)")
    op.execute(
        "CREATE TRIGGER trg_conferences_updated_at BEFORE UPDATE ON conferences "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # -- reports -------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE reports (
            id              BIGSERIAL PRIMARY KEY,
            kind            TEXT CHECK (kind IN ('daily', 'weekly', 'monthly', 'adhoc')),
            period_start    DATE,
            period_end      DATE,
            path_docx       TEXT,
            path_md         TEXT,
            path_html       TEXT,
            items_included  BIGINT[],
            qa_passed       BOOLEAN,
            qa_report       JSONB,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_reports_kind_period ON reports (kind, period_start)")

    # -- jobs ------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE jobs (
            id           BIGSERIAL PRIMARY KEY,
            kind         TEXT NOT NULL,
            payload      JSONB,
            state        TEXT NOT NULL DEFAULT 'queued'
                         CHECK (state IN ('queued', 'running', 'done', 'failed', 'deferred', 'partial')),
            priority     SMALLINT NOT NULL DEFAULT 5,
            attempts     INTEGER NOT NULL DEFAULT 0,
            not_before   TIMESTAMPTZ,
            started_at   TIMESTAMPTZ,
            finished_at  TIMESTAMPTZ,
            error        TEXT,
            result       JSONB,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_jobs_state_priority_not_before ON jobs (state, priority, not_before)")
    op.execute(
        "CREATE TRIGGER trg_jobs_updated_at BEFORE UPDATE ON jobs "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # -- run_log -----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE run_log (
            id            BIGSERIAL PRIMARY KEY,
            job_id        BIGINT REFERENCES jobs(id) ON DELETE CASCADE,
            stage         TEXT,
            event         TEXT,
            detail        JSONB,
            heartbeat_at  TIMESTAMPTZ,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_run_log_job_id ON run_log (job_id)")

    # -- resource_log --------------------------------------------------------
    op.execute(
        """
        CREATE TABLE resource_log (
            id            BIGSERIAL PRIMARY KEY,
            decision      TEXT CHECK (decision IN (
                              'proceed', 'queued', 'deferred', 'swap', 'throttled', 'thermal_pause'
                          )),
            model         TEXT,
            vram_free_mb  INTEGER,
            gpu_util      SMALLINT,
            gpu_temp      SMALLINT,
            ram_free_mb   INTEGER,
            wait_ms       INTEGER,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_resource_log_created_at ON resource_log (created_at)")

    # -- model_registry --------------------------------------------------------
    op.execute(
        """
        CREATE TABLE model_registry (
            key          TEXT PRIMARY KEY,
            ollama_name  TEXT,
            vendor       TEXT,
            origin       TEXT,
            license      TEXT,
            digest       TEXT,
            size_bytes   BIGINT,
            verified_at  TIMESTAMPTZ
        )
        """
    )

    # -- security_log --------------------------------------------------------
    op.execute(
        """
        CREATE TABLE security_log (
            id          BIGSERIAL PRIMARY KEY,
            item_id     BIGINT REFERENCES items(id) ON DELETE SET NULL,
            source_id   BIGINT REFERENCES sources(id) ON DELETE SET NULL,
            layer       TEXT CHECK (layer IN ('l1', 'heuristic', 'l2')),
            verdict     TEXT,
            score       REAL,
            excerpt     TEXT,
            action      TEXT CHECK (action IN ('flagged', 'quarantined', 'blocklisted')),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_security_log_item_id ON security_log (item_id)")
    op.execute("CREATE INDEX ix_security_log_source_id ON security_log (source_id)")

    # -- triage_feedback -------------------------------------------------------
    op.execute(
        """
        CREATE TABLE triage_feedback (
            id          BIGSERIAL PRIMARY KEY,
            item_id     BIGINT REFERENCES items(id) ON DELETE CASCADE,
            user_level  TEXT,
            agent_level TEXT,
            comment     TEXT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_triage_feedback_item_id ON triage_feedback (item_id)")

    # -- source_reliability ------------------------------------------------
    op.execute(
        """
        CREATE TABLE source_reliability (
            id            BIGSERIAL PRIMARY KEY,
            source_id     BIGINT REFERENCES sources(id) ON DELETE CASCADE,
            date          DATE,
            items         INTEGER,
            confirmed     INTEGER,
            contradicted  INTEGER,
            score         REAL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_source_reliability_source_id ON source_reliability (source_id)")

    # -- search_playbook -----------------------------------------------------
    op.execute(
        """
        CREATE TABLE search_playbook (
            id             BIGSERIAL PRIMARY KEY,
            pattern        TEXT,
            strategy       TEXT,
            lang           TEXT,
            success_count  INTEGER NOT NULL DEFAULT 0,
            fail_count     INTEGER NOT NULL DEFAULT 0,
            last_used      TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # -- lessons -----------------------------------------------------------
    op.execute(
        """
        CREATE TABLE lessons (
            id          BIGSERIAL PRIMARY KEY,
            kind        TEXT CHECK (kind IN ('calibration', 'watchlist', 'style', 'decision', 'meta')),
            text        TEXT,
            source_ref  TEXT,
            active      BOOLEAN NOT NULL DEFAULT true,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_lessons_kind_active ON lessons (kind, active)")

    # -- investigation_log -----------------------------------------------------
    op.execute(
        """
        CREATE TABLE investigation_log (
            id            BIGSERIAL PRIMARY KEY,
            job_id        BIGINT REFERENCES jobs(id) ON DELETE CASCADE,
            trigger_item  BIGINT REFERENCES items(id) ON DELETE SET NULL,
            round         SMALLINT,
            lang          TEXT,
            query         TEXT,
            engine        TEXT,
            results_n     INTEGER,
            pages_read    INTEGER,
            outcome       TEXT CHECK (outcome IN (
                              'found', 'partial', 'not_found', 'stopped_budget', 'stopped_timeout'
                          )),
            notes         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_investigation_log_job_id ON investigation_log (job_id)")
    op.execute("CREATE INDEX ix_investigation_log_trigger_item ON investigation_log (trigger_item)")

    # -- clarifications ------------------------------------------------------
    op.execute(
        """
        CREATE TABLE clarifications (
            id            BIGSERIAL PRIMARY KEY,
            kind          TEXT,
            question      TEXT,
            options       JSONB,
            answer        TEXT,
            asked_at      TIMESTAMPTZ,
            answered_at   TIMESTAMPTZ,
            timeout_at    TIMESTAMPTZ,
            assumed       BOOLEAN NOT NULL DEFAULT false,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    # -- feedback_surveys ------------------------------------------------------
    op.execute(
        """
        CREATE TABLE feedback_surveys (
            id           BIGSERIAL PRIMARY KEY,
            report_id    BIGINT REFERENCES reports(id) ON DELETE CASCADE,
            questions    JSONB,
            answers      JSONB,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            answered_at  TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_feedback_surveys_report_id ON feedback_surveys (report_id)")


def downgrade() -> None:
    for table in (
        "feedback_surveys",
        "clarifications",
        "investigation_log",
        "lessons",
        "search_playbook",
        "source_reliability",
        "triage_feedback",
        "security_log",
        "model_registry",
        "resource_log",
        "run_log",
        "jobs",
        "reports",
        "conferences",
        "contracts",
        "events",
        "entities",
        "items",
        "sources",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
    op.execute("DROP EXTENSION IF EXISTS vector")
