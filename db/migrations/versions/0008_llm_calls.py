"""add llm_calls (U8: cloud LLM provider call log)

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-05

Part of U8 (docs/adr/005-cloud-llm-cli.md): the interactive analyst can optionally route a
question through a cloud CLI (agy/Gemini, claude, codex) instead of local Ollama. Every such
call is logged here for privacy/security visibility -- provider, model, prompt size in
characters, and duration only. The prompt and response text are never stored (same principle
as `security_log.excerpt`, which does store a snippet -- this table deliberately does not,
since a cloud call's whole point is that its content already left the machine; there is no
value in duplicating it at rest and every incentive not to).

Chained after 0007_entity_relevance (another concurrent change) rather than 0006 directly --
that file claimed revision "0007" first.
"""

from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE llm_calls (
            id            BIGSERIAL PRIMARY KEY,
            provider      TEXT NOT NULL,   -- 'agy' | 'claude' | 'codex' (never 'ollama' -- local calls aren't logged here)
            model         TEXT NOT NULL,
            prompt_chars  INTEGER NOT NULL DEFAULT 0,
            duration_ms   INTEGER NOT NULL DEFAULT 0,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_llm_calls_provider ON llm_calls (provider)")
    op.execute("CREATE INDEX ix_llm_calls_created_at ON llm_calls (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS llm_calls CASCADE")
