import type { ReportDetail } from "@/types/api";

export const mockReport: ReportDetail = {
  id: 501,
  kind: "daily",
  period_start: "2026-09-03T20:00:00+03:00",
  period_end: "2026-09-04T06:00:00+03:00",
  path_docx: "/reports/2026-09-04-daily.docx",
  path_md: "/reports/2026-09-04-daily.md",
  path_html: "/reports/2026-09-04-daily.html",
  qa_passed: true,
  created_at: "2026-09-04T06:05:00+03:00",
  headline_count: 3,
  territory: null,
  items_included: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
  open_points: [
    {
      id: 1,
      question: "האם לסווג את פעילות SPARK Vision Labs כ'סטארטאפ מחובר' לרפאל?",
      options: ["כן, קשר מבוסס", "לא, קשר עקיף בלבד", "נדרש מידע נוסף"],
      answer: null,
      assumed: false,
    },
    {
      id: 2,
      question: "הפוד החדש של Elbit — לשייך לתת-תחום 'פודי כיוון' או 'פודי ISR'?",
      options: ["פודי כיוון", "פודי ISR", "שני התחומים"],
      answer: "פודי כיוון",
      assumed: true,
    },
  ],
  html: `
    <section>
      <h2>תקציר מנהלים</h2>
      <p>
        הלילה עובדו 40 פריטים חדשים, מתוכם 6 בדרגת <bdi>קריטי</bdi> ו-11 בדרגת <bdi>חשוב</bdi>.
        עיקר הפעילות מתרכזת בתחום פודי הכיוון והגימבלים האוויריים, עם דגש על
        <bdi>Elbit Systems</bdi> [1] ו-<bdi>Rafael Advanced Defense Systems</bdi> [2].
        זוהתה מגמת האצה בפיתוח יכולות Edge AI לזיהוי מטרות אוטומטי (ATR) [3],
        לצד עדכוני רגולציה על ייצוא רכיבי EO/IR באירופה [4].
      </p>
      <h3>נקודות מפתח</h3>
      <ul>
        <li>Elbit Systems חושפת דור חדש של פודי כיוון עם מודול ATR מבוסס Edge AI [1]</li>
        <li>Anduril Industries בתהליך התקשרות אפשרי לאפקטור C-UAS חדש [5]</li>
        <li>עדכון רגולציית ייצוא באירופה עשוי להשפיע על Safran ו-HENSOLDT [4]</li>
      </ul>
    </section>
  `,
};
