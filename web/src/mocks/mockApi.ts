import type {
  AskCitation,
  Conference,
  EntityDetail,
  EntitySummary,
  EventRow,
  ForecastCard,
  GraphResponse,
  InvestigationSummary,
  ItemCard,
  ItemDetail,
  Job,
  JobState,
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
  RunsCurrentResponse,
  SettingsGetResponse,
  SettingsName,
  SettingsPutResponse,
  Survey,
  TenderCard,
  TriageLevel,
} from "@/types/api";
import type { ApiClient, EntitiesQuery, GraphQuery, ItemsQuery, TendersQuery } from "@/api/types";
import type { CountryGroup } from "@/types/api";
import { normalizeCountryCode } from "@/lib/countries";
import { mockEntities, type MockEntitySeed } from "./data/entities";
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

// U7a/U7c mock-mode mirror of `eoa.report.geography.items_by_country`.
function groupItemsByCountry(rows: ItemCard[]): CountryGroup[] {
  const buckets = new Map<string, CountryGroup>();
  for (const it of rows) {
    const code = normalizeCountryCode(it.geography);
    const bucket = buckets.get(code) ?? { country: code, total: 0, red: 0, orange: 0, yellow: 0, archive: 0 };
    bucket.total += 1;
    if (it.level === "red" || it.level === "orange" || it.level === "yellow" || it.level === "archive") {
      bucket[it.level] += 1;
    }
    buckets.set(code, bucket);
  }
  return [...buckets.values()].sort((a, b) => b.total - a.total);
}

// Mutable in-memory copies so feedback / answers / lessons persist for the
// lifetime of the tab.
const items = mockItems.map((it) => ({ ...it }));
const clarifications = mockClarifications.map((c) => ({ ...c }));
const lessons = [...mockLessons];
const jobs = [...mockJobs];
const settingsStore = { ...mockSettingsYaml };
// U8: mock-only in-memory mirror of `llm_providers.{interactive_default,allow_cloud,mode,chains}`.
const llmSettingsStore: {
  interactive_default: string;
  allow_cloud: boolean;
  mode: "local" | "cloud";
  chains: Record<string, LlmChainEntry[]>;
} = {
  interactive_default: "ollama",
  allow_cloud: true,
  mode: "local",
  chains: {},
};
let lessonId = lessons.length + 1;
let investigateJobCounter = 9000;

// U8-ה, ChainsEditor: mirrors the real `services._validate_chains`/`_with_terminal_ollama`
// business rules closely enough for the mock backend to reject/normalize the same way, so e2e
// tests exercising the editor against the mock behave like the real API.
const MOCK_CHAIN_POWER_LEVELS: Record<string, string[]> = {
  ollama: [],
  agy: ["low", "medium", "high"],
  claude: ["low", "medium", "high"],
  codex: ["low", "medium", "high"],
  anthropic: ["low", "medium", "high"],
  gemini: ["low", "medium", "high"],
  openai: ["low", "medium", "high"],
};
const MOCK_CHAIN_ROLES = new Set(["resident", "investigator", "light", "report"]);

function mockWithTerminalOllama(chain: LlmChainEntry[]): LlmChainEntry[] {
  const last = chain[chain.length - 1];
  return last && last.provider === "ollama" ? chain : [...chain, { provider: "ollama" }];
}

function mockValidateChains(chains: Record<string, LlmChainEntry[]>): string[] {
  const errors: string[] = [];
  for (const [role, chain] of Object.entries(chains)) {
    if (!MOCK_CHAIN_ROLES.has(role)) {
      errors.push(`תפקיד לא ידוע בשרשרת: ${role}`);
      continue;
    }
    chain.forEach((entry, i) => {
      const levels = MOCK_CHAIN_POWER_LEVELS[entry.provider];
      if (levels === undefined) {
        errors.push(`${role}[${i}]: ספק לא ידוע: ${entry.provider}`);
        return;
      }
      if (entry.provider !== "ollama" && !(entry.model && entry.model.trim())) {
        errors.push(`${role}[${i}]: יש לבחור מודל עבור ספק ${entry.provider}`);
      }
      if (entry.power && !levels.includes(entry.power)) {
        errors.push(`${role}[${i}]: רמת עוצמה לא נתמכת עבור ${entry.provider}: ${entry.power}`);
      }
    });
  }
  return errors;
}

// U10/F15 (2026-09-05): deterministic, seed-derived relevance/watchlist/mention-count
// fields so `mocks/data/entities.ts` doesn't need every fixture row hand-edited every
// time an EntitySummary field is added (see MockEntitySeed's docstring there). Every
// mock entity is a "known player" fixture, so all score at/above the 0.4 threshold.
function toEntitySummary(seed: MockEntitySeed): EntitySummary {
  return {
    ...seed,
    relevance: seed.kind === "government" ? 0.75 : Math.min(1, 0.5 + seed.item_count / 20),
    is_watchlist: seed.item_count >= 5,
    mentions_7d: Math.min(seed.item_count, Math.round(seed.item_count * 0.6)),
    mentions_30d: seed.item_count,
  };
}

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
      recent_errors: [
        {
          id: 1,
          job_id: mockJobs[0]?.id ?? null,
          stage: "ingest",
          message: "FetchError: timeout מול 2/40 מקורות RSS",
          at: "2026-09-04T01:12:00+03:00",
        },
      ],
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
    if (query.country?.length) {
      const wanted = new Set(query.country.map((c) => c.toUpperCase()));
      filtered = filtered.filter((it) => wanted.has(normalizeCountryCode(it.geography)));
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
      groups: query.group_by === "country" ? groupItemsByCountry(filtered) : undefined,
    });
  },

  getItemsByCountry: async (query) => {
    let filtered = items.slice();
    if (query.level?.length) filtered = filtered.filter((it) => query.level!.includes(it.level));
    if (query.domain) filtered = filtered.filter((it) => it.domain === query.domain);
    if (query.since) {
      const since = new Date(query.since).getTime();
      filtered = filtered.filter((it) => new Date(it.published_at).getTime() >= since);
    }
    return delay({ countries: groupItemsByCountry(filtered) });
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
    if (query.country) filtered = filtered.filter((e) => e.country === query.country);
    let withRelevance = filtered.map(toEntitySummary);
    if (query.watchlist) withRelevance = withRelevance.filter((e) => e.is_watchlist);
    if (!query.all) withRelevance = withRelevance.filter((e) => e.relevance >= 0.4);
    const sort = query.sort ?? "last_seen";
    withRelevance.sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name);
      if (sort === "mentions_7d") return b.mentions_7d - a.mentions_7d;
      if (sort === "mentions_30d") return b.mentions_30d - a.mentions_30d;
      return new Date(b.last_seen ?? 0).getTime() - new Date(a.last_seen ?? 0).getTime();
    });
    return delay(withRelevance.slice(0, query.limit ?? 50));
  },

  getEntity: async (id: number): Promise<EntityDetail> => {
    const entity = mockEntities.find((e) => e.id === id);
    if (!entity) throw new Error("not_found");
    const summary = toEntitySummary(entity);
    const relatedItems = items.filter((it) => it.entities_mentioned.includes(entity.name));
    const businessEvents = itemToEvents(relatedItems[0] ?? items[0]);
    const edgeGroups = new Map<string, { entity_id: number; entity_name: string }[]>();
    for (const e of mockGraph.edges) {
      if (e.src !== id && e.dst !== id) continue;
      const otherId = e.src === id ? e.dst : e.src;
      const other = mockGraph.nodes.find((n) => n.id === otherId);
      const list = edgeGroups.get(e.label) ?? [];
      list.push({ entity_id: otherId, entity_name: other?.name ?? `ישות #${otherId}` });
      edgeGroups.set(e.label, list);
    }
    const levelCounts: Record<string, number> = {};
    for (const it of relatedItems) levelCounts[it.level] = (levelCounts[it.level] ?? 0) + 1;
    return delay({
      ...summary,
      timeline: relatedItems.map((it) => ({
        item_id: it.id,
        title: it.title,
        url: it.url,
        source_name: it.source_name,
        published_at: it.published_at,
        level: it.level,
      })),
      business_events: businessEvents.map((ev) => ({
        id: ev.id,
        item_id: ev.item_id,
        kind: ev.kind,
        date: ev.occurred_at,
        amount_usd: null,
        currency: null,
        counterpart: null,
        summary_he: ev.summary_he,
      })),
      kpis: {
        mentions_7d: summary.mentions_7d,
        mentions_30d: summary.mentions_30d,
        events_count: businessEvents.length,
        related_items_by_level: levelCounts,
      },
      edge_groups: Array.from(edgeGroups.entries()).map(([label, counterparts]) => ({
        label,
        counterparts,
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
  postInvestigationNew: async (_body) => {
    investigateJobCounter += 1;
    return delay({ job_id: `inv-${investigateJobCounter}` }, 300);
  },
  postInvestigationExpand: async (_jobId: string) => {
    investigateJobCounter += 1;
    return delay({ job_id: `inv-${investigateJobCounter}` }, 300);
  },

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
    const [kind, model] = (body.provider ?? "ollama").split(":");
    const mockModel = model || (kind === "ollama" ? "DictaLM 3 12B" : "default");
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
    handlers.onMeta?.(kind || "ollama", mockModel);
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
  getRunsCurrent: async (): Promise<RunsCurrentResponse> =>
    delay({
      current: {
        job_id: 8842,
        kind: "deep_search",
        state: "running",
        current_stage: null,
        stages: [],
        started_at: "2026-09-04T09:05:02+03:00",
        elapsed_min: 4.2,
        eta_min: null,
      },
      other_running: [],
    }),

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
  getReportCitations: async (id: number): Promise<ReportCitationsResponse> =>
    delay({
      report_id: id,
      citations: Object.fromEntries(
        mockReport.items_included.map((itemId, i) => {
          const item = findMockItem(itemId);
          return [
            String(i + 1),
            { item_id: itemId, url: item?.url ?? null, title: item?.title ?? null },
          ];
        }),
      ),
    }),

  getSettings: async (name: SettingsName): Promise<SettingsGetResponse> =>
    delay({ yaml: settingsStore[name] ?? "" }),
  putSettings: async (name: SettingsName, yaml: string): Promise<SettingsPutResponse> => {
    if (!yaml.trim()) {
      return delay({ ok: false, errors: ["הקובץ ריק — נדרש תוכן YAML תקין"] }, 300);
    }
    settingsStore[name] = yaml;
    return delay({ ok: true, errors: [] }, 300);
  },

  getLlmProviders: async (): Promise<LlmProvidersResponse> =>
    delay({
      mode: llmSettingsStore.mode,
      allow_cloud: llmSettingsStore.allow_cloud,
      interactive_default: llmSettingsStore.interactive_default,
      chains: llmSettingsStore.chains,
      providers: [
        { id: "ollama", label: "מקומי (Ollama)", kind: "local", available: true, models: ["resident", "light"] },
        {
          id: "agy",
          label: "Gemini (Antigravity CLI)",
          kind: "cloud",
          available: llmSettingsStore.allow_cloud,
          models: ["gemini-3.8-flash-medium", "gemini-3.1-pro-high"],
        },
        {
          id: "claude",
          label: "Claude (Claude Code CLI)",
          kind: "cloud",
          available: llmSettingsStore.allow_cloud,
          models: ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
        },
        {
          id: "codex",
          label: "Codex (Codex CLI)",
          kind: "cloud",
          available: llmSettingsStore.allow_cloud,
          models: ["default"],
        },
        // U8-ו (Revision 2026-09-06): direct-API providers — never available in the mock (no
        // real key), matching the real backend's "מוגדר / לא מוגדר" contract.
        {
          id: "anthropic",
          label: "Anthropic (API)",
          kind: "api",
          available: false,
          models: ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
          key_env: "ANTHROPIC_API_KEY",
          power_levels: ["low", "medium", "high"],
        },
        {
          id: "gemini",
          label: "Gemini (API)",
          kind: "api",
          available: false,
          models: ["gemini-3.1-pro-preview", "gemini-3.5-flash", "gemini-3.1-flash-lite"],
          key_env: "GEMINI_API_KEY",
          power_levels: ["low", "medium", "high"],
        },
        {
          id: "openai",
          label: "OpenAI (API)",
          kind: "api",
          available: false,
          models: ["gpt-5.1", "gpt-5.1-mini"],
          key_env: "OPENAI_API_KEY",
          power_levels: ["low", "medium", "high"],
        },
      ],
    }),
  getLlmCalls: async (): Promise<LlmCallsSummary> =>
    delay({
      since_hours: 24,
      providers: [],
      totals: {
        calls: 0,
        failures: 0,
        fallbacks: 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        est_cost_usd: 0,
        cloud_calls: 0,
      },
    }),
  putLlmSettings: async (body): Promise<LlmSettingsPutResponse> => {
    if (body.chains !== undefined) {
      const errors = mockValidateChains(body.chains);
      if (errors.length > 0) return delay({ ok: false, errors, revision: null }, 200);
    }
    if (body.interactive_default !== undefined) llmSettingsStore.interactive_default = body.interactive_default;
    if (body.allow_cloud !== undefined) llmSettingsStore.allow_cloud = body.allow_cloud;
    if (body.mode !== undefined) llmSettingsStore.mode = body.mode;
    if (body.chains !== undefined) {
      llmSettingsStore.chains = Object.fromEntries(
        Object.entries(body.chains).map(([role, chain]) => [role, mockWithTerminalOllama(chain)]),
      );
    }
    return delay({ ok: true, errors: [], revision: String(Date.now()) }, 200);
  },

  getMcpServers: async (): Promise<McpServersResponse> =>
    delay({
      mcp_enabled: false,
      servers: [
        {
          id: "procurement",
          label: "רכש והתקשרויות (SAM.gov / USAspending / DSCA / Federal Register / Congress.gov)",
          transport: "stdio",
          enabled: true,
          inherit_cli_only: false,
          key_configured: false,
          key_env: ["SAM_GOV_API_KEY", "CONGRESS_GOV_API_KEY"],
          tool_count: null,
          ok: null,
          error: null,
          latency_ms: null,
          tools: [],
        },
        {
          id: "janes",
          label: "Janes Data Services (equipment / news / markets / budgets / events)",
          transport: "stdio",
          enabled: true,
          inherit_cli_only: false,
          key_configured: false,
          key_env: ["JANES_API_KEY", "JANES_API_BASE"],
          tool_count: null,
          ok: null,
          error: null,
          latency_ms: null,
          tools: [],
        },
        {
          id: "patents",
          label: "פטנטים (EPO OPS / USPTO PatentsView)",
          transport: "stdio",
          enabled: true,
          inherit_cli_only: false,
          key_configured: false,
          key_env: ["EPO_OPS_KEY", "EPO_OPS_SECRET", "PATENTSVIEW_API_KEY"],
          tool_count: null,
          ok: null,
          error: null,
          latency_ms: null,
          tools: [],
        },
        {
          id: "financial_data",
          label: "נתונים פיננסיים (FMP) — מחובר בסשן Claude של המשתמש",
          transport: "http",
          enabled: false,
          inherit_cli_only: true,
          key_configured: null,
          key_env: null,
          tool_count: null,
          ok: null,
          error: null,
          latency_ms: null,
          tools: [],
        },
        {
          id: "academic_research",
          label: "מחקר אקדמי (Undermind) — מחובר בסשן Claude של המשתמש",
          transport: "http",
          enabled: false,
          inherit_cli_only: true,
          key_configured: null,
          key_env: null,
          tool_count: null,
          ok: null,
          error: null,
          latency_ms: null,
          tools: [],
        },
      ],
    }),
  postMcpServerPing: async (serverId: string): Promise<McpPingResponse> =>
    delay(
      {
        id: serverId,
        ok: serverId !== "financial_data" && serverId !== "academic_research",
        error:
          serverId === "financial_data" || serverId === "academic_research"
            ? "server is inherit_cli_only -- reachable only via a cloud CLI's own MCP config, not directly"
            : null,
        tool_count: serverId === "procurement" ? 6 : serverId === "janes" ? 7 : serverId === "patents" ? 3 : 0,
        tools: [],
        latency_ms: 120,
      },
      400,
    ),
  getMcpCalls: async (): Promise<McpCallsResponse> =>
    delay({ since_hours: 24, calls: [], totals: { calls: 0, failures: 0, flagged: 0 } }),
};

export type { TriageLevel };
