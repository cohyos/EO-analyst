import type {
  AskCitation,
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

export const realApi: ApiClient = {
  getMorning: () => request<MorningResponse>("/api/morning"),

  getItems: (query: ItemsQuery) =>
    request<ItemsResponse>(
      `/api/items${qs({
        level: query.level?.join(","),
        domain: query.domain,
        since: query.since,
        q: query.q,
        page: query.page,
        page_size: query.page_size,
        sort: query.sort,
      })}`,
    ),
  getItem: (id: number) => request<ItemDetail>(`/api/items/${id}`),
  postItemFeedback: (id, body) =>
    request<ItemCard>(`/api/items/${id}/feedback`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  postItemInvestigate: (id, body) =>
    request<{ job_id: string }>(`/api/items/${id}/investigate`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getEntities: (query: EntitiesQuery) =>
    request<EntitySummary[]>(
      `/api/entities${qs({ q: query.q, kind: query.kind, limit: query.limit })}`,
    ),
  getEntity: (id: number) => request<EntityDetail>(`/api/entities/${id}`),
  getGraph: (query: GraphQuery) =>
    request<GraphResponse>(
      `/api/graph${qs({
        entity_id: query.entity_id,
        depth: query.depth,
        labels: query.labels,
      })}`,
    ),
  getGraphNamedQuery: (name: string, arg: string) =>
    request<unknown[]>(`/api/graph/query${qs({ name, arg })}`),

  getInvestigations: (limit = 20) =>
    request<InvestigationSummary[]>(`/api/investigations${qs({ limit })}`),
  getInvestigation: (jobId: string) =>
    request<InvestigationDetail>(`/api/investigations/${jobId}`),
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
            const evt = JSON.parse(json) as AskSseEvent;
            if (evt.type === "token") handlers.onToken(evt.text);
            else if (evt.type === "citations") handlers.onCitations(evt.items);
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

  getConferences: (from, to) =>
    request<Conference[]>(`/api/conferences${qs({ from, to })}`),
  getConferencesIcalUrl: () => "/api/conferences/ical",

  getClarifications: (open = true) =>
    request<Clarification[]>(`/api/clarifications${qs({ open: open ? "true" : "false" })}`),
  postClarificationAnswer: (id, answer) =>
    request<void>(`/api/clarifications/${id}/answer`, {
      method: "POST",
      body: JSON.stringify({ answer }),
    }),
  getLatestSurvey: () => request<Survey>("/api/surveys/latest"),
  postSurveyAnswers: (id, answers) =>
    request<void>(`/api/surveys/${id}/answers`, {
      method: "POST",
      body: JSON.stringify({ answers }),
    }),
  getLessons: () => request<Lesson[]>("/api/lessons"),
  postLesson: (kind, text) =>
    request<Lesson>("/api/lessons", { method: "POST", body: JSON.stringify({ kind, text }) }),
  deleteLesson: (id) => request<void>(`/api/lessons/${id}`, { method: "DELETE" }),

  getJobs: (state, limit = 50) =>
    request<Job[]>(`/api/jobs${qs({ state, limit })}`),
  postRun: (scope, mode) =>
    request<{ job_id: string }>("/api/run", {
      method: "POST",
      body: JSON.stringify({ scope, mode }),
    }),
  postJobCancel: (id) => request<void>(`/api/jobs/${id}/cancel`, { method: "POST" }),

  getReports: (kind, limit = 30) =>
    request<ReportSummary[]>(`/api/reports${qs({ kind, limit })}`),
  getReport: (id) => request<ReportDetail>(`/api/reports/${id}`),
  getReportFileUrl: (id, fmt) => `/api/reports/${id}/file?fmt=${fmt}`,

  getSettings: (name: SettingsName) => request<SettingsGetResponse>(`/api/settings/${name}`),
  putSettings: (name: SettingsName, yaml: string) =>
    request<SettingsPutResponse>(`/api/settings/${name}`, {
      method: "PUT",
      body: JSON.stringify({ yaml }),
    }),
};

export type { TriageLevel };
