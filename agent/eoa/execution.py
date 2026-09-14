"""Cooperative execution limits shared by stage, search and model calls."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from eoa.errors import DeadlineExceeded, LeaseLost


def has_incomplete_work(stats: object) -> bool:
    """Recognize actual failures/deferrals in nested stage counters and outcomes."""
    if not isinstance(stats, dict):
        return False
    for key, value in stats.items():
        if (key in {"error", "deferred", "skipped", "partial"} or key.endswith("_error")) and value:
            return True
        if (key == "failed" or key.endswith("_failed")) and isinstance(value, (int, float)) and value > 0:
            return True
        if has_incomplete_work(value):
            return True
    return False

_deadline: ContextVar[float | None] = ContextVar("eoa_deadline", default=None)
worker_owner: ContextVar[str | None] = ContextVar("eoa_worker_owner", default=None)
lease_lost: ContextVar[threading.Event | None] = ContextVar("eoa_lease_lost", default=None)


def checkpoint() -> None:
    lost = lease_lost.get()
    if lost is not None and lost.is_set():
        raise LeaseLost("Job lease lost; this worker must stop writing results")
    end = _deadline.get()
    if end is not None and time.monotonic() >= end:
        raise DeadlineExceeded("Stage time budget exhausted")


def timeout_seconds(default: float) -> float:
    checkpoint()
    end = _deadline.get()
    return default if end is None else min(default, max(0.001, end - time.monotonic()))


def sleep(seconds: float) -> None:
    time.sleep(timeout_seconds(seconds))
    checkpoint()


@contextmanager
def deadline_scope(seconds: float) -> Iterator[None]:
    end = time.monotonic() + max(seconds, 0)
    parent = _deadline.get()
    token = _deadline.set(end if parent is None else min(end, parent))
    try:
        checkpoint()
        yield
        checkpoint()
    finally:
        _deadline.reset(token)
