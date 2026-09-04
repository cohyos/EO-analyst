import type {
  Clarification,
  GraphResponse,
  Headline,
  Job,
  Lesson,
  NightSummary,
  StatusResponse,
  Survey,
} from "@/types/api";

export const mockNightSummary: NightSummary = {
  items_ingested: 40,
  classified: 40,
  red: 6,
  orange: 11,
  deep_searches: 2,
  duration_min: 47,
  errors: 1,
};

export const mockHeadlines: Headline[] = [
  {
    item_id: 1,
    title: "Elbit Systems חושפת דור חדש של פודי כיוון",
    level: "red",
    summary_he: "פוד חדש עם מודול ATR מבוסס Edge AI וטווח זיהוי מוגדל.",
    url: "https://example-source.test/articles/2000",
  },
  {
    item_id: 7,
    title: "Anduril Industries — התקשרות אפשרית לאפקטור C-UAS חדש",
    level: "red",
    summary_he: "אותרו מקורות ראשוניים על חוזה אפשרי; חקירת עומק בעיצומה.",
    url: "https://example-source.test/articles/2006",
  },
  {
    item_id: 4,
    title: "עדכון רגולציית ייצוא באירופה משפיע על Safran ו-HENSOLDT",
    level: "orange",
    summary_he: "שינוי מדיניות ייצוא רכיבי EO/IR עשוי לעכב אספקות לשוק האסייתי.",
    url: "https://example-source.test/articles/2003",
  },
];

export const mockClarifications: Clarification[] = [
  {
    id: 1,
    kind: "classification",
    question: "האם לסווג את פעילות SPARK Vision Labs כ'סטארטאפ מחובר' לרפאל?",
    options: ["כן, קשר מבוסס", "לא, קשר עקיף בלבד", "נדרש מידע נוסף"],
    answer: null,
    asked_at: "2026-09-04T06:05:00+03:00",
    timeout_at: "2026-09-05T06:05:00+03:00",
    assumed: false,
  },
  {
    id: 2,
    kind: "dedup",
    question: "שני פרסומים על אותו חוזה IAI — למזג לכתבה אחת?",
    options: ["כן, מזג", "לא, השאר נפרדים"],
    answer: null,
    asked_at: "2026-09-03T21:40:00+03:00",
    timeout_at: "2026-09-04T21:40:00+03:00",
    assumed: false,
  },
  {
    id: 3,
    kind: "priority",
    question: "להעלות עדיפות מעקב אחר Aselsan בתחום C-UAS?",
    options: ["כן", "לא", "להשאיר ברמה הנוכחית"],
    answer: "להשאיר ברמה הנוכחית",
    asked_at: "2026-09-02T07:00:00+03:00",
    timeout_at: "2026-09-03T07:00:00+03:00",
    assumed: true,
  },
];

export const mockSurvey: Survey = {
  id: 21,
  report_id: 501,
  questions: [
    {
      id: "relevance",
      type: "scale",
      text_he: "עד כמה הדוח הבוקר היה רלוונטי למשימה שלך? (1-5)",
      options: null,
    },
    {
      id: "missed_domain",
      type: "choice",
      text_he: "האם פספסנו תחום שהיה צריך להופיע?",
      options: ["לא", "כן — נגד כטב\"מים", "כן — לוחמה EO", "כן — אחר"],
    },
    {
      id: "free_text",
      type: "text",
      text_he: "הערות חופשיות לשיפור הדוח הבא",
      options: null,
    },
  ],
  answers: null,
};

export const mockLessons: Lesson[] = [
  {
    id: 1,
    kind: "triage",
    text: "פרסומי 'Company PR' על שדרוגי תוכנה בלבד (ללא חומרה חדשה) — להוריד רמה ל-yellow כברירת מחדל.",
    active: true,
    created_at: "2026-08-20T08:00:00+03:00",
  },
  {
    id: 2,
    kind: "entity",
    text: "'HENSOLDT AG' ו-'Hensoldt' הם אותה ישות — לאחד כינויים.",
    active: true,
    created_at: "2026-08-22T08:00:00+03:00",
  },
  {
    id: 3,
    kind: "source",
    text: "מקורות TASS על יכולות מערביות — לסמן כ-rumor_speculation עד אימות נגדי.",
    active: true,
    created_at: "2026-08-25T08:00:00+03:00",
  },
  {
    id: 4,
    kind: "dedup",
    text: "כתבות מתורגמות מ-Globes ל-Calcalist באותו יום — לרוב כפילות; לבדוק כותרת בעברית.",
    active: false,
    created_at: "2026-08-18T08:00:00+03:00",
  },
];

export const mockJobs: Job[] = [
  {
    id: "job-3001",
    scope: "daily",
    mode: "full",
    state: "done",
    created_at: "2026-09-03T20:00:00+03:00",
    started_at: "2026-09-03T20:00:10+03:00",
    finished_at: "2026-09-04T06:05:00+03:00",
    progress: null,
  },
  {
    id: "inv-8842",
    scope: "ingest",
    mode: "eco",
    state: "running",
    created_at: "2026-09-04T09:05:00+03:00",
    started_at: "2026-09-04T09:05:02+03:00",
    finished_at: null,
    progress: "סבב 2/4",
  },
  {
    id: "job-3002",
    scope: "weekly",
    mode: "eco",
    state: "queued",
    created_at: "2026-09-04T10:00:00+03:00",
    started_at: null,
    finished_at: null,
    progress: null,
  },
];

export function mockStatus(): StatusResponse {
  const now = new Date();
  const vramTotal = 12288;
  const jitter = Math.sin(now.getTime() / 4000) * 800;
  const vramUsed = Math.max(1200, Math.min(vramTotal - 200, 7200 + jitter));
  return {
    at: now.toISOString(),
    services: { postgres: true, ollama: true, searxng: true, ntfy: true },
    gate: {
      gpu: {
        available: true,
        vram_used_mb: Math.round(vramUsed),
        vram_total_mb: vramTotal,
        vram_free_mb: Math.round(vramTotal - vramUsed),
        util_pct: Math.round(40 + Math.abs(Math.sin(now.getTime() / 3000)) * 55),
        temp_c: Math.round(58 + Math.abs(Math.cos(now.getTime() / 5000)) * 15),
      },
      ram: { free_mb: 65_536 - 28_400, total_mb: 65_536 },
      disk_free_gb: 214,
      loaded_models: [
        { name: "qwen2.5:14b-instruct", size_mb: 8_900, size_vram_mb: 8_900, cpu_offload: false },
      ],
      batch_window: false,
      recent_decisions: [
        {
          at: new Date(now.getTime() - 30_000).toISOString(),
          decision: "proceed",
          model: "qwen2.5:14b-instruct",
          reason: "vram ok",
        },
        {
          at: new Date(now.getTime() - 90_000).toISOString(),
          decision: "queued",
          model: "hf.co/dicta-il/DictaLM-3.0-Nemotron-12B",
          reason: "waiting for vram headroom",
        },
      ],
    },
    pipeline: {
      current_job: mockJobs[1],
      queue_depth: 1,
      stage: "deep_search",
      night_window: false,
      next_run_at: "2026-09-04T20:00:00+03:00",
      last_run: {
        started_at: "2026-09-03T20:00:10+03:00",
        finished_at: "2026-09-04T06:05:00+03:00",
        state: "done",
        stages: {
          fetch: { events: 12, last_event: "done", last_at: "2026-09-03T20:12:00+03:00" },
          dedup: { events: 4, last_event: "done", last_at: "2026-09-03T20:18:00+03:00" },
          classify: { events: 40, last_event: "done", last_at: "2026-09-03T20:55:00+03:00" },
          triage: { events: 40, last_event: "done", last_at: "2026-09-03T21:20:00+03:00" },
          deep_search: { events: 2, last_event: "skipped", last_at: "2026-09-03T21:25:00+03:00" },
          report: { events: 1, last_event: "done", last_at: "2026-09-04T06:05:00+03:00" },
        },
      },
    },
  };
}

export const mockGraph: GraphResponse = {
  nodes: [
    { id: 1, name: "Elbit Systems", kind: "company", country: "IL" },
    { id: 2, name: "Rafael Advanced Defense Systems", kind: "company", country: "IL" },
    { id: 3, name: "Leonardo DRS", kind: "company", country: "US" },
    { id: 5, name: "Teledyne FLIR", kind: "company", country: "US" },
    { id: 7, name: "Anduril Industries", kind: "company", country: "US" },
    { id: 9, name: "IAI (Israel Aerospace Industries)", kind: "company", country: "IL" },
    { id: 10, name: "משרד הביטחון — מפא\"ת", kind: "government", country: "IL" },
    { id: 12, name: "SPARK Vision Labs", kind: "company", country: "IL" },
  ],
  edges: [
    { src: 1, dst: 2, label: "COMPETITOR_OF", item_id: 3, evidence: "שתי החברות מתמודדות באותו מכרז" },
    { src: 1, dst: 12, label: "PARTNER_OF", item_id: 8, evidence: "הסכם פיתוח משותף שפורסם" },
    { src: 2, dst: 5, label: "SUPPLIER_OF", item_id: 11, evidence: "רכיבי גלאי IR מסופקים ל-Rafael" },
    { src: 1, dst: 10, label: "BIDS_AGAINST", item_id: 14, evidence: "מתמודדת במכרז מפא\"ת" },
    { src: 9, dst: 10, label: "BIDS_AGAINST", item_id: 21, evidence: "מתמודדת באותו מכרז" },
    { src: 3, dst: 7, label: "INTEGRATES_WITH", item_id: 17, evidence: "שילוב חיישן בפלטפורמת Anduril" },
    { src: 2, dst: 1, label: "COMPETITOR_OF", item_id: 3, evidence: "יריבות ישירה בתחום פודי הכיוון" },
  ],
};
