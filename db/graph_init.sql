-- db/graph_init.sql
--
-- Idempotent Apache AGE initialization for the EO-Analyst knowledge graph.
-- Safe to run multiple times, and safe to run before or after any given
-- backfill of the `entities` table. Requires the `entities` table to already
-- exist (apply db/migrations/0001_core.py first) and the `age` extension to
-- be available in the Postgres image (see docker/postgres-age).
--
-- Usage:  psql "$DATABASE_URL" -f db/graph_init.sql
--
-- Graph: eo_graph
-- Vertex label:  Entity {entity_id, name, kind, country}
-- Edge labels:   COMPETITOR_OF, SUPPLIER_OF, PARTNER_OF, ACQUIRED,
--                INTEGRATES_WITH, BIDS_AGAINST, DERIVED_FROM
--                (every edge additionally carries an `item_id` property,
--                 set by callers in agent/eoa/memory/graph.py)

CREATE EXTENSION IF NOT EXISTS age;

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- eo_graph_ensure(): idempotently makes sure the graph, the Entity vertex
-- label, and all seven edge labels exist. AGE also creates labels lazily on
-- first Cypher use, but pre-creating them here keeps first-write latency
-- predictable and lets tooling introspect the schema before any data exists.
CREATE OR REPLACE FUNCTION eo_graph_ensure() RETURNS void AS $$
DECLARE
    lbl text;
BEGIN
    LOAD 'age';
    PERFORM set_config('search_path', 'ag_catalog, "$user", public', false);

    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'eo_graph') THEN
        PERFORM ag_catalog.create_graph('eo_graph');
    END IF;

    BEGIN
        PERFORM ag_catalog.create_vlabel('eo_graph', 'Entity');
    EXCEPTION WHEN duplicate_object OR others THEN
        NULL; -- label already exists
    END;

    FOREACH lbl IN ARRAY ARRAY[
        'COMPETITOR_OF', 'SUPPLIER_OF', 'PARTNER_OF', 'ACQUIRED',
        'INTEGRATES_WITH', 'BIDS_AGAINST', 'DERIVED_FROM'
    ]
    LOOP
        BEGIN
            PERFORM ag_catalog.create_elabel('eo_graph', lbl);
        EXCEPTION WHEN duplicate_object OR others THEN
            NULL; -- label already exists
        END;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

SELECT eo_graph_ensure();

-- eo_entities_sync_vertex(): keeps the graph's Entity vertices in lock-step
-- with the relational `entities` table. Fired after every INSERT (and after
-- an UPDATE that could change name/kind/country) on `entities`.
--
-- The Cypher body is built with format(): %L safely quotes/escapes the
-- string properties (name/kind/country), and %s substitutes the *trusted*
-- integer NEW.id (a Postgres-generated bigint, never user-controlled text)
-- without adding quotes, so entity_id is stored as a Cypher number.
CREATE OR REPLACE FUNCTION eo_entities_sync_vertex() RETURNS trigger AS $fn$
DECLARE
    q text;
BEGIN
    LOAD 'age';
    PERFORM set_config('search_path', 'ag_catalog, "$user", public', false);

    q := format(
        $CY$SELECT * FROM cypher('eo_graph', $$
            MERGE (e:Entity {entity_id: %s})
            SET e.name = %L, e.kind = %L, e.country = %L
        $$) AS (v agtype)$CY$,
        NEW.id, NEW.name, COALESCE(NEW.kind, ''), COALESCE(NEW.country, '')
    );
    EXECUTE q;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_entities_sync_vertex ON entities;
CREATE TRIGGER trg_entities_sync_vertex
    AFTER INSERT OR UPDATE OF name, kind, country ON entities
    FOR EACH ROW EXECUTE FUNCTION eo_entities_sync_vertex();
