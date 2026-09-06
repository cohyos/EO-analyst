"""Per-run search-query budget (round 4, docs/qa/loop, 2026-09-06 evening incident).

A lightweight, named counter a batch caller resets once at the start of its own run and consumes
once per outbound query. Intended callers: `eoa.tenders.scan` (one counter per `scan_tenders()`
run) and `eoa.patents.scan` (one counter per patent survey/scan run) — see docs/MODULES.md
"Round 4 search" for the exact call-site wiring (not made in this round: `eoa.tenders.scan` and
`eoa.patents.scan` were being edited concurrently by other engineers this round, so only the
primitive is delivered here).

This is deliberately independent of `eoa.search.provider.search()` itself — the budget is a
*caller-side* policy ("don't issue more than N `kind: search` queries per stage run"), not a
property of one query, so a caller checks/consumes it *before* calling `search()` and marks the
remaining sources "deferred" in its own stats when exhausted, instead of the network layer
silently truncating a query the caller thought it made.
"""

from __future__ import annotations

import threading

from eoa.config import settings

_lock = threading.Lock()
_counts: dict[str, int] = {}


def reset(stage: str) -> None:
    """Start a fresh budget window for ``stage`` (e.g. "tenders", "patents"). Call once per run."""
    with _lock:
        _counts[stage] = 0


def remaining(stage: str) -> int:
    """Queries left in ``stage``'s current window. A non-positive configured limit means unlimited
    (returns a large sentinel rather than actually being infinite, so callers can still log it)."""
    limit = settings().search.max_queries_per_stage
    if limit <= 0:
        return 10**9
    with _lock:
        used = _counts.get(stage, 0)
    return max(limit - used, 0)


def exhausted(stage: str) -> bool:
    return remaining(stage) <= 0


def try_consume(stage: str) -> bool:
    """Consume one query from ``stage``'s budget if any remains; returns whether it was allowed.

    A caller that gets ``False`` back must not call `eoa.search.provider.search()` for that query —
    it should instead mark the corresponding source "deferred" in its own run stats.
    """
    limit = settings().search.max_queries_per_stage
    with _lock:
        used = _counts.get(stage, 0)
        if limit > 0 and used >= limit:
            return False
        _counts[stage] = used + 1
        return True
