#!/usr/bin/env bash
# Wait until free RAM stays above 10 GB for two consecutive minutes, then run the catch-up loop.
cd "$(dirname "$0")/.." || exit 1
ok=0
while true; do
  free=$(powershell -NoProfile -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)")
  if [ "${free:-0}" -gt 10000 ]; then ok=$((ok+1)); else ok=0; fi
  [ "$ok" -ge 2 ] && break
  sleep 60
done
echo "RAM ok (${free} MB) at $(date) — starting catch-up"
bash scripts/catchup.sh
