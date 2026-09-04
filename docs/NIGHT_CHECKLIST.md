# Night-run checklist (before 01:00 Asia/Jerusalem)

Run from the repo root on the host.

1. **Services up** — `docker compose ps` shows postgres (healthy), searxng, ntfy, fetcher, agent, web. If not: `docker compose up -d`.
2. **Ollama** — `curl 127.0.0.1:11434/api/version` answers; `ollama ps` may be empty. From a container:
   `bash scripts/verify_isolation.sh` → both checks PASS.
3. **Scheduler registered** — `docker compose logs agent | grep orchestrator_started`; jobs `daily`, `pre_flight`,
   `daytime_poll`, `weekly`, `wake_guard` appear in the log at start.
4. **Host** — machine on AC; scheduled task "EO-Analyst Wake" exists (`Get-ScheduledTask -TaskName 'EO-Analyst Wake'`);
   free RAM ≥ 8 GB expected at night (the gate defers below `resources.min_free_ram_mb`).
5. **Disk** — `eo status` shows disk free > 40 GB.
6. **Queue clean** — no stale `running` jobs: `select id,kind,state,started_at from jobs where state='running';`
   (reset with `update jobs set state='failed', error='stale' where state='running' and started_at < now()-interval '6 hours'`).
7. **Notifications** — self-hosted ntfy reachable (`curl 127.0.0.1:8090/v1/health`), relay running in fetcher
   (`docker compose logs fetcher | grep ntfy_relay_start`), phone subscribed to `http://100.70.157.25:8090/eo-analyst`
   or `notify.mirror_to_public: true`.

## What happens overnight
| time | event |
|---|---|
| 23:30 | pre-flight: service checks, warm-up of the resident model, status ping |
| 00:55 | wake guard log line |
| 01:00 | `daily_run` job enqueued → worker: ingest (via fetcher) → embed/dedup → classify → triage → deep search (≤4) → analyze → report → export/backup → notify |
| ≤06:05 | hard deadline; report + notify always run |

## Morning
- Report: `output/reports/daily_YYYY-MM-DD.docx` (+ .md/.html); UI "הבוקר" at http://127.0.0.1:8765.
- Run stats: `select result from jobs where kind='daily_run' order by id desc limit 1;`
- Stage log: `select stage,event,detail from run_log where job_id=<id> order by id;`
