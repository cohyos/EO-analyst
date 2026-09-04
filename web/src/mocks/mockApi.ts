import type {
  AskCitation,
  Conference,
  EntityDetail,
  EventRow,
  ForecastCard,
  GraphResponse,
  InvestigationSummary,
  ItemCard,
  ItemDetail,
  Job,
  JobState,
  Lesson,
  MorningResponse,
  ReportDetail,
  SettingsGetResponse,
  SettingsName,
  SettingsPutResponse,
  Survey,
  TenderCard,
  TriageLevel,
} from "@/types/api";
import type { ApiClient, EntitiesQuery, GraphQuery, ItemsQuery, TendersQuery } from "@/api/types";
import { mockEntities } from "./data/entities";
import { findMockItem, mockItems } from "./data/items";
import { findMockInvestigation, mockInvestigations } from "./data/investigations";
import { mockReport } from "./data/reports";
import { mockForecasts, mockTenders } from "./data/tenders";
import {
  mockClarifications,
  mockGraph,
  mockHeadlines,
  mockJobs,
  mockLessons,
  mockNightSummary,
  mockSurvey,
} from "./data/misc";
import { mockSettingsYaml } from "./settingsYaml";

function delay<T>(value: T, ms = 220): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

// Mutable in-memory copies so feedback / answers / lessons persist for the
// lifetime of the tab.
const items = mockItems.map((it) => ({ ...it }));
const clarifications = mockClarifications.map((c) => ({ ...c }));
const lessons = [...mockLessons];
const jobs = [...mockJobs];
const settingsStore = { ...mockSettingsYaml };
let lessonId = lessons.length + 1;
let investigateJobCounter = 9000;

function itemToEvents(item: ItemCard): EventRow[] {
  return [
    {
      id: item.id * 10 + 1,
      kind: "publication",
      summary_he: `פרסום: ${item.title}`,
      occurred_at: item.published_at,
      item_id: item.id,
    },
  ];
}

export const mockApi: ApiClient = {
  getMorning: async (): Promise<MorningResponse> =>
    delay({
      report: mockReport,
      headlines: mockHeadlines,
      open_points: mockReport.open_points,
      night_summary: mockNightSummary,
    }),

  getItems: async (query: ItemsQuery) => {
    let filtered = items.slice();
    if (query.level?.length) {
      filtered = filtered.filter((it) => query.level!.includes(it.level));
    }
    if (query.domain) {
      filtered = filtered.filter((it) => it.domain === query.domain);
    }
    if (query.q) {
      const q = query.q.toLowerCase();
      filtered = filtered.filter(
        (it) =>
          it.title.toLowerCase().includes(q) ||
          it.summary_he?.toLowerCase().includes(q) ||
          it.entities_mentioned.some((e) => e.toLowerCase().includes(q)),
      );
    }
    if (query.since) {
      const since = new Date(query.since).getTime();
      filtered = filtered.filter((it) => new Date(it.published_at).getTime() >= since);
    }
    const sort = query.sort ?? "score";
    filtered = filtered.slice().sort((a, b) =>
      sort === "score"
        ? b.score - a.score
        : new Date(b.published_at).getTime() - new Date(a.published_at).getTime(),
    );
    const page = query.page ?? 1;
    const pageSize = query.page_size ?? 50;
    const start = (page - 1) * pageSize;
    return delay({
      total: filtered.length,
      items: filtered.slice(start, start + pageSize),
    });
  },

  getItem: async (id: number): Promise<ItemDetail> => {
    const item = findMockItem(id);
    if (!item) throw new Error("not_found");
    return delay({
      ...item,
      clean_text: `${item.summary_he}\n\n${item.so_what_he}\n\n(טקסט מלא מדומה לצורך תצוגה במצב Mock.)`,
      events: itemToEvents(item),
      edges: [],
      investigations: mockInvestigations
        .filter((inv) => inv.item_id === item.id)
        .map(({ log: _log, answer: _answer, ...rest }) => rest as InvestigationSummary),
    });
  },

  postItemFeedback: async (id, body) => {
    const item = items.find((it) => it.id === id);
    if (!item) throw new Error("not_found");
    item.level = body.user_level;
    return delay({ ...item });
  },

  postItemInvestigate: async (_id, _body) => {
    investigateJobCounter += 1;
    return delay({ job_id: `inv-${investigateJobCounter}` }, 350);
  },

  getEntities: async (query: EntitiesQuery) => {
    let filtered = mockEntities.slice();
    if (query.q) {
      const q = query.q.toLowerCase();
      filtered = filtered.filter(
        (e) =>
          e.name.toLowerCase().includes(q) ||
          e.aliases.some((a) => a.toLowerCase().includes(q)),
      );
    }
    if (query.kind) filtered = filtered.filter((e) => e.kind === query.kind);
    return delay(filtered.slice(0, query.limit ?? 50));
  },

  getEntity: async (id: number): Promise<EntityDetail> => {
    const entity = mockEntities.find((e) => e.id === id);
    if (!entity) throw new Error("not_found");
    const relatedItems = items.filter((it) => it.entities_mentioned.includes(entity.name));
    return delay({
      ...entity,
      timeline: relatedItems.map((it) => ({
        kind: "item" as const,
        id: it.id,
        title: it.title,
        summary_he: it.summary_he,
        occurred_at: it.published_at,
        item_id: it.id,
      })),
      neighbors: mockGraph.edges
        .filter((e) => e.src === id || e.dst === id)
        .map((e) => {
          const otherId = e.src === id ? e.dst : e.src;
          const other = mockGraph.nodes.find((n) => n.id === otherId);
          return {
            entity_id: otherId,
            entity_name: other?.name ?? `ישות #${otherId}`,
            label: e.label,
            item_id: e.item_id,
          };
        }),
    });
  },

  getGraph: async (query: GraphQuery): Promise<GraphResponse> => {
    if (!query.entity_id) return delay(mockGraph);
    const depth = query.depth ?? 1;
    const nodeIds = new Set<number>([query.entity_id]);
    for (let d = 0; d < depth; d++) {
      for (const e of mockGraph.edges) {
        if (nodeIds.has(e.src)) nodeIds.add(e.dst);
        if (nodeIds.has(e.dst)) nodeIds.add(e.src);
      }
    }
    return delay({
      nodes: mockGraph.nodes.filter((n) => nodeIds.has(n.id)),
      edges: mockGraph.edges.filter((e) => nodeIds.has(e.src) && nodeIds.has(e.dst)),
    });
  },

  getGraphNamedQuery: async (name: string, arg: string) => {
    // Named, pre-built queries only (no free Cypher) — mocked as filtered
    // slices of the graph so the "שאילתות שמורות" buttons render results.
    if (name === "partners_of_competitors") {
      return delay(
        mockGraph.edges
          .filter((e) => e.label === "PARTNER_OF")
          .map((e) => ({ from: arg, ...e })),
      );
    }
    return delay([]);
  },

  getInvestigations: async (_limit = 20): Promise<InvestigationSummary[]> =>
    delay(
      mockInvestigations.map(({ log: _log, answer: _answer, ...rest }) => rest),
    ),
  getInvestigation: async (jobId: string) => {
    const inv = findMockInvestigation(jobId);
    if (!inv) throw new Error("not_found");
    return delay(inv, 300);
  },
  postInvestigationStop: async (_jobId: string) => delay(undefined),

  askStream: (body, handlers) => {
    let cancelled = false;
    const question = body.question.trim();
    const contextNote =
      body.context_item_ids.length || body.context_entity_ids.length
        ? ` בהתבסס על ${body.context_item_ids.length} פריטים ו-${body.context_entity_ids.length} ישויות בהקשר,`
        : "";
    const answer =
      `לגבי "${question}":${contextNote} הממצא המרכזי הוא ש-Elbit Systems ` +
      `מציגה יכולות חדשות בתחום פודי הכיוון [1], בעוד Rafael ממשיכה לפתח את ` +
      `מערך העוקבים האלקטרו-אופטיים למערכות היירוט [2]. מומלץ להמשיך מעקב ` +
      `אחר התפתחויות נוספות בטווח השבועיים הקרובים [3].`;
    const citations: AskCitation[] = [
      { n: 1, item_id: 1, title: items[0]?.title ?? "מקור 1", url: items[0]?.url ?? "#" },
      { n: 2, item_id: 2, title: items[1]?.title ?? "מקור 2", url: items[1]?.url ?? "#" },
      { n: 3, item_id: 3, title: items[2]?.title ?? "מקור 3", url: items[2]?.url ?? "#" },
    ];
    const words = answer.split(" ");
    let i = 0;
    const tick = () => {
      if (cancelled) return;
      if (i >= words.length) {
        handlers.onCitations(citations);
        handlers.onDone();
        return;
      }
      handlers.onToken((i === 0 ? "" : " ") + words[i]);
      i += 1;
      setTimeout(tick, 35);
    };
    setTimeout(tick, 150);
    return () => {
      cancelled = true;
    };
  },

  getConferences: async (_from?: string, _to?: string): Promise<Conference[]> => delay([]),
  getConferencesIcalUrl: () => "/api/conferences/ical",

  getTenders: async (query: TendersQuery): Promise<TenderCard[]> => {
    let filtered = mockTenders.slice();
    if (query.status) filtered = filtered.filter((t) => t.status === query.status);
    if (query.country) filtered = filtered.filter((t) => t.country === query.country);
    if (query.q) {
      const q = query.q.toLowerCase();
      filtered = filtered.filter(
        (t) =>
          (t.title ?? "").toLowerCase().includes(q) ||
          (t.summary_he ?? "").toLowerCase().includes(q) ||
          (t.agency ?? "").toLowerCase().includes(q) ||
          t.matched_terms.some((m) => m.toLowerCase().includes(q)),
      );
    }
    return delay(filtered.slice(0, query.limit ?? 100));
  },
  getTenderForecasts: async (limit = 100): Promise<ForecastCard[]> =>
    delay(mockForecasts.slice(0, limit)),

  getClarifications: async (open = true) =>
    delay(clarifications.filter((c) => (open ? c.answer === null : true))),
  postClarificationAnswer: async (id, answer) => {
    const c = clarifications.find((x) => x.id === id);
    if (c) c.answer = answer;
    return delay(undefined);
  },
  getLatestSurvey: async (): Promise<Survey> => delay(mockSurvey),
  postSurveyAnswers: async (_id, _answers) => delay(undefined),
  getLessons: async () => delay(lessons.slice()),
  postLesson: async (kind, text) => {
    const lesson: Lesson = {
      id: lessonId++,
      kind,
      text,
      active: true,
      created_at: new Date().toISOString(),
    };
    lessons.unshift(lesson);
    return delay(lesson);
  },
  deleteLesson: async (id) => {
    const idx = lessons.findIndex((l) => l.id === id);
    if (idx >= 0) lessons.splice(idx, 1);
    return delay(undefined);
  },

  getJobs: async (state?: string, limit = 50) =>
    delay(
      jobs
        .filter((j) => (state ? j.state === state : true))
        .slice(0, limit),
    ),
  postRun: async (scope, mode) => {
    const id = Math.floor(Math.random() * 9000) + 4000;
    const job: Job = {
      id,
      kind: scope === "daily" ? "daily_run" : scope === "weekly" ? "weekly_run" : scope,
      payload: { mode },
      state: "queued" as JobState,
      priority: 5,
      attempts: 0,
      not_before: null,
      started_at: null,
      finished_at: null,
      error: null,
      result: null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    jobs.unshift(job);
    return delay({ job_id: String(id) }, 300);
  },
  postJobCancel: async (id) => {
    const job = jobs.find((j) => j.id === id);
    if (job) {
      job.state = "failed" as JobState;
      job.error = "cancelled_by_user";
    }
    return delay(undefined);
  },

  getReports: async (kind?: string, _limit = 30) =>
    delay(
      kind && kind !== mockReport.kind
        ? []
        : [
            {
              id: mockReport.id,
              kind: mockReport.kind,
              period_start: mockReport.period_start,
              period_end: mockReport.period_end,
              path_docx: mockReport.path_docx,
              path_md: mockReport.path_md,
              path_html: mockReport.path_html,
              qa_passed: mockReport.qa_passed,
              created_at: mockReport.created_at,
              headline_count: mockReport.headline_count,
            },
          ],
    ),
  getReport: async (id: number): Promise<ReportDetail> => {
    if (id !== mockReport.id) throw new Error("not_found");
    return delay(mockReport);
  },
  getReportFileUrl: (id, fmt) => `#mock-report-${id}.${fmt}`,

  getSettings: async (name: SettingsName): Promise<SettingsGetResponse> =>
    delay({ yaml: settingsStore[name] ?? "" }),
  putSettings: async (name: SettingsName, yaml: string): Promise<SettingsPutResponse> => {
    if (!yaml.trim()) {
      return delay({ ok: false, errors: ["הקובץ ריק — נדרש תוכן YAML תקין"] }, 300);
    }
    settingsStore[name] = yaml;
    return delay({ ok: true, errors: [] }, 300);
  },
};

export type { TriageLevel };
