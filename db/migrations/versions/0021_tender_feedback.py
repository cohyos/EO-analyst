"""W2b (docs/REVIEW_2026-09-06_evening.md, user requirement 2026-09-06 18:55, verbatim: "be open --
and through the relevance feedback given to each tender, the system tunes itself"): open tender
intake + relevance feedback + self-tuning threshold/source priority.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-06

``tenders.relevance_score`` (REAL, 0-1): the normalized relevance signal the self-tuning threshold
acts on -- ``extract.relevance / 10`` when the LLM actually classified the notice, or ``0.5`` (the
neutral "unknown, don't presume either way" default -- see ``eoa.tenders.scan``) when the LLM was
deferred/unavailable. Distinct from the pre-existing ``relevance`` SMALLINT (0-10, the F24 gate's
own scale, still written/read as before) -- ``relevance_score`` is additive and only ever consumed
by the new self-tuning machinery in this migration.

``tenders.intake`` (TEXT, default ``'candidate'``): replaces the old "LLM says not relevant ->
never stored" behavior. Every notice that clears the two-signal vocabulary gate is now stored --
``'accepted'`` when ``relevance_score`` already met the (self-tuning) threshold at insert time,
``'candidate'`` otherwise, or ``'rejected-by-user'`` once an operator gives explicit 👎 feedback (see
``tender_feedback`` below). Only a hard rejection (dead link, an explicit awarded/closed
``status_hint``, a denylisted document-hosting/aggregator domain, or an exact duplicate) still
drops a notice outright -- see ``eoa.tenders.scan._gate_reject_reason``'s rewritten docstring.
Existing rows are backfilled from their current ``relevance`` (SMALLINT, 0-10) so a pre-migration
row that already looked relevant enough (>= the migration-time default threshold, 0.6) starts
``'accepted'`` rather than silently regressing to ``'candidate'``.

``tender_feedback``: one append-only row per 👍/👎 an operator gives a tender (``tender_id``,
``verdict`` in (``relevant``, ``irrelevant``), an optional free-text ``reason``, plus a
``source``/``territory``/``matched_terms`` snapshot taken at feedback time so historical feedback
stays interpretable even if the tender row's own fields are later corrected). Never updated or
deleted by application code -- a changed mind is a new row, same "provenance everywhere" spirit as
every other feedback table in this project (docs/CONVENTIONS.md).

``tender_relevance_state``: a single-row (``id = 1``) table holding the current self-tuning
``relevance_threshold`` (starts at 0.6, per the user's own requirement) --
``eoa.tenders.feedback.recompute_relevance_threshold`` overwrites it (a 1-D threshold search over
recent feedback, min 10 samples, clamped 0.3-0.8) after every new feedback row; a fresh install with
no feedback yet keeps the seeded 0.6 default.

``tender_source_priority``: one row per source id that has ever earned a priority decrement (a
source whose last 20 stored notices never drew a single 👍 despite having some feedback) --
``eoa.tenders.scan`` reads this to scan a chronically-irrelevant source last/less eagerly, never to
disable it outright (no code path in this migration or its consumers ever removes/skips a source
based on this table alone).
"""

from __future__ import annotations

from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

# Mirrors eoa.tenders.feedback.DEFAULT_RELEVANCE_THRESHOLD -- kept as a literal here (migrations
# must never import application code) so the one-time backfill below and the seeded
# tender_relevance_state row agree with each other and with the module's own default.
_DEFAULT_RELEVANCE_THRESHOLD = 0.6


def upgrade() -> None:
    # -- tenders.relevance_score / tenders.intake (additive columns + backfill) -----------------
    op.execute("ALTER TABLE tenders ADD COLUMN relevance_score REAL")
    op.execute(
        "ALTER TABLE tenders ADD COLUMN intake TEXT NOT NULL DEFAULT 'candidate' "
        "CHECK (intake IN ('candidate', 'accepted', 'rejected-by-user'))"
    )
    op.execute(
        "UPDATE tenders SET relevance_score = LEAST(1.0, GREATEST(0.0, COALESCE(relevance, 5) / 10.0))"
    )
    op.execute(
        f"UPDATE tenders SET intake = 'accepted' WHERE relevance_score >= {_DEFAULT_RELEVANCE_THRESHOLD}"
    )
    op.execute("CREATE INDEX ix_tenders_intake ON tenders (intake)")

    # -- tender_feedback (append-only, one row per 👍/👎) ----------------------------------------
    op.execute(
        """
        CREATE TABLE tender_feedback (
            id              BIGSERIAL PRIMARY KEY,
            tender_id       BIGINT NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
            verdict         TEXT NOT NULL CHECK (verdict IN ('relevant', 'irrelevant')),
            reason          TEXT,
            source          TEXT,
            territory       TEXT,
            matched_terms   TEXT[],
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_tender_feedback_tender_id ON tender_feedback (tender_id)")
    op.execute("CREATE INDEX ix_tender_feedback_created_at ON tender_feedback (created_at)")

    # -- tender_relevance_state (singleton row: the learned threshold) ---------------------------
    op.execute(
        """
        CREATE TABLE tender_relevance_state (
            id                    SMALLINT PRIMARY KEY DEFAULT 1,
            relevance_threshold   REAL NOT NULL DEFAULT 0.6,
            updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT tender_relevance_state_singleton CHECK (id = 1)
        )
        """
    )
    op.execute(
        "INSERT INTO tender_relevance_state (id, relevance_threshold) VALUES (1, 0.6) "
        "ON CONFLICT (id) DO NOTHING"
    )
    op.execute(
        "CREATE TRIGGER trg_tender_relevance_state_updated_at BEFORE UPDATE ON tender_relevance_state "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    # -- tender_source_priority (one row per source with a learned decrement) --------------------
    op.execute(
        """
        CREATE TABLE tender_source_priority (
            source_id           TEXT PRIMARY KEY,
            priority_decrement  SMALLINT NOT NULL DEFAULT 0,
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE TRIGGER trg_tender_source_priority_updated_at BEFORE UPDATE ON tender_source_priority "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS tender_source_priority")
    op.execute("DROP TABLE IF EXISTS tender_relevance_state")
    op.execute("DROP TABLE IF EXISTS tender_feedback")
    op.execute("DROP INDEX IF EXISTS ix_tenders_intake")
    op.execute("ALTER TABLE tenders DROP COLUMN IF EXISTS intake")
    op.execute("ALTER TABLE tenders DROP COLUMN IF EXISTS relevance_score")
