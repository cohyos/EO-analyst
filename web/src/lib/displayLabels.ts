import type { Locale } from "@/i18n";

const JOBS: Record<string, [string, string]> = {
  product_dossier: ["סקירת מוצר", "Product dossier"], daily_run: ["מחזור יומי", "Daily run"],
  weekly_run: ["מחזור שבועי", "Weekly run"], monthly_run: ["מחזור חודשי", "Monthly run"],
  ingest: ["קליטת מקורות", "Source ingestion"], deep_search: ["חקירת עומק", "Investigation"],
  report: ["הפקת דוח", "Report"], bd_report: ["דוח פיתוח עסקי", "Business development report"],
  product_line_report: ["דוח קו מוצר", "Product line report"], patent_scan: ["סריקת פטנטים", "Patent scan"],
  payload_extract: ["חילוץ מפרטי מטע״דים", "Payload specifications"],
  conference_scan: ["סריקת כנסים", "Conference scan"],
};
const SOURCES: Record<string, [string, string]> = {
  official: ["מקור רשמי", "Official source"], vendor_official: ["אתר היצרן", "Manufacturer"],
  datasheet: ["דף נתונים", "Datasheet"], brochure: ["עלון מוצר", "Brochure"],
  article: ["כתבה", "Article"], press: ["עיתונות", "Press"], trade_press: ["עיתונות מקצועית", "Trade press"],
  contract: ["חוזה", "Contract"], tender: ["מכרז", "Tender"], budget: ["מסמך תקציב", "Budget"],
  other: ["אחר", "Other"], unknown: ["לא ידוע", "Unknown"],
};
export function jobLabel(kind: string, locale: Locale = "he"): string {
  return JOBS[kind]?.[locale === "en" ? 1 : 0] ?? kind.replace(/_/g, " ");
}
export function sourceKindLabel(kind: string | null | undefined, locale: Locale = "he"): string | null {
  return kind ? SOURCES[kind]?.[locale === "en" ? 1 : 0] ?? kind.replace(/_/g, " ") : null;
}
