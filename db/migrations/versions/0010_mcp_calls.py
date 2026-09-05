"""add mcp_calls table (A8, docs/adr/006-mcp-sources.md)

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-06

A8's MCP tool layer logs every call the same way ``llm_calls`` (migration 0008/0009) logs every
cloud LLM call: never the arguments or the tool's output text, only enough to audit usage --
server id, tool name, a hash of the arguments, output size, duration, and the guard verdict
(`eoa.security.guard.screen` runs on every MCP tool result exactly like a fetched web page,
docs/CONVENTIONS.md rule #3).
"""

from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_calls (
            id            SERIAL PRIMARY KEY,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            server        TEXT NOT NULL,
            tool          TEXT NOT NULL,
            args_hash     TEXT NOT NULL,
            chars         INTEGER NOT NULL DEFAULT 0,
            duration_ms   INTEGER NOT NULL DEFAULT 0,
            verdict       TEXT NOT NULL DEFAULT 'clean',
            error         TEXT
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_mcp_calls_created_at ON mcp_calls (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_mcp_calls_server ON mcp_calls (server)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mcp_calls")
