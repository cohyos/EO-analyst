"""N02/F03 (SOL-REVIEW-2026-09-24, jobs/orchestrator/LLM audit round 2): the state transition
"expire -> reap -> cancel -> claim returns nothing" against a REAL PostgreSQL connection.

Old code's gap: `eoa.api.services.cancel_job` on a `deferred` job only set `payload.stop = true`
and left `state = 'deferred'` -- nothing checks that flag for a job that isn't currently executing
(`eoa.search.deep_search`'s cooperative stop check only applies to a `running` investigation's own
loop), and `eoa.memory.relational.claim_next_job` had no guard on it either, so a "cancelled"
deferred job stayed claimable and a later worker actually ran it. This test exercises the real
functions (not a mock) against a live database: insert a job as `running` with an already-expired
lease (simulating a crashed worker) -> `reap_stale_jobs()` requeues it `deferred` -> `cancel_job()`
-> `claim_next_job()` must return nothing for it. Fails on old code (the old `cancel_job`+
`claim_next_job` pair lets the claim succeed).

Uses a dedicated, never-production `kind` (`"__test_jobs_state_machine__"`) so `claim_next_job`'s
kind filter can never pick up a real job, and explicitly deletes every row it creates in a
`finally` block (no `run_log`/`jobs` rows are left behind) -- per-call `db.connection()` commits
its own transaction, so a top-level rollback isn't available here (unlike a single-transaction
test); explicit cleanup is the correct substitute, same convention as
`tests/unit/test_patents_heatmap_postgres.py`'s "skip if Postgres is unreachable" guard.

Run with: ``DATABASE_URL=... PYTHONPATH=agent python -m pytest tests/unit/test_jobs_cancel_reap_claim_postgres.py -q``
"""

from __future__ import annotations

import pytest

TEST_KIND = "__test_jobs_state_machine__"


def _pg_available() -> bool:
    try:
        from eoa.db import ping

        return ping()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="no live Postgres available")


@pytest.fixture()
def test_job_id():
    from eoa.db import connection

    with connection() as conn:
        row = conn.execute(
            "INSERT INTO jobs (kind, state, started_at, worker_id, lease_expires_at, attempts) "
            "VALUES (%s, 'running', now(), 'dead-test-worker', now() - interval '1 hour', 1) "
            "RETURNING id",
            (TEST_KIND,),
        ).fetchone()
        job_id = row["id"]
    try:
        yield job_id
    finally:
        with connection() as conn:
            conn.execute("DELETE FROM run_log WHERE job_id = %s", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


class TestExpireReapCancelClaim:
    def test_expired_lease_reaped_then_cancelled_is_never_reclaimed(self, test_job_id: int) -> None:
        from eoa.api.services import cancel_job
        from eoa.db import connection
        from eoa.memory.relational import claim_next_job, reap_stale_jobs

        job_id = test_job_id

        # -- expire: the fixture already inserted the row `running` with a lease in the past.
        with connection() as conn:
            row = conn.execute("SELECT state FROM jobs WHERE id = %s", (job_id,)).fetchone()
        assert row["state"] == "running"

        # -- reap: an expired running lease with a low attempt count is requeued `deferred`, not
        # terminalized outright (F03).
        reaped = reap_stale_jobs(max_age_hours=6)
        assert reaped >= 1
        with connection() as conn:
            row = conn.execute("SELECT state, not_before FROM jobs WHERE id = %s", (job_id,)).fetchone()
        assert row["state"] == "deferred"

        # Force `not_before` into the past so the job would otherwise be immediately claimable --
        # isolates "cancel" as the only reason claim_next_job must not return it below.
        with connection() as conn:
            conn.execute(
                "UPDATE jobs SET not_before = now() - interval '1 minute' WHERE id = %s", (job_id,)
            )

        # -- cancel: N02 fix -- a `deferred` job is terminalized immediately (state='failed'), not
        # merely flagged with payload.stop (which claim_next_job never checked while the row
        # stayed `deferred`).
        cancelled = cancel_job(job_id)
        assert cancelled is not None
        assert cancelled["state"] == "failed"
        assert cancelled["error"] == "cancelled_by_user"

        # -- claim: THE regression check. Old code: claim_next_job returns this job (state was
        # still 'deferred', not_before had passed, and nothing checked payload.stop). New code:
        # the job is 'failed' (excluded by claim_next_job's `state IN ('queued','deferred')`), so
        # nothing is returned for this test kind.
        claimed = claim_next_job([TEST_KIND], worker_id="test-claimer")
        assert claimed is None

    def test_claim_next_job_never_returns_a_stopped_deferred_job(self, test_job_id: int) -> None:
        """Defense-in-depth guard directly: even a `deferred` row that somehow still carries
        `payload.stop = true` (bypassing `cancel_job`'s own terminalization, e.g. a future code
        path that forgets it) must never be claimable. Old code: no such guard existed in
        `claim_next_job`'s WHERE clause, so this job WOULD be returned."""
        from eoa.db import connection
        from eoa.memory.relational import claim_next_job

        job_id = test_job_id
        with connection() as conn:
            conn.execute(
                "UPDATE jobs SET state = 'deferred', not_before = now() - interval '1 minute', "
                "payload = '{\"stop\": true}'::jsonb WHERE id = %s",
                (job_id,),
            )

        claimed = claim_next_job([TEST_KIND], worker_id="test-claimer")
        assert claimed is None
