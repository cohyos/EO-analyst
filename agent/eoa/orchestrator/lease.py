"""Renew job leases independently of model calls and progress callbacks."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

import structlog

from eoa.execution import lease_lost, worker_owner
from eoa.memory.relational import renew_worker_leases

log = structlog.get_logger(__name__)


@contextmanager
def keep_job_lease(job_id: int, worker_id: str, *, interval: float = 30) -> Iterator[None]:
    stop = threading.Event()
    lost = threading.Event()

    def renew() -> None:
        while not stop.wait(interval):
            try:
                # Includes child investigations claimed by the same worker during a daily run.
                owned = renew_worker_leases(worker_id)
                if job_id not in owned:
                    lost.set()
                    return
            except Exception as exc:
                log.warning("lease_renew_failed", job_id=job_id, error=str(exc)[:160])

    thread = threading.Thread(target=renew, name="eoa-lease", daemon=True)
    owner_token = worker_owner.set(worker_id)
    lost_token = lease_lost.set(lost)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
        lease_lost.reset(lost_token)
        worker_owner.reset(owner_token)
