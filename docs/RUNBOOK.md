# EO-Analyst Operations Runbook

> If you're on the ops console or responding to a failed run, start here.

## Table of Contents
1. [Daily Status Checks](#daily-status-checks)
2. [Re-Running a Stage](#re-running-a-stage)
3. [Restoring from Backup](#restoring-from-backup)
4. [Adding a Source or Watchlist Entry](#adding-a-source-or-watchlist-entry)
5. [Handling Resource Constraints](#handling-resource-constraints)
6. [Rotating the Embedding Model](#rotating-the-embedding-model)
7. [Network Isolation Verification](#network-isolation-verification)
8. [Integration Smoke Tests](#integration-smoke-tests)
9. [Web UI Quality Suite (Playwright)](#web-ui-quality-suite-playwright)

---

## Daily Status Checks

### Quick Status Snapshot

```bash
eo status
```

Shows: postgres, ollama, searxng health; GPU VRAM/util/temp; RAM free; recent gate decisions.

### Checking Tonight's Run

#### Option 1: Live logs
```bash
# Follow orchestrator (runs 01:00–06:00 Asia/Jerusalem)
docker compose logs -f agent

# Tail last 100 lines
docker compose logs --tail=100 agent
```

#### Option 2: Database queries
```bash
# Connect to postgres
psql -h 127.0.0.1 -p 5433 -U eoa -d eoanalyst

-- View run_log for tonight's job
SELECT id, kind, payload, status, started_at, finished_at, error FROM run_log
WHERE kind = 'daily_run' AND started_at > now() - interval '1 day'
ORDER BY started_at DESC;

-- Check stage timings
SELECT job_id, stage, status, duration_sec, error FROM resource_log
WHERE job_id = <job_id> ORDER BY ts DESC;

-- Last 20 items ingested
SELECT id, title, source_url, confidence, triage_level, fetched_at FROM items
ORDER BY fetched_at DESC LIMIT 20;

-- Items flagged by security (quarantine)
SELECT id, item_id, flag_level, reason, flagged_at FROM security_log
WHERE incident = TRUE ORDER BY flagged_at DESC;
```

#### Option 3: Notification channel (ntfy)
```bash
# Open your phone / Tailscale ntfy:
# https://ntfy.sh/dissertation_editor_ysf (public fallback)
# or over Tailscale: http://ntfy:80 (if on the network)

# Terminal monitoring:
curl -s "http://127.0.0.1:8090/eo-analyst/json" | jq .
```

### Manual Trigger

If a job is stuck or you want to run it now instead of waiting for 01:00:

```bash
# Manual daily run (not scheduled)
eo run daily

# With eco mode (skip heavy models)
eo run daily --mode=eco

# Just one stage
eo run ingest
eo run classify
eo run triage
eo run analyze
eo run report
```

---

## Re-Running a Stage

If a stage failed or you want to re-process items without re-fetching:

### Scenario: Re-classify items (after model swap or config change)

```bash
# Re-run classify on all items from today
eo run classify

# Manually in Python (one-off)
python3 << 'EOF'
from eoa.memory.relational import get_session
from eoa.pipeline.classify import run_classify

session = get_session()
# Query the items you want (e.g., fetched today)
items = session.query(Item).filter(Item.fetched_at > now() - interval '1 day').all()
for item in items:
    classify_item(session, item, role='resident')
session.commit()
EOF
```

### Scenario: Re-run triage (rescore, change thresholds)

```bash
eo run triage

# Or with the light model
eo run triage --mode=eco
```

### Scenario: Re-analyze items (different investigator model)

```bash
eo run analyze
```

### Scenario: Rebuild the daily report (same data, different template or format)

```bash
eo run report
```

The report stage reads items from `items` table and their analyses from `items.analysis_text`, so it won't re-ingest or re-classify. If you need to change the template:

1. Edit `agent/eoa/report/templates/daily_rtl.docx` (binary; use python-docx to script or use Word)
2. Or edit `config/config.yaml` → `report.template` to point to a different docx
3. Re-run: `eo run report`

---

## Restoring from Backup

Backups are created nightly in `output/backups/`:

```bash
ls -la output/backups/
# eo-analyst_2026-09-05.sqlite
# eo-analyst_2026-09-04.sqlite
# ...
```

Each is a full database snapshot (sqlite format).

### Full Restore

```bash
# Stop the agent (do NOT stop postgres mid-restore)
docker compose down agent fetcher web

# Dump current postgres
pg_dump -h 127.0.0.1 -p 5433 -U eoa eoanalyst > /tmp/pre-restore.sql

# Restore from backup
# (Convert sqlite to SQL or restore via SQLite's CLI — easier if you have sqlite3)
sqlite3 output/backups/eo-analyst_2026-09-05.sqlite .dump | psql -h 127.0.0.1 -p 5433 -U eoa eoanalyst

# Verify
eo status

# Restart
docker compose up -d agent fetcher web
```

### Restore a Single Table

If you only want to restore one table (e.g., items, entities):

```bash
sqlite3 output/backups/eo-analyst_2026-09-05.sqlite ".dump items" | psql -h 127.0.0.1 -p 5433 -U eoa eoanalyst
```

### Restore Graph Vertices/Edges

If you need to rebuild the knowledge graph from an older snapshot:

```bash
# Drop and re-init the graph
psql -h 127.0.0.1 -p 5433 -U eoa eoanalyst << 'EOF'
SELECT * FROM ag_catalog.drop_graph('eo_graph', true);
EOF

# Re-apply graph_init
docker compose exec -T postgres psql -U eoa -d eoanalyst -f - < db/graph_init.sql

# Re-populate from restored entities table
python3 db/seed/seed_watchlist.py
```

---

## Adding a Source or Watchlist Entry

### Add an RSS Feed or HTML Source

1. Edit `config/sources.yaml`:

```yaml
sources:
  - name: "Company Blog Example"
    url: https://example.com/feed.xml
    kind: rss
    country: us
    fallback_language: en
  - name: "Defense News HTML"
    url: https://defensenews.com/latest
    kind: html
    xpath: "//article[@data-type='news']"
    country: us
    fallback_language: en
```

2. Reload (orchestrator watches config, but to be safe):

```bash
# Restart fetcher to reload sources
docker compose restart fetcher

# Or manually trigger ingest
eo run ingest
```

### Add a Company to the Watchlist

1. Edit `config/watchlist.yaml`:

```yaml
entities:
  - name: "Company X"
    kind: company
    country: us
    confidence: high
    tags: [eo_sensors, ir_imaging]
```

2. Re-seed:

```bash
python3 db/seed/seed_watchlist.py
```

This upserts the entity into the `entities` table and creates a corresponding graph vertex.

### Add a Conference

```yaml
conferences:
  - name: "OptiCall 2026"
    month: 4
    year: 2026
    cadence: annual
    status: estimated
    tags: [eo, defense]
```

Same re-seed step applies.

---

## Handling Resource Constraints

### Scenario: Out of Memory (OOM) on GPU

The resource gate monitors VRAM automatically. If a model load fails:

```bash
# Check current usage
eo status

# Manually unload all Ollama models (they reload on next run)
ollama list
ollama rm gemma4:12b   # or whatever is loaded

# Restart Ollama
# Windows: restart from Ollama tray
# Linux: systemctl restart ollama
```

If OOM is persistent:

1. **Reduce batch size** in `config/config.yaml`:
   - `ollama.num_ctx` — lower context window per request
   - Reduce `deep_search.max_pages` or `max_queries`

2. **Switch to a smaller model**:
   - Change `config.models.resident` from `gemma4_12b` to `gemma4_e4b`
   - Re-run: `eo run daily --mode=eco`

3. **Enable polite mode** (skip heavy work if external GPU util > threshold):
   - Edit `config/config.yaml`:
   ```yaml
   resources:
     polite_mode:
       external_gpu_util_threshold: 25
       enabled_outside_night_window: true
   ```

### Scenario: GPU Thermal Throttle

If GPU temp hits `resources.gpu_temp_pause_c` (default 83°C), the gate pauses all LLM work for `queue_backoff_seconds` (exponential backoff).

```bash
# Monitor temp
watch -n 1 'nvidia-smi --query-gpu=index,name,temperature.gpu --format=csv'

# If persistent:
1. Allow the GPU to cool (ensure good ventilation, reduce other load)
2. Lower `resources.gpu_temp_stop_c` if you want a harder stop threshold (default 88°C)
3. Extend night window in `config/config.yaml` if the run won't fit before 06:00 deadline
```

### Scenario: Low Disk Space

Default warning at `resources.warn_free_disk_gb` (40 GB free); hard stop at `min_free_disk_gb` (20 GB).

```bash
# Check
df -h /

# Clean old backups (keep last 30, per config)
ls -1 output/backups/*.sqlite | sort -r | tail -n +31 | xargs rm

# Clean old logs
find output/logs -mtime +30 -delete
```

---

## Rotating the Embedding Model

Embeddings live in `items.embedding vector(1024)`. If you switch to a different dimension:

1. **Choose a new embedding model** in `config/models.yaml`:
   - `arctic_embed2` (1024-dim, current)
   - `embeddinggemma` (768-dim, fallback)

2. **Create a migration**:

```bash
# Create a new alembic migration
alembic revision --autogenerate -m "change_embedding_dim_to_768"

# Edit the generated file in db/migrations/versions/
# Add a migration step:
def upgrade():
    op.execute("""
        ALTER TABLE items
        ALTER COLUMN embedding TYPE vector(768)
        USING NULL;
    """)

def downgrade():
    op.execute("""
        ALTER TABLE items
        ALTER COLUMN embedding TYPE vector(1024)
        USING NULL;
    """)
```

3. **Apply the migration**:

```bash
docker compose run --rm agent python -m alembic upgrade head
```

4. **Update config**:

```yaml
# config/config.yaml
models:
  embed: embeddinggemma  # was arctic_embed2

# config/models.yaml
embeddinggemma:
  dim: 768  # changed from 1024
```

5. **Re-embed all items**:

```bash
# Clear embeddings (set to NULL for all items)
psql -h 127.0.0.1 -p 5433 -U eoa eoanalyst << 'EOF'
UPDATE items SET embedding = NULL;
EOF

# Re-run dedup (will re-embed)
eo run embed_dedup
```

---

## Network Isolation Verification

The `agent` container must NOT reach the public internet (ADR-002). Verify:

### Option 1: Windows (PowerShell)

```powershell
# Run the verification script (requires elevated PS)
.\scripts\host\firewall_ollama.ps1 -Verify

# Or manually:
docker compose exec agent curl -I https://example.com
# Should FAIL with name resolution error
```

### Option 2: Linux / WSL

```bash
bash scripts/verify_isolation.sh

# Or manually:
docker compose exec agent bash -c 'curl -I https://example.com || true'
# Should FAIL with "name resolution error" (DNS returns 0.0.0.0)
```

### Expected Isolation

- ✅ agent → postgres:5432 (on `internal` network, 172.28.0.10)
- ✅ agent → searxng:8080 (on `internal` network, 172.28.0.11)
- ✅ agent → ntfy:80 (on `internal` network, 172.28.0.12)
- ✅ agent → host.docker.internal:11434 (via `hostlink` bridge, resolves via `extra_hosts`)
- ❌ agent → public DNS (0.0.0.0, fails closed)
- ❌ agent → any internet domain

### If Isolation Fails

1. **Check DNS**: `docker compose exec agent nslookup example.com` should fail.
2. **Check extra_hosts**: `docker compose exec agent cat /etc/hosts` should list postgres, searxng, ntfy, host.docker.internal.
3. **Check firewall rule** (Windows only): Run `firewall_ollama.ps1` with admin rights to set the Windows Defender rule.
4. **Rebuild the agent image**: `docker compose build --no-cache agent`

---

## Integration Smoke Tests

The live stack (PostgreSQL, web API, fetcher, ntfy) has a comprehensive smoke test suite in `tests/integration/test_live_stack.py`. Run these to verify all services are functioning correctly.

### Running the Tests

```bash
# Set environment variables (optional; defaults to localhost)
export DATABASE_URL="postgresql://eoa:change-me-local-only@127.0.0.1:5433/eoanalyst"
export API_BASE_URL="http://127.0.0.1:8765"
export NTFY_BASE_URL="http://127.0.0.1:8090"

# Run all integration tests
PYTHONPATH=agent python -m pytest tests/integration/test_live_stack.py -q -m integration

# Run specific test class
PYTHONPATH=agent python -m pytest tests/integration/test_live_stack.py::TestDatabaseSchema -v

# Run with verbose output
PYTHONPATH=agent python -m pytest tests/integration/test_live_stack.py -v -m integration
```

### What Gets Tested

**Database & Schema (TestDatabaseSchema)**
- Alembic migrations are at the latest revision
- All core tables exist (sources, items, entities, events, conferences, reports, jobs, etc.)
- pgvector embedding column is present on items table
- Apache AGE graph `eo_graph` exists and Entity vertex count matches entities table

**API Endpoints (TestApiEndpoints)**
- `/api/status` returns service health (postgres, ollama, searxng, ntfy)
- `/api/items` returns paginated item list
- `/api/items/{id}` returns full item detail with clean_text and edges
- `/api/entities?q=...` searches entities by name
- `/api/graph?entity_id=...` returns knowledge graph nodes and edges
- `/api/conferences` and `/api/conferences/ical` return conference schedule
- `/api/surveys/latest` returns latest user survey (if available)
- `/api/feedback/meta` returns feedback metadata
- Error endpoints return proper `{"error": {"code", "message_he"}}` JSON
- `/` serves the React SPA (HTML)

**Fetcher Bridge (TestFetcherBridge)**
- `enqueue_job("fetch_url", ...)` creates a job in the queue
- Job transitions to `running` then `done` or `partial` state within 60 seconds
- Job result contains fetched content with `text` field

**Ntfy Integration (TestNtfyIntegration)**
- `POST` to `http://ntfy:8090/eo-analyst-test` returns 200 OK
- (Does NOT post to production topic `eo-analyst`)

**Network Isolation (TestNetworkIsolation)**
- `docker compose exec -T agent python -c "import httpx; httpx.get(...)"` fails
- Agent container cannot reach external URLs (network policy enforced)

### Test Results

Each test prints a result line like:

```
test_alembic_migration_at_head PASSED
test_core_tables_exist PASSED
test_api_status_endpoint PASSED
test_enqueue_and_poll_fetch_job SKIPPED (Fetch job did not complete within 60s)
test_agent_container_no_egress PASSED
```

Tests skip gracefully (not fail) when:
- Database is unreachable (`postgresql://...` fails)
- API endpoint returns 404 (feature not yet implemented)
- Docker or docker-compose is not available
- AGE extension is not installed
- Fetcher container is idle (job queue empty)
- Any service is temporarily down

### Continuous Integration

Add to your CI pipeline (e.g., GitHub Actions):

```yaml
- name: Integration smoke tests
  run: |
    PYTHONPATH=agent python -m pytest tests/integration/test_live_stack.py -q -m integration
```

---

## Web UI Quality Suite (Playwright)

A standalone Playwright suite lives at `e2e/` (its own `package.json` /
`node_modules` / `tsconfig.json` — independent of `web/`'s toolchain, and
never modifies anything under `web/`). It drives the **live app + real
backend** at `http://127.0.0.1:8765` — not mocks — and walks every screen
in `docs/MODULES.md` ("Web UI" section) asserting what a strict QA
engineer would: navigation/routing, RTL, keyboard shortcuts on the feed,
infinite scroll vs. the API total, WS-fed status strip readouts, streaming
on `/ask`, settings save round-trips, and an axe-core accessibility pass.

### Prerequisites

The app must already be reachable at `http://127.0.0.1:8765` (or set
`EOA_BASE_URL`). This suite does not start, build, or proxy the app — see
`e2e/README.md` for the full rationale and file-by-file breakdown.

### Running it

```bash
cd e2e
npm install
npm run install:browsers      # npx playwright install chromium — one-time
npm test                      # both viewports: 1440x900 desktop, 390x844 mobile
```

Useful variants:

```bash
npm test -- tests/02-feed.spec.ts             # a single screen
npm test -- --project=desktop-1440x900        # a single viewport
npm run test:headed                           # watch it click through the app
npm run report                                # open the last HTML report
```

### Reading the results

- **`e2e/report/index.html`** (`npm run report`) — full HTML report with a
  trace/screenshot/video attached to every failure.
- **`e2e/QA_FINDINGS.md`** — regenerated on every run (never accumulates
  stale findings from a prior run): a Hebrew-headed, severity-sorted list
  of every finding, combining (a) explicit findings the tests record
  mid-run for things that aren't simple pass/fail (e.g. an older/newer
  build showing different copy for the same underlying check) and (b)
  every outright test failure, each with its screenshot path and an
  expected-vs-actual description.

### Known live-data side effects

A few tests exercise real state-changing endpoints (re-rating an item via
the `1`-`4` feed shortcuts, saving a Settings tab) against the live
backend. Where the mutation isn't naturally idempotent, the test reads the
value first and restores it after asserting the request fired — see
`e2e/README.md` § "Notes on live-data side effects" for the exact
best-effort-cleanup caveat if a run aborts mid-test.

---

## Emergency Procedures

### Stop Everything

```bash
docker compose down
# or for a clean slate:
docker compose down -v  # also removes volumes
```

### Stop Just the Agent (Keep DB)

```bash
docker compose stop agent
docker compose logs -f agent  # inspect what went wrong
docker compose start agent
```

### Drain the Job Queue (If Stuck)

```bash
psql -h 127.0.0.1 -p 5433 -U eoa eoanalyst << 'EOF'
DELETE FROM jobs WHERE status NOT IN ('done', 'failed');
SELECT * FROM jobs;
EOF
```

### Reset a Stuck Job

```bash
# Mark job as failed so orchestrator doesn't retry forever
psql -h 127.0.0.1 -p 5433 -U eoa eoanalyst << 'EOF'
UPDATE jobs SET status = 'failed', error = 'manual reset' WHERE id = <job_id>;
EOF
```

### Reload Configuration

```bash
# Orchestrator re-reads config.yaml on each job start, so just:
docker compose restart agent
```

---

## Appendix: Common Error Messages

| Error | Cause | Fix |
|-------|-------|-----|
| `ResourceUnavailable: VRAM 8000 MB < min 9500 MB` | Not enough VRAM loaded | Run `ollama list`, then `ollama rm <model>` and re-run |
| `LLMOutputError: validation failed` | Model output doesn't match schema | Check recent prompts; may need to adjust prompt or model choice |
| `FetchError: robots.txt blocks` | Source respects robots.txt | Remove source or request robots.txt exception |
| `SecurityFlag: prompt injection detected` | Suspicious content in fetched text | Check quarantine in security_log; investigate source or adjust guard thresholds |
| `DeadlineExceeded` | Stage took too long | Reduce scope (fewer sources, shorter context), or extend night window |
| `pg_isready: could not connect` | Postgres not healthy | `docker compose logs postgres`, check volumes |

---

## See Also

- **CONVENTIONS.md** — Engineering hard rules
- **MODULES.md** — API reference (debug with structured logging by binding job_id, stage, item_id)
- **docs/adr/002-*.md** — Network isolation details
- **תוכנית_פיתוח_מפורטת_v2.md** — Full requirements + architecture (Hebrew)
