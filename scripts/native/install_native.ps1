#!/usr/bin/env pwsh
<#
.SYNOPSIS
EO-Analyst native (no-Docker) installer for Windows + PowerShell 7 (non-admin).

.DESCRIPTION
Idempotent one-time setup. Everything lands under `<repo>\runtime\` (gitignored) except a
single Task Scheduler entry (see scripts\native\register_autostart.ps1, run separately).

Steps:
  0. Create .env from .env.example if missing (same pattern as install.ps1); read POSTGRES_PASSWORD.
  1. Python (host, >=3.12, no download): resolve the newest suitable interpreter already on
     this machine -- tries `py -3.14`, `py -3.13`, `py -3.12` via the Windows `py` launcher,
     then falls back to whatever `python` resolves to on PATH (pyproject.toml's
     `requires-python = ">=3.12"` is enforced either way) -- `python -m venv .venv`, then
     `.venv\Scripts\python -m pip install -e ".[guard-onnx]"` (+ `,dev` with -Dev). No `uv`,
     no python.org download: this repo runs on whatever Python 3.12+ the host already has
     (verified 2026-09-05 against the host's Python 3.14 install).
  2. PostgreSQL 17 portable (EDB "binaries without installer" zip) into runtime\pgsql;
     initdb into runtime\pgdata; postgresql.conf overrides (port 5432 -- the native port;
     5433 was only ever the Docker Compose host-mapping, now retired); start it;
     CREATE DATABASE eoanalyst; alembic upgrade head; db\seed\seed_watchlist.py. The unused
     pgAdmin 4 / StackBuilder / symbols folders that ship inside the zip are deleted after
     extraction (~300 MB saved; nothing under runtime\pgsql\bin is touched).
     (db\graph_init.sql is intentionally skipped -- Apache AGE is retired, see
     docs\PLAN_WINDOWS_NATIVE.md step 1a.)
  3. ntfy: download the Windows amd64 release zip into runtime\ntfy, verify its SHA256
     against the release's checksums.txt, flatten the zip's nested
     `ntfy_<version>_windows_amd64\ntfy.exe` into runtime\ntfy\ntfy.exe, write
     runtime\ntfy\server.yml (deliberately WITHOUT `attachment-cache-dir` -- see step 3 body).
  4. Guard model: try `docker cp eoa-agent:/opt/models/prompt-guard runtime\models\prompt-guard`
     first, if a docker container literally named `eoa-agent` exists (its filesystem already
     has the baked-in ONNX model from docker\agent\Dockerfile's `guard-model` build stage --
     `docker cp` reads a container's filesystem and works whether it's running or stopped; it
     does not start, stop, or otherwise touch the container's run state). Falls back to a
     huggingface_hub snapshot_download of the ONNX prompt-injection classifier only when no
     such container is found or the copy fails.
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
Skip the prompt-guard ONNX model install (docker cp or Hugging Face download).

.PARAMETER SkipFrontend
Skip `npm ci && npm run build` in web\.

.PARAMETER Dev
Also install the `dev` extra (pytest, ruff, mypy, ...) into the venv.

.PARAMETER DryRun
Print every action this script would take (downloads, extractions, file writes, subprocess
calls) and exit without changing anything on disk or in any running service. Safe to run at
any time to preview what a real invocation would do; combine with -Dev / -Skip* to preview
a specific configuration.

.PARAMETER PgVersion
PostgreSQL 17.x minor version for the EDB binaries zip. Default: 17.9 -- this is the exact
version installed and verified working on this machine on 2026-09-05 (zip size 334,313,473
bytes from https://get.enterprisedb.com/postgresql/postgresql-17.9-1-windows-x64-binaries.zip).

.PARAMETER NtfyVersion
ntfy release tag (without the leading "v"). Default: 2.28.0 -- the exact version installed
and verified working on this machine on 2026-09-05, downloaded from
https://github.com/binwiederhier/ntfy/releases/download/v2.28.0/ntfy_2.28.0_windows_amd64.zip
(sha256 fa49abd3462a588e1555701d360de84f192a45764a97054c4cafb486ea3cdf78, verified against
that release's checksums.txt).

.EXAMPLE
./scripts/native/install_native.ps1
./scripts/native/install_native.ps1 -DryRun
./scripts/native/install_native.ps1 -Dev
./scripts/native/install_native.ps1 -SkipPostgres -SkipNtfy   # re-run just venv+frontend+guard

.NOTES
Non-admin. No system-wide installs. Nothing here touches Program Files or the registry.
Does not start, stop, or otherwise manage the live stack (postgres/ntfy/orchestrator/api) --
that is scripts\native\eoa-supervisor.ps1's job, run separately via `eo native start`.
#>

#Requires -Version 7

param(
    [switch] $SkipDownloads,
    [switch] $SkipPostgres,
    [switch] $SkipNtfy,
    [switch] $SkipGuardModel,
    [switch] $SkipFrontend,
    [switch] $Dev,
    [switch] $DryRun,
    [string] $PgVersion = "17.9",
    [string] $NtfyVersion = "2.28.0"
)

$ErrorActionPreference = "Stop"
$VerbosePreference = "Continue"

# scripts\native -> scripts -> repo root
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runtimeDir = Join-Path $repoRoot "runtime"
$envFile = Join-Path $repoRoot ".env"
$envExample = Join-Path $repoRoot ".env.example"

function New-DirIfNeeded {
    # All directory creation goes through here so -DryRun genuinely changes nothing on disk.
    param([Parameter(Mandatory)][string[]] $Path)
    if ($DryRun) { return }
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

New-DirIfNeeded -Path $runtimeDir

Write-Host "EO-Analyst native installer (Windows, PowerShell 7, no Docker)" -ForegroundColor Cyan
Write-Host "repo root: $repoRoot" -ForegroundColor DarkGray
Write-Host "runtime:   $runtimeDir" -ForegroundColor DarkGray
if ($DryRun) {
    Write-Host "-DryRun: no files will be created/modified/deleted and no subprocess with side effects will run." -ForegroundColor Yellow
}

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

function Write-DryRun {
    param([Parameter(Mandatory)][string]$Message)
    Write-Host "  [DryRun] would $Message" -ForegroundColor Yellow
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
    if ($DryRun) {
        Write-DryRun "create $envFile from $envExample"
    } else {
        if (-not (Test-Path $envExample)) { Write-Error ".env.example not found at $envExample" }
        Copy-Item $envExample $envFile
        Write-Verbose "Created $envFile from .env.example"
    }
} else {
    Write-Verbose "$envFile already exists"
}

$pgPassword = Get-EnvValue -Path $envFile -Key "POSTGRES_PASSWORD"
if (-not $pgPassword) {
    if ($DryRun) {
        Write-Warning "POSTGRES_PASSWORD not readable yet (likely because .env doesn't exist yet and -DryRun skipped creating it) -- using a placeholder for the rest of this dry run."
        $pgPassword = "<POSTGRES_PASSWORD>"
    } else {
        Write-Error "POSTGRES_PASSWORD missing from $envFile"
    }
}
if ($pgPassword -eq "change-me-local-only") {
    Write-Warning "POSTGRES_PASSWORD in .env is still the placeholder value. Consider changing it before running this in anything but a fully local dev box."
}

# ============================================================================
# Step 1: Python (host, >=3.12) + venv + package install
# ============================================================================
Write-Host "`n[1/7] Python (host, >=3.12) + venv + package install..." -ForegroundColor Cyan

function Find-HostPython {
    <#
    Resolves the newest suitable Python already installed on this machine. No download, no
    `uv`: this project runs fine on whatever Python >=3.12 the host provides (verified against
    the host's Python 3.14 install on 2026-09-05 -- all wheels for
    ".[guard-onnx,dev]" installed cleanly, including onnxruntime, torch-cpu, numpy,
    psycopg-binary, lxml, ddgs).
    #>
    $tried = New-Object System.Collections.Generic.List[string]

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($ver in @("3.14", "3.13", "3.12")) {
            $tried.Add("py -$ver") | Out-Null
            & py "-$ver" -c "import sys" *> $null
            if ($LASTEXITCODE -eq 0) {
                return [pscustomobject]@{ Exe = "py"; Args = @("-$ver") }
            }
        }
    }

    $pythonCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCmd) {
        $tried.Add("python (PATH: $($pythonCmd.Source))") | Out-Null
        $verOut = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and $verOut) {
            $parts = $verOut.Trim().Split(".")
            $maj = [int]$parts[0]; $min = [int]$parts[1]
            if ($maj -gt 3 -or ($maj -eq 3 -and $min -ge 12)) {
                return [pscustomobject]@{ Exe = "python"; Args = @() }
            }
            Write-Warning "python on PATH is $($verOut.Trim()), which is below the pyproject.toml floor of >=3.12"
        }
    }

    Write-Error "No Python >=3.12 found (tried: $($tried -join ', ')). Install Python 3.12+ (e.g. from python.org or the Microsoft Store) and ensure it's on PATH or reachable via the 'py' launcher, then re-run this script."
}

$hostPython = Find-HostPython
$hostPythonArgsDisplay = ($hostPython.Args -join " ")
$hostPythonVersion = (& $hostPython.Exe @($hostPython.Args) -c "import sys; print(sys.version.split()[0])").Trim()
Write-Verbose "Using host Python $hostPythonVersion ($($hostPython.Exe) $hostPythonArgsDisplay)"

$venvDir = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    if ($DryRun) {
        Write-DryRun "run: $($hostPython.Exe) $hostPythonArgsDisplay -m venv `"$venvDir`""
    } else {
        Write-Verbose "$($hostPython.Exe) $hostPythonArgsDisplay -m venv $venvDir"
        & $hostPython.Exe @($hostPython.Args) -m venv $venvDir
        if ($LASTEXITCODE -ne 0) { Write-Error "python -m venv failed" }
    }
} else {
    Write-Verbose "$venvDir already exists; leaving it in place"
}

$extras = if ($Dev) { ".[guard-onnx,dev]" } else { ".[guard-onnx]" }

if ($DryRun) {
    Write-DryRun "run: `"$venvPython`" -m pip install -e `"$extras`" (from $repoRoot)"
} else {
    if (-not (Test-Path $venvPython)) { Write-Error "venv python not found at $venvPython" }
    Write-Host "  Decision: '<venv>\Scripts\python -m pip install -e $extras' -- no uv, no lock file." -ForegroundColor DarkGray
    Write-Host "  Reason: no uv.lock is committed to this repo, and pip resolves the same" -ForegroundColor DarkGray
    Write-Host "  pyproject.toml dependency set uv would; the native venv only needs to be" -ForegroundColor DarkGray
    Write-Host "  reproducible on this one machine, not distributed as a locked artifact." -ForegroundColor DarkGray
    Push-Location $repoRoot
    try {
        & $venvPython -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) { Write-Error "pip install --upgrade pip failed" }
        & $venvPython -m pip install -e $extras
        if ($LASTEXITCODE -ne 0) { Write-Error "pip install -e $extras failed" }
    } finally {
        Pop-Location
    }
    Write-Verbose "Python environment ready: $venvPython"
}

# ============================================================================
# Step 2: PostgreSQL 17 portable
# ============================================================================
if ($SkipPostgres) {
    Write-Host "`n[2/7] Skipping PostgreSQL (-SkipPostgres)" -ForegroundColor Yellow
} else {
    Write-Host "`n[2/7] PostgreSQL $PgVersion (portable, no installer, port 5432)..." -ForegroundColor Cyan

    $pgRoot = Join-Path $runtimeDir "pgsql"
    $pgData = Join-Path $runtimeDir "pgdata"
    $downloadsDir = Join-Path $runtimeDir "downloads"
    $pgLogDir = Join-Path $runtimeDir "logs\pg"
    New-DirIfNeeded -Path @($downloadsDir, $pgLogDir)

    $pgCtl = Join-Path $pgRoot "bin\pg_ctl.exe"
    $initdb = Join-Path $pgRoot "bin\initdb.exe"
    $psql = Join-Path $pgRoot "bin\psql.exe"

    if (-not (Test-Path (Join-Path $pgRoot "bin\postgres.exe"))) {
        $zipName = "postgresql-$PgVersion-1-windows-x64-binaries.zip"
        $zipUrl = "https://get.enterprisedb.com/postgresql/$zipName"
        $zipPath = Join-Path $downloadsDir $zipName

        if (-not (Test-Path $zipPath)) {
            $sizeNote = if ($PgVersion -eq "17.9") { "334,313,473 bytes (verified 2026-09-05; EDB publishes no SHA256/manifest for this zip -- see warning below)" } else { "~320-360 MB (EDB publishes no manifest/SHA256 for this zip -- see warning below)" }
            Announce-Download -FileName $zipName -Source $zipUrl -SizeNote $sizeNote
            if ($DryRun) {
                Write-DryRun "download $zipUrl -> $zipPath"
            } else {
                Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
                $sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
                Write-Verbose "Downloaded $zipPath ($sizeMb MB)"
                Write-Warning "EDB does not publish a SHA256/manifest for the 'binaries without installer' zip -- integrity here rests on TLS transport + a successful HTTP 200 only; no hash verification was possible."
            }
        } else {
            Write-Verbose "Reusing cached $zipPath"
        }

        if ($DryRun) {
            Write-DryRun "expand $zipPath -> $runtimeDir, then delete runtime\pgsql\pgAdmin 4, StackBuilder, symbols (~300 MB of unused bundled tools)"
        } elseif (Test-Path $zipPath) {
            Write-Verbose "Expanding $zipPath -> $runtimeDir (the zip has a top-level pgsql\ folder)"
            Expand-Archive -Path $zipPath -DestinationPath $runtimeDir -Force
            if (-not (Test-Path (Join-Path $pgRoot "bin\postgres.exe"))) {
                Write-Error "postgres.exe not found under $pgRoot after extraction -- unexpected zip layout"
            }

            # The EDB zip bundles pgAdmin 4, StackBuilder, and debug symbols -- none of which
            # this project uses (we drive postgres via pg_ctl/psql only). Removing them saves
            # ~300 MB and leaves everything under runtime\pgsql\bin untouched.
            $bloatDirs = @(
                (Join-Path $pgRoot "pgAdmin 4"),
                (Join-Path $pgRoot "StackBuilder"),
                (Join-Path $pgRoot "symbols")
            )
            foreach ($dir in $bloatDirs) {
                if (Test-Path $dir) {
                    Remove-Item -Path $dir -Recurse -Force
                    Write-Verbose "Removed $dir"
                }
            }
        }
    } else {
        Write-Verbose "$pgRoot already has PostgreSQL binaries"
    }

    if ((Test-Path (Join-Path $pgData "PG_VERSION"))) {
        Write-Verbose "$pgData already initialized (PG_VERSION present); leaving initdb alone"
    } elseif ($DryRun) {
        Write-DryRun "run: initdb -D $pgData -U eoa --auth=scram-sha-256 -E UTF8 --locale=C, then append postgresql.conf overrides (port=5432, listen_addresses=127.0.0.1, timezone/log_timezone=Asia/Jerusalem, shared_buffers=512MB, max_connections=60, logging_collector=on, log_directory=$pgLogDir, log_filename=postgresql-%Y-%m-%d.log, log_rotation_age=1d)"
    } else {
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
port = 5432
listen_addresses = '127.0.0.1'
timezone = 'Asia/Jerusalem'
log_timezone = 'Asia/Jerusalem'
shared_buffers = 512MB
max_connections = 60
logging_collector = on
log_directory = '$pgLogDirForward'
log_filename = 'postgresql-%Y-%m-%d.log'
log_rotation_age = 1d
"@
        Write-Verbose "Wrote postgresql.conf overrides (port 5432, Asia/Jerusalem, logging -> $pgLogDir)"
    }

    # --- From here on: (re-)start, ensure DB exists, migrate, seed. Skipped entirely under
    #     -DryRun since each of these steps depends on the previous one's real effect. ---
    if ($DryRun) {
        Write-DryRun "start postgres if not already running (pg_ctl -D $pgData -w start), CREATE DATABASE eoanalyst if missing, run 'alembic upgrade head' and db\seed\seed_watchlist.py against postgresql://eoa:***@127.0.0.1:5432/eoanalyst"
    } else {
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
        $dbExists = & $psql -U eoa -h 127.0.0.1 -p 5432 -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='eoanalyst'" 2>$null
        if ($dbExists -notmatch "1") {
            Write-Verbose "Creating database eoanalyst (owner eoa)"
            & $psql -U eoa -h 127.0.0.1 -p 5432 -d postgres -c "CREATE DATABASE eoanalyst OWNER eoa;" | Out-Null
            if ($LASTEXITCODE -ne 0) { Write-Error "CREATE DATABASE eoanalyst failed" }
        } else {
            Write-Verbose "database eoanalyst already exists"
        }

        $env:DATABASE_URL = "postgresql://eoa:$pgPassword@127.0.0.1:5432/eoanalyst"
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
        Write-Verbose "PostgreSQL ready on 127.0.0.1:5432/eoanalyst"
    }
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
    New-DirIfNeeded -Path @($ntfyDir, $downloadsDir)
    $ntfyExe = Join-Path $ntfyDir "ntfy.exe"

    if (-not (Test-Path $ntfyExe)) {
        $zipName = "ntfy_$($NtfyVersion)_windows_amd64.zip"
        $zipUrl = "https://github.com/binwiederhier/ntfy/releases/download/v$NtfyVersion/$zipName"
        $checksumsUrl = "https://github.com/binwiederhier/ntfy/releases/download/v$NtfyVersion/checksums.txt"
        $zipPath = Join-Path $downloadsDir $zipName
        # The zip extracts to a nested folder named after itself, e.g.
        # ntfy_2.28.0_windows_amd64\ntfy.exe -- flattened into runtime\ntfy\ntfy.exe below.

        if (-not (Test-Path $zipPath)) {
            $sizeNote = if ($NtfyVersion -eq "2.28.0") { "single static Go binary, sha256 fa49abd3462a588e1555701d360de84f192a45764a97054c4cafb486ea3cdf78 (verified 2026-09-05)" } else { "~10-15 MB (single static Go binary; exact size varies per release)" }
            Announce-Download -FileName $zipName -Source $zipUrl -SizeNote $sizeNote
            if ($DryRun) {
                Write-DryRun "download $zipUrl -> $zipPath"
            } else {
                Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
                $sizeMb = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
                Write-Verbose "Downloaded $zipPath ($sizeMb MB)"
            }
        } else {
            Write-Verbose "Reusing cached $zipPath"
        }

        if ($DryRun) {
            Write-DryRun "fetch $checksumsUrl, verify SHA256 of $zipName, expand it, and copy the nested ntfy.exe to $ntfyExe"
        } elseif (Test-Path $zipPath) {
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
        }
    } else {
        Write-Verbose "$ntfyExe already present"
    }

    $cacheFile = (Join-Path $ntfyDir "cache.db") -replace '\\', '/'
    $serverYml = Join-Path $ntfyDir "server.yml"
    $serverYmlContent = @"
# EO-Analyst self-hosted ntfy (native). Phone subscribes via Tailscale: http://100.70.157.25:8091/eo-analyst
# NOTE: attachment-cache-dir is deliberately NOT set -- with it, ntfy 2.28.0 on Windows fails
# to serve (verified 2026-09-05: requests come back 501/refused). Leave it unset.
base-url: "http://100.70.157.25:8091"
listen-http: "0.0.0.0:8091"
cache-file: "$cacheFile"
cache-duration: "72h"
behind-proxy: false
log-level: info
"@
    if ($DryRun) {
        Write-DryRun "write $serverYml"
    } else {
        $serverYmlContent | Set-Content -Path $serverYml -Encoding utf8
        Write-Verbose "Wrote $serverYml"
    }
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
        New-DirIfNeeded -Path @($modelDir)

        # Prefer copying the already-baked-in model out of the (retiring) docker container's
        # filesystem over re-downloading ~370 MB from Hugging Face. `docker cp` reads a
        # container's filesystem regardless of whether it's running or stopped -- it does not
        # start, stop, or otherwise manage the container.
        $dockerContainerName = "eoa-agent"
        $dockerContainerExists = $false
        $dockerCmd = Get-Command docker -ErrorAction SilentlyContinue
        if ($dockerCmd) {
            $existingNames = & docker ps -a --filter "name=^/$dockerContainerName`$" --format "{{.Names}}" 2>$null
            $dockerContainerExists = ($LASTEXITCODE -eq 0) -and ($existingNames -match [Regex]::Escape($dockerContainerName))
        }

        $modelInstalled = $false
        if ($dockerContainerExists) {
            Write-Host "Found docker container '$dockerContainerName' -- copying its baked-in guard model instead of downloading from Hugging Face." -ForegroundColor DarkGray
            if ($DryRun) {
                Write-DryRun "run: docker cp ${dockerContainerName}:/opt/models/prompt-guard `"$modelDir`""
                $modelInstalled = $true
            } else {
                & docker cp "${dockerContainerName}:/opt/models/prompt-guard" $modelDir
                if ($LASTEXITCODE -eq 0 -and (Test-Path (Join-Path $modelDir "model.onnx"))) {
                    $modelInstalled = $true
                    Write-Verbose "Copied guard model from docker container '$dockerContainerName' -> $modelDir"
                } else {
                    Write-Warning "docker cp from '$dockerContainerName' failed or produced no model.onnx -- falling back to huggingface_hub download"
                }
            }
        } else {
            Write-Verbose "No docker container named '$dockerContainerName' found -- will download from Hugging Face"
        }

        if (-not $modelInstalled) {
            Announce-Download -FileName "protectai/deberta-v3-base-prompt-injection-v2 (onnx/ subfolder)" `
                -Source "https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2" `
                -SizeNote "~370 MB (model.onnx + tokenizer files; via huggingface_hub, not a single direct URL -- mirrors docker\agent\Dockerfile's 'guard-model' build stage)"

            if ($DryRun) {
                Write-DryRun "run a Python helper script that snapshot_download()s $MODEL_ID's onnx/ folder (or exports it locally via optimum-cli if no onnx/ folder exists on the hub) into $modelDir"
            } else {
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
    }
}

# ============================================================================
# Step 5: Frontend
# ============================================================================
if ($SkipFrontend) {
    Write-Host "`n[5/7] Skipping frontend build (-SkipFrontend)" -ForegroundColor Yellow
} else {
    Write-Host "`n[5/7] Frontend (npm ci && npm run build)..." -ForegroundColor Cyan
    if ($DryRun) {
        Write-DryRun "run: npm ci && npm run build (in $(Join-Path $repoRoot 'web'))"
    } else {
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
$eoaEnvContent = @"
EOA_ROOT=$($repoRoot -replace '\\', '/')
EOA_ROLE=host
DATABASE_URL=postgresql://eoa:$pgPassword@127.0.0.1:5432/eoanalyst
OLLAMA_URL=http://127.0.0.1:11434
NTFY_URL=http://127.0.0.1:8091
EOA_GUARD_L1_DIR=$guardDir
HF_HUB_OFFLINE=1
TZ=Asia/Jerusalem
PYTHONUTF8=1
PYTHONUNBUFFERED=1
"@
if ($DryRun) {
    Write-DryRun "write $eoaEnvPath with:"
    $eoaEnvContent -split "`n" | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
} else {
    $eoaEnvContent | Set-Content -Path $eoaEnvPath -Encoding utf8
    Write-Verbose "Wrote $eoaEnvPath"
}

# ============================================================================
# Summary
# ============================================================================
Write-Host "`n" -ForegroundColor Cyan
if ($DryRun) {
    Write-Host "Dry run complete -- nothing was changed." -ForegroundColor Green
} else {
    Write-Host "Native install complete." -ForegroundColor Green
}
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
  runtime\pgsql\, pgdata\   PostgreSQL $PgVersion binaries + cluster (port 5432)
  runtime\ntfy\             ntfy.exe + server.yml (port 8091, listens on 0.0.0.0 for Tailscale)
  runtime\models\prompt-guard\  ONNX prompt-injection classifier
  runtime\eoa.env           env vars for the supervisor / eo CLI
  runtime\logs\             per-service logs (written by the supervisor)

.venv\ (repo root): project virtualenv, built from the host's own Python (>=3.12) -- no
managed Python download.

See docs\adr\004-windows-native.md for the full rationale and security posture.
"@
