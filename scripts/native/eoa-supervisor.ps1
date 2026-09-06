#!/usr/bin/env pwsh
<#
.SYNOPSIS
Long-running supervisor for the native (no-Docker) EO-Analyst stack.

.DESCRIPTION
Loads runtime\eoa.env into the process environment, then starts and keeps alive, in order:
  1. postgres     (pg_ctl -D runtime\pgdata -w start, only if not already running)
  2. ntfy         (runtime\ntfy\ntfy.exe serve --config runtime\ntfy\server.yml)
  3. orchestrator (.venv\Scripts\python.exe -m eoa.orchestrator.main)
  4. api          (.venv\Scripts\python.exe -m uvicorn eoa.api.app:app --host 127.0.0.1 --port 8765)

Each child's stdout/stderr is logged to runtime\logs\<name>.log with daily rotation
(a new file per calendar day, old ones left in place for retention/cleanup elsewhere).
A crashed child (steps 2-4; postgres restarts are left to `pg_ctl`/manual intervention
since a repeatedly-crashing database is a much bigger problem than a stale supervisor loop)
is restarted with exponential backoff (1s, 2s, 4s, ... capped at 60s), reset to the initial
delay once the child has stayed up for at least 60s.

A pidfile is written to runtime\supervisor.pid (this process) and one per child to
runtime\pids\<name>.pid. `eo native stop` (agent\eoa\cli.py) and this script's own Ctrl+C
handling both write/react to a sentinel file, runtime\supervisor.stop, checked every 2s --
NOT SIGTERM. On Windows, `os.kill(pid, SIGTERM)`/`Stop-Process` on a Python process does not
invoke that process's own SIGTERM handler; it terminates it via TerminateProcess(). So the
graceful path here is: write the sentinel -> supervisor notices it -> supervisor stops each
child itself (Stop-Process -Id, or `pg_ctl stop -m fast` for postgres) rather than relying on
a child's own signal handling. See agent\eoa\orchestrator\main.py's Windows note for why its
own SIGINT/SIGTERM handlers are still registered (interactive `eo orchestrate` / Ctrl+C use)
without contradicting this.

Every 60s, GET http://127.0.0.1:8765/api/status is probed and logged (agent\eoa\api\routes\status.py
-- there is no separate /api/health route in this codebase).

.EXAMPLE
pwsh -NoProfile -File scripts\native\eoa-supervisor.ps1
# to stop from another shell:
New-Item -ItemType File -Force (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "runtime\supervisor.stop")

.NOTES
Intended to be launched by scripts\native\register_autostart.ps1 (Task Scheduler, at logon)
or by `eo native start` (agent\eoa\cli.py). Also runnable directly for foreground debugging.
#>

#Requires -Version 7

param(
    [int] $HealthIntervalSeconds = 60,
    [int] $StopPollSeconds = 2
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runtimeDir = Join-Path $repoRoot "runtime"
$logDir = Join-Path $runtimeDir "logs"
$pidDir = Join-Path $runtimeDir "pids"
# Q6-14 (2026-09-06): prune pidfiles that belong to dead processes or to names this supervisor
# does not manage (e.g. agent.pid/web.pid written by hand before the supervisor existed).
if (Test-Path $pidDir) {
    Get-ChildItem -Path $pidDir -Filter "*.pid" | ForEach-Object {
        $raw = (Get-Content $_.FullName -ErrorAction SilentlyContinue | Select-Object -First 1)
        $alive = $false
        if ($raw -match '^\d+$') { $alive = [bool](Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue) }
        if (-not $alive -or $_.BaseName -in @('agent','web')) { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
    }
}
$sentinelPath = Join-Path $runtimeDir "supervisor.stop"
$supervisorPidPath = Join-Path $runtimeDir "supervisor.pid"
$envFilePath = Join-Path $runtimeDir "eoa.env"

New-Item -ItemType Directory -Force -Path $logDir, $pidDir | Out-Null
Remove-Item $sentinelPath -Force -ErrorAction SilentlyContinue

function Write-Log {
    param([string]$Message)
    $line = "[{0:yyyy-MM-dd HH:mm:ss}] {1}" -f (Get-Date), $Message
    Write-Host $line
    Add-Content -Path (Join-Path $logDir "supervisor.log") -Value $line
}

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        Write-Log "WARNING: $Path not found -- run scripts\native\install_native.ps1 first. Continuing with whatever is already in the environment."
        return
    }
    foreach ($line in Get-Content $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) { continue }
        $key = $parts[0].Trim()
        $value = $parts[1].Trim()
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
    Write-Log "Loaded environment from $Path"
}

Import-DotEnv -Path $envFilePath

[System.Diagnostics.Process]::GetCurrentProcess().Id | Set-Content -Path $supervisorPidPath -Encoding ascii
Write-Log "Supervisor started (pid $((Get-Content $supervisorPidPath)))"

# ---------------------------------------------------------------------------
# Child process bookkeeping
# ---------------------------------------------------------------------------
# $children[name] = @{ Process = <Process|$null>; LogWriterDate = <string>; NextDelay = <int>;
#                       StartedAt = <DateTime>; Kind = 'managed'|'external' }
$children = @{}

function Get-DailyLogPath {
    param([string]$Name)
    return Join-Path $logDir ("{0}.{1:yyyy-MM-dd}.log" -f $Name, (Get-Date))
}

function Start-ManagedChild {
    <#
    Starts one supervised child (ntfy / orchestrator / api) with stdout+stderr redirected to
    today's rotated log file, and records its pid under runtime\pids\<name>.pid.
    #>
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$ArgumentList,
        [string]$WorkingDirectory = $repoRoot
    )
    $logPath = Get-DailyLogPath -Name $Name
    Write-Log "Starting '$Name': $FilePath $($ArgumentList -join ' ')"
    $proc = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $logPath -RedirectStandardError "$logPath.err" `
        -WindowStyle Hidden -PassThru
    $proc.Id | Set-Content -Path (Join-Path $pidDir "$Name.pid") -Encoding ascii
    return $proc
}

function Stop-ManagedChild {
    param([Parameter(Mandatory)][string]$Name)
    $entry = $children[$Name]
    if ($entry -and $entry.Process -and -not $entry.Process.HasExited) {
        Write-Log "Stopping '$Name' (pid $($entry.Process.Id))"
        try { Stop-Process -Id $entry.Process.Id -Force -ErrorAction Stop } catch { Write-Log "  (already gone: $_)" }
        try { $entry.Process.WaitForExit(10000) | Out-Null } catch {}
    }
    Remove-Item (Join-Path $pidDir "$Name.pid") -Force -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
# 1. postgres
# ---------------------------------------------------------------------------
$pgCtl = Join-Path $runtimeDir "pgsql\bin\pg_ctl.exe"
$pgData = Join-Path $runtimeDir "pgdata"

function Start-Postgres {
    if (-not (Test-Path $pgCtl)) {
        Write-Log "WARNING: $pgCtl not found -- run install_native.ps1 first. Skipping postgres."
        return
    }
    & $pgCtl -D $pgData status *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Log "postgres already running"
        return
    }
    Write-Log "Starting postgres (pg_ctl -D $pgData -w start)"
    & $pgCtl -D $pgData -l (Join-Path $logDir "postgres.log") -w start
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR: pg_ctl start failed (see runtime\logs\postgres.log)"
    } else {
        Write-Log "postgres up"
    }
}

function Stop-Postgres {
    if (-not (Test-Path $pgCtl)) { return }
    Write-Log "Stopping postgres (pg_ctl -D $pgData stop -m fast)"
    & $pgCtl -D $pgData stop -m fast 2>&1 | ForEach-Object { Write-Log "  pg_ctl: $_" }
}

Start-Postgres

# ---------------------------------------------------------------------------
# 2-4. ntfy, orchestrator, api -- managed with restart-on-exit + backoff
# ---------------------------------------------------------------------------
$ntfyExe = Join-Path $runtimeDir "ntfy\ntfy.exe"
$ntfyConfig = Join-Path $runtimeDir "ntfy\server.yml"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

$managedSpecs = @(
    @{
        Name = "ntfy"
        FilePath = $ntfyExe
        ArgumentList = @("serve", "--config", $ntfyConfig)
        Enabled = (Test-Path $ntfyExe)
    },
    @{
        Name = "orchestrator"
        FilePath = $venvPython
        ArgumentList = @("-m", "eoa.orchestrator.main")
        Enabled = (Test-Path $venvPython)
    },
    @{
        Name = "api"
        FilePath = $venvPython
        ArgumentList = @("-m", "uvicorn", "eoa.api.app:app", "--host", "127.0.0.1", "--port", "8765")
        Enabled = (Test-Path $venvPython)
    }
)

foreach ($spec in $managedSpecs) {
    if (-not $spec.Enabled) {
        Write-Log "WARNING: '$($spec.Name)' prerequisite missing ($($spec.FilePath)) -- run install_native.ps1 first. Not starting."
        continue
    }
    $proc = Start-ManagedChild -Name $spec.Name -FilePath $spec.FilePath -ArgumentList $spec.ArgumentList
    $children[$spec.Name] = @{
        Process = $proc
        NextDelay = 1
        StartedAt = Get-Date
        FilePath = $spec.FilePath
        ArgumentList = $spec.ArgumentList
    }
}

# ---------------------------------------------------------------------------
# Main loop: watch for the stop sentinel, restart dead children, periodic health probe.
# ---------------------------------------------------------------------------
$lastHealthCheck = Get-Date -Year 1970
$maxBackoff = 60

Write-Log "Supervisor watching $($children.Keys -join ', ') -- stop with a file at $sentinelPath"

try {
    while ($true) {
        if (Test-Path $sentinelPath) {
            Write-Log "Stop sentinel detected -- shutting down"
            break
        }

        foreach ($name in @($children.Keys)) {
            $entry = $children[$name]
            if ($entry.Process -and $entry.Process.HasExited) {
                $upFor = (Get-Date) - $entry.StartedAt
                if ($upFor.TotalSeconds -ge 60) {
                    $entry.NextDelay = 1
                }
                Write-Log "'$name' exited (code $($entry.Process.ExitCode)) after $([int]$upFor.TotalSeconds)s -- restarting in $($entry.NextDelay)s"
                Start-Sleep -Seconds $entry.NextDelay
                if (Test-Path $sentinelPath) { break }
                $newProc = Start-ManagedChild -Name $name -FilePath $entry.FilePath -ArgumentList $entry.ArgumentList
                $entry.Process = $newProc
                $entry.StartedAt = Get-Date
                $entry.NextDelay = [Math]::Min($entry.NextDelay * 2, $maxBackoff)
                $children[$name] = $entry
            }
        }

        if (((Get-Date) - $lastHealthCheck).TotalSeconds -ge $HealthIntervalSeconds) {
            $lastHealthCheck = Get-Date
            try {
                $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/status" -TimeoutSec 5 -UseBasicParsing
                Write-Log "health: api/status -> HTTP $($resp.StatusCode)"
            } catch {
                Write-Log "health: api/status -> FAILED ($($_.Exception.Message))"
            }
        }

        Start-Sleep -Seconds $StopPollSeconds
    }
} finally {
    foreach ($name in @($children.Keys)) {
        Stop-ManagedChild -Name $name
    }
    Stop-Postgres
    Remove-Item $sentinelPath -Force -ErrorAction SilentlyContinue
    Remove-Item $supervisorPidPath -Force -ErrorAction SilentlyContinue
    Write-Log "Supervisor stopped"
}
