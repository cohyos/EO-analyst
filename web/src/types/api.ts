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
  // Present on `_item_card` (agent/eoa/api/services.py:197) — the "explain
  // the gaps" complement to `key_facts`, missing from the original
  // docs/API.md ItemCard field list.
  uncertainty_he: string | null;
  // A12 (מעקב טכנולוגי, 2026-09-06): additive, only ever non-null for domain === "tech_dev".
  tech_maturity: TechMaturity | null;
  tech_actor_kind: TechActorKind | null;
  tech_readiness_note_he: string | null;
  // A13 (מיקוד תעשייה ישראלית, docs/PLAN_WINDOWS_NATIVE.md): additive deterministic scoring —
  // relevance to the Israeli defense industry (0..1) and the reason codes behind it. `null` when
  // the pipeline hasn't scored the item. `GET /api/items?israel=true` filters to >= 0.5.
  israel_relevance: number | null;
  israel_reasons: string[];
}

// A12 (מעקב טכנולוגי): "רדאר טכנולוגי" -- GET /api/tech/radar, GET /api/tech/items.
export type TechMaturity = "lab" | "prototype" | "qualified" | "fielded";
export type TechActorKind = "academia" | "lab" | "startup" | "prime" | "government";

export interface TechRadarSubdomain {
  subdomain: string;
  label_he: string;
  counts: Partial<Record<TechMaturity | "unknown", number>>;
  total: number;
  sparkline: number[];
}

export interface TechRadarResponse {
  weeks: number;
  maturities: TechMaturity[];
  subdomains: TechRadarSubdomain[];
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
  // U7a: present only when the request set `group_by=country` — per-country
  // counts (+ level breakdown) for the *current* filters, additive so
  // existing callers reading only total/items are unaffected.
  groups?: CountryGroup[];
}

export interface CountryGroup {
  country: string;
  total: number;
  red: number;
  orange: number;
  yellow: number;
  archive: number;
}

export interface ItemsByCountryResponse {
  countries: CountryGroup[];
}

export interface EntitySummary {
  id: number;
  name: string;
  kind: string;
  country: string | null;
  aliases: string[];
  // Real API check, 2026-09-04: `GET /api/entities` returns `focus` as an
  // array of domain ids (e.g. `["air_defense","c_uas"]`), not a string —
  // rendering it directly used to concatenate the ids with no separator.
  focus: string[];
  item_count: number;
  last_seen: string | null;
  // U10/F15 (2026-09-05): relevance score 0..1 (eoa.pipeline.entity_relevance) and
  // whether the entity matches a config/watchlist.yaml company/program. The list
  // defaults to relevance >= 0.4; `is_watchlist` drives the "רשימת מעקב" facet/badge.
  relevance: number;
  is_watchlist: boolean;
  mentions_7d: number;
  mentions_30d: number;
  // A13 (מיקוד תעשייה ישראלית): whether this entity is classified as Israeli.
  // `GET /api/entities?israel=true` filters to `is_israeli = true`.
  is_israeli: boolean;
}

// U10 (2026-09-05): the entity timeline is items-only now (title links to
// /items/:id, source domain, date, level badge) — business events (kind/date/
// amount/counterpart) are a separate list, see BusinessEvent below. This
// replaces the old combined event+item `TimelineEntry` shape, which relied on
// a frontend-only `occurred_at` field the backend never actually populated
// (one of U10's "the links don't work" causes).
export interface EntityTimelineItem {
  item_id: number;
  title: string;
  url: string;
  source_name: string | null;
  published_at: string | null;
  level: TriageLevel;
}

export interface BusinessEvent {
  id: number;
  item_id: number | null;
  kind: string;
  date: string | null;
  amount_usd: number | null;
  currency: string | null;
  counterpart: string | null;
  summary_he: string | null;
}

export interface EntityKpis {
  mentions_7d: number;
  mentions_30d: number;
  events_count: number;
  related_items_by_level: Record<string, number>;
}

export interface EdgeGroupCounterpart {
  entity_id: number;
  entity_name: string;
}

export interface EdgeGroup {
  label: string;
  counterparts: EdgeGroupCounterpart[];
}

export interface NeighborEdge {
  entity_id: number;
  entity_name: string;
  label: string;
  item_id: number;
}

export interface EntityDetail extends EntitySummary {
  timeline: EntityTimelineItem[];
  business_events: BusinessEvent[];
  kpis: EntityKpis;
  edge_groups: EdgeGroup[];
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
  "queued" | "running" | "done" | "failed" | "stopped" | "error" | "not_found";

// U11/F17/F18 (docs/REVIEW_2026-09-05.md): the granular reason an investigation ended, distinct
// from the raw job `state` -- lets the UI show "נעצר בגלל תקציב" vs "לא נמצא" vs "נמצא" instead of
// one opaque outcome string.
export type InvestigationOutcomeReason =
  | "found"
  | "partial"
  | "not_found"
  // 2026-09-06 (job 86 regression fix): the finish-time answer was judged not to address the
  // question at all (see web/src/lib/investigations.ts for the label/tone and the backend fix in
  // eoa.search.deep_search).
  | "off_topic"
  | "stopped_budget"
  | "stopped_timeout"
  | "insufficient_context";

export interface InvestigationSummary {
  job_id: string;
  item_id: number | null;
  question: string;
  item_title: string | null;
  error: string | null;
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
  key_facts?: string[];
  what_was_tried_he?: string;
  contradictions_he?: string;
  // U11/F17/F18: budget/outcome accounting merged into the job result so the UI can show *why*
  // an investigation ended (docs/REVIEW_2026-09-05.md) instead of a single opaque outcome string.
  queries_used?: number;
  max_queries?: number;
  pages_read?: number;
  max_pages?: number;
  rounds?: number;
  stopped_reason?: InvestigationOutcomeReason | string;
  // W10 (docs/REVIEW_2026-09-06_evening.md round 4): set by the L2 prompt-injection guard when it
  // has to partially block an answer. Field names as documented by the deep-search engineer
  // working the same round -- not yet landed on the live backend, so every consumer of these must
  // treat their absence as the normal case, not an error (`GET /api/security-reviews` -- see
  // `agent/eoa/api/routes/security_review.py` -- likewise returns `[]` until they exist).
  security_review?: boolean;
  security_review_reason_he?: string | null;
  security_review_snippet?: string | null;
  /** Set by `POST /api/security-reviews/{job_id}/approve|dismiss` once handled -- the banner
   * hides once this is true, without needing a fresh page load. */
  security_review_resolved?: boolean;
}

export interface InvestigationDetail extends InvestigationSummary {
  log: InvestigationLogLine[];
  answer: InvestigationOut | null;
}

/** W10: one row of `GET /api/security-reviews` (agent/eoa/api/routes/security_review.py) -- a
 * `deep_search` job flagged for review and not yet approved/dismissed. */
export interface SecurityReviewCard {
  job_id: number;
  item_id: number | null;
  question: string | null;
  item_title: string | null;
  reason_he: string | null;
  snippet: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface AskCitation {
  n: number;
  item_id: number;
  title: string;
  url: string;
  // U11 (2026-09-06 answer-format rewrite): present once the final `sources` SSE event has
  // arrived -- `level`/`source_name` come from the same `items` row the citation was built from
  // (used for the "מקורות (n)" footer's badge + domain), `note` is the model's optional
  // per-source relevance line (`source_notes` in the prompt contract) which belongs in that
  // footer, never inline in the answer body. All three are absent on the earlier `citations`
  // event, which only carries enough to resolve `[n]` -> item/url for the inline chips.
  level?: TriageLevel | null;
  source_name?: string | null;
  note?: string | null;
}

export type AskHistoryMessage = { role: "user" | "assistant"; content: string };

export interface AskRequest {
  question: string;
  context_item_ids: number[];
  context_entity_ids: number[];
  history: AskHistoryMessage[];
  /** U8: "ollama" | "agy[:<model>]" | "claude[:<model>]" | "codex[:<model>]"; omit for the server default. */
  provider?: string | null;
}

export type AskSseEvent =
  | { type: "token"; text: string }
  | { type: "citations"; items: AskCitation[] }
  | { type: "meta"; provider: string; model: string }
  // U11: sent once, after the answer finishes streaming -- citations enriched with
  // level/source_name/note for the sources footer (see AskCitation above).
  | { type: "sources"; items: AskCitation[] }
  // Round 2 (docs/qa/loop/round_2_chat_fixes.md, D5 P2): sent 0-3 times, after streaming ends
  // and before `sources`, when the server wholesale-replaces the streamed answer -- a citation
  // repair pass that attached [n] markers, an "⚠ ללא ציטוטים" prefix when repair still couldn't,
  // or an "⚠ ייתכן שהתשובה אינה עוסקת בשאלה" prefix from the topic-anchor guard. The UI must
  // replace the message's whole `content` with `text`, not append it.
  // Round 3 (docs/qa/loop/round_3_chat_fixes.md, D5 grounding guard): an additive, optional
  // `ungrounded_removed` count is present when this event comes from the grounded-entity /
  // cross-source-conflation guard (eoa.api.ask_grounding) stripping a fabricated sentence --
  // not required reading for the UI today, kept for future surfacing/telemetry.
  | { type: "answer_final"; text: string; ungrounded_removed?: number }
  | { type: "done" };

// U8 (docs/adr/005-cloud-llm-cli.md + "Revision 2026-09-06"): local Ollama vs. cloud CLI
// (agy/claude/codex) vs. direct-API (anthropic/gemini/openai) provider routing.
export interface LlmProviderInfo {
  id: string;
  label: string;
  kind: "local" | "cloud" | "api";
  available: boolean;
  models: string[];
  /** api providers only: the .env variable name whose presence controls `available` — the key
   * value itself is never sent to the UI. */
  key_env?: string;
  /** api providers only: effort/thinking levels this provider's chat() call accepts. */
  power_levels?: string[];
}

/** One link of a role's fallback chain (U8-ה), as returned by GET /api/llm/providers. */
export interface LlmChainEntry {
  provider: string;
  model?: string | null;
  power?: string | null;
}

export interface LlmProvidersResponse {
  /** U8-א: the global local/cloud switch — applies to chat AND the night pipeline/queued jobs. */
  mode: "local" | "cloud";
  allow_cloud: boolean;
  interactive_default: string;
  /** U8-ה: per-role ("resident"/"investigator"/"light"/"report") configured fallback chains,
   * used only when `mode === "cloud"`. Empty when no chains are configured yet. */
  chains: Record<string, LlmChainEntry[]>;
  providers: LlmProviderInfo[];
}

export interface LlmSettingsPutResponse {
  ok: boolean;
  errors: string[];
  revision: string | null;
}

/** U8-4: GET /api/llm/calls?since=24h — per-provider fallback-chain call accounting. */
export interface LlmCallsProviderSummary {
  provider: string;
  calls: number;
  failures: number;
  fallbacks: number;
  prompt_tokens: number;
  completion_tokens: number;
  est_cost_usd: number;
}

export interface LlmCallsSummary {
  since_hours: number;
  providers: LlmCallsProviderSummary[];
  totals: {
    calls: number;
    failures: number;
    fallbacks: number;
    prompt_tokens: number;
    completion_tokens: number;
    est_cost_usd: number;
    cloud_calls: number;
  };
}

// A8 (docs/adr/006-mcp-sources.md): MCP (Model Context Protocol) tool sources for the
// interactive analyst — read-only, allow-listed, DATA-framed exactly like a fetched web page.
export interface McpServerInfo {
  id: string;
  label: string;
  transport: "stdio" | "http";
  enabled: boolean;
  inherit_cli_only: boolean;
  /** null when the server needs no key at all. */
  key_configured: boolean | null;
  key_env: string[] | null;
  tool_count: number | null;
  ok: boolean | null;
  error: string | null;
  latency_ms: number | null;
  tools: string[];
}

export interface McpServersResponse {
  mcp_enabled: boolean;
  servers: McpServerInfo[];
}

export interface McpPingResponse {
  id: string;
  ok: boolean;
  error: string | null;
  tool_count: number;
  tools: string[];
  latency_ms: number;
}

export interface McpCallSummary {
  server: string;
  tool: string;
  calls: number;
  failures: number;
  flagged: number;
  avg_duration_ms: number;
  total_chars: number;
}

export interface McpCallsResponse {
  since_hours: number;
  calls: McpCallSummary[];
  totals: { calls: number; failures: number; flagged: number };
}

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
  /** A11: only populated for kind === "bd_territory" (ISO-2/region code) -- null otherwise. */
  territory: string | null;
}

// A11 "דוח מיקוד לפיתוח עסקי, מכירה ושיווק לפי טריטוריה" (eoa.report.bd_territory).
// Mirrors `eoa.api.services.bd_territories` (agent/eoa/api/services.py).
export interface BdTerritoryOption {
  territory: string;
  items: number;
  tenders: number;
  forecasts: number;
  configured: boolean;
}

// Mirrors `eoa.api.services.build_or_enqueue_bd_report`'s three possible shapes.
export interface BdReportCreateResponse {
  job_id: string | number;
  status?: "queued" | "failed";
  error?: string | null;
  report?: ReportDetail;
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

// F12 (docs/REVIEW_2026-09-05.md): every count here is a rolling last-24h aggregate straight from
// the DB (agent/eoa/api/services.py `_night_summary`) -- `duration_min`/`state` are the one
// exception, describing the last *completed* nightly run specifically (there's no 24h-window
// meaning for "how long did the run take").
export interface NightSummary {
  items_ingested: number;
  classified: number;
  red: number;
  orange: number;
  deep_searches: number;
  duration_min: number | null;
  errors: number;
  state: JobState | "none";
  tenders_open: number;
  tenders_unknown: number;
  new_forecasts: number;
}

// U2: backs the Morning "שגיאות אחרונות" drawer (agent/eoa/api/services.py `recent_errors`).
export interface RecentErrorLogEntry {
  id: number;
  job_id: number | null;
  stage: string | null;
  message: string;
  at: string | null;
}

export interface MorningResponse {
  report: ReportDetail | null;
  headlines: Headline[];
  open_points: OpenPoint[];
  night_summary: NightSummary;
  recent_errors: RecentErrorLogEntry[];
}

// Mirrors eoa.conferences.tracker.conference_card (agent/eoa/conferences/tracker.py):
// the "legacy" fields (location/starts_at/ends_at/url/relevance_he) plus the
// full FR-12 field set additively — both are always present on a real response.
export interface ConferenceFieldChange {
  from: unknown;
  to: unknown;
}

export interface Conference {
  id: number;
  name: string;
  location: string | null;
  starts_at: string;
  ends_at: string;
  url: string | null;
  relevance_he: string | null;
  organizer: string | null;
  start_date: string | null;
  end_date: string | null;
  city: string | null;
  venue: string | null;
  cadence: string | null;
  relevance: number | null;
  rationale: string | null;
  registration_opens: string | null;
  early_bird_deadline: string | null;
  cfp_deadline: string | null;
  cost_range: string | null;
  registration_url: string | null;
  entry_conditions: string | null;
  status: string | null;
  last_verified_at: string | null;
  changes: Record<string, ConferenceFieldChange>;
}

// Mirrors the `tenders` table CHECK constraint (db/migrations/versions/0004_tenders.py,
// 0012_tenders_archived_status.py). "archived" (F24, 2026-09-06): a 'closed' tender more than 30
// days past its deadline -- never deleted, just hidden from the board's default view.
export type TenderStatus = "open" | "closed" | "awarded" | "unknown" | "archived";

// Mirrors `_tender_card` (agent/eoa/api/services.py) / docs/API.md section 5.2.
export interface TenderCard {
  id: number;
  source: string | null;
  external_ref: string | null;
  title: string | null;
  agency: string | null;
  country: string | null;
  published_at: string | null;
  deadline: string | null;
  url: string | null;
  cpv_naics: string[];
  summary_he: string | null;
  relevance: number | null;
  matched_terms: string[];
  entities: string[];
  status: TenderStatus;
  item_id: number | null;
  created_at: string;
  updated_at: string;
}

// F24 (2026-09-06): `GET /api/tenders` response shape -- the filtered/capped tender list PLUS a
// status -> count summary that always reflects the true totals (honoring country/q, but not the
// status/since_days/include_* narrowing) for the board's header chips. Mirrors
// `services.list_tenders` (agent/eoa/api/services.py).
export interface TendersResponse {
  tenders: TenderCard[];
  counts: Partial<Record<TenderStatus, number>>;
}

// Mirrors `_forecast_card` (agent/eoa/api/services.py) / docs/API.md section 5.2.
// `sources` is a plain TEXT[] of URLs (db/migrations/versions/0004_tenders.py) —
// not a structured citation object like AskCitation/InvestigationSource.
export interface ForecastCard {
  id: number;
  platform: string;
  buyer_country: string | null;
  trigger_event_id: number | null;
  trigger_item_id: number | null;
  payload_need: string;
  candidate_vendors: string[];
  likelihood: number | null;
  window_from: string | null;
  window_to: string | null;
  rationale_he: string | null;
  sources: string[];
  created_at: string;
  updated_at: string;
}

// A15 (docs/TENDER_PORTALS.md): per-source status returned by the coverage panel's backing
// endpoint (`GET /api/tenders/coverage`). Mirrors `_tender_source_status` (agent/eoa/api/services.py).
export type TenderSourceStatus =
  "integrated_keyless" | "waiting_for_key" | "not_integrated";

// Mirrors one entry of `tender_source_coverage`'s per-region `sources` list.
export interface TenderSourceCoverageItem {
  id: string;
  name: string;
  kind: "api_json" | "rss" | "html" | "search";
  country: string;
  status: TenderSourceStatus;
  verified: boolean;
  needs_key_env_var: string | null;
  notices_stored: number;
  last_fetch_at: string | null;
}

export interface TenderSourceCoverageRegion {
  region: string;
  sources: TenderSourceCoverageItem[];
}

// Mirrors `services.tender_source_coverage()` / `GET /api/tenders/coverage`.
export interface TenderSourceCoverageResponse {
  regions: TenderSourceCoverageRegion[];
  totals: Partial<Record<TenderSourceStatus | "search_only", number>>;
  source_count: number;
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

// Mirrors the `jobs` table CHECK constraint (db/migrations/versions/0001_core.py:270)
// exactly. Real API check, 2026-09-04: `GET /api/jobs` never returns "error"
// or "cancelled" — cancelling a queued job sets state="failed" with
// error="cancelled_by_user" (agent/eoa/api/services.py:cancel_job).
export type JobState = "queued" | "running" | "done" | "failed" | "deferred" | "partial";

// Mirrors the real `jobs` row shape returned by `GET /api/jobs` /
// `GET /api/jobs/{id}/cancel` exactly (real API check, 2026-09-04) — the
// row has no `scope`/`mode`/`progress` columns; `mode` (for daily_run jobs)
// lives inside `payload`, and `scope` doesn't exist at all (only `kind`,
// e.g. "daily_run", set from the scope by `enqueue_run`).
export interface Job {
  id: number;
  kind: string;
  payload: Record<string, unknown> | null;
  state: JobState;
  priority: number;
  attempts: number;
  not_before: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  result: unknown;
  created_at: string;
  updated_at: string;
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
  "proceed" | "queued" | "deferred" | "swap" | "throttled" | "thermal_pause";

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

// F12: a stage's `status` is derived from its own terminal `run_log` event (not a raw heartbeat
// row count, which is why the old timeline showed "2" for nearly every stage regardless of what
// it actually did) -- "pending" means the stage was never reached this run.
export type StageStatus = "pending" | "running" | "done" | "failed" | "skipped";

export interface PipelineStageInfo {
  status: StageStatus;
  minutes: number | null;
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

// U4/F17: `GET /api/runs/current` -- what "הרץ עכשיו" kicked off (if anything), with per-stage
// progress/ETA, plus any other job a separate worker has claimed concurrently (F17: a
// `deep_search` job ran to completion without the analyst ever seeing it in the UI).
export interface RunStageEntry {
  stage: string;
  status: StageStatus;
  minutes: number | null;
}

export interface CurrentRun {
  job_id: number;
  kind: string;
  state: JobState;
  current_stage: string | null;
  stages: RunStageEntry[];
  started_at: string | null;
  elapsed_min: number | null;
  eta_min: number | null;
}

export interface OtherRunningJob {
  job_id: number;
  kind: string;
  started_at: string | null;
}

export interface RunsCurrentResponse {
  current: CurrentRun | null;
  other_running: OtherRunningJob[];
}

// U3: `GET /api/reports/{id}/citations` -- `n -> {item_id, url, title}` for every `[n]` marker a
// report can contain (agent/eoa/api/services.py `report_citations`).
export interface ReportCitation {
  item_id: number | null;
  url: string | null;
  title: string | null;
}

export interface ReportCitationsResponse {
  report_id: number;
  citations: Record<string, ReportCitation>;
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

// A14: patent / IP landscape tracking (agent/eoa/patents/**).
export interface PatentRecord {
  id: number;
  pub_number: string;
  kind: string | null;
  title: string | null;
  abstract: string | null;
  assignees: string[];
  inventors: string[];
  cpc: string[];
  priority_date: string | null;
  filing_date: string | null;
  publication_date: string | null;
  grant_date: string | null;
  family_id: string | null;
  jurisdictions: string[];
  forward_citations: number | null;
  backward_citations: number | null;
  url: string | null;
  source: string;
  subdomain: string | null;
  claims_summary_he: string | null;
  so_what_he: string | null;
  israel_relevance: number | null;
  value_score: number | null;
  value_reasons: string[];
  created_at: string;
  updated_at: string;
}

export interface PatentsResponse {
  patents: PatentRecord[];
  total: number;
}

export interface PatentsStatusResponse {
  structured_sources_configured: boolean;
  banner_he: string | null;
}

export interface PatentHeatmapCell {
  cpc: string;
  assignee: string;
  n: number;
}

export interface PatentHeatmapResponse {
  cpc_codes: string[];
  assignees: string[];
  cells: PatentHeatmapCell[];
}

export type PatentSurveyStatus = "running" | "done" | "failed";

export interface PatentSurveyCard {
  id: number;
  topic: string;
  status: PatentSurveyStatus;
  created_at: string;
  report_id: number | null;
  path_docx: string | null;
  path_md: string | null;
  path_html: string | null;
}

export interface PatentSurveyCreateResponse {
  survey?: PatentSurveyCard;
  job_id: number;
  status?: "queued" | "failed";
  error?: string | null;
}

// --- A17: מטע"דים -- מפרטים ומחירי ייחוס (eoa.payloads), עם היסטוריית גרסאות ------------------

export type PayloadCategory =
  "gimbal" | "pod" | "thermal_camera" | "detector_core" | "lrf" | "seeker" | "other";

export interface PayloadRecord {
  id: number;
  canonical_name: string;
  vendor_entity_name: string | null;
  family: string | null;
  category: PayloadCategory;
  first_seen: string | null;
  last_seen: string | null;
  notes: string | null;
  spec_version_count: number;
  price_ref_count: number;
  latest_spec_date: string | null;
  latest_price_date: string | null;
  created_at: string;
  updated_at: string;
}

export interface PayloadSpecDetector {
  type?: string | null;
  resolution?: string | null;
  pitch_um?: number | null;
}

export interface PayloadSpecFov {
  wide_deg?: number | null;
  narrow_deg?: number | null;
}

export interface PayloadSpecRanges {
  detect?: number | null;
  recognize?: number | null;
  identify?: number | null;
  target_class?: string | null;
}

export interface PayloadSpec {
  mass_kg?: number | null;
  channels?: string[];
  detector?: PayloadSpecDetector | null;
  fov?: PayloadSpecFov | null;
  ranges_km?: PayloadSpecRanges | null;
  stabilisation_urad?: number | null;
  interfaces?: string[];
  trl?: string | null;
  other?: Record<string, string>;
}

export interface PayloadSpecVersion {
  id: number;
  payload_id: number;
  version_no: number;
  effective_date: string;
  spec: PayloadSpec;
  source_item_id: number | null;
  source_url: string | null;
  source_quote: string | null;
  confidence: number | null;
  created_at: string;
}

export type PayloadPriceKind = "unit" | "contract" | "estimate";

export interface PayloadPriceRef {
  id: number;
  payload_id: number;
  price_usd: number | null;
  currency: string | null;
  original_amount: number | null;
  quantity: number | null;
  unit_price_usd: number | null;
  price_kind: PayloadPriceKind;
  date: string;
  buyer: string | null;
  programme: string | null;
  source_item_id: number | null;
  source_url: string | null;
  source_quote: string | null;
  created_at: string;
}

export interface PayloadsResponse {
  payloads: PayloadRecord[];
  total: number;
}

export interface PayloadDetailResponse {
  payload: PayloadRecord;
  spec_versions: PayloadSpecVersion[];
  price_refs: PayloadPriceRef[];
}

export interface PayloadDiffResponse {
  payload_id: number;
  a: PayloadSpecVersion;
  b: PayloadSpecVersion;
  changed_fields: string[];
}
