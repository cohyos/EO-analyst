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
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from eoa.config import settings
from eoa.errors import DeadlineExceeded, LeaseLost, ResourceUnavailable
from eoa.execution import checkpoint, deadline_scope, has_incomplete_work
from eoa.memory.relational import claim_next_job, enqueue_job, finish_job, heartbeat, reap_stale_jobs
from eoa.notify import ntfy
from eoa.pipeline.investigation_context import ensure_context_he

log = structlog.get_logger(__name__)

# U8 (docs/adr/005-cloud-llm-cli.md + "Revision 2026-09-06" section): every LLM call made
# anywhere in this process runs inside the orchestrator/worker -- the night pipeline
# (daily/weekly/monthly/ingest cron jobs, built below) *and* every job the API enqueues onto the
# same queue, including a manually triggered "investigate" (POST /api/items/{id}/investigate ->
# a `deep_search` job) or "run now" (POST /api/run). `eoa.llm.ollama_client.chat`/
# `chat_structured` check this env var before honoring a role-based (no explicit `provider`
# argument) call -- previously this forced "ollama" outright; as of Revision 2026-09-06 it
# instead routes the call through `llm_providers.mode`'s configured fallback chain for that role
# (`Settings.effective_chain`), which is *itself* always terminated by a local Ollama entry
# (enforced even if the user's own chain configuration omits one). In "local" mode (the default)
# this is unchanged from before: every role resolves to just Ollama. In "cloud" mode, this is the
# single choke point that makes the global switch apply to the pipeline AND to a manual
# "investigate"/"run now" (both queued jobs, run inside this same process) uniformly -- and the
# one place that guarantees a cloud choice can never leave a run without a local answer if every
# cloud leg fails. `setdefault` so a test harness that needs to opt a single process out can
# still set the env var before importing this module.
os.environ.setdefault("EOA_PIPELINE", "1")


def _worker_id() -> str:
    """A stable-enough identifier for this process, used as the job lease owner."""
    return f"{socket.gethostname()}:{os.getpid()}"


STAGE_ORDER = [
    "ingest",
    "embed_dedup",
    "classify",
    "dedup_xlang",
    "triage",
    "deep_search",
    "analyze",
    "corroborate",
    "stories",
    "tenders",
    "post_tenders_catchup",
    "report",
    "tech_daily_report",
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
    """Monotonic timestamp of the next night-window end (Asia/Jerusalem).

    F40 (audit 2026-09-24, orchestrator half): ``end`` (built via ``now.replace(...)``) shares the
    exact same ``ZoneInfo`` ``tzinfo`` *instance* as ``now``. CPython's aware-datetime subtraction
    skips UTC-offset normalization whenever both operands' ``tzinfo`` attributes are the same
    object (a documented optimization) and falls back to a naive wall-clock subtraction instead --
    verified live: for a window end computed just after Israel's spring-forward transition, the
    naive ``end - now`` in this zone was off by exactly one hour versus the real elapsed time
    (``end.astimezone(UTC) - now.astimezone(UTC)``). Across a DST transition inside the night
    window this under/over-counts the deadline by that hour. Converting both sides to UTC before
    subtracting forces the correct absolute-time arithmetic regardless of shared ``tzinfo``
    identity."""
    s = settings()
    tz = ZoneInfo(s.timezone)
    now = datetime.now(tz)
    h, m = (int(x) for x in s.schedule.night_window.end.split(":"))
    end = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if end <= now:
        end += timedelta(days=1)
    grace = timedelta(minutes=s.schedule.deadline_grace_minutes)
    end_with_grace_utc = (end + grace).astimezone(UTC)
    now_utc = now.astimezone(UTC)
    return time.monotonic() + (end_with_grace_utc - now_utc).total_seconds()


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
        with deadline_scope(max(budget or 0, 0.001) * 60):
            out = fn()
        rs.stats[stage] = _as_dict(out) | {"minutes": round((time.monotonic() - t0) / 60, 1)}
        _hb(rs, "partial" if has_incomplete_work(rs.stats[stage]) else "done", stage=stage, **rs.stats[stage])
        return out
    except LeaseLost:
        raise
    except ResourceUnavailable as exc:
        rs.stats[stage] = {"deferred": str(exc)[:200], "minutes": round((time.monotonic() - t0) / 60, 1)}
        _hb(rs, "deferred", stage=stage, error=str(exc)[:200], minutes=rs.stats[stage]["minutes"])
        log.warning("stage_deferred", stage=stage, error=str(exc))
        return None
    except DeadlineExceeded:
        rs.stats[stage] = {"partial": "deadline", "minutes": round((time.monotonic() - t0) / 60, 1)}
        _hb(rs, "deadline", stage=stage, minutes=rs.stats[stage]["minutes"])
        return None
    except Exception as exc:
        rs.failures[stage] = rs.failures.get(stage, 0) + 1
        error_type = type(exc).__name__
        # UI-ERRORS (docs/qa/content_review/UI-ERRORS.md): `str(exc)` is worthless for exceptions
        # raised with a single non-string arg (e.g. `KeyError(0)` -> str is just "0") -- the Morning
        # errors panel showed a bare "0" with no way to tell what actually broke (error 279,
        # 2026-09-08 01:31:51, tenders stage). When the raw message is empty or looks like nothing
        # but a bare number, fall back to `repr(exc)`, which always carries the exception's own
        # class name (e.g. "KeyError(0)").
        raw_message = str(exc)
        message = raw_message if raw_message and not raw_message.strip().isdigit() else f"{error_type}: {exc!r}"
        message = message[:300]
        tb_frames = traceback.extract_tb(exc.__traceback__)
        # Last 5 frames, file:line:function only -- no local variable values (those can carry
        # sensitive item/source content and this detail blob is read straight back out by the
        # Morning "שגיאות" panel's "פרטים טכניים" expander).
        traceback_tail = [f"{Path(fr.filename).name}:{fr.lineno}:{fr.name}" for fr in tb_frames[-5:]]
        rs.stats[stage] = {"error": message, "minutes": round((time.monotonic() - t0) / 60, 1)}
        _hb(
            rs,
            "error",
            stage=stage,
            error=message,
            error_type=error_type,
            traceback_tail=traceback_tail,
            minutes=rs.stats[stage]["minutes"],
            trace=traceback.format_exc()[-1500:],
        )
        log.error("stage_failed", stage=stage, error=message, error_type=error_type)
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
        _run_stage(rs, "corroborate", _run_corroboration)
        _run_stage(
            rs,
            "stories",
            lambda: _as_dict(
                __import__("eoa.pipeline.story_clustering", fromlist=["assign_story_ids"]).assign_story_ids()
            ),
        )
        _run_stage(rs, "tenders", lambda: _as_dict(_run_tenders(role=role)))
        _run_stage(rs, "post_tenders_catchup", lambda: _post_tenders_catchup(role=role))
        paths = _run_stage(rs, "report", _build_report, mandatory=True)
        # tech_daily (2026-09-17, user request -- daily EO/IR supply-chain technology-watch
        # report): a non-mandatory stage right after `report` -- `_run_stage` already isolates any
        # exception (logged, never re-raised) and the F4 6-hour idempotency guard inside
        # `build_tech_daily` itself stops a duplicate build on the same `period_end`, same
        # contract as `daily_run`/`weekly_run` both landing on one night for the daily report.
        _run_stage(rs, "tech_daily_report", _build_tech_daily_report)
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
    report_ok = isinstance(stats.get("report"), dict) and not has_incomplete_work(stats["report"])
    any_problem = has_incomplete_work(stats)
    return "done" if report_ok and not any_problem else ("partial" if report_ok else "failed")


def _ingest(poll: bool = False) -> Any:
    """Ingest through the fetcher container when running as the isolated agent, else in-process
    (synchronous: ``run_ingest_remote`` already manages its own event loop internally, so calling
    it from inside another ``asyncio.run()`` would raise). ``poll`` -- the daytime ``ingest`` job
    (payload ``mode: poll``), which enforces the daily source cadence (F35)."""
    from eoa.fetch.remote import run_ingest_remote

    return run_ingest_remote(poll=poll)


def _run_corroboration() -> dict[str, Any]:
    """``corroborate`` stage (2026-09-07 user requirement): runs right after ``analyze`` so a
    freshly-analyzed item's independent-corroboration status is available before the report is
    drafted. Two passes in one stage call: the normal stage-marked sweep of items that haven't
    been through ``corroborate`` yet (``run_corroboration``), then a nightly re-check of the last
    7 days' in-scope items (``recheck_recent``) since a corroborating second article can land days
    after the original was first checked."""
    from eoa.pipeline.corroboration import recheck_recent, run_corroboration

    checked = _as_dict(run_corroboration())
    rechecked = _as_dict(recheck_recent())
    return {"checked": checked, "rechecked": rechecked}


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


def _run_deep_search_job_local(job: dict[str, Any]) -> str:
    """The local, per-job ReAct investigation for one claimed ``deep_search`` job -- unchanged
    behaviour, factored out of ``run_deep_searches`` so both the ordinary local-mode loop and the
    U8-6b cloud-batch-failure fallback (below) can call it. Returns the outcome string; job
    finish/deferred/red-alert side effects happen here, same as before this revision."""
    from eoa.search.deep_search import investigate

    p = job.get("payload") or {}
    try:
        inv = investigate(
            p.get("question", ""),
            item_id=p.get("item_id"),
            job_id=job["id"],
            context_he=ensure_context_he(p),
            budget_multiplier=float(p.get("budget_multiplier") or 1.0),
            prior_findings_he=p.get("prior_findings_he", ""),
        )
        finish_job(job["id"], "done", result=_investigation_result_payload(inv))
        if p.get("level") == "red" and inv.result and inv.result.outcome != "not_found":
            _red_alert_for(p.get("item_id"), inv.result.answer_he)
        return inv.outcome
    except ResourceUnavailable as exc:
        # could not search at all (GPU/RAM) — keep the job for a later retry instead of
        # recording a fabricated not_found; retry after a cooldown rather than immediately
        finish_job(
            job["id"],
            "deferred",
            error=str(exc)[:300],
            not_before=datetime.now(tz=UTC) + timedelta(minutes=30),
        )
        raise
    except (DeadlineExceeded, LeaseLost):
        finish_job(job["id"], "deferred", error="stage interrupted", not_before=datetime.now(tz=UTC) + timedelta(minutes=30))
        raise
    except Exception as exc:
        finish_job(job["id"], "failed", error=str(exc)[:400])
        return "failed"


def run_deep_searches(rs: RunState) -> dict[str, Any]:
    """Consume queued deep_search jobs (from triage) within this run's budget and the nightly cap.

    U8-6b (Revision 2026-09-06): when ``llm_providers.mode == "cloud"``, every job claimed for
    this run is delegated to ``eoa.search.deep_search.investigate_batch_cloud`` in ONE call
    instead of one ``investigate()`` ReAct loop per job -- point 6's "כל שאלות החקירה בקובץ אחד
    וקריאה אחת". If that call itself fails outright (both tool-capable CLIs unavailable/erroring
    -- ``investigate_batch_cloud`` already tried claude then agy internally), the already-claimed
    jobs fall back to the same local per-job loop local mode always used, via
    ``_run_deep_search_job_local`` -- no job is ever silently dropped. Local mode (default) is
    completely unchanged, byte for byte, from before this revision.

    F02 (SOL-REVIEW-2026-09-24): the cloud-batch path used to gate on ``mode == "cloud"`` alone --
    ``eoa.llm.chain.run_chain`` (every other cloud dispatch point: chat and structured calls) also
    requires ``llm_providers.allow_cloud`` before a cloud leg is even considered, but this batch
    entry point called ``investigate_batch_cloud`` -> the CLI runners directly, bypassing that
    kill switch entirely -- an operator flipping ``allow_cloud: false`` (the documented "stop all
    cloud spend/egress now" control) did not actually stop this one path. ``allow_cloud`` is now
    required here too, alongside ``mode == "cloud"``; ``investigate_batch_cloud`` itself also
    checks it (defense in depth, in case some other caller is ever added)."""
    cap = settings().deep_search.max_per_night
    per_min = settings().deep_search.per_investigation_timeout_min

    if settings().llm_providers.mode == "cloud" and settings().llm_providers.allow_cloud:
        from eoa.search.deep_search import investigate_batch_cloud

        claimed: list[dict[str, Any]] = []
        while len(claimed) < cap:
            job = claim_next_job(["deep_search"], worker_id=_worker_id())
            if not job:
                break
            claimed.append(job)
        if not claimed:
            return {"investigations": 0, "outcomes": ""}
        pending = [
            {
                "job_id": job["id"],
                "item_id": (job.get("payload") or {}).get("item_id"),
                "question": (job.get("payload") or {}).get("question", ""),
                "entities": [],
                "seed_en": "",
                "context_he": ensure_context_he(job.get("payload") or {}),
            }
            for job in claimed
        ]
        try:
            results, cross_insights_he = investigate_batch_cloud(pending)
        except (DeadlineExceeded, LeaseLost):
            for pending_job in claimed:
                finish_job(pending_job["id"], "deferred", error="stage interrupted", not_before=datetime.now(tz=UTC) + timedelta(minutes=30))
            raise
        except Exception as exc:
            log.warning(
                "deep_search_cloud_batch_failed_falling_back_local", n=len(claimed), error=str(exc)[:300]
            )
            # F04 (audit 2026-09-24): the old `[_run_deep_search_job_local(job) for job in
            # claimed]` comprehension aborted outright on the first job whose local fallback
            # itself raised `ResourceUnavailable` (GPU/RAM gate) -- every other already-claimed
            # child stayed `running` until F03's reaper eventually caught it. Loop explicitly so
            # one job's resource failure does not strand the rest of the batch, and defer every
            # claimed child this loop never got to reach (a `DeadlineExceeded`/`LeaseLost`, or any
            # other exception that still escapes the per-job handling below) in a `finally`
            # instead of leaving it `running`.
            outcomes = []
            processed_ids: set[int] = set()
            try:
                for job in claimed:
                    processed_ids.add(job["id"])
                    try:
                        outcomes.append(_run_deep_search_job_local(job))
                    except ResourceUnavailable:
                        # `_run_deep_search_job_local` already deferred this job itself before
                        # re-raising -- keep going with the rest of the batch.
                        outcomes.append("deferred")
            finally:
                interrupted = 0
                for job in claimed:
                    if job["id"] not in processed_ids:
                        interrupted += 1
                        finish_job(
                            job["id"],
                            "deferred",
                            error="cloud batch fallback interrupted",
                            not_before=datetime.now(tz=UTC) + timedelta(minutes=30),
                        )
            return {
                "investigations": len(claimed),
                "outcomes": ",".join(outcomes),
                "failed": outcomes.count("failed"),
                # F24 (SOL-REVIEW-2026-09-24): a resource-unavailable child this branch itself
                # defers (appended as "deferred" to `outcomes` above) OR one this loop never even
                # reached because the batch was interrupted (deferred in the `finally` above,
                # never appended to `outcomes` at all) is real incomplete work for tonight's
                # daily run -- neither was visible to `has_incomplete_work` before (only `failed`
                # was reported), so a run with every claimed child merely deferred could still
                # compute `done`. `interrupted` is defined inside the `finally` block above but
                # always runs before this `return` is reached (the `finally` always executes
                # before the enclosing `try` completes).
                "deferred": outcomes.count("deferred") + interrupted,
            }

        outcomes = []
        for job in claimed:
            inv = results.get(job["id"])
            p = job.get("payload") or {}
            if inv is None:
                finish_job(
                    job["id"], "failed", error="cloud batch investigation returned no result for this job"
                )
                outcomes.append("failed")
                continue
            finish_job(job["id"], "done", result=_investigation_result_payload(inv))
            outcomes.append(inv.outcome)
            if p.get("level") == "red" and inv.result and inv.result.outcome != "not_found":
                _red_alert_for(p.get("item_id"), inv.result.answer_he)
        if cross_insights_he:
            log.info("deep_search_cloud_cross_insights", text=cross_insights_he[:500])
        return {
            "investigations": len(claimed),
            "outcomes": ",".join(outcomes),
            "cross_insights_he": cross_insights_he,
            # F24 (audit 2026-09-24): `has_incomplete_work` only recurses into dict values and
            # only recognizes a `failed`/`*_failed` KEY as a problem count -- the prior
            # comma-joined `outcomes` STRING made a failed child investigation invisible to it, so
            # the enclosing `daily_run` could finish `done` with a failed investigation buried
            # inside. This explicit count is picked up automatically (`has_incomplete_work`
            # already treats any `failed`-named int key > 0 as incomplete work).
            "failed": outcomes.count("failed"),
        }

    done, outcomes = 0, []
    while done < cap:
        left = rs.time_left_min()
        if left is not None and left < per_min + settings().stages.get("report", 30) + 10:
            log.warning("deep_search_stop_time", left_min=round(left))
            break
        job = claim_next_job(["deep_search"], worker_id=_worker_id())
        if not job:
            break
        outcomes.append(_run_deep_search_job_local(job))
        done += 1
    return {"investigations": done, "outcomes": ",".join(outcomes), "failed": outcomes.count("failed")}


def run_deep_search_job(job: dict[str, Any]) -> dict[str, Any]:
    """A single on-demand investigation (from the UI/CLI). U12's "הרחב חקירה" (expand) sets
    `budget_multiplier`/`prior_findings_he` in the payload (see `eoa.api.services.expand_investigation`)
    to re-run with a larger budget and the prior attempt's findings folded in, instead of a
    plain identical re-run of the same question."""
    from eoa.search.deep_search import investigate

    p = job.get("payload") or {}
    question = (p.get("question") or "").strip()
    if not question and p.get("item_id"):
        # Defensive: a job enqueued without a question (older UI paths) gets the item-derived default.
        from eoa.api.services import default_investigation_question
        from eoa.db import connection

        with connection() as conn:
            row = conn.execute(
                "SELECT title, so_what_he FROM items WHERE id = %s", (p["item_id"],)
            ).fetchone()
        if row:
            question = default_investigation_question(row["title"] or "", row.get("so_what_he"))
    inv = investigate(
        question,
        item_id=p.get("item_id"),
        job_id=job["id"],
        context_he=ensure_context_he(p),
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


def _build_tech_daily_report() -> Any:
    from eoa.report.tech_daily import build_tech_daily

    return build_tech_daily()


#: F22/N07 + S02 (SOL-REVIEW3-2026-09-24): how long `run_weekly` keeps deferring itself while it
#: waits for tonight's `daily_run` to reach a terminal state. Past this budget it ends `partial`
#: WITHOUT building the weekly report (S02: never publish a weekly report on non-final daily data).
#: 12h covers a full night window plus retries of a slow or deferred daily run.
WEEKLY_DAILY_WAIT_MAX = timedelta(hours=12)

#: S01/S02: a `daily_run` created up to this long BEFORE the weekly job itself still counts as
#: "tonight's" (the Saturday cron creates both at 01:00; a manual weekly an hour after the nightly
#: daily reuses it instead of running a second one).
WEEKLY_DAILY_LOOKBACK = timedelta(hours=6)

#: S02: `_daily_run_state_tonight`'s result when the jobs query itself failed -- deliberately not
#: `None`, which means "no daily_run exists" and makes `run_weekly` admit one.
DAILY_STATE_QUERY_FAILED = "__query_failed__"


def _daily_run_state_tonight(since: datetime) -> str | None:
    """F22/N07/S02: the ``state`` of tonight's ``daily_run`` -- the most recent one created after
    ``since`` OR any still non-terminal one (``queued``/``running``/``deferred``, whenever it was
    created: it is the run that will produce tonight's data, and admitting another would be a
    duplicate). ``None`` when no such row exists; :data:`DAILY_STATE_QUERY_FAILED` when the query
    itself failed (S02: a DB error must never read as "no daily run", which would make
    :func:`run_weekly` admit a second one)."""
    from eoa.db import connection

    sql = """
        SELECT state FROM jobs
        WHERE kind = 'daily_run'
          AND (created_at > %(since)s OR state IN ('queued', 'running', 'deferred'))
        ORDER BY created_at DESC
        LIMIT 1
    """
    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(sql, {"since": since})
            row = cur.fetchone()
            return row["state"] if row else None
    except Exception as exc:
        log.warning("daily_run_state_check_failed", error=str(exc)[:160])
        return DAILY_STATE_QUERY_FAILED


def _job_age(job: dict[str, Any]) -> timedelta:
    created_at = job.get("created_at")
    if not isinstance(created_at, datetime):
        return timedelta(0)
    now = datetime.now(tz=UTC)
    return now - (created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC))


def _weekly_daily_anchor(job: dict[str, Any]) -> datetime:
    """``since`` for :func:`_daily_run_state_tonight`: the weekly job's own creation time minus
    :data:`WEEKLY_DAILY_LOOKBACK` (stable across the job's deferral retries)."""
    created_at = job.get("created_at")
    if isinstance(created_at, datetime):
        base = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    else:
        base = datetime.now(tz=UTC)
    return base - WEEKLY_DAILY_LOOKBACK


def run_weekly(job: dict[str, Any]) -> dict[str, Any]:
    """``weekly_run`` handler: waits for tonight's ``daily_run`` (the full nightly pipeline and
    daily report) to reach a terminal state, then builds the weekly analyst report (trends,
    business events, conference lookahead, FR-11.4 meta-summary) on top of that night's
    freshly-analyzed items. A weekly-report failure is logged and recorded but never fails the job.

    S01/S02/R03 (SOL-REVIEW3-2026-09-24) -- this handler NEVER runs the nightly pipeline itself.
    The earlier versions did (as a "stand-alone weekly" fallback), which is what forced the
    asymmetric admission rule that let a weekly suppress the Saturday daily (S01), and the
    timeout branch fell through to ``build_weekly()`` on incomplete daily data (S02). Now:

    * no ``daily_run`` for tonight -> admit one through the shared admission gate
      (:func:`eoa.orchestrator.admission.admit_daily_run`; it returns ``None`` when an
      equivalent run is already active, which is equally fine) and defer to wait for it;
    * ``queued``/``running``/``deferred``, or the state query failed -> defer
      (``ResourceUnavailable``: ``Worker.run`` requeues it ``deferred`` with a cooldown);
    * still not terminal after :data:`WEEKLY_DAILY_WAIT_MAX` -> end ``partial`` WITHOUT building
      the weekly report;
    * terminal (``done``/``partial``/``failed``) -> tonight's data is final: build the report."""
    state = _daily_run_state_tonight(_weekly_daily_anchor(job))
    if state in {None, "queued", "running", "deferred", DAILY_STATE_QUERY_FAILED}:
        if _job_age(job) < WEEKLY_DAILY_WAIT_MAX:
            if state is None:
                from eoa.orchestrator import admission

                try:
                    daily_job_id = admission.admit_daily_run("full", priority=2)
                except Exception as exc:
                    raise ResourceUnavailable(f"weekly_run could not admit tonight's daily_run: {exc}") from exc
                log.info("weekly_run_admitted_daily_run", daily_job_id=daily_job_id)
            raise ResourceUnavailable(
                f"weekly_run waiting for tonight's daily_run to reach a terminal state (state={state!r})"
            )
        log.error("weekly_run_daily_wait_timed_out", state=state)
        return {
            "status": "partial",
            "weekly_report_skipped": "daily_run_not_terminal_after_wait",
            "daily_run_state": state,
            "daily_run_wait_error": (
                f"tonight's daily_run is still {state!r} after waiting {WEEKLY_DAILY_WAIT_MAX}; "
                "the weekly report was not built on incomplete data"
            ),
        }
    log.info("weekly_run_daily_run_terminal", state=state)
    stats: dict[str, Any] = {"daily_run_state": state}
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


def run_bd_report(job: dict[str, Any]) -> dict[str, Any]:
    """``bd_report`` job kind (A11): builds one territory's "דוח מיקוד לפיתוח עסקי" when the
    payload names a ``territory`` (API-enqueued, ``eoa.api.services.enqueue_bd_report``) --
    returning ``{"bd_report": {"report_id", "qa_passed", "territory"}}`` so
    ``services.build_or_enqueue_bd_report``'s poll loop can resolve the finished report. With no
    ``territory`` in the payload (the weekly scheduler job, ``orchestrator.main.build_scheduler``)
    it instead builds one for every territory in ``config.bd_report.territories``, returning
    ``{"bd_reports": {territory: {...}}}`` -- a failure for one territory never blocks the others
    (docs/CONVENTIONS.md rule 9)."""
    from eoa.report.bd_territory import build_bd_territory

    payload = job.get("payload") or {}
    lookback_days = int(payload.get("lookback_days") or settings().bd_report.lookback_days)
    territory = payload.get("territory")

    if territory:
        try:
            paths = build_bd_territory(territory, lookback_days)
            return {
                "bd_report": {
                    "report_id": paths.report_id,
                    "qa_passed": paths.qa.passed,
                    "territory": paths.territory,
                }
            }
        except Exception as exc:
            log.error("bd_report_failed", territory=territory, error=str(exc)[:300])
            return {"bd_report_error": str(exc)[:300]}

    results: dict[str, Any] = {}
    for t in settings().bd_report.territories:
        try:
            paths = build_bd_territory(t, lookback_days)
            results[t] = {"report_id": paths.report_id, "qa_passed": paths.qa.passed}
        except Exception as exc:
            log.error("bd_report_failed", territory=t, error=str(exc)[:300])
            results[t] = {"error": str(exc)[:300]}
    return {"bd_reports": results}


def run_product_line_report(job: dict[str, Any]) -> dict[str, Any]:
    """``product_line_report`` job kind (PL-backend, user request 2026-09-07): builds one product
    line's status & business-development report when the payload names a ``line_id`` (API-enqueued,
    ``eoa.api.services.enqueue_product_line_report``) -- returning ``{"product_line_report":
    {"report_id", "qa_passed", "line_id"}}``. With no ``line_id`` in the payload (the weekly
    scheduler job, mirrors ``bd_report``'s own convention) it instead builds one for every
    configured product line (``config/product_lines.yaml``), returning
    ``{"product_line_reports": {line_id: {...}}}`` -- a failure for one line never blocks the others
    (docs/CONVENTIONS.md rule 9)."""
    from eoa.product_lines.registry import product_line_ids
    from eoa.report.product_line import build_product_line

    payload = job.get("payload") or {}
    lookback_days = int(payload.get("lookback_days") or 90)
    line_id = payload.get("line_id")

    if line_id:
        try:
            paths = build_product_line(line_id, lookback_days)
            return {
                "product_line_report": {
                    "report_id": paths.report_id,
                    "qa_passed": paths.qa.passed,
                    "line_id": paths.line_id,
                }
            }
        except Exception as exc:
            log.error("product_line_report_failed", line_id=line_id, error=str(exc)[:300])
            return {"product_line_report_error": str(exc)[:300]}

    results: dict[str, Any] = {}
    for line in product_line_ids():
        try:
            paths = build_product_line(line, lookback_days)
            results[line] = {"report_id": paths.report_id, "qa_passed": paths.qa.passed}
        except Exception as exc:
            log.error("product_line_report_failed", line_id=line, error=str(exc)[:300])
            results[line] = {"error": str(exc)[:300]}
    return {"product_line_reports": results}


def run_tech_daily_report_job(job: dict[str, Any]) -> dict[str, Any]:
    """``tech_daily_report`` job kind ("בנה דוח טכנולוגיה עכשיו", user request 2026-09-17):
    builds one on-demand tech-daily report from the payload ``{lookback_days, force}``
    (API-enqueued, ``eoa.api.services.enqueue_tech_daily_report``). Distinct from the nightly
    ``tech_daily_report`` *stage* run inside ``run_daily`` (``_build_tech_daily_report`` above,
    a `_run_stage` name, not a job kind -- the two namespaces never collide); this is the
    standalone job kind the UI button enqueues. Returns ``{"report_id": ...}``."""
    from eoa.report.tech_daily import build_tech_daily

    payload = job.get("payload") or {}
    lookback_days = int(payload.get("lookback_days") or 1)
    force = bool(payload.get("force") or False)
    paths = build_tech_daily(lookback_days=lookback_days, force=force)
    return {"report_id": paths.report_id}


def run_product_dossier(job: dict[str, Any]) -> dict[str, Any]:
    """``product_dossier`` job kind (PD-backend, user request 2026-09-08): builds one "סקירת שוק
    עמוקה למוצר" (``eoa.dossier.report.build_product_dossier``) from the payload
    ``{product_key, product_name, vendor, aliases, product_line, budget_multiplier, llm_leg}``
    (API-enqueued, ``eoa.api.services.enqueue_product_dossier``/``rerun_product_dossier``).
    ``product_key`` itself is not passed to the builder (it is re-derived deterministically from
    ``vendor``+``product_name`` -- see ``eoa.dossier.corpus.slugify_product_key`` -- so the two can
    never drift); it is only used here for the log line. ``llm_leg`` (PD-cloud-tools, 2026-09-09):
    an optional per-run override, e.g. ``"codex:gpt-6-astra"`` -- forwarded verbatim into
    ``build_product_dossier``; ``None``/absent preserves the prior dispatch (the role's configured
    cloud chain, unchanged). Returns ``{"product_dossier": {"report_id", "dossier_id",
    "product_key", "outcome", "confidence"}}`` so the enqueue endpoint's poll loop can resolve the
    finished run; a failure never blocks the orchestrator (docs/CONVENTIONS.md rule 9)."""
    from eoa.dossier.report import build_product_dossier

    payload = job.get("payload") or {}
    product_name = (payload.get("product_name") or "").strip()
    if not product_name:
        return {"product_dossier_error": "missing product_name"}
    try:
        paths = build_product_dossier(
            product_name,
            payload.get("vendor"),
            payload.get("aliases") or [],
            product_line=payload.get("product_line"),
            budget_multiplier=payload.get("budget_multiplier"),
            job_id=job["id"],
            llm_leg=payload.get("llm_leg"),
        )
        return {
            "product_dossier": {
                "report_id": paths.report_id,
                "dossier_id": paths.dossier_id,
                "product_key": paths.product_key,
                "outcome": paths.outcome,
                "confidence": paths.confidence,
            }
        }
    except Exception as exc:
        log.error(
            "product_dossier_failed",
            product_key=payload.get("product_key"),
            product_name=product_name,
            error=str(exc)[:300],
        )
        return {"product_dossier_error": str(exc)[:300]}


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
    """Nightly logical backup of the LIVE database (ADR-004, native stack).

    Order of preference: (1) the portable ``pg_dump`` shipped under ``runtime/pgsql/bin`` (or any
    ``pg_dump`` on PATH) run against ``DATABASE_URL`` -> ``eoanalyst_<stamp>.dump`` (custom format,
    restorable with ``pg_restore``); (2) per-table ``COPY ... TO STDOUT`` through psycopg into a
    gzip archive -> ``eoanalyst_<stamp>.copy.gz``. The old ``docker compose exec postgres pg_dump``
    path was removed: with EOA_ROLE=host it silently dumped the retired Docker database (Q1-1)."""
    import gzip
    import shutil
    import subprocess
    from pathlib import Path

    out_dir = Path("output/backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(tz=UTC):%Y%m%d}"
    keep = settings().retention.backups_keep
    db_url = settings().database_url
    root = Path(os.environ.get("EOA_ROOT", Path(__file__).resolve().parents[3]))
    candidates = [
        root / "runtime" / "pgsql" / "bin" / "pg_dump.exe",
        root / "runtime" / "pgsql" / "bin" / "pg_dump",
    ]
    pg_dump_bin = next((str(c) for c in candidates if c.exists()), None) or shutil.which("pg_dump")
    try:
        if pg_dump_bin:
            name = out_dir / f"eoanalyst_{stamp}.dump"
            subprocess.run(
                [
                    pg_dump_bin,
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    "--file",
                    str(name),
                    db_url,
                ],
                capture_output=True,
                check=True,
                timeout=900,
            )
        else:
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
        for old in sorted(out_dir.glob("eoanalyst_*"))[:-keep]:
            old.unlink(missing_ok=True)
        return {"backup": str(name)}
    except Exception as exc:
        log.warning("backup_failed", error=str(exc)[:200])
        return {"backup_error": str(exc)[:200]}


def _notify(rs: RunState, paths: Any) -> dict[str, Any]:
    """Mandatory ``notify`` stage: the nightly ntfy push (success or "report missing" failure).

    N03 (SOL-REVIEW-2026-09-24): a worker crash between this function actually delivering the
    push and the outer ``Worker.run``/``finish_job`` call recording the job as finished leaves the
    job ``running`` past its lease; ``reap_stale_jobs`` requeues it ``deferred`` and a later
    worker reruns THIS SAME job (same ``rs.job_id``) end to end, including this stage, a second
    time. ``rs.job_id`` is stable across that reap-and-reclaim (the row is updated in place, never
    re-inserted), so it is a safe idempotency key -- see :func:`eoa.memory.relational.
    claim_notification_pending`.

    R02/N03 (SOL-REVIEW2-2026-09-24): the OLD marker (``mark_notification_sent``) recorded "sent"
    right BEFORE the send, unconditionally -- ``ntfy.send`` can return ``Sent(ok=False)`` (HTTP
    error, timeout, unreachable server) without raising, so a genuinely failed push was marked
    "sent" anyway and a replay would skip it forever, silently losing the notification. This now
    goes through a claim/result pair instead: :func:`~eoa.memory.relational.
    claim_notification_pending` claims the ``(kind, key)`` row ``pending`` BEFORE attempting
    delivery (first attempt, a bounded retry of a prior ``failed`` attempt, or reclaiming a
    ``pending`` row stale long enough to be a crashed claim -- see that function's docstring for
    the full state machine and its documented at-least-once trade-off), and
    :func:`~eoa.memory.relational.mark_notification_result` records the actual outcome (checking
    ``Sent.ok``) once delivery is attempted -- including when the attempt raises, so an exception
    still lands on ``failed`` (retryable) rather than leaving the row ``pending`` until the
    stale-claim window expires.

    R02 (SOL-REVIEW3-2026-09-24 blocker 3): landing a delivery on ``failed`` here is only half the
    fix -- the job worker only ever claims ``queued``/``deferred`` *jobs*, and a failed notification
    is not a job, so nothing revisited that row until this job (``daily_run``) happened to be
    reaped/replayed, if ever. ``mark_notification_result`` is now handed ``payload`` -- the exact
    ``ntfy.send()`` kwargs for the message just attempted, built via ``ntfy.build_failure``/
    ``build_report_ready`` -- so it can persist a reproducible copy and schedule ``next_attempt_at``
    on failure; the scheduled sweep in :mod:`eoa.notify.retry`
    (``eoa.orchestrator.main.build_scheduler``'s ``notification_retry`` job, every ~15 minutes)
    is what actually resends it."""
    from eoa.memory.relational import claim_notification_pending, mark_notification_result

    key = str(rs.job_id)
    if not claim_notification_pending("daily_report", key):
        log.info("daily_notify_skipped_already_sent_or_in_flight", job_id=rs.job_id)
        return {"notification_skipped": "already_sent_for_this_job"}
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
    # Built once, outside the try, from the exact same arguments passed to ntfy.failure/
    # report_ready below -- so `payload` is always bound (including on the except path, if the
    # send call itself raises) and `ntfy.send(**payload)` in eoa.notify.retry reproduces this
    # notification byte for byte.
    if not docx:
        payload = ntfy.build_failure("report", "הדוח היומי לא הופק הלילה — ראה run_log")
    else:
        payload = ntfy.build_report_ready(
            "יומי", str(docx), headlines, ui_url=f"http://127.0.0.1:{settings().api.port}/"
        )
    try:
        if not docx:
            sent = ntfy.failure("report", "הדוח היומי לא הופק הלילה — ראה run_log")
            mark_notification_result("daily_report", key, ok=sent.ok, payload=payload)
            out: dict[str, Any] = {"headlines": len(headlines), "report_missing": True}
        else:
            sent = ntfy.report_ready(
                "יומי", str(docx), headlines, ui_url=f"http://127.0.0.1:{settings().api.port}/"
            )
            mark_notification_result("daily_report", key, ok=sent.ok, payload=payload)
            out = {"headlines": len(headlines)}
        # F22-style convention (every other stage's `*_error` key, e.g. export_backup's
        # `backup_error`): a push that actually failed to deliver (`Sent(ok=False)` -- HTTP
        # error/timeout/unreachable server, no exception) should still mark the overall run
        # `partial` rather than `done`, via `has_incomplete_work`'s `*_error` key scan.
        if not sent.ok:
            out["notification_error"] = f"ntfy delivery failed: {sent.url or '<no url>'}"
        return out
    except Exception:
        mark_notification_result("daily_report", key, ok=False, payload=payload)
        raise


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


def _tender_items_needing_pipeline() -> list[int] | None:
    """F22 (docs/REVIEW_2026-09-05.md): ids of tender-derived ``items`` rows (``report_kind =
    'tender'``, inserted by ``eoa.tenders.scan._insert_tender_and_item``) still missing an
    embedding or a triage level. The ``tenders`` stage runs after ``embed_dedup``/``classify``/
    ``triage``/``analyze`` in :data:`STAGE_ORDER`, so any item it creates this run never went
    through those stages tonight and would otherwise sit unprocessed until the *next* night's
    stages happen to sweep up the backlog (5 such items observed one morning). Not restricted to
    "created this run" -- a tender item still missing these fields for any reason (e.g. a previous
    night's catch-up itself got deferred) is equally worth picking up here, and the set is normally
    tiny either way.

    F25 (audit 2026-09-24): a query failure used to be swallowed into a bare ``[]`` here --
    indistinguishable from "no tender items are pending" -- so :func:`_post_tenders_catchup`
    reported ``{"items": 0}`` and the run looked clean even though the candidate query never ran.
    ``None`` (rather than ``[]``) now signals "the query itself failed" to the caller; nothing is
    mutated on that path, so the underlying items (still missing an embedding/level) remain
    exactly as eligible for the next attempt as they were before this call."""
    from eoa.db import connection

    try:
        with connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM items
                WHERE report_kind = 'tender' AND security_status = 'clean'
                  AND (embedding IS NULL OR level IS NULL)
                ORDER BY id
                """
            )
            return [r["id"] for r in cur.fetchall()]
    except Exception as exc:
        log.warning("post_tenders_catchup_query_failed", error=str(exc)[:200])
        return None


def _post_tenders_catchup(*, role: str = "resident") -> dict[str, Any]:
    """F22: run embed_dedup + classify + triage, scoped (via each stage function's additive
    ``item_ids`` parameter) to just the tender-derived items still missing an embedding/level --
    see :func:`_tender_items_needing_pipeline`. A handful of rows, not a backlog re-sweep of
    everything else still pending those stages; a failure in one sub-stage never blocks the others
    (docs/CONVENTIONS.md rule 9) and never fails the run -- the items simply get caught by the next
    night's stages as before this fix.

    F25 (audit 2026-09-24): a failure in the candidate-selection query itself (as opposed to
    finding zero candidates) is now reported as an explicit ``query_error`` -- picked up by
    ``has_incomplete_work``/``_compute_run_status`` so the run is ``partial`` rather than silently
    ``done`` -- instead of being indistinguishable from "nothing was pending"."""
    item_ids = _tender_items_needing_pipeline()
    if item_ids is None:
        return {"items": 0, "query_error": "failed to list tender items needing pipeline"}
    if not item_ids:
        return {"items": 0}

    out: dict[str, Any] = {"items": len(item_ids)}
    try:
        from eoa.pipeline.dedup import run_dedup

        out["embed_dedup"] = _as_dict(run_dedup(item_ids=item_ids))
    except (DeadlineExceeded, LeaseLost):
        raise
    except ResourceUnavailable as exc:
        out["embed_dedup"] = {"deferred": str(exc)[:200]}
    except Exception as exc:
        log.warning("post_tenders_catchup_embed_dedup_failed", error=str(exc)[:200])
        out["embed_dedup_error"] = str(exc)[:200]

    try:
        from eoa.pipeline.classify import run_classify

        out["classify"] = _as_dict(run_classify(role=role, item_ids=item_ids))
    except (DeadlineExceeded, LeaseLost):
        raise
    except ResourceUnavailable as exc:
        out["classify"] = {"deferred": str(exc)[:200]}
    except Exception as exc:
        log.warning("post_tenders_catchup_classify_failed", error=str(exc)[:200])
        out["classify_error"] = str(exc)[:200]

    try:
        from eoa.pipeline.triage import run_triage

        out["triage"] = _as_dict(run_triage(role=role, item_ids=item_ids))
    except (DeadlineExceeded, LeaseLost):
        raise
    except ResourceUnavailable as exc:
        out["triage"] = {"deferred": str(exc)[:200]}
    except Exception as exc:
        log.warning("post_tenders_catchup_triage_failed", error=str(exc)[:200])
        out["triage_error"] = str(exc)[:200]

    return out


def run_tender_scan(job: dict[str, Any]) -> dict[str, Any]:
    """``tender_scan`` job kind: an on-demand/standalone run of the same scan+forecast pair the
    ``"tenders"`` daily-run stage performs (see :func:`_run_tenders`)."""
    payload = job.get("payload") or {}
    role = "light" if payload.get("mode") == "eco" and settings().has_model("light") else "resident"
    return _run_tenders(role=role)


def run_patent_scan(job: dict[str, Any]) -> dict[str, Any]:
    """``patent_scan`` job kind (A14): weekly scan of every configured watch topic + assignee
    (``config/patents.yaml``), then a best-effort analyze/valuation pass over whatever is still
    unanalyzed -- mirrors ``run_tender_scan``'s "scan + a bit of pipeline follow-through" shape. A
    payload ``topic`` (the CLI's ``eo run patents --topic``) scans just that one ad-hoc topic
    instead of the full configured set."""
    from eoa.patents.analyze import analyze_patents
    from eoa.patents.scan import WatchTopic, scan_patents
    from eoa.patents.valuation import score_and_persist

    payload = job.get("payload") or {}
    topic = payload.get("topic")
    scan_stats = scan_patents(
        topics=[WatchTopic(name_he=topic, query=topic)] if topic else None, assignees=[] if topic else None
    )
    analyze_stats = analyze_patents(30)
    scored = score_and_persist(limit=100)
    return {"scan": _as_dict(scan_stats), "analyze": _as_dict(analyze_stats), "valued": scored}


def run_patent_survey(job: dict[str, Any]) -> dict[str, Any]:
    """``patent_survey`` job kind (A14): the on-demand "סקר פטנטים" pipeline for the ``topic`` in
    the payload (``eoa.api.routes.patents.create_patent_survey``), returning
    ``{"patent_survey": {"report_id", "survey_id", "patent_count"}}`` so that endpoint's poll loop
    can resolve the finished survey."""
    from eoa.patents.survey import build_patent_survey

    payload = job.get("payload") or {}
    topic = (payload.get("topic") or "").strip()
    territory = (payload.get("territory") or "").strip() or None
    if not topic:
        return {"patent_survey_error": "missing topic"}
    try:
        paths = build_patent_survey(topic, territory=territory)
        return {
            "patent_survey": {
                "report_id": paths.report_id,
                "survey_id": paths.survey_id,
                "patent_count": paths.patent_count,
            }
        }
    except Exception as exc:
        log.error("patent_survey_failed", topic=topic, error=str(exc)[:300])
        return {"patent_survey_error": str(exc)[:300]}


def run_payload_extract_job(job: dict[str, Any]) -> dict[str, Any]:
    """``payload_extract`` job kind (A17): scan triaged items mentioning EO payload vocabulary and
    persist any spec/price found via the append-only rules in ``eoa.payloads.extract``. Payload
    ``limit``/``item_ids`` mirror the same optional-override shape other scan-style jobs accept."""
    from eoa.payloads.extract import run_payload_extract

    payload = job.get("payload") or {}
    limit = int(payload.get("limit") or 20)
    item_ids = payload.get("item_ids")
    stats = run_payload_extract(limit, item_ids=item_ids)
    return _as_dict(stats)


HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "daily_run": run_daily,
    "ingest": lambda job: _as_dict(_ingest(poll=(job.get("payload") or {}).get("mode") == "poll")),
    "report": lambda job: _as_dict(_build_report()),
    "deep_search": run_deep_search_job,
    "weekly_run": run_weekly,
    "monthly_run": run_monthly,
    "conference_scan": run_conference_scan,
    "tender_scan": run_tender_scan,
    "bd_report": run_bd_report,
    "product_line_report": run_product_line_report,
    "tech_daily_report": run_tech_daily_report_job,
    "product_dossier": run_product_dossier,
    "patent_scan": run_patent_scan,
    "patent_survey": run_patent_survey,
    "payload_extract": run_payload_extract_job,
    # Native single-process mode (ADR-004): serve any stray fetch_url job in-process instead of
    # leaving it queued forever (the fetcher container that used to claim these is gone).
    "fetch_url": lambda job: _fetch_url_job(job),
}


def _fetch_url_job(job: dict[str, Any]) -> dict[str, Any]:
    from eoa.fetch.remote import _fetch_local

    url = (job.get("payload") or {}).get("url") or ""
    return _fetch_local(url)


# ----------------------------------------------------------------------------- worker loop
def _terminal_state(res: dict[str, Any]) -> str:
    """The job's terminal state from a handler result's optional `status` field (#16): a
    recognized `done`/`partial`/`failed` value wins (e.g. `run_daily`'s computed status);
    otherwise default to `done` (the handler didn't opt into stage-aware status and returned
    without raising).

    F23 (audit 2026-09-24): that unconditional "no status -> done" default let a failed
    standalone weekly/monthly/dossier/patent-survey/bd/product-line report land as `done` --
    those handlers (`run_monthly`, `run_product_dossier`, `run_patent_survey`, ... ) catch their
    own exceptions and return a `*_error` field instead of raising or ever setting `status`. It
    also missed the case where a `status` IS present but scoped to only part of the result --
    `run_weekly`'s own `stats["status"]` (from the daily-pipeline portion, via `run_daily`) stays
    `done` even when the separate `weekly_report_error` sibling key it adds afterward means the
    weekly-specific work failed. `has_incomplete_work` (the same recursive `*_error`/`error`/
    `deferred`/`skipped`/`partial`/`*_failed` scan `_compute_run_status` already uses for
    `run_daily` itself) now gates the decision instead of `status` alone."""
    state = res.get("status")
    recognized = state if state in {"done", "partial", "failed"} else None
    if recognized == "failed":
        return "failed"
    if not has_incomplete_work(res):
        return recognized or "done"
    if recognized is not None:
        # `status` already summarized its own scope, but a problem outside that scope was also
        # recorded (e.g. run_weekly's daily-pipeline `status=done` plus its own
        # `weekly_report_error`) -- never silently keep `done` once anything is wrong.
        return "partial"
    # No handler-computed `status` at all: a lone `*_error` result with nothing else built is an
    # outright failure; a multi-target loop (bd_report/product_line_report without a `territory`/
    # `line_id`) that also recorded at least one real success is a partial failure instead.
    return "partial" if _has_recorded_success(res) else "failed"


def _has_recorded_success(value: Any) -> bool:
    """F23: True if a nested handler result carries a completed sub-result (a `report_id`/
    `survey_id`/`dossier_id`, or any deeper successful sub-result) alongside whatever
    `has_incomplete_work` flagged -- i.e. a multi-target loop where some, but not all, targets
    succeeded."""
    if isinstance(value, dict):
        if any(value.get(k) for k in ("report_id", "survey_id", "dossier_id")):
            return True
        for key, v in value.items():
            if key.endswith("_error") or key in {"error", "deferred", "skipped", "partial", "status"}:
                continue
            if _has_recorded_success(v):
                return True
    elif isinstance(value, list):
        return any(_has_recorded_success(item) for item in value)
    return False


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
                from eoa.orchestrator.lease import keep_job_lease

                with keep_job_lease(job["id"], self.worker_id):
                    result = handler(job)
                    checkpoint()
                    res = result if isinstance(result, dict) else _as_dict(result)
                    finish_job(job["id"], _terminal_state(res), result=res, worker_id=self.worker_id)
            except LeaseLost:
                log.warning("job_lease_lost", job_id=job["id"])
            except ResourceUnavailable as exc:
                finish_job(job["id"], "deferred", error=str(exc)[:400],
                           not_before=datetime.now(tz=UTC) + timedelta(minutes=30), worker_id=self.worker_id)
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
