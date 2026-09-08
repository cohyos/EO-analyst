import type {
  DossierDetail,
  DossierRunDetail,
  DossierRunRef,
  DossierSource,
  DossierSummary,
  ProductDossierOut,
} from "@/types/api";

// PD-ui (docs/PLAN_PRODUCT_DOSSIER.md): "סקירות מוצר" -- mock data for VITE_USE_MOCKS. Two
// fixtures per the task brief: a realistic, richly-populated SPECTRO XR dossier (the plan's own
// worked example, section 1) and a sparsely-populated "STRATOS" dossier showing the honest
// "לא נמצא במקורות" / empty-array rendering a partial/early-stage investigation produces.

function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9֐-׿]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function productKeyFor(productName: string, vendor?: string | null): string {
  const parts = [vendor, productName].filter((p): p is string => !!p && p.trim().length > 0);
  return slugify(parts.join(" ")) || `product-${Date.now()}`;
}

const SPECTRO_SOURCES: DossierSource[] = [
  {
    n: 1,
    url: "https://elbitsystems.com/products/c4isr/observation-and-surveillance/spectro-xr/",
    title: "SPECTRO XR — Elbit Systems product page",
    kind: "official",
    reliability: "high",
    accessed_at: "2026-09-07T06:00:00+03:00",
  },
  {
    n: 2,
    url: "https://elbitsystems.com/media/spectro-xr-datasheet.pdf",
    title: "SPECTRO XR — Datasheet (Elbit Systems)",
    kind: "datasheet",
    reliability: "high",
    accessed_at: "2026-09-07T06:02:00+03:00",
  },
  {
    n: 3,
    url: "https://www.idf.il/en/mini-sites/2022-events/border-security/spectro-xr-deployment/",
    title: "IDF — Long-range border observation systems deployment",
    kind: "official",
    reliability: "high",
    accessed_at: "2026-09-07T06:05:00+03:00",
  },
  {
    n: 4,
    url: "https://www.janes.com/defence-news/news-detail/elbit-spectro-xr-border-surveillance",
    title: "Janes — Elbit unveils SPECTRO XR long-range EO surveillance system",
    kind: "article",
    reliability: "medium",
    accessed_at: "2026-09-07T06:08:00+03:00",
  },
  {
    n: 5,
    url: "https://www.gov.il/he/departments/publications/reports/moj-tender-2023-border-eo",
    title: 'מכרז משרד הביטחון — מערכות תצפית אלקטרו-אופטיות לגבול',
    kind: "official",
    reliability: "high",
    accessed_at: "2026-09-07T06:10:00+03:00",
  },
  {
    n: 6,
    url: "https://www.defensenews.com/global/mideast-africa/2024/03/12/elbit-wins-border-surveillance-contract/",
    title: "Defense News — Elbit wins border-surveillance framework contract",
    kind: "article",
    reliability: "medium",
    accessed_at: "2026-09-07T06:12:00+03:00",
  },
  {
    n: 7,
    url: "https://patents.google.com/patent/US10896327B2",
    title: "US10896327B2 — Long-range multi-sensor observation system (Elbit Systems)",
    kind: "official",
    reliability: "high",
    accessed_at: "2026-09-07T06:15:00+03:00",
  },
  {
    n: 8,
    url: "https://www.shephardmedia.com/news/landwarfareintl/spectro-xr-latin-america-order/",
    title: "Shephard Media — Latin American customer orders SPECTRO XR systems",
    kind: "article",
    reliability: "medium",
    accessed_at: "2026-09-07T06:18:00+03:00",
  },
];

const SPECTRO_DATA: ProductDossierOut = {
  identity: {
    product_name: "SPECTRO XR",
    vendor: "Elbit Systems",
    product_family: "SPECTRO",
    category_he: "מערכת תצפית אלקטרו-אופטית ארוכת טווח להגנת גבולות",
    first_announced: "2016-06-01",
    status_he: "בייצור ובשירות מבצעי",
    cites: [1, 2],
  },
  summary: [
    {
      text_he:
        "SPECTRO XR הינה מערכת תצפית אלקטרו-אופטית ארוכת טווח של אלביט מערכות, המיועדת לגילוי, זיהוי וזיהוי חיובי של מטרות לאורך גבולות יבשתיים וימיים.",
      cites: [1, 2],
    },
    {
      text_he: "המערכת משלבת ערוצי EO/IR וטווח לייזר בתוך כדור מיוצב, ומופעלת ממגדלי תצפית קבועים וניידים.",
      cites: [2],
    },
    {
      text_he: "היא בשירות מבצעי בצה\"ל לאורך גבולות ישראל מאז 2018, ונמכרה ללקוחות נוספים באמריקה הלטינית.",
      cites: [3, 8],
    },
    {
      text_he: "מתחרה ישירה במוצרים כגון WESCAM MX-25 של L3Harris ו-Corvus Alpha של Safran.",
      cites: [4],
    },
  ],
  specifications: [
    { parameter_he: "טווח גילוי (אדם)", value: "26", unit: 'ק"מ', variant: null, source_kind: "datasheet", cites: [2] },
    { parameter_he: "טווח זיהוי (רכב)", value: "36", unit: 'ק"מ', variant: null, source_kind: "datasheet", cites: [2] },
    { parameter_he: "משקל", value: "95", unit: 'ק"ג', variant: null, source_kind: "datasheet", cites: [2] },
    { parameter_he: "ערוצי חיישנים", value: "EO צבע, IR קירור, LRF", unit: null, variant: null, source_kind: "datasheet", cites: [2] },
    { parameter_he: "ייצוב", value: "פעיל, שני צירים", unit: null, variant: "XR2", source_kind: "brochure", cites: [1] },
    { parameter_he: "ממשקים", value: "Ethernet, RS-422", unit: null, variant: null, source_kind: "datasheet", cites: [2] },
  ],
  variants_and_versions: [
    {
      name: "SPECTRO XR",
      year: 2016,
      changes_he: "גרסת הבסיס — הכרזה ראשונה בתערוכת ISDEF.",
      platforms: ["מגדל תצפית קבוע"],
      cites: [1],
    },
    {
      name: "SPECTRO XR2",
      year: 2022,
      changes_he: "שדרוג חיישן IR לרזולוציה גבוהה יותר וטווח מורחב.",
      platforms: ["מגדל תצפית קבוע", "רכב תצפית נייד"],
      cites: [1, 4],
    },
  ],
  performance: [
    {
      metric_he: "טווח גילוי אדם",
      claimed_value: '26 ק"מ',
      tested_value: '22 ק"מ (הודגם בתנאי שדה, IDF)',
      conditions_he: "ראות טובה, יעד עומד",
      cites: [2, 3],
    },
    {
      metric_he: "זמן תגובה לתראה",
      claimed_value: "לא נמסר",
      tested_value: null,
      conditions_he: null,
      cites: [],
    },
  ],
  maturity: {
    trl: 9,
    operational_users: ["צה\"ל", "לקוח באמריקה הלטינית (לא נחשף)"],
    platforms_integrated: ["מגדל תצפית קבוע", "רכב תצפית נייד"],
    first_fielding: "2018-01-01",
    assessment_he: "מערכת בשלה, בשירות מבצעי מתמשך מעל 6 שנים עם היסטוריית רכש חוזרת.",
    cites: [3, 6],
  },
  deals: [
    {
      date: "2023-11-01",
      customer: "משרד הביטחון הישראלי",
      country: "IL",
      kind: "contract_award",
      amount: "לא נמסר",
      currency: null,
      quantity: null,
      platform: "מגדל תצפית קבוע",
      cites: [5],
      confidence: 0.7,
    },
    {
      date: "2024-03-12",
      customer: "לא נחשף",
      country: null,
      kind: "framework",
      amount: "לא נמסר",
      currency: null,
      quantity: null,
      platform: null,
      cites: [6],
      confidence: 0.5,
    },
    {
      date: "2019-08-20",
      customer: "לקוח באמריקה הלטינית",
      country: null,
      kind: "export_license",
      amount: "לא נמסר",
      currency: null,
      quantity: 12,
      platform: "רכב תצפית נייד",
      cites: [8],
      confidence: 0.6,
    },
  ],
  pricing: [],
  partnerships: [
    { partner: "Elbit Systems of America", role_he: "יצרן משנה (ייצור מקומי ל-US)", since: "2019-01-01", cites: [4] },
  ],
  competitors: [
    {
      product: "WESCAM MX-25",
      vendor: "L3Harris",
      comparison_he: "תחום דמיון בכדור תצפית ארוך-טווח; MX-25 מתמקד בעיקר באוויר, SPECTRO XR מתוכנן לתצפית קרקעית קבועה.",
      cites: [4],
    },
    {
      product: "Corvus Alpha",
      vendor: "Safran Electronics & Defense",
      comparison_he: "מוצר מתחרה ישיר בתחום תצפית גבולות ארוכת-טווח באירופה.",
      cites: [4],
    },
  ],
  regulatory_export: {
    export_regime_he: "DECA (רישוי יצוא ביטחוני ישראלי)",
    restrictions_he: "יצוא כפוף לאישור אגף הפיקוח על יצוא ביטחוני (אפ\"י).",
    cites: [8],
  },
  patents: [
    {
      pub_number: "US10896327B2",
      title: "Long-range multi-sensor observation system",
      assignee: "Elbit Systems Ltd.",
      relevance_he: "פטנט הליבה על שילוב חיישני EO/IR וייצוב בכדור תצפית ארוך-טווח.",
      cites: [7],
    },
  ],
  tenders_and_forecasts: [
    {
      tender_id: null,
      title: 'מכרז משרד הביטחון — מערכות תצפית אלקטרו-אופטיות לגבול (2023)',
      status: "awarded",
      relevance_he: "המכרז שהוביל להסכם המסגרת מ-2023 עם אלביט מערכות.",
      cites: [5],
    },
  ],
  risks_and_gaps: [
    { text_he: 'לא נמצאו במקורות פרטי מחיר יחידה או שווי חוזה כספי מדויק.', cites: [] },
    { text_he: "זהות הלקוח באמריקה הלטינית לא נחשפה במקור הזמין.", cites: [8] },
    { text_he: "אין אישור עצמאי (שאינו יצרן) לביצועי הזיהוי הנטענים מעבר לטווח שהודגם בשדה.", cites: [2, 3] },
  ],
  what_changed: [],
  bd_implications: [
    {
      text_he:
        "מוצר בוגר עם היסטוריית רכש חוזרת ממשרד הביטחון — פוטנציאל להצעות תחזוקה/שדרוג לגרסת XR2 בלקוחות קיימים.",
      cites: [3, 6],
    },
    {
      text_he: "היעדר מידע על תמחור מקשה על השוואת תחרותיות מול MX-25/Corvus Alpha; מומלץ איסוף נוסף לפני הצעת מחיר.",
      cites: [],
    },
  ],
};

const STRATOS_SOURCES: DossierSource[] = [
  {
    n: 1,
    url: "https://www.safran-electronics-defense.com/observation-optronics/stratos",
    title: "STRATOS — Safran Electronics & Defense product overview",
    kind: "official",
    reliability: "medium",
    accessed_at: "2026-09-07T07:00:00+03:00",
  },
];

const STRATOS_DATA: ProductDossierOut = {
  identity: {
    product_name: "STRATOS",
    vendor: "Safran Electronics & Defense",
    product_family: null,
    category_he: "מערכת תצפית אלקטרו-אופטית לגבולות (הכרזה ראשונית)",
    first_announced: null,
    status_he: "בפיתוח (לפי הכרזת יצרן)",
    cites: [1],
  },
  summary: [
    {
      text_he: 'STRATOS היא מערכת תצפית אלקטרו-אופטית שהוכרזה על-ידי Safran, ללא פרטי מפרט מלאים במקור הזמין.',
      cites: [1],
    },
  ],
  specifications: [],
  variants_and_versions: [],
  performance: [],
  maturity: {
    trl: null,
    operational_users: [],
    platforms_integrated: [],
    first_fielding: null,
    assessment_he: "לא נמצא במקורות מידע מספק להערכת בשלות.",
    cites: [],
  },
  deals: [],
  pricing: [],
  partnerships: [],
  competitors: [],
  regulatory_export: { export_regime_he: null, restrictions_he: null, cites: [] },
  patents: [],
  tenders_and_forecasts: [],
  risks_and_gaps: [
    { text_he: "המחקר הראשוני מצא רק מקור רשמי יחיד — נדרש סבב מחקר נוסף (תקציב כפול) להעמקה.", cites: [1] },
  ],
  what_changed: [],
  bd_implications: [
    { text_he: "מוקדם מדי לגזור המלצת פעולה עסקית — נדרש מידע נוסף על מפרט ולוחות זמנים.", cites: [] },
  ],
};

interface MockDossierProduct {
  product_key: string;
  product_name: string;
  vendor: string | null;
  aliases: string[];
  runs: DossierRunDetail[];
  pending_job: { job_id: string; state: string } | null;
}

function makeRun(
  id: number,
  createdAt: string,
  outcome: DossierRunRef["outcome"],
  confidence: number | null,
  reportId: number | null,
  data: ProductDossierOut,
  sources: DossierSource[],
): DossierRunDetail {
  return {
    id,
    created_at: createdAt,
    outcome,
    confidence,
    report_id: reportId,
    data,
    sources,
    path_docx: reportId ? `/reports/dossier_${id}.docx` : null,
    path_md: reportId ? `/reports/dossier_${id}.md` : null,
    path_html: reportId ? `/reports/dossier_${id}.html` : null,
  };
}

export const mockDossierProducts: Record<string, MockDossierProduct> = {
  "elbit-systems-spectro-xr": {
    product_key: "elbit-systems-spectro-xr",
    product_name: "SPECTRO XR",
    vendor: "Elbit Systems",
    aliases: ["Spectro", "SPECTRO XR", "ספקטרו"],
    runs: [makeRun(501, "2026-09-06T21:10:00+03:00", "found", 0.82, 940, SPECTRO_DATA, SPECTRO_SOURCES)],
    pending_job: null,
  },
  "safran-electronics-defense-stratos": {
    product_key: "safran-electronics-defense-stratos",
    product_name: "STRATOS",
    vendor: "Safran Electronics & Defense",
    aliases: ["STRATOS"],
    runs: [makeRun(502, "2026-09-05T09:40:00+03:00", "partial", 0.28, null, STRATOS_DATA, STRATOS_SOURCES)],
    pending_job: null,
  },
};

let nextMockDossierRunId = 600;
const pendingTimers: Record<string, ReturnType<typeof setTimeout>> = {};

/** Builds a light "second run" derived from the SPECTRO XR fixture with one new deal and a
 * `what_changed` diff, so "הרץ שוב" / a fresh create for an already-known product has something
 * real to show in the run-history "השווה" expander. Any other product name gets the sparse
 * STRATOS-shaped fixture instead — realistic for a first, still-thin investigation. */
function buildFollowUpRun(product: MockDossierProduct, id: number, reportId: number): DossierRunDetail {
  const isSpectro = /spectro/i.test(product.product_name);
  const base = isSpectro ? SPECTRO_DATA : STRATOS_DATA;
  const sources = isSpectro ? SPECTRO_SOURCES : STRATOS_SOURCES;
  const data: ProductDossierOut = isSpectro
    ? {
        ...base,
        deals: [
          {
            date: "2026-09-01",
            customer: "לקוח נוסף (לא נחשף)",
            country: null,
            kind: "option",
            amount: "לא נמסר",
            currency: null,
            quantity: 6,
            platform: "מגדל תצפית קבוע",
            cites: [6],
            confidence: 0.55,
          },
          ...base.deals,
        ],
        what_changed: [
          { text_he: "התווספה עסקת אופציה חדשה שאותרה במחקר החוזר.", cites: [6] },
          { text_he: "לא זוהו שינויים במפרט הטכני מאז הסקירה הקודמת.", cites: [] },
        ],
      }
    : { ...base, what_changed: [{ text_he: "לא נמצאו עדכונים נוספים מאז הסקירה הקודמת.", cites: [] }] };
  return makeRun(id, new Date().toISOString(), isSpectro ? "found" : "partial", isSpectro ? 0.84 : 0.3, reportId, data, sources);
}

export function buildMockDossierSummaries(): DossierSummary[] {
  return Object.values(mockDossierProducts).map((p) => {
    const latestRun = p.runs[0] ?? null;
    return {
      product_key: p.product_key,
      product_name: p.product_name,
      vendor: p.vendor,
      latest: latestRun
        ? {
            id: latestRun.id,
            created_at: latestRun.created_at,
            outcome: latestRun.outcome,
            confidence: latestRun.confidence,
            report_id: latestRun.report_id,
          }
        : null,
      // `count` = deal count on the latest run (see DossierSummary's own doc comment in
      // web/src/types/api.ts for why) -- not the number of past runs.
      count: latestRun?.data.deals.length ?? 0,
    };
  });
}

export function buildMockDossierDetail(key: string): DossierDetail | null {
  const p = mockDossierProducts[key];
  if (!p) return null;
  const latestRun = p.runs[0] ?? null;
  return {
    product_key: p.product_key,
    product_name: p.product_name,
    vendor: p.vendor,
    aliases: p.aliases,
    dossiers: p.runs.map((r) => ({
      id: r.id,
      created_at: r.created_at,
      outcome: r.outcome,
      confidence: r.confidence,
      report_id: r.report_id,
    })),
    latest: latestRun ? { ...latestRun.data, sources: latestRun.sources } : null,
    pending_job: p.pending_job,
  };
}

export function buildMockDossierRunDetail(key: string, id: number): DossierRunDetail | null {
  const p = mockDossierProducts[key];
  if (!p) return null;
  return p.runs.find((r) => r.id === id) ?? null;
}

/** Registers a brand-new product (or reuses an existing one for the same key) with a
 * `pending_job`, then resolves it a few seconds later with a fresh run -- mirrors the "queue a
 * build, poll for it to finish" flow every other mock report-creation endpoint in this app uses
 * (see `postProductLineReport`/`postBdReport`). */
export function createOrRerunMockDossier(
  productName: string,
  vendor: string | null | undefined,
  aliases: string[] | undefined,
): { job_id: string; product_key: string } {
  const key = productKeyFor(productName, vendor);
  const jobId = `mock-dossier-job-${nextMockDossierRunId}`;
  const existing = mockDossierProducts[key];
  if (existing) {
    existing.pending_job = { job_id: jobId, state: "queued" };
  } else {
    mockDossierProducts[key] = {
      product_key: key,
      product_name: productName,
      vendor: vendor ?? null,
      aliases: aliases && aliases.length > 0 ? aliases : [productName],
      runs: [],
      pending_job: { job_id: jobId, state: "queued" },
    };
  }
  scheduleMockDossierCompletion(key);
  return { job_id: jobId, product_key: key };
}

export function rerunMockDossier(key: string): { job_id: string } | null {
  const p = mockDossierProducts[key];
  if (!p) return null;
  const jobId = `mock-dossier-job-${nextMockDossierRunId}`;
  p.pending_job = { job_id: jobId, state: "queued" };
  scheduleMockDossierCompletion(key);
  return { job_id: jobId };
}

function scheduleMockDossierCompletion(key: string): void {
  if (pendingTimers[key]) clearTimeout(pendingTimers[key]);
  pendingTimers[key] = setTimeout(() => {
    const p = mockDossierProducts[key];
    if (!p) return;
    const id = nextMockDossierRunId++;
    const reportId = 940 + id;
    const run = p.runs.length === 0
      ? makeRun(
          id,
          new Date().toISOString(),
          /spectro/i.test(p.product_name) ? "found" : "partial",
          /spectro/i.test(p.product_name) ? 0.82 : 0.3,
          reportId,
          /spectro/i.test(p.product_name) ? SPECTRO_DATA : STRATOS_DATA,
          /spectro/i.test(p.product_name) ? SPECTRO_SOURCES : STRATOS_SOURCES,
        )
      : buildFollowUpRun(p, id, reportId);
    p.runs.unshift(run);
    p.pending_job = null;
    delete pendingTimers[key];
  }, 6000);
}
