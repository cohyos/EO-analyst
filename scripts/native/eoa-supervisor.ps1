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

On shutdown, postgres is left running by default (Q6-4, 2026-09-06) -- pass -StopPostgres (or
-KeepPostgres:$false) to also stop it, or trigger the stop via `eo native stop --with-postgres`,
which writes a sentinel file this script recognizes. See the -StopPostgres/-KeepPostgres param
docs and the `finally` block below.

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
child tree itself (Process.Kill(true), or `pg_ctl stop -m fast` for postgres) rather than relying on
a child's own signal handling. See agent\eoa\orchestrator\main.py's Windows note for why its
own SIGINT/SIGTERM handlers are still registered (interactive `eo orchestrate` / Ctrl+C use)
without contradicting this.

Every 60s, GET http://127.0.0.1:8765/api/health is probed and logged without database,
search or GPU work (agent\eoa\api\routes\status.py).

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
    [int] $StopPollSeconds = 2,
    # Q6-4 (2026-09-06): by default postgres is left running when the supervisor stops -- a
    # database that stays up across `eo native stop` avoids the unconditional shutdown the r2 QA
    # pass flagged. Pass -StopPostgres (or have the sentinel content say so, see `eo native stop
    # --with-postgres`) to also stop postgres. -KeepPostgres is accepted as an explicit synonym
    # for the default (keep postgres up); it exists so callers can be explicit either way and so
    # -KeepPostgres:$false reads naturally as "don't keep it".
    [switch] $StopPostgres,
    [bool] $KeepPostgres = $true
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
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8:backslashreplace"

[System.Diagnostics.Process]::GetCurrentProcess().Id | Set-Content -Path $supervisorPidPath -Encoding ascii
Write-Log "Supervisor started (pid $((Get-Content $supervisorPidPath)))"

# ---------------------------------------------------------------------------
# Q6-5a (2026-09-06): log housekeeping, run once at startup.
#   1. Archive legacy, non-dated *.log files left over from before this supervisor existed
#      (e.g. agent.log, web.log, agent.err.log, web.err.log) into runtime\logs\archive\<yyyymmdd>\.
#   2. Rotate away this supervisor's own dated logs (<name>.yyyy-MM-dd.log[.err]) once they are
#      older than 14 days.
# supervisor.log and postgres.log are excluded from archiving: both are actively appended to by
# processes this same script manages across restarts (Write-Log -> supervisor.log for the life of
# the machine; pg_ctl's -l target is postgres.log, and moving it while postgres holds the handle
# open would orphan future writes).
# ---------------------------------------------------------------------------
$datedLogPattern = '^.+\.\d{4}-\d{2}-\d{2}\.log(\.err)?$'
$legacyExcluded = @('supervisor.log', 'postgres.log')
try {
    if (Test-Path $logDir) {
        $legacyLogs = Get-ChildItem -Path $logDir -Filter "*.log" -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notmatch $datedLogPattern -and $_.Name -notin $legacyExcluded }
        if ($legacyLogs) {
            $archiveDir = Join-Path $logDir ("archive\{0:yyyyMMdd}" -f (Get-Date))
            New-Item -ItemType Directory -Force -Path $archiveDir | Out-Null
            foreach ($f in $legacyLogs) {
                try {
                    Move-Item -Path $f.FullName -Destination (Join-Path $archiveDir $f.Name) -Force -ErrorAction Stop
                    Write-Log "Archived legacy log '$($f.Name)' -> $archiveDir"
                } catch {
                    Write-Log "WARNING: could not archive legacy log '$($f.Name)': $_"
                }
            }
        }

        $rotateCutoff = (Get-Date).AddDays(-14)
        $oldDatedLogs = Get-ChildItem -Path $logDir -Filter "*.log*" -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match $datedLogPattern -and $_.LastWriteTime -lt $rotateCutoff }
        foreach ($f in $oldDatedLogs) {
            try {
                Remove-Item -Path $f.FullName -Force -ErrorAction Stop
                Write-Log "Rotated out old dated log '$($f.Name)' (last write $($f.LastWriteTime))"
            } catch {
                Write-Log "WARNING: could not rotate old dated log '$($f.Name)': $_"
            }
        }
    }
} catch {
    Write-Log "WARNING: log housekeeping (archive/rotate) failed: $_"
}

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
        # Python's venv launcher has a separate interpreter child. Capture handles before
        # killing so PID reuse cannot target an unrelated process, then verify descendants too.
        $treeIds = [System.Collections.Generic.HashSet[int]]::new()
        [void]$treeIds.Add($entry.Process.Id)
        $snapshot = @(Get-CimInstance Win32_Process)
        do {
            $added = $false
            foreach ($child in $snapshot) {
                if ($treeIds.Contains([int]$child.ParentProcessId) -and $treeIds.Add([int]$child.ProcessId)) {
                    $added = $true
                }
            }
        } while ($added)
        $handles = @($treeIds | ForEach-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        $entry.Process.Kill($true)
        # Some Windows launchers exit before .NET finishes traversing their descendants.
        # The handles captured above still identify the exact owned processes.
        foreach ($handle in $handles) {
            if (-not $handle.HasExited) { $handle.Kill($true) }
        }
        foreach ($handle in $handles) {
            if (-not $handle.WaitForExit(10000)) {
                throw "Managed descendant $($handle.Id) of '$Name' did not stop"
            }
        }
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
                $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/health" -TimeoutSec 5 -UseBasicParsing
                Write-Log "health: api/health -> HTTP $($resp.StatusCode)"
            } catch {
                Write-Log "health: api/health -> FAILED ($($_.Exception.Message))"
            }
        }

        Start-Sleep -Seconds $StopPollSeconds
    }
} finally {
    foreach ($name in @($children.Keys)) {
        Stop-ManagedChild -Name $name
    }

    # Q6-4 (2026-09-06): postgres is kept running by default. It is stopped only if the script
    # was launched with -StopPostgres / -KeepPostgres:$false, or if the sentinel file that
    # triggered this shutdown carries the "with-postgres" marker (written by
    # `eo native stop --with-postgres`; see agent\eoa\cli.py).
    $sentinelSaysStopPostgres = $false
    if (Test-Path $sentinelPath) {
        $sentinelContent = Get-Content -Path $sentinelPath -Raw -ErrorAction SilentlyContinue
        if ($sentinelContent -match 'with-postgres') { $sentinelSaysStopPostgres = $true }
    }
    $shouldStopPostgres = $StopPostgres -or (-not $KeepPostgres) -or $sentinelSaysStopPostgres
    if ($shouldStopPostgres) {
        Stop-Postgres
    } else {
        Write-Log "Leaving postgres running (default; pass -StopPostgres or 'eo native stop --with-postgres' to stop it too)"
    }

    Remove-Item $sentinelPath -Force -ErrorAction SilentlyContinue
    Remove-Item $supervisorPidPath -Force -ErrorAction SilentlyContinue
    Write-Log "Supervisor stopped"
}
