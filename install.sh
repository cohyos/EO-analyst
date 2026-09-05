#!/usr/bin/env bash
#
# EO-Analyst local development setup for Linux / WSL
#
# Idempotent initialization script that:
#   1. Checks prerequisites (docker, docker-compose, nvidia-smi, ollama, python/uv, node)
#   2. Creates .env from .env.example if missing, fills SEARXNG_SECRET with random hex
#   3. Builds docker images (postgres, agent, fetcher, web)
#   4. Starts postgres, searxng, ntfy containers
#   5. Waits for postgres health
#   6. Runs alembic migrations (prefers host python, falls back to container)
#   7. Applies db/graph_init.sql
#   8. Runs db/seed/seed_watchlist.py
#   9. Pulls Ollama models from config/models.yaml for roles in config/config.yaml
#  10. Prints instructions for setting Ollama env vars
#  11. Prints next steps
#
# Usage:
#   ./install.sh
#   ./install.sh --skip-models
#   ./install.sh --skip-build
#   ./install.sh --skip-build --skip-models
#
# Notes:
#   - Non-root execution expected. Docker group membership required.
#   - Ollama runs on the host (native service or container-ollama profile).
#   - Containers reach host Ollama via host.docker.internal (Docker Desktop) or the host network (Linux native).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
ENV_FILE="$REPO_ROOT/.env"
ENV_EXAMPLE="$REPO_ROOT/.env.example"

SKIP_MODELS=0
SKIP_BUILD=0

# Parse flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-models) SKIP_MODELS=1; shift ;;
        --skip-build) SKIP_BUILD=1; shift ;;
        *) echo "Unknown flag: $1"; exit 1 ;;
    esac
done

# Color output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

log_info() { echo -e "${CYAN}[INFO]${NC} $*"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ============================================================================
# 1. Check prerequisites
# ============================================================================
log_info "Checking prerequisites..."

check_command() {
    local cmd="$1"
    local hint="${2:-}"
    if ! command -v "$cmd" &>/dev/null; then
        log_error "$cmd not found in PATH. $hint"
    fi
    log_ok "$cmd found"
}

check_command "docker" "Install Docker: https://docker.io"
check_command "docker-compose" "Install Docker Compose: https://docs.docker.com/compose"
check_command "ollama" "Install Ollama: https://ollama.com"
check_command "python3" "Python ≥3.12 required. Install: https://python.org"
check_command "node" "Node.js ≥20 required. Install: https://nodejs.org"

# Check Python version
PY_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
    log_error "Python ≥3.12 required; found $PY_VERSION"
fi
log_ok "Python $PY_VERSION"

# Check nvidia-smi (optional)
if command -v nvidia-smi &>/dev/null; then
    GPU_INFO=$(nvidia-smi --query-gpu=name --format=csv,noheader,nounits 2>/dev/null || echo "unknown")
    log_ok "NVIDIA GPU detected: $GPU_INFO"
else
    log_warn "nvidia-smi not found. GPU allocation will fail at runtime. Install NVIDIA drivers if available."
fi

# ============================================================================
# 2. Create .env if missing
# ============================================================================
log_info "Preparing .env..."

if [[ ! -f "$ENV_FILE" ]]; then
    if [[ ! -f "$ENV_EXAMPLE" ]]; then
        log_error ".env.example not found at $ENV_EXAMPLE"
    fi
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    log_ok "Created $ENV_FILE from .env.example"
else
    log_ok "$ENV_FILE already exists"
fi

# Generate SEARXNG_SECRET if not set
if grep -q "SEARXNG_SECRET=change-me-local-only" "$ENV_FILE"; then
    SECRET=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p)
    sed -i.bak "s/SEARXNG_SECRET=change-me-local-only/SEARXNG_SECRET=$SECRET/" "$ENV_FILE"
    rm -f "$ENV_FILE.bak"
    log_ok "Generated SEARXNG_SECRET: $SECRET"
else
    log_ok "SEARXNG_SECRET already configured"
fi

# ============================================================================
# 3. Build docker images
# ============================================================================
if [[ $SKIP_BUILD -eq 0 ]]; then
    log_info "Building docker images..."
    cd "$REPO_ROOT"
    docker compose build postgres agent fetcher web || log_error "docker compose build failed"
    log_ok "Docker images built"
else
    log_warn "Skipping docker build (--skip-build)"
fi

# ============================================================================
# 4. Start services (postgres, searxng, ntfy)
# ============================================================================
log_info "Starting services (postgres, searxng, ntfy)..."
cd "$REPO_ROOT"
docker compose up -d postgres searxng ntfy || log_error "docker compose up failed"
log_ok "Services started"

# ============================================================================
# 5. Wait for postgres health
# ============================================================================
log_info "Waiting for postgres to be healthy..."
MAX_WAIT=60
WAITED=0
while [[ $WAITED -lt $MAX_WAIT ]]; do
    if docker compose exec -T postgres pg_isready -U eoa -d eoanalyst >/dev/null 2>&1; then
        log_ok "Postgres is healthy"
        break
    fi
    sleep 2
    WAITED=$((WAITED + 2))
done

if [[ $WAITED -ge $MAX_WAIT ]]; then
    log_error "Postgres did not become healthy after $MAX_WAIT seconds"
fi

# ============================================================================
# 6. Run migrations (prefer host python, fall back to container)
# ============================================================================
log_info "Running database migrations..."

MIGRATE_VIA_HOST=0

# Try host python first
if [[ -f "$ENV_FILE" ]]; then
    DB_PW=$(grep "^POSTGRES_PASSWORD=" "$ENV_FILE" | cut -d'=' -f2)
    if [[ -n "$DB_PW" ]]; then
        export DATABASE_URL="postgresql://eoa:${DB_PW}@127.0.0.1:5433/eoanalyst"
        cd "$REPO_ROOT"
        if python3 -m alembic upgrade head 2>&1; then
            MIGRATE_VIA_HOST=1
            log_ok "Migrations completed via host python"
        fi
    fi
fi

# Fall back to container
if [[ $MIGRATE_VIA_HOST -eq 0 ]]; then
    log_info "Running migrations via docker container..."
    cd "$REPO_ROOT"
    docker compose run --rm agent python -m alembic upgrade head || log_error "Alembic migrations failed"
    log_ok "Migrations completed via container"
fi

# ============================================================================
# 7. Apply graph_init.sql -- NO-OP (ADR-004: AGE replaced by the plain-SQL
#    graph_edges table, created by alembic migration 0006, applied in step 6).
#    db/graph_init.sql is kept only for reference; another agent owns this
#    installer, so this step is disabled rather than removed/rewritten.
# ============================================================================
log_info "Knowledge graph: no-op (AGE deprecated, ADR-004; graph_edges created by alembic)"

# ============================================================================
# 8. Run seed
# ============================================================================
log_info "Seeding database (watchlist, conferences, entities)..."

SEED_VIA_HOST=0

# Try host python first
cd "$REPO_ROOT"
if python3 db/seed/seed_watchlist.py 2>&1; then
    SEED_VIA_HOST=1
    log_ok "Seed completed via host python"
fi

# Fall back to container
if [[ $SEED_VIA_HOST -eq 0 ]]; then
    log_info "Running seed via docker container..."
    docker compose run --rm agent python db/seed/seed_watchlist.py || log_error "Seed script failed"
    log_ok "Seed completed via container"
fi

# ============================================================================
# 9. Pull Ollama models
# ============================================================================
if [[ $SKIP_MODELS -eq 0 ]]; then
    log_info "Pulling Ollama models..."

    cd "$REPO_ROOT"

    # Use Python to parse YAML and extract model keys
    MODELS_TO_FETCH=$(python3 << 'EOF'
import yaml

with open('config/models.yaml', 'r') as f:
    models_cfg = yaml.safe_load(f)

with open('config/config.yaml', 'r') as f:
    cfg = yaml.safe_load(f)

models_to_fetch = set()
for role in ['resident', 'light', 'hebrew_editor', 'heavy_investigator', 'embed', 'guard_l1', 'guard_l2']:
    model_key = cfg.get('models', {}).get(role)
    if model_key and model_key != 'null':
        models_to_fetch.add(model_key)

for key in sorted(models_to_fetch):
    print(key)
EOF
    )

    for model_key in $MODELS_TO_FETCH; do
        # Extract ollama name from models.yaml
        OLLAMA_NAME=$(python3 << EOF
import yaml
with open('config/models.yaml', 'r') as f:
    cfg = yaml.safe_load(f)
    spec = cfg.get('models', {}).get('$model_key', {})
    print(spec.get('ollama', ''))
EOF
        )

        if [[ -n "$OLLAMA_NAME" ]]; then
            log_info "Pulling $OLLAMA_NAME (key: $model_key)..."
            if ollama pull "$OLLAMA_NAME" 2>&1; then
                log_ok "Pulled $OLLAMA_NAME"
            else
                log_warn "Failed to pull $OLLAMA_NAME; continuing"
            fi
        fi
    done

    log_ok "Model pulls completed"
else
    log_warn "Skipping Ollama model pulls (--skip-models)"
fi

# ============================================================================
# 10. Instructions for Ollama environment variables
# ============================================================================
log_info "Ollama environment variables (ADR-002)..."
cat << 'EOF'

To set Ollama environment variables, add them to your shell profile (~/.bashrc, ~/.zshrc, etc.):

  export OLLAMA_HOST="0.0.0.0:11434"
  export OLLAMA_NO_CLOUD="1"
  export OLLAMA_MAX_LOADED_MODELS="1"
  export OLLAMA_NUM_PARALLEL="1"
  export OLLAMA_FLASH_ATTENTION="1"
  export OLLAMA_KV_CACHE_TYPE="q8_0"
  export OLLAMA_GPU_OVERHEAD="1258291200"
  export OLLAMA_KEEP_ALIVE="30m"

Or set them temporarily in your current shell:

  source <(cat << 'VARS'
    export OLLAMA_HOST="0.0.0.0:11434"
    export OLLAMA_NO_CLOUD="1"
    export OLLAMA_MAX_LOADED_MODELS="1"
    export OLLAMA_NUM_PARALLEL="1"
    export OLLAMA_FLASH_ATTENTION="1"
    export OLLAMA_KV_CACHE_TYPE="q8_0"
    export OLLAMA_GPU_OVERHEAD="1258291200"
    export OLLAMA_KEEP_ALIVE="30m"
  VARS
  )

Then restart Ollama to apply the settings.

EOF
log_ok "See Ollama configuration instructions above"

# ============================================================================
# Next steps
# ============================================================================
log_ok "Setup complete!"

cat << 'EOF'

Next steps:

1. Set Ollama environment variables in your shell profile (see above).

2. Restart Ollama to apply env vars:
   ollama serve  # or restart the Ollama service

3. Start the agent and web UI:
   docker compose up -d agent web

   Then open http://127.0.0.1:8765 in your browser.

4. Check status:
   eo status

5. View orchestrator logs:
   docker compose logs -f agent

6. Run a manual test cycle:
   eo run daily --mode=eco

See docs/RUNBOOK.md for operational procedures.

Config files (read/edit as needed):
  .env                       — database password, Ollama URL, etc.
  config/config.yaml        — schedule, thresholds, resource limits
  config/models.yaml        — Ollama model registry + vendor origins
  config/models.lock        — pinned model digests (auto-updated)
  config/watchlist.yaml     — companies, programs, conferences to track
  config/sources.yaml       — RSS feeds and HTML sources

Documentation:
  docs/CONVENTIONS.md        — hard rules and engineering standards
  docs/MODULES.md            — module/API reference
  docs/RUNBOOK.md            — ops procedures (check logs, re-run stages, restore from backup)
  docs/adr/002-*.md          — Ollama + network isolation (ADR-002)
  תוכנית_פיתוח_מפורטת_v2.md  — full development plan (Hebrew)

EOF
