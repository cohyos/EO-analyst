"""Tests for `eoa.notify.retry` (R02, SOL-REVIEW3-2026-09-24 blocker 3): bounded, scheduled
retries for failed notification deliveries.

No DB: `eoa.memory.relational.claim_notification_pending`/`mark_notification_result` and
`eoa.notify.retry._due_failed_notifications` are monkeypatched, mirroring
`tests/unit/test_jobs_status.py`'s stubbing style for the same functions. Several tests back the
claim/result pair with a small in-memory state machine (`_FakeNotificationsTable`) instead of a
plain mock so the actual pending -> failed -> due -> pending -> sent transitions -- including the
backoff schedule and the attempts cap -- are exercised end to end, the same technique
`TestNotifyIdempotency` in `test_jobs_status.py` uses for the claim/result pair alone.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_notification_retry.py -q``
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from eoa.notify import ntfy
from eoa.notify import retry as retry_mod
from eoa.notify.retry import retry_failed_notifications


class _FakeNotificationsTable:
    """In-memory stand-in for the `notifications_sent` state machine: `claim_notification_pending`
    + `mark_notification_result` + the retry sweep's due-selection query
    (`_due_failed_notifications`), enough to reproduce the real Postgres predicates in
    `eoa.memory.relational`/`eoa.notify.retry` without a live connection."""

    def __init__(self, max_attempts: int = 5, now: datetime | None = None) -> None:
        self.max_attempts = max_attempts
        self.now = now or datetime.now(tz=UTC)
        self.rows: dict[tuple[str, str], dict[str, Any]] = {}

    def seed_failed(
        self,
        kind: str,
        key: str,
        *,
        attempts: int,
        payload: dict[str, Any] | None,
        next_attempt_at: datetime | None,
    ) -> None:
        self.rows[(kind, key)] = {
            "status": "failed",
            "attempts": attempts,
            "payload": payload,
            "next_attempt_at": next_attempt_at,
            "stale": False,
        }

    def seed_stale_pending(self, kind: str, key: str, *, attempts: int, payload: dict[str, Any] | None) -> None:
        """A claim whose worker crashed mid-send: `pending`, older than the stale window."""
        self.rows[(kind, key)] = {
            "status": "pending",
            "attempts": attempts,
            "payload": payload,
            "next_attempt_at": None,
            "stale": True,
        }

    def claim(
        self,
        kind: str,
        key: str,
        *,
        payload: dict[str, Any] | None = None,
        respect_backoff: bool = False,
        stale_minutes: int = 10,
    ) -> bool:
        row = self.rows.get((kind, key))
        if row is None:
            self.rows[(kind, key)] = {
                "status": "pending",
                "attempts": 1,
                "payload": payload,
                "next_attempt_at": None,
                "stale": False,
            }
            return True
        due = row["next_attempt_at"] is None or row["next_attempt_at"] <= self.now
        failed_ok = (
            row["status"] == "failed" and row["attempts"] < self.max_attempts and (not respect_backoff or due)
        )
        stale_ok = (
            row["status"] == "pending"
            and row["stale"]
            and (not respect_backoff or row["attempts"] < self.max_attempts)
        )
        if failed_ok or stale_ok:
            row["status"] = "pending"
            row["attempts"] += 1
            row["stale"] = False
            if payload is not None:
                row["payload"] = payload
            return True
        return False

    def result(self, kind: str, key: str, *, ok: bool, payload: dict[str, Any] | None = None) -> None:
        row = self.rows[(kind, key)]
        row["status"] = "sent" if ok else "failed"
        if payload is not None:
            row["payload"] = payload
        if ok:
            row["next_attempt_at"] = None
        else:
            attempts = row["attempts"]
            if attempts <= 1:
                backoff = timedelta(minutes=10)
            elif attempts == 2:
                backoff = timedelta(minutes=30)
            else:
                backoff = timedelta(hours=2)
            row["next_attempt_at"] = self.now + backoff

    def due_failed(self, limit: int) -> list[dict[str, Any]]:
        out = []
        for (kind, key), row in self.rows.items():
            if row["attempts"] >= self.max_attempts or row["payload"] is None:
                continue
            failed_due = row["status"] == "failed" and (
                row["next_attempt_at"] is None or row["next_attempt_at"] <= self.now
            )
            stale_pending = row["status"] == "pending" and row["stale"]
            if not (failed_due or stale_pending):
                continue
            out.append({"kind": kind, "key": key, "payload": row["payload"]})
        return out[:limit]

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("eoa.notify.retry.claim_notification_pending", self.claim)
        monkeypatch.setattr("eoa.notify.retry.mark_notification_result", self.result)
        monkeypatch.setattr("eoa.notify.retry._due_failed_notifications", lambda limit: self.due_failed(limit))


_PAYLOAD = {"title": "x", "body": "y", "priority": "high", "tags": ["x"], "click": None}


class TestDueQuery:
    """`_due_failed_notifications`'s SQL is the one piece that cannot be exercised without a real
    Postgres connection (per the task brief: no live-DB access from unit tests) -- these assert the
    query text/params actually encode the required predicates (status/attempts/payload/
    next_attempt_at) rather than trusting the docstring."""

    def test_query_filters_on_status_attempts_payload_and_due_time(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        class _FakeCursor:
            def execute(self, sql: str, params: dict | None = None) -> None:
                captured["sql"] = sql
                captured["params"] = params

            def fetchall(self):
                return []

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _FakeConn:
            def cursor(self, **kwargs):
                captured["cursor_kwargs"] = kwargs
                return _FakeCursor()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr("eoa.db.connection", lambda: _FakeConn())

        rows = retry_mod._due_failed_notifications(7)

        assert rows == []
        sql = captured["sql"]
        assert "status = 'failed'" in sql
        assert "attempts < %(max_attempts)s" in sql
        assert "payload IS NOT NULL" in sql
        assert "next_attempt_at IS NULL OR next_attempt_at <= now()" in sql
        # T04 (SOL-REVIEW4-2026-09-24): stale `pending` rows (a crashed send) are candidates too
        assert "status = 'pending'" in sql
        assert "updated_at < now() - (%(stale_minutes)s || ' minutes')::interval" in sql
        # the attempts cap / stale window must be the SAME constants claim_notification_pending
        # uses, not independent hardcoded numbers that could drift out of sync.
        from eoa.memory.relational import NOTIFICATION_MAX_ATTEMPTS, NOTIFICATION_PENDING_STALE_MINUTES

        assert captured["params"] == {
            "max_attempts": NOTIFICATION_MAX_ATTEMPTS,
            "stale_minutes": NOTIFICATION_PENDING_STALE_MINUTES,
            "limit": 7,
        }


class TestT04StalePendingAndBackoffAtClaim:
    """T04 (SOL-REVIEW4-2026-09-24): (a) a send that crashed mid-flight left the row `pending`
    forever -- the sweep selected only `failed`; (b) the sweep's SELECT and claim were separate, so
    a replay that failed in between could be resent immediately, bypassing its backoff."""

    def test_stale_pending_row_is_resent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        table = _FakeNotificationsTable()
        table.seed_stale_pending("daily_report", "9", attempts=1, payload=_PAYLOAD)
        table.install(monkeypatch)
        sent: list[dict] = []
        monkeypatch.setattr(
            "eoa.notify.retry.ntfy.send", lambda **kw: sent.append(kw) or ntfy.Sent(True, "i", "u")
        )

        counts = retry_failed_notifications(limit=10)

        assert sent == [_PAYLOAD]
        assert table.rows[("daily_report", "9")]["status"] == "sent"
        assert counts["sent"] == 1

    def test_sweep_claims_with_backoff_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The candidate list is stale by the time of the claim: a replay failed in between and
        scheduled a new backoff. The sweep's claim (respect_backoff=True) must refuse it."""
        now = datetime.now(tz=UTC)
        table = _FakeNotificationsTable(now=now)
        table.seed_failed("daily_report", "3", attempts=2, payload=_PAYLOAD, next_attempt_at=None)
        table.install(monkeypatch)
        stale_candidates = table.due_failed(10)
        table.rows[("daily_report", "3")]["next_attempt_at"] = now + timedelta(minutes=30)  # replay failed
        monkeypatch.setattr("eoa.notify.retry._due_failed_notifications", lambda limit: stale_candidates)
        monkeypatch.setattr("eoa.notify.retry.ntfy.send", _fail_if_sent)

        counts = retry_failed_notifications(limit=10)

        assert counts == {"considered": 1, "sent": 0, "failed": 0, "skipped": 1}

    def test_claim_sql_enforces_backoff_and_stores_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.memory import relational

        captured: dict[str, Any] = {}

        class _Cur:
            def execute(self, sql, params=None):
                captured["sql"], captured["params"] = sql, params

            def fetchone(self):
                return {"id": 1}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Conn:
            def cursor(self, **_kw):
                return _Cur()

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(relational, "connection", lambda: _Conn())
        assert relational.claim_notification_pending("k", "1", payload=_PAYLOAD, respect_backoff=True)
        sql = captured["sql"]
        assert "NOT %(respect_backoff)s" in sql
        assert "notifications_sent.next_attempt_at <= now()" in sql
        assert "payload = COALESCE(EXCLUDED.payload, notifications_sent.payload)" in sql
        assert captured["params"]["respect_backoff"] is True
        assert captured["params"]["payload"] is not None


def _fail_if_sent(**_kw):
    raise AssertionError("must not resend before the backoff is due")


class TestRetryFailedNotifications:
    def test_resends_due_failed_row_and_marks_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        table = _FakeNotificationsTable()
        table.seed_failed("daily_report", "1", attempts=1, payload=_PAYLOAD, next_attempt_at=None)
        table.install(monkeypatch)
        sent_calls: list[dict] = []
        monkeypatch.setattr(
            "eoa.notify.retry.ntfy.send",
            lambda **kw: sent_calls.append(kw) or ntfy.Sent(True, "id1", "http://x"),
        )

        counts = retry_failed_notifications(limit=10)

        assert sent_calls == [_PAYLOAD]
        assert table.rows[("daily_report", "1")]["status"] == "sent"
        assert counts == {"considered": 1, "sent": 1, "failed": 0, "skipped": 0}

    def test_not_yet_due_row_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        now = datetime.now(tz=UTC)
        table = _FakeNotificationsTable(now=now)
        table.seed_failed(
            "daily_report", "2", attempts=1, payload=_PAYLOAD, next_attempt_at=now + timedelta(minutes=5)
        )
        table.install(monkeypatch)
        sent_calls: list[dict] = []
        monkeypatch.setattr(
            "eoa.notify.retry.ntfy.send", lambda **kw: sent_calls.append(kw) or ntfy.Sent(True, None, "u")
        )

        counts = retry_failed_notifications()

        assert not sent_calls
        assert table.rows[("daily_report", "2")]["status"] == "failed"  # untouched
        assert counts == {"considered": 0, "sent": 0, "failed": 0, "skipped": 0}

    def test_attempts_cap_excludes_exhausted_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.memory.relational import NOTIFICATION_MAX_ATTEMPTS

        table = _FakeNotificationsTable()
        table.seed_failed(
            "daily_report",
            "3",
            attempts=NOTIFICATION_MAX_ATTEMPTS,
            payload=_PAYLOAD,
            next_attempt_at=None,
        )
        table.install(monkeypatch)
        sent_calls: list[dict] = []
        monkeypatch.setattr(
            "eoa.notify.retry.ntfy.send", lambda **kw: sent_calls.append(kw) or ntfy.Sent(True, None, "u")
        )

        counts = retry_failed_notifications()

        assert not sent_calls
        assert counts == {"considered": 0, "sent": 0, "failed": 0, "skipped": 0}

    def test_legacy_row_with_no_payload_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        table = _FakeNotificationsTable()
        table.seed_failed("daily_report", "4", attempts=1, payload=None, next_attempt_at=None)
        table.install(monkeypatch)
        sent_calls: list[dict] = []
        monkeypatch.setattr(
            "eoa.notify.retry.ntfy.send", lambda **kw: sent_calls.append(kw) or ntfy.Sent(True, None, "u")
        )

        counts = retry_failed_notifications()

        assert not sent_calls
        assert counts == {"considered": 0, "sent": 0, "failed": 0, "skipped": 0}

    def test_exception_during_resend_marks_failed_and_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        table = _FakeNotificationsTable()
        table.seed_failed("daily_report", "5", attempts=1, payload=_PAYLOAD, next_attempt_at=None)
        table.install(monkeypatch)

        def _raise(**_kw):
            raise RuntimeError("ntfy server unreachable")

        monkeypatch.setattr("eoa.notify.retry.ntfy.send", _raise)

        counts = retry_failed_notifications()

        assert table.rows[("daily_report", "5")]["status"] == "failed"
        assert counts == {"considered": 1, "sent": 0, "failed": 1, "skipped": 0}

    def test_lost_claim_race_is_skipped_not_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A concurrent job replay (or another sweep tick) can claim the same row first --
        `claim_notification_pending` returning False must make the sweep move on, never send."""
        table = _FakeNotificationsTable()
        table.seed_failed("daily_report", "6", attempts=1, payload=_PAYLOAD, next_attempt_at=None)
        table.install(monkeypatch)
        monkeypatch.setattr("eoa.notify.retry.claim_notification_pending", lambda kind, key, **_k: False)
        sent_calls: list[dict] = []
        monkeypatch.setattr(
            "eoa.notify.retry.ntfy.send", lambda **kw: sent_calls.append(kw) or ntfy.Sent(True, None, "u")
        )

        counts = retry_failed_notifications()

        assert not sent_calls
        assert counts == {"considered": 1, "sent": 0, "failed": 0, "skipped": 1}

    def test_query_error_returns_zero_counts_without_raising(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _raise(limit):
            raise RuntimeError("no db in unit tests")

        monkeypatch.setattr("eoa.notify.retry._due_failed_notifications", _raise)

        counts = retry_failed_notifications()

        assert counts == {"considered": 0, "sent": 0, "failed": 0, "skipped": 0}


class TestNotifyStoresPayloadAndSchedulesRetry:
    """R02: `_notify` (eoa.orchestrator.jobs) must store a resendable `payload` and schedule a
    `next_attempt_at` on a `Sent(ok=False)` failure -- the actual gap this blocker closes: a
    `failed` row must become visible to `retry_failed_notifications`'s due-selection."""

    def test_failed_send_leaves_row_retryable_by_the_sweep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import types

        from eoa.orchestrator.jobs import RunState, _notify

        table = _FakeNotificationsTable()
        monkeypatch.setattr("eoa.memory.relational.claim_notification_pending", table.claim)
        monkeypatch.setattr("eoa.memory.relational.mark_notification_result", table.result)
        monkeypatch.setattr("eoa.db.connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
        monkeypatch.setattr(
            "eoa.orchestrator.jobs.ntfy.report_ready",
            lambda *a, **kw: ntfy.Sent(False, None, "http://x"),
        )

        rs = RunState(job_id=42)
        result = _notify(rs, paths=types.SimpleNamespace(docx="output/reports/2026-09-04.docx"))

        assert "notification_error" in result
        row = table.rows[("daily_report", "42")]
        assert row["status"] == "failed"
        assert row["payload"] is not None  # resendable
        assert row["next_attempt_at"] is not None  # scheduled, not left indefinitely stuck
        assert row["next_attempt_at"] > table.now  # backoff -- not due THIS instant

        # Not due yet (backoff hasn't elapsed).
        assert table.due_failed(10) == []

        # Once the backoff window passes, the row is exactly what the sweep looks for.
        table.now = row["next_attempt_at"] + timedelta(seconds=1)
        assert table.due_failed(10) == [{"kind": "daily_report", "key": "42", "payload": row["payload"]}]
