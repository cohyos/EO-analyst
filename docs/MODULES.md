# EO-Analyst — Modules

Per-module reference, one section per unit of the system. See
`docs/CONVENTIONS.md` for the overall layout and hard rules.

## Docker infrastructure

Files: `docker-compose.yml` (repo root), `docker/postgres-age/`,
`docker/agent/`, `docker/fetcher/`, `docker/web/`, `config/searxng/`,
`scripts/verify_isolation.ps1` / `.sh`.

### Services

| Service    | Image / build                     | Networks              | Published ports (host) | Purpose |
|------------|------------------------------------|------------------------|-------------------------|---------|
| `postgres` | `docker/postgres-age` (custom)     | `internal` (172.28.0.10) | `127.0.0.1:5433->5432` | PostgreSQL 17 + Apache AGE (graph) + pgvector (embeddings) |
| `searxng`  | `searxng/searxng:latest`           | `egress`, `internal` (172.28.0.11) | none | Meta-search backend for `eoa.search` |
| `ntfy`     | `binwiederhier/ntfy`               | `internal` (172.28.0.12) | `127.0.0.1:8090->80` | Push notifications (`eoa.notify`); also reachable over Tailscale later |
| `fetcher`  | `docker/fetcher` (custom)          | `egress`, `internal`   | none | RSS/HTML fetching + sanitization (`eoa.fetch.service`) — the only compute service allowed general internet access besides `searxng` |
| `agent`    | `docker/agent` (custom)            | `internal`, `hostlink` | none | Orchestrator (`eoa.orchestrator.main`) — scheduler, pipeline, LLM calls, DB/graph writes |
| `web`      | `docker/web` (custom)              | `internal`             | `127.0.0.1:8765->8765` | FastAPI app + optional built React UI (`eoa.api.app:app`) |
| `ollama`   | `ollama/ollama` (profile `container-ollama`, off by default) | `internal` | none | Optional containerized GPU Ollama, alternative to the host install |

Every service sets `TZ=Asia/Jerusalem` and `restart: unless-stopped` (except
the opt-in `ollama` profile service, which is also `unless-stopped` once
started). Postgres has a `pg_isready` healthcheck; `agent`, `fetcher`, and
`web` wait on it via `depends_on: postgres: condition: service_healthy`.

### Networks

- **`egress`** — plain bridge, has internet access. Only `searxng` and
  `fetcher` are attached, matching `docs/CONVENTIONS.md` rule 13
  ("`fetcher` and `searxng` are on `egress`; everything else is on
  `internal`").
- **`internal`** — `internal: true` bridge, subnet `172.28.0.0/24`,
  **not routable to the internet at all**. `postgres`, `searxng`, and `ntfy`
  are given static IPs on it (`.10`, `.11`, `.12`) so `agent` can reach them
  without relying on DNS (see below). Every service except the opt-in
  `ollama` profile sits on this network.
- **`hostlink`** — plain bridge, attached only to `agent`, no published
  ports, no other service attached. Exists solely to restore the
  `host.docker.internal` routing path that `internal: true` blocks (see
  next section).

### Reaching host Ollama from `agent`

`agent` calls Ollama running natively on the Windows host (not
containerized, unless the `container-ollama` profile is used) at
`OLLAMA_URL=http://host.docker.internal:11434`. On Docker Desktop, an
`internal: true` network disables *all* routing out of the bridge —
including the special `host.docker.internal` gateway path — so `agent`
cannot be `internal`-only and still reach the host.

The fix: `agent` is attached to a second network, `hostlink` (a plain,
non-internal bridge with nothing else on it), and gets
`extra_hosts: ["host.docker.internal:host-gateway"]`, which resolves that
name to the Docker Desktop host without any DNS lookup, routed over
`hostlink`.

### What `agent`'s isolation actually guarantees (read before assuming more)

The task this network topology was built to satisfy is: *`agent` must reach
host Ollama, but must not reach the public internet.* Docker Desktop's
bridge networking cannot express "route to the gateway only, block
everything else" — any non-`internal` network capable of reaching
`host.docker.internal` is, by construction, NAT'd to the real internet too.
There is no `docker-compose.yml`-level primitive to split those two routes
apart, and Windows has no native iptables to reach into the Docker Desktop
VM and add that rule by hand.

So the isolation actually implemented is **DNS-layer, not network-layer**:

- `agent` sets `dns: [0.0.0.0]`, an address that can never answer a query —
  so *any* hostname lookup fails immediately, including arbitrary internet
  domains.
- `extra_hosts` gives `agent` static, DNS-free entries for exactly the hosts
  it is allowed to reach: `postgres`, `searxng`, `ntfy` (their static
  `internal` IPs above) and `host.docker.internal` (via `hostlink`).

**Guaranteed:** `agent` cannot resolve or reach any hostname other than the
four in its `extra_hosts` list — this covers the realistic egress path for
application code (`httpx.get("https://...")`, an LLM SDK calling out, a
compromised dependency phoning home by domain name).

**Not guaranteed:** a process that connects to a hardcoded IP literal
(bypassing DNS entirely) can still reach the internet through the
`hostlink` bridge's NAT path. Closing that gap needs a host-level firewall
rule (Windows Defender Firewall, or an iptables rule inside the Docker
Desktop WSL2 VM) scoped to the `hostlink` bridge's subnet — deliberately out
of scope for `docker-compose.yml`, which cannot express it declaratively.
This is on top of, not instead of, the existing code-level control in
`docs/CONVENTIONS.md` rule 1 (all Ollama access funneled through
`eoa.llm.ollama_client`).

Run `scripts/verify_isolation.ps1` (or `.sh` under WSL2/Linux) after
`docker compose up` to check both properties: `https://example.com` must
fail (DNS-layer block), `http://host.docker.internal:11434/api/tags` must
succeed (host Ollama reachable). The scripts print which guarantee each
check actually exercises.

### PostgreSQL image (`docker/postgres-age/`)

Built from `apache/age:release_PG17_1.7.0` — the official Apache AGE image,
confirmed on Docker Hub to be a PostgreSQL-17 build of AGE (release lines
`release_PG17_1.6.0` and `release_PG17_1.7.0` both exist; `1.7.0` is used as
the more recent of the two). That base image already builds AGE from
source `FROM postgres:17` and runs `CREATE EXTENSION age;` on first init.

pgvector is not part of that image, so `docker/postgres-age/Dockerfile` adds
a throwaway build stage (`postgres:17` + `postgresql-server-dev-17`) that
compiles pgvector `v0.8.6` from source, and copies `vector.so` plus its
SQL/control files into the final image.

`docker/postgres-age/init/01-extensions.sql` runs after the base image's own
init script and is idempotent: `CREATE EXTENSION IF NOT EXISTS age/vector`,
then a guarded `create_graph('eo_graph')` that only runs if
`ag_catalog.ag_graph` doesn't already have a row named `eo_graph` (AGE's
`create_graph()` is not itself idempotent — it raises if the graph exists).

`shared_preload_libraries=age` is set via `CMD ["postgres", "-c",
"shared_preload_libraries=age"]` (restated explicitly in our Dockerfile,
inherited from the base image) since it can only be set at server start, not
via `ALTER SYSTEM` at runtime.

### App images (`docker/agent/`, `docker/fetcher/`, `docker/web/`)

All three are `python:3.12-slim`, install with
`pip install uv && uv pip install --system -e .` from `pyproject.toml`, and
**must be built with the repo root as context** (not their own
`docker/<name>/` directory), because the package build needs
`pyproject.toml`, `agent/`, and `README.md` from the repo root:

```
docker compose build agent   # -> context: ., dockerfile: docker/agent/Dockerfile
```

- `fetcher` additionally installs Playwright + Chromium
  (`playwright install --with-deps chromium`) in its own final Docker stage
  (`runtime`, built `FROM deps`), so that layer never affects the `agent`
  image (a wholly separate Dockerfile/image). Playwright is not currently in
  `pyproject.toml`'s dependencies (`eoa.fetch` today uses
  feedparser/trafilatura/httpx per `docs/CONVENTIONS.md`); it's installed at
  the image layer ahead of any code depending on it — add it to
  `pyproject.toml` once `eoa.fetch` actually imports it.
- `web` optionally builds the React 19 + Vite UI in a `node:24-alpine` stage
  before the Python stage, gated by both file presence (`web/package.json`)
  and a `BUILD_FRONTEND` build ARG (`auto` default — skip gracefully if
  `web/package.json` doesn't exist yet, as in the current repo state;
  `force` — fail loudly if it's missing; `skip` — never build). The built
  `dist/` (or an empty dir, if skipped) is copied into the final image at
  `web/dist`.

### SearXNG config (`config/searxng/`)

`settings.yml`: `use_default_settings.engines.keep_only` whitelists
`duckduckgo, bing, brave, startpage, wikipedia, arxiv, google scholar,
github` (every other default engine is disabled); `search.formats: [html,
json]`; `search.safe_search: 0`; `search.default_lang: "en"`;
`server.limiter: false`. `server.secret_key` holds a placeholder string only
— SearXNG's settings loader overwrites it at process start from the
`SEARXNG_SECRET` environment variable (set in `.env`, wired through
`docker-compose.yml`), so the real secret never lives in this committed
file.

`limiter.toml` is a minimal bot-detection config, inert while
`server.limiter: false`, but correct (and includes the `internal` network's
subnet in its trust/pass lists) so the limiter can be turned on later
without authoring this file from scratch.

### Validating the compose file

```
docker compose config
```

parses cleanly given a `.env` populated from `.env.example` (in particular
`POSTGRES_PASSWORD` and `SEARXNG_SECRET`, which are marked `:?required` and
intentionally fail `docker compose config`/`up` if left unset — no secrets
ship with a working default). This repo's `docker compose build` / `up` were
intentionally **not** run as part of authoring this infra — validate those
locally with Docker Desktop running.

## Memory layer

Files: `alembic.ini` (repo root), `db/migrations/` (Alembic env + versions),
`db/graph_init.sql`, `db/seed/seed_watchlist.py`, `agent/eoa/memory/`
(`relational.py`, `graph.py`, `vector.py`).

### Schema (`db/migrations/versions/0001_core.py`)

Single hand-written migration (no ORM models — raw DDL via `op.execute`)
creating all 19 tables from `docs/CONVENTIONS.md`'s "Database" section:
`sources, items, entities, events, contracts, conferences, reports, jobs,
run_log, resource_log, model_registry, security_log, triage_feedback,
source_reliability, search_playbook, lessons, investigation_log,
clarifications, feedback_surveys`. Every mutable table gets `created_at` /
`updated_at` (the latter kept current by a shared `set_updated_at()` trigger
function) even where the task spec didn't spell them out explicitly, per
"All timestamps `timestamptz`, defaults `now()`" — append-only log tables
(`run_log`, `resource_log`, `security_log`, etc.) only get `created_at`.

Only the `vector` (pgvector) extension is created by this migration — **not**
`age`. That's deliberate: `tests/integration/test_schema.py` needs the core
migration to succeed even against a plain `pgvector/pgvector:pg17` image with
no AGE installed (graph tests are skipped there, not the whole suite). AGE
setup is entirely `db/graph_init.sql`'s job, applied as a separate step after
migrations.

`items.embedding` is `vector(EMBED_DIM)`, `EMBED_DIM` read from the `EMBED_DIM`
env var at migration-apply time (default `1024`), with an HNSW index
(`vector_cosine_ops`). Other indexes: `items(level, published_at)`,
`items(text_hash)`, `items(source_id)`, `jobs(state, priority, not_before)`,
plus one per FK column and per CHECK-constrained enum-like column.

**Uniqueness assumptions not spelled out in the task spec, added so the
memory-layer upsert helpers have a conflict target:** `sources.name` UNIQUE
(a configured source is identified by name, not URL, which can be null for
`kind='search'` sources) and `conferences.name` UNIQUE (needed for
`db/seed/seed_watchlist.py`'s idempotent re-seeding). `entities.name` UNIQUE
and `items.url` UNIQUE were explicit in the spec.

`db/migrations/env.py` reads `DATABASE_URL` (default
`postgresql://eoa:change-me-local-only@127.0.0.1:5433/eoanalyst`, matching
`eoa.config.settings().database_url`) and rewrites a bare `postgresql://`
scheme to `postgresql+psycopg://` for SQLAlchemy/Alembic, since the project
standardizes on psycopg3.

### Graph init (`db/graph_init.sql`)

Idempotent, run separately from Alembic (`psql "$DATABASE_URL" -f
db/graph_init.sql`) after `0001_core.py` has created the `entities` table it
attaches to. Creates the `age` extension, the `eo_graph` graph, the `Entity`
vlabel, and the seven edge labels (`COMPETITOR_OF, SUPPLIER_OF, PARTNER_OF,
ACQUIRED, INTEGRATES_WITH, BIDS_AGAINST, DERIVED_FROM`) via a reusable
`eo_graph_ensure()` PL/pgSQL function (each label creation is wrapped in its
own `BEGIN...EXCEPTION WHEN duplicate_object OR others THEN NULL END` block
for idempotency, since AGE's own catalog schema for labels isn't stable
enough across versions to query directly). Also installs an `AFTER INSERT OR
UPDATE OF name, kind, country` trigger on `entities`
(`eo_entities_sync_vertex()`) that `MERGE`s the corresponding `Entity {
entity_id, name, kind, country }` vertex via `cypher()` built with
`format()` — `%L` for the string properties (proper escaping/quoting) and
bare `%s` only for the trusted, Postgres-generated `NEW.id` integer.

This is a second, container-init-adjacent piece of AGE setup alongside
`docker/postgres-age/init/01-extensions.sql` (which the docker/infra agent
wrote to run `CREATE EXTENSION`/`create_graph` at first container boot,
before any application tables exist). `db/graph_init.sql` is the
schema-aware half — it needs `entities` to exist, so it cannot live in that
container init script and is instead a deploy-time step run after
migrations.

### `agent/eoa/memory/relational.py`

Typed psycopg3 helpers (dict rows) over the plain relational tables:
`upsert_source`, `insert_item` (`ON CONFLICT (url) DO UPDATE` refreshing only
`fetched_at`), `get_items_for_stage` / `mark_stage` (pipeline-stage tracking
via `items.processed_stages text[]`, restricted to `security_status='clean'`
items), `update_item_fields` (a `psycopg.sql.Identifier`-based dynamic
`UPDATE` restricted to an explicit column allow-list, so field names can
never come from untrusted input), `insert_event`, `upsert_entity`,
`record_resource_decision`, `log_security`, `enqueue_job`, `claim_next_job`
(`SELECT ... FOR UPDATE SKIP LOCKED` inside a `WITH` CTE, priority-then-age
ordered), `finish_job`, `heartbeat`, `insert_investigation_log`,
`get_lessons`, `add_lesson`, `recent_feedback`.

No function calls `conn.commit()`/`rollback()` directly — every one assumes
`eoa.db.connection()` follows standard psycopg3/psycopg_pool semantics
(commit on clean exit of the `with` block, rollback on exception). If the
concurrently-developed `eoa.db` module does *not* do this, every write helper
here needs an explicit commit added.

### `agent/eoa/memory/graph.py`

The **only** module in the codebase that issues Cypher. Wraps every AGE call
through `_run_cypher(cypher_body, params, out_columns)`, which always runs
`LOAD 'age'` + `SET search_path` first, then the actual
`SELECT * FROM cypher('eo_graph', $$ ... $$, %s::agtype) AS (...)` — values
are passed through AGE's native `params` argument (a JSON blob bound as a
normal, injection-safe SQL parameter, referenced in Cypher as `$name`), never
string-interpolated into the dollar-quoted Cypher body. Edge/vertex *labels*
can't be parameterized by Cypher itself, so `add_edge`/`neighbors` check the
label against `EDGE_LABELS` before embedding it in the query text.

Public API: `ensure_graph()`, `merge_entity(entity_id, name, kind, country)`,
`add_edge(src_entity_id, dst_entity_id, label, item_id, props=None)`,
`neighbors(entity_id, label=None, depth=1)`, `partners_of_competitors`,
`suppliers_of_program_bidders`, `startups_linked_to_majors`,
`entity_timeline` (this last one is a plain relational join, not Cypher — see
docstring; it's grouped here because it's graph-adjacent domain logic, not
because it needs AGE).

`_parse_agtype(raw)` strips a trailing `::vertex` / `::edge` / `::path` type
suffix and `json.loads`es the remainder, tagging dict results with an
`_agtype` key naming the stripped kind. Covered by
`tests/unit/test_agtype_parse.py`, which passes standalone today.

**Modeling assumptions** (the task spec named these functions but the schema
has no explicit "startup"/"major" or program-bidder relationship):
`suppliers_of_program_bidders` assumes programs are `Entity` vertices
(`kind='program'`) and that bidding companies carry a `BIDS_AGAINST` edge
directly to the program vertex. `startups_linked_to_majors` has no
"startup"/"major" flag to key off, so it's implemented as a connectivity
heuristic — Entities with at least `min_links` distinct neighbors, ordered by
link count descending, with `link_count` attached to each result.

### `agent/eoa/memory/vector.py`

`upsert_embedding(item_id, vec)`, `nearest(vec, limit, days)` (returns
`(item_id, cosine_similarity)` pairs via `1 - (embedding <=> vec)`, optional
`days` lookback window over `published_at`/`fetched_at`/`created_at`),
`find_duplicate(vec, threshold, days)` (thin wrapper over `nearest(limit=1)`
compared against `threshold`, matching `config.dedup.cosine_threshold` /
`config.dedup.lookback_days`). Vectors are sent as pgvector text literals
(`[v1,v2,...]`) cast with `::vector` in SQL, so no pgvector Python-side type
adapter registration is required on the connection.

### `db/seed/seed_watchlist.py`

Reads `config/watchlist.yaml`, upserts every `companies` entry as an entity
(`kind='company'`) and every `programs` entry as an entity (`kind='program'`)
via `eoa.memory.relational.upsert_entity`, and upserts every
`conferences_seed` entry into `conferences` with `status='estimated'` (an
`ON CONFLICT` that preserves an existing `status='confirmed'` row instead of
downgrading it) and a `start_date` computed by `_next_occurrence(month,
cadence, today)`. That helper handles `biennial_odd`/`biennial_even`
precisely (rolls the candidate year forward to the next one with matching
parity); a plain `biennial` cadence (as used by `ISDEF` in
`config/watchlist.yaml`, which specifies no parity) has no anchor year to
derive odd/even alignment from, so it falls back to "next annual slot" —
documented in the function's docstring as a known limitation, not a
guarantee that year is correct.

### Tests

`tests/unit/test_agtype_parse.py` — pure unit tests for `_parse_agtype`;
stubs `eoa.db` into `sys.modules` first if the real module isn't importable
yet, so this file doesn't depend on the concurrently-developed
config/db/errors layer. Passes today via
`PYTHONPATH=agent python -m pytest tests/unit -q`.

`tests/integration/test_schema.py` (`@pytest.mark.integration`, needs
Docker) — builds `docker/postgres-age` if that Dockerfile exists, else falls
back to `pgvector/pgvector:pg17` and skips the AGE-dependent test with an
explicit reason; runs `alembic upgrade head` against the container; checks
all core tables exist, an item with an embedding is found by `nearest()`, an
`entities` insert produces a matching graph vertex (AGE path only), and the
`claim_next_job`/`finish_job` flow round-trips. Not run as part of this
change (no Docker available in this environment) — verify locally.
