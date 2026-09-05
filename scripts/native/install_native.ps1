#!/usr/bin/env pwsh
<#
.SYNOPSIS
EO-Analyst native (no-Docker) installer for Windows + PowerShell 7 (non-admin).

.DESCRIPTION
Idempotent one-time setup. Everything lands under `<repo>\runtime\` (gitignored) except a
single Task Scheduler entry (see scripts\native\register_autostart.ps1, run separately).

Steps:
  0. Create .env from .env.example if missing (same pattern as install.ps1); read POSTGRES_PASSWORD.
  1. Python 3.12: install `uv` via host pip if missing, `uv python install 3.12` into
     runtime\python, `uv venv .venv --python 3.12`, `uv pip install -e ".[guard-onnx]"`
     (+ `,dev` with -Dev).
  2. PostgreSQL 17 portable (EDB "binaries without installer" zip) into runtime\pgsql;
     initdb into runtime\pgdata; postgresql.conf overrides (port 5433 etc.); start it;
     CREATE DATABASE eoanalyst; alembic upgrade head; db\seed\seed_watchlist.py.
     (db\graph_init.sql is intentionally skipped -- Apache AGE is retired, see
     docs\PLAN_WINDOWS_NATIVE.md step 1a.)
  3. ntfy: download the Windows amd64 release zip into runtime\ntfy, verify its SHA256
     against the release's checksums.txt, write runtime\ntfy\server.yml.
  4. Guard model: huggingface_hub snapshot_download of the ONNX prompt-injection classifier
     into runtime\models\prompt-guard (mirrors docker\agent\Dockerfile's `guard-model` stage).
  5. Frontend: `npm ci` + `npm run build` in web\.
  6. Ollama: verify http://127.0.0.1:11434/api/version reachable and warn about any
     configured model role missing from `ollama list` (never installs/pulls anything).
  7. Write runtime\eoa.env (consumed by scripts\native\eoa-supervisor.ps1 and the CLI).

Every download step prints the exact filename, source URL, and an expected/observed size
BEFORE fetching, and verifies SHA256 when the upstream publishes one (ntfy does; the EDB
PostgreSQL zip does not -- noted at that step).

.PARAMETER SkipDownloads
Fail fast instead of downloading anything not already cached under runtime\downloads.

.PARAMETER SkipPostgres
Skip the entire PostgreSQL step (download/initdb/migrate/seed).

.PARAMETER SkipNtfy
Skip the ntfy download + server.yml step.

.PARAMETER SkipGuardModel
Skip the prompt-guard ONNX model download.

.PARAMETER SkipFrontend
Skip `npm ci && npm run build` in web\.

.PARAMETER Dev
Also install the `dev` extra (pytest, ruff, mypy, ...) into the venv.

.PARAMETER PgVersion
PostgreSQL 17.x minor version for the EDB binaries zip. Default: 17.6 (could not be
live-verified against https://www.enterprisedb.com/download-postgresql-binaries from this
session -- confirm/override before running with real downloads).

.PARAMETER NtfyVersion
ntfy release tag (without the leading "v"). Default: 2.11.0 -- likewise unverified against
https://github.com/binwiederhier/ntfy/releases from this session; check the latest release
and pass -NtfyVersion to override if newer.

.EXAMPLE
./scripts/native/install_native.ps1
./scripts/native/install_native.ps1 -Dev
./scripts/native/install_native.ps1 -SkipPostgres -SkipNtfy   # re-run just venv+frontend+guard

.NOTES
Non-admin. No system-wide installs. Nothing here touches Program Files or the registry
except `uv`'s user-site pip install (scripts land in the per-user Python Scripts dir).
#>

#Requires -Version 7

param(
    [switch] $SkipDownloads,
    [switch] $SkipPostgres,
    [switch] $SkipNtfy,
    [switch] $SkipGuardModel,
    [switch] $SkipFrontend,
    [switch] $Dev,
    [string] $PgVersion = "17.6",
    [string] $NtfyVersion = "2.11.0"
)

$ErrorActionPreference = "Stop"
$VerbosePreference = "Continue"

# scripts\native -> scripts -> repo root
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runtimeDir = Join-Path $repoRoot "runtime"
$envFile = Join-Path $repoRoot ".env"
$envExample = Join-Path $repoRoot ".env.example"

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

Write-Host "EO-Analyst native installer (Windows, PowerShell 7, no Docker)" -ForegroundColor Cyan
Write-Host "repo root: $repoRoot" -ForegroundColor DarkGray
Write-Host "runtime:   $runtimeDir" -ForegroundColor DarkGray

# ============================================================================
# Helpers
# ============================================================================

function Get-EnvValue {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Key)
    if (-not (Test-Path $Path)) { return $null }
    $line = Get-Content $Path | Where-Object { $_ -match "^\s*$([Regex]::Escape($Key))=" } | Select-Object -First 1
    if (-not $line) { return $null }
    return ($line -split "=", 2)[1].Trim()
}

function Announce-Download {
    param(
        [Parameter(Mandatory)][string]$FileName,
        [Parameter(Mandatory)][string]$Source,
        [Parameter(Mandatory)][string]$SizeNote
    )
    Write-Host ""
    Write-Host "About to download:" -ForegroundColor Yellow
    Write-Host "  file:   $FileName"
    Write-Host "  source: $Source"
    Write-Host "  size:   $SizeNote"
    if ($SkipDownloads) {
        Write-Error "-SkipDownloads was passed and '$FileName' is not already cached -- aborting."
    }
}

# ---- Minimal, dependency-free YAML scalar reader -----------------------------------------
# No PSYaml/powershell-yaml module is installed on this machine (verified via
# `Get-Module -ListAvailable powershell-yaml` -- nothing returned), so step 6 (Ollama model
# check) below reads the two narrow shapes config.yaml/models.yaml actually use with regex
# instead of adding a new module dependency. NOT a general YAML parser.
function Read-YamlNormalized {
    param([Parameter(Mandatory)][string]$Path)
    return (Get-Content $Path -Raw) -replace "`r`n", "`n"
}

function Get-YamlTopBlock {
    # Captures the indented lines directly under a top-level `Key:` mapping.
    param([Parameter(Mandatory)][string]$Text, [Parameter(Mandatory)][string]$Key)
    $pattern = "(?m)^$([Regex]::Escape($Key)):[ \t]*(?:#[^\n]*)?\n((?:^[ \t]+\S[^\n]*\n?)*)"
    if ($Text -match $pattern) { return $Matches[1] }
    return $null
}

function Get-YamlNestedBlock {
    # Within a 2-space-indented mapping block, captures one `  ItemKey:` entry's own
    # (4-space-or-deeper-indented) field lines.
    param([Parameter(Mandatory)][string]$Block, [Parameter(Mandatory)][string]$ItemKey)
    $pattern = "(?m)^[ \t]{2}$([Regex]::Escape($ItemKey)):[ \t]*(?:#[^\n]*)?\n((?:^[ \t]{4,}\S[^\n]*\n?)*)"
    if ($Block -match $pattern) { return $Matches[1] }
    return $null
}

function Get-YamlScalarField {
    # Reads a single `field: value` (optionally quoted, optional trailing comment) from a block.
    param([Parameter(Mandatory)][string]$Block, [Parameter(Mandatory)][string]$Field)
    $pattern = "(?m)^[ \t]*$([Regex]::Escape($Field)):[ \t]*[""']?([^""'#\n]+?)[""']?[ \t]*(?:#[^\n]*)?$"
    if ($Block -match $pattern) { return $Matches[1].Trim() }
    return $null
}

# ============================================================================
# Step 0: .env
# ============================================================================
Write-Host "`n[0/7] Preparing .env..." -ForegroundColor Cyan

if (-not (Test-Path $envFile)) {
    if (-not (Test-Path $envExample)) { Write-Error ".env.example not found at $envExample" }
    Copy-Item $envExample $envFile
    Write-Verbose "Created $envFile from .env.example"
} else {
    Write-Verbose "$envFile already exists"
}

$pgPassword = Get-EnvValue -Path $envFile -Key "POSTGRES_PASSWORD"
if (-not $pgPassword) { Write-Error "POSTGRES_PASSWORD missing from $envFile" }
if ($pgPassword -eq "change-me-local-only") {
    Write-Warning "POSTGRES_PASSWORD in .env is still the placeholder value. Consider changing it before running this in anything but a fully local dev box."
}

# ============================================================================
# Step 1: Python 3.12 (uv) + venv + package install
# ============================================================================
Write-Host "`n[1/7] Python 3.12 (uv) + venv + package install..." -ForegroundColor Cyan

$uvPythonDir = Join-Path $runtimeDir "python"
$venvDir = Join-Path $repoRoot ".venv"
New-Item -ItemType Directory -Force -Path $uvPythonDir | Out-Null

$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    Write-Verbose "uv not found on PATH; installing via host pip (python -m pip install --user uv)"
    & python -m pip install --user uv
    if ($LASTEXITCODE -ne 0) { Write-Error "python -m pip install --user uv failed" }
    $userScripts = (& python -m site --user-base) + "\Scripts"
    if (Test-Path $userScripts) { $env:PATH = "$userScripts;$env:PATH" }
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uvCmd) {
        Write-Error "uv was installed but is still not on PATH. Open a new shell (so PATH picks up $userScripts) and re-run this script, or add that directory to PATH yourself."
    }
}
Write-Verbose "uv: $($uvCmd.Source)"

$env:UV_PYTHON_INSTALL_DIR = $uvPythonDir
Write-Verbose "uv python install 3.12 (UV_PYTHON_INSTALL_DIR=$uvPythonDir)"
& uv python install 3.12
if ($LASTEXITCODE -ne 0) { Write-Error "uv python install 3.12 failed" }

if (-not (Test-Path $venvDir)) {
    Write-Verbose "uv venv $venvDir --python 3.12"
    & uv venv $venvDir --python 3.12
    if ($LASTEXITCODE -ne 0) { Write-Error "uv venv failed" }
} else {
    Write-Verbose "$venvDir already exists; leaving it in place"
}

$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) { Write-Error "venv python not found at $venvPython" }

$extras = if ($Dev) { ".[guard-onnx,dev]" } else { ".[guard-onnx]" }
Write-Host "  Decision: 'uv pip install --python <venv> -e $extras' rather than 'uv sync'." -ForegroundColor DarkGray
Write-Host "  Reason: no uv.lock is committed to this repo, and docker\agent\Dockerfile already" -ForegroundColor DarkGray
Write-Host "  installs the exact same way ('uv pip install --system -e `".[guard-onnx]`"'), so the" -ForegroundColor DarkGray
Write-Host "  native venv and the (retiring) container image resolve dependencies identically." -ForegroundColor DarkGray
Push-Location $repoRoot
try {
    & uv pip install --python $venvPython -e $extras
    if ($LASTEXITCODE -ne 0) { Write-Error "uv pip install -e $extras failed" }
} finally {
    Pop-Location
}
Write-Verbose "Python environment ready: $venvPython"

# ============================================================================
# Step 2: PostgreSQL 17 portable
# ============================================================================
if ($SkipPostgres) {
    Write-Host "`n[2/7] Skipping PostgreSQL (-SkipPostgres)" -ForegroundColor Yellow
} else {
    Write-Host "`n[2/7] PostgreSQL $PgVersion (portable, no installer)..." -ForegroundColor Cyan

    $pgRoot = Join-Path $runtimeDir "pgsql"
    $pgData = Join-Path $runtimeDir "pgdata"
    $downloadsDir = Join-Path $runtimeDir "downloads"
    $pgLogDir = Join-Path $runtimeDir "logs\pg"
    New-Item -ItemType Directory -Force -Path $downloadsDir, $pgLogDir | Out-Null

    $pgCtl = Join-Path $pgRoot "bin\pg_ctl.exe"
    $initdb = Join-Path $pgRoot "bin\initdb.exe"
    $psql = Join-Path $pgRoot "bin\psql.exe"

    if (-not (Test-Path (Join-Path $pgRoot "bin\postgres.exe"))) {
        $zipName = "postgresql-$PgVersion-1-windows-x64-binaries.zip"
        $zipUrl = "https://get.enterprisedb.com/postgresql/$zipName"
        $zipPath = Join-Path $downloadsDir $zipName

        if (-not (Test-Path $zipPath)) {
            Announce-Download -FileName $zipName -Source $zipUrl -SizeNote "~330-360 MB (EDB publishes no manifest/SHA256 for this zip -- see warning below)"
            Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
            $sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
            Write-Verbose "Downloaded $zipPath ($sizeMb MB)"
            Write-Warning "EDB does not publish a SHA256/manifest for the 'binaries without installer' zip -- integrity here rests on TLS transport + a successful HTTP 200 only; no hash verification was possible."
        } else {
            Write-Verbose "Reusing cached $zipPath"
        }

        Write-Verbose "Expanding $zipPath -> $runtimeDir (the zip has a top-level pgsql\ folder)"
        Expand-Archive -Path $zipPath -DestinationPath $runtimeDir -Force
        if (-not (Test-Path (Join-Path $pgRoot "bin\postgres.exe"))) {
            Write-Error "postgres.exe not found under $pgRoot after extraction -- unexpected zip layout"
        }
    } else {
        Write-Verbose "$pgRoot already has PostgreSQL binaries"
    }

    if (-not (Test-Path (Join-Path $pgData "PG_VERSION"))) {
        Write-Verbose "Running initdb -D $pgData -U eoa"
        $pwFile = Join-Path ([System.IO.Path]::GetTempPath()) "eoa_pg_pw_$([guid]::NewGuid().ToString('N')).txt"
        Set-Content -Path $pwFile -Value $pgPassword -NoNewline -Encoding ascii
        try {
            & $initdb -D $pgData -U eoa --auth=scram-sha-256 --pwfile=$pwFile -E UTF8 --locale=C
            if ($LASTEXITCODE -ne 0) { Write-Error "initdb failed" }
        } finally {
            Remove-Item $pwFile -Force -ErrorAction SilentlyContinue
        }

        $confPath = Join-Path $pgData "postgresql.conf"
        $pgLogDirForward = $pgLogDir -replace '\\', '/'
        Add-Content -Path $confPath -Value @"

# --- EO-Analyst native overrides (scripts\native\install_native.ps1) ---
port = 5433
listen_addresses = '127.0.0.1'
timezone = 'Asia/Jerusalem'
shared_buffers = 512MB
max_connections = 60
log_destination = 'stderr'
logging_collector = on
log_directory = '$pgLogDirForward'
"@
        Write-Verbose "Wrote postgresql.conf overrides (port 5433, Asia/Jerusalem, logging -> $pgLogDir)"
    } else {
        Write-Verbose "$pgData already initialized (PG_VERSION present); leaving initdb alone"
    }

    # --- Idempotent from here: (re-)start, ensure DB exists, migrate, seed ---
    $statusResult = & $pgCtl -D $pgData status 2>&1
    $isRunning = $LASTEXITCODE -eq 0
    if (-not $isRunning) {
        Write-Verbose "Starting postgres (pg_ctl -D $pgData -w start)"
        & $pgCtl -D $pgData -l (Join-Path $pgLogDir "startup.log") -w start
        if ($LASTEXITCODE -ne 0) { Write-Error "pg_ctl start failed -- see $pgLogDir\startup.log" }
    } else {
        Write-Verbose "postgres already running"
    }

    $env:PGPASSWORD = $pgPassword
    $dbExists = & $psql -U eoa -h 127.0.0.1 -p 5433 -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='eoanalyst'" 2>$null
    if ($dbExists -notmatch "1") {
        Write-Verbose "Creating database eoanalyst (owner eoa)"
        & $psql -U eoa -h 127.0.0.1 -p 5433 -d postgres -c "CREATE DATABASE eoanalyst OWNER eoa;" | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Error "CREATE DATABASE eoanalyst failed" }
    } else {
        Write-Verbose "database eoanalyst already exists"
    }

    $env:DATABASE_URL = "postgresql://eoa:$pgPassword@127.0.0.1:5433/eoanalyst"
    Push-Location $repoRoot
    try {
        Write-Verbose "alembic upgrade head"
        & $venvPython -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { Write-Error "alembic upgrade head failed" }

        Write-Verbose "db\seed\seed_watchlist.py"
        & $venvPython (Join-Path $repoRoot "db\seed\seed_watchlist.py")
        if ($LASTEXITCODE -ne 0) { Write-Error "seed_watchlist.py failed" }
    } finally {
        Pop-Location
        Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    }
    Write-Verbose "db\graph_init.sql intentionally skipped -- Apache AGE is retired (docs\PLAN_WINDOWS_NATIVE.md step 1a; graph_edges table comes from an alembic migration instead)."
    Write-Verbose "PostgreSQL ready on 127.0.0.1:5433/eoanalyst"
}

# ============================================================================
# Step 3: ntfy
# ============================================================================
if ($SkipNtfy) {
    Write-Host "`n[3/7] Skipping ntfy (-SkipNtfy)" -ForegroundColor Yellow
} else {
    Write-Host "`n[3/7] ntfy $NtfyVersion..." -ForegroundColor Cyan

    $ntfyDir = Join-Path $runtimeDir "ntfy"
    $downloadsDir = Join-Path $runtimeDir "downloads"
    New-Item -ItemType Directory -Force -Path $ntfyDir, $downloadsDir | Out-Null
    $ntfyExe = Join-Path $ntfyDir "ntfy.exe"

    if (-not (Test-Path $ntfyExe)) {
        $zipName = "ntfy_$($NtfyVersion)_windows_amd64.zip"
        $zipUrl = "https://github.com/binwiederhier/ntfy/releases/download/v$NtfyVersion/$zipName"
        $checksumsUrl = "https://github.com/binwiederhier/ntfy/releases/download/v$NtfyVersion/checksums.txt"
        $zipPath = Join-Path $downloadsDir $zipName

        if (-not (Test-Path $zipPath)) {
            Announce-Download -FileName $zipName -Source $zipUrl -SizeNote "~10-15 MB (single static Go binary; exact size varies per release)"
            Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
            $sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
            Write-Verbose "Downloaded $zipPath ($sizeMb MB)"
        } else {
            Write-Verbose "Reusing cached $zipPath"
        }

        $checksumsPath = Join-Path $downloadsDir "ntfy_$($NtfyVersion)_checksums.txt"
        try {
            if (-not $SkipDownloads -or (Test-Path $checksumsPath)) {
                if (-not (Test-Path $checksumsPath)) {
                    Write-Verbose "Fetching $checksumsUrl for SHA256 verification"
                    Invoke-WebRequest -Uri $checksumsUrl -OutFile $checksumsPath
                }
                $checksumLine = Get-Content $checksumsPath | Where-Object { $_ -match [Regex]::Escape($zipName) } | Select-Object -First 1
                if ($checksumLine) {
                    $expectedHash = ($checksumLine -split '\s+')[0].ToLower()
                    $actualHash = (Get-FileHash -Path $zipPath -Algorithm SHA256).Hash.ToLower()
                    if ($expectedHash -ne $actualHash) {
                        Write-Error "ntfy zip SHA256 mismatch for ${zipName}: expected $expectedHash, got $actualHash"
                    }
                    Write-Verbose "SHA256 verified for $zipName ($actualHash)"
                } else {
                    Write-Warning "checksums.txt did not list an entry for $zipName -- skipping hash verification"
                }
            }
        } catch {
            Write-Warning "Could not fetch/verify ntfy checksums.txt: $_"
        }

        $extractDir = Join-Path $downloadsDir "ntfy_extract"
        Write-Verbose "Expanding $zipPath -> $extractDir"
        Expand-Archive -Path $zipPath -DestinationPath $extractDir -Force
        $foundExe = Get-ChildItem -Path $extractDir -Filter "ntfy.exe" -Recurse | Select-Object -First 1
        if (-not $foundExe) { Write-Error "ntfy.exe not found inside $zipName" }
        Copy-Item $foundExe.FullName $ntfyExe -Force
        Write-Verbose "Installed $ntfyExe"
    } else {
        Write-Verbose "$ntfyExe already present"
    }

    $attachDir = Join-Path $ntfyDir "attachments"
    New-Item -ItemType Directory -Force -Path $attachDir | Out-Null
    $cacheFile = (Join-Path $ntfyDir "cache.db") -replace '\\', '/'
    $attachDirForward = $attachDir -replace '\\', '/'
    $serverYml = Join-Path $ntfyDir "server.yml"
    @"
base-url: http://127.0.0.1:8090
# Bound to 0.0.0.0 (not 127.0.0.1): the phone subscribes over Tailscale at
# http://100.70.157.25:8090/eo-analyst (see docs\adr\003-agent-fetcher-bridge-and-notify-relay.md
# and docs\adr\004-windows-native.md). Windows Firewall's default "private network" inbound
# rules still gate who on the LAN/Tailscale interface can actually reach this port.
listen-http: "0.0.0.0:8090"
cache-file: $cacheFile
attachment-cache-dir: $attachDirForward
behind-proxy: false
"@ | Set-Content -Path $serverYml -Encoding utf8
    Write-Verbose "Wrote $serverYml"
}

# ============================================================================
# Step 4: Guard model (ONNX prompt-injection classifier)
# ============================================================================
if ($SkipGuardModel) {
    Write-Host "`n[4/7] Skipping guard model (-SkipGuardModel)" -ForegroundColor Yellow
} else {
    Write-Host "`n[4/7] Prompt-guard ONNX model..." -ForegroundColor Cyan

    $modelDir = Join-Path $runtimeDir "models\prompt-guard"
    if (Test-Path (Join-Path $modelDir "model.onnx")) {
        Write-Verbose "$modelDir already has model.onnx; skipping"
    } else {
        New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
        Announce-Download -FileName "protectai/deberta-v3-base-prompt-injection-v2 (onnx/ subfolder)" `
            -Source "https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2" `
            -SizeNote "~370 MB (model.onnx + tokenizer files; via huggingface_hub, not a single direct URL -- mirrors docker\agent\Dockerfile's 'guard-model' build stage)"

        $guardScript = @'
import os
import shutil
import subprocess
import sys

from huggingface_hub import list_repo_files, snapshot_download

MODEL_ID = "protectai/deberta-v3-base-prompt-injection-v2"
DEST = sys.argv[1]
os.makedirs(DEST, exist_ok=True)

files = list_repo_files(MODEL_ID)
if any(f.startswith("onnx/") for f in files):
    print(f"[guard-model] {MODEL_ID}: onnx/ folder present -- downloading it")
    tmp = os.path.join(DEST, "_hf_dl")
    snapshot_download(MODEL_ID, allow_patterns=["onnx/*"], local_dir=tmp)
    onnx_dir = os.path.join(tmp, "onnx")
    for name in os.listdir(onnx_dir):
        shutil.move(os.path.join(onnx_dir, name), os.path.join(DEST, name))
    shutil.rmtree(tmp, ignore_errors=True)
else:
    print(f"[guard-model] {MODEL_ID}: no onnx/ folder on the hub -- exporting locally")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-cache-dir", "optimum[exporters]>=1.21"],
        check=True,
    )
    subprocess.run(
        ["optimum-cli", "export", "onnx", "--model", MODEL_ID, "--task", "text-classification", DEST],
        check=True,
    )

assert os.path.isfile(os.path.join(DEST, "model.onnx")), f"guard model install failed: no model.onnx in {DEST}"
print("[guard-model] done:", sorted(os.listdir(DEST)))
'@
        $scriptPath = Join-Path ([System.IO.Path]::GetTempPath()) "eoa_guard_model_install_$([guid]::NewGuid().ToString('N')).py"
        Set-Content -Path $scriptPath -Value $guardScript -Encoding utf8
        try {
            & $venvPython $scriptPath $modelDir
            if ($LASTEXITCODE -ne 0) { Write-Error "Guard model install failed" }
        } finally {
            Remove-Item $scriptPath -Force -ErrorAction SilentlyContinue
        }
        Write-Verbose "Guard model ready at $modelDir"
    }
}

# ============================================================================
# Step 5: Frontend
# ============================================================================
if ($SkipFrontend) {
    Write-Host "`n[5/7] Skipping frontend build (-SkipFrontend)" -ForegroundColor Yellow
} else {
    Write-Host "`n[5/7] Frontend (npm ci && npm run build)..." -ForegroundColor Cyan
    Push-Location (Join-Path $repoRoot "web")
    try {
        & npm ci
        if ($LASTEXITCODE -ne 0) { Write-Error "npm ci failed" }
        & npm run build
        if ($LASTEXITCODE -ne 0) { Write-Error "npm run build failed" }
    } finally {
        Pop-Location
    }
    Write-Verbose "Frontend built -> web\dist (served by FastAPI's StaticFiles mount)"
}

# ============================================================================
# Step 6: Ollama check (verify only -- never installs/pulls)
# ============================================================================
Write-Host "`n[6/7] Ollama check..." -ForegroundColor Cyan

$ollamaReachable = $false
try {
    $verResp = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/version" -TimeoutSec 5
    Write-Verbose "Ollama reachable: version $($verResp.version)"
    $ollamaReachable = $true
} catch {
    Write-Warning "Ollama not reachable at http://127.0.0.1:11434 -- is it running? ($_)"
}

if ($ollamaReachable) {
    try {
        $have = @{}
        (Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 5).models | ForEach-Object { $have[$_.name] = $true }

        $cfgText = Read-YamlNormalized (Join-Path $repoRoot "config\config.yaml")
        $registryText = Read-YamlNormalized (Join-Path $repoRoot "config\models.yaml")
        $cfgModelsBlock = Get-YamlTopBlock -Text $cfgText -Key "models"
        $registryModelsBlock = Get-YamlTopBlock -Text $registryText -Key "models"

        $roles = @("resident", "investigator", "light", "hebrew_editor", "heavy_investigator", "embed", "guard_l1", "guard_l2")
        foreach ($role in $roles) {
            $key = Get-YamlScalarField -Block $cfgModelsBlock -Field $role
            if (-not $key -or $key -eq "null") { continue }
            $itemBlock = Get-YamlNestedBlock -Block $registryModelsBlock -ItemKey $key
            if (-not $itemBlock) {
                Write-Warning "role '$role' -> key '$key' not found in config\models.yaml"
                continue
            }
            $ollamaName = Get-YamlScalarField -Block $itemBlock -Field "ollama"
            if (-not $ollamaName) { continue }  # e.g. guard_l1 runs via ONNX, not Ollama
            if ($have.ContainsKey($ollamaName)) {
                Write-Verbose "OK: $role -> $ollamaName present"
            } else {
                Write-Warning "$role -> $ollamaName NOT found in 'ollama list' (pull manually: ollama pull $ollamaName)"
            }
        }
    } catch {
        Write-Warning "Could not check configured models against 'ollama list': $_"
    }
}

# ============================================================================
# Step 7: runtime\eoa.env
# ============================================================================
Write-Host "`n[7/7] Writing runtime\eoa.env..." -ForegroundColor Cyan

$guardDir = (Join-Path $runtimeDir "models\prompt-guard") -replace '\\', '/'
$eoaEnvPath = Join-Path $runtimeDir "eoa.env"
@"
DATABASE_URL=postgresql://eoa:$pgPassword@127.0.0.1:5433/eoanalyst
EOA_ROLE=host
OLLAMA_URL=http://127.0.0.1:11434
NTFY_URL=http://127.0.0.1:8090
NTFY_TOPIC=eo-analyst
EOA_GUARD_L1_DIR=$guardDir
HF_HUB_OFFLINE=1
TZ=Asia/Jerusalem
PYTHONUTF8=1
EOA_ROOT=$repoRoot
"@ | Set-Content -Path $eoaEnvPath -Encoding utf8
Write-Verbose "Wrote $eoaEnvPath"

# ============================================================================
# Summary
# ============================================================================
Write-Host "`n" -ForegroundColor Cyan
Write-Host "Native install complete." -ForegroundColor Green
Write-Host @"

Next steps:
  1. Review runtime\eoa.env (created above).
  2. Start everything:            eo native start
     (or directly:                pwsh -File scripts\native\eoa-supervisor.ps1)
  3. Check status:                eo native status
  4. Register autostart (once):   pwsh -File scripts\native\register_autostart.ps1
  5. Migrate data from Docker (one-time, if applicable):
                                   pwsh -File scripts\native\migrate_from_docker.ps1 -DryRun

Files/dirs created under runtime\ (gitignored):
  runtime\python\           uv-managed CPython 3.12
  runtime\pgsql\, pgdata\   PostgreSQL 17 binaries + cluster (port 5433)
  runtime\ntfy\             ntfy.exe + server.yml (port 8090, listens on 0.0.0.0 for Tailscale)
  runtime\models\prompt-guard\  ONNX prompt-injection classifier
  runtime\eoa.env           env vars for the supervisor / eo CLI
  runtime\logs\             per-service logs (written by the supervisor)

See docs\adr\004-windows-native.md for the full rationale and security posture.
"@
