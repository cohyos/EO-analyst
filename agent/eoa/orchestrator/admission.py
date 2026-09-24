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

Scope note (S01/F31, SOL-REVIEW3-2026-09-24): `weekly_run` is NOT in the daily equivalence
set. The round-3 version put it there for `daily_run` but checked only `weekly_run` for weekly --
an asymmetric relation, so on Saturday (both crons fire at 01:00) a weekly admitted first made
the scheduled daily see "equivalent run active" and skip, and the weekly then waited hours for a
daily that never came. `weekly_run` now never runs the nightly pipeline itself: it only waits for
tonight's `daily_run` to reach a terminal state (admitting one through `admit_daily_run` if none
exists) and then builds the weekly report. So `daily_run`/`report` compete for the one nightly
analysis slot, `weekly_run` competes only with itself, and the result is identical in either
lock order: exactly one `daily_run` and one `weekly_run`.

`deferred` counts as active (S01): a deferred run is waiting to retry, and admitting another
equivalent job next to it would run the pipeline twice once both are claimed. A deferred row
older than `DEFERRED_ACTIVE_HOURS` is RETIRED (`failed`, "superseded") inside the same locked
transaction before the check (T01, SOL-REVIEW4-2026-09-24) -- merely ignoring it, as round 4 did,
left it claimable, so it could still run after its replacement: two pipelines. Retiring keeps a
run stuck deferred since an old night from blocking admission forever without that risk.
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

#: U4/F17 + S01 + T02 (SOL-REVIEW4-2026-09-24): for each kind, the active kinds that block its
#: admission. Deliberately ONE-WAY, not a symmetric closure: a standalone `report` is refused while
#: a `daily_run` is active (the daily run builds that same report itself), but a `daily_run` is
#: never refused because of a `report` -- a long `report` would otherwise suppress the nightly
#: pipeline (the Saturday case: weekly's admission of the missing daily kept failing until its
#: 12h wait budget ran out). `weekly_run` competes only with itself (module docstring Scope note).
RUN_IDEMPOTENCY_GROUPS: dict[str, tuple[str, ...]] = {
    "daily_run": ("daily_run",),
    "report": ("report", "daily_run"),
    "weekly_run": ("weekly_run",),
}

#: S01: a `deferred` job created within this many hours still blocks an equivalent admission.
DEFERRED_ACTIVE_HOURS = 20


def equivalent_kinds_for(kind: str) -> list[str]:
    """The kinds whose active job blocks admitting `kind` (see `RUN_IDEMPOTENCY_GROUPS`); any
    other kind is blocked only by itself."""
    return sorted(RUN_IDEMPOTENCY_GROUPS.get(kind, (kind,)))


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
    `queued`/`running` (or `deferred` within `DEFERRED_ACTIVE_HOURS` -- an older deferred one is
    retired first, T01), else inserts the new job and returns its id.

    The advisory lock is acquired FIRST and held for the whole check+insert (one
    `db.connection()` transaction, committed/rolled back by that context manager) -- a concurrent
    caller (API or scheduler) blocks on the lock until this transaction commits, then re-checks
    against the row this call just inserted. This is the one admission gate every caller must go
    through for `daily_run`/`weekly_run`/`report`."""
    kinds = equivalent_kinds if equivalent_kinds is not None else [kind]
    existing = None
    job_id: int | None = None
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%(ns)s)", {"ns": ADMISSION_LOCK_NS})
        # V03 (SOL-REVIEW5-2026-09-24): retire only stale deferred rows of the kind being ADMITTED
        # -- the new job is their replacement. Retiring across `kinds` let a standalone `report`
        # admission discard an overdue `daily_run` whose pipeline nothing then re-ran.
        cur.execute(
            "UPDATE jobs SET state = 'failed', finished_at = now(), "
            "error = 'superseded: deferred longer than ' || %(deferred_hours)s || 'h, retired at admission' "
            "WHERE kind = %(kind)s AND state = 'deferred' "
            "AND created_at <= now() - make_interval(hours => %(deferred_hours)s)",
            {"kind": kind, "deferred_hours": DEFERRED_ACTIVE_HOURS},
        )
        cur.execute(
            "SELECT * FROM jobs WHERE kind = ANY(%(kinds)s) AND (state IN ('queued', 'running') "
            "OR (state = 'deferred' AND created_at > now() - make_interval(hours => %(deferred_hours)s))) "
            "ORDER BY created_at DESC LIMIT 1",
            {"kinds": kinds, "deferred_hours": DEFERRED_ACTIVE_HOURS},
        )
        existing = cur.fetchone()
        if existing is None:
            cur.execute(
                "INSERT INTO jobs (kind, payload, priority, state) "
                "VALUES (%(kind)s, %(payload)s, %(priority)s, 'queued') RETURNING id",
                {"kind": kind, "payload": Json(payload) if payload is not None else None, "priority": priority},
            )
            job_id = cur.fetchone()["id"]
    # T01 follow-up (SOL-REVIEW5-2026-09-24): raise only AFTER the transaction has committed, so a
    # refused admission keeps its stale-deferred retirement instead of rolling it back.
    if existing is not None:
        raise RunAlreadyActive(dict(existing))
    log.info("job.enqueued", job_id=job_id, kind=kind, priority=priority)
    assert job_id is not None
    return job_id


def admit_daily_run(mode: str = "full", priority: int = 2) -> int | None:
    """Scheduler entry point for a `daily_run` (the "daily" cron job and
    `reconcile_missed_night_run`'s startup catch-up) -- goes through the same `admit_run` gate as
    the API's `enqueue_run("daily", ...)`, blocked only by another active `daily_run` (T02), so a
    concurrent API "run now" / another scheduler tick / `run_weekly`'s own admission of a missing
    daily can never duplicate it. Returns `None` (logged, not raised) instead of letting `RunAlreadyActive`
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
    """Scheduler entry point for `weekly_run` (the "weekly" cron job). Checks only `weekly_run`
    itself (`equivalent_kinds_for("weekly_run")`, see the module docstring's Scope note): an
    active `daily_run` never blocks it and it never blocks a `daily_run`; a second `weekly_run`
    racing this one is still refused."""
    try:
        return admit_run(
            "weekly_run", {"mode": mode}, priority=priority, equivalent_kinds=equivalent_kinds_for("weekly_run")
        )
    except RunAlreadyActive as exc:
        log.info("scheduled_weekly_run_skipped_already_active", existing_job=exc.job)
        return None
