#!/usr/bin/env bash
# scripts/verify_isolation.sh
#
# Verifies the `agent` container's network isolation posture. Mirrors
# scripts/verify_isolation.ps1 (see its header comment for the full
# explanation, including the known limitation of this approach). Intended
# for use inside WSL2 / a Linux shell; on native Windows PowerShell use
# verify_isolation.ps1 instead.
#
# Usage: scripts/verify_isolation.sh [service]   (default service: agent)

set -u
SERVICE="${1:-agent}"
FAILURES=0

echo "Verifying network isolation for service '${SERVICE}'..."

run_case() {
    local name="$1"
    local py_code="$2"
    local expectation="$3"   # "fail" or "success"

    echo
    echo "== ${name} =="
    echo "  expecting: ${expectation}"

    if docker compose exec -T "${SERVICE}" python -c "${py_code}"; then
        exit_code=0
    else
        exit_code=$?
    fi

    if [ "${expectation}" = "fail" ]; then
        if [ "${exit_code}" -ne 0 ]; then
            echo "  PASS (call failed as expected, exit ${exit_code})"
        else
            echo "  FAIL (call SUCCEEDED - internet egress is not blocked!)"
            FAILURES=$((FAILURES + 1))
        fi
    else
        if [ "${exit_code}" -eq 0 ]; then
            echo "  PASS (call succeeded as expected)"
        else
            echo "  FAIL (call failed, exit ${exit_code} - check Ollama is running on the host at :11434, and that 'docker compose up' has started ${SERVICE})"
            FAILURES=$((FAILURES + 1))
        fi
    fi
}

run_case \
    "Public internet (should be unreachable)" \
    "import httpx; httpx.get('https://example.com', timeout=5)" \
    "fail"

run_case \
    "Host Ollama via host.docker.internal (should be reachable)" \
    "import httpx; httpx.get('http://host.docker.internal:11434/api/tags', timeout=5).raise_for_status()" \
    "success"

echo
if [ "${FAILURES}" -eq 0 ]; then
    echo "All isolation checks passed."
    exit 0
else
    echo "${FAILURES} isolation check(s) FAILED. See docker-compose.yml's network isolation note and docs/MODULES.md."
    exit 1
fi
