"""Scheduler entrypoint: night-window daily run, pre-flight, daytime RSS polling, weekly/monthly jobs,
plus the job worker. ``python -m eoa.orchestrator.main``.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from datetime import UTC, datetime
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
        lambda: enqueue_job("weekly_run", {"mode": "full"}, priority=2),
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
    return sched


def wake_guard() -> None:
    """Log the clock at 00:55 so run_log shows whether the machine was awake before the window."""
    log.info("wake_guard", now=datetime.now(tz=UTC).isoformat())


def main() -> None:
    configure_logging()
    s = settings()
    worker = Worker()
    worker.start()
    sched = build_scheduler()
    sched.add_job(wake_guard, _cron(ZoneInfo(s.timezone), "00:55"), id="wake_guard")
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
