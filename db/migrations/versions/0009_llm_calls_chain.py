"""extend llm_calls for fallback-chain accounting (U8 Revision 2026-09-06)

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-06

Part of U8-ה/U8-ו (docs/adr/005-cloud-llm-cli.md, "Revision 2026-09-06" section): the global
local/cloud switch routes each role through an ordered fallback chain instead of a single
interactive-only cloud CLI choice, and adds direct-API providers priced per token. Every chain
attempt -- successful or not, including the local Ollama terminal entry when it is reached by a
fallback -- is now logged here, not just a successful cloud CLI call as in 0008. New columns:

* ``attempt_no``        -- 1-based position of this attempt within its chain call.
* ``fell_back_from``    -- the previous entry's provider id, if this attempt followed a failure.
* ``prompt_tokens`` / ``completion_tokens`` -- usage, when the provider/CLI reports it.
* ``est_cost_usd``      -- ``eoa.llm.cost.estimate_cost_usd`` at call time (0 for CLI providers).
* ``batch_size``         -- how many logical items this one call covered (U8-6 batch mode; 1 for
  a plain single-item call).
* ``role``               -- the config role (resident/investigator/light/report) this attempt was
  made for -- needed to break the `GET /api/llm/calls` summary down usefully.
* ``error``              -- the failure message, NULL on a successful attempt (this is how the
  summary endpoint tells a failed attempt from a successful one; there is no separate boolean).

``provider`` may now be ``'ollama'`` (previously excluded by convention, per 0008's docstring --
that only held while every cloud call necessarily succeeded before the chain concept existed).
"""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("attempt_no", "INTEGER"),
    ("fell_back_from", "TEXT"),
    ("prompt_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("completion_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("est_cost_usd", "NUMERIC(12, 6) NOT NULL DEFAULT 0"),
    ("batch_size", "INTEGER NOT NULL DEFAULT 1"),
    ("role", "TEXT"),
    ("error", "TEXT"),
]


def upgrade() -> None:
    for name, ddl_type in _COLUMNS:
        op.execute(f"ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS {name} {ddl_type}")
    op.execute("CREATE INDEX IF NOT EXISTS ix_llm_calls_role ON llm_calls (role)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_llm_calls_role")
    for name, _ in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE llm_calls DROP COLUMN IF EXISTS {name}")
