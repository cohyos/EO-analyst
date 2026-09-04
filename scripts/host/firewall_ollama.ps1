# Run ONCE in an elevated PowerShell (Run as Administrator). Not run by Claude (no admin in session).
# Purpose (plan §2.4, layer 2): Ollama listens on 0.0.0.0:11434 so Docker containers can reach it via
# host.docker.internal. These rules (1) allow inbound 11434 ONLY from Docker/WSL private ranges and loopback,
# (2) block all OUTBOUND traffic from ollama.exe except loopback — model pulls are done with the rule
# temporarily disabled (see -Pull switch).
param([switch]$Pull)

$ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if (-not (Test-Path $ollama)) { $ollama = (Get-Command ollama -ErrorAction Stop).Source }

if ($Pull) {
    Disable-NetFirewallRule -DisplayName "EO-Analyst Ollama outbound block" -ErrorAction SilentlyContinue
    Write-Host "Outbound block disabled. Pull models now, then re-run this script without -Pull."
    exit 0
}

Remove-NetFirewallRule -DisplayName "EO-Analyst Ollama inbound (docker only)" -ErrorAction SilentlyContinue
Remove-NetFirewallRule -DisplayName "EO-Analyst Ollama outbound block" -ErrorAction SilentlyContinue

New-NetFirewallRule -DisplayName "EO-Analyst Ollama inbound (docker only)" -Direction Inbound -Program $ollama `
    -Protocol TCP -LocalPort 11434 -RemoteAddress 127.0.0.1,172.16.0.0/12,192.168.0.0/16,10.0.0.0/8 -Action Allow -Profile Any | Out-Null
New-NetFirewallRule -DisplayName "EO-Analyst Ollama inbound block (rest)" -Direction Inbound -Program $ollama `
    -Protocol TCP -LocalPort 11434 -Action Block -Profile Any | Out-Null
New-NetFirewallRule -DisplayName "EO-Analyst Ollama outbound block" -Direction Outbound -Program $ollama `
    -RemoteAddress Internet -Action Block -Profile Any | Out-Null
Write-Host "Firewall rules installed for $ollama"
Get-NetFirewallRule -DisplayName "EO-Analyst*" | Select-Object DisplayName, Direction, Action, Enabled
