"""Regression test for `eoa.pipeline.dedup.link_cross_language` (2026-09-17, story-clustering
task investigation): last night's nightly run logged `dedup_xlang: linked=0`. Live evidence (the
Rafael SPICE-1000/F-35 story, 6 items) showed why: the query used to require `b.domain = a.domain`
-- an exact match on the item's TAXONOMY classification (`eoa.pipeline.classify`), not the outlet.
The 6 real SPICE items came back tagged with FIVE different domains (`computer_vision`,
`secondary`, `airborne_pods`, `out_of_scope`, `NULL`) for the exact same event, so the equality
gate made the stage a near-total no-op -- the only Hebrew item's one same-domain candidate was
already `dedup_of`-linked (excluded from the join by `dedup_of IS NULL`), leaving zero eligible
pairs. The fix drops the domain-equality requirement (the >=2 shared distinctive entities + tight
time window already do the real "same story" work) while still excluding `out_of_scope`/
`secondary` items on BOTH sides (previously only checked on `a`).

This can't be a real end-to-end DB test without a live Postgres (out of this task's scope per the
"scoped pytest only" rule) -- it instead asserts on the exact SQL text `link_cross_language` sends,
so a future re-introduction of the domain-equality gate (or dropping the out_of_scope/secondary
guard) fails loudly here instead of silently reintroducing the bug.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_dedup_xlang.py -q``
"""

from __future__ import annotations

import pytest


class _FakeResult:
    def fetchall(self) -> list:
        return []


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: tuple[str, tuple] | None = None

    def execute(self, query: str, params: tuple = ()) -> _FakeResult:
        self.executed = (query, params)
        return _FakeResult()

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.fixture()
def fake_conn(monkeypatch: pytest.MonkeyPatch) -> _FakeConnection:
    from eoa import db

    conn = _FakeConnection()
    monkeypatch.setattr(db, "connection", lambda: conn)
    return conn


class TestLinkCrossLanguageQuery:
    def test_does_not_require_exact_domain_match(self, fake_conn: _FakeConnection) -> None:
        from eoa.pipeline.dedup import link_cross_language

        link_cross_language(lookback_days=7)
        query, _ = fake_conn.executed
        assert "b.domain = a.domain" not in query

    def test_excludes_out_of_scope_and_secondary_on_both_sides(self, fake_conn: _FakeConnection) -> None:
        from eoa.pipeline.dedup import link_cross_language

        link_cross_language(lookback_days=7)
        query, _ = fake_conn.executed
        assert "a.domain NOT IN ('out_of_scope', 'secondary')" in query
        assert "b.domain NOT IN ('out_of_scope', 'secondary')" in query

    def test_still_requires_different_language_and_shared_entities(self, fake_conn: _FakeConnection) -> None:
        from eoa.pipeline.dedup import link_cross_language

        link_cross_language(lookback_days=7)
        query, _ = fake_conn.executed
        assert "b.lang IS DISTINCT FROM a.lang" in query
        assert "cardinality(ARRAY(SELECT unnest(a.entities_mentioned) INTERSECT SELECT unnest(b.entities_mentioned))) >= 2" in query

    def test_lookback_days_param_is_passed_through(self, fake_conn: _FakeConnection) -> None:
        from eoa.pipeline.dedup import link_cross_language

        link_cross_language(lookback_days=14)
        _, params = fake_conn.executed
        assert params == (14,)
