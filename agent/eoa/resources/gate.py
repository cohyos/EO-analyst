"""Compute Resource Gate (FR-14): the single checkpoint before every model call.

Decision flow for ``acquire(role)``:

1. Read host telemetry (GPU VRAM/util/temp, RAM, disk, Ollama loaded models).
2. Hard stops: disk below minimum, GPU above stop temperature -> ``ResourceUnavailable``.
3. Thermal pause: above pause temperature -> wait (bounded) and re-check.
4. Polite mode (outside the night window, or forced): if external GPU utilisation is above the
   threshold -> batch calls are deferred (``ResourceUnavailable``); interactive calls proceed.
5. VRAM: if the requested model is already loaded -> proceed. Otherwise the free VRAM (plus what
   would be freed by unloading other Ollama models that satisfied the minimum-loaded time) must
   cover the model's estimate + safety margin. If not -> queue with backoff until timeout.

Every decision is written to ``resource_log`` (best-effort) and kept in memory for the status panel.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import structlog

from eoa.config import ModelSpec, settings
from eoa.errors import ResourceUnavailable
from eoa.resources import gpu as telemetry

log = structlog.get_logger(__name__)

Decision = Literal["proceed", "queued", "deferred", "swap", "throttled", "thermal_pause"]


@dataclass
class GateDecision:
    decision: Decision
    model: str
    vram_free_mb: int
    gpu_util: int
    gpu_temp: int
    ram_free_mb: int
    wait_ms: int
    reason: str
    at: datetime


class ResourceGate:
    """Thread-safe gate; one instance per process (see ``gate()``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded_since: dict[str, float] = {}
        self.history: deque[GateDecision] = deque(maxlen=200)
        self.force_night_mode: bool = False
        self._sleep = time.sleep  # patched in tests

    # ------------------------------------------------------------------ helpers
    def _record(self, d: GateDecision) -> None:
        self.history.append(d)
        log.info(
            "gate_decision",
            decision=d.decision,
            model=d.model,
            reason=d.reason,
            vram_free_mb=d.vram_free_mb,
            gpu_util=d.gpu_util,
            gpu_temp=d.gpu_temp,
            wait_ms=d.wait_ms,
        )
        try:  # best-effort persistence; never fail a call because logging failed
            from eoa.memory.relational import record_resource_decision

            record_resource_decision(
                decision=d.decision,
                model=d.model,
                vram_free_mb=d.vram_free_mb,
                gpu_util=d.gpu_util,
                gpu_temp=d.gpu_temp,
                ram_free_mb=d.ram_free_mb,
                wait_ms=d.wait_ms,
            )
        except Exception:
            pass

    @staticmethod
    def _in_night_window(now: datetime | None = None) -> bool:
        from zoneinfo import ZoneInfo

        s = settings()
        tz = ZoneInfo(s.timezone)
        now_local = (now or datetime.now(tz=UTC)).astimezone(tz)
        start_h, start_m = (int(x) for x in s.schedule.night_window.start.split(":"))
        end_h, end_m = (int(x) for x in s.schedule.night_window.end.split(":"))
        cur = now_local.hour * 60 + now_local.minute
        start, end = start_h * 60 + start_m, end_h * 60 + end_m
        return start <= cur < end if start < end else cur >= start or cur < end

    def is_batch_window(self) -> bool:
        return self.force_night_mode or self._in_night_window()

    def _eligible_for_unload(self, host: telemetry.HostStatus, keep: str) -> list[telemetry.LoadedModel]:
        """Other Ollama models that have exceeded the minimum loaded time. A model the gate has never
        seen (loaded by someone else, e.g. before this process started, or by another gate instance)
        counts as eligible too — we have no evidence it was just loaded, so waiting for it would be
        an unbounded (and wrong) assumption."""
        min_loaded = settings().resources.min_loaded_seconds
        now = time.monotonic()
        out = []
        for m in host.loaded_models:
            if m.name == keep:
                continue
            since = self._loaded_since.get(m.name)
            if since is None or now - since >= min_loaded:
                out.append(m)
        return out

    def _reclaimable_vram(self, host: telemetry.HostStatus, keep: str) -> int:
        """VRAM held by other Ollama models that may be unloaded now."""
        return sum(m.size_vram_mb for m in self._eligible_for_unload(host, keep))

    # ------------------------------------------------------------------ main API
    def acquire(self, role: str, *, interactive: bool = False, est_vram_mb: int | None = None) -> ModelSpec:
        """Block until the model for ``role`` may be used; return its spec. Raises ResourceUnavailable.

        Serialised with ``self._lock``: the GPU is a single resource and two concurrent admissions
        could otherwise both pass the VRAM check and then oversubscribe it."""
        with self._lock:
            return self._acquire_locked(role, interactive=interactive, est_vram_mb=est_vram_mb)

    def _acquire_locked(self, role: str, *, interactive: bool, est_vram_mb: int | None) -> ModelSpec:
        s = settings()
        spec = s.model(role)
        need = est_vram_mb or spec.est_vram_mb
        rc = s.resources
        backoffs = list(rc.queue_backoff_seconds) or [5]
        # 2026-09-06 P1: an interactive caller (chat, on-demand actions) gets the short
        # `interactive_wait_s` deadline instead of the patient `queue_timeout_min` nightly/pipeline
        # callers use -- see `ResourcesCfg.interactive_wait_s` for why. Raising
        # `ResourceUnavailable` this quickly is deliberate: the caller (currently `/api/ask`) turns
        # it into an immediate, clear "busy" message instead of the request hanging.
        deadline = time.monotonic() + (rc.interactive_wait_s if interactive else rc.queue_timeout_min * 60)
        waited_ms = 0
        attempt = 0
        model_name = spec.ollama or spec.hf or spec.key

        if spec.runtime != "ollama" or need == 0:
            # CPU-side models (guard classifiers, embeddings on CPU) only need RAM/disk sanity, but
            # transient RAM pressure still queues with backoff instead of failing outright.
            while True:
                host = telemetry.snapshot(s.ollama_url)
                self._check_hard_stops(host, model_name)
                if host.ram_total_mb and host.ram_free_mb < rc.min_free_ram_mb:
                    if time.monotonic() > deadline:
                        self._record(
                            self._decision(
                                "deferred",
                                model_name,
                                host,
                                waited_ms,
                                f"ram {host.ram_free_mb}MB too low; timeout",
                            )
                        )
                        raise ResourceUnavailable(
                            f"RAM free {host.ram_free_mb} MB < {rc.min_free_ram_mb} MB "
                            f"after {waited_ms // 1000}s"
                        )
                    delay = backoffs[min(attempt, len(backoffs) - 1)]
                    self._record(
                        self._decision(
                            "queued",
                            model_name,
                            host,
                            waited_ms,
                            f"ram {host.ram_free_mb}MB too low; retry in {delay}s",
                        )
                    )
                    self._sleep(delay)
                    waited_ms += delay * 1000
                    attempt += 1
                    continue
                self._record(self._decision("proceed", model_name, host, waited_ms, "cpu-runtime"))
                return spec

        while True:
            host = telemetry.snapshot(s.ollama_url)
            self._check_hard_stops(host, model_name)

            # thermal pause (bounded by the same deadline)
            if host.gpu.available and host.gpu.temp_c >= rc.gpu_temp_pause_c:
                self._record(
                    self._decision(
                        "thermal_pause",
                        model_name,
                        host,
                        waited_ms,
                        f"gpu {host.gpu.temp_c}C >= {rc.gpu_temp_pause_c}C",
                    )
                )
                if time.monotonic() > deadline:
                    raise ResourceUnavailable(f"thermal pause exceeded timeout for {model_name}")
                # short bounded naps (<= 30s) so we re-check the temperature often rather than
                # committing to a fixed 120s sleep past the deadline or past a quick cool-down
                pause = max(1, min(30, int(deadline - time.monotonic())))
                self._sleep(pause)
                waited_ms += pause * 1000
                continue

            # polite mode: someone else is using the GPU during the day
            if (
                not interactive
                and rc.polite_mode.enabled_outside_night_window
                and not self.is_batch_window()
                and host.gpu.available
                and host.gpu.util_pct > rc.polite_mode.external_gpu_util_threshold
                and host.ollama_vram_mb == 0  # if only we are loaded, the util is probably ours
            ):
                self._record(
                    self._decision(
                        "deferred",
                        model_name,
                        host,
                        waited_ms,
                        f"polite: external gpu util {host.gpu.util_pct}%",
                    )
                )
                raise ResourceUnavailable(
                    "polite mode: GPU busy with external work; deferred to night window"
                )

            # transient RAM pressure (the user's own jobs by day): queue with backoff instead of failing
            if host.ram_total_mb and host.ram_free_mb < rc.min_free_ram_mb:
                if time.monotonic() > deadline:
                    self._record(
                        self._decision(
                            "deferred",
                            model_name,
                            host,
                            waited_ms,
                            f"ram {host.ram_free_mb}MB too low; timeout",
                        )
                    )
                    raise ResourceUnavailable(
                        f"RAM free {host.ram_free_mb} MB < {rc.min_free_ram_mb} MB after {waited_ms // 1000}s"
                    )
                delay = backoffs[min(attempt, len(backoffs) - 1)]
                self._record(
                    self._decision(
                        "queued",
                        model_name,
                        host,
                        waited_ms,
                        f"ram {host.ram_free_mb}MB too low; retry in {delay}s",
                    )
                )
                self._sleep(delay)
                waited_ms += delay * 1000
                attempt += 1
                continue

            # already loaded -> go
            if any(m.name == model_name for m in host.loaded_models):
                self._loaded_since.setdefault(model_name, time.monotonic())
                self._record(self._decision("proceed", model_name, host, waited_ms, "already loaded"))
                return spec

            if not host.gpu.available:
                self._record(
                    self._decision(
                        "proceed", model_name, host, waited_ms, "no gpu telemetry; trusting ollama"
                    )
                )
                return spec

            free = host.gpu.vram_free_mb
            reclaim = self._reclaimable_vram(host, keep=model_name)
            required = need + rc.vram_safety_margin_mb
            if free >= required:
                self._loaded_since[model_name] = time.monotonic()
                self._record(
                    self._decision("proceed", model_name, host, waited_ms, f"free {free}MB >= {required}MB")
                )
                return spec
            if free + reclaim >= required:
                self._unload_others(host, keep=model_name)
                # re-snapshot: an unload can fail silently (Ollama call error) or free less than its
                # reported size, so only proceed once VRAM is actually available.
                host = telemetry.snapshot(s.ollama_url)
                free = host.gpu.vram_free_mb
                if free >= required:
                    self._loaded_since[model_name] = time.monotonic()
                    self._record(
                        self._decision(
                            "swap", model_name, host, waited_ms, f"reclaim {reclaim}MB from other models"
                        )
                    )
                    return spec
                self._record(
                    self._decision(
                        "queued",
                        model_name,
                        host,
                        waited_ms,
                        f"unload freed less than expected; free {free}MB < {required}MB",
                    )
                )

            # queue with backoff
            if time.monotonic() > deadline:
                self._record(
                    self._decision(
                        "deferred",
                        model_name,
                        host,
                        waited_ms,
                        f"queue timeout; free {free}MB < {required}MB",
                    )
                )
                raise ResourceUnavailable(f"VRAM unavailable for {model_name} after {waited_ms // 1000}s")
            delay = backoffs[min(attempt, len(backoffs) - 1)]
            self._record(
                self._decision(
                    "queued", model_name, host, waited_ms, f"free {free}MB < {required}MB; retry in {delay}s"
                )
            )
            self._sleep(delay)
            waited_ms += delay * 1000
            attempt += 1

    # ------------------------------------------------------------------ internals
    def _check_hard_stops(self, host: telemetry.HostStatus, model_name: str) -> None:
        rc = settings().resources
        if host.disk_free_gb < rc.min_free_disk_gb:
            self._record(
                self._decision("deferred", model_name, host, 0, f"disk {host.disk_free_gb:.1f}GB too low")
            )
            raise ResourceUnavailable(f"disk free {host.disk_free_gb:.1f} GB < {rc.min_free_disk_gb} GB")
        if host.gpu.available and host.gpu.temp_c >= rc.gpu_temp_stop_c:
            self._record(self._decision("deferred", model_name, host, 0, f"gpu {host.gpu.temp_c}C stop"))
            raise ResourceUnavailable(f"GPU temperature {host.gpu.temp_c}C >= stop threshold")

    def _unload_others(self, host: telemetry.HostStatus, keep: str) -> None:
        """Unload only the models eligible per ``_eligible_for_unload`` — never a freshly loaded one
        that has not met the minimum-loaded-time policy."""
        from eoa.llm.ollama_client import unload_model

        for m in self._eligible_for_unload(host, keep):
            try:
                unload_model(m.name)
                self._loaded_since.pop(m.name, None)
            except Exception as exc:
                log.warning("unload_failed", model=m.name, error=str(exc))

    @staticmethod
    def _decision(
        decision: Decision, model: str, host: telemetry.HostStatus, wait_ms: int, reason: str
    ) -> GateDecision:
        return GateDecision(
            decision=decision,
            model=model,
            vram_free_mb=host.gpu.vram_free_mb,
            gpu_util=host.gpu.util_pct,
            gpu_temp=host.gpu.temp_c,
            ram_free_mb=host.ram_free_mb,
            wait_ms=wait_ms,
            reason=reason,
            at=datetime.now(tz=UTC),
        )

    def status(self) -> dict:
        """Snapshot for the UI status panel."""
        s = settings()
        host = telemetry.snapshot(s.ollama_url)
        return {
            "at": host.at.isoformat(),
            "gpu": {
                "available": host.gpu.available,
                "vram_total_mb": host.gpu.vram_total_mb,
                "vram_used_mb": host.gpu.vram_used_mb,
                "vram_free_mb": host.gpu.vram_free_mb,
                "util_pct": host.gpu.util_pct,
                "temp_c": host.gpu.temp_c,
            },
            "ram": {"free_mb": host.ram_free_mb, "total_mb": host.ram_total_mb},
            "disk_free_gb": round(host.disk_free_gb, 1),
            "loaded_models": [
                {
                    "name": m.name,
                    "size_mb": m.size_mb,
                    "size_vram_mb": m.size_vram_mb,
                    "cpu_offload": m.partially_on_cpu,
                }
                for m in host.loaded_models
            ],
            "batch_window": self.is_batch_window(),
            "recent_decisions": [
                {"at": d.at.isoformat(), "decision": d.decision, "model": d.model, "reason": d.reason}
                for d in list(self.history)[-20:]
            ],
        }


_gate: ResourceGate | None = None
_gate_lock = threading.Lock()


def gate() -> ResourceGate:
    """Process-wide gate singleton."""
    global _gate
    with _gate_lock:
        if _gate is None:
            _gate = ResourceGate()
        return _gate
