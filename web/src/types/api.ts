// Types mirroring docs/API.md exactly. Keep field names identical to the
// backend contract (snake_case) so the API layer needs no translation.

// "unclassified" is a client-side-only pseudo-level for items whose
// `level` column is still null (not yet triaged by the pipeline) — the
// backend never accepts it as a filter value in `GET /api/items?level=`.
export type TriageLevel = "red" | "orange" | "yellow" | "archive" | "unclassified";

export type SecurityStatus = "clean" | "quarantined" | "flagged" | string;

export interface ItemCard {
  id: number;
  title: string;
  url: string;
  source_name: string;
  published_at: string;
  lang: string;
  domain: string;
  subdomain: string | null;
  report_kind: string;
  trl: string | null;
  geography: string | null;
  score: number;
  level: TriageLevel;
  triage_reason: string | null;
  summary_he: string | null;
  so_what_he: string | null;
  entities_mentioned: string[];
  tags: string[];
  security_status: SecurityStatus;
  dedup_of: number | null;
  key_facts: string[];
}

export interface ItemDetail extends ItemCard {
  clean_text: string;
  events: EventRow[];
  edges: EdgeRow[];
  investigations: InvestigationSummary[];
}

export interface EventRow {
  id: number;
  kind: string;
  summary_he: string;
  occurred_at: string | null;
  item_id: number;
}

export interface EdgeRow {
  src: number;
  dst: number;
  label: string;
  item_id: number;
  evidence: string | null;
}

export interface ItemsResponse {
  total: number;
  items: ItemCard[];
}

export interface EntitySummary {
  id: number;
  name: string;
  kind: string;
  country: string | null;
  aliases: string[];
  focus: string | null;
  item_count: number;
  last_seen: string | null;
}

export interface TimelineEntry {
  kind: "event" | "item";
  id: number;
  title: string;
  summary_he?: string | null;
  occurred_at: string | null;
  item_id: number | null;
}

export interface NeighborEdge {
  entity_id: number;
  entity_name: string;
  label: string;
  item_id: number;
}

export interface EntityDetail extends EntitySummary {
  timeline: TimelineEntry[];
  neighbors: NeighborEdge[];
}

export interface GraphNode {
  id: number;
  name: string;
  kind: string;
  country: string | null;
}

export interface GraphEdgeRow {
  src: number;
  dst: number;
  label: string;
  item_id: number;
  evidence: string | null;
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdgeRow[];
}

export type InvestigationState =
  | "queued"
  | "running"
  | "done"
  | "stopped"
  | "error"
  | "not_found";

export interface InvestigationSummary {
  job_id: string;
  item_id: number | null;
  question: string;
  state: InvestigationState;
  rounds: number;
  queries: number;
  pages_read: number;
  outcome: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface InvestigationLogLine {
  round: number;
  lang: string;
  query: string;
  results: number;
  outcome: string;
  at: string;
}

export interface InvestigationSource {
  n: number;
  item_id: number | null;
  title: string;
  url: string;
}

export interface InvestigationOut {
  answer_he: string;
  sources: InvestigationSource[];
  outcome: string;
}

export interface InvestigationDetail extends InvestigationSummary {
  log: InvestigationLogLine[];
  answer: InvestigationOut | null;
}

export interface AskCitation {
  n: number;
  item_id: number;
  title: string;
  url: string;
}

export type AskHistoryMessage = { role: "user" | "assistant"; content: string };

export interface AskRequest {
  question: string;
  context_item_ids: number[];
  context_entity_ids: number[];
  history: AskHistoryMessage[];
}

export type AskSseEvent =
  | { type: "token"; text: string }
  | { type: "citations"; items: AskCitation[] }
  | { type: "done" };

export interface ReportSummary {
  id: number;
  kind: string;
  period_start: string;
  period_end: string;
  path_docx: string | null;
  path_md: string | null;
  path_html: string | null;
  qa_passed: boolean;
  created_at: string;
  headline_count: number;
}

export interface ReportDetail extends ReportSummary {
  html: string;
  open_points: OpenPoint[];
  items_included: number[];
}

export interface OpenPoint {
  id: number;
  question: string;
  options: string[] | null;
  answer: string | null;
  assumed: boolean;
}

export interface Headline {
  item_id: number;
  title: string;
  level: TriageLevel;
  summary_he: string;
  url: string;
}

export interface NightSummary {
  items_ingested: number;
  classified: number;
  red: number;
  orange: number;
  deep_searches: number;
  duration_min: number;
  errors: number;
}

export interface MorningResponse {
  report: ReportDetail | null;
  headlines: Headline[];
  open_points: OpenPoint[];
  night_summary: NightSummary;
}

export interface Conference {
  id: number;
  name: string;
  location: string | null;
  starts_at: string;
  ends_at: string;
  url: string | null;
  relevance_he: string | null;
}

export interface Clarification {
  id: number;
  kind: string;
  question: string;
  options: string[] | null;
  answer: string | null;
  asked_at: string;
  timeout_at: string | null;
  assumed: boolean;
}

export interface SurveyQuestion {
  id: string;
  type: "choice" | "scale" | "text";
  text_he: string;
  options: string[] | null;
}

export interface Survey {
  id: number;
  report_id: number | null;
  questions: SurveyQuestion[];
  answers: Record<string, unknown> | null;
}

export interface Lesson {
  id: number;
  kind: string;
  text: string;
  active: boolean;
  created_at: string;
}

export type JobState =
  | "queued"
  | "running"
  | "done"
  | "error"
  | "cancelled";

export interface Job {
  id: string;
  scope: string;
  mode: "eco" | "full";
  state: JobState;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  progress?: string | null;
}

export interface ResourceGateGpu {
  available: boolean;
  vram_total_mb: number;
  vram_used_mb: number;
  vram_free_mb: number;
  util_pct: number;
  temp_c: number;
}

export interface ResourceGateRam {
  free_mb: number;
  total_mb: number;
}

export interface LoadedModel {
  name: string;
  size_mb: number;
  size_vram_mb: number;
  cpu_offload: boolean;
}

// Mirrors eoa.resources.gate.Decision (agent/eoa/resources/gate.py)
export type GateDecisionKind =
  | "proceed"
  | "queued"
  | "deferred"
  | "swap"
  | "throttled"
  | "thermal_pause";

export interface GateDecision {
  at: string;
  decision: GateDecisionKind;
  model: string;
  reason: string;
}

// Mirrors ResourceGate.status() (agent/eoa/resources/gate.py) exactly —
// nested gpu/ram, plural loaded_models, recent_decisions history.
export interface ResourceGateStatus {
  gpu: ResourceGateGpu;
  ram: ResourceGateRam;
  disk_free_gb: number;
  loaded_models: LoadedModel[];
  batch_window: boolean;
  recent_decisions: GateDecision[];
}

export interface PipelineStageInfo {
  events: number;
  last_event: string | null;
  last_at: string | null;
}

export interface PipelineLastRun {
  started_at: string | null;
  finished_at: string | null;
  state: JobState;
  stages: Record<string, PipelineStageInfo>;
}

export interface PipelineStatus {
  current_job: Job | null;
  queue_depth: number;
  stage: string | null;
  night_window: boolean;
  next_run_at: string | null;
  last_run: PipelineLastRun | null;
}

export interface StatusResponse {
  at: string;
  services: {
    postgres: boolean;
    ollama: boolean;
    searxng: boolean;
    ntfy: boolean;
  };
  gate: ResourceGateStatus;
  pipeline: PipelineStatus;
}

export type StatusWsMessage =
  | (StatusResponse & { type?: undefined })
  | { type: "log"; job_id?: string; level?: string; message: string; at: string };

export interface ApiErrorBody {
  error: { code: string; message_he: string; detail: unknown };
}

export const SETTINGS_NAMES = [
  "config",
  "sources",
  "watchlist",
  "taxonomy",
  "models",
] as const;
export type SettingsName = (typeof SETTINGS_NAMES)[number];

export interface SettingsGetResponse {
  yaml: string;
}

export interface SettingsPutResponse {
  ok: boolean;
  errors: string[];
}
