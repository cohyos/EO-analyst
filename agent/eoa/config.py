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
    num_predict: dict[str, int] = {}
    options: dict[str, Any] = {}


class SearxngCfg(BaseModel):
    url: str = "http://searxng:8080"
    engines: list[str] = []
    engines_by_lang: dict[str, list[str]] = {}
    rate_limit_per_minute: int = 20


class DdgsCfg(BaseModel):
    """Settings for the pure-Python `ddgs` metasearch backend (native-Windows default, no SearXNG container)."""

    backends: list[str] = ["duckduckgo", "bing", "google", "brave", "yahoo"]
    timeout_s: int = 15
    max_results: int = 10


class SearchCfg(BaseModel):
    """Which metasearch backend `eoa.search.provider.search()` dispatches to."""

    provider: str = "ddgs"  # "ddgs" (pure-Python, default) | "searxng" (legacy Docker container)
    ddgs: DdgsCfg = DdgsCfg()


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


class ObsidianExportCfg(BaseModel):
    enabled: bool = False
    vault_dir: str = "output/obsidian"
    entities: bool = True
    items: bool = True
    reports: bool = True
    min_level: str = "yellow"


class ExportCfg(BaseModel):
    obsidian: ObsidianExportCfg = ObsidianExportCfg()


class CliProviderCfg(BaseModel):
    """One cloud CLI provider (U8, docs/adr/005-cloud-llm-cli.md): ``binary`` is looked up via
    ``shutil.which`` (a bare name resolves through PATH; an absolute path works too), ``models``
    is the static list offered in the picker (the CLIs don't expose a reliable model-listing API).
    ``power_levels`` (U8-ג, Revision 2026-09-06) are the effort/reasoning levels the CLI accepts
    on this machine (verified live 2026-09-06 against each CLI's ``--help``): ``agy``/``claude``
    both take a bare ``--effort <level>`` (``claude`` additionally accepts ``xhigh``/``max``, not
    offered here to keep the three CLI providers' pickers uniform); ``codex`` has no dedicated
    flag but honors the same values via ``-c model_reasoning_effort=<level>``.
    """

    binary: str
    models: list[str] = Field(default_factory=list)
    power_levels: list[str] = Field(default_factory=lambda: ["low", "medium", "high"])


class ChainEntryCfg(BaseModel):
    """One link of a role's fallback chain (U8-ה, Revision 2026-09-06).

    ``provider`` is any provider id known to ``eoa.llm.chain`` -- a CLI kind
    (``agy``/``claude``/``codex``), a direct API kind (``anthropic``/``gemini``/``openai``), or
    ``ollama`` for the local terminal entry. ``power`` is a provider-specific effort/thinking
    level (e.g. ``"low"``/``"medium"``/``"high"``); ``None`` uses that provider's default.
    """

    provider: str
    model: str | None = None
    power: str | None = None


class ApiProviderCfg(BaseModel):
    """One direct-API cloud provider (U8-ו, Revision 2026-09-06). The API key itself is never
    configured here -- it is read straight from the environment (``ANTHROPIC_API_KEY`` /
    ``GEMINI_API_KEY`` / ``OPENAI_API_KEY``, populated from ``.env`` only) and never logged or
    surfaced to the UI beyond a "מוגדר / לא מוגדר" boolean.
    """

    models: list[str] = Field(default_factory=list)
    power_levels: list[str] = Field(default_factory=lambda: ["low", "medium", "high"])


class PricingEntryCfg(BaseModel):
    """USD per million tokens for one ``"<provider>:<model>"`` key (U8-ו/5). CLI/subscription
    providers (agy/claude/codex) are not priced here -- they cost $0 by design, even though their
    reported token usage is still counted in ``llm_calls`` when available."""

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0


class LlmProvidersCfg(BaseModel):
    """U8: LLM provider routing (docs/adr/005-cloud-llm-cli.md + Revision 2026-09-06).

    ``mode`` is the global local/cloud switch (U8-א): "local" (default) means every role resolves
    to the local Ollama model everywhere, including the night pipeline; "cloud" means each role in
    ``chains`` resolves to its configured fallback chain (``eoa.llm.chain.run_chain``), which is
    *always* terminated by a local Ollama entry (enforced by ``effective_chain`` below even if the
    user's own chain omits one). The interactive chat keeps its own per-question override
    (``interactive_default`` / the request's explicit ``provider``), which is independent of
    ``mode`` and always wins when set.
    """

    mode: str = "local"  # "local" | "cloud" -- global switch, applies pipeline-wide (U8-א)
    allow_cloud: bool = True
    interactive_default: str = "ollama"  # "ollama" | "agy[:<model>]" | "claude[:<model>]" | "codex[:<model>]"
    timeout_s: int = 120
    cli: dict[str, CliProviderCfg] = Field(
        default_factory=lambda: {
            "agy": CliProviderCfg(
                binary="agy",
                models=["gemini-3.8-flash-medium", "gemini-3.8-flash-high", "gemini-3.1-pro-high"],
            ),
            "claude": CliProviderCfg(
                binary="claude",
                models=["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
            ),
            "codex": CliProviderCfg(binary="codex", models=["default"]),
        }
    )
    # U8-ו: direct-API providers (key-based, no CLI). Model lists here are the "approximate,
    # edit me" defaults asked for in point 5 -- ``list_models()`` refreshes them live when the key
    # is present and the API exposes a listing endpoint (currently only Gemini's).
    api: dict[str, ApiProviderCfg] = Field(
        default_factory=lambda: {
            "anthropic": ApiProviderCfg(
                models=["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
                power_levels=["low", "medium", "high"],
            ),
            "gemini": ApiProviderCfg(
                models=["gemini-3.1-pro-preview", "gemini-3.5-flash", "gemini-3.1-flash-lite"],
                power_levels=["low", "medium", "high"],
            ),
            "openai": ApiProviderCfg(
                models=["gpt-5.1", "gpt-5.1-mini"],
                power_levels=["low", "medium", "high"],
            ),
        }
    )
    # U8-ה: per-role ("resident"/"investigator"/"light"/"report") ordered fallback chains, used
    # only when mode == "cloud". A role missing from this dict (or mode == "local") falls back to
    # a single-entry local chain -- see ``effective_chain``.
    chains: dict[str, list[ChainEntryCfg]] = Field(default_factory=dict)
    # U8-ו/5: "approximate, edit me" USD-per-million-token defaults for the API models above.
    # Keyed "<provider>:<model>"; CLI providers are intentionally absent (cost 0).
    pricing: dict[str, PricingEntryCfg] = Field(
        default_factory=lambda: {
            "anthropic:claude-opus-5": PricingEntryCfg(input_per_mtok=15.0, output_per_mtok=75.0),
            "anthropic:claude-sonnet-5": PricingEntryCfg(input_per_mtok=3.0, output_per_mtok=15.0),
            "anthropic:claude-haiku-4-5-20251001": PricingEntryCfg(input_per_mtok=0.8, output_per_mtok=4.0),
            "gemini:gemini-3.1-pro-preview": PricingEntryCfg(input_per_mtok=1.25, output_per_mtok=10.0),
            "gemini:gemini-3.5-flash": PricingEntryCfg(input_per_mtok=0.3, output_per_mtok=2.5),
            "gemini:gemini-3.1-flash-lite": PricingEntryCfg(input_per_mtok=0.1, output_per_mtok=0.4),
            "openai:gpt-5.1": PricingEntryCfg(input_per_mtok=5.0, output_per_mtok=15.0),
            "openai:gpt-5.1-mini": PricingEntryCfg(input_per_mtok=0.5, output_per_mtok=2.0),
        }
    )

    def effective_chain(self, role: str) -> list[ChainEntryCfg]:
        """The chain a role-based call (no explicit ``provider`` override) should try, in order.

        ``mode == "local"`` (default): always just ``[ollama]``, regardless of ``chains`` -- the
        global switch (U8-א) means a "cloud" chain configured for a role has zero effect until the
        user flips ``mode`` to "cloud". ``mode == "cloud"``: the role's configured chain, with a
        local Ollama entry enforced at the end even if the user's own list omits one (U8-א: "the
        chain always ends with the local Ollama model (enforced)").
        """
        if self.mode != "cloud":
            return [ChainEntryCfg(provider="ollama")]
        chain = list(self.chains.get(role) or [])
        if not chain:
            return [ChainEntryCfg(provider="ollama")]
        if chain[-1].provider != "ollama":
            chain.append(ChainEntryCfg(provider="ollama"))
        return chain


class McpServerCfg(BaseModel):
    """One MCP (Model Context Protocol) server (A8, docs/adr/006-mcp-sources.md).

    ``transport`` is ``"stdio"`` (``command``/``args``/``env`` spawn a local subprocess -- our own
    servers under ``eoa.mcp_servers.*``) or ``"http"`` (``url`` -- a streamable-HTTP server, e.g. a
    server already running elsewhere). ``env`` lists environment variable *names* only (values come
    from the process environment / ``.env``, never from this file -- ``docs/CONVENTIONS.md`` rule
    #12). ``allow_tools``/``deny_tools`` are tool-name allow/deny lists (empty ``allow_tools`` means
    "every tool this server advertises"); ``deny_tools`` always wins over ``allow_tools``.
    """

    id: str
    label: str = ""
    transport: str = "stdio"  # "stdio" | "http"
    enabled: bool = False
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: list[str] = Field(default_factory=list)
    allow_tools: list[str] = Field(default_factory=list)
    deny_tools: list[str] = Field(default_factory=list)
    timeout_s: int = 30
    max_output_chars: int = 8000
    # A9 (config/mcp.yaml comments): a server that only exists inside the user's own Claude
    # desktop/CLI session (no local process, no HTTP endpoint this project can reach directly) --
    # reachable only by the `claude` CLI provider inheriting its own configured MCP servers, never
    # by `eoa.mcp.registry` connecting to it itself.
    inherit_cli_only: bool = False


class ProcurementMcpCfg(BaseModel):
    """Defaults consumed by ``eoa.mcp_servers.procurement`` (not by the client/registry)."""

    psc_codes_eo_ir: list[str] = Field(
        default_factory=lambda: ["5855", "6650", "1270", "5840", "5841"]
    )


class McpCfg(BaseModel):
    """A8: MCP tool layer for the local ReAct deep-search loop (docs/adr/006-mcp-sources.md).

    ``enabled`` is the config-level kill switch checked before any MCP tool is added to the
    investigator's tool list or before any cloud CLI is handed ``--mcp-config`` -- disabled by
    default so existing behaviour is completely unchanged until explicitly turned on.

    ``inherit_cli_mcp`` (point 4, docs/adr/006-mcp-sources.md): per-CLI-kind opt-in for handing our
    stdio servers (and any ``inherit_cli_only`` server) to that CLI via its own MCP flag --
    verified live only for ``claude`` (``--mcp-config``) as of 2026-09-06; ``agy``/``codex`` default
    to ``False`` since neither has a documented equivalent flag.
    """

    enabled: bool = False
    servers: list[McpServerCfg] = Field(default_factory=list)
    inherit_cli_mcp: dict[str, bool] = Field(
        default_factory=lambda: {"claude": True, "agy": False, "codex": False}
    )
    procurement: ProcurementMcpCfg = ProcurementMcpCfg()

    def server(self, server_id: str) -> McpServerCfg | None:
        return next((s for s in self.servers if s.id == server_id), None)

    def enabled_servers(self) -> list[McpServerCfg]:
        return [s for s in self.servers if s.enabled and not s.inherit_cli_only]

    def stdio_servers_for_cli(self) -> list[McpServerCfg]:
        """Stdio servers to hand a supporting cloud CLI via its own MCP-config flag: our own
        enabled stdio servers, plus any ``inherit_cli_only`` server IS excluded here (that one has
        no local command for the CLI to spawn -- it depends on the CLI's own separately configured
        MCP servers, not ours)."""
        return [s for s in self.servers if s.enabled and s.transport == "stdio" and s.command]


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
    search: SearchCfg = SearchCfg()
    fetch: FetchCfg = FetchCfg()
    security: SecurityCfg = SecurityCfg()
    report: ReportCfg = ReportCfg()
    notify: NotifyCfg = NotifyCfg()
    retention: RetentionCfg = RetentionCfg()
    api: ApiCfg = ApiCfg()
    export: ExportCfg = ExportCfg()
    llm_providers: LlmProvidersCfg = LlmProvidersCfg()
    mcp: McpCfg = McpCfg()

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


def _load_yaml_optional(name: str) -> dict[str, Any]:
    """Like ``_load_yaml`` but tolerant of a missing file -- used for ``mcp.yaml`` (A8), which is
    additive and may not exist yet on an older checkout or a fresh test fixture directory."""
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    return _load_yaml(name)


@lru_cache(maxsize=1)
def settings() -> Settings:
    """Load and cache settings. Call `settings.cache_clear()` in tests to reload."""
    base = _load_yaml("config.yaml")
    registry = ModelsRegistry(**_load_yaml("models.yaml"))
    taxonomy = _load_yaml("taxonomy.yaml")
    watchlist = _load_yaml("watchlist.yaml")
    mcp_data = _load_yaml_optional("mcp.yaml")
    if "mcp" not in base and mcp_data:
        base = {**base, "mcp": mcp_data}
    return Settings(registry=registry, taxonomy=taxonomy, watchlist=watchlist, **base)
