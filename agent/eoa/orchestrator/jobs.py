"""Job runner: executes queued jobs (daily_run, ingest, deep_search, report, weekly_run,
monthly_run) with deadline budgeting, heartbeats, carry-over and a circuit breaker per stage.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from eoa.config import settings
from eoa.errors import DeadlineExceeded, ResourceUnavailable
from eoa.memory.relational import claim_next_job, enqueue_job, finish_job, heartbeat, reap_stale_jobs
from eoa.notify import ntfy

log = structlog.get_logger(__name__)

# U8 (docs/adr/005-cloud-llm-cli.md): every LLM call made anywhere in this process runs inside
# the orchestrator/worker -- the night pipeline (daily/weekly/monthly/ingest cron jobs, built
# below) *and* every job the API enqueues onto the same queue, including a manually triggered
# "investigate" (POST /api/items/{id}/investigate -> a `deep_search` job) or "run now"
# (POST /api/run). `eoa.llm.ollama_client.chat`/`chat_structured` check this before honoring
# any `provider` argument or the user's `llm_providers.interactive_default` setting, and force
# "ollama" whenever it is set -- this is the single choke point that keeps a cloud-model choice
# from ever leaking into an automated or queued run. `setdefault` so a test harness that needs
# to opt a single process out can still set the env var before importing this module.
os.environ.setdefault("EOA_PIPELINE", "1")


def _worker_id() -> str:
    """A stable-enough identifier for this process, used as the job lease owner."""
    return f"{socket.gethostname()}:{os.getpid()}"


STAGE_ORDER = [
    "ingest",
    "embed_dedup",
    "classify",
    "triage",
    "deep_search",
    "analyze",
    "tenders",
    "report",
    "export_backup",
    "notify",
]


@dataclass
class RunState:
    job_id: int
    started: float = field(default_factory=time.monotonic)
    deadline: float | None = None  # monotonic; None = no hard deadline (on-demand runs)
    stage: str = ""
    stats: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    mode: str = "full"  # full | eco

    def time_left_min(self) -> float | None:
        return None if self.deadline is None else max((self.deadline - time.monotonic()) / 60, 0)

    def budget_min(self, stage: str) -> float | None:
        """Minutes this stage may take: configured budget, capped by what is left before the deadline
        minus a reserve for the mandatory report+notify stages."""
        cfg = settings().stages.get(stage, 30)
        left = self.time_left_min()
        if left is None:
            return float(cfg)
        reserve = (
            0 if stage in {"report", "export_backup", "notify"} else settings().stages.get("report", 30) + 10
        )
        return max(min(cfg, left - reserve), 0)


def _night_deadline() -> float:
    """Monotonic timestamp of the next night-window end (Asia/Jerusalem)."""
    s = settings()
    tz = ZoneInfo(s.timezone)
    now = datetime.now(tz)
    h, m = (int(x) for x in s.schedule.night_window.end.split(":"))
    end = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if end <= now:
        end += timedelta(days=1)
    grace = timedelta(minutes=s.schedule.deadline_grace_minutes)
    return time.monotonic() + (end + grace - now).total_seconds()


def _hb(rs: RunState, event: str, **detail: Any) -> None:
    rs.stage = detail.get("stage", rs.stage)
    try:
        heartbeat(rs.job_id, rs.stage, event, detail)
    except Exception as exc:
        log.debug("heartbeat_failed", error=str(exc)[:100])


def _run_stage(rs: RunState, stage: str, fn: Callable[[], Any], *, mandatory: bool = False) -> Any:
    """Run one stage with budget check, heartbeat, error isolation and circuit breaker."""
    budget = rs.budget_min(stage)
    if budget is not None and budget <= 0 and not mandatory:
        _hb(rs, "skipped_no_time", stage=stage)
        log.warning("stage_skipped_no_time", stage=stage)
        rs.stats[stage] = {"skipped": "no_time"}
        return None
    if rs.failures.get(stage, 0) >= 3:
        _hb(rs, "skipped_circuit_open", stage=stage)
        rs.stats[stage] = {"skipped": "circuit_open"}
        return None
    _hb(rs, "start", stage=stage, budget_min=budget)
    t0 = time.monotonic()
    try:
        out = fn()
        rs.stats[stage] = _as_dict(out) | {"minutes": round((time.monotonic() - t0) / 60, 1)}
        _hb(rs, "done", stage=stage, **rs.stats[stage])
        return out
    except ResourceUnavailable as exc:
        rs.stats[stage] = {"deferred": str(exc)[:200], "minutes": round((time.monotonic() - t0) / 60, 1)}
        _hb(rs, "deferred", stage=stage, error=str(exc)[:200])
        log.warning("stage_deferred", stage=stage, error=str(exc))
        return None
    except DeadlineExceeded:
        rs.stats[stage] = {"partial": "deadline"}
        _hb(rs, "deadline", stage=stage)
        return None
    except Exception as exc:
        rs.failures[stage] = rs.failures.get(stage, 0) + 1
        rs.stats[stage] = {"error": str(exc)[:300]}
        _hb(rs, "error", stage=stage, error=str(exc)[:300], trace=traceback.format_exc()[-1500:])
        log.error("stage_failed", stage=stage, error=str(exc))
        if rs.failures[stage] >= 3:
            ntfy.failure(stage, f"{exc}"[:300])
        return None


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in vars(obj).items() if isinstance(v, int | float | str | bool)}
    return {"result": str(obj)[:200]}


# ----------------------------------------------------------------------------- job kinds
def run_daily(job: dict[str, Any], *, night: bool | None = None) -> dict[str, Any]:
    """The full daily cycle. ``night`` forces/unforces the hard deadline; default = is it the night window."""
    from eoa.resources.gate import gate

    payload = job.get("payload") or {}
    rs = RunState(job_id=job["id"], mode=payload.get("mode", "full"))
    is_night = gate().is_batch_window() if night is None else night
    rs.deadline = _night_deadline() if is_night else None
    if is_night:
        gate().force_night_mode = True
    role = "light" if rs.mode == "eco" and settings().has_model("light") else "resident"
    log.info("daily_run_start", job_id=job["id"], night=is_night, mode=rs.mode, role=role)
    try:
        _run_stage(rs, "ingest", _ingest)
        _run_stage(
            rs, "embed_dedup", lambda: __import__("eoa.pipeline.dedup", fromlist=["run_dedup"]).run_dedup()
        )
        _run_stage(
            rs,
            "classify",
            lambda: __import__("eoa.pipeline.classify", fromlist=["run_classify"]).run_classify(role=role),
        )
        _run_stage(
            rs,
            "dedup_xlang",
            lambda: {
                "linked": __import__(
                    "eoa.pipeline.dedup", fromlist=["link_cross_language"]
                ).link_cross_language()
            },
        )
        _run_stage(
            rs,
            "triage",
            lambda: __import__("eoa.pipeline.triage", fromlist=["run_triage"]).run_triage(role=role),
        )
        if rs.mode == "full":
            _run_stage(rs, "deep_search", lambda: run_deep_searches(rs))
        _run_stage(
            rs,
            "analyze",
            lambda: __import__("eoa.pipeline.analyze", fromlist=["run_analyze"]).run_analyze(role=role),
        )
        _run_stage(rs, "tenders", lambda: _as_dict(_run_tenders(role=role)))
        paths = _run_stage(rs, "report", _build_report, mandatory=True)
        _run_stage(rs, "export_backup", _backup, mandatory=True)
        _run_stage(rs, "notify", lambda: _notify(rs, paths), mandatory=True)
    finally:
        gate().force_night_mode = False
    rs.stats["total_minutes"] = round((time.monotonic() - rs.started) / 60, 1)
    rs.stats["status"] = _compute_run_status(rs.stats)
    log.info("daily_run_done", job_id=job["id"], stats=rs.stats)
    return rs.stats


def _compute_run_status(stats: dict[str, Any]) -> str:
    """Terminal `done`/`partial`/`failed` status from a `run_daily` stats dict's per-stage outcomes.

    `done` requires the mandatory `report` stage to have actually produced a report (no
    `error`/`deferred`/`skipped` marker on it) *and* no other stage recorded a problem
    (`error`/`deferred`/`skipped`/`partial`). If the report itself is missing or failed, the whole
    run is `failed` regardless of anything else; otherwise a problem elsewhere is `partial`."""
    report_ok = isinstance(stats.get("report"), dict) and not {"error", "deferred", "skipped"} & set(
        stats["report"]
    )
    any_problem = any(
        isinstance(v, dict) and ({"error", "deferred", "skipped", "partial"} & set(v)) for v in stats.values()
    )
    return "done" if report_ok and not any_problem else ("partial" if report_ok else "failed")


def _ingest() -> Any:
    """Ingest through the fetcher container when running as the isolated agent, else in-process
    (synchronous: ``run_ingest_remote`` already manages its own event loop internally, so calling
    it from inside another ``asyncio.run()`` would raise)."""
    from eoa.fetch.remote import run_ingest_remote

    return run_ingest_remote()


def _investigation_result_payload(inv: Any) -> dict[str, Any]:
    """U11/F17/F18 (docs/REVIEW_2026-09-05.md): merge the model's `InvestigationOut` with the
    budget/outcome accounting the API/UI need to show *why* an investigation ended the way it did
    (a bare `not_found` chip told the analyst nothing) instead of the raw model output alone."""
    payload: dict[str, Any] = inv.result.model_dump() if inv.result else {}
    payload.update(
        {
            "queries_used": inv.queries_used,
            "max_queries": inv.max_queries,
            "pages_read": inv.pages_used,
            "max_pages": inv.max_pages,
            "rounds": inv.rounds_done,
            "stopped_reason": inv.stopped_reason,
        }
    )
    return payload


def run_deep_searches(rs: RunState) -> dict[str, Any]:
    """Consume queued deep_search jobs (from triage) within this run's budget and the nightly cap."""
    from eoa.search.deep_search import investigate

    cap = settings().deep_search.max_per_night
    done, outcomes = 0, []
    per_min = settings().deep_search.per_investigation_timeout_min
    while done < cap:
        left = rs.time_left_min()
        if left is not None and left < per_min + settings().stages.get("report", 30) + 10:
            log.warning("deep_search_stop_time", left_min=round(left))
            break
        job = claim_next_job(["deep_search"], worker_id=_worker_id())
        if not job:
            break
        p = job.get("payload") or {}
        try:
            inv = investigate(
                p.get("question", ""),
                item_id=p.get("item_id"),
                job_id=job["id"],
                context_he=p.get("context_he", ""),
                budget_multiplier=float(p.get("budget_multiplier") or 1.0),
                prior_findings_he=p.get("prior_findings_he", ""),
            )
            finish_job(job["id"], "done", result=_investigation_result_payload(inv))
            outcomes.append(inv.outcome)
            if p.get("level") == "red" and inv.result and inv.result.outcome != "not_found":
                _red_alert_for(p.get("item_id"), inv.result.answer_he)
        except ResourceUnavailable as exc:
            # could not search at all (GPU/RAM) — keep the job for a later retry instead of
            # recording a fabricated not_found; retry after a cooldown rather than immediately
            finish_job(
                job["id"],
                "deferred",
                error=str(exc)[:300],
                not_before=datetime.now(tz=UTC) + timedelta(minutes=30),
            )
            outcomes.append("deferred")
            raise
        except Exception as exc:
            finish_job(job["id"], "failed", error=str(exc)[:400])
            outcomes.append("failed")
        done += 1
    return {"investigations": done, "outcomes": ",".join(outcomes)}


def run_deep_search_job(job: dict[str, Any]) -> dict[str, Any]:
    """A single on-demand investigation (from the UI/CLI). U12's "הרחב חקירה" (expand) sets
    `budget_multiplier`/`prior_findings_he` in the payload (see `eoa.api.services.expand_investigation`)
    to re-run with a larger budget and the prior attempt's findings folded in, instead of a
    plain identical re-run of the same question."""
    from eoa.search.deep_search import investigate

    p = job.get("payload") or {}
    inv = investigate(
        p.get("question", ""),
        item_id=p.get("item_id"),
        job_id=job["id"],
        context_he=p.get("context_he", ""),
        budget_multiplier=float(p.get("budget_multiplier") or 1.0),
        prior_findings_he=p.get("prior_findings_he", ""),
    )
    return _investigation_result_payload(inv)


def _red_alert_for(item_id: int | None, answer_he: str) -> None:
    if not item_id:
        return
    try:
        from eoa.db import connection

        with connection() as conn:
            row = conn.execute("SELECT title, url FROM items WHERE id=%s", (item_id,)).fetchone()
        if row:
            ntfy.red_alert(row["title"], answer_he[:300], row["url"])
    except Exception as exc:
        log.debug("red_alert_failed", error=str(exc)[:100])


def _build_report() -> Any:
    from eoa.report.daily import build_daily

    return build_daily()


def _daily_run_already_covered(within_hours: int = 6) -> bool:
    """F4: True if a separate ``daily_run`` job started/finished within the last ``within_hours``
    hours in a ``running``/``done``/``partial`` state. Both ``daily_run`` and ``weekly_run`` are
    scheduled for the same night (config ``schedule.weekly_run`` sat 01:00, same as the nightly
    ``daily_run``); without this guard, ``run_weekly`` unconditionally re-running the *entire*
    nightly pipeline (ingest..notify, including its own daily report + notification) produced two
    daily reports and duplicate notifications on Saturday nights."""
    from eoa.db import connection

    sql = """
        SELECT 1 FROM jobs
        WHERE kind = 'daily_run'
          AND state IN ('running', 'done', 'partial')
          AND created_at > now() - make_interval(hours => %(hours)s)
        LIMIT 1
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, {"hours": within_hours})
            return cur.fetchone() is not None
    except Exception as exc:
        log.warning("daily_run_covered_check_failed", error=str(exc)[:160])
        return False


def run_weekly(job: dict[str, Any]) -> dict[str, Any]:
    """``weekly_run`` handler: normally runs the full nightly pipeline (ingest..notify, including
    the daily report) via :func:`run_daily`, then additionally builds the weekly analyst report
    (trends, business events, conference lookahead, FR-11.4 meta-summary) on top of the same
    night's freshly-analyzed items. F4: when a separate ``daily_run`` job has already run (or is
    running) tonight (:func:`_daily_run_already_covered`), the nightly pipeline is *not* re-run
    here — only the weekly report is built, on top of whatever that other job already
    ingested/analyzed — since running it twice produced two daily reports and duplicate
    notifications. A weekly-report failure is logged and recorded but never fails the job outright —
    the (possibly skipped) daily pipeline's own results still count as the run's primary outcome."""
    if _daily_run_already_covered():
        log.info("weekly_run_skips_daily_pipeline", reason="daily_run_already_covered_tonight")
        stats: dict[str, Any] = {"daily_pipeline_skipped": "daily_run_already_covered"}
    else:
        stats = run_daily(job)
    try:
        from eoa.report.weekly import build_weekly

        paths = build_weekly()
        stats["weekly_report"] = {"report_id": paths.report_id, "qa_passed": paths.qa.passed}
    except Exception as exc:
        log.error("weekly_report_failed", error=str(exc)[:300])
        stats["weekly_report_error"] = str(exc)[:300]
    return stats


def run_monthly(job: dict[str, Any]) -> dict[str, Any]:
    """``monthly_run`` handler: builds the monthly competitive-landscape report (players map, top
    events, 24-month conference horizon, watchlist changes) on the previous full calendar month.
    No daily-pipeline stages run here — this is purely a report build on already-collected data."""
    try:
        from eoa.report.monthly import build_monthly

        paths = build_monthly()
        return {"monthly_report": {"report_id": paths.report_id, "qa_passed": paths.qa.passed}}
    except Exception as exc:
        log.error("monthly_report_failed", error=str(exc)[:300])
        return {"monthly_report_error": str(exc)[:300]}


def _backup() -> dict[str, Any]:
    """Obsidian vault export (if enabled) + pg_dump via docker (best effort) + retention prune."""
    out: dict[str, Any] = {}
    try:
        from eoa.export.obsidian import export_vault

        if settings().export.obsidian.enabled:
            stats = export_vault()
            out["obsidian"] = {
                k: v for k, v in vars(stats).items() if isinstance(v, int | float | str | bool)
            }
    except Exception as exc:
        log.warning("obsidian_export_skipped", error=str(exc)[:160])
        out["obsidian_error"] = str(exc)[:160]
    out.update(_pg_dump())
    return out


def _pg_dump() -> dict[str, Any]:
    """Nightly backup. Host: `docker compose exec postgres pg_dump`. Inside the agent container (no docker CLI):
    per-table `COPY ... TO STDOUT` into a gzip-compressed SQL-ish archive that psql can restore with its copy meta-command."""
    import gzip
    import subprocess
    from pathlib import Path

    out_dir = Path("output/backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(tz=UTC):%Y%m%d}"
    keep = settings().retention.backups_keep
    try:
        if os.environ.get("EOA_ROLE") == "agent":
            from psycopg import sql

            from eoa.db import connection

            name = out_dir / f"eoanalyst_{stamp}.copy.gz"
            with connection() as conn, gzip.open(name, "wt", encoding="utf-8") as fh:
                tables = [
                    r["tablename"]
                    for r in conn.execute(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1"
                    ).fetchall()
                ]
                fh.write(
                    "-- EO-Analyst logical backup; restore with: psql -d <db> -f <this file> "
                    "(after applying migrations up to the same revision)\n"
                )
                for t in tables:
                    ident = sql.Identifier(t).as_string(conn)
                    fh.write(f"\\copy {ident} FROM STDIN WITH (FORMAT csv, HEADER)\n")
                    with (
                        conn.cursor() as cur,
                        cur.copy(
                            sql.SQL("COPY {} TO STDOUT WITH (FORMAT csv, HEADER)").format(sql.Identifier(t))
                        ) as cp,
                    ):
                        for chunk in cp:
                            fh.write(bytes(chunk).decode("utf-8"))
                    fh.write("\\.\n")
        else:
            name = out_dir / f"eoanalyst_{stamp}.sql.gz"
            with gzip.open(name, "wb") as fh:
                p1 = subprocess.run(
                    [
                        "docker",
                        "compose",
                        "exec",
                        "-T",
                        "postgres",
                        "pg_dump",
                        "-U",
                        "eoa",
                        "-d",
                        "eoanalyst",
                    ],
                    capture_output=True,
                    check=True,
                    timeout=600,
                )
                fh.write(p1.stdout)
        for old in sorted(out_dir.glob("eoanalyst_*"))[:-keep]:
            old.unlink(missing_ok=True)
        return {"backup": str(name)}
    except Exception as exc:
        log.warning("backup_failed", error=str(exc)[:200])
        return {"backup_error": str(exc)[:200]}


def _notify(rs: RunState, paths: Any) -> dict[str, Any]:
    headlines: list[str] = []
    try:
        from eoa.db import connection

        with connection() as conn:
            rows = conn.execute(
                "SELECT title, level FROM items WHERE level IN ('red','orange') AND created_at > now() - interval '1 day' "
                "ORDER BY score DESC NULLS LAST LIMIT 3"
            ).fetchall()
            headlines = [f"{'🔴' if r['level'] == 'red' else '🟠'} {r['title'][:90]}" for r in rows]
    except Exception:
        pass
    docx = getattr(paths, "docx", None)
    if not docx:
        ntfy.failure("report", "הדוח היומי לא הופק הלילה — ראה run_log")
        return {"headlines": len(headlines), "report_missing": True}
    ntfy.report_ready("יומי", str(docx), headlines, ui_url=f"http://127.0.0.1:{settings().api.port}/")
    return {"headlines": len(headlines)}


def run_conference_scan(job: dict[str, Any]) -> dict[str, Any]:
    """FR-12.3: monthly conference-tracker scan (roll horizon + verify stale + discover new)."""
    from eoa.conferences.tracker import monthly_scan

    return monthly_scan()


def _run_tenders(role: str = "resident") -> dict[str, Any]:
    """section 5.2 / FR-5.2: tender/RFI/RFP ingestion + tender-likelihood forecasting
    (``eoa.tenders``). Called both as the ``"tenders"`` stage inside :func:`run_daily` (after
    ``analyze``) and by :func:`run_tender_scan` for the standalone ``tender_scan`` job kind."""
    from eoa.tenders.forecast import forecast_tenders
    from eoa.tenders.scan import scan_tenders

    scan_stats = scan_tenders(role=role)
    forecast_stats = forecast_tenders(role=role)
    return {"scan": _as_dict(scan_stats), "forecast": _as_dict(forecast_stats)}


def run_tender_scan(job: dict[str, Any]) -> dict[str, Any]:
    """``tender_scan`` job kind: an on-demand/standalone run of the same scan+forecast pair the
    ``"tenders"`` daily-run stage performs (see :func:`_run_tenders`)."""
    payload = job.get("payload") or {}
    role = "light" if payload.get("mode") == "eco" and settings().has_model("light") else "resident"
    return _run_tenders(role=role)


HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "daily_run": run_daily,
    "ingest": lambda job: _as_dict(_ingest()),
    "report": lambda job: _as_dict(_build_report()),
    "deep_search": run_deep_search_job,
    "weekly_run": run_weekly,
    "monthly_run": run_monthly,
    "conference_scan": run_conference_scan,
    "tender_scan": run_tender_scan,
}


# ----------------------------------------------------------------------------- worker loop
def _terminal_state(res: dict[str, Any]) -> str:
    """The job's terminal state from a handler result's optional `status` field (#16): a
    recognized `done`/`partial`/`failed` value wins (e.g. `run_daily`'s computed status);
    otherwise default to `done` (the handler didn't opt into stage-aware status and returned
    without raising)."""
    state = res.get("status")
    return state if state in {"done", "partial", "failed"} else "done"


def _default_kinds() -> list[str]:
    """Role-aware default job kinds: the isolated ``agent`` worker never claims ``ingest`` — that
    is fetcher-owned (the fetcher container is the one with egress network access). Host/dev
    workers may claim everything, including ``ingest`` (in-process ingest, see ``_ingest``)."""
    if os.environ.get("EOA_ROLE") == "agent":
        return [k for k in HANDLERS if k != "ingest"]
    return list(HANDLERS)


class Worker(threading.Thread):
    """Polls the jobs table and runs one job at a time (GPU is a single resource)."""

    def __init__(self, kinds: list[str] | None = None, poll_seconds: int = 10) -> None:
        super().__init__(daemon=True, name="eoa-worker")
        self.kinds = kinds or _default_kinds()
        self.poll = poll_seconds
        self.stop_event = threading.Event()
        self.current: dict[str, Any] | None = None
        self.worker_id = _worker_id()

    def run(self) -> None:
        try:
            reaped = reap_stale_jobs()
            if reaped:
                log.warning("worker_reaped_stale_jobs", count=reaped)
        except Exception as exc:
            log.error("reap_stale_jobs_failed", error=str(exc)[:200])
        log.info("worker_start", kinds=self.kinds, worker_id=self.worker_id)
        while not self.stop_event.is_set():
            try:
                job = claim_next_job(self.kinds, worker_id=self.worker_id)
            except Exception as exc:
                log.error("claim_failed", error=str(exc)[:200])
                job = None
            if not job:
                self.stop_event.wait(self.poll)
                continue
            self.current = job
            handler = HANDLERS.get(job["kind"])
            try:
                if handler is None:
                    finish_job(
                        job["id"], "failed", error=f"no handler for {job['kind']}", worker_id=self.worker_id
                    )
                    continue
                result = handler(job)
                res = result if isinstance(result, dict) else _as_dict(result)
                finish_job(job["id"], _terminal_state(res), result=res, worker_id=self.worker_id)
            except Exception as exc:
                log.error("job_failed", job_id=job["id"], kind=job["kind"], error=str(exc)[:300])
                finish_job(job["id"], "failed", error=f"{exc}"[:400], worker_id=self.worker_id)
            finally:
                self.current = None

    def stop(self) -> None:
        self.stop_event.set()


def enqueue_daily(mode: str = "full", priority: int = 2) -> int:
    """Queue a daily run now (returns job id)."""
    return enqueue_job("daily_run", {"mode": mode}, priority=priority)
