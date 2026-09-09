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
  // CORR (cross-source corroboration, 2026-09-07): additive — absent/null on any response built
  // before the backend lands this means "not yet checked", never an error. The frontend always
  // normalizes a missing/partial value to `{ status: "unknown", count: 0, sources: [], checked_at:
  // null }` (see `normalizeCorroboration` in `web/src/api/normalize.ts`) so every consumer can read
  // `item.corroboration.status` unconditionally instead of null-checking the whole object.
  corroboration?: Corroboration | null;
  // PL-ui (2026-09-07): additive -- ids from `web/src/lib/productLines.ts`'s fixed catalog.
  // Absent/undefined means "not yet tagged," normalized to `[]` by both API clients so every
  // consumer can read it unconditionally.
  product_lines?: string[];
}

export type CorroborationStatus =
  "single_source" | "corroborated" | "official_primary" | "unknown";

export type CorroborationSourceKind = "duplicate" | "same_event" | "official";

export interface CorroborationSource {
  item_id: number;
  source_name: string;
  url: string;
  published_at: string | null;
  kind: CorroborationSourceKind;
}

export interface Corroboration {
  status: CorroborationStatus;
  count: number;
  sources: CorroborationSource[];
  checked_at: string | null;
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

// R10-graph (docs/qa/loop/round_10_fixes.md): the analyst-facing entity graph explorer
// (`web/src/components/graph/**`) is built on five new read endpoints layered on top of the
// U10 `GraphNode`/`GraphEdgeRow` shapes above (which the original `/entities/{id}/graph` and
// `/graph` endpoints keep returning unchanged).

/** A graph node enriched with the stats an analyst actually needs at a glance: mention volume,
 * recency, a corroboration-status breakdown across every item mentioning it, and the distinct
 * product lines those items carry (entities themselves don't carry a `product_lines` column --
 * this is aggregated server-side from the items that mention the entity). */
export interface GraphNodeStats extends GraphNode {
  mention_count: number;
  last_seen: string | null;
  corroboration: {
    corroborated: number;
    official_primary: number;
    single_source: number;
    unknown: number;
  };
  product_lines: string[];
}

export interface GraphEdgeEvidence {
  item_id: number;
  title: string | null;
  published_at: string | null;
}

/** A `graph_edges` group, collapsed by (src, dst, relation): `weight` is the number of distinct
 * items backing the relation, `first_seen`/`last_seen` bound its evidence dates, `evidence` is
 * up to 3 of its most recent supporting items. */
export interface GraphEdgeAgg {
  src: number;
  dst: number;
  relation: string;
  weight: number;
  first_seen: string | null;
  last_seen: string | null;
  evidence: GraphEdgeEvidence[];
}

export interface NeighborhoodResponse {
  nodes: GraphNodeStats[];
  edges: GraphEdgeAgg[];
  center_id: number;
  /** true when the neighborhood had more than 300 nodes and was capped -- the UI shows a
   * "הצג עוד" control rather than silently truncating. */
  truncated?: boolean;
}

export interface GraphOverviewResponse {
  nodes: GraphNodeStats[];
  edges: GraphEdgeAgg[];
}

export interface GraphPathResponse {
  nodes: GraphNode[];
  edges: GraphEdgeAgg[];
  hops: number;
}

export interface GraphSearchResult {
  id: number;
  name: string;
  kind: string;
  country: string | null;
  mention_count: number;
}

export interface EntityInvestigationRef {
  job_id: number | string;
  state: string;
  question: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface EntityReportRef {
  id: number;
  kind: string;
  period_start: string | null;
  period_end: string | null;
  created_at: string | null;
}

/** `GET /api/entities/{id}/detail`: `EntityDetail` (below) plus the investigations and reports
 * that cite the entity -- neither is exposed by the original `GET /api/entities/{id}`. */
export interface EntityDetailFull extends EntityDetail {
  investigations: EntityInvestigationRef[];
  reports: EntityReportRef[];
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
  | "insufficient_context"
  // Round-5 P7 (docs/REPORT_TEMPLATE_BENCHMARK.md DS3): the investigation could not actually be
  // carried out (every fetched page was quarantined by the security guard, every search hit was
  // screened out before any page was read, or the cloud-delegated answer was fully redacted) --
  // distinct from `not_found` (searched fully, genuinely nothing there). Also a first-class value
  // of `InvestigationOut.outcome` itself, not just `stopped_reason` -- see
  // agent/eoa/llm/schemas/analysis.py.
  | "blocked";

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
  /** Round-5 P7 (docs/REPORT_TEMPLATE_BENCHMARK.md DS3): one Hebrew sentence explaining *why*
   * `outcome === "blocked"` -- always set on a blocked result, absent/null otherwise. See
   * `agent/eoa/llm/schemas/analysis.py::InvestigationOut.blocked_reason_he`. */
  blocked_reason_he?: string | null;
  /** Investigation confidence 0-1 as persisted in `jobs.result.confidence` (null when the
   * engine did not set one). */
  confidence?: number | null;
}

export interface InvestigationDetail extends InvestigationSummary {
  log: InvestigationLogLine[];
  answer: InvestigationOut | null;
  // R10-links (docs/qa/loop/round_10_fixes.md): trigger item / rerun-expansion lineage / citing
  // reports, in one nested object -- additive, absent/null on any response built before the
  // backend lands this.
  provenance?: InvestigationProvenance | null;
}

/** R10-links: the item that triggered an investigation -- `null` for a free-standing question
 * (U12, no `item_id` on the original request). */
export interface InvestigationTriggerItem {
  id: number;
  title: string | null;
  url: string | null;
  source_name: string | null;
  published_at: string | null;
}

/** R10-links: one node of an investigation's own rerun/expansion chain (oldest-first). `kind`
 * describes how *that* job relates to its own immediate predecessor -- exactly one entry in a
 * non-trivial chain is `"original"`. */
export interface InvestigationLineageEntry {
  job_id: string;
  outcome: string | null;
  confidence: number | null;
  finished_at: string | null;
  kind: "rerun" | "expansion" | "original";
}

/** R10-links: one report whose "חקירות עומק" section cites this investigation. */
export interface InvestigationReportRef {
  id: number;
  kind: string | null;
  period_end: string | null;
  territory: string | null;
  title_he: string;
  path_html: string | null;
}

/** R10-links: `GET /api/investigations/{id}`'s nested `provenance` object. */
export interface InvestigationProvenance {
  job: {
    job_id: string;
    state: string | null;
    question: string | null;
    started_at: string | null;
    finished_at: string | null;
  };
  trigger_item: InvestigationTriggerItem | null;
  lineage: InvestigationLineageEntry[];
  reports: InvestigationReportRef[];
}

/** R10-links: `GET /api/items/{id}/investigations` -- richer than the plain `investigations`
 * array embedded in `ItemDetail` (job_id/question/state/dates only): outcome, confidence, and
 * rerun/expansion lineage pointers, for the outcome badge + confidence + date the item page's
 * investigations block shows. */
export interface ItemInvestigationRef {
  job_id: string;
  question: string | null;
  state: InvestigationState;
  error: string | null;
  outcome: string | null;
  confidence: number | null;
  started_at: string | null;
  finished_at: string | null;
  rerun_of_job_id: string | null;
  expanded_from_job_id: string | null;
}

/** R10-links: `GET /api/reports/{id}/investigations` -- one entry per investigation the report's
 * own "חקירות עומק" section actually rendered (the same shape
 * `eoa.report.daily.collect_deep_search` produces; only the fields the "חקירות בדוח" side list
 * needs are declared here -- the response may carry more). */
export interface ReportInvestigationRef {
  job_id: string;
  item_id: number | null;
  trigger_title: string | null;
  question: string | null;
  outcome: string | null;
  confidence: number | null;
  rerun_of_job_id: string | null;
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
  // CORR (2026-09-07): present once the backend enriches citations from the same `items` row's
  // corroboration check -- same shape/absence convention as `ItemCard.corroboration` above.
  corroboration?: Corroboration | null;
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
  // W14 (docs/REVIEW_2026-09-06_evening.md, user finding 2026-09-06 19:10): additive fields from
  // `eoa.api.services._report_card` -- a descriptive Hebrew title/subject/preview so the reports
  // list is no longer a flat run of identical-looking "<kind> — <date>" rows, plus version
  // grouping (`group_key`/`is_latest`) so only the newest run per kind+subject shows by default.
  /** e.g. "סקר פטנטים: FPA עם פיקסל דיגיטלי (DROIC) — 06.09 19:03" / "דוח יומי — 06.09". */
  title_he: string;
  /** Topic (patent_survey) or territory Hebrew name (bd_territory) -- null for daily/weekly/monthly. */
  subject_he: string | null;
  /** `created_at`, ISO -- kept separate from `created_at` so the UI never has to guess which of
   * the two timestamp fields is meant for display. */
  built_at: string;
  /** First two sentences of the executive summary, plain text (citation markers/markdown stripped). */
  preview_he: string | null;
  /** Count of "נספח מקורות" (sources appendix) rows -- may differ from `headline_count` when the
   * report's citation registry was extended beyond the base item/patent list. */
  source_count: number;
  /** Count of `qa_report.errors` -- 0 when the report has no recorded QA errors. */
  qa_issues: number;
  /** kind+subject (kind+period for daily/weekly/monthly) -- rows sharing a `group_key` are
   * different versions/re-runs of the same report; see `is_latest`. */
  group_key: string;
  /** True for the newest row in its `group_key` (within the returned list, for `getReports`; a
   * one-off "any newer row in this group?" check for `getReport`'s single-report detail). The UI
   * shows only `is_latest` rows by default, with older ones behind a version expander. */
  is_latest: boolean;
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

// PL-ui (2026-09-07): "קווי מוצר" -- product-line status & business-development tracking for the
// six EO/IR product lines the frozen contract (docs/qa/loop/round_7_fixes.md "### PL-ui status")
// defines. `id` is a stable key the backend also uses -- see `web/src/lib/productLines.ts` for the
// fixed catalog of the six ids + Hebrew/English names.
export interface ProductLineStats {
  items_7d: number;
  items_30d: number;
  events_30d: number;
  open_tenders: number;
  forecasts: number;
  patents_90d: number;
  active_competitors: number;
}

/** Subset of `ReportSummary` carried inline on `GET /api/product-lines` list rows, per the frozen
 * contract -- the full row (for the "open" link) is fetched via the normal `getReport`/`getReports`
 * endpoints using this `id`. */
export interface ProductLineReportRef {
  id: number;
  created_at: string;
  qa_passed: boolean;
  path_html: string;
}

export interface ProductLine {
  id: string;
  name_he: string;
  name_en: string;
  subdomains: string[];
  exemplar_systems: string[];
  competitors: string[];
  stats: ProductLineStats;
  latest_report: ProductLineReportRef | null;
}

export interface ProductLineDetail extends ProductLine {
  recent_items: ItemCard[];
  open_tenders: TenderCard[];
  reports: ReportSummary[];
}

/** `POST /api/product-lines/{id}/report` -- queues a report build; poll `getProductLine(id)` for
 * `reports`/`latest_report` to update, same pattern as the BD page's own report-creation flow. */
export interface ProductLineReportCreateResponse {
  job_id: string | number;
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

// U2/UI-ERRORS (docs/qa/content_review/UI-ERRORS.md): backs the Morning "שגיאות בריצה האחרונה"
// panel (agent/eoa/api/services.py `recent_errors`). Every field beyond id/job_id/stage/message/at
// is computed server-side at read time from `run_log.detail` (never persisted separately), so a
// classification-table improvement there applies retroactively to historical rows too.
export interface RecentErrorLogEntry {
  id: number;
  job_id: number | null;
  stage: string | null;
  message: string;
  /** Exception class name, e.g. "KeyError" — null when it could not be determined at all. */
  error_type: string | null;
  /** Last 5 stack frames as "file:line:function" — no local variable values. */
  traceback_tail: string[];
  item_id: number | null;
  /** Deep link for this error: an item, an investigation, or the run's own replay timeline. */
  link: string | null;
  cause_he: string;
  action_he: string;
  impact_he: string;
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

// W2b (0021_tender_feedback.py, "be open" requirement, 2026-09-06 evening): every notice that
// clears the two-signal vocabulary gate is now stored -- 'candidate' (below the self-tuning
// relevance threshold) or 'accepted' (at/above it) at insert time; 'rejected-by-user' once an
// operator gives explicit 👎 feedback (hidden by default, see services.list_tenders).
export type TenderIntake = "candidate" | "accepted" | "rejected-by-user";

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
  // W2b (additive): 0-1 self-tuning relevance signal + intake bucket -- see TenderIntake above.
  relevance_score: number | null;
  intake: TenderIntake;
  matched_terms: string[];
  entities: string[];
  status: TenderStatus;
  item_id: number | null;
  created_at: string;
  updated_at: string;
  // PL-ui (2026-09-07): additive -- ids from `web/src/lib/productLines.ts`'s fixed catalog.
  // Absent/undefined means "not yet tagged," normalized to `[]` by both API clients so every
  // consumer can read it unconditionally.
  product_lines?: string[];
}

// W2b: one 👍/👎 an operator gave a tender. Mirrors `tender_feedback`
// (db/migrations/versions/0021_tender_feedback.py) / `eoa.tenders.feedback`.
export type TenderFeedbackVerdict = "relevant" | "irrelevant";

export interface TenderFeedback {
  id: number;
  tender_id: number;
  verdict: TenderFeedbackVerdict;
  reason: string | null;
  source: string | null;
  territory: string | null;
  matched_terms: string[];
  created_at: string;
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
  // W2b (additive): self-tuning scan-priority decrement (0 = baseline; never disables a source,
  // only nudges eoa.tenders.scan to poll it later in the pass) -- see eoa.tenders.feedback.
  priority_decrement: number;
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
  // W20 (docs/REVIEW_2026-09-06_evening.md): per-kind identifying subject derived server-side
  // from the job's own `payload` (deep_search's question, bd_report's territory, patent_survey's
  // topic) or `created_at` for the period-based runs that carry no per-job subject at all — null
  // when the kind has no defined subject rule. See `_job_subject_he` in `eoa.api.services`.
  subject_he: string | null;
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
  // agent/eoa/api/services.py `_stage_timeline_from_log`: the terminal `run_log` row's `detail`
  // JSON, stripped of `minutes`/`stage`. For a `failed` stage this carries `{"error": "<exc>"[:300]}`
  // (agent/eoa/orchestrator/jobs.py line ~145) -- the UI QA fix (2026-09-08) surfaces it as the
  // failed stage's legend tooltip so an analyst can see *why* a stage failed without reading logs.
  detail?: Record<string, unknown> | null;
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
  // W19b (docs/REVIEW_2026-09-06_evening.md, migration 0024): the model-number/generation half
  // of `canonical_name` with the leading vendor/family stripped (e.g. "MX-15" for a payload
  // whose `family` is "MX") -- `eoa.payloads.models.parse_family_variant`'s other half. Feeds
  // the collapsed vendor -> family -> variant tree (`@/lib/payloadFamilies`,
  // `GET /api/payloads/tree`) so several variants of one product line group under one
  // expandable row instead of flooding the operator with a flat list. Optional (rather than
  // always-present-but-nullable like the fields around it) so existing test fixtures built
  // before W19b (`PayloadsPage.test.tsx`, `i18n/englishMode.test.tsx`) that construct a
  // `PayloadRecord` literal without it keep compiling unchanged.
  variant?: string | null;
  category: PayloadCategory;
  first_seen: string | null;
  last_seen: string | null;
  notes: string | null;
  // W19 (docs/REVIEW_2026-09-06_evening.md, migration 0022): identity-level image/spec-sheet
  // reference -- vendor's own product page, never invented (spec_source is a short label, e.g.
  // "l3harris.com"). All three are nullable; the UI shows an honest "spec/price not yet
  // documented" state when spec_url is null.
  image_url: string | null;
  spec_url: string | null;
  spec_source: string | null;
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

// --- W19b (docs/REVIEW_2026-09-06_evening.md): GET /api/payloads/tree -- vendor -> family ->
// variant grouping with counts, mirrored client-side (without the watchlist corporate-alias
// merge, which needs config/watchlist.yaml) by `@/lib/payloadFamilies`'s `buildPayloadTree` so
// the collapsed-tree UI doesn't need a second round trip for data it already fetched via
// `GET /api/payloads`. Shape must stay identical to `eoa.payloads.models.build_payload_tree`'s
// return value (agent/eoa/payloads/models.py). ---------------------------------------------

export interface PayloadTreeVariant {
  id: number;
  canonical_name: string;
  variant: string;
  category: PayloadCategory;
  image_url: string | null;
  spec_url: string | null;
  spec_source: string | null;
  spec_version_count: number;
  price_ref_count: number;
  latest_spec_date: string | null;
  latest_price_date: string | null;
}

export interface PayloadTreeFamily {
  family: string;
  variant_count: number;
  spec_version_count: number;
  price_ref_count: number;
  latest_spec_date: string | null;
  latest_price_date: string | null;
  variants: PayloadTreeVariant[];
}

export interface PayloadTreeVendor {
  vendor: string;
  family_count: number;
  payload_count: number;
  spec_version_count: number;
  price_ref_count: number;
  families: PayloadTreeFamily[];
}

export interface PayloadTreeResponse {
  vendors: PayloadTreeVendor[];
  vendor_count: number;
  family_count: number;
  payload_count: number;
}

// --- PD-ui (docs/PLAN_PRODUCT_DOSSIER.md): "סקירת שוק עמוקה למוצר" -- product_dossier ----------
// Frozen contract, sections 3/5/6. Every fact-bearing row/object carries its own `cites: number[]`
// (citation numbers into the dossier's own `sources` registry, section 2) -- a field the research
// could not establish is `null`/an empty list, never a guess (rendered "לא נמצא במקורות"). This is
// a UI-side mirror of `agent/eoa/llm/schemas/product_dossier.py::ProductDossierOut`, built against
// the frozen contract before the backend lane (PD-backend) lands it -- see `normalizeDossier*` in
// `web/src/api/real.ts` for the defensive, never-throws normalization every field goes through.

/** Mirrors a Pydantic `Sentence`-style object (section 3's "cites per sentence via Sentence
 * objects like the reports"): one prose sentence/bullet plus the citation numbers backing it. */
export interface DossierSentence {
  text_he: string;
  cites: number[];
}

export interface DossierIdentity {
  product_name: string;
  vendor: string | null;
  product_family: string | null;
  category_he: string | null;
  first_announced: string | null;
  /** e.g. "בפיתוח" / "בייצור" / "בשירות" / "הוצא משירות" -- free Hebrew text from the model, not
   * a fixed enum (the plan's "in development / in production / fielded / retired" are examples). */
  status_he: string | null;
  cites: number[];
}

/** LESSONS-2 (2026-09-09, docs/qa/content_review/LESSONS-fable-dossier.md item 7): high/medium/low,
 * computed deterministically by the backend (`eoa.dossier.extract`) after grounding -- never
 * authored by the model. Optional/absent on data from before this field existed. */
export type DossierRowConfidence = "high" | "medium" | "low";

export interface DossierSpecRow {
  parameter_he: string;
  value: string;
  unit: string | null;
  variant: string | null;
  /** datasheet / brochure / article / official */
  source_kind: string | null;
  cites: number[];
  /** PD-vocab-ui (2026-09-09): stable key into `config/spec_vocabulary.yaml`/`specVocabulary.ts`
   * (docs/PLAN_SPEC_VOCABULARY.md) -- set once the extraction lane (PD-vocab-extract) lands;
   * `""`/absent for a legacy free-named row (pre-vocabulary) or a genuine `other_specifications`
   * overflow row. `DossierSpecTable` groups/orders by this when present and falls back to a flat
   * render of `parameter_he` when it isn't -- both shapes must render, never just one. */
  key?: string;
  confidence?: DossierRowConfidence | null;
}

export interface DossierVersionRow {
  name: string;
  year: number | null;
  changes_he: string;
  platforms: string[];
  cites: number[];
  /** LESSONS-2 item 7: what evidence (from a cited source) supports this variant actually
   * existing -- distinct from `changes_he` (what changed). */
  evidence_he?: string | null;
  confidence?: DossierRowConfidence | null;
}

export interface DossierPerformanceRow {
  metric_he: string;
  claimed_value: string | null;
  /** "tested_or_operational_value" in the plan -- kept visually/semantically distinct from
   * `claimed_value` everywhere this renders (never merged into one cell). */
  tested_value: string | null;
  conditions_he: string | null;
  cites: number[];
  /** See `DossierSpecRow.key` -- same vocabulary, same fallback discipline. */
  key?: string;
  confidence?: DossierRowConfidence | null;
}

export interface DossierMaturity {
  trl: number | null;
  operational_users: string[];
  platforms_integrated: string[];
  first_fielding: string | null;
  assessment_he: string | null;
  cites: number[];
}

/** contract_award / FMS / framework / option / export_license */
export type DossierDealKind = "contract_award" | "FMS" | "framework" | "option" | "export_license" | string;

export interface DossierDealRow {
  date: string | null;
  /** PD-fix (2026-09-08, item 3): "deal" (the deal's own date) vs "published" (backfilled from
   * the cited source's own publish date -- never indistinguishable from an actual deal-closing
   * date). Optional: absent on data persisted before this field existed. */
  date_kind?: "deal" | "published" | string | null;
  customer: string | null;
  country: string | null;
  /** PD-fix (2026-09-08, item 3): set instead of `country` when a source names only a broader
   * region ("Asia-Pacific country") rather than a specific country. Optional: absent on data
   * persisted before this field existed. */
  region_he?: string | null;
  kind: DossierDealKind;
  amount: string | null;
  currency: string | null;
  quantity: number | null;
  platform: string | null;
  cites: number[];
  /** 0..1, deal-level confidence -- distinct from the whole dossier's own `confidence`. */
  confidence: number | null;
  /** LESSONS-2 item 4: the high/medium/low row-confidence column -- distinct from the pre-existing
   * continuous `confidence` float above (that one is the model's own subjective estimate for the
   * deal itself; this one is the deterministic, definition-based classification). */
  confidence_level?: DossierRowConfidence | null;
}

export interface DossierPriceRow {
  figure: string;
  currency: string | null;
  /** e.g. "ליחידה" / "למגרש של N" / "לתוכנית כולה". */
  basis_he: string;
  date: string | null;
  source_kind: string | null;
  cites: number[];
}

export interface DossierPartnerRow {
  partner: string;
  /** integrator / subcontractor / co-development / reseller (free Hebrew text). */
  role_he: string;
  since: string | null;
  cites: number[];
  confidence?: DossierRowConfidence | null;
}

export interface DossierCompetitorRow {
  product: string;
  vendor: string | null;
  comparison_he: string;
  cites: number[];
  confidence?: DossierRowConfidence | null;
}

/** LESSONS-2 item 1: "ציר זמן כרונולוגי" -- built from deals/programme-deals/variants/
 * identity.first_announced (deterministic) plus LLM-extracted milestones with a real cited date. */
export type DossierTimelineKind = "launch" | "contract" | "integration" | "exhibition" | "variant" | "milestone" | string;

export interface DossierTimelineRow {
  date: string | null;
  event_he: string;
  kind: DossierTimelineKind;
  cites: number[];
}

/** LESSONS-2 item 2: the labelled analyst pricing estimate -- wholly separate from `pricing`
 * (published figures only). `null` unless the backend's gate (a cited contract total with
 * duration/scope AND a cited market anchor) passed. */
export interface DossierAssumptionRow {
  text_he: string;
  cites: number[];
}

export interface DossierMarketAnchorRow {
  product_he: string;
  price_range_he: string;
  cites: number[];
}

export interface DossierPricingEstimate {
  method_he: string;
  assumptions: DossierAssumptionRow[];
  market_anchors: DossierMarketAnchorRow[];
  range_low: number | null;
  range_high: number | null;
  currency: string | null;
  basis_he: string | null;
  /** Always "low" -- an estimate is a labelled inference, never presented at higher confidence. */
  confidence: "low";
}

/** LESSONS-2 item 3: up to 5 critically-reviewed vendor claims. */
export type DossierClaimVerdict = "plausible" | "unverified" | "contradicted" | string;

export interface DossierClaimReviewRow {
  claim_he: string;
  basis_he: string | null;
  verifiability_he: string | null;
  comparability_he: string | null;
  verdict: DossierClaimVerdict;
  cites: number[];
}

/** LESSONS-2 item 5: renders the backend's cross-run gap tracking (closed/open/new) when present. */
export type DossierGapStatus = "closed" | "open" | "new" | string;

export interface DossierGapTrackingRow {
  gap_he: string;
  status: DossierGapStatus;
  cites: number[];
}

/** LESSONS-2 item 7: platforms as their own table (platform, domain, integration evidence, cites). */
export interface DossierPlatformRow {
  platform: string;
  domain: string | null;
  integration_evidence_he: string | null;
  cites: number[];
}

export interface DossierRegulatoryExport {
  export_regime_he: string | null;
  restrictions_he: string | null;
  cites: number[];
}

export interface DossierPatentRef {
  pub_number: string;
  title: string | null;
  assignee: string | null;
  relevance_he: string | null;
  cites: number[];
}

export interface DossierTenderRef {
  tender_id: number | null;
  title: string;
  status: string | null;
  relevance_he: string | null;
  cites: number[];
}

/** `ProductDossierOut` (docs/PLAN_PRODUCT_DOSSIER.md section 3). */
export interface ProductDossierOut {
  identity: DossierIdentity;
  /** <= 6 sentences, what the product is and where it stands. */
  summary: DossierSentence[];
  specifications: DossierSpecRow[];
  variants_and_versions: DossierVersionRow[];
  performance: DossierPerformanceRow[];
  maturity: DossierMaturity;
  deals: DossierDealRow[];
  pricing: DossierPriceRow[];
  partnerships: DossierPartnerRow[];
  competitors: DossierCompetitorRow[];
  regulatory_export: DossierRegulatoryExport;
  patents: DossierPatentRef[];
  tenders_and_forecasts: DossierTenderRef[];
  /** What is NOT known, contradictions between sources. */
  risks_and_gaps: DossierSentence[];
  /** vs. the previous dossier of the same product_key -- `null`/empty on a product's first run. */
  what_changed: DossierSentence[];
  /** For the user's BD role, hedged, quantity first. */
  bd_implications: DossierSentence[];
  /** PD-vocab-ui (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md §3.3 item 3): a genuine spec fact that
   * matched no vocabulary key -- the deliberate overflow valve so an unanticipated spec is never
   * silently dropped. Rows here always carry `key: ""`. Renders as its own small un-grouped "אחר"
   * table at the end of the spec section (§5.1), never diffed by key (§4), never merged into the
   * grouped table. Absent/`[]` for data from before this field existed. */
  other_specifications?: DossierSpecRow[];
  /** LESSONS-2 item 1. Absent/`[]` for data from before this field existed. */
  timeline?: DossierTimelineRow[];
  /** LESSONS-2 item 2. Absent/`null` for data from before this field existed, or when the gate
   * didn't pass this run. */
  pricing_estimate?: DossierPricingEstimate | null;
  /** LESSONS-2 item 3. Absent/`[]` for data from before this field existed. */
  claims_review?: DossierClaimReviewRow[];
  /** LESSONS-2 item 5. Absent/`[]` for data from before this field existed. */
  gaps_tracking?: DossierGapTrackingRow[];
  /** LESSONS-2 item 7. Absent/`[]` for data from before this field existed. */
  platforms?: DossierPlatformRow[];
}

/** One row of the dossier's own citation registry (section 2's `sources JSONB`). */
export interface DossierSource {
  n: number;
  url: string;
  title: string;
  kind: string | null;
  reliability: string | null;
  accessed_at: string | null;
}

export type DossierOutcome = "found" | "partial" | "not_found";

/** One `product_dossiers` row, without the heavy `data`/`sources` payload -- the shape both the
 * list endpoint's `latest` and a product's own `dossiers` run-history array use. */
export interface DossierRunRef {
  id: number;
  created_at: string;
  outcome: DossierOutcome;
  confidence: number | null;
  report_id: number | null;
  /** PD-cloud-tools (2026-09-09): the LLM leg this run's research + extraction actually used --
   * "codex:<model>" / "claude:<model>" / "agy:<model>", or "local" for the configured chain (the
   * default, and every run made before this field existed). */
  llm_leg: string;
}

/** `GET /api/dossiers` list row. The frozen contract (section 5) declares only a bare `count` on
 * this row, with no further definition -- the run-history array (`dossiers`) lives on the
 * *detail* endpoint, not here. Since section 6's own card spec calls for a "deal count" (not a
 * run count) at list granularity, and the list row otherwise carries nothing deal-shaped, this
 * is read as the product's total number of grounded deals in its latest dossier (not the number
 * of past runs) -- PD-backend: please confirm/align `_dossier_card`'s `count` field to this
 * reading, or add a distinct field if a run count is also needed here. */
export interface DossierSummary {
  product_key: string;
  product_name: string;
  vendor: string | null;
  latest: DossierRunRef | null;
  count: number;
  /** PD-vocab-ui (2026-09-09): NOT currently returned by `list_dossiers()`
   * (`agent/eoa/api/services.py`) even though `product_dossiers.product_line` exists in the DB --
   * declared here, optional, for forward compatibility once the backend adds it (a straightforward
   * additive change, flagged to the backend lane rather than assumed). Comparison entry points
   * (`DossiersPage`'s "השווה" picker) work without it today (the gate itself runs on
   * `DossierComparePage`, which resolves each product's line from `GET /api/dossiers/{key}/{id}`,
   * the one endpoint that already returns it -- see `DossierRunDetail.product_line` below). */
  product_line?: string | null;
}

/** PD-fix (2026-09-08, item 5): one research topic's live status, written by
 * `eoa.dossier.plan.run_plan`'s `on_progress` into the running job's own row and surfaced through
 * `pending_job.progress` below -- what the detail page's pending-run banner polls (every 10s, same
 * as the rest of `pending_job`) to show a per-topic check/spinner instead of one opaque "running"
 * line for a run that can take ~2h. */
export interface DossierProgressTopic {
  topic: string;
  title_he: string;
  status: "pending" | "running" | "done" | "failed";
  seconds: number | null;
  sources_found: number | null;
}

export interface DossierPendingJob {
  job_id: string;
  state: string;
  /** One entry per planned research topic, in question order; `[]` before the backend has written
   * its first progress snapshot (or for a job enqueued before this fix shipped). */
  progress: DossierProgressTopic[];
}

/** `GET /api/dossiers/{product_key}` -- the product's identity/aliases, its full run history, the
 * latest run's full structured data + sources, and any in-flight (re)run job. */
export interface DossierDetail {
  product_key: string;
  product_name: string;
  vendor: string | null;
  aliases: string[];
  dossiers: DossierRunRef[];
  latest: (ProductDossierOut & { sources: DossierSource[] }) | null;
  pending_job: DossierPendingJob | null;
  /** See `DossierSummary.product_line`'s doc comment -- same backend gap, same forward-compat
   * field. Not returned by `dossier_detail()` today either; `DossierComparePage` falls back to
   * `getDossierRun` for the authoritative value. */
  product_line?: string | null;
}

/** `GET /api/dossiers/{product_key}/{id}` -- one specific run, in full. */
export interface DossierRunDetail extends DossierRunRef {
  data: ProductDossierOut;
  sources: DossierSource[];
  path_docx: string | null;
  path_md: string | null;
  path_html: string | null;
  /** PD-vocab-ui (2026-09-09): `get_dossier()` (`agent/eoa/api/services.py`) already returns
   * `product_key`/`product_name`/`vendor`/`product_line` alongside `data`/`sources` -- this is the
   * one dossier endpoint the live backend DOES carry `product_line` on today (unlike the list/
   * detail endpoints, see `DossierSummary`/`DossierDetail`'s own notes), so `DossierComparePage`
   * uses this endpoint as the authoritative source for gating "same product line" comparisons.
   * All optional here since the frozen contract (docs/PLAN_PRODUCT_DOSSIER.md §5) never declared
   * them on this type and older/mock data may omit them. */
  product_key?: string;
  product_name?: string;
  vendor?: string | null;
  product_line?: string | null;
}

/** `POST /api/dossiers` request body. */
export interface DossierCreateBody {
  product_name: string;
  vendor?: string | null;
  aliases?: string[];
  product_line?: string | null;
  /** 1 (regular) or 2 (double) research budget -- section 4's `budget_multiplier`. */
  budget_multiplier?: number | null;
  /** PD-cloud-tools (2026-09-09): optional per-run model override -- "codex:<model>" |
   * "claude:<model>" | "agy:<model>" | "local"/null (the configured chain, the default). */
  llm_leg?: string | null;
}

export interface DossierCreateResponse {
  job_id: string;
  product_key: string;
}

export interface DossierRerunResponse {
  job_id: string;
}
