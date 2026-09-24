"""Shared run-admission path (F31, SOL-REVIEW2-2026-09-24): the single `pg_advisory_xact_lock` +
equivalent-kind check + insert that decides whether a new `daily_run`/`weekly_run`/`report` job
may be enqueued -- used by BOTH the API's "run now" button (`eoa.api.services.enqueue_run`) and
every scheduled trigger that can enqueue one of those same kinds (`eoa.orchestrator.main`'s
"daily"/"weekly" cron jobs and `reconcile_missed_night_run`'s startup catch-up).

F31 follow-up (SOL-REVIEW2-2026-09-24): `enqueue_run`'s own advisory lock + equivalent-kind
SELECT + INSERT (all inside one transaction) closed the race between two concurrent API callers,
but the scheduler enqueued `daily_run`/`weekly_run` straight through `eoa.memory.relational.
enqueue_job`/`enqueue_daily` -- a different code path with no lock and no equivalence check at
all. A "run now" API click landing the same instant as the 01:00 cron tick (or
`reconcile_missed_night_run`'s startup catch-up firing right as that same tick lands) could each
pass their own uncoordinated check and enqueue two overlapping jobs. `admit_run` below is the one
function every one of those call sites now goes through, so they all serialize on the identical
advisory-lock namespace and see each other's just-inserted (or already-active) rows.

Scope note: `admit_daily_run`/`admit_weekly_run` deliberately do NOT use the same equivalence set
for both kinds. `admit_daily_run` uses the full `daily_run`/`weekly_run`/`report` closure (a
`daily_run` and a `report`/`weekly_run` really do compete for the same "one nightly analysis run"
slot). `admit_weekly_run` checks only `weekly_run` itself -- `run_weekly` (see
`eoa.orchestrator.jobs`) is *designed* to run concurrently with, and wait on, a separate
`daily_run` job (that is the entire point of its own `_daily_run_state_tonight` wait/defer logic);
gating `weekly_run`'s enqueue on "no `daily_run` active" would block the weekly report from ever
being created on the one night it is scheduled to run.
"""

from __future__ import annotations

from typing import Any

import structlog
from psycopg.types.json import Json

from eoa import db

log = structlog.get_logger(__name__)

#: One fixed advisory-lock namespace shared by every admission-gated enqueue in the process --
#: arbitrary, just needs to not collide with another `pg_advisory_xact_lock(int)` caller in this
#: codebase (grep `pg_advisory` before reusing it elsewhere). Same value `eoa.api.services` used
#: as `_ENQUEUE_RUN_LOCK_NS` before this factor-out, kept identical so the two call sites always
#: contend for the exact same lock.
ADMISSION_LOCK_NS = 872_351_004

#: U4/F17 + F31 follow-up: kinds that count as "the same effective run" for idempotency -- a
#: `daily_run` (which `run_weekly` runs first when it stands alone, see
#: `eoa.orchestrator.jobs.run_weekly`) and the standalone `report` job all compete for the same
#: "one nightly analysis run" slot.
RUN_IDEMPOTENCY_GROUPS: dict[str, tuple[str, ...]] = {
    "daily_run": ("daily_run", "weekly_run"),
    "report": ("report", "daily_run", "weekly_run"),
}


def equivalent_kinds_for(kind: str) -> list[str]:
    """Symmetric closure over `RUN_IDEMPOTENCY_GROUPS`: every kind reachable from `kind` through
    any group it appears in, whether as that group's own key or one of its listed members."""
    found = {kind}
    changed = True
    while changed:
        changed = False
        for group_kind, members in RUN_IDEMPOTENCY_GROUPS.items():
            group = {group_kind, *members}
            if found & group and not group <= found:
                found |= group
                changed = True
    return sorted(found)


class RunAlreadyActive(Exception):
    """An equivalent run is already queued/running; `eoa.api.routes.jobs` maps this to HTTP 409."""

    def __init__(self, job: dict[str, Any]) -> None:
        self.job = job
        super().__init__(f"a {job.get('kind')} job is already {job.get('state')} (id={job.get('id')})")


def admit_run(
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    priority: int = 5,
    equivalent_kinds: list[str] | None = None,
) -> int:
    """Atomically check-and-enqueue `kind` (with `payload`/`priority`): raises `RunAlreadyActive`
    if a job whose kind is in `equivalent_kinds` (defaults to `[kind]` alone) is already
    `queued`/`running`, else inserts the new job and returns its id.

    The advisory lock is acquired FIRST and held for the whole check+insert (one
    `db.connection()` transaction, committed/rolled back by that context manager) -- a concurrent
    caller (API or scheduler) blocks on the lock until this transaction commits, then re-checks
    against the row this call just inserted. This is the one admission gate every caller must go
    through for `daily_run`/`weekly_run`/`report`."""
    kinds = equivalent_kinds if equivalent_kinds is not None else [kind]
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%(ns)s)", {"ns": ADMISSION_LOCK_NS})
        cur.execute(
            "SELECT * FROM jobs WHERE kind = ANY(%(kinds)s) AND state IN ('queued', 'running') "
            "ORDER BY created_at DESC LIMIT 1",
            {"kinds": kinds},
        )
        existing = cur.fetchone()
        if existing is not None:
            raise RunAlreadyActive(dict(existing))
        cur.execute(
            "INSERT INTO jobs (kind, payload, priority, state) "
            "VALUES (%(kind)s, %(payload)s, %(priority)s, 'queued') RETURNING id",
            {"kind": kind, "payload": Json(payload) if payload is not None else None, "priority": priority},
        )
        job_id: int = cur.fetchone()["id"]
    log.info("job.enqueued", job_id=job_id, kind=kind, priority=priority)
    return job_id


def admit_daily_run(mode: str = "full", priority: int = 2) -> int | None:
    """Scheduler entry point for a `daily_run` (the "daily" cron job and
    `reconcile_missed_night_run`'s startup catch-up) -- goes through the same `admit_run` gate as
    the API's `enqueue_run("daily"/"report", ...)`, using the full `daily_run`/`weekly_run`/
    `report` equivalence closure, so a concurrent API "run now" / another scheduler tick can never
    duplicate it. Returns `None` (logged, not raised) instead of letting `RunAlreadyActive`
    propagate -- the scheduler's own cron callback has no HTTP response to map it to, and "nothing
    to do, an equivalent run is already active" is a normal outcome here, not an error."""
    try:
        return admit_run(
            "daily_run", {"mode": mode}, priority=priority, equivalent_kinds=equivalent_kinds_for("daily_run")
        )
    except RunAlreadyActive as exc:
        log.info("scheduled_daily_run_skipped_already_active", existing_job=exc.job)
        return None


def admit_weekly_run(mode: str = "full", priority: int = 3) -> int | None:
    """Scheduler entry point for `weekly_run` (the "weekly" cron job). Deliberately checks only
    `weekly_run` itself (see the module docstring's Scope note) -- NOT the full equivalence
    closure `admit_daily_run` uses -- so an already-active `daily_run` never blocks this from
    being created; it still refuses a second `weekly_run` racing another one."""
    try:
        return admit_run("weekly_run", {"mode": mode}, priority=priority, equivalent_kinds=["weekly_run"])
    except RunAlreadyActive as exc:
        log.info("scheduled_weekly_run_skipped_already_active", existing_job=exc.job)
        return None
