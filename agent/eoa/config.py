"""Typed settings loaded from config/*.yaml plus a few environment overrides.

Usage::

    from eoa.config import settings
    s = settings()
    s.schedule.night_window.start   # "01:00"
    s.model("resident")             # ModelSpec for the resident role
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from eoa.errors import ConfigError, ModelNotAllowed

REPO_ROOT = Path(os.environ.get("EOA_ROOT", Path(__file__).resolve().parents[2]))
CONFIG_DIR = Path(os.environ.get("EOA_CONFIG_DIR", REPO_ROOT / "config"))


class TimeWindow(BaseModel):
    start: str
    end: str


class ScheduleCfg(BaseModel):
    night_window: TimeWindow
    pre_flight_at: str = "23:30"
    daytime_rss_poll_minutes: int = 120
    weekly_run: dict[str, str] = Field(default_factory=lambda: {"weekday": "sat", "start": "01:00"})
    monthly_run: dict[str, int] = Field(default_factory=lambda: {"day": 1})
    deadline_grace_minutes: int = 5


class DeepSearchCfg(BaseModel):
    trigger_levels: list[str] = ["red"]
    max_per_night: int = 4
    max_queries: int = 15
    max_pages: int = 30
    per_investigation_timeout_min: int = 25
    langs_primary: list[str] = ["he", "en"]
    langs_secondary: list[str] = ["ru", "zh", "fr", "de"]
    confidence_stop: float = 0.8


class TriageCfg(BaseModel):
    levels: dict[str, int] = {"red": 8, "orange": 6, "yellow": 4}
    daily_report_max_items: int = 10


class DedupCfg(BaseModel):
    cosine_threshold: float = 0.92
    lookback_days: int = 7


class PoliteModeCfg(BaseModel):
    external_gpu_util_threshold: int = 25
    enabled_outside_night_window: bool = True


class ResourcesCfg(BaseModel):
    vram_total_mb: int = 12227
    vram_safety_margin_mb: int = 1200
    min_free_vram_mb: dict[str, int] = {"resident": 9500, "light": 6000, "embed": 1500}
    min_free_ram_mb: int = 8000
    min_free_disk_gb: int = 20
    warn_free_disk_gb: int = 40
    gpu_temp_pause_c: int = 83
    gpu_temp_stop_c: int = 88
    queue_backoff_seconds: list[int] = [5, 10, 30, 60]
    queue_timeout_min: int = 20
    min_loaded_seconds: int = 300
    polite_mode: PoliteModeCfg = PoliteModeCfg()


class OllamaCfg(BaseModel):
    url: str = "http://127.0.0.1:11434"
    keep_alive: str = "30m"
    num_ctx: dict[str, int] = {}
    options: dict[str, Any] = {}


class SearxngCfg(BaseModel):
    url: str = "http://searxng:8080"
    engines: list[str] = []
    rate_limit_per_minute: int = 20


class FetchCfg(BaseModel):
    timeout_seconds: int = 20
    max_bytes: int = 2_000_000
    user_agent: str = "eo-analyst/0.1"
    respect_robots: bool = True


class SecurityCfg(BaseModel):
    quarantine_on_flag: bool = True
    blocklist_after_incidents: int = 2
    hidden_text_min_ratio: float = 0.15
    max_base64_blob_chars: int = 200


class ReportCfg(BaseModel):
    language: str = "he"
    formats: list[str] = ["docx", "md", "html"]
    output_dir: str = "output/reports"
    template: str | None = None
    citation_style: str = "numbered"
    require_citations: bool = True


class NotifyCfg(BaseModel):
    url: str = "http://ntfy:80"
    topic: str = "eo-analyst"
    public_fallback_url: str | None = None
    public_fallback_topic: str | None = None
    send_report_link: bool = True
    send_red_alerts: bool = True
    send_security_alerts: bool = True
    clarification_timeout_min: int = 5
    mirror_to_public: bool = False


class RetentionCfg(BaseModel):
    raw_text_days: int = 90
    logs_days: int = 30
    backups_keep: int = 30


class ApiCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    status_push_seconds: int = 2


class ModelSpec(BaseModel):
    key: str
    ollama: str | None = None
    hf: str | None = None
    vendor: str
    origin: str
    license: str
    role_hint: str = ""
    est_vram_mb: int = 0
    ctx_max: int = 8192
    dim: int | None = None
    runtime: str = "ollama"
    capabilities: list[str] = []


class ModelsRegistry(BaseModel):
    allowed_origins: list[str]
    allowed_formats: list[str]
    models: dict[str, ModelSpec]

    @field_validator("models", mode="before")
    @classmethod
    def _inject_keys(cls, v: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {k: {**spec, "key": k} for k, spec in v.items()}


class Settings(BaseModel):
    timezone: str = "Asia/Jerusalem"
    schedule: ScheduleCfg
    stages: dict[str, int] = {}
    deep_search: DeepSearchCfg = DeepSearchCfg()
    triage: TriageCfg = TriageCfg()
    dedup: DedupCfg = DedupCfg()
    resources: ResourcesCfg = ResourcesCfg()
    ollama: OllamaCfg = OllamaCfg()
    models: dict[str, str | None] = {}
    searxng: SearxngCfg = SearxngCfg()
    fetch: FetchCfg = FetchCfg()
    security: SecurityCfg = SecurityCfg()
    report: ReportCfg = ReportCfg()
    notify: NotifyCfg = NotifyCfg()
    retention: RetentionCfg = RetentionCfg()
    api: ApiCfg = ApiCfg()

    registry: ModelsRegistry
    taxonomy: dict[str, Any] = {}
    watchlist: dict[str, Any] = {}

    # ---- derived / env-driven -------------------------------------------------
    @property
    def database_url(self) -> str:
        return os.environ.get(
            "DATABASE_URL", "postgresql://eoa:change-me-local-only@127.0.0.1:5433/eoanalyst"
        )

    @property
    def ollama_url(self) -> str:
        return os.environ.get("OLLAMA_URL", self.ollama.url)

    @property
    def searxng_url(self) -> str:
        return os.environ.get("SEARXNG_URL", self.searxng.url)

    @property
    def embed_dim(self) -> int:
        spec = self.model("embed")
        if spec.dim is None:
            raise ConfigError(f"embedding model {spec.key} has no dim")
        return spec.dim

    def model(self, role: str) -> ModelSpec:
        """Resolve a role (resident/light/embed/...) to its ModelSpec, enforcing the allow-list."""
        key = self.models.get(role)
        if not key:
            raise ConfigError(f"no model configured for role '{role}'")
        spec = self.registry.models.get(key)
        if spec is None:
            raise ModelNotAllowed(f"model key '{key}' is not in models.yaml")
        if spec.origin not in self.registry.allowed_origins:
            raise ModelNotAllowed(f"model '{key}' origin {spec.origin} not allowed")
        return spec

    def has_model(self, role: str) -> bool:
        return bool(self.models.get(role))


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a mapping")
    return data


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Load and cache settings. Call `settings.cache_clear()` in tests to reload."""
    base = _load_yaml("config.yaml")
    registry = ModelsRegistry(**_load_yaml("models.yaml"))
    taxonomy = _load_yaml("taxonomy.yaml")
    watchlist = _load_yaml("watchlist.yaml")
    return Settings(registry=registry, taxonomy=taxonomy, watchlist=watchlist, **base)
