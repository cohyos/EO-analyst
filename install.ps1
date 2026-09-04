#!/usr/bin/env pwsh
<#
.SYNOPSIS
EO-Analyst local development setup for Windows + PowerShell 7 (non-admin).

.DESCRIPTION
Idempotent initialization script that:
  1. Checks prerequisites (Docker Desktop, nvidia-smi, ollama, python/uv, node)
  2. Creates .env from .env.example if missing, fills SEARXNG_SECRET with random hex
  3. Builds docker images (postgres, agent, fetcher, web)
  4. Starts postgres, searxng, ntfy containers
  5. Waits for postgres health
  6. Runs alembic migrations (prefers host python, falls back to container)
  7. Applies db/graph_init.sql
  8. Runs db/seed/seed_watchlist.py
  9. Pulls Ollama models from config/models.yaml for roles in config/config.yaml
 10. Sets Ollama user-scope env vars (ADR-002)
 11. Prints next steps

.PARAMETER SkipModels
If $true, skip Ollama model pulls.

.PARAMETER SkipBuild
If $true, skip docker compose build.

.EXAMPLE
./install.ps1
./install.ps1 -SkipModels
./install.ps1 -SkipBuild -SkipModels

.NOTES
Non-admin execution. Requires Docker Desktop running. GPU/Ollama setup runs on the host
(native Windows service); containers reach it via host.docker.internal.
#>

#Requires -Version 7

param(
    [switch] $SkipModels,
    [switch] $SkipBuild
)

$ErrorActionPreference = "Stop"
$VerbosePreference = "Continue"

$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot ".env"
$envExample = Join-Path $repoRoot ".env.example"

Write-Host "EO-Analyst Setup (Windows, PowerShell 7)" -ForegroundColor Cyan

# ============================================================================
# 1. Check prerequisites
# ============================================================================
Write-Host "`n[1/10] Checking prerequisites..." -ForegroundColor Cyan

function Assert-Command {
    param([string] $Name, [string] $Hint = "")
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $cmd) {
        $msg = "❌ $Name not found in PATH."
        if ($Hint) { $msg += " $Hint" }
        Write-Error $msg
    } else {
        Write-Verbose "✓ $Name found at $($cmd.Source)"
    }
}

Assert-Command "docker" "Docker Desktop or Docker CLI required."
Assert-Command "docker-compose" "Docker Compose required (or use `docker compose`)."
Assert-Command "ollama" "Ollama required. https://ollama.com"
Assert-Command "python" "Python ≥3.12 or uv. https://python.org or https://astral.sh/uv"
Assert-Command "node" "Node.js ≥20 required for the web UI. https://nodejs.org"

$python = (python --version 2>&1) -replace "Python ", ""
if ([version]$python -lt [version]"3.12") {
    Write-Error "Python ≥3.12 required; found $python"
}
Write-Verbose "✓ Python $python"

# Check nvidia-smi (GPU detection, required for resource gate)
$nvidia = Get-Command "nvidia-smi" -ErrorAction SilentlyContinue
if ($nvidia) {
    $gpuInfo = & nvidia-smi --query-gpu=name --format=csv,noheader,nounits 2>$null
    Write-Verbose "✓ NVIDIA GPU detected: $gpuInfo"
} else {
    Write-Warning "nvidia-smi not found. Continuing anyway, but GPU allocation will fail at runtime."
}

# ============================================================================
# 2. Create .env if missing
# ============================================================================
Write-Host "`n[2/10] Preparing .env..." -ForegroundColor Cyan

if (-not (Test-Path $envFile)) {
    if (-not (Test-Path $envExample)) {
        Write-Error ".env.example not found at $envExample"
    }
    Copy-Item $envExample $envFile
    Write-Verbose "Created $envFile from .env.example"
} else {
    Write-Verbose "$envFile already exists"
}

# Generate SEARXNG_SECRET if not set
$envContent = Get-Content $envFile -Raw
if ($envContent -match "SEARXNG_SECRET=change-me-local-only") {
    $secret = -join ((1..32) | ForEach-Object { "{0:x}" -f (Get-Random -Min 0 -Max 15) })
    $envContent = $envContent -replace "SEARXNG_SECRET=change-me-local-only", "SEARXNG_SECRET=$secret"
    Set-Content -Path $envFile -Value $envContent -NoNewline
    Write-Verbose "Generated SEARXNG_SECRET: $secret"
} else {
    Write-Verbose "SEARXNG_SECRET already configured"
}

# ============================================================================
# 3. Build docker images
# ============================================================================
if (-not $SkipBuild) {
    Write-Host "`n[3/10] Building docker images..." -ForegroundColor Cyan
    Push-Location $repoRoot
    & docker compose build postgres agent fetcher web
    if ($LASTEXITCODE -ne 0) {
        Write-Error "docker compose build failed"
    }
    Pop-Location
    Write-Verbose "✓ Docker images built"
} else {
    Write-Host "`n[3/10] Skipping docker build (-SkipBuild)" -ForegroundColor Yellow
}

# ============================================================================
# 4. Start services (postgres, searxng, ntfy)
# ============================================================================
Write-Host "`n[4/10] Starting services..." -ForegroundColor Cyan
Push-Location $repoRoot

# Load .env for docker compose
$env:COMPOSE_FILE = "docker-compose.yml"

& docker compose up -d postgres searxng ntfy
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker compose up failed"
}
Write-Verbose "✓ Services started (postgres, searxng, ntfy)"

# ============================================================================
# 5. Wait for postgres health
# ============================================================================
Write-Host "`n[5/10] Waiting for postgres to be healthy..." -ForegroundColor Cyan

$maxWait = 60
$waited = 0
while ($waited -lt $maxWait) {
    $health = & docker compose exec -T postgres pg_isready -U eoa -d eoanalyst 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Verbose "✓ Postgres is healthy"
        break
    }
    Start-Sleep -Seconds 2
    $waited += 2
}

if ($waited -ge $maxWait) {
    Write-Error "Postgres did not become healthy after $maxWait seconds"
}

Pop-Location

# ============================================================================
# 6. Run migrations (prefer host python, fall back to container)
# ============================================================================
Write-Host "`n[6/10] Running database migrations..." -ForegroundColor Cyan

Push-Location $repoRoot

# Try host python first
$migrateViaHost = $false
try {
    $dbUrl = Get-Content $envFile | Select-String "POSTGRES_PASSWORD" | ForEach-Object {
        $pwd = $_ -replace "POSTGRES_PASSWORD=", ""
        "postgresql://eoa:${pwd}@127.0.0.1:5433/eoanalyst"
    }

    if ($dbUrl) {
        $env:DATABASE_URL = $dbUrl
        & python -m alembic upgrade head 2>&1 | ForEach-Object { Write-Verbose $_ }
        if ($LASTEXITCODE -eq 0) {
            $migrateViaHost = $true
            Write-Verbose "✓ Migrations completed via host python"
        }
    }
} catch {
    Write-Verbose "Host python migration failed, will try container: $_"
}

# Fall back to container if needed
if (-not $migrateViaHost) {
    Write-Verbose "Running migrations via docker container..."
    & docker compose run --rm agent python -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Alembic migrations failed"
    }
    Write-Verbose "✓ Migrations completed via container"
}

Pop-Location

# ============================================================================
# 7. Apply graph_init.sql
# ============================================================================
Write-Host "`n[7/10] Initializing knowledge graph..." -ForegroundColor Cyan

Push-Location $repoRoot

$graphInit = Join-Path $repoRoot "db" "graph_init.sql"
if (-not (Test-Path $graphInit)) {
    Write-Error "db/graph_init.sql not found"
}

& docker compose exec -T postgres psql -U eoa -d eoanalyst -f - <  $graphInit
if ($LASTEXITCODE -ne 0) {
    Write-Error "Graph initialization failed"
}
Write-Verbose "✓ Knowledge graph initialized"

Pop-Location

# ============================================================================
# 8. Run seed
# ============================================================================
Write-Host "`n[8/10] Seeding database (watchlist, conferences, entities)..." -ForegroundColor Cyan

Push-Location $repoRoot

# Try host python first
$seedViaHost = $false
try {
    & python db/seed/seed_watchlist.py 2>&1 | ForEach-Object { Write-Verbose $_ }
    if ($LASTEXITCODE -eq 0) {
        $seedViaHost = $true
        Write-Verbose "✓ Seed completed via host python"
    }
} catch {
    Write-Verbose "Host seed failed, will try container: $_"
}

# Fall back to container
if (-not $seedViaHost) {
    Write-Verbose "Running seed via docker container..."
    & docker compose run --rm agent python db/seed/seed_watchlist.py
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Seed script failed"
    }
    Write-Verbose "✓ Seed completed via container"
}

Pop-Location

# ============================================================================
# 9. Pull Ollama models
# ============================================================================
if (-not $SkipModels) {
    Write-Host "`n[9/10] Pulling Ollama models..." -ForegroundColor Cyan

    Push-Location $repoRoot

    # Parse config/models.yaml and config/config.yaml to determine which models to pull
    $modelsCfg = Get-Content (Join-Path $repoRoot "config" "models.yaml") -Raw | ConvertFrom-Yaml
    $cfg = Get-Content (Join-Path $repoRoot "config" "config.yaml") -Raw | ConvertFrom-Yaml

    # Gather model keys referenced in config.models
    $modelsToFetch = @()
    foreach ($role in @("resident", "light", "hebrew_editor", "heavy_investigator", "embed", "guard_l1", "guard_l2")) {
        $modelKey = $cfg.models.$role
        if ($modelKey -and $modelKey -ne "null") {
            if ($modelKey -notin $modelsToFetch) {
                $modelsToFetch += $modelKey
            }
        }
    }

    Write-Verbose "Models to fetch: $($modelsToFetch -join ', ')"

    foreach ($modelKey in $modelsToFetch) {
        $modelSpec = $modelsCfg.models.$modelKey
        if ($modelSpec) {
            $ollamaName = $modelSpec.ollama
            if ($ollamaName) {
                Write-Verbose "Pulling $ollamaName (key: $modelKey)..."
                & ollama pull $ollamaName 2>&1 | ForEach-Object { Write-Verbose $_ }
                if ($LASTEXITCODE -ne 0) {
                    Write-Warning "Failed to pull $ollamaName; continuing"
                } else {
                    Write-Verbose "✓ Pulled $ollamaName"
                }
            }
        }
    }

    Write-Verbose "✓ Model pulls completed"
    Pop-Location
} else {
    Write-Host "`n[9/10] Skipping Ollama model pulls (-SkipModels)" -ForegroundColor Yellow
}

# ============================================================================
# 10. Set Ollama environment variables (user-scope, ADR-002)
# ============================================================================
Write-Host "`n[10/10] Setting Ollama environment variables (user-scope)..." -ForegroundColor Cyan

$ollamaEnv = @{
    "OLLAMA_HOST"                = "0.0.0.0:11434"
    "OLLAMA_NO_CLOUD"           = "1"
    "OLLAMA_MAX_LOADED_MODELS"  = "1"
    "OLLAMA_NUM_PARALLEL"       = "1"
    "OLLAMA_FLASH_ATTENTION"    = "1"
    "OLLAMA_KV_CACHE_TYPE"      = "q8_0"
    "OLLAMA_GPU_OVERHEAD"       = "1258291200"
    "OLLAMA_KEEP_ALIVE"         = "30m"
}

foreach ($key in $ollamaEnv.Keys) {
    $val = $ollamaEnv[$key]
    try {
        [Environment]::SetEnvironmentVariable($key, $val, "User")
        Write-Verbose "Set $key=$val (user-scope)"
    } catch {
        Write-Warning "Failed to set $key: $_"
    }
}

Write-Verbose "✓ Ollama environment variables set (requires Ollama restart to take effect)"

# ============================================================================
# Next steps
# ============================================================================
Write-Host "`n" -ForegroundColor Cyan
Write-Host "✅ Setup complete!" -ForegroundColor Green
Write-Host "`nNext steps:" -ForegroundColor Cyan
Write-Host @"
1. [ELEVATED] Run the Windows Firewall script once (admin PowerShell):
   .\scripts\host\firewall_ollama.ps1

   This restricts Ollama to localhost (required by ADR-002 security posture).
   Skip this only for development on an isolated network.

2. [OPTIONAL] If you plan to use Claude Codex review features:
   .\scripts\host\codex_sandbox_setup.ps1
   (requires elevated PS; grants Windows Sandbox permission to Claude Code)

3. Restart Ollama to apply env vars (user-scope, ADR-002):
   - On Windows: restart the Ollama system tray app or
   - Services > Ollama > Restart

4. Start the agent and web UI:
   docker compose up -d agent web

   Then open http://127.0.0.1:8765 in your browser.

5. Check status:
   eo status

6. View orchestrator logs:
   docker compose logs -f agent

7. Run a manual test cycle:
   eo run daily --mode=eco

See docs/RUNBOOK.md for operational procedures.
"@

Write-Host "`nConfig files (read/edit as needed):" -ForegroundColor Cyan
Write-Host @"
- .env                       — database password, Ollama URL, etc.
- config/config.yaml        — schedule, thresholds, resource limits
- config/models.yaml        — Ollama model registry + vendor origins
- config/models.lock        — pinned model digests (auto-updated)
- config/watchlist.yaml     — companies, programs, conferences to track
- config/sources.yaml       — RSS feeds and HTML sources
"@

Write-Host "`n📚 Documentation:" -ForegroundColor Cyan
Write-Host @"
- docs/CONVENTIONS.md        — hard rules and engineering standards
- docs/MODULES.md            — module/API reference
- docs/RUNBOOK.md            — ops procedures (check logs, re-run stages, restore from backup)
- docs/adr/002-*.md          — Ollama + network isolation (ADR-002)
- תוכנית_פיתוח_מפורטת_v2.md  — full development plan (Hebrew)
"@
