"""drop pgvector dependency; add graph_edges (Apache AGE replacement)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-05

Part of the Windows-native migration (ADR-004, docs/PLAN_WINDOWS_NATIVE.md step 1a):
removes the hard PostgreSQL extension dependencies (pgvector, Apache AGE) so the app
runs on plain PostgreSQL 17.

**Vectors.** A fresh install (migration 0001 onward, as edited 2026-09-05) already
creates ``items.embedding`` as ``REAL[]`` with no extension involved, so on such a
database this half of the migration is a no-op. An *existing* database that ran the
original 0001-0005 chain still has ``items.embedding vector(N)`` and the ``vector``
extension installed; there we add a ``embedding_arr REAL[]`` column, backfill it from
the pgvector column via ``vector -> real[]`` cast (pgvector supports this cast
natively), drop the old ``vector`` column, and rename ``embedding_arr`` to
``embedding``. Both paths converge on the same end state: ``items.embedding REAL[]``,
no ``vector`` extension. The backfill is guarded by an explicit
``SELECT 1 FROM pg_type WHERE typname = 'vector'`` check (not just "column looks like
vector") so this migration cannot fail even in the pathological case of a column
typed ``vector`` on a server where the extension has since been dropped out from
under it. ``DROP EXTENSION IF EXISTS vector`` always runs at the end -- safe (and a
no-op) whether or not the extension was ever created.

**Graph.** Creates ``graph_edges``, the plain-SQL replacement for the Apache AGE
``eo_graph`` graph (see ``agent/eoa/memory/graph.py``, rewritten in the same change
to run on this table instead of Cypher). Entities themselves are no longer separate
graph vertices -- the existing ``entities`` table rows *are* the vertices now, keyed
by the same ``id``. This migration does **not** touch Apache AGE or its
``eo_graph`` data at all (no ``DROP EXTENSION age``, no touching ``ag_catalog``):
the docker-era AGE graph is being retired, not migrated in-place, and its edges are
carried over separately via ``scripts/export_age_edges.py`` (a one-off, run by hand
against the live docker DB after this migration has been applied there).
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

EMBED_DIM_FOR_DOWNGRADE = 1024


def _items_embedding_udt_name(bind) -> str | None:
    row = bind.execute(
        text(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_name = 'items' AND column_name = 'embedding'"
        )
    ).first()
    return row[0] if row else None


def _pg_type_exists(bind, typname: str) -> bool:
    return bind.execute(text("SELECT 1 FROM pg_type WHERE typname = :t"), {"t": typname}).first() is not None


def upgrade() -> None:
    bind = op.get_bind()

    # -- vectors: migrate off pgvector only if this DB actually has it ------
    embedding_udt = _items_embedding_udt_name(bind)
    if embedding_udt == "vector":
        op.execute("ALTER TABLE items ADD COLUMN embedding_arr REAL[]")
        if _pg_type_exists(bind, "vector"):
            op.execute(
                "UPDATE items SET embedding_arr = embedding::real[] WHERE embedding IS NOT NULL"
            )
        else:
            # Column is typed `vector` but the type/extension is gone -- nothing to
            # cast from; leave embedding_arr NULL rather than fail the migration.
            pass
        op.execute("DROP INDEX IF EXISTS ix_items_embedding_hnsw")
        op.execute("ALTER TABLE items DROP COLUMN embedding")
        op.execute("ALTER TABLE items RENAME COLUMN embedding_arr TO embedding")
    # else: already REAL[] (fresh install off the edited 0001) or the column is
    # missing entirely (shouldn't happen) -- nothing to migrate.

    # Safe whether or not the extension was ever created on this server.
    op.execute("DROP EXTENSION IF EXISTS vector")

    # -- graph: plain-SQL replacement for Apache AGE's eo_graph --------------
    # AGE itself is left untouched (see module docstring) -- this table is purely
    # additive and does not depend on the `age` extension existing.
    op.execute(
        """
        CREATE TABLE graph_edges (
            id             BIGSERIAL PRIMARY KEY,
            src_entity_id  BIGINT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            dst_entity_id  BIGINT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            label          TEXT NOT NULL,
            item_id        BIGINT NULL REFERENCES items(id) ON DELETE SET NULL,
            props          JSONB NOT NULL DEFAULT '{}',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (src_entity_id, dst_entity_id, label, item_id)
        )
        """
    )
    op.execute("CREATE INDEX ix_graph_edges_src ON graph_edges (src_entity_id)")
    op.execute("CREATE INDEX ix_graph_edges_dst ON graph_edges (dst_entity_id)")
    op.execute("CREATE INDEX ix_graph_edges_label ON graph_edges (label)")
    op.execute(
        "CREATE TRIGGER trg_graph_edges_updated_at BEFORE UPDATE ON graph_edges "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS graph_edges CASCADE")

    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    embedding_udt = _items_embedding_udt_name(bind)
    if embedding_udt != "vector":
        op.execute(f"ALTER TABLE items ADD COLUMN embedding_vec VECTOR({EMBED_DIM_FOR_DOWNGRADE})")
        if _pg_type_exists(bind, "vector"):
            op.execute(
                "UPDATE items SET embedding_vec = embedding::vector "
                "WHERE embedding IS NOT NULL"
            )
        op.execute("ALTER TABLE items DROP COLUMN embedding")
        op.execute("ALTER TABLE items RENAME COLUMN embedding_vec TO embedding")
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_items_embedding_hnsw "
            "ON items USING hnsw (embedding vector_cosine_ops)"
        )
