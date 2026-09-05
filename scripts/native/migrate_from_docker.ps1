#!/usr/bin/env pwsh
<#
.SYNOPSIS
One-time data move: dump the (retiring) dockerized PostgreSQL and restore it into the
native cluster started by install_native.ps1.

.DESCRIPTION
Assumes, BEFORE this script runs:
  - `alembic upgrade head` (through migration 0006, from another agent's work item) has
    already been applied against the DOCKER database, so `items.embedding` is `real[]`
    (not pgvector) and `graph_edges` exists (Apache AGE tables are gone).
  - `scripts\export_age_edges.py` has already been run against the docker database (if it
    still had AGE data at that point) so any AGE-era edges are already reflected in
    `graph_edges` before the dump below.
  - The native cluster from scripts\native\install_native.ps1 is up (empty `eoanalyst` DB,
    schema already migrated to head there too).

Steps:
  1. `docker compose exec -T postgres pg_dump -U eoa -d eoanalyst --no-owner --no-privileges
     --exclude-schema=ag_catalog --exclude-extension=age --exclude-extension=vector -Fc`
     -> output\backups\docker_final_<timestamp>.dump (custom format, for pg_restore)
     and a second plain-SQL dump (`-Fp`) alongside it, for human inspection/grep.
  2. `pg_restore --no-owner --clean --if-exists` into the native cluster (127.0.0.1:5432 --
     the native port; 5433 was only ever the Docker Compose host-mapping, now retired).
  3. Row counts, both sides, for: items, entities, events, tenders, tender_forecasts,
     graph_edges, reports, conferences, jobs.

.PARAMETER DryRun
Print every command that would run (with resolved paths/args) without executing pg_dump,
pg_restore, or any DB-mutating psql call. Row-count queries against the native side are also
skipped (the whole point is to run before that side has meaningful data).

.PARAMETER SkipRestore
Dump only; do not restore. Useful to inspect the dump before committing to the restore.

.EXAMPLE
./scripts/native/migrate_from_docker.ps1 -DryRun
./scripts/native/migrate_from_docker.ps1

.NOTES
Requires the `postgres` container from docker-compose.yml to still be up (for pg_dump) and
the native cluster (runtime\pgsql, port 5432) to already be running (for pg_restore).
#>

#Requires -Version 7

param(
    [switch] $DryRun,
    [switch] $SkipRestore
)

$ErrorActionPreference = "Stop"
$VerbosePreference = "Continue"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runtimeDir = Join-Path $repoRoot "runtime"
$backupsDir = Join-Path $repoRoot "output\backups"
$envFile = Join-Path $repoRoot ".env"

New-Item -ItemType Directory -Force -Path $backupsDir | Out-Null

function Get-EnvValue {
    param([string]$Path, [string]$Key)
    if (-not (Test-Path $Path)) { return $null }
    $line = Get-Content $Path | Where-Object { $_ -match "^\s*$([Regex]::Escape($Key))=" } | Select-Object -First 1
    if (-not $line) { return $null }
    return ($line -split "=", 2)[1].Trim()
}

function Invoke-Step {
    <#
    Prints the command it's about to run; executes it for real unless -DryRun.
    $Block is a scriptblock so nothing side-effecting runs while composing the dry-run log.
    #>
    param(
        [Parameter(Mandatory)][string]$Description,
        [Parameter(Mandatory)][scriptblock]$Block
    )
    Write-Host ""
    Write-Host "==> $Description" -ForegroundColor Cyan
    if ($DryRun) {
        Write-Host "  [DryRun] not executed" -ForegroundColor Yellow
        return
    }
    & $Block
}

$pgPassword = Get-EnvValue -Path $envFile -Key "POSTGRES_PASSWORD"
if (-not $pgPassword) { Write-Error "POSTGRES_PASSWORD not found in $envFile" }

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dumpCustom = Join-Path $backupsDir "docker_final_$timestamp.dump"
$dumpPlain = Join-Path $backupsDir "docker_final_$timestamp.sql"

$pgDumpArgsCommon = @(
    "exec", "-T", "postgres",
    "pg_dump", "-U", "eoa", "-d", "eoanalyst",
    "--no-owner", "--no-privileges",
    "--exclude-schema=ag_catalog",
    "--exclude-extension=age",
    "--exclude-extension=vector"
)

Write-Host "EO-Analyst: migrate Docker Postgres -> native Postgres" -ForegroundColor Cyan
Write-Host "dump (custom, for pg_restore): $dumpCustom" -ForegroundColor DarkGray
Write-Host "dump (plain SQL, for inspection): $dumpPlain" -ForegroundColor DarkGray
if ($DryRun) { Write-Host "-DryRun: no command below will actually execute." -ForegroundColor Yellow }

# ============================================================================
# 1. Dump from the docker container
# ============================================================================
Invoke-Step -Description "docker compose exec postgres pg_dump (custom format -Fc) -> $dumpCustom" -Block {
    Push-Location $repoRoot
    try {
        # `docker compose exec` streams pg_dump's binary output to stdout; PowerShell 7's native
        # '>' file redirection writes the child process's raw stdout bytes through untouched
        # (unlike piping into `Set-Content`, which re-encodes text and can corrupt binary data).
        $customArgs = $pgDumpArgsCommon + @("-Fc")
        docker compose @customArgs > $dumpCustom
        if ($LASTEXITCODE -ne 0) { Write-Error "pg_dump (custom format) failed" }
        Write-Verbose "Wrote $dumpCustom ($((Get-Item $dumpCustom).Length) bytes)"
    } finally {
        Pop-Location
    }
}

Invoke-Step -Description "docker compose exec postgres pg_dump (plain SQL -Fp) -> $dumpPlain" -Block {
    Push-Location $repoRoot
    try {
        $plainArgs = $pgDumpArgsCommon + @("-Fp")
        docker compose @plainArgs > $dumpPlain
        if ($LASTEXITCODE -ne 0) { Write-Error "pg_dump (plain SQL) failed" }
        Write-Verbose "Wrote $dumpPlain ($((Get-Item $dumpPlain).Length) bytes)"
    } finally {
        Pop-Location
    }
}

# ============================================================================
# 2. Restore into the native cluster
# ============================================================================
$pgRestore = Join-Path $runtimeDir "pgsql\bin\pg_restore.exe"
$psql = Join-Path $runtimeDir "pgsql\bin\psql.exe"

if ($SkipRestore) {
    Write-Host "`n-SkipRestore: leaving the native database untouched." -ForegroundColor Yellow
} else {
    Invoke-Step -Description "pg_restore --no-owner --clean --if-exists into native eoanalyst (127.0.0.1:5432)" -Block {
        if (-not (Test-Path $pgRestore)) { Write-Error "$pgRestore not found -- run install_native.ps1 first" }
        $env:PGPASSWORD = $pgPassword
        try {
            & $pgRestore -h 127.0.0.1 -p 5432 -U eoa -d eoanalyst --no-owner --clean --if-exists $dumpCustom
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "pg_restore exited $LASTEXITCODE -- pg_restore commonly warns/exits non-zero on harmless 'does not exist, skipping' DROP statements from --clean --if-exists on a fresh DB. Verify the row counts below before trusting this exit code alone."
            } else {
                Write-Verbose "pg_restore completed"
            }
        } finally {
            Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
        }
    }
}

# ============================================================================
# 3. Row counts, both sides
# ============================================================================
$tables = @("items", "entities", "events", "tenders", "tender_forecasts", "graph_edges", "reports", "conferences", "jobs")

function Get-RowCounts {
    param(
        [Parameter(Mandatory)][string]$Label,
        [Parameter(Mandatory)][scriptblock]$RunPsql
    )
    Write-Host "`n-- Row counts: $Label --" -ForegroundColor Cyan
    $counts = [ordered]@{}
    foreach ($table in $tables) {
        $sql = "SELECT count(*) FROM $table;"
        try {
            $out = & $RunPsql $sql
            $n = ($out | Select-Object -Last 1).Trim()
            $counts[$table] = $n
        } catch {
            $counts[$table] = "ERROR: $($_.Exception.Message)"
        }
    }
    $counts.GetEnumerator() | ForEach-Object { Write-Host ("  {0,-20} {1}" -f $_.Key, $_.Value) }
    return $counts
}

if ($DryRun) {
    Write-Host "`n-DryRun: skipping row-count queries on both sides." -ForegroundColor Yellow
} else {
    Push-Location $repoRoot
    try {
        $dockerCounts = Get-RowCounts -Label "docker (source)" -RunPsql {
            param($sql)
            docker compose exec -T postgres psql -U eoa -d eoanalyst -tAc $sql
        }
    } finally {
        Pop-Location
    }

    $env:PGPASSWORD = $pgPassword
    try {
        $nativeCounts = Get-RowCounts -Label "native (target, 127.0.0.1:5432)" -RunPsql {
            param($sql)
            & $psql -h 127.0.0.1 -p 5432 -U eoa -d eoanalyst -tAc $sql
        }
    } finally {
        Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    }

    Write-Host "`n-- Comparison --" -ForegroundColor Cyan
    foreach ($table in $tables) {
        $d = $dockerCounts[$table]
        $n = $nativeCounts[$table]
        $flag = if ($d -eq $n) { "OK" } else { "MISMATCH" }
        Write-Host ("  {0,-20} docker={1,-10} native={2,-10} {3}" -f $table, $d, $n, $flag)
    }
}

Write-Host "`nDone. Backups kept at:" -ForegroundColor Green
Write-Host "  $dumpCustom"
Write-Host "  $dumpPlain"
