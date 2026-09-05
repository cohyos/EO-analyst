import type { BdTerritoryOption, ReportDetail } from "@/types/api";

// A11 "דוח מיקוד לפיתוח עסקי, מכירה ושיווק לפי טריטוריה" -- mock data for VITE_USE_MOCKS.

export const mockBdTerritories: BdTerritoryOption[] = [
  { territory: "US", items: 18, tenders: 3, forecasts: 2, configured: true },
  { territory: "IL", items: 9, tenders: 1, forecasts: 1, configured: true },
  { territory: "EU", items: 6, tenders: 2, forecasts: 0, configured: true },
  { territory: "GB", items: 4, tenders: 0, forecasts: 1, configured: true },
  { territory: "IN", items: 3, tenders: 1, forecasts: 0, configured: true },
  { territory: "KR", items: 2, tenders: 0, forecasts: 0, configured: true },
  { territory: "AE", items: 1, tenders: 0, forecasts: 0, configured: false },
];

export const mockBdReports: ReportDetail[] = [
  {
    id: 901,
    kind: "bd_territory",
    territory: "US",
    period_start: "2026-06-08T00:00:00+03:00",
    period_end: "2026-09-06T00:00:00+03:00",
    path_docx: "/reports/bd_us_2026-09-06.docx",
    path_md: "/reports/bd_us_2026-09-06.md",
    path_html: "/reports/bd_us_2026-09-06.html",
    qa_passed: true,
    created_at: "2026-09-06T07:00:00+03:00",
    headline_count: 18,
    items_included: [101, 102, 103],
    open_points: [],
    html: `
      <h1>דוח מיקוד לפיתוח עסקי — US</h1>
      <section>
        <h2>תקציר מנהלים</h2>
        <p>
          צבא ארה"ב מקדם מכרז חדש לפוד כיוון (targeting pod) [1], ותוכנית נגד כטב"מים חדשה
          הוכרזה בארה"ב בחודשים האחרונים [2]. הפעולה הדחופה ביותר: יצירת קשר עם הגורם המזמין
          לקראת ה-RFI הימי הצפוי.
        </p>
      </section>
      <h2>תמונת שוק בטריטוריה</h2>
      <p>
        צבא ארה"ב מקדם מכרז לפוד כיוון חדש [1].<br/>
        תוכנית C-UAS חדשה הוכרזה בארה"ב [2].
      </p>
      <h2>נקודות כניסה ופעולות מומלצות</h2>
      <table>
        <tr><th>עדיפות</th><th>פעולה</th><th>נימוק</th><th>אחראי</th><th>תזמון</th></tr>
        <tr><td>גבוהה</td><td>ליזום פגישת היכרות עם US Navy לקראת ה-RFI הימי</td><td>נפתח RFI לכיוון ימי בארה"ב [3]</td><td>פיתוח עסקי</td><td>מיידי</td></tr>
        <tr><td>בינונית</td><td>להציג יכולות ג'ימבל בכנס AUSA הקרוב</td><td>Vendor X כבר פעילה בשוק הזה [2]</td><td>שיווק</td><td>רבעון הקרוב</td></tr>
      </table>
    `,
  },
  {
    id: 902,
    kind: "bd_territory",
    territory: "IL",
    period_start: "2026-06-08T00:00:00+03:00",
    period_end: "2026-09-06T00:00:00+03:00",
    path_docx: "/reports/bd_il_2026-09-06.docx",
    path_md: "/reports/bd_il_2026-09-06.md",
    path_html: "/reports/bd_il_2026-09-06.html",
    qa_passed: true,
    created_at: "2026-09-06T07:05:00+03:00",
    headline_count: 9,
    items_included: [201, 202],
    open_points: [],
    html: `
      <h1>דוח מיקוד לפיתוח עסקי — IL</h1>
      <section>
        <h2>תקציר מנהלים</h2>
        <p>אלביט מערכות ורפאל ממשיכות להוביל את השוק המקומי [1], עם התמקדות בפתרונות C-UAS [2].</p>
      </section>
    `,
  },
];
