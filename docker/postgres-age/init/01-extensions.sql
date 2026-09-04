-- docker/postgres-age/init/01-extensions.sql
--
-- Idempotent bootstrap for Apache AGE (graph) + pgvector (embeddings) in the
-- eo-analyst database. Runs once, automatically, on first container start
-- against an empty data directory (standard postgres image behaviour for
-- /docker-entrypoint-initdb.d/*.sql, executed in alphabetical order — this
-- file runs after the base apache/age image's own
-- 00-create-extension-age.sql). Written to also be safe to re-run manually
-- (e.g. `docker compose exec postgres psql -U eoa -d eoanalyst -f ...`)
-- against an already-initialized database.

-- Extensions -----------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS age;
CREATE EXTENSION IF NOT EXISTS vector;

-- AGE requires its catalog on the search_path and an explicit LOAD in every
-- session that uses Cypher (per Apache AGE docs); this session-level setup
-- only affects this init script's own connection, application code must do
-- the same via eoa.memory.graph.
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- Graph bootstrap --------------------------------------------------------
-- create_graph() is NOT idempotent on its own (raises if the graph already
-- exists), so guard it with an existence check against ag_catalog.ag_graph.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'eo_graph'
    ) THEN
        PERFORM ag_catalog.create_graph('eo_graph');
    END IF;
END
$$;
