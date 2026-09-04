#!/usr/bin/env bash
# Host-side catch-up: repeat pipeline stages until nothing is left or 12 rounds, tolerating gate deferrals.
cd "$(dirname "$0")/.." || exit 1
export DATABASE_URL="${DATABASE_URL:-postgresql://eoa:change-me-local-only@127.0.0.1:5433/eoanalyst}" PYTHONPATH=agent PYTHONIOENCODING=utf-8 SEARXNG_URL="${SEARXNG_URL:-http://127.0.0.1:8088}" NTFY_URL="${NTFY_URL:-http://127.0.0.1:8090}"
for i in $(seq 1 12); do
  echo "=== round $i $(date) ==="
  python -m eoa.cli run classify 2>&1 | grep -E '"event": "(classify_done|classify_deferred_resources)"' | tail -1
  python -m eoa.cli run triage   2>&1 | grep -E '"event": "(triage_done|triage_deferred_resources)"' | tail -1
  python -m eoa.cli run analyze  2>&1 | grep -E '"event": "(analyze_done|analyze_deferred_resources)"' | tail -1
  left=$(docker compose exec -T postgres psql -U eoa -d eoanalyst -Atc "select count(*) from items where security_status='clean' and dedup_of is null and not ('analyze' = any(coalesce(processed_stages,'{}')))")
  echo "items not yet analyzed: $left"
  [ "${left:-1}" -eq 0 ] && break
  sleep 600
done

# after catch-up: golden-set evals (ADR-001 validation)
PYTHONIOENCODING=utf-8 python evals/run_evals.py --set-name classify_triage --role resident > output/logs/evals_run.log 2>&1

# first real daily report from the catch-up results
python -m eoa.cli run report > output/logs/first_report.log 2>&1
