"""F28/N08 (SOL-REVIEW-2026-09-24): the security-review claim + rerun-enqueue transaction, against
a REAL PostgreSQL connection.

Old code: `approve_security_review` called `_claim_pending_review` (its own `db.connection()`,
committed immediately) and THEN `enqueue_job` (a second, separate `db.connection()`). If the
`enqueue_job` call raised for any reason, the review was left permanently `security_review_resolved
= true` with no rerun job ever created -- an approval that silently disappears (N08). New code runs
both statements on one caller-owned `conn` inside a single `with connection() as conn:` block --
an exception during the enqueue rolls the whole transaction back, so the claim itself never
commits either. This test forces `enqueue_job` to raise and asserts the original job's `result`
still shows `security_review_resolved` unset/false afterward -- fails on old code (the claim would
have already committed by the time `enqueue_job` raises).

Skips (does not fail) when DATABASE_URL / a local Postgres is unreachable. Explicitly deletes every
row it creates.

Run with: ``DATABASE_URL=... PYTHONPATH=agent python -m pytest tests/unit/test_security_review_atomic_postgres.py -q``
"""

from __future__ import annotations

import pytest


def _pg_available() -> bool:
    try:
        from eoa.db import ping

        return ping()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="no live Postgres available")


@pytest.fixture()
def flagged_job_id():
    from psycopg.types.json import Json

    from eoa.db import connection

    payload = {"item_id": None, "question": "N08 test question -- safe to delete"}
    result = {
        "security_review": True,
        "security_flag_reason": "test_reason",
        "security_flag_snippet": "test snippet",
    }
    with connection() as conn:
        row = conn.execute(
            "INSERT INTO jobs (kind, state, payload, result, finished_at) "
            "VALUES ('deep_search', 'done', %s, %s, now()) RETURNING id",
            (Json(payload), Json(result)),
        ).fetchone()
        job_id = row["id"]
    try:
        yield job_id
    finally:
        with connection() as conn:
            conn.execute("DELETE FROM run_log WHERE job_id = %s", (job_id,))
            conn.execute("DELETE FROM jobs WHERE id = %s", (job_id,))


class TestApproveSecurityReviewAtomicity:
    def test_enqueue_failure_rolls_back_the_claim(self, flagged_job_id: int, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.api.routes import security_review
        from eoa.db import connection

        def _boom(*_a: object, **_kw: object) -> int:
            raise RuntimeError("simulated enqueue failure")

        monkeypatch.setattr(security_review, "enqueue_job", _boom)

        with pytest.raises(RuntimeError, match="simulated enqueue failure"):
            security_review.approve_security_review(flagged_job_id)

        # THE regression check: the claim must NOT have committed just because the enqueue after
        # it failed -- old code's separate-transaction claim would already be resolved here.
        with connection() as conn:
            row = conn.execute("SELECT result FROM jobs WHERE id = %s", (flagged_job_id,)).fetchone()
        assert row is not None
        assert row["result"].get("security_review_resolved") is not True

        # And the review is therefore still pending -- a second, real approve (no failure this
        # time) still finds and claims it, exactly as if the failed attempt never happened.
        job = security_review._claim_pending_review(flagged_job_id, dismissed=True)
        assert job is not None
        assert job["result"]["security_review_resolved"] is True

    def test_successful_approve_commits_both_claim_and_enqueue_together(
        self, flagged_job_id: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eoa.api.routes import security_review
        from eoa.db import connection

        result = security_review.approve_security_review(flagged_job_id)
        new_job_id = result["job_id"]
        try:
            with connection() as conn:
                original = conn.execute(
                    "SELECT result FROM jobs WHERE id = %s", (flagged_job_id,)
                ).fetchone()
                rerun = conn.execute("SELECT kind, payload FROM jobs WHERE id = %s", (new_job_id,)).fetchone()
            assert original["result"]["security_review_resolved"] is True
            assert rerun is not None
            assert rerun["kind"] == "deep_search"
            assert rerun["payload"]["expanded_from_job_id"] == flagged_job_id
        finally:
            with connection() as conn:
                conn.execute("DELETE FROM run_log WHERE job_id = %s", (new_job_id,))
                conn.execute("DELETE FROM jobs WHERE id = %s", (new_job_id,))
