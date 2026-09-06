import type {
  AskRequest,
  AskSseEvent,
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
  LlmProvidersResponse,
  LlmSettingsPutResponse,
  McpCallsResponse,
  McpPingResponse,
  McpServersResponse,
  MorningResponse,
  PatentHeatmapResponse,
  PatentRecord,
  PatentSurveyCard,
  PatentSurveyCreateResponse,
  PatentsStatusResponse,
  ReportCitationsResponse,
  ReportDetail,
  ReportSummary,
  RunsCurrentResponse,
  SettingsGetResponse,
  SettingsName,
  SettingsPutResponse,
  Survey,
  TechRadarResponse,
  TenderCard,
  TenderStatus,
  TriageLevel,
} from "@/types/api";
import type {
  ApiClient,
  EntitiesQuery,
  GraphQuery,
  ItemsQuery,
  PatentsQuery,
  TechItemsQuery,
  TendersQuery,
} from "./types";
import {
  arr,
  bool,
  idStr,
  normalizeInvestigationLogLine,
  normalizeNightSummary,
  normalizeReportCitations,
  normalizeRunsCurrent,
  num,
  str,
} from "./normalize";

/** Thrown by `request()` for any non-2xx response carrying the `{"error": {...}}` envelope
 * (see `eoa.api.errors`/`app.py`). Exported so callers can distinguish e.g. a `"conflict"` (409,
 * U4/F17: an equivalent run is already active) from any other failure. */
export class ApiError extends Error {
  code: string;
  detail: unknown;
  constructor(code: string, messageHe: string, detail: unknown) {
    super(messageHe);
    this.code = code;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // ignore parse failure, fall through to generic error
    }
    const err = body as { error?: { code: string; message_he: string; detail: unknown } } | null;
    if (err?.error) {
      throw new ApiError(err.error.code, err.error.message_he, err.error.detail);
    }
    throw new ApiError("http_error", `שגיאת שרת (${res.status})`, res.statusText);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | boolean | undefined>): string {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") usp.set(k, String(v));
  }
  const s = usp.toString();
  return s ? `?${s}` : "";
}

// --- Normalizers ------------------------------------------------------
// The backend can return partial rows (empty DB, mid-ingest, a field the
// pipeline hasn't populated yet). These coerce every nullable-in-practice
// field into the shape web/src/types/api.ts + the pages expect, so no page
// component needs to special-case a missing array or object.

function normalizeItemCard(raw: Partial<ItemCard> | null | undefined): ItemCard {
  const r = raw ?? {};
  return {
    id: num(r.id),
    title: str(r.title),
    url: str(r.url),
    source_name: str(r.source_name),
    published_at: str(r.published_at),
    lang: str(r.lang),
    domain: str(r.domain),
    subdomain: r.subdomain ?? null,
    report_kind: str(r.report_kind),
    trl: r.trl ?? null,
    geography: r.geography ?? null,
    score: num(r.score),
    // Real data (2026-09-04 QA against the live backend): most items in a
    // fresh DB have level=null (not yet classified). Defaulting that to
    // "yellow" silently mislabeled ~98% of the feed — surface it as its own
    // "unclassified" level instead so the UI shows a "טרם סווג" chip.
    level: (r.level ?? "unclassified") as TriageLevel,
    triage_reason: r.triage_reason ?? null,
    summary_he: r.summary_he ?? null,
    so_what_he: r.so_what_he ?? null,
    entities_mentioned: arr(r.entities_mentioned),
    tags: arr(r.tags),
    security_status: r.security_status ?? "clean",
    dedup_of: r.dedup_of ?? null,
    key_facts: arr(r.key_facts),
    uncertainty_he: r.uncertainty_he ?? null,
    tech_maturity: r.tech_maturity ?? null,
    tech_actor_kind: r.tech_actor_kind ?? null,
    tech_readiness_note_he: r.tech_readiness_note_he ?? null,
    israel_relevance: typeof r.israel_relevance === "number" ? r.israel_relevance : null,
    israel_reasons: arr(r.israel_reasons),
  };
}

function normalizeTechRadar(raw: Partial<TechRadarResponse> | null | undefined): TechRadarResponse {
  const r = raw ?? {};
  return {
    weeks: num(r.weeks, 12),
    maturities: arr(r.maturities),
    subdomains: arr(r.subdomains).map((s) => ({
      subdomain: str(s.subdomain),
      label_he: str(s.label_he),
      counts: s.counts ?? {},
      total: num(s.total),
      sparkline: arr(s.sparkline),
    })),
  };
}

function normalizeItemDetail(raw: Partial<ItemDetail> | null | undefined): ItemDetail {
  const r = raw ?? {};
  return {
    ...normalizeItemCard(r),
    clean_text: str(r.clean_text),
    events: arr(r.events),
    edges: arr(r.edges),
    investigations: arr(r.investigations).map(normalizeInvestigationSummary),
  };
}

function normalizeEntitySummary(raw: Partial<EntitySummary> | null | undefined): EntitySummary {
  const r = raw ?? {};
  return {
    id: num(r.id),
    name: str(r.name),
    kind: str(r.kind),
    country: r.country ?? null,
    aliases: arr(r.aliases),
    focus: arr(r.focus),
    item_count: num(r.item_count),
    last_seen: r.last_seen ?? null,
    relevance: typeof r.relevance === "number" ? r.relevance : 0,
    is_watchlist: bool(r.is_watchlist),
    mentions_7d: num(r.mentions_7d),
    mentions_30d: num(r.mentions_30d),
    is_israeli: bool(r.is_israeli),
  };
}

function normalizeEntityDetail(raw: Partial<EntityDetail> | null | undefined): EntityDetail {
  const r = raw ?? {};
  return {
    ...normalizeEntitySummary(r),
    timeline: arr(r.timeline),
    business_events: arr(r.business_events),
    kpis: r.kpis ?? { mentions_7d: 0, mentions_30d: 0, events_count: 0, related_items_by_level: {} },
    edge_groups: arr(r.edge_groups),
    neighbors: arr(r.neighbors),
  };
}

function normalizeGraph(raw: Partial<GraphResponse> | null | undefined): GraphResponse {
  const r = raw ?? {};
  return { nodes: arr(r.nodes), edges: arr(r.edges) };
}

function normalizeInvestigationSummary(
  raw: Partial<InvestigationSummary> | null | undefined,
): InvestigationSummary {
  const r = raw ?? {};
  return {
    job_id: idStr(r.job_id),
    item_id: r.item_id ?? null,
    question: str(r.question),
    item_title: r.item_title ?? null,
    error: r.error ?? null,
    state: r.state ?? "not_found",
    rounds: num(r.rounds),
    queries: num(r.queries),
    pages_read: num(r.pages_read),
    outcome: r.outcome ?? null,
    started_at: r.started_at ?? null,
    finished_at: r.finished_at ?? null,
  };
}

function normalizeInvestigationDetail(
  raw: Partial<InvestigationDetail> | null | undefined,
): InvestigationDetail {
  const r = raw ?? {};
  return {
    ...normalizeInvestigationSummary(r),
    log: arr(r.log).map(normalizeInvestigationLogLine),
    answer: r.answer
      ? {
          answer_he: str(r.answer.answer_he),
          sources: arr(r.answer.sources),
          outcome: str(r.answer.outcome),
          key_facts: arr(r.answer.key_facts),
          what_was_tried_he: str(r.answer.what_was_tried_he),
          contradictions_he: str(r.answer.contradictions_he),
          queries_used: r.answer.queries_used ?? undefined,
          max_queries: r.answer.max_queries ?? undefined,
          pages_read: r.answer.pages_read ?? undefined,
          max_pages: r.answer.max_pages ?? undefined,
          rounds: r.answer.rounds ?? undefined,
          stopped_reason: r.answer.stopped_reason ?? undefined,
        }
      : null,
  };
}

function normalizeReportSummary(raw: Partial<ReportSummary> | null | undefined): ReportSummary {
  const r = raw ?? {};
  return {
    id: num(r.id),
    kind: str(r.kind),
    period_start: str(r.period_start),
    period_end: str(r.period_end),
    path_docx: r.path_docx ?? null,
    path_md: r.path_md ?? null,
    path_html: r.path_html ?? null,
    qa_passed: bool(r.qa_passed),
    created_at: str(r.created_at),
    headline_count: num(r.headline_count),
    territory: r.territory ?? null,
  };
}

function normalizeReportDetail(raw: Partial<ReportDetail> | null | undefined): ReportDetail {
  const r = raw ?? {};
  return {
    ...normalizeReportSummary(r),
    html: str(r.html),
    open_points: arr(r.open_points),
    items_included: arr(r.items_included),
  };
}

function normalizeConference(raw: Partial<Conference> | null | undefined): Conference {
  const r = raw ?? {};
  return {
    id: num(r.id),
    name: str(r.name),
    location: r.location ?? null,
    starts_at: str(r.starts_at),
    ends_at: str(r.ends_at),
    url: r.url ?? null,
    relevance_he: r.relevance_he ?? null,
    organizer: r.organizer ?? null,
    start_date: r.start_date ?? null,
    end_date: r.end_date ?? null,
    city: r.city ?? null,
    venue: r.venue ?? null,
    cadence: r.cadence ?? null,
    relevance: r.relevance ?? null,
    rationale: r.rationale ?? null,
    registration_opens: r.registration_opens ?? null,
    early_bird_deadline: r.early_bird_deadline ?? null,
    cfp_deadline: r.cfp_deadline ?? null,
    cost_range: r.cost_range ?? null,
    registration_url: r.registration_url ?? null,
    entry_conditions: r.entry_conditions ?? null,
    status: r.status ?? null,
    last_verified_at: r.last_verified_at ?? null,
    changes: r.changes ?? {},
  };
}

function normalizeTenderCard(raw: Partial<TenderCard> | null | undefined): TenderCard {
  const r = raw ?? {};
  return {
    id: num(r.id),
    source: r.source ?? null,
    external_ref: r.external_ref ?? null,
    title: r.title ?? null,
    agency: r.agency ?? null,
    country: r.country ?? null,
    published_at: r.published_at ?? null,
    deadline: r.deadline ?? null,
    url: r.url ?? null,
    cpv_naics: arr(r.cpv_naics),
    summary_he: r.summary_he ?? null,
    relevance: r.relevance ?? null,
    matched_terms: arr(r.matched_terms),
    entities: arr(r.entities),
    status: (r.status ?? "unknown") as TenderCard["status"],
    item_id: r.item_id ?? null,
    created_at: str(r.created_at),
    updated_at: str(r.updated_at),
  };
}

function normalizePatentRecord(raw: Partial<PatentRecord> | null | undefined): PatentRecord {
  const r = raw ?? {};
  return {
    id: num(r.id),
    pub_number: str(r.pub_number),
    kind: r.kind ?? null,
    title: r.title ?? null,
    abstract: r.abstract ?? null,
    assignees: arr(r.assignees),
    inventors: arr(r.inventors),
    cpc: arr(r.cpc),
    priority_date: r.priority_date ?? null,
    filing_date: r.filing_date ?? null,
    publication_date: r.publication_date ?? null,
    grant_date: r.grant_date ?? null,
    family_id: r.family_id ?? null,
    jurisdictions: arr(r.jurisdictions),
    forward_citations: r.forward_citations ?? null,
    backward_citations: r.backward_citations ?? null,
    url: r.url ?? null,
    source: str(r.source) || "unknown",
    subdomain: r.subdomain ?? null,
    claims_summary_he: r.claims_summary_he ?? null,
    so_what_he: r.so_what_he ?? null,
    israel_relevance: r.israel_relevance ?? null,
    value_score: r.value_score ?? null,
    value_reasons: arr(r.value_reasons),
    created_at: str(r.created_at),
    updated_at: str(r.updated_at),
  };
}

function normalizePatentSurveyCard(raw: Partial<PatentSurveyCard> | null | undefined): PatentSurveyCard {
  const r = raw ?? {};
  return {
    id: num(r.id),
    topic: str(r.topic),
    status: (r.status ?? "running") as PatentSurveyCard["status"],
    created_at: str(r.created_at),
    report_id: r.report_id ?? null,
    path_docx: r.path_docx ?? null,
    path_md: r.path_md ?? null,
    path_html: r.path_html ?? null,
  };
}

function normalizeForecastCard(raw: Partial<ForecastCard> | null | undefined): ForecastCard {
  const r = raw ?? {};
  return {
    id: num(r.id),
    platform: str(r.platform),
    buyer_country: r.buyer_country ?? null,
    trigger_event_id: r.trigger_event_id ?? null,
    trigger_item_id: r.trigger_item_id ?? null,
    payload_need: str(r.payload_need),
    candidate_vendors: arr(r.candidate_vendors),
    likelihood: r.likelihood ?? null,
    window_from: r.window_from ?? null,
    window_to: r.window_to ?? null,
    rationale_he: r.rationale_he ?? null,
    sources: arr(r.sources),
    created_at: str(r.created_at),
    updated_at: str(r.updated_at),
  };
}

function normalizeClarification(raw: Partial<Clarification> | null | undefined): Clarification {
  const r = raw ?? {};
  return {
    id: num(r.id),
    kind: str(r.kind),
    question: str(r.question),
    options: r.options ?? null,
    answer: r.answer ?? null,
    asked_at: str(r.asked_at),
    timeout_at: r.timeout_at ?? null,
    assumed: bool(r.assumed),
  };
}

function normalizeSurvey(raw: Partial<Survey> | null | undefined): Survey {
  const r = raw ?? {};
  return {
    id: num(r.id),
    report_id: r.report_id ?? null,
    questions: arr(r.questions),
    answers: r.answers ?? null,
  };
}

function normalizeLesson(raw: Partial<Lesson> | null | undefined): Lesson {
  const r = raw ?? {};
  return {
    id: num(r.id),
    kind: str(r.kind),
    text: str(r.text),
    active: bool(r.active),
    created_at: str(r.created_at),
  };
}

function normalizeJob(raw: Partial<Job> | null | undefined): Job {
  const r = raw ?? {};
  return {
    id: num(r.id),
    kind: str(r.kind),
    payload: (r.payload as Record<string, unknown> | null | undefined) ?? null,
    state: (r.state ?? "queued") as Job["state"],
    priority: num(r.priority),
    attempts: num(r.attempts),
    not_before: r.not_before ?? null,
    started_at: r.started_at ?? null,
    finished_at: r.finished_at ?? null,
    error: r.error ?? null,
    result: r.result ?? null,
    created_at: str(r.created_at),
    updated_at: str(r.updated_at),
  };
}

export const realApi: ApiClient = {
  getMorning: async () => {
    const data = await request<Partial<MorningResponse>>("/api/morning");
    return {
      report: data?.report ? normalizeReportDetail(data.report) : null,
      headlines: arr(data?.headlines),
      open_points: arr(data?.open_points),
      night_summary: normalizeNightSummary(data?.night_summary),
      recent_errors: arr(data?.recent_errors).map((e) => ({
        id: num(e?.id),
        job_id: e?.job_id ?? null,
        stage: e?.stage ?? null,
        message: str(e?.message),
        at: e?.at ?? null,
      })),
    };
  },

  getItems: async (query: ItemsQuery) => {
    const data = await request<Partial<ItemsResponse>>(
      `/api/items${qs({
        level: query.level?.join(","),
        domain: query.domain,
        since: query.since,
        q: query.q,
        country: query.country?.join(","),
        group_by: query.group_by,
        page: query.page,
        page_size: query.page_size,
        sort: query.sort,
        israel: query.israel || undefined,
      })}`,
    );
    const items = arr(data?.items).map(normalizeItemCard);
    return { total: num(data?.total, items.length), items, groups: data?.groups ?? undefined };
  },
  getItemsByCountry: async (query) => {
    const data = await request<Partial<ItemsByCountryResponse>>(
      `/api/items/by-country${qs({
        level: query.level?.join(","),
        domain: query.domain,
        since: query.since,
      })}`,
    );
    return { countries: arr(data?.countries) };
  },
  getItem: async (id: number) =>
    normalizeItemDetail(await request<Partial<ItemDetail>>(`/api/items/${id}`)),
  postItemFeedback: async (id, body) =>
    normalizeItemCard(
      await request<Partial<ItemCard>>(`/api/items/${id}/feedback`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    ),
  postItemInvestigate: async (id, body) => {
    const data = await request<{ job_id?: string | number; existing?: boolean }>(
      `/api/items/${id}/investigate`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
    return { job_id: idStr(data?.job_id), existing: bool(data?.existing) };
  },

  getEntities: async (query: EntitiesQuery) => {
    const data = await request<Partial<EntitySummary>[] | null>(
      `/api/entities${qs({
        q: query.q,
        kind: query.kind,
        country: query.country,
        watchlist: query.watchlist || undefined,
        all: query.all || undefined,
        sort: query.sort,
        limit: query.limit,
        israel: query.israel || undefined,
      })}`,
    );
    return arr(data).map(normalizeEntitySummary);
  },
  getEntity: async (id: number) =>
    normalizeEntityDetail(await request<Partial<EntityDetail>>(`/api/entities/${id}`)),
  getGraph: async (query: GraphQuery) =>
    normalizeGraph(
      await request<Partial<GraphResponse>>(
        `/api/graph${qs({
          entity_id: query.entity_id,
          depth: query.depth,
          labels: query.labels,
        })}`,
      ),
    ),
  getGraphNamedQuery: async (name: string, arg: string) =>
    arr(await request<unknown[] | null>(`/api/graph/query${qs({ name, arg })}`)),

  getInvestigations: async (limit = 20) =>
    arr(
      await request<Partial<InvestigationSummary>[] | null>(
        `/api/investigations${qs({ limit })}`,
      ),
    ).map(normalizeInvestigationSummary),
  getInvestigation: async (jobId: string) =>
    normalizeInvestigationDetail(
      await request<Partial<InvestigationDetail>>(`/api/investigations/${jobId}`),
    ),
  postInvestigationStop: (jobId: string) =>
    request<void>(`/api/investigations/${jobId}/stop`, { method: "POST" }),
  postInvestigationNew: async (body) => {
    const data = await request<{ job_id?: string | number }>(`/api/investigations`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return { job_id: idStr(data?.job_id) };
  },
  postInvestigationExpand: async (jobId: string) => {
    const data = await request<{ job_id?: string | number }>(
      `/api/investigations/${jobId}/expand`,
      { method: "POST" },
    );
    return { job_id: idStr(data?.job_id) };
  },

  askStream: (body: AskRequest, handlers) => {
    const controller = new AbortController();
    (async () => {
      try {
        const res = await fetch("/api/ask", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        if (!res.ok || !res.body) {
          throw new Error(`שגיאת שרת (${res.status})`);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n\n");
          buffer = lines.pop() ?? "";
          for (const chunk of lines) {
            const dataLine = chunk
              .split("\n")
              .find((l) => l.startsWith("data:"));
            if (!dataLine) continue;
            const json = dataLine.slice(5).trim();
            if (!json) continue;
            let evt: AskSseEvent;
            try {
              evt = JSON.parse(json) as AskSseEvent;
            } catch {
              continue; // ignore malformed SSE frame rather than crashing the stream
            }
            if (evt.type === "token") handlers.onToken(str(evt.text));
            else if (evt.type === "citations") handlers.onCitations(arr(evt.items));
            else if (evt.type === "meta") handlers.onMeta?.(str(evt.provider), str(evt.model));
            else if (evt.type === "sources") handlers.onSources?.(arr(evt.items));
            else if (evt.type === "done") handlers.onDone();
          }
        }
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        handlers.onError(err as Error);
      }
    })();
    return () => controller.abort();
  },

  getConferences: async (from, to) =>
    arr(await request<Partial<Conference>[] | null>(`/api/conferences${qs({ from, to })}`)).map(
      normalizeConference,
    ),
  getConferencesIcalUrl: () => "/api/conferences/ical",

  getTenders: async (query: TendersQuery) => {
    const raw = await request<{
      tenders?: Partial<TenderCard>[] | null;
      counts?: Partial<Record<TenderStatus, number>> | null;
    } | null>(
      `/api/tenders${qs({
        status: query.status,
        country: query.country,
        q: query.q,
        since_days: query.since_days,
        include_closed: query.include_closed,
        include_archived: query.include_archived,
        limit: query.limit,
      })}`,
    );
    return {
      tenders: arr(raw?.tenders).map(normalizeTenderCard),
      counts: raw?.counts ?? {},
    };
  },
  getTenderForecasts: async (limit = 100) =>
    arr(
      await request<Partial<ForecastCard>[] | null>(`/api/tenders/forecasts${qs({ limit })}`),
    ).map(normalizeForecastCard),

  // A14: פטנטים ו-IP.
  getPatents: async (query: PatentsQuery) => {
    const raw = await request<{ patents?: Partial<PatentRecord>[] | null; total?: number }>(
      `/api/patents${qs({
        assignee: query.assignee,
        subdomain: query.subdomain,
        israeli: query.israeli,
        min_value_score: query.min_value_score,
        q: query.q,
        limit: query.limit,
      })}`,
    );
    return { patents: arr(raw?.patents).map(normalizePatentRecord), total: raw?.total ?? 0 };
  },
  getPatentsStatus: async () =>
    request<PatentsStatusResponse>("/api/patents/status"),
  getPatentsHeatmap: async (topCpc = 10, topAssignees = 10) =>
    request<PatentHeatmapResponse>(
      `/api/patents/heatmap${qs({ top_cpc: topCpc, top_assignees: topAssignees })}`,
    ),
  getPatentSurveys: async (limit = 30) =>
    arr(await request<Partial<PatentSurveyCard>[] | null>(`/api/patents/surveys${qs({ limit })}`)).map(
      normalizePatentSurveyCard,
    ),
  createPatentSurvey: async (topic: string) =>
    request<PatentSurveyCreateResponse>("/api/patents/surveys", {
      method: "POST",
      body: JSON.stringify({ topic }),
    }),

  // A12 (מעקב טכנולוגי, 2026-09-06): "רדאר טכנולוגי".
  getTechRadar: async (weeks = 12) =>
    normalizeTechRadar(await request<Partial<TechRadarResponse>>(`/api/tech/radar${qs({ weeks })}`)),
  getTechItems: async (query: TechItemsQuery = {}) => {
    const data = await request<{ total?: number; items?: Partial<ItemCard>[] | null }>(
      `/api/tech/items${qs({
        subdomain: query.subdomain,
        maturity: query.maturity,
        actor_kind: query.actor_kind,
        since: query.since,
        page: query.page,
        page_size: query.page_size,
      })}`,
    );
    const items = arr(data?.items).map(normalizeItemCard);
    return { total: num(data?.total, items.length), items };
  },

  getClarifications: async (open = true) =>
    arr(
      await request<Partial<Clarification>[] | null>(
        `/api/clarifications${qs({ open: open ? "true" : "false" })}`,
      ),
    ).map(normalizeClarification),
  postClarificationAnswer: (id, answer) =>
    request<void>(`/api/clarifications/${id}/answer`, {
      method: "POST",
      body: JSON.stringify({ answer }),
    }),
  getLatestSurvey: async () =>
    normalizeSurvey(await request<Partial<Survey>>("/api/surveys/latest")),
  postSurveyAnswers: (id, answers) =>
    request<void>(`/api/surveys/${id}/answers`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    }),
  getLessons: async () =>
    arr(await request<Partial<Lesson>[] | null>("/api/lessons")).map(normalizeLesson),
  postLesson: async (kind, text) =>
    normalizeLesson(
      await request<Partial<Lesson>>("/api/lessons", {
        method: "POST",
        body: JSON.stringify({ kind, text }),
      }),
    ),
  deleteLesson: (id) => request<void>(`/api/lessons/${id}`, { method: "DELETE" }),

  getJobs: async (state, limit = 50) =>
    arr(await request<Partial<Job>[] | null>(`/api/jobs${qs({ state, limit })}`)).map(
      normalizeJob,
    ),
  postRun: async (scope, mode) => {
    const data = await request<{ job_id?: string | number }>("/api/run", {
      method: "POST",
      body: JSON.stringify({ scope, mode }),
    });
    return { job_id: idStr(data?.job_id) };
  },
  postJobCancel: (id) => request<void>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  // (`id` is a `jobs.id` integer on the wire — the template literal above
  // coerces either the numeric or string form the caller passes.)
  getRunsCurrent: async () =>
    normalizeRunsCurrent(await request<Partial<RunsCurrentResponse>>("/api/runs/current")),

  getReports: async (kind, limit = 30) =>
    arr(
      await request<Partial<ReportSummary>[] | null>(`/api/reports${qs({ kind, limit })}`),
    ).map(normalizeReportSummary),
  getReport: async (id) =>
    normalizeReportDetail(await request<Partial<ReportDetail>>(`/api/reports/${id}`)),
  getReportFileUrl: (id, fmt) => `/api/reports/${id}/file?fmt=${fmt}`,
  getReportCitations: async (id) =>
    normalizeReportCitations(
      await request<Partial<ReportCitationsResponse>>(`/api/reports/${id}/citations`),
    ),

  getBdTerritories: async () =>
    arr(await request<Partial<BdTerritoryOption>[] | null>("/api/bd/territories")).map((t) => ({
      territory: str(t.territory),
      items: num(t.items),
      tenders: num(t.tenders),
      forecasts: num(t.forecasts),
      configured: bool(t.configured),
    })),
  getBdReports: async (territory) =>
    arr(
      await request<Partial<ReportSummary>[] | null>(`/api/bd/reports${qs({ territory })}`),
    ).map(normalizeReportSummary),
  postBdReport: async (territory, lookbackDays) => {
    const data = await request<Partial<BdReportCreateResponse>>("/api/bd/reports", {
      method: "POST",
      body: JSON.stringify({ territory, lookback_days: lookbackDays }),
    });
    return {
      job_id: idStr(data?.job_id),
      status: data?.status,
      error: data?.error ?? null,
      report: data?.report ? normalizeReportDetail(data.report) : undefined,
    };
  },

  getSettings: async (name: SettingsName) => {
    const data = await request<Partial<SettingsGetResponse>>(`/api/settings/${name}`);
    return { yaml: str(data?.yaml) };
  },
  putSettings: async (name: SettingsName, yaml: string) => {
    const data = await request<Partial<SettingsPutResponse>>(`/api/settings/${name}`, {
      method: "PUT",
      body: JSON.stringify({ yaml }),
    });
    return { ok: bool(data?.ok), errors: arr(data?.errors) };
  },

  getLlmProviders: async () => {
    const data = await request<Partial<LlmProvidersResponse>>("/api/llm/providers");
    return {
      mode: data?.mode === "cloud" ? "cloud" : "local",
      allow_cloud: bool(data?.allow_cloud),
      interactive_default: str(data?.interactive_default) || "ollama",
      chains: data?.chains ?? {},
      providers: arr(data?.providers).map((p) => ({
        id: str(p?.id),
        label: str(p?.label),
        kind: p?.kind === "cloud" ? "cloud" : p?.kind === "api" ? "api" : "local",
        available: bool(p?.available),
        models: arr(p?.models).map((m) => str(m)),
        key_env: p?.key_env ? str(p.key_env) : undefined,
        power_levels: p?.power_levels ? arr(p.power_levels).map((lvl) => str(lvl)) : undefined,
      })),
    };
  },
  getLlmCalls: async (since = "24h") => {
    const data = await request<Partial<LlmCallsSummary>>(`/api/llm/calls${qs({ since })}`);
    return {
      since_hours: Number(data?.since_hours) || 24,
      providers: arr(data?.providers).map((p) => ({
        provider: str(p?.provider),
        calls: Number(p?.calls) || 0,
        failures: Number(p?.failures) || 0,
        fallbacks: Number(p?.fallbacks) || 0,
        prompt_tokens: Number(p?.prompt_tokens) || 0,
        completion_tokens: Number(p?.completion_tokens) || 0,
        est_cost_usd: Number(p?.est_cost_usd) || 0,
      })),
      totals: {
        calls: Number(data?.totals?.calls) || 0,
        failures: Number(data?.totals?.failures) || 0,
        fallbacks: Number(data?.totals?.fallbacks) || 0,
        prompt_tokens: Number(data?.totals?.prompt_tokens) || 0,
        completion_tokens: Number(data?.totals?.completion_tokens) || 0,
        est_cost_usd: Number(data?.totals?.est_cost_usd) || 0,
        cloud_calls: Number(data?.totals?.cloud_calls) || 0,
      },
    };
  },
  putLlmSettings: async (body) => {
    const data = await request<Partial<LlmSettingsPutResponse>>("/api/llm/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    });
    return { ok: bool(data?.ok), errors: arr(data?.errors), revision: data?.revision ?? null };
  },

  getMcpServers: async () => {
    const data = await request<Partial<McpServersResponse>>("/api/mcp/servers");
    return {
      mcp_enabled: bool(data?.mcp_enabled),
      servers: arr(data?.servers).map((s) => ({
        id: str(s?.id),
        label: str(s?.label),
        transport: s?.transport === "http" ? "http" : "stdio",
        enabled: bool(s?.enabled),
        inherit_cli_only: bool(s?.inherit_cli_only),
        key_configured: s?.key_configured === null || s?.key_configured === undefined ? null : bool(s.key_configured),
        key_env: s?.key_env ? arr(s.key_env).map((k) => str(k)) : null,
        tool_count: s?.tool_count === null || s?.tool_count === undefined ? null : Number(s.tool_count),
        ok: s?.ok === null || s?.ok === undefined ? null : bool(s.ok),
        error: s?.error ?? null,
        latency_ms: s?.latency_ms === null || s?.latency_ms === undefined ? null : Number(s.latency_ms),
        tools: arr(s?.tools).map((tName) => str(tName)),
      })),
    };
  },
  postMcpServerPing: async (serverId: string) => {
    const data = await request<Partial<McpPingResponse>>(`/api/mcp/servers/${encodeURIComponent(serverId)}/ping`, {
      method: "POST",
    });
    return {
      id: str(data?.id) || serverId,
      ok: bool(data?.ok),
      error: data?.error ?? null,
      tool_count: Number(data?.tool_count) || 0,
      tools: arr(data?.tools).map((t) => str(t)),
      latency_ms: Number(data?.latency_ms) || 0,
    };
  },
  getMcpCalls: async (since = "24h") => {
    const data = await request<Partial<McpCallsResponse>>(`/api/mcp/calls${qs({ since })}`);
    return {
      since_hours: Number(data?.since_hours) || 24,
      calls: arr(data?.calls).map((c) => ({
        server: str(c?.server),
        tool: str(c?.tool),
        calls: Number(c?.calls) || 0,
        failures: Number(c?.failures) || 0,
        flagged: Number(c?.flagged) || 0,
        avg_duration_ms: Number(c?.avg_duration_ms) || 0,
        total_chars: Number(c?.total_chars) || 0,
      })),
      totals: {
        calls: Number(data?.totals?.calls) || 0,
        failures: Number(data?.totals?.failures) || 0,
        flagged: Number(data?.totals?.flagged) || 0,
      },
    };
  },
};

export type { TriageLevel };
