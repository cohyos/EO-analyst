import type {
  AskRequest,
  AskSseEvent,
  BdReportCreateResponse,
  BdTerritoryOption,
  Clarification,
  Conference,
  Corroboration,
  DossierCompetitorRow,
  DossierCreateBody,
  DossierCreateResponse,
  DossierDealRow,
  DossierDetail,
  DossierIdentity,
  DossierMaturity,
  DossierPatentRef,
  DossierPartnerRow,
  DossierPendingJob,
  DossierPerformanceRow,
  DossierProgressTopic,
  DossierPriceRow,
  DossierRegulatoryExport,
  DossierRerunResponse,
  DossierRunDetail,
  DossierRunRef,
  DossierSentence,
  DossierSource,
  DossierSpecRow,
  DossierSummary,
  DossierTenderRef,
  DossierVersionRow,
  EntityDetail,
  EntityDetailFull,
  EntitySummary,
  ForecastCard,
  GraphEdgeAgg,
  GraphNodeStats,
  GraphOverviewResponse,
  GraphPathResponse,
  GraphResponse,
  GraphSearchResult,
  InvestigationDetail,
  InvestigationProvenance,
  InvestigationSummary,
  ItemCard,
  ItemDetail,
  ItemInvestigationRef,
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
  PayloadDetailResponse,
  PayloadDiffResponse,
  PayloadRecord,
  PayloadTreeResponse,
  ProductDossierOut,
  ProductLine,
  ProductLineDetail,
  ProductLineReportCreateResponse,
  ReportCitationsResponse,
  ReportDetail,
  ReportInvestigationRef,
  ReportSummary,
  RunsCurrentResponse,
  SecurityReviewCard,
  SettingsGetResponse,
  SettingsName,
  SettingsPutResponse,
  Survey,
  TechRadarResponse,
  TenderCard,
  TenderFeedback,
  TenderFeedbackVerdict,
  TenderSourceCoverageResponse,
  TenderStatus,
  TriageLevel,
} from "@/types/api";
import type {
  ApiClient,
  EntitiesQuery,
  GraphQuery,
  ItemsQuery,
  NeighborhoodQuery,
  PatentsQuery,
  PayloadsQuery,
  TechItemsQuery,
  TendersQuery,
} from "./types";
import {
  arr,
  bool,
  idStr,
  normalizeCorroboration,
  normalizeInvestigationLogLine,
  normalizeNightSummary,
  normalizeReportCitations,
  normalizeRunsCurrent,
  normalizeSecurityReviewCard,
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

// --- Remote-access session gate (ADR-008, docs/adr/008-remote-access.md) ---------------------
// `agent/eoa/api/auth.py`'s middleware returns 401 `{"error":{"code":"auth_required",...}}` for
// any non-loopback client (Tailscale/LAN) without a valid session cookie. No passcode is ever
// stored client-side -- the session lives entirely in an HttpOnly cookie the browser manages; the
// two tiny pub/sub flags below just let `AccessGate`/`TopBar` react to what the API just told us.
type BoolListener = (value: boolean) => void;

let authRequired = false;
let remoteSessionActive = false;
const authRequiredListeners = new Set<BoolListener>();
const remoteSessionListeners = new Set<BoolListener>();

function setAuthRequired(value: boolean): void {
  if (authRequired === value) return;
  authRequired = value;
  for (const listener of authRequiredListeners) listener(value);
}

function setRemoteSessionActive(value: boolean): void {
  if (remoteSessionActive === value) return;
  remoteSessionActive = value;
  for (const listener of remoteSessionListeners) listener(value);
}

/** Subscribe to the "a 401 auth_required just happened" flag. Returns an unsubscribe function. */
export function subscribeAuthRequired(listener: BoolListener): () => void {
  authRequiredListeners.add(listener);
  return () => authRequiredListeners.delete(listener);
}

export function getAuthRequired(): boolean {
  return authRequired;
}

/** Subscribe to "the last response was an authenticated remote session" (set from the
 * `X-EOA-Remote-Session` response header -- see `auth.py::RemoteAccessMiddleware`). */
export function subscribeRemoteSession(listener: BoolListener): () => void {
  remoteSessionListeners.add(listener);
  return () => remoteSessionListeners.delete(listener);
}

export function getRemoteSessionActive(): boolean {
  return remoteSessionActive;
}

/** `POST /api/auth/login` -- on success the server sets the HttpOnly session cookie itself. */
export async function loginRemoteAccess(passcode: string): Promise<void> {
  await request<{ ok: boolean }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ passcode }),
  });
  setAuthRequired(false);
}

export async function logoutRemoteAccess(): Promise<void> {
  try {
    await request<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
  } finally {
    setRemoteSessionActive(false);
  }
}

// W13 (docs/REVIEW_2026-09-06_evening.md round 4): "טעינת הדוחות איטית... הרבה שעוני חול" -- a
// request that never resolves (a hung backend, a dead connection) used to leave every page's
// LoadingState spinning forever, with no way out but a manual reload. Every call through `request`
// now aborts after `timeoutMs` (default 10s) and surfaces a dedicated `ApiError("timeout", ...)` so
// `ErrorState` can show "השרת לא הגיב, נסה שוב" with a working retry button instead of an endless
// spinner. A few endpoints are legitimately synchronous and slow (e.g. `postBdReport`/
// `createPatentSurvey` build a report inline for up to ~55s before falling back to a job id) --
// those pass an explicit longer `timeoutMs`.
const DEFAULT_TIMEOUT_MS = 10_000;

async function request<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...fetchInit } = init ?? {};
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res: Response;
  try {
    res = await fetch(path, {
      ...fetchInit,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...(fetchInit.headers ?? {}),
      },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("timeout", "השרת לא הגיב, נסה שוב", null);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
  setRemoteSessionActive(res.headers.get("x-eoa-remote-session") === "1");
  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      // ignore parse failure, fall through to generic error
    }
    const err = body as {
      error?: { code: string; message_he: string; detail: unknown };
    } | null;
    if (err?.error) {
      if (err.error.code === "auth_required") setAuthRequired(true);
      throw new ApiError(err.error.code, err.error.message_he, err.error.detail);
    }
    throw new ApiError("http_error", `שגיאת שרת (${res.status})`, res.statusText);
  }
  setAuthRequired(false);
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
    // CORR: absent on any backend build that predates this feature -- normalizes to "unknown".
    corroboration: normalizeCorroboration(r.corroboration),
    // PL-ui (2026-09-07): absent on any backend build that predates this feature -- normalizes to
    // "no product lines tagged" rather than undefined, so `FeedFilters`' client-side filter can
    // read `item.product_lines` unconditionally.
    product_lines: arr(r.product_lines),
  };
}

function normalizeTechRadar(
  raw: Partial<TechRadarResponse> | null | undefined,
): TechRadarResponse {
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

function normalizeEntitySummary(
  raw: Partial<EntitySummary> | null | undefined,
): EntitySummary {
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

function normalizeEntityDetail(
  raw: Partial<EntityDetail> | null | undefined,
): EntityDetail {
  const r = raw ?? {};
  return {
    ...normalizeEntitySummary(r),
    timeline: arr(r.timeline),
    business_events: arr(r.business_events),
    kpis: r.kpis ?? {
      mentions_7d: 0,
      mentions_30d: 0,
      events_count: 0,
      related_items_by_level: {},
    },
    edge_groups: arr(r.edge_groups),
    neighbors: arr(r.neighbors),
  };
}

function normalizeGraph(raw: Partial<GraphResponse> | null | undefined): GraphResponse {
  const r = raw ?? {};
  return { nodes: arr(r.nodes), edges: arr(r.edges) };
}

// R10-graph normalizers -------------------------------------------------

function normalizeGraphNodeStats(
  raw: Partial<GraphNodeStats> | null | undefined,
): GraphNodeStats {
  const r = raw ?? {};
  return {
    id: num(r.id),
    name: str(r.name),
    kind: str(r.kind),
    country: r.country ?? null,
    mention_count: num(r.mention_count),
    last_seen: r.last_seen ?? null,
    corroboration: {
      corroborated: num(r.corroboration?.corroborated),
      official_primary: num(r.corroboration?.official_primary),
      single_source: num(r.corroboration?.single_source),
      unknown: num(r.corroboration?.unknown),
    },
    product_lines: arr(r.product_lines),
  };
}

function normalizeGraphEdgeAgg(
  raw: Partial<GraphEdgeAgg> | null | undefined,
): GraphEdgeAgg {
  const r = raw ?? {};
  return {
    src: num(r.src),
    dst: num(r.dst),
    relation: str(r.relation),
    weight: num(r.weight, 1),
    first_seen: r.first_seen ?? null,
    last_seen: r.last_seen ?? null,
    evidence: arr(r.evidence).map((e) => ({
      item_id: num(e.item_id),
      title: e.title ?? null,
      published_at: e.published_at ?? null,
    })),
  };
}

function normalizeNeighborhood(
  raw:
    | Partial<{
        nodes: unknown[];
        edges: unknown[];
        center_id: number;
        truncated: boolean;
      }>
    | null
    | undefined,
) {
  const r = raw ?? {};
  return {
    nodes: arr(r.nodes as Partial<GraphNodeStats>[]).map(normalizeGraphNodeStats),
    edges: arr(r.edges as Partial<GraphEdgeAgg>[]).map(normalizeGraphEdgeAgg),
    center_id: num(r.center_id),
    truncated: bool(r.truncated),
  };
}

function normalizeGraphOverview(
  raw: Partial<GraphOverviewResponse> | null | undefined,
): GraphOverviewResponse {
  const r = raw ?? {};
  return {
    nodes: arr(r.nodes).map(normalizeGraphNodeStats),
    edges: arr(r.edges).map(normalizeGraphEdgeAgg),
  };
}

function normalizeGraphPath(
  raw: Partial<GraphPathResponse> | null | undefined,
): GraphPathResponse {
  const r = raw ?? {};
  return {
    nodes: arr(r.nodes).map((n) => ({
      id: num(n.id),
      name: str(n.name),
      kind: str(n.kind),
      country: n.country ?? null,
    })),
    edges: arr(r.edges).map(normalizeGraphEdgeAgg),
    hops: num(r.hops),
  };
}

function normalizeEntityDetailFull(
  raw: Partial<EntityDetailFull> | null | undefined,
): EntityDetailFull {
  const r = raw ?? {};
  return {
    ...normalizeEntityDetail(r),
    investigations: arr(r.investigations).map((i) => ({
      job_id: i.job_id ?? "",
      state: str(i.state),
      question: i.question ?? null,
      started_at: i.started_at ?? null,
      finished_at: i.finished_at ?? null,
    })),
    reports: arr(r.reports).map((rp) => ({
      id: num(rp.id),
      kind: str(rp.kind),
      period_start: rp.period_start ?? null,
      period_end: rp.period_end ?? null,
      created_at: rp.created_at ?? null,
    })),
  };
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

// R10-links: `GET /api/investigations/{id}`'s nested `provenance` object -- trigger item /
// rerun-expansion lineage / citing reports, additive and possibly absent on an older backend.
function normalizeInvestigationProvenance(
  raw: Partial<InvestigationProvenance> | null | undefined,
): InvestigationProvenance | null {
  if (!raw) return null;
  const job: Partial<InvestigationProvenance["job"]> = raw.job ?? {};
  return {
    job: {
      job_id: idStr(job.job_id),
      state: job.state ?? null,
      question: job.question ?? null,
      started_at: job.started_at ?? null,
      finished_at: job.finished_at ?? null,
    },
    trigger_item: raw.trigger_item
      ? {
          id: num(raw.trigger_item.id),
          title: raw.trigger_item.title ?? null,
          url: raw.trigger_item.url ?? null,
          source_name: raw.trigger_item.source_name ?? null,
          published_at: raw.trigger_item.published_at ?? null,
        }
      : null,
    lineage: arr(raw.lineage).map((l) => ({
      job_id: idStr(l.job_id),
      outcome: l.outcome ?? null,
      confidence: typeof l.confidence === "number" ? l.confidence : null,
      finished_at: l.finished_at ?? null,
      kind: l.kind === "rerun" || l.kind === "expansion" ? l.kind : "original",
    })),
    reports: arr(raw.reports).map((rp) => ({
      id: num(rp.id),
      kind: rp.kind ?? null,
      period_end: rp.period_end ?? null,
      territory: rp.territory ?? null,
      title_he: str(rp.title_he),
      path_html: rp.path_html ?? null,
    })),
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
          // Round 12 follow-up: these were declared on InvestigationOut and read by
          // InvestigationDetailPage but never survived normalisation (the page tests bypassed it).
          security_review: r.answer.security_review ?? undefined,
          security_review_reason_he: r.answer.security_review_reason_he ?? undefined,
          security_review_snippet: r.answer.security_review_snippet ?? undefined,
          security_review_resolved: r.answer.security_review_resolved ?? undefined,
          blocked_reason_he: r.answer.blocked_reason_he ?? undefined,
          confidence: typeof r.answer.confidence === "number" ? r.answer.confidence : null,
        }
      : null,
    provenance: normalizeInvestigationProvenance(r.provenance),
  };
}

// R10-links: `GET /api/items/{id}/investigations`.
function normalizeItemInvestigationRef(
  raw: Partial<ItemInvestigationRef> | null | undefined,
): ItemInvestigationRef {
  const r = raw ?? {};
  return {
    job_id: idStr(r.job_id),
    question: r.question ?? null,
    state: r.state ?? "not_found",
    error: r.error ?? null,
    outcome: r.outcome ?? null,
    confidence: typeof r.confidence === "number" ? r.confidence : null,
    started_at: r.started_at ?? null,
    finished_at: r.finished_at ?? null,
    rerun_of_job_id: r.rerun_of_job_id != null ? idStr(r.rerun_of_job_id) : null,
    expanded_from_job_id:
      r.expanded_from_job_id != null ? idStr(r.expanded_from_job_id) : null,
  };
}

// R10-links: `GET /api/reports/{id}/investigations`.
function normalizeReportInvestigationRef(
  raw: Partial<ReportInvestigationRef> | null | undefined,
): ReportInvestigationRef {
  const r = raw ?? {};
  return {
    job_id: idStr(r.job_id),
    item_id: r.item_id ?? null,
    trigger_title: r.trigger_title ?? null,
    question: r.question ?? null,
    outcome: r.outcome ?? null,
    confidence: typeof r.confidence === "number" ? r.confidence : null,
    rerun_of_job_id: r.rerun_of_job_id != null ? idStr(r.rerun_of_job_id) : null,
  };
}

function normalizeReportSummary(
  raw: Partial<ReportSummary> | null | undefined,
): ReportSummary {
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
    // W14: additive title/preview/grouping fields -- see the ReportSummary type doc.
    title_he: str(r.title_he) || `${str(r.kind)} — ${str(r.period_end)}`,
    subject_he: r.subject_he ?? null,
    built_at: str(r.built_at) || str(r.created_at),
    preview_he: r.preview_he ?? null,
    source_count: num(r.source_count),
    qa_issues: num(r.qa_issues),
    group_key: str(r.group_key) || `${str(r.kind)}:${r.id ?? ""}`,
    is_latest: bool(r.is_latest, true),
  };
}

function normalizeReportDetail(
  raw: Partial<ReportDetail> | null | undefined,
): ReportDetail {
  const r = raw ?? {};
  return {
    ...normalizeReportSummary(r),
    html: str(r.html),
    open_points: arr(r.open_points),
    items_included: arr(r.items_included),
  };
}

// PL-ui (2026-09-07): mirrors the frozen contract in docs/qa/loop/round_7_fixes.md "### PL-ui
// status" -- a backend build that predates this feature (or omits a field) normalizes to empty
// arrays/zero stats/no report, never throws, so the page can render its empty states instead of
// an error screen.
function normalizeProductLine(raw: Partial<ProductLine> | null | undefined): ProductLine {
  const r = raw ?? {};
  const s: Partial<ProductLine["stats"]> = r.stats ?? {};
  return {
    id: str(r.id),
    name_he: str(r.name_he),
    name_en: str(r.name_en),
    subdomains: arr(r.subdomains),
    exemplar_systems: arr(r.exemplar_systems),
    competitors: arr(r.competitors),
    stats: {
      items_7d: num(s.items_7d),
      items_30d: num(s.items_30d),
      events_30d: num(s.events_30d),
      open_tenders: num(s.open_tenders),
      forecasts: num(s.forecasts),
      patents_90d: num(s.patents_90d),
      active_competitors: num(s.active_competitors),
    },
    latest_report: r.latest_report
      ? {
          id: num(r.latest_report.id),
          created_at: str(r.latest_report.created_at),
          qa_passed: bool(r.latest_report.qa_passed),
          path_html: str(r.latest_report.path_html),
        }
      : null,
  };
}

function normalizeProductLineDetail(
  raw: Partial<ProductLineDetail> | null | undefined,
): ProductLineDetail {
  const r = raw ?? {};
  return {
    ...normalizeProductLine(r),
    recent_items: arr(r.recent_items).map(normalizeItemCard),
    open_tenders: arr(r.open_tenders).map(normalizeTenderCard),
    reports: arr(r.reports).map(normalizeReportSummary),
  };
}

// PD-ui (docs/PLAN_PRODUCT_DOSSIER.md): "סקירות מוצר" -- mirrors the frozen contract (section 3/5)
// exactly like the PL-ui normalizers above: a backend build that predates this feature, or omits
// a field, normalizes to an empty array / null / "not established" shape and never throws, so the
// page renders its own empty/"לא נמצא במקורות" states instead of an error screen.
function normalizeDossierCites(raw: unknown): number[] {
  return Array.isArray(raw) ? raw.filter((n): n is number => typeof n === "number") : [];
}

function normalizeDossierSentence(raw: Partial<DossierSentence> | null | undefined): DossierSentence {
  const r = raw ?? {};
  return { text_he: str(r.text_he), cites: normalizeDossierCites(r.cites) };
}

function normalizeDossierIdentity(raw: Partial<DossierIdentity> | null | undefined): DossierIdentity {
  const r = raw ?? {};
  return {
    product_name: str(r.product_name),
    vendor: r.vendor ?? null,
    product_family: r.product_family ?? null,
    category_he: r.category_he ?? null,
    first_announced: r.first_announced ?? null,
    status_he: r.status_he ?? null,
    cites: normalizeDossierCites(r.cites),
  };
}

function normalizeDossierSpecRow(raw: Partial<DossierSpecRow> | null | undefined): DossierSpecRow {
  const r = raw ?? {};
  return {
    parameter_he: str(r.parameter_he),
    value: str(r.value),
    unit: r.unit ?? null,
    variant: r.variant ?? null,
    source_kind: r.source_kind ?? null,
    cites: normalizeDossierCites(r.cites),
  };
}

function normalizeDossierVersionRow(raw: Partial<DossierVersionRow> | null | undefined): DossierVersionRow {
  const r = raw ?? {};
  return {
    name: str(r.name),
    year: typeof r.year === "number" ? r.year : null,
    changes_he: str(r.changes_he),
    platforms: arr(r.platforms).map((p) => str(p)),
    cites: normalizeDossierCites(r.cites),
  };
}

function normalizeDossierPerformanceRow(
  raw: Partial<DossierPerformanceRow> | null | undefined,
): DossierPerformanceRow {
  const r = raw ?? {};
  return {
    metric_he: str(r.metric_he),
    claimed_value: r.claimed_value ?? null,
    tested_value: r.tested_value ?? null,
    conditions_he: r.conditions_he ?? null,
    cites: normalizeDossierCites(r.cites),
  };
}

function normalizeDossierMaturity(raw: Partial<DossierMaturity> | null | undefined): DossierMaturity {
  const r = raw ?? {};
  return {
    trl: typeof r.trl === "number" ? r.trl : null,
    operational_users: arr(r.operational_users).map((u) => str(u)),
    platforms_integrated: arr(r.platforms_integrated).map((p) => str(p)),
    first_fielding: r.first_fielding ?? null,
    assessment_he: r.assessment_he ?? null,
    cites: normalizeDossierCites(r.cites),
  };
}

function normalizeDossierDealRow(raw: Partial<DossierDealRow> | null | undefined): DossierDealRow {
  const r = raw ?? {};
  return {
    date: r.date ?? null,
    // PD-fix-2 (2026-09-08, item 3): pass through date_kind/region_he -- without this the deal
    // table can never distinguish a backfilled "published" date from a real deal date, or show a
    // region-only source's region instead of a blank/guessed country.
    date_kind: r.date_kind ?? null,
    customer: r.customer ?? null,
    country: r.country ?? null,
    region_he: r.region_he ?? null,
    kind: str(r.kind),
    amount: r.amount ?? null,
    currency: r.currency ?? null,
    quantity: typeof r.quantity === "number" ? r.quantity : null,
    platform: r.platform ?? null,
    cites: normalizeDossierCites(r.cites),
    confidence: typeof r.confidence === "number" ? r.confidence : null,
  };
}

function normalizeDossierPriceRow(raw: Partial<DossierPriceRow> | null | undefined): DossierPriceRow {
  const r = raw ?? {};
  return {
    figure: str(r.figure),
    currency: r.currency ?? null,
    basis_he: str(r.basis_he),
    date: r.date ?? null,
    source_kind: r.source_kind ?? null,
    cites: normalizeDossierCites(r.cites),
  };
}

function normalizeDossierPartnerRow(raw: Partial<DossierPartnerRow> | null | undefined): DossierPartnerRow {
  const r = raw ?? {};
  return {
    partner: str(r.partner),
    role_he: str(r.role_he),
    since: r.since ?? null,
    cites: normalizeDossierCites(r.cites),
  };
}

function normalizeDossierCompetitorRow(
  raw: Partial<DossierCompetitorRow> | null | undefined,
): DossierCompetitorRow {
  const r = raw ?? {};
  return {
    product: str(r.product),
    vendor: r.vendor ?? null,
    comparison_he: str(r.comparison_he),
    cites: normalizeDossierCites(r.cites),
  };
}

function normalizeDossierRegulatoryExport(
  raw: Partial<DossierRegulatoryExport> | null | undefined,
): DossierRegulatoryExport {
  const r = raw ?? {};
  return {
    export_regime_he: r.export_regime_he ?? null,
    restrictions_he: r.restrictions_he ?? null,
    cites: normalizeDossierCites(r.cites),
  };
}

function normalizeDossierPatentRef(raw: Partial<DossierPatentRef> | null | undefined): DossierPatentRef {
  const r = raw ?? {};
  return {
    pub_number: str(r.pub_number),
    title: r.title ?? null,
    assignee: r.assignee ?? null,
    relevance_he: r.relevance_he ?? null,
    cites: normalizeDossierCites(r.cites),
  };
}

function normalizeDossierTenderRef(raw: Partial<DossierTenderRef> | null | undefined): DossierTenderRef {
  const r = raw ?? {};
  return {
    tender_id: typeof r.tender_id === "number" ? r.tender_id : null,
    title: str(r.title),
    status: r.status ?? null,
    relevance_he: r.relevance_he ?? null,
    cites: normalizeDossierCites(r.cites),
  };
}

function normalizeProductDossierOut(
  raw: Partial<ProductDossierOut> | null | undefined,
): ProductDossierOut {
  const r = raw ?? {};
  return {
    identity: normalizeDossierIdentity(r.identity),
    // The backend uses the plan's `_he` names for the four sentence lists (summary_he,
    // risks_and_gaps_he, what_changed_he, bd_implications_he); accept both spellings so the
    // detail page never shows a placeholder for content that is actually there (first live run).
    summary: arr(
      ((r as Record<string, unknown>).summary_he ?? r.summary) as Partial<DossierSentence>[] | null | undefined,
    ).map(normalizeDossierSentence),
    specifications: arr(r.specifications).map(normalizeDossierSpecRow),
    variants_and_versions: arr(r.variants_and_versions).map(normalizeDossierVersionRow),
    performance: arr(r.performance).map(normalizeDossierPerformanceRow),
    maturity: normalizeDossierMaturity(r.maturity),
    deals: arr(r.deals).map(normalizeDossierDealRow),
    pricing: arr(r.pricing).map(normalizeDossierPriceRow),
    partnerships: arr(r.partnerships).map(normalizeDossierPartnerRow),
    competitors: arr(r.competitors).map(normalizeDossierCompetitorRow),
    regulatory_export: normalizeDossierRegulatoryExport(r.regulatory_export),
    patents: arr(r.patents).map(normalizeDossierPatentRef),
    tenders_and_forecasts: arr(r.tenders_and_forecasts).map(normalizeDossierTenderRef),
    risks_and_gaps: arr(
      ((r as Record<string, unknown>).risks_and_gaps_he ?? r.risks_and_gaps) as Partial<DossierSentence>[] | null | undefined,
    ).map(normalizeDossierSentence),
    what_changed: arr(
      ((r as Record<string, unknown>).what_changed_he ?? r.what_changed) as Partial<DossierSentence>[] | null | undefined,
    ).map(normalizeDossierSentence),
    bd_implications: arr(
      ((r as Record<string, unknown>).bd_implications_he ?? r.bd_implications) as Partial<DossierSentence>[] | null | undefined,
    ).map(normalizeDossierSentence),
  };
}

function normalizeDossierSource(raw: Partial<DossierSource> | null | undefined): DossierSource {
  const r = raw ?? {};
  return {
    n: num(r.n),
    url: str(r.url),
    title: str(r.title),
    kind: r.kind ?? null,
    reliability: r.reliability ?? null,
    accessed_at: r.accessed_at ?? null,
  };
}

const VALID_DOSSIER_OUTCOMES = new Set(["found", "partial", "not_found"]);

function normalizeDossierOutcome(value: unknown): DossierRunRef["outcome"] {
  return typeof value === "string" && VALID_DOSSIER_OUTCOMES.has(value)
    ? (value as DossierRunRef["outcome"])
    : "not_found";
}

function normalizeDossierRunRef(raw: Partial<DossierRunRef> | null | undefined): DossierRunRef {
  const r = raw ?? {};
  return {
    id: num(r.id),
    created_at: str(r.created_at),
    outcome: normalizeDossierOutcome(r.outcome),
    confidence: typeof r.confidence === "number" ? r.confidence : null,
    report_id: typeof r.report_id === "number" ? r.report_id : null,
  };
}

function normalizeDossierSummary(raw: Partial<DossierSummary> | null | undefined): DossierSummary {
  const r = raw ?? {};
  return {
    product_key: str(r.product_key),
    product_name: str(r.product_name),
    vendor: r.vendor ?? null,
    latest: r.latest ? normalizeDossierRunRef(r.latest) : null,
    count: num(r.count),
  };
}

const DOSSIER_PROGRESS_STATUSES = new Set(["pending", "running", "done", "failed"]);

function normalizeDossierProgressTopic(
  raw: Partial<DossierProgressTopic> | null | undefined,
): DossierProgressTopic {
  const r = raw ?? {};
  const status = DOSSIER_PROGRESS_STATUSES.has(String(r.status)) ? (r.status as DossierProgressTopic["status"]) : "pending";
  return {
    topic: str(r.topic),
    title_he: str(r.title_he),
    status,
    seconds: typeof r.seconds === "number" ? r.seconds : null,
    sources_found: typeof r.sources_found === "number" ? r.sources_found : null,
  };
}

function normalizeDossierPendingJob(
  raw: Partial<DossierPendingJob> | null | undefined,
): DossierPendingJob | null {
  if (!raw) return null;
  return {
    job_id: idStr(raw.job_id),
    state: str(raw.state),
    // PD-fix (2026-09-08, item 5): eoa.dossier.plan.run_plan's on_progress writes this into the
    // running job's own row; eoa.api.services._pending_dossier_job surfaces it verbatim.
    progress: arr(raw.progress).map(normalizeDossierProgressTopic),
  };
}

function normalizeDossierDetail(raw: Partial<DossierDetail> | null | undefined): DossierDetail {
  const r = raw ?? {};
  return {
    product_key: str(r.product_key),
    product_name: str(r.product_name),
    vendor: r.vendor ?? null,
    aliases: arr(r.aliases).map((a) => str(a)),
    dossiers: arr(r.dossiers).map(normalizeDossierRunRef),
    latest: r.latest
      ? { ...normalizeProductDossierOut(r.latest), sources: arr(r.latest.sources).map(normalizeDossierSource) }
      : null,
    pending_job: normalizeDossierPendingJob(r.pending_job),
  };
}

function normalizeDossierRunDetail(
  raw: Partial<DossierRunDetail> | null | undefined,
): DossierRunDetail {
  const r = raw ?? {};
  return {
    ...normalizeDossierRunRef(r),
    data: normalizeProductDossierOut(r.data),
    sources: arr(r.sources).map(normalizeDossierSource),
    path_docx: r.path_docx ?? null,
    path_md: r.path_md ?? null,
    path_html: r.path_html ?? null,
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
    relevance_score: r.relevance_score ?? null,
    intake: (r.intake ?? "candidate") as TenderCard["intake"],
    matched_terms: arr(r.matched_terms),
    entities: arr(r.entities),
    status: (r.status ?? "unknown") as TenderCard["status"],
    item_id: r.item_id ?? null,
    created_at: str(r.created_at),
    updated_at: str(r.updated_at),
    // PL-ui (2026-09-07): see the matching comment on `normalizeItemCard` above.
    product_lines: arr(r.product_lines),
  };
}

function normalizeTenderFeedback(
  raw: Partial<TenderFeedback> | null | undefined,
): TenderFeedback {
  const r = raw ?? {};
  return {
    id: num(r.id),
    tender_id: num(r.tender_id),
    verdict: (r.verdict ?? "relevant") as TenderFeedbackVerdict,
    reason: r.reason ?? null,
    source: r.source ?? null,
    territory: r.territory ?? null,
    matched_terms: arr(r.matched_terms),
    created_at: str(r.created_at),
  };
}

function normalizePatentRecord(
  raw: Partial<PatentRecord> | null | undefined,
): PatentRecord {
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

function normalizePayloadRecord(
  raw: Partial<PayloadRecord> | null | undefined,
): PayloadRecord {
  const r = raw ?? {};
  return {
    id: num(r.id),
    canonical_name: str(r.canonical_name),
    vendor_entity_name: r.vendor_entity_name ?? null,
    family: r.family ?? null,
    variant: r.variant ?? null,
    category: (r.category ?? "other") as PayloadRecord["category"],
    first_seen: r.first_seen ?? null,
    last_seen: r.last_seen ?? null,
    notes: r.notes ?? null,
    // W19 (migration 0022): identity-level image/spec-sheet reference, nullable.
    image_url: r.image_url ?? null,
    spec_url: r.spec_url ?? null,
    spec_source: r.spec_source ?? null,
    spec_version_count: num(r.spec_version_count),
    price_ref_count: num(r.price_ref_count),
    latest_spec_date: r.latest_spec_date ?? null,
    latest_price_date: r.latest_price_date ?? null,
    created_at: str(r.created_at),
    updated_at: str(r.updated_at),
  };
}

function normalizePatentSurveyCard(
  raw: Partial<PatentSurveyCard> | null | undefined,
): PatentSurveyCard {
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

function normalizeForecastCard(
  raw: Partial<ForecastCard> | null | undefined,
): ForecastCard {
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

function normalizeClarification(
  raw: Partial<Clarification> | null | undefined,
): Clarification {
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
    // W20 (docs/REVIEW_2026-09-06_evening.md): server-derived per-kind subject, see
    // `_job_subject_he` in `eoa.api.services`.
    subject_he: r.subject_he ?? null,
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
    return {
      total: num(data?.total, items.length),
      items,
      groups: data?.groups ?? undefined,
    };
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
  // CORR: "בדוק אימות מחדש" -- re-runs the corroboration check and returns the same shape as
  // `ItemCard.corroboration`, not a whole ItemCard.
  postItemCorroborate: async (id) =>
    normalizeCorroboration(
      await request<Partial<Corroboration>>(`/api/items/${id}/corroborate`, {
        method: "POST",
      }),
    ),
  // R10-links.
  getItemInvestigations: async (id: number) =>
    arr(
      await request<Partial<ItemInvestigationRef>[] | null>(
        `/api/items/${id}/investigations`,
      ),
    ).map(normalizeItemInvestigationRef),

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

  searchGraphEntities: async (q: string, limit?: number) =>
    arr(
      await request<Partial<GraphSearchResult>[] | null>(
        `/api/graph/search${qs({ q, limit })}`,
      ),
    ).map((r) => ({
      id: num(r.id),
      name: str(r.name),
      kind: str(r.kind),
      country: r.country ?? null,
      mention_count: num(r.mention_count),
    })),
  getGraphOverview: async (limit?: number, since?: string) =>
    normalizeGraphOverview(
      await request<Partial<GraphOverviewResponse>>(
        `/api/graph/overview${qs({ limit, since })}`,
      ),
    ),
  getGraphNeighborhood: async (entityId: number, query: NeighborhoodQuery = {}) =>
    normalizeNeighborhood(
      await request<
        Partial<{
          nodes: unknown[];
          edges: unknown[];
          center_id: number;
          truncated: boolean;
        }>
      >(
        `/api/graph/neighborhood/${entityId}${qs({
          depth: query.depth,
          kinds: query.kinds?.length ? query.kinds.join(",") : undefined,
          relation_types: query.relationTypes?.length
            ? query.relationTypes.join(",")
            : undefined,
          since: query.since,
          limit: query.limit,
        })}`,
      ),
    ),
  getGraphPath: async (a: number, b: number, maxDepth?: number) => {
    try {
      return normalizeGraphPath(
        await request<Partial<GraphPathResponse>>(
          `/api/graph/path${qs({ a, b, max_depth: maxDepth })}`,
        ),
      );
    } catch (err) {
      if (err instanceof ApiError && err.code === "not_found") return null;
      throw err;
    }
  },
  getEntityDetail: async (id: number) =>
    normalizeEntityDetailFull(
      await request<Partial<EntityDetailFull>>(`/api/entities/${id}/detail`),
    ),

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

  // W10 (docs/REVIEW_2026-09-06_evening.md round 4): agent/eoa/api/routes/security_review.py.
  getSecurityReviews: async () =>
    arr(await request<Partial<SecurityReviewCard>[] | null>("/api/security-reviews")).map(
      normalizeSecurityReviewCard,
    ),
  postSecurityReviewApprove: async (jobId: string) => {
    const data = await request<{ job_id?: string | number }>(
      `/api/security-reviews/${jobId}/approve`,
      { method: "POST" },
    );
    return { job_id: idStr(data?.job_id) };
  },
  postSecurityReviewDismiss: (jobId: string) =>
    request<{ ok: boolean }>(`/api/security-reviews/${jobId}/dismiss`, {
      method: "POST",
    }),

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
            const dataLine = chunk.split("\n").find((l) => l.startsWith("data:"));
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
            else if (evt.type === "meta")
              handlers.onMeta?.(str(evt.provider), str(evt.model));
            else if (evt.type === "sources") handlers.onSources?.(arr(evt.items));
            else if (evt.type === "answer_final") handlers.onAnswerFinal?.(str(evt.text));
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
    arr(
      await request<Partial<Conference>[] | null>(`/api/conferences${qs({ from, to })}`),
    ).map(normalizeConference),
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
      await request<Partial<ForecastCard>[] | null>(
        `/api/tenders/forecasts${qs({ limit })}`,
      ),
    ).map(normalizeForecastCard),

  getTenderSourceCoverage: async () => {
    type CoverageItem =
      TenderSourceCoverageResponse["regions"][number]["sources"][number];
    const raw = await request<Partial<TenderSourceCoverageResponse> | null>(
      "/api/tenders/coverage",
    );
    return {
      regions: arr(raw?.regions).map((r) => ({
        region: str(r?.region, "other"),
        sources: arr(r?.sources).map((s): CoverageItem => ({
          id: str(s?.id),
          name: str(s?.name),
          kind: (s?.kind ?? "search") as CoverageItem["kind"],
          country: str(s?.country, "other"),
          status: (s?.status ?? "not_integrated") as CoverageItem["status"],
          verified: bool(s?.verified),
          needs_key_env_var: s?.needs_key_env_var ?? null,
          notices_stored: num(s?.notices_stored),
          last_fetch_at: s?.last_fetch_at ?? null,
          priority_decrement: num(s?.priority_decrement),
        })),
      })),
      totals: raw?.totals ?? {},
      source_count: num(raw?.source_count),
    };
  },

  postTenderFeedback: async (tenderId, verdict, reason = null) =>
    normalizeTenderFeedback(
      await request<Partial<TenderFeedback>>(`/api/tenders/${tenderId}/feedback`, {
        method: "POST",
        body: JSON.stringify({ verdict, reason }),
      }),
    ),
  getTenderFeedback: async (tenderId) =>
    arr(
      await request<Partial<TenderFeedback>[] | null>(
        `/api/tenders/${tenderId}/feedback`,
      ),
    ).map(normalizeTenderFeedback),

  // A14: פטנטים ו-IP.
  getPatents: async (query: PatentsQuery) => {
    const raw = await request<{
      patents?: Partial<PatentRecord>[] | null;
      total?: number;
    }>(
      `/api/patents${qs({
        assignee: query.assignee,
        subdomain: query.subdomain,
        israeli: query.israeli,
        min_value_score: query.min_value_score,
        q: query.q,
        limit: query.limit,
      })}`,
    );
    return {
      patents: arr(raw?.patents).map(normalizePatentRecord),
      total: raw?.total ?? 0,
    };
  },
  getPatentsStatus: async () => request<PatentsStatusResponse>("/api/patents/status"),
  getPatentsHeatmap: async (topCpc = 10, topAssignees = 10) =>
    request<PatentHeatmapResponse>(
      `/api/patents/heatmap${qs({ top_cpc: topCpc, top_assignees: topAssignees })}`,
    ),
  getPatentSurveys: async (limit = 30) =>
    arr(
      await request<Partial<PatentSurveyCard>[] | null>(
        `/api/patents/surveys${qs({ limit })}`,
      ),
    ).map(normalizePatentSurveyCard),
  createPatentSurvey: async (topic: string) =>
    // Builds synchronously in-request when it finishes quickly enough, else falls back to a
    // job_id to poll -- needs more than the default 10s before that fallback is a false timeout.
    request<PatentSurveyCreateResponse>("/api/patents/surveys", {
      method: "POST",
      body: JSON.stringify({ topic }),
      timeoutMs: 65_000,
    }),

  // A17: מטע"דים -- מפרטים ומחירי ייחוס, עם היסטוריית גרסאות.
  getPayloads: async (query: PayloadsQuery = {}) => {
    const raw = await request<{
      payloads?: Partial<PayloadRecord>[] | null;
      total?: number;
    }>(
      `/api/payloads${qs({ category: query.category, vendor: query.vendor, family: query.family, q: query.q, limit: query.limit })}`,
    );
    return {
      payloads: arr(raw?.payloads).map(normalizePayloadRecord),
      total: raw?.total ?? 0,
    };
  },
  getPayload: async (id: number) => request<PayloadDetailResponse>(`/api/payloads/${id}`),
  getPayloadDiff: async (id: number, a: number, b: number) =>
    request<PayloadDiffResponse>(`/api/payloads/${id}/diff${qs({ a, b })}`),
  getPayloadTree: async () => request<PayloadTreeResponse>("/api/payloads/tree"),

  // A12 (מעקב טכנולוגי, 2026-09-06): "רדאר טכנולוגי".
  getTechRadar: async (weeks = 12) =>
    normalizeTechRadar(
      await request<Partial<TechRadarResponse>>(`/api/tech/radar${qs({ weeks })}`),
    ),
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
    normalizeRunsCurrent(
      await request<Partial<RunsCurrentResponse>>("/api/runs/current"),
    ),

  getReports: async (kind, limit = 30) =>
    arr(
      await request<Partial<ReportSummary>[] | null>(
        `/api/reports${qs({ kind, limit })}`,
      ),
    ).map(normalizeReportSummary),
  getReport: async (id) =>
    normalizeReportDetail(await request<Partial<ReportDetail>>(`/api/reports/${id}`)),
  getReportFileUrl: (id, fmt) => `/api/reports/${id}/file?fmt=${fmt}`,
  getReportCitations: async (id) =>
    normalizeReportCitations(
      await request<Partial<ReportCitationsResponse>>(`/api/reports/${id}/citations`),
    ),
  // R10-links.
  getReportInvestigations: async (id: number) =>
    arr(
      await request<Partial<ReportInvestigationRef>[] | null>(
        `/api/reports/${id}/investigations`,
      ),
    ).map(normalizeReportInvestigationRef),

  getBdTerritories: async () =>
    arr(await request<Partial<BdTerritoryOption>[] | null>("/api/bd/territories")).map(
      (t) => ({
        territory: str(t.territory),
        items: num(t.items),
        tenders: num(t.tenders),
        forecasts: num(t.forecasts),
        configured: bool(t.configured),
      }),
    ),
  getBdReports: async (territory) =>
    arr(
      await request<Partial<ReportSummary>[] | null>(
        `/api/bd/reports${qs({ territory })}`,
      ),
    ).map(normalizeReportSummary),
  postBdReport: async (territory, lookbackDays) => {
    // "Builds synchronously if the underlying bd_report job finishes within ~55s, else enqueues
    // it" (agent/eoa/api/routes/bd.py) -- same reasoning as createPatentSurvey above.
    const data = await request<Partial<BdReportCreateResponse>>("/api/bd/reports", {
      method: "POST",
      body: JSON.stringify({ territory, lookback_days: lookbackDays }),
      timeoutMs: 65_000,
    });
    return {
      job_id: idStr(data?.job_id),
      status: data?.status,
      error: data?.error ?? null,
      report: data?.report ? normalizeReportDetail(data.report) : undefined,
    };
  },

  // PL-ui (2026-09-07): built against a frozen contract (docs/qa/loop/round_7_fixes.md "### PL-ui
  // status") that may not exist on the live API yet -- every call here degrades to the
  // normalizers' empty-shape defaults (see `normalizeProductLine`/`normalizeProductLineDetail`
  // above) rather than throwing on a missing/malformed field, so a 404 is the only way this
  // surfaces as an error to the page (same as every other list/detail endpoint in this file).
  getProductLines: async () =>
    arr(await request<Partial<ProductLine>[] | null>("/api/product-lines")).map(
      normalizeProductLine,
    ),
  getProductLine: async (id) =>
    normalizeProductLineDetail(
      await request<Partial<ProductLineDetail>>(`/api/product-lines/${id}`),
    ),
  postProductLineReport: async (id) => {
    const data = await request<Partial<ProductLineReportCreateResponse>>(
      `/api/product-lines/${id}/report`,
      { method: "POST" },
    );
    return { job_id: idStr(data?.job_id) };
  },

  // PD-ui (docs/PLAN_PRODUCT_DOSSIER.md): "סקירות מוצר" -- built against a frozen contract
  // (section 5) that may not exist on the live API yet -- every call here degrades to the
  // normalizers' empty-shape defaults, same reasoning as the PL-ui block above.
  getDossiers: async () =>
    arr(await request<Partial<DossierSummary>[] | null>("/api/dossiers")).map(normalizeDossierSummary),
  postDossier: async (body: DossierCreateBody) => {
    const data = await request<Partial<DossierCreateResponse>>("/api/dossiers", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return { job_id: idStr(data?.job_id), product_key: str(data?.product_key) };
  },
  getDossier: async (productKey: string) =>
    normalizeDossierDetail(await request<Partial<DossierDetail>>(`/api/dossiers/${productKey}`)),
  getDossierRun: async (productKey: string, id: number) =>
    normalizeDossierRunDetail(
      await request<Partial<DossierRunDetail>>(`/api/dossiers/${productKey}/${id}`),
    ),
  postDossierRerun: async (productKey: string, body?: { budget_multiplier?: number | null }) => {
    const data = await request<Partial<DossierRerunResponse>>(`/api/dossiers/${productKey}/rerun`, {
      method: "POST",
      body: JSON.stringify(body ?? {}),
    });
    return { job_id: idStr(data?.job_id) };
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
        power_levels: p?.power_levels
          ? arr(p.power_levels).map((lvl) => str(lvl))
          : undefined,
      })),
    };
  },
  getLlmCalls: async (since = "24h") => {
    const data = await request<Partial<LlmCallsSummary>>(
      `/api/llm/calls${qs({ since })}`,
    );
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
    return {
      ok: bool(data?.ok),
      errors: arr(data?.errors),
      revision: data?.revision ?? null,
    };
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
        key_configured:
          s?.key_configured === null || s?.key_configured === undefined
            ? null
            : bool(s.key_configured),
        key_env: s?.key_env ? arr(s.key_env).map((k) => str(k)) : null,
        tool_count:
          s?.tool_count === null || s?.tool_count === undefined
            ? null
            : Number(s.tool_count),
        ok: s?.ok === null || s?.ok === undefined ? null : bool(s.ok),
        error: s?.error ?? null,
        latency_ms:
          s?.latency_ms === null || s?.latency_ms === undefined
            ? null
            : Number(s.latency_ms),
        tools: arr(s?.tools).map((tName) => str(tName)),
      })),
    };
  },
  postMcpServerPing: async (serverId: string) => {
    const data = await request<Partial<McpPingResponse>>(
      `/api/mcp/servers/${encodeURIComponent(serverId)}/ping`,
      {
        method: "POST",
      },
    );
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
    const data = await request<Partial<McpCallsResponse>>(
      `/api/mcp/calls${qs({ since })}`,
    );
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
