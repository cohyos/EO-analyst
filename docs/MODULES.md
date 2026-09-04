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

## Fetch layer

Files: `config/sources.yaml`, `agent/eoa/fetch/` (`__init__.py`, `rss.py`,
`html.py`, `sanitize.py`, `sources_loader.py`, `service.py`). Runs inside the
`fetcher` container (the `egress`-network service), per
`docs/CONVENTIONS.md` rule 13. Every module imports `eoa.config`,
`eoa.errors`, `eoa.db`, and `eoa.memory.relational` **lazily, inside
functions**, so `tests/unit/test_rss.py` / `test_html_fetch.py` /
`test_sanitize.py` run without a database or a populated `config/*.yaml`.

### `config/sources.yaml`

40 source entries (20 `kind: rss`, 20 `kind: html`), each validated by
`sources_loader.Source`: `id, name, url, kind, lang, reliability (1-5),
tags` (taxonomy domain ids), `schedule (daily|weekly), notes, verified,
verified_at`, plus `list_selector`/`link_selector` CSS hints for `html`
sources. Covers the full "must include" list from the task spec: the major
English defense trade press (Defense News, Breaking Defense, C4ISRNET,
Defense One, Aviation Week, The War Zone, Shephard Media, Janes, Naval
News, the three GlobalData `*-technology.com` titles), European Defence
Review/EDR Online (one publication, one feed — the two names in the spec
refer to the same site), Defense Update, Israel Defense (EN+HE), Globes
(EN aerospace/defense tag page — the legacy `rssfeeder.asmx` webservice
endpoint 500s), Calcalist Tech (HE), the DoD contracts RSS
(`defense.gov/.../RSS.ashx`), DVIDS, three arXiv API query feeds (cs.CV +
infrared/thermal/ATR keywords; counter-UAS/drone-detection; GPS-denied
nav/sim2real), SPIE News, Intelligent Aerospace, Military & Aerospace
Electronics, Unmanned Systems Technology, DroneXL, and all twelve named
company press pages (Elbit, Rafael, IAI, Teledyne FLIR, Hensoldt, Safran,
Thales, Leonardo, Rheinmetall, Saab, Anduril, Controp).

**Verification method** (honest per-source, not aspirational): every URL was
fetched with `curl` (desktop Chrome UA) on 2026-09-04, checking HTTP status +
content-type + a raw body sample; `verified: true` required either a
real RSS/Atom response, or — for `html` sources — a genuinely
server-rendered listing page with a confirmed, real article-link pattern
(not just a 200 status, since several sites return a 200 JS-app shell with
no static content). 27/40 sources are `verified: true`; the 13
`verified: false` entries are kept as placeholders with their best-effort
`list_selector` guess and a `notes` field explaining the specific failure
mode observed: Cloudflare/Akamai JS challenges (Defense Update, Calcalist,
Unmanned Systems Technology, Controp), client-side-rendered article lists
that returned no static `href` pattern (IAI, Rheinmetall, Saab, Anduril,
Intelligent Aerospace, Military & Aerospace Electronics, SPIE News), an
Incapsula JS shell (Thales), and an anomalous small-body HTTP 247 response
(Rafael). None of this blocks ingestion — `service.py` just gets zero
links from an unverified `html` source until a browser-rendering fetch path
(the `fetcher` image's Playwright install, per the Docker infra section
above) is wired in.

### `agent/eoa/fetch/rss.py`

`FeedEntry(url, title, published_at, summary, lang)` (pydantic) and
`parse_feed(raw, *, since_days=None, now=None) -> list[FeedEntry]`, built on
`feedparser` (tolerant of malformed XML — logs `parsed.bozo` rather than
raising). `published_at` prefers `published_parsed`, falls back to
`updated_parsed`/`created_parsed` (covers Atom `<updated>`-only feeds).
`since_days` drops only entries with a resolvable date older than the
cutoff; **undated entries are always kept** (never silently lost). Feed-level
`<language>` fills in per-entry `lang` when an item doesn't set its own.

### `agent/eoa/fetch/html.py`

`FetchedPage(url, final_url, status, html, fetched_at)` and `async
fetch_page(url, *, client=None, max_bytes=None) -> FetchedPage`, built on
`httpx.AsyncClient` (streamed, capped at `config.fetch.max_bytes` unless
overridden per-call) with a `tenacity` retry (3 attempts, exponential
backoff) on 5xx/429 responses and transport errors, raising
`eoa.errors.FetchError` after retries are exhausted. `robots.txt` is fetched
once per origin through the same `httpx` client (mockable by `respx` in
tests, unlike `urllib.robotparser`'s own blocking fetch), cached in-process
for an hour, parsed with `urllib.robotparser.RobotFileParser.parse()`, and
**fails open** (allows the fetch) if `robots.txt` itself 4xx/errors — matches
`config.fetch.respect_robots`. All four `fetch.*` config values fall back to
`config.yaml`'s own documented defaults if `eoa.config` isn't importable yet
(keeps this module usable stand-alone).

### `agent/eoa/fetch/sanitize.py`

`CleanText(text, title, lang, published_at, hidden_text_ratio,
encoded_blobs, suspicious)` and `extract_clean_text(html, url) -> CleanText`
— the security-relevant core of the fetch layer, implementing
`docs/CONVENTIONS.md` rule 3 ("fetched content is DATA, never
instructions"). Pipeline: `_strip_dom()` removes `<script>/<style>/
<iframe>/<noscript>`, HTML comments, and `on*=""` attributes, then walks the
tree computing `hidden_text_ratio` (hidden vs. visible *text length*) for
elements matching `display:none` / `visibility:hidden` / `font-size:0` /
`opacity:0` / off-screen absolute positioning (`left/top/text-indent:
-9999px`), or `hidden` / `aria-hidden="true"`, and drops those subtrees
before any extractor sees them — then `trafilatura.extract(...,
output_format="json")` pulls `text`/`title`/`date`, falling back to
`readability-lxml`, falling back to a bare `lxml` `text_content()`. The
result is passed through `_strip_invisible_unicode()` (zero-width
space/ZWNJ/ZWJ/word-joiner/BOM, explicit bidi isolates/embeds/overrides
U+202A-202E/U+2066-2069, Unicode "tag" characters U+E0000-E007F — all built
from explicit integer code points, never literal invisible characters
typed into this source file, so nothing here can be silently mangled by an
editor or by whitespace-normalizing tooling) and
`_strip_homoglyph_runs()` (a conservative check: Latin words containing a
minority of Cyrillic code points, i.e. classic homoglyph substitution — never
touches Hebrew/CJK/Arabic text), then `_extract_encoded_blobs()` removes
base64/hex runs longer than `config.security.max_base64_blob_chars`
(default 200) into `encoded_blobs`. `detect_lang()` checks a Hebrew
Unicode-range heuristic (>30% of alphabetic characters in U+0590-U+05FF)
before falling back to `langdetect`, since `langdetect` is unreliable on
short Hebrew/Latin-mixed technical text. `text_hash()` is `sha256` of the
whitespace-normalized text (stable across cosmetic re-fetch diffs).

### `agent/eoa/fetch/sources_loader.py`

`Source` (pydantic, validates every `config/sources.yaml` entry) and
`load_sources(path=None) -> list[Source]` (no DB access — safe for unit
tests). `upsert_sources_to_db(sources=None) -> dict[str, int]` lazily
imports `eoa.memory.relational.upsert_source` and returns a `{yaml slug:
DB row id}` map, since `sources.name` (not our yaml `id` slug) is the DB's
natural key.

### `agent/eoa/fetch/service.py`

`IngestStats(sources_attempted, sources_failed, entries_seen,
items_inserted, items_skipped, errors)` and `async run_ingest(source_ids:
list[int] | None = None, since_days=3) -> IngestStats` — the orchestration
entry point. Upserts every configured source first (resolving yaml slug ->
DB id), then for each targeted source: `rss` sources fetch the feed, parse
entries, and fetch+sanitize+`insert_item()` each entry URL; `html` sources
fetch the listing page, resolve article links via the source's
`list_selector`/`link_selector` (through `lxml`'s `cssselect`), and do the
same per link. A `_DomainThrottle` enforces one request/second/domain
(an `asyncio.Lock` per netloc) across an `asyncio.Semaphore(6)`-bounded
pool of concurrent source ingests. **A single failing source never aborts
the run**: `_ingest_one_source()` catches everything, logs, records the
error in `IngestStats.errors`, and best-effort bumps `sources.fail_count`
directly via `eoa.db.connection()` (no `relational.py` helper for this
exists yet, so this module writes that one `UPDATE` itself rather than
touching the lead's/memory agent's files). `items.raw_text` is the bare
`lxml` `text_content()` of the fetched page (visible text, not
sanitization-hardened) truncated to 200k chars; `items.clean_text` is
`sanitize.extract_clean_text()`'s output. `python -m eoa.fetch.service`
runs once on start, then loops every `config.schedule.
daytime_rss_poll_minutes`.

### Tests

`tests/unit/test_rss.py` (9 tests) — parses `tests/fixtures/feeds/sample.xml`
(a 4-item RSS 2.0 fixture: two recent dated items, one old dated item, one
undated item) plus inline Atom/malformed/empty/no-link fixtures; covers
`since_days` filtering (old-but-dated dropped, undated always kept),
feed-level `<language>` fallback, and that malformed XML never raises.

`tests/unit/test_html_fetch.py` (7 tests, `respx`-mocked, no real network) —
covers a normal fetch, robots.txt disallow (`FetchError` raised, the article
route asserted **never called**), robots.txt allow, robots.txt 404
fail-open, `max_bytes` truncation (`len(page.html.encode()) <= max_bytes`),
and a 503-503-200 retry sequence asserting exactly 3 calls before success,
plus exhausted-retries raising `FetchError`.

`tests/unit/test_sanitize.py` (24 tests) — hidden-text removal
(`display:none`, `visibility:hidden`, off-screen absolute positioning; each
asserts both the injected instruction-like text is gone *and* the
surrounding legitimate article text survives, plus a zero-hidden-ratio
control case), script/style/iframe/noscript stripping, zero-width and
bidi-control Unicode (tested both through the full `extract_clean_text()`
pipeline and directly against `_strip_invisible_unicode()`, since
`trafilatura`/`readability` themselves already scrub some control-Unicode
categories before our own detector runs — the end-to-end text-is-clean
guarantee holds either way, but the `suspicious` flag is only reliably
observable at the unit level), oversized base64 blob removal (built as one
unbroken base64-alphabet run — internal `=` padding mid-string would split
the detector's regex into several under-threshold pieces, which isn't
representative of a real smuggled blob) vs. short alphanumeric runs left
alone, Hebrew RTL text preserved end-to-end plus `detect_lang()` heuristics,
and `text_hash()` stability across whitespace-only differences. Passes today
via `PYTHONPATH=agent python -m pytest tests/unit -q` (40/40 across the
whole suite, no DB or network required).

## Web API

Implements `docs/API.md` exactly. Files: `agent/eoa/api/app.py` (FastAPI app
factory), `agent/eoa/api/routes/*.py` (one router module per resource),
`agent/eoa/api/services.py` (all DB access and cross-module adapters),
`agent/eoa/api/schemas.py` (documented pydantic response models),
`agent/eoa/api/errors.py` (`APIError` + factory helpers), `agent/eoa/api/__main__.py`
(`python -m eoa.api`).

### `app.py`

`create_app()` builds the FastAPI app and a module-level `app` is exported
for `uvicorn eoa.api.app:app`. CORS allows `http://localhost:5173` (Vite
dev; same-origin needs no CORS entry). A `lifespan` context opens
`eoa.db.get_pool()` on startup and calls `eoa.db.close_pool()` on shutdown.
Four exception handlers normalize every non-2xx response to
`{"error": {"code", "message_he", "detail"}}`: the app's own `APIError`
(routes `raise not_found(...)` / `bad_request(...)` / `not_implemented(...)`
from `errors.py`), Starlette's `HTTPException` (framework 404s etc., code
derived from the status via a small map), `RequestValidationError`
(pydantic/query validation failures -> `code="validation_error"`,
`detail=exc.errors()`), and a catch-all `Exception` handler
(`code="internal_error"`, logged via `structlog`). If `web/dist` exists, a
custom `_SPAStaticFiles` (subclassing Starlette's `StaticFiles`) is mounted
at `/` after every API router: any 404 for a path *not* starting with
`api/` or `ws/` falls back to `index.html` (client-side routing); a 404
under `api/`/`ws/` propagates as a normal 404 instead of being swallowed
into the SPA shell.

### `services.py`

Every route handler calls into this module — never `eoa.db` directly —
via small helpers (`_fetchone`/`_fetchall`/`_execute`) over
`eoa.db.connection()`, always with parameterised SQL (never string-formatted
user input). Grouped by resource: status/pipeline (`services_status()` pings
Postgres/Ollama/SearXNG/ntfy — the latter two via a 2s-timeout HEAD-then-GET
probe — plus `pipeline_status()` reading `jobs`/`run_log` and
`eoa.resources.gate.gate()`), items (`list_items` with level/domain/since/q
filters + paging, `get_item`, `item_feedback` writing `triage_feedback` and
updating `items.level` via `eoa.memory.relational.update_item_fields`,
`investigate_item` enqueuing a `deep_search` job at `priority=0`), entities
+ graph (`list_entities`/`get_entity` join `items.entities_mentioned` by
name since entities have no item FK; `build_graph` and the three
`GET /api/graph/query` named queries call straight into
`eoa.memory.graph`), reports + `morning()` (reads report YAML/HTML off
disk relative to `REPO_ROOT`, aggregates a best-effort `night_summary` from
the latest `daily_run` job's `result` payload or, if absent, from real
`items`/`jobs`/`run_log` counts in that job's time window — never invented
numbers), investigations, `ask_retrieve`/`ask_build_messages` (RAG
retrieval + prompt assembly for `/api/ask`), conferences (true stub, see
below), clarifications, surveys, lessons, jobs/`run`, and settings
(YAML read/validate/atomic-write).

**Adapters for concurrently-developed, not-yet-implemented modules** (per
the task brief): `_deep_search_answer()` optionally imports
`eoa.search.deep_search.load_answer(job_id)`; if that module/function
doesn't exist yet it falls back to the job's own `result` column, then to
`{"error": {"code": "not_implemented", ...}}` — never a fabricated
investigation outcome. `list_conferences()` / `conferences_ical()` are
honest stubs (`[]` / an empty `VCALENDAR`) per `docs/API.md`'s explicit
"phase C, stub returns [] for now", even though the `conferences` table
already exists in the schema — the ingestion pipeline that would populate
it isn't built yet.

**Known limitations, called out in code comments rather than hidden:**
`GET /api/items/{id}`'s `edges` is always `[]` — `eoa.memory.graph`'s
public API (`neighbors()`) returns vertices, not edge properties, so there
is no way to look up "edges evidenced by this item_id" without adding
Cypher outside `graph.py` (out of this module's file scope). `build_graph()`
similarly cannot recover a real `item_id`/`evidence` per edge from
`neighbors()`, so those two fields are `null` on every `/api/graph` edge —
the `src`/`dst`/`label` are real, evidence attribution is not yet exposed.
`ItemCard.key_facts` is always `[]`: nothing in the `items` schema models
per-item structured facts yet.

### `routes/ask.py` and `ollama_client.chat_stream()`

`POST /api/ask` retrieves up to 8 items nearest the question's embedding
(`ollama_client.embed` + `eoa.memory.vector.nearest`) plus any explicit
`context_item_ids`/`context_entity_ids`, numbers them `[n]`, wraps each
through `ollama_client.wrap_data` (so retrieved text is DATA, never
instructions, per `docs/CONVENTIONS.md` rule 3), and builds a Hebrew RAG
system prompt from the existing `system_analyst` template
(`eoa.llm.prompts.render("system_analyst", data_guard=DATA_GUARD_SYSTEM)`)
plus a citation instruction. The response streams over SSE
(`text/event-stream`): a `citations` event first, then a `token` event per
content delta from the new `ollama_client.chat_stream()` generator
(`role="resident"`, `interactive=True`), then `done` (or an `error` event on
failure — the stream never just dies). `chat_stream()` was added at the end
of `agent/eoa/llm/ollama_client.py` without touching any existing function,
mirroring `chat()`'s gate-acquire/payload shape but with `stream: true` and
yielding `message.content` deltas from the newline-delimited response.

### Tests

`tests/unit/test_api_smoke.py` — `fastapi.testclient.TestClient` against
`create_app()`, with `eoa.db.get_pool`/`close_pool` monkeypatched (no
Postgres) and `eoa.api.services` functions monkeypatched per-test with
fixtures (no DB queries, no Ollama). Covers `/api/status`, `/api/items`,
`/api/items/{id}/feedback` (success, 404, and a 400 for an invalid
`user_level`), `/api/morning`, `GET /api/settings/taxonomy`, and the
`{"error": {...}}` shape for a 404 on an unmapped route, a 404 on an
unknown settings name, and a 422 from FastAPI's own query validation.
Passes today via `PYTHONPATH=agent python -m pytest tests/unit -q`
(48/48 across the whole suite, no DB/GPU/Ollama required) and is clean
under `ruff check` / `ruff format --check`.

## Report layer

Files: `agent/eoa/report/daily.py`, `agent/eoa/report/docx_builder.py`,
`agent/eoa/report/qa_citations.py`, `agent/eoa/llm/prompts/report_daily.md`,
`tests/unit/test_report_qa.py`, `tests/unit/test_docx_builder.py`.

Produces the daily report (`output/reports/daily_YYYY-MM-DD.{docx,md,html}`)
from analyzed `items`: a numbered-citation Hebrew RTL Word document plus
Markdown and HTML siblings, gated by a citation QA pass that blocks
uncited factual claims per `docs/CONVENTIONS.md` rule 4.

### `agent/eoa/report/qa_citations.py`

`check(draft: DailyReportDraft, items) -> QAResult` (`passed`, `errors:
list[str]`, `uncited_sentences`, `bad_refs`). Splits `exec_summary_he` and
every section's `prose_he` into sentences (`split_sentences`, on `. ? ! :`
followed by whitespace/EOS), skipping a decimal point mid-number and a
period immediately after a small allow-list of Hebrew abbreviations
(ד"ר, ארה"ב, צה"ל, ...) — checked by testing whether the text before the
trailing punctuation ends with the bare abbreviation, since Hebrew
abbreviations carry their own gershayim/geresh and never end with a
literal period themselves. `is_factual(sentence)` flags a sentence as
needing a citation if it contains a digit, a currency sign, a capitalized
Latin token (entity), a Hebrew or English month name, or one of a small set
of announcement verbs (זכתה, חתמה, רכשה, ...). Every factual sentence must
carry at least one `[n]`; every `[n]` anywhere (including a non-factual
sentence) must resolve to an `n` present in `items`. `outlook_he` is exempt
from the citation requirement (it's the analyst's own forward-looking
judgement) but must open with an explicit assessment marker (להערכתנו /
נראה ש / ייתכן); out-of-range refs inside it are still flagged.
`QAResult.errors` are precise Hebrew messages, reused verbatim as the
corrective-retry prompt in `daily.py`.

### `agent/eoa/report/docx_builder.py`

python-docx + lxml. Hebrew RTL correctness is the point of this file:

- `_configure_document_defaults(doc)` sets `w:bidi` + right `w:jc` on both
  `docDefaults/w:pPrDefault` and the `Normal`/`Heading 1`/`Heading 2`/
  `Title`/`Subtitle` styles, and `w:rtl` + `w:rFonts[w:cs]="David"` (with
  `w:ascii`/`w:hAnsi`="Arial") on `docDefaults/w:rPrDefault` and those same
  styles — David only ever applies to Hebrew (complex-script) glyphs, Arial
  to Latin ones, which is the standard OOXML pattern for a bilingual
  document (there is no docx primitive for a literal "fallback font chain"
  within one run).
- `add_mixed_paragraph(container, text, style=None, *, size_pt=11)` is the
  core primitive: `split_runs(text)` walks the string character by character
  classifying each as Hebrew (u0590-u05FF, uFB1D-uFB4F) or other (Latin
  letter/digit); punctuation/whitespace inherit whatever run they fall in
  rather than forcing a break, so a trailing space stays attached to the
  preceding Hebrew word instead of becoming its own fragment.
  `split_runs_with_citations` first carves every `[n]` token out of the raw
  text (before he/other classification) into its own "cite" run — doing
  this after classification was tried first and is wrong: a bracket has no
  letter/digit class of its own, so it silently inherits the class of
  whichever run precedes it, and a `[` immediately after Hebrew text ends
  up trapped inside the Hebrew run instead of opening the citation token
  (caught by `tests/unit/test_docx_builder.py`, which failed on exactly
  this before the fix). Each `he` run gets `run.font.rtl = True` and
  `w:rFonts[w:cs]="David"`; each `other`/`cite` run stays `rtl=False`/Arial;
  `cite` runs are additionally superscripted at `size_pt - 2`. `container`
  can be the `Document` or a table cell — both expose `.add_paragraph`.
- `add_hyperlink(paragraph, url, text, *, hebrew=False)` — relationship-based
  (`part.relate_to(..., RELATIONSHIP_TYPE.HYPERLINK, is_external=True)`),
  styled with the built-in `Hyperlink` character style rather than manual
  color/underline elements.
- `_set_table_rtl(table)` appends `w:bidiVisual` to `tblPr` (both the
  business-events table and the sources appendix use it).
- `_ensure_bidi(ppr)` / `_flag_update_fields(doc)` insert `w:bidi` /
  `w:updateFields` at a schema-valid position via python-docx's
  `insert_element_before(elm, *tagnames)` (inserts before whichever of the
  given sibling tags is present, appends otherwise) — needed because
  `CT_PPr`/`CT_Settings` have a strict child-element sequence and neither
  `w:bidi` nor `w:updateFields` has a high-level python-docx property.
  `_flag_update_fields` is what makes Word refresh the TOC field
  (`_add_toc_field`, a `w:fldSimple` with instr `TOC \o "1-2"`) and the
  footer PAGE field (`_add_footer_page_number`) the moment the document
  opens, instead of showing stale/placeholder field text.
- `build_docx(draft, items, events, *, period_end, deep_search=None,
  open_clarifications=None, generated_at=None, qa=None) -> Document`:
  title block (`TITLE_TEXT`, `hebrew_date_str(period_end)`, a generated-by
  line, and a bold QA-failure warning paragraph when `qa.passed` is
  `False`) -> page break -> TOC field -> page break -> "תקציר מנהלים" ->
  one "Heading 1" per `draft.sections` entry -> "טבלת אירועים עסקיים"
  (תאריך | סוג | צדדים | לקוח/תוכנית | סכום | מקור[n], only if `events`) ->
  "חקירות עומק" (only if `deep_search`) -> "נקודות פתוחות"
  (`draft.open_points_he` + any DB `open_clarifications`) -> "מבט קדימה" ->
  "נספח מקורות" (n, title, source, date, URL as a clickable hyperlink — one
  per item). `save_docx(doc, path)` creates parent dirs and saves;
  `validate_docx(path)` re-opens the zip, rejects duplicate part names,
  parses every `.xml`/`.rels` part with lxml (well-formedness only, not
  full OOXML schema validation), and re-opens with `docx.Document(...)` to
  confirm python-docx itself accepts it.
- `render_markdown(...)` / `render_html(...)` mirror the same section order
  as plain GFM tables and a standalone `<div dir="rtl" lang="he">` page
  respectively; the HTML renderer turns every `[n]` in prose into an
  `<a href="#src-n">` anchor pointing at the matching appendix row
  (`id="src-n"`) and HTML-escapes all model-generated text before
  citation-linking it.
- Level emoji/labels come from `config/taxonomy.yaml`'s `triage_levels`
  (via `daily.py`'s `_level_label`, not hardcoded in `docx_builder.py`),
  per "config, not code".

### `agent/eoa/report/daily.py`

- `collect_items(period_start=None, period_end=None, max_items=None)` —
  `items` with `level` in (red, orange) in the period (`published_at`
  falling back to `fetched_at`/`created_at`), `security_status='clean'`,
  `dedup_of IS NULL`, ordered by `score DESC`, capped at
  `config.triage.daily_report_max_items`; if fewer than 3 rows come back the
  same query is re-run additionally allowing `yellow`. Each row gets a
  stable 1-based `n`. **Schema closure:** `items` table now has `key_facts
  TEXT[]`, `uncertainty_he TEXT`, and `source_name TEXT` columns added via
  migration `db/migrations/versions/0002_analysis_fields.py` (run post-0001).
  `eoa.memory.relational._ITEM_UPDATABLE_FIELDS` includes all three fields,
  so `eoa.pipeline.analyze.persist_analysis()` can now correctly persist key
  facts and uncertainty from the LLM. A database trigger
  `trg_items_source_name` on `items` BEFORE INSERT auto-populates
  `source_name` from the linked `sources.name` when not explicitly provided,
  so data-layer code never needs to copy that field twice.
- `collect_events` / `collect_deep_search` / `collect_open_clarifications` —
  `events` in the period (joined to `items`/`sources` for a display source
  name); `jobs` rows with `kind='deep_search'`, `state IN ('done',
  'partial')`, `finished_at` in the period, left-joined to the triggering
  item via `payload->>'item_id'` and shaped from `jobs.result` (an
  `InvestigationOut`-like payload — degrades to empty fields since no
  deep-search execution module writes `jobs.result` yet); `clarifications`
  rows with `answered_at IS NULL`.
- `draft_report(items) -> DailyReportDraft` — zero items short-circuits to a
  fixed "no new items" draft with no LLM call; otherwise renders
  `llm/prompts/report_daily.md` (items grouped by taxonomy domain order via
  `_group_by_domain`/`_format_items_block`, each item block showing
  n/title/source/date/level/summary_he/so_what_he/key_facts), wraps that
  block with `wrap_data(...)` per the DATA-guard convention, and calls
  `chat_structured("resident", DailyReportDraft, ..., task="report")`
  (`config.ollama.num_ctx.report = 32768`).
- `build_daily(period_start=None, period_end=None) -> ReportPaths(docx, md,
  html, report_id, qa)`: collect -> draft -> `qa_citations.check` -> on
  failure, one corrective retry (feeding `qa.errors` back to the model) ->
  re-check -> if still failing, `_strip_uncited` removes exactly the
  sentences `qa` flagged (and any section that becomes empty), and the
  final `QAResult` is forced `passed=False` carrying the original
  (pre-strip) errors so `qa_report` shows what was actually wrong ->
  `_extend_citation_registry` builds the numbered list used for rendering
  (the LLM-facing `items` list extended with any event whose source item
  wasn't already numbered, so the business table's מקור[n] and the sources
  appendix stay consistent without affecting the citation-range QA, which
  always validates against the original `items`) -> `build_docx` +
  `save_docx` + `validate_docx`, `render_markdown`, `render_html` written
  to `output/reports/daily_<period_end>.{docx,md,html}`
  (`config.report.output_dir`, resolved against `eoa.config.REPO_ROOT` if
  relative) -> one `reports` row inserted (`kind='daily'`,
  `items_included`, `qa_passed`, `qa_report` JSONB with
  `errors`/`uncited_sentences`/`bad_refs`). All "now" reads go through
  `zoneinfo.ZoneInfo("Asia/Jerusalem")` per `docs/CONVENTIONS.md` rule 7; DB
  `date` columns/params stay naive `datetime.date` (no tz component,
  matching `events.date`/`reports.period_*` as `DATE` columns).

### Tests

`tests/unit/test_report_qa.py` — sentence splitting (boundaries, decimal
guard, abbreviation guard, empty input), `is_factual` per rule, and
`check()` (cited passes, uncited factual fails, out-of-range ref fails,
section prose checked, outlook exempt-but-needs-marker, multi-citation
sentences). `tests/unit/test_docx_builder.py` — `split_runs`/
`split_runs_with_citations` in isolation, `add_mixed_paragraph` (RTL flags
per run, bidi+right-aligned paragraph, superscript citations),
`add_hyperlink`, then a full `build_docx()` from a 3-item/1-event fixture
reopened with python-docx: document defaults carry `w:bidi`/`w:rtl`,
expected headings exist, hyperlink count equals item count, both tables
are `bidiVisual`, TOC/PAGE fields and `w:updateFields` are present, a
failing `QAResult` renders a visible warning paragraph; `validate_docx`
round-trips a saved file and separately rejects a hand-corrupted duplicate
zip part / truncated XML part. `render_markdown`/`render_html` are checked
for citation numbers, the sources appendix, `dir="rtl"`, `#src-n` anchors,
and HTML-escaping of model text. No DB and no Ollama in any of these —
`daily.py`'s DB- and `chat_structured`-calling functions
(`collect_*`/`draft_report`/`build_daily`) are exercised only through
`docx_builder`/`qa_citations`, which take plain dicts/`DailyReportDraft` in,
not through the DB-touching collectors themselves (would need
`respx`/DB mocking, out of scope for this pass). 54/54 pass via
`PYTHONPATH=agent python -m pytest tests/unit/test_report_qa.py
tests/unit/test_docx_builder.py -q`; both new source files and both new
test files are clean under `ruff check` / `ruff format --check`.

A sample report built from the same 3-item/1-event fixture used in the
docx test is saved at `output/reports/sample_daily.docx` (generated and
`validate_docx`-checked directly against `docx_builder`, not through
`build_daily`, since that needs a live DB).

## Web UI

`web/` — React 19 + TypeScript + Vite + Tailwind v3, the "חדר מצב + עמית"
hybrid ops console (`תוכנית_פיתוח_מפורטת_v2.md` §8, alternative C): a
dashboard-first console with a persistent, dockable "שאל את האנליסט" chat
panel, built end to end against `docs/API.md` with no dependency on the
concurrently-developed backend. Fully RTL (`dir="rtl" lang="he"` on
`<html>`), dark theme by default with a light toggle, Heebo (Hebrew/body)
+ IBM Plex Mono (numbers/codes, `tabular-nums`) via Google Fonts, petrol
accent (`--accent: #17909f`) / thermal-amber "hot" accent
(`--hot: #d97a3f`), triage colors paired with icons (not color alone).
Latin tokens inside Hebrew text are wrapped in `<bdi>` throughout.

### Layout

`src/components/shell/AppShell.tsx` composes the persistent shell used by
every route: `TopBar` (page title, night-window badge, "הרץ עכשיו" →
`POST /api/run`, theme toggle, ⌘K), `NavRail` (right/start side, the 9
screens, icon-only under `md:`), `StatusStrip` (bottom, fixed — VRAM bar,
GPU %/°C, RAM, disk, loaded-model chip, queue depth, current stage,
PG/Ollama/SearXNG/ntfy service dots, fed by `useStatusSocket` from
`WS /ws/status` with backoff auto-reconnect and an explicit "מנותק"
state), and `ChatPanel` (left/end side, collapsible, backed by the shared
`useAskChat` hook so the docked panel and the full `/ask` page are the
same logic). `CommandPalette` is the ⌘K global search over items/entities.
`useUiStore` (zustand, `src/store/uiStore.ts`) holds only small UI state —
theme (persisted to `localStorage`), chat open/collapsed, and the chat's
context-item list (drag-and-drop or "הוסף להקשר" from any item/entity via
`AddToContextButton`) — everything else is server state through TanStack
Query.

### Screens (`src/pages/`, routed in `src/App.tsx`)

1. `MorningPage` (`/`) — night-summary tiles, latest report's HTML exec
   summary with `[n]` citation chips linked to `/feed?open=<id>`
   (`lib/reportHtml.ts` maps `[n]` → `items_included[n-1]`, the report
   builder's own convention — the contract has no explicit per-report
   citation map), 3 headlines, open points with inline one-click answers,
   "פתח דוח docx" download.
2. `FeedPage` (`/feed`) — dense, windowed feed (`hooks/useVirtualList.ts`,
   a small fixed-row-height windower, no external virtualization
   dependency) with level/domain/text/sort filters, a master-detail panel
   (`FeedDetailPanel`) instead of inline row expansion so row height stays
   fixed. Full keyboard set on `window` (ignored while a form field has
   focus): `J`/`K` or arrows move selection, `1`-`4` set
   red/orange/yellow/archive via `POST /api/items/{id}/feedback`, `X`
   archives, `Enter` opens the detail panel, `I` opens a deep-search via
   `POST /api/items/{id}/investigate`, `A` adds the item to the chat
   context. "למה הציון?" reveals `triage_reason`. `?open=<id>` deep-links
   into a specific item (used by Morning/Ask citation clicks).
3. `EntitiesListPage` (`/entities`) + `EntityDetailPage`
   (`/entities/:id`) — search list; detail page has a Cytoscape graph
   (`components/entities/EntityGraph.tsx`, nodes shaped/colored by kind,
   click an edge for its label/evidence + a link to the source item),
   depth 1/2 selector, the three named-query buttons ("שותפי המתחרים" /
   "ספקי המתמודדים בתוכנית" / "סטארטאפים מחוברים" →
   `GET /api/graph/query`), an entity timeline, and a neighbors list.
4. `InvestigationsListPage` (`/investigations`) + `InvestigationDetailPage`
   (`/investigations/:jobId`) — table of jobs; detail view live-tails the
   ReAct log (round/lang/query/results/outcome) via
   `hooks/useInvestigationSocket.ts` (`WS /ws/investigations/{job_id}`),
   shows the final cited answer, and offers "עצור" /
   "המשך חקירה".
5. `AskPage` (`/ask`) — the same `ChatThread` component as the docked
   panel, uncollapsed, with a sources side-list; streams
   `POST /api/ask` SSE (`token`/`citations`/`done`) through
   `hooks/useAskChat.ts`. `[n]` markers are rendered as hover/click
   citation chips by `components/CitationText.tsx` (shared with the
   investigation answer view).
6. `ConferencesPage` (`/conferences`) — 24-month table + iCal export
   link; renders the "לוח הכנסים יופעל בשלב ג׳" empty state whenever the
   (stub) API returns `[]`, per contract.
7. `InboxPage` (`/inbox`) — open clarifications with one-click answers,
   the latest survey (choice/scale/text question types), and "מה למדתי
   ממך" lessons with delete.
8. `ReportsPage` (`/reports`) — kind-filtered list; HTML report viewer
   with an auto-generated TOC (`h2`/`h3` walk) and docx/md download
   links.
9. `SettingsPage` (`/settings`) — tabbed YAML editors for
   config/sources/watchlist/taxonomy/models against `GET`/`PUT
   /api/settings/{name}`, surfacing `errors[]` from a failed validation;
   quick eco/full mode + "הרץ ריצה יומית" controls; a jobs table with
   cancel.

### API layer and mock mode

`src/api/types.ts` defines one `ApiClient` interface mirroring
`docs/API.md` exactly (same field names, same endpoints); `src/api/real.ts`
implements it against `fetch`/SSE/`WebSocket` through the Vite dev proxy
(`/api`, `/ws` → `http://127.0.0.1:8765`, see `vite.config.ts`) and
`src/mocks/mockApi.ts` implements the identical interface over in-memory
data — no page or component ever branches on which one is active.
`src/api/index.ts` picks one via `VITE_USE_MOCKS` (`.env`/`.env.local`,
default **off**, see `.env.example`). Mock data
(`src/mocks/data/*.ts`) is realistic Hebrew defense-EO content: 40 items
(deterministic `mulberry32` PRNG so runs and tests are stable) across the
`config/taxonomy.yaml` domains, 12 entities (Elbit Systems, Rafael,
Leonardo DRS, HENSOLDT, Teledyne FLIR, Safran, Anduril, Aselsan, IAI,
מפא"ת, EDF, a fictional startup), 2 investigations (one done, one
running), 1 daily report, plus clarifications/survey/lessons/jobs and a
simulated `/ws/status` tick and `/ws/investigations/{id}` log feed.
`src/lib/taxonomy.ts` mirrors `config/taxonomy.yaml`'s domain
ids/labels for filter UI only — the backend config files stay the
source of truth.

### Stack notes / deviations

- React 19, TypeScript, Vite 5, Tailwind v3 (CSS-variable tokens in
  `src/styles/globals.css`, light override via `[data-theme="light"]`,
  dark via `prefers-color-scheme` when no explicit choice is stored),
  `react-router-dom` v6, `@tanstack/react-query` v5, `zustand` v5,
  `cytoscape` + `@types/cytoscape`, `recharts`, `lucide-react`.
- `recharts` is installed per the required stack but not yet wired into
  a chart — the screens built in this pass use compact stat tiles/meters
  (`StatTile`, the status-strip `Meter`) rather than time-series charts;
  nothing in `docs/API.md` v1 currently demands one. Left as a documented
  gap, not a silent omission.
- No UI kit; all components hand-written for RTL correctness. Only
  physical Tailwind border/inset utilities are used for panel dividers
  (`border-l`/`border-r`) even where a logical `border-s`/`border-e`
  would read more "correctly RTL", because Tailwind v3's logical-property
  support does not clearly cover border-*width* utilities; logical
  utilities that Tailwind v3.3+ does support (`ms-`/`me-`/`ps-`/`pe-`/
  `start-`/`end-`/`text-start`/`text-end`) are used freely.
- Command palette (⌘K) does a live client-side query against
  `GET /api/items`/`GET /api/entities` rather than a dedicated search
  endpoint (none exists in the contract).
- `npm run build` code-splits `cytoscape`, `recharts`, the React/router
  vendor chunk, and the TanStack Query vendor chunk
  (`vite.config.ts` → `build.rollupOptions.output.manualChunks`) so no
  chunk exceeds Vite's 500 kB warning threshold. Current production build:
  index.html 1.1 kB, CSS 18.1 kB (4.7 kB gzip), JS ≈795 kB raw / ≈255 kB
  gzip total across 5 chunks (largest: `cytoscape` 443.8 kB raw /
  142.4 kB gzip, `index` 274.7 kB raw / 83.0 kB gzip).

### Scripts (`web/package.json`)

`npm run dev` (Vite dev server, port 5173, proxying `/api` and `/ws` to
`127.0.0.1:8765`), `build` (`tsc -b && vite build`), `preview`, `test`
(`vitest run`) / `test:watch`, `lint` (`eslint .`), `format`
(`prettier --write .`).

### Tests (`vitest` + `@testing-library/react`, `src/test/setup.ts`)

20 tests across 4 files, all green: `LevelBadge.test.tsx` (label +
`data-level` + accessible name per triage level, size variant),
`CitationText.test.tsx` (`[n]` → chip, unmatched `n` left as plain text,
hover tooltip content, `onOpenItem` callback), `StatusStrip.test.tsx`
(parses a full `StatusResponse` into the VRAM/GPU/RAM/disk/service-dot
readouts, and renders the disconnected state instead of stale numbers),
and `FeedPage.test.tsx` (J/K selection movement including the
end-of-list clamp, 1-4 feedback calls, `X` archive, `Enter` opens the
detail panel via `GET /api/items/{id}`, `I` triggers investigate, and
typing shortcuts are ignored while a form field has focus) — the API
module is mocked with `vi.mock("@/api", ...)` so these run without a
backend or `VITE_USE_MOCKS`.

### What's stubbed / left for later

- `ConferencesPage` only implements the phase-C empty state and the
  table/iCal-link chrome — there is no real data to page through yet,
  matching the backend's own stub (`docs/API.md`: "phase C, stub returns
  [] for now").
- No dedicated E2E/Playwright suite — verification here was `lint` +
  `vitest` + `build` plus a manual pass through all 9 screens (both
  themes, desktop and a 375 px mobile viewport) against
  `VITE_USE_MOCKS=true` in the browser preview tool.
- The "3 usability sessions with the analyst" step in §8.4 of the dev
  plan is a product/pilot activity, not a coding task, and is out of
  scope for this pass.

## Orchestrator

Files: `agent/eoa/orchestrator/main.py`, `agent/eoa/orchestrator/jobs.py`.

The scheduler entry point (`python -m eoa.orchestrator.main`) runs a background APScheduler daemon for nightly batch cycles (01:00 UTC), pre-flight checks (23:30), daytime RSS polling (every 2 hours), and on-demand investigation jobs. The job worker loop claims queued jobs in priority order and executes them with deadline budgeting and circuit breakers per stage.

### `main.py`

- `configure_logging()` — sets up structlog with JSON output to stdout (pretty-printed if a TTY is attached).
- `pre_flight()` — 23:30 check: postgres, ollama, searxng health; disk/thermal status; resident model warm-up; backup.
- `build_scheduler()` — APScheduler background scheduler with CronTrigger jobs (configurable from `config.yaml`).
- `main()` — register signal handlers (SIGTERM/SIGINT), start scheduler, run the job `Worker` loop in a thread, wait for signals.

### `jobs.py`

- `RunState` dataclass: tracks job_id, deadline (monotonic), current stage, stats/failures dict, mode (full|eco).
  - `time_left_min()` — minutes before deadline.
  - `budget_min(stage)` — minutes allowed for this stage, capped by deadline minus reserves for report/notify.

- `_run_stage(rs, stage, fn, *, mandatory=bool)` — execute one stage with:
  - Budget check (skip if time remaining ≤ 0 and stage is not mandatory).
  - Circuit breaker (skip if stage has failed ≥ 3 times).
  - Heartbeat logging before/after.
  - Timeout via `concurrent.futures.wait(..., timeout=budget*60)`.
  - Error isolation (a failing source never aborts the run).

- `run_daily(job, *, night=bool)` → stats dict — orchestrates the full cycle:
  1. ingest (RSS/HTML)
  2. embed_dedup (cosine-sim dedup)
  3. classify (LLM scoring + entities)
  4. triage (red/orange/yellow levels)
  5. deep_search (max 4 ReAct investigations)
  6. analyze (deeper review)
  7. report (docx + ntfy push)
  8. export_backup (sqlite snapshot)

  Each stage receives a `budget_min()` cap from `RunState`. Returns `{"stage": result_or_error, ...}`.

- `Worker` — daemon thread that `claim_next_job()` in a loop (SELECT...FOR UPDATE SKIP LOCKED), calls `run_daily()` or other handlers, calls `finish_job(job_id, status, result/error)`, heartbeats throughout.

## Config

Files: `agent/eoa/config.py`, `config/*.yaml` (config.yaml, models.yaml, models.lock, taxonomy.yaml, watchlist.yaml, sources.yaml).

Typed settings loader (Pydantic) over YAML files + environment overrides. All defaults live in config files, never hardcoded.

### `agent/eoa/config.py`

- `settings()` → `Settings` (LRU-cached, re-read on `EOA_ROOT`/`EOA_CONFIG_DIR` env change).
- `Settings` includes: `ScheduleCfg`, `DeepSearchCfg`, `TriageCfg`, `DedupCfg`, `ResourcesCfg`, `OllamaCfg`, `SearXngCfg`, `FetchCfg`, `SecurityCfg`, `ReportCfg`, `NotifyCfg`, `RetentionCfg`.
- `ModelSpec` — `models.yaml` entry: vendor, origin (US/EU/UK/CH/CA/IL), license, role_hint, est_vram_mb, capabilities, ollama/hf URLs.
- `model(role: str)` → `ModelSpec` for a given role (resident/light/embed/guard_l1/guard_l2).
- All config reads go through this module, per "config, not code" rule 6.

### Config files

| File | Structure |
|------|-----------|
| `config.yaml` | Schedule (night window 01:00–06:00, pre-flight 23:30, RSS poll mins), stages (time budgets), deep_search/triage/dedup thresholds, resources (VRAM limits, disk thresholds, thermal pause/stop), ollama (context sizes, options), models (role→key mapping), searxng (engines, rate limit), fetch/security/report/notify/retention settings |
| `models.yaml` | Model registry: `models: { model_key: { ollama, vendor, origin, role_hint, est_vram_mb, ctx_max, capabilities } }` |
| `models.lock` | Auto-generated; pinned model digests (`ollama pull` output). Regenerated by `eo models lock`. |
| `taxonomy.yaml` | Domain/sub-domain classification schema (categories like eo_sensors, ir_imaging, etc.) |
| `watchlist.yaml` | Entities to track: companies, programs, conferences (name, month, cadence, status) |
| `sources.yaml` | RSS/HTML sources: name, url, kind, lang, reliability, tags, selectors for html sources |

## Resource Gate

Files: `agent/eoa/resources/gate.py`, `agent/eoa/resources/gpu.py`.

Single checkpoint before every LLM call. Monitors GPU VRAM/util/temp, system RAM, disk, and Ollama loaded models.

### `agent/eoa/resources/gate.py`

- `ResourceGate` class (singleton via `gate()` factory):
  - `acquire(model_role: str, est_vram_mb: int, *, interactive=False)` → waits/proceeds or raises `ResourceUnavailable`.
  - `status()` → dict with gpu (vram_used_mb, vram_total_mb, util_pct, temp_c), ram, disk, thermal_state, loaded_models, recent decisions.
  - `force_night_mode: bool` — skip polite mode (for testing).

- Decision flow:
  1. Read telemetry (GPU, RAM, disk, Ollama).
  2. Hard stops: disk < min_free_disk_gb → raise; GPU temp > stop_c → raise.
  3. Thermal pause: temp > pause_c → sleep + retry (bounded by queue_timeout_min).
  4. Polite mode (outside night window, external GPU util > threshold): defer batch calls, allow interactive.
  5. VRAM: if model already loaded → proceed; else if (free + unloadable models' space) ≥ est_vram_mb + safety_margin → proceed or queue with exponential backoff.

- Every decision is best-effort logged to `resource_log` table and retained in memory (status panel queries `.history`).

### `agent/eoa/resources/gpu.py`

- `get_gpu_status()` → dict (vram_used_mb, vram_total_mb, util_pct, temp_c) from `nvidia-smi`.
- `get_ollama_loaded()` → list of loaded model names via `ollama list`.
- `get_ram_status()` → dict (total_mb, used_mb, free_mb).
- `get_disk_status(path)` → dict (total_gb, used_gb, free_gb).

All functions raise `RuntimeError` if the underlying tools fail (nvidia-smi missing, ollama unreachable, etc.).

## Security Guard

Files: `agent/eoa/security/guard.py`, `agent/eoa/security/heuristics.py`.

Multi-layer defense against prompt injection and malicious content in fetched text.

### `agent/eoa/security/guard.py`

- `ScreenResult` dataclass: verdict (clean|flagged|quarantined), score (0.0–1.0), layer (heuristic|l1|l2|sanitizer), kind, excerpt, heuristics/l1_score/l2/sanitizer_flags details.

- `screen(text, title, url, source_id, item_id, *, job_id=None)` → `ScreenResult`:
  1. **Sanitizer** (`extract_clean_text` side-effects): detects hidden text, invisible Unicode, base64 blobs → suspicious=True sets score.
  2. **L1 (CPU)**: DeBERTa prompt-injection classifier on 512-token chunks; max prob over chunks.
  3. **L2 (LLM)**: if L1 is uncertain or suspicious, LLM judge (no tools, reads text as DATA) on smaller excerpt.
  4. Quarantine logic: if flagged and `config.security.quarantine_on_flag` → item marked security_status='quarantined', logged to security_log.

- Thresholds (from config): L1/L2 cutoff, hidden_text_min_ratio, max_base64_blob_chars.

## Fetch Layer (detailed ref)

The fetcher layer is already documented above; here is a quick recap of the public API per `docs/CONVENTIONS.md` rule 13 (fetcher on egress network only).

- **Sources** (`config/sources.yaml`): 40 RSS + HTML sources, hand-verified 2026-09-04.
- **`eoa.fetch.rss`**: `parse_feed(raw, since_days=None)` → list[FeedEntry].
- **`eoa.fetch.html`**: `async fetch_page(url, *, max_bytes=None)` → FetchedPage; respects robots.txt (config-driven).
- **`eoa.fetch.sanitize`**: `extract_clean_text(html, url)` → CleanText (security-hardened via CONVENTIONS rule 3 DATA wrapping).
- **`eoa.fetch.service`**: `async run_ingest(source_ids=None, since_days=3)` → IngestStats. Runs on start, then loops every config.schedule.daytime_rss_poll_minutes.

## Pipeline Stages

Files: `agent/eoa/pipeline/` (dedup.py, classify.py, triage.py, analyze.py).

Each stage reads from `items` with `processed_stages` filtering, processes via LLM or heuristic, and writes results back via `update_item_fields()`.

### Common patterns

- `get_items_for_stage(stage)` — items not yet marked processed for this stage, security_status='clean' only.
- `mark_stage(item_id, stage, status='done')` — append stage to `processed_stages` array.
- `update_item_fields(item_id, **kwargs)` — restricted to allow-list (title, summary_he, so_what_he, confidence, level, entities_mentioned, embedding, analysis_text, etc.).

### `dedup.py`

- `run_dedup()` → dict (processed, duplicates_removed, errors).
  - Embed every unduplicated item with the configured embed model.
  - Find nearest neighbors (cosine > threshold, within lookback_days).
  - Mark duplicates with `dedup_of` FK to the earliest item, security_status='clean'.

### `classify.py`

- `classify_item(item_dict, *, role='resident')` → `ClassifyOut` (pydantic).
- `run_classify(role='resident')` → `ClassifyStats` (done, out_of_scope, failed).
  - LLM rates confidence (0–1) against taxonomy domains/subs.
  - Extracts mentioned entities (company/program names); upserts to entities table.
  - Sets `items.summary_he`, `items.domain`, `items.subdomain`, `items.confidence`.

### `triage.py`

- `triage_item(item_dict, *, role='resident')` → int (triage_level: 0–10).
- `run_triage(role='resident')` → `TriageStats`.
  - LLM scores urgency/impact (0–10) given summary_he, domain, source reliability.
  - Thresholds from config: red (≥8), orange (≥6), yellow (≥4), below → archive.
  - Sets `items.level` and controls what makes it into `daily_report_max_items`.

### `analyze.py`

- `analyze_item(item_dict, *, role='resident')` → `AnalysisOut` (pydantic).
- `run_analyze(role='resident')` → `AnalysisStats`.
  - Deep review for red/orange items; extracts key_facts, so_what_he, uncertainty.
  - Persists to `items.analysis_text` (JSON).

## Deep Search

Files: `agent/eoa/search/deep_search.py`, `agent/eoa/search/searxng_client.py`.

Autonomous multi-round ReAct investigation triggered for red-level items. Logs every attempt to `investigation_log`.

### `searxng_client.py`

- `search(query, lang='en', *, pages=1)` → list[SearchHit] (title, snippet, url, engine).
- `ping()` → bool (SearXNG liveness check, 2s timeout).
- Every search is logged with query, count, query_plan round, and timestamp.

### `deep_search.py`

- `investigate(question, *, item_id=None, job_id=None)` → `Investigation` (result: InvestigationOut | None, rounds, total_queries, total_pages, total_time_sec).
- `load_answer(job_id)` → InvestigationOut | None (read from jobs.result, best-effort).

**4-round persistence protocol:**
1. Direct queries (Hebrew + English).
2. Reformulations (synonyms, alternate names, acronyms).
3. Source-type switch (press, SEC/EDGAR, contracts, patents, archives, conferences; add secondary languages).
4. Entity decomposition (subsidiaries, partners, program codes).

**Budgets:** max_queries, max_pages, per_investigation_timeout_min, confidence_stop (stop early if answer confidence ≥ threshold).

**LLM agents:** `query_planner` (no tools) decides next query; `reader` (no tools, text is DATA) summarizes fetched pages; orchestrator loop calls `search()` / `fetch_page()` tools and feeds results back.

## Notification (ntfy)

Files: `agent/eoa/notify/ntfy.py`.

Push notifications to self-hosted ntfy (fallback to public topic) and a 5-minute clarification gate for user input.

### `agent/eoa/notify/ntfy.py`

- `send(title, body, *, priority='default', tags=None, click=None, actions=None, to_public_fallback=True)` → Sent (ok, message_id, url).
  - Tries config.notify.url (self-hosted) first.
  - Falls back to config.notify.public_fallback_url/topic if available and `to_public_fallback=True`.
  - Priorities: min, low, default, high, urgent (int 1–5 mapped in ntfy headers).

- `ask_user(question, options: list[str], timeout_sec=300)` → str (option chosen or 'timeout').
  - Sends a notification with `Actions` for user to tap an option.
  - Polls the ntfy message for action responses up to timeout.
  - Returns the selected action or 'timeout' if the user doesn't respond.

- `status(message, priority='default')` — convenience wrapper for brief status updates.

## CLI

Files: `agent/eoa/cli.py`.

Typer-based command-line interface. Entry point: `eo` (mapped to `eoa.cli:app` in pyproject.toml).

### Commands

- `eo run [daily|ingest|classify|triage|analyze|report] [--mode=full|eco]` — run a cycle or one stage immediately (respects resource gate + polite mode).
- `eo investigate "question" [--item-id=N]` — trigger a deep-search investigation and print the answer.
- `eo status` — show services (postgres, ollama, searxng), GPU/RAM/disk, loaded models, recent gate decisions.
- `eo models list` — show the registry (model_key: vendor, origin, capabilities, est_vram_mb).
- `eo models lock` — pin digests to `models.lock` from current Ollama library.
- `eo models pull <model_key>` — pull a single model to Ollama.
- `eo serve` — start FastAPI app (wraps `uvicorn eoa.api.app:app`).
- `eo orchestrate` — start the background scheduler (runs nightly + on-demand).

Every command binds `job_id`, `stage`, and other context to structlog before logging.

## Obsidian export (FR-6.5)

Files: `agent/eoa/export/__init__.py`, `agent/eoa/export/obsidian.py`,
`tests/unit/test_obsidian_export.py`. Config: `config.export.obsidian`
(`eoa.config.ObsidianExportCfg`, appended to `Settings.export: ExportCfg`).

Turns the relational/graph memory into a plain-Markdown [Obsidian](https://obsidian.md)
vault so an analyst can browse entities, items, reports, and daily digests as
linked notes without any dedicated UI. Entry point:
`eoa.export.obsidian.export_vault(since_days: int | None = None) -> ExportStats`.

### Config

```yaml
export:
  obsidian: { enabled: true, vault_dir: output/obsidian, entities: true, items: true, reports: true, min_level: yellow }
```

`enabled` gates the whole export (a `False` run is a documented no-op —
`export_vault()` returns a zero-count `ExportStats` and writes nothing).
`vault_dir` is resolved against `eoa.config.REPO_ROOT` when relative, same
convention as `config.report.output_dir` in `report/daily.py`. `entities` /
`items` / `reports` toggle each section independently. `min_level` is one of
`red/orange/yellow/archive` (`obsidian.LEVEL_ORDER`); `_levels_at_or_above()`
resolves it to "this level and everything more severe" (e.g. `yellow` →
`[red, orange, yellow]`), since the schema's `items.level` CHECK constraint
orders severity red > orange > yellow > archive, not alphabetically.

### Vault layout

- **`Entities/<Name>.md`** — one note per `entities` row, filename = the
  entity's own `name` (already unique in the schema, so no id suffix is
  needed). YAML frontmatter: `kind`, `country`, `aliases`, `focus`, `tags`
  (`["entity", kind]`). Body:
  - **"ציר זמן"** — `eoa.memory.graph.entity_timeline(entity_id)` (events)
    merged with items whose `entities_mentioned` array names the entity
    (queried directly here as plain parameterized SQL — not Cypher, so it
    doesn't need to live in `eoa.memory.graph`, matching the precedent set by
    `eoa.fetch.service`'s ad hoc `sources.fail_count` update), de-duplicated
    on `(item_id, text)`, sorted newest first. Each line:
    `- YYYY-MM-DD — [[Items/<id> <slug>]] — summary_he`.
  - **"קשרים"** — one `eoa.memory.graph.neighbors(entity_id, label=L)` call
    per label in `eoa.memory.graph.EDGE_LABELS` (that function has no
    "all labels" mode), rendered `- [[Entities/<Other>]] — LABEL (מקור: ...)`.
    **Known limitation, called out in `_entity_neighbor_lines`'s docstring
    and in the rendered מקור text itself rather than hidden:**
    `neighbors()` returns only the neighboring `Entity` vertex, never the
    edge itself, so the evidencing `item_id` cannot be resolved through the
    public graph API — the same gap `docs/MODULES.md`'s Web API section
    documents for `services.build_graph()`'s edges (`item_id`/`evidence`
    always `null`). Fixing this would mean issuing Cypher outside
    `eoa/memory/graph.py`, which `docs/CONVENTIONS.md` explicitly forbids
    ("the ONLY module allowed to issue Cypher"), so the מקור parenthetical
    is rendered as unresolved rather than invented, per rule 5.
  - **"מקורות"** — every item id referenced above, deduplicated.
- **`Items/<id> <slug>.md`** — one note per exported item, filename
  `<id> <slug(title)>` (the id keeps filenames collision-free even when two
  titles produce the same slug). Frontmatter: `url`, `source`
  (`source_name`), `published_at`, `domain`, `level`, `score`, `entities`
  (`entities_mentioned`). Body: `summary_he`, then optional
  "למה זה חשוב" (`so_what_he`), "עובדות מפתח" (`key_facts` bullet list),
  "אי-ודאות" (`uncertainty_he`), "ישויות" (wikilinks to each mentioned
  entity) — each section omitted entirely when the underlying field is
  empty, never rendered as an empty heading.
- **`Reports/<kind>_<date>.md`** — one note per `reports` row (`kind`,
  `period_start`, `period_end` in frontmatter). Body is `reports.path_md`'s
  file content verbatim if that path exists on disk (resolved against
  `REPO_ROOT`), else a placeholder sentence — never fabricated — plus a
  `**Word:** \`<path_docx>\`` pointer (a plain path reference, not a
  wikilink/embed, since the docx lives outside the vault).
- **`Daily/<date>.md`** — a MOC (map of content) per calendar date, listing
  that date's `red`/`orange` items only, **independent of `min_level`** (a
  dedicated `_list_items(["red", "orange"], since_days)` query, not filtered
  through the `min_level`-gated item set) — per the FR-6.5 spec's explicit
  "red/orange items" requirement. Each line:
  `- <emoji> <label> [[Items/<id> <slug>]]` (level emoji/label from
  `config.taxonomy.triage_levels`, same convention as `report/daily.py`'s
  `_level_label`).
- **`_index.md`** — counts of entities/items/reports/daily notes written and
  the run's `min_level`/`since_days`/timestamp. Deliberately plain text, not
  wikilinks to folder names — Obsidian only resolves `[[Name]]` to an actual
  note, and no `Entities.md`/`Items.md` folder-note exists.

### Idempotency / atomicity

Every file is written by `_atomic_write()`: `tempfile.mkstemp()` in the same
directory, write, `os.replace()` into place (single filesystem-level
rename, so a reader never observes a partial file), with the temp file
removed on any exception. Re-running `export_vault()` overwrites the same
deterministic filenames — nothing under `vault_dir` is ever deleted, so
notes a user adds by hand in Obsidian (or any file outside this module's own
naming scheme) survive re-export. Backlinks are never computed or stored by
this module: every cross-reference is a plain `[[wikilink]]`, and Obsidian
derives backlinks/the graph view itself from those on open.

### `slugify(name, *, max_len=80)`

Windows-safe filename fragment: strips `` /\:*?"<>| ``, collapses
whitespace, trims trailing dots/spaces (illegal at the end of a Windows
filename), truncates to `max_len`, and prefixes an underscore onto the
reserved DOS device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`-`9`,
`LPT1`-`9`, case-insensitive) since those are illegal as a Windows filename
even with an extension. Hebrew and other non-ASCII Unicode pass through
untouched — Windows/NTFS has no restriction on the script used, only on the
specific ASCII punctuation set above.

### Tests

`tests/unit/test_obsidian_export.py` (30 tests, no DB/AGE — every DB- or
graph-touching function is monkeypatched at the `eoa.export.obsidian` module
level, mirroring `tests/unit/test_persist_analysis.py`'s stubbing style):
`slugify` (ASCII, Hebrew preserved, unsafe-char stripping, whitespace
collapse, trailing dot/space trim, empty/None input, max-length truncation,
reserved Windows device names case-insensitive, a non-reserved name
containing "CON" as a substring left untouched), `_atomic_write` (content
written, parent dirs created, overwrite is idempotent, no leftover temp
file, Hebrew content round-trips), `_levels_at_or_above` (each `min_level`
value, `ConfigError` on an unknown level), `render_item_md` (a full item —
every optional section present and correctly formatted — and a minimal item
where every optional section is correctly omitted), an entity page built
from 2 events + 1 neighbor (`_entity_timeline_lines` newest-first ordering
and dedup, `_entity_neighbor_lines` per-label rendering, then the full
`render_entity_md` output asserting frontmatter/headings/wikilinks, plus a
no-events/no-neighbors case rendering the three placeholder sentences),
`render_report_md` (missing `path_md` file → placeholder + docx pointer;
existing `path_md` file → its content copied in, via a `REPO_ROOT`
monkeypatch onto `tmp_path`), `render_daily_md`, and `export_vault()`'s
documented `enabled=False` no-op short-circuit (zero-count `ExportStats`,
vault directory never created). Passes today via
`PYTHONPATH=agent python -m pytest tests/unit/test_obsidian_export.py -q`
(30/30) and is clean under `ruff check` / `ruff format --check`.

`PYTHONPATH=agent python -c "from eoa.config import settings; print(settings().export.obsidian)"`
confirms the config wiring: `enabled=True vault_dir='output/obsidian'
entities=True items=True reports=True min_level='yellow'`.
