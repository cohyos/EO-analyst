<#
.SYNOPSIS
    Verifies the `agent` container's network isolation posture.

.DESCRIPTION
    Runs two checks inside the running `agent` container via
    `docker compose exec`:

      1. httpx.get('https://example.com', timeout=5)
         Expected to FAIL — agent's DNS is pinned to 0.0.0.0 (see
         docker-compose.yml), so arbitrary internet hostnames cannot resolve.

      2. httpx.get('http://host.docker.internal:11434/api/tags')
         Expected to SUCCEED — host.docker.internal is a static /etc/hosts
         entry (extra_hosts: host-gateway), routed via the dedicated
         `hostlink` network, and does not depend on DNS. Requires Ollama to
         actually be running on the Windows host at :11434.

    KNOWN LIMITATION (see docker-compose.yml's top-of-file comment): this
    proves hostname-based egress is blocked, not that all egress is blocked.
    A process that connects to a hardcoded IP literal instead of a hostname
    can still reach the internet through the `hostlink` bridge's NAT path —
    Docker Desktop has no compose-level primitive to prevent that. Treat a
    PASS here as "DNS-layer isolation confirmed", not "network-layer
    isolation confirmed".

.EXAMPLE
    ./scripts/verify_isolation.ps1
#>

param(
    [string]$Service = "agent"
)

$ErrorActionPreference = "Continue"
$failures = 0

function Test-Case {
    param(
        [string]$Name,
        [string]$PyCode,
        [ValidateSet("ExpectFail", "ExpectSuccess")]
        [string]$Expectation
    )

    Write-Host ""
    Write-Host "== $Name ==" -ForegroundColor Cyan
    Write-Host "  expecting: $Expectation"

    # Run the command and capture exit code
    & docker compose exec -T $Service python -c $PyCode
    $exitCode = $LASTEXITCODE

    if ($Expectation -eq "ExpectFail") {
        if ($exitCode -ne 0) {
            Write-Host "  PASS (call failed as expected, exit $exitCode)" -ForegroundColor Green
        }
        else {
            Write-Host "  FAIL (call SUCCEEDED - internet egress is not blocked!)" -ForegroundColor Red
            $script:failures++
        }
    }
    else {
        if ($exitCode -eq 0) {
            Write-Host "  PASS (call succeeded as expected)" -ForegroundColor Green
        }
        else {
            $msg = "  FAIL (call failed, exit $exitCode - check Ollama is running on the host at :11434, and that docker compose up has started $Service)"
            Write-Host $msg -ForegroundColor Red
            $script:failures++
        }
    }
}

Write-Host "Verifying network isolation for service '$Service'..." -ForegroundColor Yellow

Test-Case `
    -Name "Public internet (should be unreachable)" `
    -PyCode "import httpx; httpx.get('https://example.com', timeout=5)" `
    -Expectation "ExpectFail"

Test-Case `
    -Name "Host Ollama via host.docker.internal (should be reachable)" `
    -PyCode "import httpx; httpx.get('http://host.docker.internal:11434/api/tags', timeout=5).raise_for_status()" `
    -Expectation "ExpectSuccess"

Write-Host ""
if ($failures -eq 0) {
    Write-Host "All isolation checks passed." -ForegroundColor Green
    exit 0
}
else {
    Write-Host "$failures isolation check(s) FAILED. See docker-compose.yml's network isolation note and docs/MODULES.md." -ForegroundColor Red
    exit 1
}
