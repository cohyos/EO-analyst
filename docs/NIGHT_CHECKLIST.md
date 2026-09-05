# Night-run checklist (before 01:00 Asia/Jerusalem)

Run from the repo root on the host. As of 2026-09-05 (ADR-004) the stack runs natively on
Windows — no Docker. See `docs/RUNBOOK.md` § "Native (Windows) Operations" for the full
reference; the old Docker Compose version of this checklist is preserved in git history
(pre-2026-09-05 commits) if you're ever restoring that topology.

1. **Services up** — `eo native status` shows postgres, ntfy, orchestrator, and api all
   running (reads `runtime\pids\*.pid`). If not: `eo native start`.
2. **Ollama** — `curl 127.0.0.1:11434/api/version` answers; `ollama ps` may be empty. Network
   isolation between the orchestrator process and the internet no longer exists natively
   (ADR-004 §3-4) — there is nothing to `verify_isolation` against; the guard/SSRF/allow-list
   layers in `agent/eoa/security/` are the primary defense now.
3. **Scheduler registered** — `eo native logs orchestrator | Select-String orchestrator_started`;
   jobs `daily`, `pre_flight`, `daytime_poll`, `weekly`, `wake_guard` appear in the log at start.
4. **Host** — machine on AC; scheduled task "EO-Analyst Wake" exists (`Get-ScheduledTask -TaskName 'EO-Analyst Wake'`);
   scheduled task "EO-Analyst Supervisor" exists and is enabled (`Get-ScheduledTask -TaskName 'EO-Analyst Supervisor'`,
   registered via `scripts\native\register_autostart.ps1`); free RAM ≥ 8 GB expected at night
   (the gate defers below `resources.min_free_ram_mb`).
5. **Disk** — `eo status` shows disk free > 40 GB.
6. **Queue clean** — no stale `running` jobs: connect with
   `runtime\pgsql\bin\psql -h 127.0.0.1 -p 5432 -U eoa -d eoanalyst` and run
   `select id,kind,state,started_at from jobs where state='running';`
   (reset with `update jobs set state='failed', error='stale' where state='running' and started_at < now()-interval '6 hours'`).
7. **Notifications** — self-hosted ntfy reachable (`curl 127.0.0.1:8091/v1/health`); pidfile
   present at `runtime\pids\ntfy.pid` and `eo native status` shows it alive; phone subscribed to
   `http://100.70.157.25:8091/eo-analyst` or `notify.mirror_to_public: true`.

## What happens overnight
| time | event |
|---|---|
| 23:30 | pre-flight: service checks, warm-up of the resident model, status ping |
| 00:55 | wake guard log line |
| 01:00 | `daily_run` job enqueued → worker: ingest (in-process, `EOA_ROLE=host`) → embed/dedup → classify → triage → deep search (≤4) → analyze → report → export/backup → notify |
| ≤06:05 | hard deadline; report + notify always run |

## Morning
- Report: `output/reports/daily_YYYY-MM-DD.docx` (+ .md/.html); UI "הבוקר" at http://127.0.0.1:8765.
- Run stats: `select result from jobs where kind='daily_run' order by id desc limit 1;`
- Stage log: `select stage,event,detail from run_log where job_id=<id> order by id;`
