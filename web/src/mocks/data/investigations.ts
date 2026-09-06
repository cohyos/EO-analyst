import type { InvestigationDetail, InvestigationLogLine } from "@/types/api";

const log1: InvestigationLogLine[] = [
  {
    round: 1,
    lang: "he",
    query: "Elbit Systems פוד כיוון דור חדש 2026",
    results: 8,
    outcome: "נמצאו 3 מקורות רלוונטיים; ממשיך לסבב 2 לאימות מפרט טכני",
    at: "2026-09-03T22:11:00+03:00",
  },
  {
    round: 2,
    lang: "en",
    query: "Elbit Systems next-gen targeting pod specifications 2026",
    results: 12,
    outcome: "אותר מקור ראשוני (Company PR) + כתבה משנית ב-Janes; חיפוש פטנטים",
    at: "2026-09-03T22:13:40+03:00",
  },
  {
    round: 3,
    lang: "en",
    query: "Elbit targeting pod patent USPTO 2025 2026",
    results: 5,
    outcome: "נמצא פטנט תואם; מרכיב תשובה סופית עם ציטוטים",
    at: "2026-09-03T22:16:05+03:00",
  },
];

const log2: InvestigationLogLine[] = [
  {
    round: 1,
    lang: "he",
    query: "Anduril נגד כטב\"מים אפקטור חדש רכש",
    results: 6,
    outcome: "לא נמצא מידע חדש ישיר; מרחיב לחיפוש אנגלית",
    at: "2026-09-03T09:05:00+03:00",
  },
  {
    round: 2,
    lang: "en",
    query: "Anduril counter-UAS effector contract award 2026",
    results: 14,
    outcome: "נמצאו 2 מקורות (Defense News, GovTribe); ממשיך לבדוק פרטי חוזה",
    at: "2026-09-03T09:07:22+03:00",
  },
];

export const mockInvestigations: InvestigationDetail[] = [
  {
    job_id: "inv-8841",
    item_id: 1,
    question: "מה המפרט הטכני המלא של הפוד החדש ומי הלקוחות הפוטנציאליים?",
    item_title: null,
    error: null,
    state: "done",
    rounds: 3,
    queries: 3,
    pages_read: 25,
    outcome: "found",
    started_at: "2026-09-03T22:11:00+03:00",
    finished_at: "2026-09-03T22:16:30+03:00",
    log: log1,
    answer: {
      answer_he:
        "הפוד החדש של Elbit Systems משלב חיישן EO/IR ברזולוציה גבוהה עם מודול ATR מבוסס Edge AI [1]. " +
        "מפרט ראשוני שפורסם בהודעת החברה מציין טווח זיהוי מוגדל וצריכת הספק מופחתת [2]. " +
        "פטנט קשור שהוגש לאחרונה מתאר שיטת ייצוב גימבל חדשה התומכת בפלטפורמה זו [3]. " +
        "לא אותרו עדיין לקוחות רכש מוצהרים; ממליץ להמשיך מעקב.",
      sources: [
        { n: 1, item_id: 1, title: "Elbit Systems חושפת דור חדש של פודי כיוון", url: "https://example-source.test/articles/2000" },
        { n: 2, item_id: 5, title: "Company PR — מפרט הפוד החדש", url: "https://example-source.test/articles/2005" },
        { n: 3, item_id: 9, title: "פטנט חדש של Elbit Systems בתחום פודי כיוון", url: "https://example-source.test/articles/2009" },
      ],
      outcome: "found",
    },
  },
  {
    job_id: "inv-8842",
    item_id: 7,
    question: "האם יש חוזה רכש רשמי לאפקטור החדש של Anduril?",
    item_title: null,
    error: null,
    state: "running",
    rounds: 2,
    queries: 2,
    pages_read: 14,
    outcome: "in_progress",
    started_at: "2026-09-03T09:05:00+03:00",
    finished_at: null,
    log: log2,
    answer: null,
  },
];

export function findMockInvestigation(jobId: string): InvestigationDetail | undefined {
  return mockInvestigations.find((inv) => inv.job_id === jobId);
}
