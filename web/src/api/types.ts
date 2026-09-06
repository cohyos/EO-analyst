import type {
  AskCitation,
  AskRequest,
  BdReportCreateResponse,
  BdTerritoryOption,
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
  PatentHeatmapResponse,
  PatentSurveyCard,
  PatentSurveyCreateResponse,
  PatentsResponse,
  PatentsStatusResponse,
  PayloadDetailResponse,
  PayloadDiffResponse,
  PayloadsResponse,
  PayloadTreeResponse,
  ReportCitationsResponse,
  ReportDetail,
  ReportSummary,
  RunsCurrentResponse,
  SecurityReviewCard,
  SettingsGetResponse,
  SettingsName,
  SettingsPutResponse,
  Survey,
  TechRadarResponse,
  TenderFeedback,
  TenderFeedbackVerdict,
  TenderSourceCoverageResponse,
  TenderStatus,
  TendersResponse,
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
  /** A13 (מיקוד תעשייה ישראלית): filter to items with `israel_relevance >= 0.5`. */
  israel?: boolean;
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
  /** A13 (מיקוד תעשייה ישראלית): filter to `is_israeli = true` entities. */
  israel?: boolean;
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
  /** F24: default view is 'open'/'unknown' from the last `since_days` days -- ignored once
   * `status` is set explicitly. */
  since_days?: number;
  include_closed?: boolean;
  include_archived?: boolean;
  limit?: number;
}

/** A14: `GET /api/patents` filters. */
export interface PatentsQuery {
  assignee?: string;
  subdomain?: string;
  israeli?: boolean;
  min_value_score?: number;
  q?: string;
  limit?: number;
}

/** A17: `GET /api/payloads` filters. */
export interface PayloadsQuery {
  category?: string;
  vendor?: string;
  // W19b (docs/REVIEW_2026-09-06_evening.md): filter to one payload family (e.g. "MX") --
  // mirrors `vendor` above, matches `eoa.api.routes.payloads.list_payloads`'s new `family` param.
  family?: string;
  q?: string;
  limit?: number;
}

/** A12 (מעקב טכנולוגי): `GET /api/tech/items` filters. */
export interface TechItemsQuery {
  subdomain?: string;
  maturity?: string;
  actor_kind?: string;
  /** period filter -- ISO date/datetime, items published/fetched on or after this. */
  since?: string;
  page?: number;
  page_size?: number;
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
  /** `existing: true` (Q5-3, docs/qa/findings_Q5_r1.md) means the backend reused a `done`
   * investigation for this item from the last 24h instead of enqueueing a new job. */
  postItemInvestigate(
    id: number,
    body: { question: string | null },
  ): Promise<{ job_id: string; existing: boolean }>;

  getEntities(query: EntitiesQuery): Promise<EntitySummary[]>;
  getEntity(id: number): Promise<EntityDetail>;
  getGraph(query: GraphQuery): Promise<GraphResponse>;
  getGraphNamedQuery(name: string, arg: string): Promise<unknown[]>;

  getInvestigations(limit?: number): Promise<InvestigationSummary[]>;
  getInvestigation(jobId: string): Promise<InvestigationDetail>;
  postInvestigationStop(jobId: string): Promise<void>;
  /** U12 "חקירה חדשה": start a free-standing investigation from a typed question. */
  postInvestigationNew(body: {
    question: string;
    item_id?: number | null;
  }): Promise<{ job_id: string }>;
  /** U12 "הרחב חקירה (תקציב נוסף)": re-run with double budget + prior findings as context. */
  postInvestigationExpand(jobId: string): Promise<{ job_id: string }>;

  // W10 (docs/REVIEW_2026-09-06_evening.md round 4): agent/eoa/api/routes/security_review.py.
  getSecurityReviews(): Promise<SecurityReviewCard[]>;
  /** "אשר והמשך": re-runs the flagged investigation with the snippet whitelisted. */
  postSecurityReviewApprove(jobId: string): Promise<{ job_id: string }>;
  /** "דחה": marks the flagged investigation reviewed, no re-run. */
  postSecurityReviewDismiss(jobId: string): Promise<{ ok: boolean }>;

  askStream(
    body: AskRequest,
    handlers: {
      onToken: (text: string) => void;
      onCitations: (items: AskCitation[]) => void;
      /** U8: provider/model that will answer — sent once, right after citations. */
      onMeta?: (provider: string, model: string) => void;
      /** U11: citations enriched with level/source_name/note, sent once after the answer
       * finishes streaming, for the "מקורות (n)" footer. */
      onSources?: (items: AskCitation[]) => void;
      /** Round 2 (docs/qa/loop/round_2_chat_fixes.md): the server wholesale-replaced the
       * streamed answer (citation repair, or a no-citations/off-topic warning prefix) — the
       * caller must replace the message content with `text`, not append it. */
      onAnswerFinal?: (text: string) => void;
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

  getTenders(query: TendersQuery): Promise<TendersResponse>;
  getTenderForecasts(limit?: number): Promise<ForecastCard[]>;
  /** A15 (docs/TENDER_PORTALS.md): per-region tender-source coverage for the "כיסוי מקורות" panel. */
  getTenderSourceCoverage(): Promise<TenderSourceCoverageResponse>;
  /** W2b: one-click 👍/👎 (+ optional free-text reason) on a tenders row -- self-tunes the
   * relevance threshold + this source's scan priority (eoa.tenders.feedback). */
  postTenderFeedback(
    tenderId: number,
    verdict: TenderFeedbackVerdict,
    reason?: string | null,
  ): Promise<TenderFeedback>;
  getTenderFeedback(tenderId: number): Promise<TenderFeedback[]>;

  /** A14: פטנטים ו-IP (agent/eoa/patents/**). */
  getPatents(query: PatentsQuery): Promise<PatentsResponse>;
  getPatentsStatus(): Promise<PatentsStatusResponse>;
  getPatentsHeatmap(
    topCpc?: number,
    topAssignees?: number,
  ): Promise<PatentHeatmapResponse>;
  getPatentSurveys(limit?: number): Promise<PatentSurveyCard[]>;
  createPatentSurvey(topic: string): Promise<PatentSurveyCreateResponse>;

  /** A17: מטע"דים -- מפרטים ומחירי ייחוס, עם היסטוריית גרסאות (agent/eoa/payloads/**). */
  getPayloads(query?: PayloadsQuery): Promise<PayloadsResponse>;
  getPayload(id: number): Promise<PayloadDetailResponse>;
  getPayloadDiff(id: number, a: number, b: number): Promise<PayloadDiffResponse>;
  // W19b: vendor -> family -> variant grouping with counts (agent/eoa/payloads/models.py's
  // `build_payload_tree`). PayloadsPage itself builds its tree client-side from the already
  // fetched `getPayloads` list (`@/lib/payloadFamilies`) rather than calling this a second time;
  // it exists on the client for API-surface completeness / other future consumers.
  getPayloadTree(): Promise<PayloadTreeResponse>;

  /** A12 (מעקב טכנולוגי, 2026-09-06): "רדאר טכנולוגי" -- subdomain x maturity matrix. */
  getTechRadar(weeks?: number): Promise<TechRadarResponse>;
  getTechItems(query?: TechItemsQuery): Promise<{ total: number; items: ItemCard[] }>;

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

  // A11 "דוח מיקוד לפיתוח עסקי, מכירה ושיווק לפי טריטוריה" (eoa.report.bd_territory).
  /** Candidate territories for the selector, with item/tender/forecast counts, most active first. */
  getBdTerritories(): Promise<BdTerritoryOption[]>;
  getBdReports(territory?: string): Promise<ReportSummary[]>;
  /** Builds synchronously if the underlying job finishes within ~55s (returns `report`), else
   * returns just a `job_id` to poll via `getBdReports`. */
  postBdReport(territory: string, lookbackDays: number): Promise<BdReportCreateResponse>;

  getSettings(name: SettingsName): Promise<SettingsGetResponse>;
  putSettings(name: SettingsName, yaml: string): Promise<SettingsPutResponse>;
}
