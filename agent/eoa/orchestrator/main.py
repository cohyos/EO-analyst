"""Scheduler entrypoint: night-window daily run, pre-flight, daytime RSS polling, weekly/monthly jobs,
plus the job worker. ``python -m eoa.orchestrator.main``.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from eoa.config import settings
from eoa.memory.relational import enqueue_job
from eoa.notify import ntfy
from eoa.orchestrator.jobs import Worker, enqueue_daily

log = structlog.get_logger(__name__)


def configure_logging(level: str = "INFO") -> None:
    """JSON logs to stdout (containers) — human-readable when a TTY is attached."""
    # Redirected Windows streams may default to cp1252, which cannot encode Hebrew.
    # Configure both before binding loggers; diagnostic output must not abort a stage.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s")
    renderer = structlog.dev.ConsoleRenderer() if sys.stdout.isatty() else structlog.processors.JSONRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(),
    )


def pre_flight() -> dict:
    """23:30 checks: services, disk/thermal, warm-up of the resident model, backup. Sends a status ping."""
    from eoa import db
    from eoa.llm import ollama_client
    from eoa.memory.relational import reap_stale_jobs
    from eoa.resources.gate import gate
    from eoa.search.provider import ping as searx_ping

    st = gate().status()
    try:
        reaped = reap_stale_jobs()
    except Exception as exc:
        log.warning("pre_flight_reap_failed", error=str(exc)[:160])
        reaped = 0
    checks = {
        "postgres": db.ping(),
        "ollama": ollama_client.ping(),
        "searxng": searx_ping(),
        "disk_free_gb": st["disk_free_gb"],
        "gpu_temp": st["gpu"]["temp_c"],
        "vram_free_mb": st["gpu"]["vram_free_mb"],
        "reaped_stale_jobs": reaped,
    }
    problems = [k for k in ("postgres", "ollama") if not checks[k]]
    if checks["disk_free_gb"] < settings().resources.warn_free_disk_gb:
        problems.append(f"disk {checks['disk_free_gb']} GB")
    try:
        if st.get("local_inference_paused"):
            checks["warm_up"] = "paused"
            log.info("pre_flight", **checks)
            if problems:
                ntfy.status("pre-flight: בעיות — " + "; ".join(problems), priority="high")
            return checks
        ollama_client.warm_up("resident")
        checks["warm_up"] = True
    except Exception as exc:
        checks["warm_up"] = False
        problems.append(f"warm-up: {str(exc)[:80]}")
    log.info("pre_flight", **checks)
    if problems:
        ntfy.status("pre-flight: בעיות — " + "; ".join(problems), priority="high")
    return checks


def _cron(tz: ZoneInfo, hhmm: str, **extra: str) -> CronTrigger:
    h, m = hhmm.split(":")
    return CronTrigger(hour=int(h), minute=int(m), timezone=tz, **extra)


def build_scheduler() -> BackgroundScheduler:
    s = settings()
    tz = ZoneInfo(s.timezone)
    sched = BackgroundScheduler(timezone=tz)
    sched.add_job(
        lambda: enqueue_daily("full", priority=2),
        _cron(tz, s.schedule.night_window.start),
        id="daily",
        name="daily run",
        misfire_grace_time=3600,
        coalesce=True,
    )
    sched.add_job(
        pre_flight,
        _cron(tz, s.schedule.pre_flight_at),
        id="pre_flight",
        misfire_grace_time=1800,
        coalesce=True,
    )
    sched.add_job(
        lambda: enqueue_job("ingest", {"mode": "poll"}, priority=6),
        CronTrigger(minute=0, hour=f"*/{max(s.schedule.daytime_rss_poll_minutes // 60, 1)}", timezone=tz),
        id="daytime_poll",
        coalesce=True,
    )
    wk = s.schedule.weekly_run
    sched.add_job(
        # F22 (audit 2026-09-24): a numerically worse priority than "daily" (2) -- not equal --
        # so that when both land in the queue at the same 01:00 tick (APScheduler's default
        # executor can run same-tick jobs concurrently, so insertion order into `jobs` is not
        # guaranteed), `claim_next_job`'s `ORDER BY priority ASC` picks `daily_run` first as long
        # as both rows already exist by the time a worker polls -- which they will, well within
        # the worker's 10s poll interval. `run_weekly`'s own `_daily_run_already_covered` check
        # (now also treating a merely `queued` daily_run as covered) is the actual correctness
        # guard against a double pipeline run; this priority is defense in depth for the common
        # case, reducing how often that check's rarer "weekly claimed first" branch is exercised.
        lambda: enqueue_job("weekly_run", {"mode": "full"}, priority=3),
        _cron(tz, wk.get("start", "01:00"), day_of_week=wk.get("weekday", "sat")),
        id="weekly",
        coalesce=True,
    )
    mo_day = s.schedule.monthly_run.get("day", 1)
    sched.add_job(
        lambda: enqueue_job("conference_scan", {}, priority=4),
        CronTrigger(day=mo_day, hour=2, minute=30, timezone=tz),
        id="conference_scan",
        name="monthly conference tracker scan (FR-12.3)",
        misfire_grace_time=3600,
        coalesce=True,
    )
    sched.add_job(
        lambda: enqueue_job("monthly_run", {}, priority=2),
        CronTrigger(day=mo_day, hour=3, minute=30, timezone=tz),
        id="monthly",
        name="monthly report (FR-5.4: נוף תחרותי מלא ומפת שחקנים)",
        misfire_grace_time=3600,
        coalesce=True,
    )

    # FR-11.4: transparency loop — what changed because of the user's feedback (Saturday morning)
    def _weekly_meta() -> None:
        try:
            from eoa.feedback.calibration import calibrate
            from eoa.feedback.meta import post_weekly_meta

            calibrate()
            post_weekly_meta()
        except Exception as exc:
            log.warning("weekly_meta_failed", error=str(exc)[:160])

    sched.add_job(
        _weekly_meta,
        _cron(tz, "06:30", day_of_week=wk.get("weekday", "sat")),
        id="weekly_meta",
        coalesce=True,
    )

    # FR-12.4: conference reminders every morning
    def _reminders() -> None:
        try:
            from eoa.conferences.reminders import send_reminders

            send_reminders()
        except Exception as exc:
            log.warning("conference_reminders_failed", error=str(exc)[:160])

    sched.add_job(_reminders, _cron(tz, "07:00"), id="conference_reminders", coalesce=True)

    # A11: weekly BD-by-territory reports for the configured default territory set
    # (config/config.yaml `bd_report.territories`) -- no `territory` in the payload, which is how
    # `eoa.orchestrator.jobs.run_bd_report` distinguishes this loop-over-defaults run from an
    # on-demand single-territory run (`eoa.api.services.enqueue_bd_report`).
    sched.add_job(
        lambda: enqueue_job("bd_report", {}, priority=4),
        _cron(tz, "06:30", day_of_week="sun"),
        id="bd_report_weekly",
        name="דוח מיקוד לפיתוח עסקי לפי טריטוריה -- שבועי (A11)",
        misfire_grace_time=3600,
        coalesce=True,
    )
    # PL-backend (user request 2026-09-07): weekly product-line status & business-development
    # reports for every configured product line (config/product_lines.yaml) -- no `line_id` in the
    # payload, mirroring `bd_report_weekly`'s own convention (eoa.orchestrator.jobs.
    # run_product_line_report distinguishes this loop-over-all-lines run from an on-demand
    # single-line run, eoa.api.services.enqueue_product_line_report). Offset 15 minutes after the
    # BD-territory weekly job (06:30) so the two don't contend for the resident model at once.
    sched.add_job(
        lambda: enqueue_job("product_line_report", {}, priority=4),
        _cron(tz, "06:45", day_of_week="sun"),
        id="product_line_report_weekly",
        name="דוח מעקב קו מוצר -- שבועי (PL-backend)",
        misfire_grace_time=3600,
        coalesce=True,
    )
    # A14: weekly patent/IP scan (config/patents.yaml: schedule.weekday/start, default Tue 05:30).
    sched.add_job(
        lambda: enqueue_job("patent_scan", {}, priority=4),
        _cron(tz, "05:30", day_of_week="tue"),
        id="patent_scan_weekly",
        name="פטנטים ו-IP -- סריקה שבועית (A14)",
        misfire_grace_time=3600,
        coalesce=True,
    )

    # A17: optional nightly EO-payload spec/price extraction scan (config/config.yaml
    # `payloads.enabled`, default true) -- job kind/API/UI exist regardless of this flag; it only
    # controls whether the scan runs unattended every night.
    if s.payloads.enabled:
        sched.add_job(
            lambda: enqueue_job("payload_extract", {"limit": s.payloads.nightly_limit}, priority=5),
            CronTrigger(hour=4, minute=15, timezone=tz),
            id="payload_extract_nightly",
            name='מטע"דים -- סריקת מפרט/מחיר לילית (A17)',
            misfire_grace_time=3600,
            coalesce=True,
        )

    return sched


def wake_guard() -> None:
    """Log the clock at 00:55 so run_log shows whether the machine was awake before the window."""
    log.info("wake_guard", now=datetime.now(tz=UTC).isoformat())


def reconcile_missed_night_run() -> None:
    """F07 (audit 2026-09-24): a restart just after the 01:00 cron fire (crash, update, supervisor
    bounce) used to skip that night's run outright -- `misfire_grace_time` only covers a fire
    missed *while the scheduler wasn't running yet*, and once the process is back up, APScheduler
    computes the "daily" job's next fire time as tomorrow's 01:00, so nothing catches up tonight's
    pipeline. Called once at startup (`main()`, right after the scheduler is built but before it
    starts): if it is currently inside the night window (`schedule.night_window`, Asia/Jerusalem)
    and no `daily_run`/`weekly_run` job has been created since that window opened tonight, enqueue
    a daily run now -- the same job the 01:00 cron itself would have queued."""
    s = settings()
    tz = ZoneInfo(s.timezone)
    now = datetime.now(tz)
    sh, sm = (int(x) for x in s.schedule.night_window.start.split(":"))
    eh, em = (int(x) for x in s.schedule.night_window.end.split(":"))
    window_start = now.replace(hour=sh, minute=sm, second=0, microsecond=0)
    window_end = now.replace(hour=eh, minute=em, second=0, microsecond=0)
    if window_end <= window_start:  # a window that crosses midnight
        window_end += timedelta(days=1)
    if window_start > now:  # window hasn't opened yet today -- it opened yesterday instead
        window_start -= timedelta(days=1)
        window_end -= timedelta(days=1)
    if not (window_start <= now < window_end):
        return
    from eoa import db

    try:
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM jobs WHERE kind IN ('daily_run', 'weekly_run') "
                "AND created_at >= %(start)s LIMIT 1",
                {"start": window_start.astimezone(UTC)},
            )
            covered = cur.fetchone() is not None
    except Exception as exc:
        log.warning("reconcile_missed_night_run_check_failed", error=str(exc)[:160])
        return
    if covered:
        return
    job_id = enqueue_daily("full", priority=2)
    log.warning(
        "reconcile_missed_night_run_enqueued", job_id=job_id, window_start=window_start.isoformat()
    )


def main() -> None:
    configure_logging()
    s = settings()
    worker = Worker()
    worker.start()
    sched = build_scheduler()
    sched.add_job(wake_guard, _cron(ZoneInfo(s.timezone), "00:55"), id="wake_guard")
    try:
        reconcile_missed_night_run()
    except Exception as exc:
        log.warning("reconcile_missed_night_run_failed", error=str(exc)[:160])
    sched.start()
    log.info(
        "orchestrator_started",
        night=f"{s.schedule.night_window.start}-{s.schedule.night_window.end}",
        tz=s.timezone,
    )
    ntfy.status("orchestrator up — ממתין לחלון הלילה", priority="min")

    stop = threading.Event()

    def _sig(*_: object) -> None:
        stop.set()

    # Windows note: there is no asyncio event loop here at all (BackgroundScheduler is
    # thread-based, not AsyncIOScheduler), so `loop.add_signal_handler` -- unsupported on
    # Windows' ProactorEventLoop -- never enters the picture. Plain `signal.signal()` for
    # SIGINT and SIGTERM is accepted without error on Windows too (both are in the small set
    # CPython documents as settable there), but SIGTERM's handler is effectively dead code on
    # Windows: `os.kill(pid, signal.SIGTERM)` (and PowerShell's `Stop-Process`) call
    # TerminateProcess() directly, which kills the process without ever invoking a registered
    # handler. That's why scripts/native/eoa-supervisor.ps1 stops this process with
    # `Stop-Process` + a `runtime\supervisor.stop` sentinel it manages itself, not SIGTERM.
    # SIGINT (Ctrl+C) and, on Windows only, SIGBREAK (Ctrl+Break) *are* delivered as real
    # console control events and do invoke Python handlers, so they're kept here for
    # interactive `eo orchestrate` runs.
    sigs: list[int] = [signal.SIGINT, signal.SIGTERM]
    if sys.platform == "win32" and hasattr(signal, "SIGBREAK"):
        sigs.append(signal.SIGBREAK)  # type: ignore[attr-defined]
    for sig in sigs:
        try:
            signal.signal(sig, _sig)
        except (ValueError, OSError):
            pass
    while not stop.is_set():
        time.sleep(1)
    sched.shutdown(wait=False)
    worker.stop()
    log.info("orchestrator_stopped")


if __name__ == "__main__":
    main()
