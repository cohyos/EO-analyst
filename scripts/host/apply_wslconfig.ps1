# Apply %USERPROFILE%\.wslconfig (RAM/CPU caps for the Docker Desktop VM). Run in YOUR terminal when no
# EO-Analyst night run is active (it stops Docker for ~1 minute). Not run by Claude.
param([switch]$NoRestart)

Write-Host "Current .wslconfig:"; Get-Content "$env:USERPROFILE\.wslconfig"
Write-Host "Shutting down WSL (Docker Desktop will restart its engine automatically)..."
wsl --shutdown
Start-Sleep -Seconds 8
if (-not $NoRestart) {
    $docker = "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (-not (Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue) -and (Test-Path $docker)) {
        Start-Process $docker
    }
    Write-Host "Waiting for the Docker engine..."
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) { if ((docker info 2>$null) -and $LASTEXITCODE -eq 0) { $ok = $true; break }; Start-Sleep 3 }
    if ($ok) {
        Set-Location (Split-Path $PSScriptRoot -Parent | Split-Path -Parent)
        docker compose up -d
        docker compose ps
    } else { Write-Warning "Docker engine did not come back within 3 minutes; open Docker Desktop manually, then run: docker compose up -d" }
}
Write-Host "WSL memory cap now:"; wsl -d docker-desktop -- sh -c "free -g | head -2" 2>$null
