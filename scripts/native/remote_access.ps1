<#
.SYNOPSIS
    Remote-access helper for EO-Analyst over Tailscale (ADR-008, docs\adr\008-remote-access.md).

.DESCRIPTION
    Idempotent helper around three independent, unrelated pieces of "let me reach my analyst app
    from my phone" setup:

      1. -SetPasscode : sets the shared passcode the API's own login gate checks
                         (agent\eoa\api\auth.py) -- written to runtime\eoa.env, never echoed.
      2. -Enable /
         -Disable /
         -Status       : manages the Tailscale Serve reverse-proxy share itself (the thing that
                         actually exposes 127.0.0.1:8765 to your tailnet over HTTPS).
      3. -NoSleep       : a convenience so the machine doesn't go to sleep and drop the share.

    None of this replaces the app-level gate: `config\config.yaml`'s `api.remote_access.enabled`
    is the switch that makes the API actually require a session for non-loopback callers. This
    script does not flip that flag for you (editing YAML from PowerShell reliably is more trouble
    than it's worth) -- `-Enable` prints a loud reminder if it looks like you forgot.

.PARAMETER SetPasscode
    Prompts (Read-Host -AsSecureString, never echoed) for a new access passcode and writes
    EOA_ACCESS_PASSCODE=<value> into runtime\eoa.env, replacing any previous line. Reminds you to
    restart the API afterward (it hashes the passcode once at process start -- see auth.py).

.PARAMETER Enable
    Runs `tailscale serve --bg --https=443 http://127.0.0.1:8765` (idempotent -- Tailscale Serve
    itself no-ops if the mapping already exists) and prints the resulting https:// URL.

.PARAMETER IncludeNtfy
    With -Enable, also serves ntfy (127.0.0.1:8091) on --https=8443. Off by default.

.PARAMETER Disable
    Runs `tailscale serve reset`, turning off every Serve mapping this machine was publishing.

.PARAMETER Status
    Prints Tailscale's own serve status, this machine's tailnet identity, whether
    EOA_ACCESS_PASSCODE is set (never its value), and whether the app-level gate
    (`api.remote_access.enabled` in config\config.yaml) is currently on.

.PARAMETER NoSleep
    Sets the active Windows power plan to never sleep on AC power
    (`powercfg /change standby-timeout-ac 0`) -- user-level, no admin rights needed.

.EXAMPLE
    pwsh -File scripts\native\remote_access.ps1 -SetPasscode
    pwsh -File scripts\native\remote_access.ps1 -Enable
    pwsh -File scripts\native\remote_access.ps1 -Status
    pwsh -File scripts\native\remote_access.ps1 -Disable
#>

[CmdletBinding()]
param(
    [switch]$SetPasscode,
    [switch]$Enable,
    [switch]$IncludeNtfy,
    [switch]$Disable,
    [switch]$Status,
    [switch]$NoSleep
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$RuntimeDir = Join-Path $RepoRoot "runtime"
$EnvPath = Join-Path $RuntimeDir "eoa.env"
$ConfigPath = Join-Path $RepoRoot "config\config.yaml"

function Test-TailscaleAvailable {
    $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if (-not $cmd) {
        Write-Error "tailscale.exe not found on PATH. Install Tailscale first: https://tailscale.com/download/windows"
        return $false
    }
    return $true
}

function Get-RemoteAccessEnabledFromConfig {
    <# Best-effort check of api.remote_access.enabled in config\config.yaml -- a plain regex over
       the known block shape (see agent\eoa\config.py::RemoteAccessCfg), not a full YAML parse. #>
    if (-not (Test-Path $ConfigPath)) { return $null }
    $text = Get-Content -Path $ConfigPath -Raw
    if ($text -match "(?ms)remote_access:\s*\r?\n(?:.*\r?\n)*?\s*enabled:\s*(true|false)") {
        return $Matches[1] -eq "true"
    }
    return $null
}

function Get-PasscodeConfigured {
    if (-not (Test-Path $EnvPath)) { return $false }
    $line = Get-Content -Path $EnvPath | Where-Object { $_ -match "^EOA_ACCESS_PASSCODE=" } | Select-Object -Last 1
    return [bool]$line -and ($line -replace "^EOA_ACCESS_PASSCODE=", "").Trim().Length -gt 0
}

function Get-TailscaleHttpsUrl {
    try {
        $json = tailscale status --json 2>$null | ConvertFrom-Json
        $dnsName = $json.Self.DNSName
        if ($dnsName) {
            return "https://$($dnsName.TrimEnd('.'))"
        }
    } catch {
        # fall through
    }
    return $null
}

function Invoke-SetPasscode {
    Write-Host "== EO-Analyst remote access: set passcode ==" -ForegroundColor Cyan
    $secure = Read-Host -Prompt "קוד גישה חדש / new access passcode (won't be shown)" -AsSecureString
    if ($secure.Length -eq 0) {
        Write-Warning "No passcode entered -- nothing written."
        return
    }

    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($secure)
    try {
        $plain = [System.Runtime.InteropServices.Marshal]::PtrToStringUni($bstr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeGlobalAllocUnicode($bstr)
    }

    if ($plain.Length -lt 8) {
        Write-Warning "That passcode is under 8 characters -- consider something longer since it is the only thing standing between a Tailscale peer and this app."
    }

    if (-not (Test-Path $RuntimeDir)) {
        New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
    }

    $existingLines = @()
    if (Test-Path $EnvPath) {
        $existingLines = Get-Content -Path $EnvPath | Where-Object { $_ -notmatch "^EOA_ACCESS_PASSCODE=" }
    }
    $newLines = $existingLines + "EOA_ACCESS_PASSCODE=$plain"
    Set-Content -Path $EnvPath -Value $newLines -Encoding UTF8

    # Never let the plaintext linger in memory longer than necessary.
    $plain = $null
    [System.GC]::Collect()

    Write-Host "Passcode written to runtime\eoa.env (EOA_ACCESS_PASSCODE=...)." -ForegroundColor Green
    Write-Host "Restart the API for it to take effect:" -ForegroundColor Yellow
    Write-Host "    eo native stop"
    Write-Host "    eo native start"

    # `Get-X -eq $false` passes `-eq $false` as ARGUMENTS to the function (PowerShell parses a
    # bare command call greedily), so the warning fired even with enabled: true -- compare the
    # call's result instead (lead fix 2026-09-08).
    if ((Get-RemoteAccessEnabledFromConfig) -eq $false) {
        Write-Warning "config\config.yaml api.remote_access.enabled is still 'false' -- the API will NOT require this passcode until you set it to 'true' and restart."
    }
}

function Invoke-Enable {
    if (-not (Test-TailscaleAvailable)) { return }

    Write-Host "== EO-Analyst remote access: enable Tailscale Serve ==" -ForegroundColor Cyan
    Write-Host "Checking tailscale status..."
    tailscale status

    $serveArgs = @("serve", "--bg", "--https=443", "http://127.0.0.1:8765")
    Write-Host "Running: tailscale $($serveArgs -join ' ')"
    & tailscale @serveArgs

    if ($IncludeNtfy) {
        $ntfyArgs = @("serve", "--bg", "--https=8443", "http://127.0.0.1:8091")
        Write-Host "Running: tailscale $($ntfyArgs -join ' ')"
        & tailscale @ntfyArgs
    }

    Write-Host ""
    Write-Host "Current serve config:" -ForegroundColor Cyan
    tailscale serve status

    $url = Get-TailscaleHttpsUrl
    if ($url) {
        Write-Host ""
        Write-Host "EO-Analyst should now be reachable at: $url" -ForegroundColor Green
        if ($IncludeNtfy) {
            $hostOnly = $url -replace "^https://", ""
            Write-Host "ntfy should be reachable at: https://${hostOnly}:8443 (via the --https=8443 mapping above)"
        }
    } else {
        Write-Warning "Could not determine this machine's tailnet hostname automatically -- run 'tailscale status' to find it."
    }

    $gateOn = Get-RemoteAccessEnabledFromConfig
    if ($gateOn -ne $true) {
        Write-Host ""
        Write-Warning "api.remote_access.enabled in config\config.yaml is NOT 'true' -- anyone who reaches the URL above gets in with NO passcode. Set it to true, run -SetPasscode if you haven't, then 'eo native stop' / 'eo native start'."
    } else {
        Write-Host "api.remote_access.enabled is 'true' -- the passcode gate is active for this URL." -ForegroundColor Green
    }
}

function Invoke-Disable {
    if (-not (Test-TailscaleAvailable)) { return }
    Write-Host "== EO-Analyst remote access: disable Tailscale Serve ==" -ForegroundColor Cyan
    tailscale serve reset
    Write-Host "All Tailscale Serve mappings from this machine have been reset." -ForegroundColor Green
}

function Invoke-Status {
    Write-Host "== EO-Analyst remote access: status ==" -ForegroundColor Cyan

    if (Test-TailscaleAvailable) {
        Write-Host ""
        Write-Host "-- tailscale status --" -ForegroundColor DarkCyan
        tailscale status
        Write-Host ""
        Write-Host "-- tailscale serve status --" -ForegroundColor DarkCyan
        tailscale serve status
    }

    Write-Host ""
    Write-Host "-- EO-Analyst app-level gate --" -ForegroundColor DarkCyan
    $gateOn = Get-RemoteAccessEnabledFromConfig
    switch ($gateOn) {
        $true { Write-Host "api.remote_access.enabled: true (passcode required for non-loopback clients)" -ForegroundColor Green }
        $false { Write-Host "api.remote_access.enabled: false (no gate -- fine if not sharing via Tailscale Serve)" -ForegroundColor Yellow }
        default { Write-Warning "Could not read api.remote_access.enabled from $ConfigPath" }
    }
    $passcodeSet = Get-PasscodeConfigured
    Write-Host "EOA_ACCESS_PASSCODE configured in runtime\eoa.env: $passcodeSet"
}

function Invoke-NoSleep {
    Write-Host "== EO-Analyst remote access: keep this PC awake on AC power ==" -ForegroundColor Cyan
    Write-Host "Running: powercfg /change standby-timeout-ac 0"
    powercfg /change standby-timeout-ac 0
    Write-Host "Done -- this PC will no longer sleep automatically while plugged in (AC). Battery/DC behavior is unchanged." -ForegroundColor Green
}

$ranSomething = $false

if ($SetPasscode) { Invoke-SetPasscode; $ranSomething = $true }
if ($NoSleep) { Invoke-NoSleep; $ranSomething = $true }

if ($Enable -and $Disable) {
    Write-Error "Pass only one of -Enable / -Disable."
    exit 1
}
if ($Enable) { Invoke-Enable; $ranSomething = $true }
if ($Disable) { Invoke-Disable; $ranSomething = $true }
if ($Status) { Invoke-Status; $ranSomething = $true }

if (-not $ranSomething) {
    Write-Host "Usage: remote_access.ps1 [-SetPasscode] [-Enable [-IncludeNtfy]] [-Disable] [-Status] [-NoSleep]"
    Write-Host "See the script header (Get-Help .\scripts\native\remote_access.ps1 -Full) for details."
}
