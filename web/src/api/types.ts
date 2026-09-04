import type {
  AskCitation,
  AskRequest,
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

export interface ItemsQuery {
  level?: TriageLevel[];
  domain?: string;
  since?: string;
  q?: string;
  page?: number;
  page_size?: number;
  sort?: "score" | "published_at";
}

export interface EntitiesQuery {
  q?: string;
  kind?: string;
  limit?: number;
}

export interface GraphQuery {
  entity_id: number;
  depth?: number;
  labels?: string;
}

/**
 * Client-side surface of docs/API.md. Both the real (fetch-based) client and
 * the mock client implement this exact shape so pages never branch on
 * VITE_USE_MOCKS themselves.
 */
export interface ApiClient {
  getMorning(): Promise<MorningResponse>;

  getItems(query: ItemsQuery): Promise<ItemsResponse>;
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

  askStream(
    body: AskRequest,
    handlers: {
      onToken: (text: string) => void;
      onCitations: (items: AskCitation[]) => void;
      onDone: () => void;
      onError: (err: Error) => void;
    },
  ): () => void; // returns an abort function

  getConferences(from?: string, to?: string): Promise<Conference[]>;
  getConferencesIcalUrl(): string;

  getClarifications(open?: boolean): Promise<Clarification[]>;
  postClarificationAnswer(id: number, answer: string): Promise<void>;
  getLatestSurvey(): Promise<Survey>;
  postSurveyAnswers(id: number, answers: Record<string, unknown>): Promise<void>;
  getLessons(): Promise<Lesson[]>;
  postLesson(kind: string, text: string): Promise<Lesson>;
  deleteLesson(id: number): Promise<void>;

  getJobs(state?: string, limit?: number): Promise<Job[]>;
  postRun(scope: string, mode: "eco" | "full"): Promise<{ job_id: string }>;
  postJobCancel(id: number): Promise<void>;

  getReports(kind?: string, limit?: number): Promise<ReportSummary[]>;
  getReport(id: number): Promise<ReportDetail>;
  getReportFileUrl(id: number, fmt: "docx" | "md" | "html"): string;

  getSettings(name: SettingsName): Promise<SettingsGetResponse>;
  putSettings(name: SettingsName, yaml: string): Promise<SettingsPutResponse>;
}
