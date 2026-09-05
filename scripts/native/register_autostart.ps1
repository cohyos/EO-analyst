#!/usr/bin/env pwsh
<#
.SYNOPSIS
Registers (or unregisters) the user-level Task Scheduler task that autostarts the
EO-Analyst native supervisor at logon.

.DESCRIPTION
Creates/updates a task named "EO-Analyst Supervisor":
  - Trigger:   at logon of the current user.
  - Action:    pwsh -NoProfile -WindowStyle Hidden -File <repo>\scripts\native\eoa-supervisor.ps1
  - Principal: the current user, LeastPrivilege (no admin required or requested).
  - Settings:  restart on failure (3 attempts, 1-minute interval), run only when the user is
               logged on (not S4U/hidden-session), start-when-available so a delayed logon
               still triggers it.

This task is independent of, and does not touch, the pre-existing "EO-Analyst Wake" task
(Task Scheduler, daily 00:55, WakeToRun=true, a no-op `cmd.exe /c exit 0` action). That task's
only job is to force the laptop out of sleep before the 01:00-06:00 night window so the
*already-running* supervisor's orchestrator can pick up its scheduled work -- it starts
nothing itself. See docs\adr\004-windows-native.md for how the two tasks relate.

.PARAMETER Unregister
Remove the "EO-Analyst Supervisor" task instead of creating/updating it.

.PARAMETER TaskName
Override the task name (default: "EO-Analyst Supervisor").

.EXAMPLE
./scripts/native/register_autostart.ps1
./scripts/native/register_autostart.ps1 -Unregister

.NOTES
User-level task (no admin rights needed or used) -- registered under the current user's
Task Scheduler library, not \Microsoft\Windows\..., and not in the root with SYSTEM rights.
#>

#Requires -Version 7

param(
    [switch] $Unregister,
    [string] $TaskName = "EO-Analyst Supervisor"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$supervisorScript = Join-Path $repoRoot "scripts\native\eoa-supervisor.ps1"

if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $existing) {
        Write-Host "No task named '$TaskName' is registered; nothing to do." -ForegroundColor Yellow
        return
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Unregistered task '$TaskName'." -ForegroundColor Green
    return
}

if (-not (Test-Path $supervisorScript)) {
    Write-Error "Supervisor script not found at $supervisorScript"
}

$pwshCmd = Get-Command pwsh -ErrorAction SilentlyContinue
if (-not $pwshCmd) { Write-Error "pwsh not found on PATH -- PowerShell 7 is required to run the supervisor." }

$action = New-ScheduledTaskAction -Execute $pwshCmd.Source `
    -Argument "-NoProfile -WindowStyle Hidden -File `"$supervisorScript`"" `
    -WorkingDirectory $repoRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -MultipleInstances IgnoreNew

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Updating existing task '$TaskName'..." -ForegroundColor Cyan
    Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
} else {
    Write-Host "Registering task '$TaskName'..." -ForegroundColor Cyan
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
        -Description "Runs the EO-Analyst native supervisor (postgres, ntfy, orchestrator, api) at user logon. No admin rights. See docs\adr\004-windows-native.md." | Out-Null
}

Write-Host "Done. Task detail:" -ForegroundColor Green
Get-ScheduledTask -TaskName $TaskName | Format-List TaskName, State
Write-Host "Run it now with: Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor DarkGray
Write-Host "(or just: eo native start / pwsh -File scripts\native\eoa-supervisor.ps1)" -ForegroundColor DarkGray
