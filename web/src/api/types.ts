import type {
  AskCitation,
  AskRequest,
  Clarification,
  Conference,
  EntityDetail,
  EntitySummary,
  ForecastCard,
  GraphResponse,
  InvestigationDetail,
  InvestigationSummary,
  ItemCard,
  ItemDetail,
  ItemsByCountryResponse,
  ItemsResponse,
  Job,
  Lesson,
  LlmCallsSummary,
  LlmChainEntry,
  LlmProvidersResponse,
  LlmSettingsPutResponse,
  McpCallsResponse,
  McpPingResponse,
  McpServersResponse,
  MorningResponse,
  ReportCitationsResponse,
  ReportDetail,
  ReportSummary,
  RunsCurrentResponse,
  SettingsGetResponse,
  SettingsName,
  SettingsPutResponse,
  Survey,
  TenderCard,
  TenderStatus,
  TriageLevel,
} from "@/types/api";

export interface ItemsQuery {
  level?: TriageLevel[];
  domain?: string;
  since?: string;
  q?: string;
  /** U7b: one or more ISO-2/region codes (e.g. `["US","IL"]`) — comma-joined on the wire. */
  country?: string[];
  /** U7a: request the additive `groups` aggregation on the response. */
  group_by?: "country";
  page?: number;
  page_size?: number;
  sort?: "score" | "published_at";
}

export interface ItemsByCountryQuery {
  level?: TriageLevel[];
  domain?: string;
  since?: string;
}

export interface EntitiesQuery {
  q?: string;
  kind?: string;
  country?: string;
  watchlist?: boolean;
  /** "הצג הכל" — bypass the backend's default relevance >= 0.4 filter (F15). */
  all?: boolean;
  sort?: "last_seen" | "mentions_7d" | "mentions_30d" | "name";
  limit?: number;
}

export interface GraphQuery {
  entity_id: number;
  depth?: number;
  labels?: string;
}

export interface TendersQuery {
  status?: TenderStatus;
  country?: string;
  q?: string;
  limit?: number;
}

/**
 * Client-side surface of docs/API.md. Both the real (fetch-based) client and
 * the mock client implement this exact shape so pages never branch on
 * VITE_USE_MOCKS themselves.
 */
export interface ApiClient {
  getMorning(): Promise<MorningResponse>;

  getItems(query: ItemsQuery): Promise<ItemsResponse>;
  getItemsByCountry(query: ItemsByCountryQuery): Promise<ItemsByCountryResponse>;
  getItem(id: number): Promise<ItemDetail>;
  postItemFeedback(
    id: number,
    body: { user_level: TriageLevel; comment: string | null },
  ): Promise<ItemCard>;
  postItemInvestigate(
    id: number,
    body: { question: string | null },
  ): Promise<{ job_id: string }>;

  getEntities(query: EntitiesQuery): Promise<EntitySummary[]>;
  getEntity(id: number): Promise<EntityDetail>;
  getGraph(query: GraphQuery): Promise<GraphResponse>;
  getGraphNamedQuery(name: string, arg: string): Promise<unknown[]>;

  getInvestigations(limit?: number): Promise<InvestigationSummary[]>;
  getInvestigation(jobId: string): Promise<InvestigationDetail>;
  postInvestigationStop(jobId: string): Promise<void>;
  /** U12 "חקירה חדשה": start a free-standing investigation from a typed question. */
  postInvestigationNew(body: { question: string; item_id?: number | null }): Promise<{ job_id: string }>;
  /** U12 "הרחב חקירה (תקציב נוסף)": re-run with double budget + prior findings as context. */
  postInvestigationExpand(jobId: string): Promise<{ job_id: string }>;

  askStream(
    body: AskRequest,
    handlers: {
      onToken: (text: string) => void;
      onCitations: (items: AskCitation[]) => void;
      /** U8: provider/model that will answer — sent once, right after citations. */
      onMeta?: (provider: string, model: string) => void;
      onDone: () => void;
      onError: (err: Error) => void;
    },
  ): () => void; // returns an abort function

  /** U8: local Ollama vs. cloud CLI/API — availability, models, power levels, current mode/default. */
  getLlmProviders(): Promise<LlmProvidersResponse>;
  putLlmSettings(body: {
    interactive_default?: string;
    allow_cloud?: boolean;
    /** U8-א (Revision 2026-09-06): the global local/cloud switch. */
    mode?: "local" | "cloud";
    /** U8-ה, ChainsEditor: replaces the entire `llm_providers.chains` map when provided. The
     * server validates every entry (known provider id, non-empty model on a non-ollama step,
     * power within that provider's own power_levels) and appends the local `ollama` terminal
     * step to any role that doesn't already end with one. */
    chains?: Record<string, LlmChainEntry[]>;
  }): Promise<LlmSettingsPutResponse>;
  /** U8-4: per-provider fallback-chain call accounting (`?since=24h`) for the Settings card. */
  getLlmCalls(since?: string): Promise<LlmCallsSummary>;

  /** A8 (docs/adr/006-mcp-sources.md): MCP tool sources — status/tools/last error per server. */
  getMcpServers(): Promise<McpServersResponse>;
  /** A8: "בדוק חיבור" — connect, list tools, disconnect. */
  postMcpServerPing(serverId: string): Promise<McpPingResponse>;
  /** A8: per-server/tool call accounting (`?since=24h`) for the Settings MCP card. */
  getMcpCalls(since?: string): Promise<McpCallsResponse>;

  getConferences(from?: string, to?: string): Promise<Conference[]>;
  getConferencesIcalUrl(): string;

  getTenders(query: TendersQuery): Promise<TenderCard[]>;
  getTenderForecasts(limit?: number): Promise<ForecastCard[]>;

  getClarifications(open?: boolean): Promise<Clarification[]>;
  postClarificationAnswer(id: number, answer: string): Promise<void>;
  getLatestSurvey(): Promise<Survey>;
  postSurveyAnswers(id: number, answers: Record<string, unknown>): Promise<void>;
  getLessons(): Promise<Lesson[]>;
  postLesson(kind: string, text: string): Promise<Lesson>;
  deleteLesson(id: number): Promise<void>;

  getJobs(state?: string, limit?: number): Promise<Job[]>;
  /**
   * U4/F17: idempotent on the server -- if an equivalent run is already queued/running, this
   * rejects with an `ApiError` (code `"conflict"`, `detail: {job_id, kind, state}`) instead of
   * enqueueing a second one. Callers that want "run now" semantics (rather than a hard failure)
   * should catch that specific case and treat it as "already running".
   */
  postRun(scope: string, mode: "eco" | "full"): Promise<{ job_id: string }>;
  postJobCancel(id: number): Promise<void>;
  /** U4/F17: the active run's stage-by-stage progress/ETA, plus any other concurrently-running job. */
  getRunsCurrent(): Promise<RunsCurrentResponse>;

  getReports(kind?: string, limit?: number): Promise<ReportSummary[]>;
  getReport(id: number): Promise<ReportDetail>;
  getReportFileUrl(id: number, fmt: "docx" | "md" | "html"): string;
  /** U3: `n -> {item_id, url, title}` for every `[n]` citation marker the report contains. */
  getReportCitations(id: number): Promise<ReportCitationsResponse>;

  getSettings(name: SettingsName): Promise<SettingsGetResponse>;
  putSettings(name: SettingsName, yaml: string): Promise<SettingsPutResponse>;
}
