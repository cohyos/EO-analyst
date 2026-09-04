import type {
  AskRequest,
  AskSseEvent,
  Clarification,
  Conference,
  EntityDetail,
  EntitySummary,
  GraphResponse,
  InvestigationDetail,
  InvestigationSummary,
  ItemCard,
  ItemDetail,
  ItemsResponse,
  Job,
  Lesson,
  MorningResponse,
  ReportDetail,
  ReportSummary,
  SettingsGetResponse,
  SettingsName,
  SettingsPutResponse,
  Survey,
  TriageLevel,
} from "@/types/api";
import type { ApiClient, EntitiesQuery, GraphQuery, ItemsQuery } from "./types";
import {
  arr,
  bool,
  idStr,
  normalizeInvestigationLogLine,
  normalizeNightSummary,
  num,
  str,
} from "./normalize";

class ApiError extends Error {
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

function qs(params: Record<string, string | number | undefined>): string {
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
  };
}

function normalizeEntityDetail(raw: Partial<EntityDetail> | null | undefined): EntityDetail {
  const r = raw ?? {};
  return {
    ...normalizeEntitySummary(r),
    timeline: arr(r.timeline),
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
    };
  },

  getItems: async (query: ItemsQuery) => {
    const data = await request<Partial<ItemsResponse>>(
      `/api/items${qs({
        level: query.level?.join(","),
        domain: query.domain,
        since: query.since,
        q: query.q,
        page: query.page,
        page_size: query.page_size,
        sort: query.sort,
      })}`,
    );
    const items = arr(data?.items).map(normalizeItemCard);
    return { total: num(data?.total, items.length), items };
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
    const data = await request<{ job_id?: string | number }>(`/api/items/${id}/investigate`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return { job_id: idStr(data?.job_id) };
  },

  getEntities: async (query: EntitiesQuery) => {
    const data = await request<Partial<EntitySummary>[] | null>(
      `/api/entities${qs({ q: query.q, kind: query.kind, limit: query.limit })}`,
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

  getReports: async (kind, limit = 30) =>
    arr(
      await request<Partial<ReportSummary>[] | null>(`/api/reports${qs({ kind, limit })}`),
    ).map(normalizeReportSummary),
  getReport: async (id) =>
    normalizeReportDetail(await request<Partial<ReportDetail>>(`/api/reports/${id}`)),
  getReportFileUrl: (id, fmt) => `/api/reports/${id}/file?fmt=${fmt}`,

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
};

export type { TriageLevel };
