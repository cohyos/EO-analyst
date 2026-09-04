"""Host telemetry: GPU (nvidia-smi), RAM, disk, and Ollama's loaded models.

Everything here is read-only and cheap; the gate polls it before every inference call.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx
import structlog

log = structlog.get_logger(__name__)

NVIDIA_SMI = os.environ.get("EOA_NVIDIA_SMI", "nvidia-smi")


@dataclass
class GpuStatus:
    vram_total_mb: int
    vram_used_mb: int
    util_pct: int
    temp_c: int
    available: bool = True
    error: str | None = None

    @property
    def vram_free_mb(self) -> int:
        return max(self.vram_total_mb - self.vram_used_mb, 0)


@dataclass
class LoadedModel:
    name: str
    size_mb: int
    size_vram_mb: int
    expires_at: str | None = None

    @property
    def partially_on_cpu(self) -> bool:
        return self.size_vram_mb < self.size_mb


@dataclass
class HostStatus:
    at: datetime
    gpu: GpuStatus
    ram_free_mb: int
    ram_total_mb: int
    disk_free_gb: float
    loaded_models: list[LoadedModel] = field(default_factory=list)

    @property
    def ollama_vram_mb(self) -> int:
        return sum(m.size_vram_mb for m in self.loaded_models)

    @property
    def external_vram_mb(self) -> int:
        """VRAM used by anything that is not Ollama (the user's own work, the desktop)."""
        return max(self.gpu.vram_used_mb - self.ollama_vram_mb, 0)


def read_gpu() -> GpuStatus:
    """Query nvidia-smi once. Returns available=False if it fails (CPU-only fallback)."""
    try:
        out = (
            subprocess.run(
                [
                    NVIDIA_SMI,
                    "--query-gpu=memory.total,memory.used,utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=True,
            )
            .stdout.strip()
            .splitlines()[0]
        )
        total, used, util, temp = (int(float(x)) for x in out.split(","))
        return GpuStatus(total, used, util, temp)
    except Exception as exc:
        log.warning("nvidia_smi_failed", error=str(exc))
        return GpuStatus(0, 0, 0, 0, available=False, error=str(exc))


def read_ram() -> tuple[int, int]:
    """Return (free_mb, total_mb) using psutil if present, else OS-specific fallbacks."""
    try:
        import psutil  # type: ignore[import-not-found]

        vm = psutil.virtual_memory()
        return int(vm.available / 2**20), int(vm.total / 2**20)
    except ImportError:
        pass
    if os.name == "nt":
        try:
            out = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_OperatingSystem | Select-Object FreePhysicalMemory,TotalVisibleMemorySize | "
                    'ForEach-Object { "$($_.FreePhysicalMemory) $($_.TotalVisibleMemorySize)" })',
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout.split()
            return int(out[0]) // 1024, int(out[1]) // 1024
        except Exception:
            return 0, 0
    try:
        meminfo = Path("/proc/meminfo").read_text().splitlines()
        vals = {ln.split(":")[0]: int(ln.split()[1]) for ln in meminfo if ":" in ln}
        return vals.get("MemAvailable", 0) // 1024, vals.get("MemTotal", 0) // 1024
    except Exception:
        return 0, 0


def read_disk(path: str | Path = ".") -> float:
    """Free disk space in GB for the volume holding `path`."""
    usage = shutil.disk_usage(str(path))
    return usage.free / 2**30


def read_ollama_ps(ollama_url: str, timeout: float = 3.0) -> list[LoadedModel]:
    """Ask Ollama which models are loaded and how much VRAM each takes."""
    try:
        r = httpx.get(f"{ollama_url.rstrip('/')}/api/ps", timeout=timeout)
        r.raise_for_status()
        models = []
        for m in r.json().get("models", []):
            models.append(
                LoadedModel(
                    name=m.get("name", "?"),
                    size_mb=int(m.get("size", 0) / 2**20),
                    size_vram_mb=int(m.get("size_vram", 0) / 2**20),
                    expires_at=m.get("expires_at"),
                )
            )
        return models
    except Exception as exc:
        log.debug("ollama_ps_failed", error=str(exc))
        return []


def snapshot(ollama_url: str, disk_path: str | Path = ".") -> HostStatus:
    """One consistent read of everything the gate needs."""
    free, total = read_ram()
    return HostStatus(
        at=datetime.now(tz=UTC),
        gpu=read_gpu(),
        ram_free_mb=free,
        ram_total_mb=total,
        disk_free_gb=read_disk(disk_path),
        loaded_models=read_ollama_ps(ollama_url),
    )
