// Shared coercion helpers + safe defaults for API payloads.
//
// The real backend can legitimately return null/partial objects — an empty
// database before the first nightly run, a report that hasn't been built
// yet, a status push before the resource gate initializes, etc. This module
// is the single place that knows the "zero" shape of every such object so
// web/src/api/real.ts and web/src/hooks/useStatusSocket.ts (which talks to
// /ws/status directly, outside the ApiClient) can both normalize incoming
// data into the shapes web/src/types/api.ts promises the rest of the app.
import type {
  Corroboration,
  CorroborationSource,
  CorroborationSourceKind,
  CorroborationStatus,
  CurrentRun,
  GateDecision,
  InvestigationLogLine,
  LoadedModel,
  NightSummary,
  OtherRunningJob,
  PipelineLastRun,
  PipelineStageInfo,
  PipelineStatus,
  ReportCitation,
  ReportCitationsResponse,
  ResourceGateStatus,
  RunsCurrentResponse,
  RunStageEntry,
  SecurityReviewCard,
  StageStatus,
  StatusResponse,
} from "@/types/api";

export function arr<T>(value: T[] | null | undefined): T[] {
  return Array.isArray(value) ? value : [];
}

export function str(value: string | null | undefined, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

/**
 * Coerces an id that the backend may return as either a string or a number
 * into a string. `jobs.id` (and therefore every `job_id` field — investigation
 * summaries/detail, `POST /api/items/{id}/investigate`, `POST /api/run`) is a
 * plain Postgres integer on the wire (real API check, 2026-09-04: `GET
 * /api/investigations` returns `"job_id": 10`, not `"10"`) even though
 * `docs/API.md`/`types/api.ts` model it as `string`. Plain `str()` would
 * silently turn every one of those into `""` (its `typeof !== "string"`
 * fallback), which breaks `/investigations/${job_id}` links and React list
 * keys throughout the Investigations UI.
 */
export function idStr(value: string | number | null | undefined, fallback = ""): string {
  if (typeof value === "string") return value;
  if (typeof value === "number" && !Number.isNaN(value)) return String(value);
  return fallback;
}

export function num(value: number | null | undefined, fallback = 0): number {
  return typeof value === "number" && !Number.isNaN(value) ? value : fallback;
}

export function bool(value: boolean | null | undefined, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

export function obj<T extends object>(value: T | null | undefined, fallback: T): T {
  return value && typeof value === "object" ? value : fallback;
}

export const ZERO_NIGHT_SUMMARY: NightSummary = {
  items_ingested: 0,
  classified: 0,
  red: 0,
  orange: 0,
  deep_searches: 0,
  duration_min: null,
  errors: 0,
  state: "none",
  tenders_open: 0,
  tenders_unknown: 0,
  new_forecasts: 0,
};

export function normalizeNightSummary(
  raw: Partial<NightSummary> | null | undefined,
): NightSummary {
  return {
    items_ingested: num(raw?.items_ingested),
    classified: num(raw?.classified),
    red: num(raw?.red),
    orange: num(raw?.orange),
    deep_searches: num(raw?.deep_searches),
    duration_min: typeof raw?.duration_min === "number" ? raw.duration_min : null,
    errors: num(raw?.errors),
    state: raw?.state ?? "none",
    tenders_open: num(raw?.tenders_open),
    tenders_unknown: num(raw?.tenders_unknown),
    new_forecasts: num(raw?.new_forecasts),
  };
}

export const DEFAULT_GATE: ResourceGateStatus = {
  local_inference_paused: false,
  gpu: {
    available: false,
    vram_total_mb: 0,
    vram_used_mb: 0,
    vram_free_mb: 0,
    util_pct: 0,
    temp_c: 0,
  },
  ram: { free_mb: 0, total_mb: 0 },
  disk_free_gb: 0,
  loaded_models: [],
  batch_window: false,
  recent_decisions: [],
};

export const DEFAULT_PIPELINE: PipelineStatus = {
  current_job: null,
  queue_depth: 0,
  stage: null,
  night_window: false,
  next_run_at: null,
  last_run: null,
};

function normalizeLoadedModel(raw: Partial<LoadedModel> | null | undefined): LoadedModel {
  const r = raw ?? {};
  return {
    name: str(r.name),
    size_mb: num(r.size_mb),
    size_vram_mb: num(r.size_vram_mb),
    cpu_offload: bool(r.cpu_offload),
  };
}

function normalizeGateDecision(
  raw: Partial<GateDecision> | null | undefined,
): GateDecision {
  const r = raw ?? {};
  return {
    at: str(r.at),
    decision: (r.decision ?? "proceed") as GateDecision["decision"],
    model: str(r.model),
    reason: str(r.reason),
  };
}

export function normalizeGate(
  raw: Partial<ResourceGateStatus> | null | undefined,
): ResourceGateStatus {
  const r = raw ?? {};
  return {
    gpu: {
      available: bool(r.gpu?.available),
      vram_total_mb: num(r.gpu?.vram_total_mb),
      vram_used_mb: num(r.gpu?.vram_used_mb),
      vram_free_mb: num(r.gpu?.vram_free_mb),
      util_pct: num(r.gpu?.util_pct),
      temp_c: num(r.gpu?.temp_c),
    },
    ram: {
      free_mb: num(r.ram?.free_mb),
      total_mb: num(r.ram?.total_mb),
    },
    disk_free_gb: num(r.disk_free_gb),
    loaded_models: arr(r.loaded_models).map(normalizeLoadedModel),
    local_inference_paused: bool(r.local_inference_paused),
    batch_window: bool(r.batch_window),
    recent_decisions: arr(r.recent_decisions).map(normalizeGateDecision),
  };
}

const VALID_STAGE_STATUSES: readonly StageStatus[] = [
  "pending",
  "running",
  "done",
  "failed",
  "skipped",
  "partial",
  "deferred",
];

function normalizeStageStatus(value: unknown): StageStatus {
  return typeof value === "string" &&
    (VALID_STAGE_STATUSES as readonly string[]).includes(value)
    ? (value as StageStatus)
    : "pending";
}

function normalizeStageInfo(
  raw: Partial<PipelineStageInfo> | null | undefined,
): PipelineStageInfo {
  const r = raw ?? {};
  return {
    status: normalizeStageStatus(r.status),
    minutes: typeof r.minutes === "number" ? r.minutes : null,
    last_event: r.last_event ?? null,
    last_at: r.last_at ?? null,
  };
}

function normalizeLastRun(
  raw: Partial<PipelineLastRun> | null | undefined,
): PipelineLastRun | null {
  if (!raw) return null;
  const stages: Record<string, PipelineStageInfo> = {};
  for (const [k, v] of Object.entries(raw.stages ?? {})) {
    stages[k] = normalizeStageInfo(v as Partial<PipelineStageInfo>);
  }
  return {
    started_at: raw.started_at ?? null,
    finished_at: raw.finished_at ?? null,
    state: (raw.state ?? "done") as PipelineLastRun["state"],
    stages,
  };
}

export function normalizePipeline(
  raw: Partial<PipelineStatus> | null | undefined,
): PipelineStatus {
  const r = raw ?? {};
  return {
    current_job: r.current_job ?? null,
    queue_depth: num(r.queue_depth),
    stage: r.stage ?? null,
    night_window: bool(r.night_window),
    next_run_at: r.next_run_at ?? null,
    last_run: normalizeLastRun(r.last_run),
  };
}

function normalizeRunStageEntry(
  raw: Partial<RunStageEntry> | null | undefined,
): RunStageEntry {
  const r = raw ?? {};
  return {
    stage: str(r.stage),
    status: normalizeStageStatus(r.status),
    minutes: typeof r.minutes === "number" ? r.minutes : null,
  };
}

function normalizeCurrentRun(
  raw: Partial<CurrentRun> | null | undefined,
): CurrentRun | null {
  if (!raw) return null;
  return {
    job_id: num(raw.job_id),
    kind: str(raw.kind),
    state: (raw.state ?? "queued") as CurrentRun["state"],
    current_stage: raw.current_stage ?? null,
    stages: arr(raw.stages).map(normalizeRunStageEntry),
    started_at: raw.started_at ?? null,
    elapsed_min: typeof raw.elapsed_min === "number" ? raw.elapsed_min : null,
    eta_min: typeof raw.eta_min === "number" ? raw.eta_min : null,
  };
}

function normalizeOtherRunningJob(
  raw: Partial<OtherRunningJob> | null | undefined,
): OtherRunningJob {
  const r = raw ?? {};
  return { job_id: num(r.job_id), kind: str(r.kind), started_at: r.started_at ?? null };
}

/** Normalizes `GET /api/runs/current` (U4/F17) into a guaranteed shape. */
export function normalizeRunsCurrent(
  raw: Partial<RunsCurrentResponse> | null | undefined,
): RunsCurrentResponse {
  return {
    current: normalizeCurrentRun(raw?.current),
    other_running: arr(raw?.other_running).map(normalizeOtherRunningJob),
  };
}

function normalizeReportCitation(
  raw: Partial<ReportCitation> | null | undefined,
): ReportCitation {
  const r = raw ?? {};
  return {
    item_id: typeof r.item_id === "number" ? r.item_id : null,
    url: r.url ?? null,
    title: r.title ?? null,
  };
}

/** Normalizes `GET /api/reports/{id}/citations` (U3) into a guaranteed shape. */
export function normalizeReportCitations(
  raw: Partial<ReportCitationsResponse> | null | undefined,
): ReportCitationsResponse {
  const rawCitations = raw?.citations ?? {};
  const citations: Record<string, ReportCitation> = {};
  for (const [n, c] of Object.entries(rawCitations)) {
    citations[n] = normalizeReportCitation(c);
  }
  return { report_id: num(raw?.report_id), citations };
}

/** W10: normalizes one `GET /api/security-reviews` row into a guaranteed shape. */
export function normalizeSecurityReviewCard(
  raw: Partial<SecurityReviewCard> | null | undefined,
): SecurityReviewCard {
  return {
    job_id: num(raw?.job_id),
    item_id: raw?.item_id ?? null,
    question: raw?.question ?? null,
    item_title: raw?.item_title ?? null,
    reason_he: raw?.reason_he ?? null,
    snippet: raw?.snippet ?? null,
    started_at: raw?.started_at ?? null,
    finished_at: raw?.finished_at ?? null,
  };
}

/** Normalizes a `/api/status` or `/ws/status` push into a guaranteed shape. */
export function normalizeStatus(
  raw: Partial<StatusResponse> | null | undefined,
): StatusResponse {
  return {
    at: str(raw?.at),
    services: {
      postgres: bool(raw?.services?.postgres),
      ollama: bool(raw?.services?.ollama),
      searxng: bool(raw?.services?.searxng),
      ntfy: bool(raw?.services?.ntfy),
    },
    gate: normalizeGate(raw?.gate),
    pipeline: normalizePipeline(raw?.pipeline),
  };
}

/**
 * `investigation_log` rows on the wire — both in `GET /api/investigations/{job_id}`'s
 * `log` array (agent/eoa/api/services.py:702, a bare `SELECT *`) and every
 * `WS /ws/investigations/{job_id}` push (routes/investigations.py:52-53,
 * which sends the raw row dict) — use the real table's column names
 * (db/migrations/versions/0001_core.py:426-440): `results_n`, not `results`;
 * `created_at`, not `at`. Rendering the raw row directly (as both call sites
 * used to) silently dropped the results count and the timestamp on every
 * log line. This is the single place both call sites normalize into the
 * frontend's clean `InvestigationLogLine` view model.
 */
// CORR (cross-source corroboration, 2026-09-07): normalizes the additive `ItemCard.corroboration`
// / `AskCitation.corroboration` field. Missing/partial input (the field is entirely absent until
// the backend lands it, and individual sub-fields may be partial even after) always normalizes to
// a safe "unknown, nothing found" shape rather than throwing or rendering `undefined` -- see the
// field's doc comment in web/src/types/api.ts.
export const ZERO_CORROBORATION: Corroboration = {
  status: "unknown",
  count: 0,
  sources: [],
  checked_at: null,
};

const VALID_CORROBORATION_STATUSES: readonly CorroborationStatus[] = [
  "single_source",
  "corroborated",
  "official_primary",
  "unknown",
];

const VALID_CORROBORATION_SOURCE_KINDS: readonly CorroborationSourceKind[] = [
  "duplicate",
  "same_event",
  "official",
];

function normalizeCorroborationStatus(value: unknown): CorroborationStatus {
  return typeof value === "string" &&
    (VALID_CORROBORATION_STATUSES as readonly string[]).includes(value)
    ? (value as CorroborationStatus)
    : "unknown";
}

function normalizeCorroborationSourceKind(value: unknown): CorroborationSourceKind {
  return typeof value === "string" &&
    (VALID_CORROBORATION_SOURCE_KINDS as readonly string[]).includes(value)
    ? (value as CorroborationSourceKind)
    : "same_event";
}

function normalizeCorroborationSource(
  raw: Partial<CorroborationSource> | null | undefined,
): CorroborationSource {
  const r = raw ?? {};
  return {
    item_id: num(r.item_id),
    source_name: str(r.source_name),
    url: str(r.url),
    published_at: r.published_at ?? null,
    kind: normalizeCorroborationSourceKind(r.kind),
  };
}

export function normalizeCorroboration(
  raw: Partial<Corroboration> | null | undefined,
): Corroboration {
  if (!raw) return ZERO_CORROBORATION;
  return {
    status: normalizeCorroborationStatus(raw.status),
    count: num(raw.count),
    sources: arr(raw.sources).map(normalizeCorroborationSource),
    checked_at: raw.checked_at ?? null,
  };
}

export function normalizeInvestigationLogLine(
  raw:
    | (Partial<InvestigationLogLine> & { results_n?: number; created_at?: string })
    | null
    | undefined,
): InvestigationLogLine {
  const r = raw ?? {};
  return {
    round: num(r.round),
    lang: str(r.lang),
    query: str(r.query),
    results: num(r.results ?? r.results_n),
    outcome: str(r.outcome),
    at: str(r.at ?? r.created_at),
  };
}
