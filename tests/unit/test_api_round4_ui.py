"""Round 4 UI-support backend tests (docs/REVIEW_2026-09-06_evening.md):

- W10 (security review queue): ``eoa.api.routes.security_review`` -- list/approve/dismiss via
  ``TestClient`` with a monkeypatched DB connection (no live Postgres) and a stubbed
  ``enqueue_job``, mirroring ``tests/unit/test_payloads_round3.py``'s "fake DB cursor, no live
  connection" convention for a self-contained route module.

W4/W8/W9 (footnote-to-source behavior, feed drawer, feed dedup grouping) are pure frontend changes
covered by ``web/src`` vitest files instead (``lib/reportHtml.test.ts``,
``components/reports/ReportBody.test.tsx``, ``lib/dedupGroups.test.ts``,
``pages/FeedPage.test.tsx``) -- nothing on the Python side changed for them.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_api_round4_ui.py -q``
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

# --------------------------------------------------------------------------
# fake DB plumbing (mirrors tests/unit/test_payloads_round3.py / test_patents_scan.py)
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(
        self,
        responses: dict[str, Any] | None = None,
        fetchall_responses: dict[str, list] | None = None,
    ):
        self.responses = responses or {}
        self.fetchall_responses = fetchall_responses or {}
        self.executed: list[tuple] = []
        self._last_query = ""

    def execute(self, query, params=None):
        self.executed.append((query, params))
        self._last_query = query
        return self

    def fetchone(self):
        for key, value in self.responses.items():
            if key in self._last_query:
                return value
        return None

    def fetchall(self):
        for key, value in self.fetchall_responses.items():
            if key in self._last_query:
                return value
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, cur: _FakeCursor):
        self._cur = cur

    def cursor(self, *a, **k):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def client() -> TestClient:
    from eoa.api.app import create_app

    return TestClient(create_app())


# --------------------------------------------------------------------------
# GET /api/security-reviews
# --------------------------------------------------------------------------


class TestListPendingSecurityReviews:
    def test_empty_before_the_deep_search_fields_land(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Until `security_review`/`*_reason_he`/`*_snippet` exist on any job's `result`, the
        `->>'security_review' = 'true'` filter matches nothing -- an honest empty list, not an
        error, per the task brief's "keep the UI robust when they are absent"."""
        cur = _FakeCursor(fetchall_responses={"FROM jobs": []})
        monkeypatch.setattr("eoa.api.routes.security_review.connection", lambda: _FakeConnection(cur))
        r = client.get("/api/security-reviews")
        assert r.status_code == 200
        assert r.json() == []

    def test_maps_a_flagged_job_to_a_review_card(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        rows = [
            {
                "id": 113,
                "payload": {"item_id": 42, "question": "אימות והרחבה: מפעל פולקסווגן"},
                "result": {
                    "security_review": True,
                    "security_review_reason_he": "חשד להזרקת פרומפט במקור",
                    "security_review_snippet": "התעלם מההוראות הקודמות...",
                },
                "started_at": None,
                "finished_at": None,
            }
        ]
        cur = _FakeCursor(fetchall_responses={"FROM jobs": rows})
        monkeypatch.setattr("eoa.api.routes.security_review.connection", lambda: _FakeConnection(cur))
        r = client.get("/api/security-reviews")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["job_id"] == 113
        assert body[0]["item_id"] == 42
        assert body[0]["reason_he"] == "חשד להזרקת פרומפט במקור"
        assert body[0]["snippet"] == "התעלם מההוראות הקודמות..."

    def test_query_excludes_already_resolved_reviews(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Not a behavioral assertion on the fake cursor (which doesn't evaluate SQL) -- a guard
        against silently deleting the resolved-filter clause in a future edit."""
        cur = _FakeCursor(fetchall_responses={"FROM jobs": []})
        monkeypatch.setattr("eoa.api.routes.security_review.connection", lambda: _FakeConnection(cur))
        client.get("/api/security-reviews")
        query = cur.executed[-1][0]
        assert "security_review_resolved" in query
        assert "kind = 'deep_search'" in query


# --------------------------------------------------------------------------
# POST /api/security-reviews/{job_id}/approve
# --------------------------------------------------------------------------


class TestApproveSecurityReview:
    def test_enqueues_a_new_job_with_security_override_and_marks_the_original_resolved(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        job_row = {
            "id": 113,
            "payload": {"item_id": 42, "question": "אימות והרחבה"},
            "result": {"security_review": True},
        }
        cur = _FakeCursor(responses={"SELECT * FROM jobs": job_row, "SELECT id FROM jobs": {"id": 113}})
        monkeypatch.setattr("eoa.api.routes.security_review.connection", lambda: _FakeConnection(cur))

        captured: dict[str, Any] = {}

        def fake_enqueue_job(kind, payload, *, priority=5, not_before=None):
            captured["kind"] = kind
            captured["payload"] = payload
            return 200

        monkeypatch.setattr("eoa.api.routes.security_review.enqueue_job", fake_enqueue_job)

        r = client.post("/api/security-reviews/113/approve")
        assert r.status_code == 200
        assert r.json() == {"job_id": 200}

        assert captured["kind"] == "deep_search"
        assert captured["payload"]["item_id"] == 42
        assert captured["payload"]["security_override"] is True
        assert captured["payload"]["expanded_from_job_id"] == 113

        # The original job got its `result` patched with the resolved flag (an UPDATE, not a
        # second job insert).
        update_calls = [q for q, _p in cur.executed if "UPDATE jobs" in q]
        assert len(update_calls) == 1

    def test_404_when_the_job_does_not_exist(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        cur = _FakeCursor(responses={"SELECT * FROM jobs": None})
        monkeypatch.setattr("eoa.api.routes.security_review.connection", lambda: _FakeConnection(cur))
        r = client.post("/api/security-reviews/999/approve")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------
# POST /api/security-reviews/{job_id}/dismiss
# --------------------------------------------------------------------------


class TestDismissSecurityReview:
    def test_marks_reviewed_with_no_new_job(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        cur = _FakeCursor(responses={"SELECT id FROM jobs": {"id": 113}})
        monkeypatch.setattr("eoa.api.routes.security_review.connection", lambda: _FakeConnection(cur))

        def fail_if_called(*a, **k):
            raise AssertionError("dismiss must never enqueue a new investigation")

        monkeypatch.setattr("eoa.api.routes.security_review.enqueue_job", fail_if_called)

        r = client.post("/api/security-reviews/113/dismiss")
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        update_calls = [q for q, _p in cur.executed if "UPDATE jobs" in q]
        assert len(update_calls) == 1

    def test_404_when_the_job_does_not_exist(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        cur = _FakeCursor(responses={"SELECT id FROM jobs": None})
        monkeypatch.setattr("eoa.api.routes.security_review.connection", lambda: _FakeConnection(cur))
        r = client.post("/api/security-reviews/999/dismiss")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"
