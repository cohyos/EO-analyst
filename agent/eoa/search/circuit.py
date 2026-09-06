"""Per-provider health/circuit breaker (round 4, docs/qa/loop, 2026-09-06 evening incident).

`runtime/logs/orchestrator.2026-09-06.log` showed ddgs racking up 21 "operation timed out"
failures and Google (one of the ddgs text backends) returning 12 captcha "sorry" pages in a
single day; every failing query still paid the full ~15-30s timeout before ``provider.search``
gave up, and the tender/patent scans kept hammering the same dead backend for hours.

This module tracks consecutive failures per provider name (``"ddgs"`` / ``"searxng"``). After
``circuit_fail_threshold`` consecutive failures the circuit opens for an exponentially growing
cool-down (``circuit_base_cooldown_minutes``, doubling on every repeat failure, capped at
``circuit_max_cooldown_minutes``); while open, ``allow()`` returns ``False`` instantly (no network
call, no timeout cost) so ``eoa.search.provider.search`` can skip straight to the next provider in
the rotation. One structlog line is emitted per state change (opened/reopened/closed) — never per
query, per the round-4 spec (a busy night must not spam the log once per rejected query).

Process-local, in-memory only (module-level registry): the orchestrator worker and the API server
are separate processes in this deployment (see docs/MODULES.md), so each gets its own view of
provider health. That is an accepted trade-off, not a bug — the expensive resource being protected
is the *current process's* outbound network timeouts; a stale "open" circuit in one process costs
that process nothing extra once it's using the file cache too (see `eoa.search.cache`), and a
fresh process (e.g. after a supervisor restart) re-learning provider health from scratch is
strictly safer than a lie surviving a restart.
"""

from __future__ import annotations

import threading
import time

import structlog

from eoa.config import settings

log = structlog.get_logger(__name__)


class ProviderCircuit:
    """Consecutive-failure circuit breaker for one search provider."""

    def __init__(
        self,
        name: str,
        *,
        fail_threshold: int,
        base_cooldown_s: float,
        max_cooldown_s: float,
    ) -> None:
        self.name = name
        self.fail_threshold = max(fail_threshold, 1)
        self.base_cooldown_s = max(base_cooldown_s, 1.0)
        self.max_cooldown_s = max(max_cooldown_s, self.base_cooldown_s)
        self._lock = threading.Lock()
        self.consecutive_failures = 0
        self.open_count = 0  # number of times opened; drives the exponential backoff
        self.state = "closed"  # "closed" | "open"
        self.opened_at: float | None = None
        self.cooldown_s = 0.0

    def allow(self, *, now: float | None = None) -> bool:
        """True if a real network attempt should be made; False to skip this provider instantly.

        A closed circuit always allows. An open circuit allows exactly one "probe" attempt once
        its cool-down has elapsed (a half-open retry) — the probe's own success/failure then
        drives ``record_success``/``record_failure`` as usual; it is not tracked as a separate
        state so a second concurrent caller during the same instant also gets to try (cheap and
        harmless: worst case is two real requests instead of one during the retry moment).
        """
        with self._lock:
            if self.state != "open":
                return True
            now = time.monotonic() if now is None else now
            assert self.opened_at is not None
            return now >= self.opened_at + self.cooldown_s

    def record_success(self) -> None:
        with self._lock:
            was_unhealthy = self.state == "open" or self.consecutive_failures > 0
            self.state = "closed"
            self.consecutive_failures = 0
            self.open_count = 0
            self.opened_at = None
            self.cooldown_s = 0.0
        if was_unhealthy:
            log.info("search_circuit_closed", provider=self.name)

    def record_failure(self, reason: str = "") -> None:
        with self._lock:
            self.consecutive_failures += 1
            if self.consecutive_failures < self.fail_threshold:
                return
            was_open = self.state == "open"
            self.open_count += 1
            self.cooldown_s = min(self.base_cooldown_s * (2 ** (self.open_count - 1)), self.max_cooldown_s)
            self.state = "open"
            self.opened_at = time.monotonic()
            cooldown_s = self.cooldown_s
        event = "search_circuit_reopened" if was_open else "search_circuit_opened"
        log.warning(event, provider=self.name, cooldown_s=round(cooldown_s, 1), reason=reason[:160])


_circuits: dict[str, ProviderCircuit] = {}
_circuits_lock = threading.Lock()


def get_circuit(name: str) -> ProviderCircuit:
    """Return the (lazily created, process-wide) circuit for ``name`` (e.g. "ddgs", "searxng")."""
    with _circuits_lock:
        circuit = _circuits.get(name)
        if circuit is None:
            cfg = settings().search
            circuit = ProviderCircuit(
                name,
                fail_threshold=cfg.circuit_fail_threshold,
                base_cooldown_s=cfg.circuit_base_cooldown_minutes * 60,
                max_cooldown_s=cfg.circuit_max_cooldown_minutes * 60,
            )
            _circuits[name] = circuit
        return circuit


def reset_all() -> None:
    """Test/ops helper: drop every tracked circuit so the next `get_circuit` starts fresh."""
    with _circuits_lock:
        _circuits.clear()
