// Tenders-page HTML share export (product request, 2026-09-15): a complete standalone HTML
// document listing the currently-visible tenders (and, on the forecasts tab, forecasts too) with
// working links, so it can be shared to a recipient without app access. Mirrors the columns/labels
// `TenderTable.tsx` already renders (Hebrew-fixed, same as that table -- this page has never been
// migrated to `t()` for its own content) so the export is WYSIWYG with what the operator sees;
// `locale` only affects date formatting and product-line names, both of which already have
// locale-aware helpers.
import type { TenderFiltersState } from "@/components/tenders/TenderFilters";
import { externalUrl } from "./shareContent";
import { TENDER_STATUS_LABEL, relevanceScorePercent, tenderSourceLabel } from "./tenders";
import { productLineLabel } from "./productLines";
import { formatDate, formatDateTime } from "./time";
import type { ForecastCard, TenderCard } from "@/types/api";

export interface TendersExportFilters extends TenderFiltersState {
  showClosedArchived: boolean;
}

export interface BuildTendersHtmlOptions {
  tenders: TenderCard[];
  forecasts?: ForecastCard[];
  filters: TendersExportFilters;
  locale: "he" | "en";
  appUrl: string;
  generatedAt: Date;
}

export function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Wraps an escaped value in `<bdi>` (English/Latin-bearing fields -- agency names, source
 * labels, country codes, product-line names -- can embed Latin text inside the RTL document). */
function bdi(value: string | null | undefined): string {
  const v = (value ?? "").trim();
  return v ? `<bdi>${escapeHtml(v)}</bdi>` : "—";
}

function linkedTitle(tender: TenderCard): string {
  const title = tender.title?.trim() || "(ללא כותרת)";
  const safe = `<bdi>${escapeHtml(title)}</bdi>`;
  const url = externalUrl(tender.url);
  return url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${safe}</a>` : safe;
}

function productLinesCell(tender: TenderCard, locale: "he" | "en"): string {
  const ids = tender.product_lines ?? [];
  if (ids.length === 0) return "—";
  return `<bdi>${ids.map((id) => escapeHtml(productLineLabel(id, locale))).join(", ")}</bdi>`;
}

function filtersSummary(filters: TendersExportFilters, locale: "he" | "en"): string {
  const parts: string[] = [];
  if (filters.status) parts.push(`סטטוס: ${TENDER_STATUS_LABEL[filters.status]}`);
  if (filters.country) parts.push(`מדינה: ${filters.country}`);
  if (filters.productLines.length > 0) {
    parts.push(
      `קו מוצר: ${filters.productLines.map((id) => productLineLabel(id, locale)).join(", ")}`,
    );
  }
  if (filters.q) parts.push(`חיפוש: "${filters.q}"`);
  parts.push(`כולל סגורים/ארכיון: ${filters.showClosedArchived ? "כן" : "לא"}`);
  return parts.map((p) => escapeHtml(p)).join(" · ");
}

const TABLE_COLUMNS = [
  "כותרת",
  "גוף מזמין",
  "מדינה",
  "סטטוס",
  "פורסם",
  "מועד סגירה",
  "מקור",
  "קו מוצר",
  "רלוונטיות",
];

function tenderTableRow(tender: TenderCard, locale: "he" | "en"): string {
  // No amount column: the tenders table has no value/currency field (notices rarely publish one),
  // so an always-empty column would only waste phone width.
  return `<tr>
    <td>${linkedTitle(tender)}</td>
    <td>${bdi(tender.agency)}</td>
    <td>${bdi(tender.country)}</td>
    <td>${escapeHtml(TENDER_STATUS_LABEL[tender.status])}</td>
    <td>${formatDate(tender.published_at, locale)}</td>
    <td>${formatDate(tender.deadline, locale)}</td>
    <td>${bdi(tenderSourceLabel(tender.source))}</td>
    <td>${productLinesCell(tender, locale)}</td>
    <td>${escapeHtml(relevanceScorePercent(tender.relevance_score))}</td>
  </tr>`;
}

function tenderCard(tender: TenderCard, locale: "he" | "en"): string {
  const rows: Array<[string, string]> = [
    ["כותרת", linkedTitle(tender)],
    ["גוף מזמין", bdi(tender.agency)],
    ["מדינה", bdi(tender.country)],
    ["סטטוס", escapeHtml(TENDER_STATUS_LABEL[tender.status])],
    ["פורסם", formatDate(tender.published_at, locale)],
    ["מועד סגירה", formatDate(tender.deadline, locale)],
    ["מקור", bdi(tenderSourceLabel(tender.source))],
    ["קו מוצר", productLinesCell(tender, locale)],
    ["רלוונטיות", escapeHtml(relevanceScorePercent(tender.relevance_score))],
  ];
  return `<div class="card">${rows
    .map(([label, value]) => `<div class="row"><span class="label">${label}</span><span class="value">${value}</span></div>`)
    .join("")}</div>`;
}

function forecastsSection(forecasts: ForecastCard[], locale: "he" | "en"): string {
  if (forecasts.length === 0) return "";
  const headers = ["פלטפורמה", "מדינת קונה", "צורך", "ספקים מועמדים", "סבירות", "חלון זמן", "מקורות"];
  const rows = forecasts
    .map((f) => {
      const window =
        f.window_from || f.window_to
          ? `${formatDate(f.window_from, locale)} – ${formatDate(f.window_to, locale)}`
          : "—";
      const sources =
        f.sources.length === 0
          ? "—"
          : f.sources
              .map((s) => {
                const url = externalUrl(s);
                return url
                  ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a>`
                  : escapeHtml(s);
              })
              .join("<br />");
      return `<tr>
        <td>${bdi(f.platform)}</td>
        <td>${bdi(f.buyer_country)}</td>
        <td><bdi>${escapeHtml(f.payload_need)}</bdi></td>
        <td>${f.candidate_vendors.length ? `<bdi>${escapeHtml(f.candidate_vendors.join(", "))}</bdi>` : "—"}</td>
        <td>${escapeHtml(relevanceScorePercent(f.likelihood))}</td>
        <td>${window}</td>
        <td>${sources}</td>
      </tr>`;
    })
    .join("");
  return `<section>
    <h2>תחזית מכרזים</h2>
    <p class="count">${forecasts.length} תחזיות</p>
    <div class="table-wrap">
      <table>
        <thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  </section>`;
}

const STYLE = `
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 24px;
    background: #ffffff;
    color: #14181f;
    font-family: system-ui, -apple-system, "Segoe UI", Arial, sans-serif;
    line-height: 1.5;
  }
  header { margin-bottom: 20px; }
  h1 { font-size: 1.4rem; margin: 0 0 8px; }
  h2 { font-size: 1.1rem; margin: 24px 0 8px; }
  .meta { margin: 2px 0; color: #4b5563; font-size: 0.85rem; }
  .count { margin: 8px 0; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
  th, td { border: 1px solid #d0d5dd; padding: 6px 8px; text-align: start; vertical-align: top; }
  th { background: #f3f4f6; font-weight: 600; }
  tbody tr:nth-child(even) { background: #fafafa; }
  a { color: #1d4ed8; }
  .cards { display: none; }
  .card {
    border: 1px solid #d0d5dd;
    border-radius: 8px;
    padding: 10px 12px;
    margin-bottom: 10px;
  }
  .card .row { display: flex; justify-content: space-between; gap: 8px; padding: 3px 0; font-size: 0.85rem; }
  .card .label { color: #6b7280; flex-shrink: 0; }
  .card .value { text-align: end; }
  footer { margin-top: 28px; padding-top: 12px; border-top: 1px solid #d0d5dd; font-size: 0.85rem; }
  @media (max-width: 640px) {
    .table-wrap { display: none; }
    .cards { display: block; }
  }
`;

/** Builds a COMPLETE standalone HTML document for the tenders board -- inline CSS only, no
 * external assets or scripts, so it works as a downloaded file / new-tab preview / pasted
 * clipboard payload with no dependency on the app being reachable. */
export function buildTendersHtml(options: BuildTendersHtmlOptions): string {
  const { tenders, forecasts, filters, locale, appUrl, generatedAt } = options;
  const title = "מכרזים והזדמנויות — EO-Analyst";
  return `<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${escapeHtml(title)}</title>
<style>${STYLE}</style>
</head>
<body>
<header>
  <h1>${escapeHtml(title)}</h1>
  <p class="meta">נוצר: ${formatDateTime(generatedAt.toISOString(), locale)}</p>
  <p class="meta">סינון: ${filtersSummary(filters, locale)}</p>
  <p class="count">${tenders.length} מכרזים בתצוגה</p>
</header>
<section>
  <div class="table-wrap">
    <table>
      <thead><tr>${TABLE_COLUMNS.map((c) => `<th>${c}</th>`).join("")}</tr></thead>
      <tbody>${tenders.map((t) => tenderTableRow(t, locale)).join("")}</tbody>
    </table>
  </div>
  <div class="cards">${tenders.map((t) => tenderCard(t, locale)).join("")}</div>
</section>
${forecasts ? forecastsSection(forecasts, locale) : ""}
<footer>
  <a href="${escapeHtml(appUrl)}">פתח באפליקציה (רשת פנימית)</a>
</footer>
</body>
</html>`;
}

/** Plain-text version for mail/WhatsApp bodies: one line per tender (title — buyer — deadline —
 * URL), capped so a long list never produces an unusably long message body. */
export function buildTendersShareText(tenders: TenderCard[], cap = 60): string {
  const shown = tenders.slice(0, cap);
  const lines = shown.map((tender) => {
    const title = tender.title?.trim() || "(ללא כותרת)";
    const buyer = tender.agency?.trim() || "—";
    const deadline = tender.deadline ? formatDate(tender.deadline) : "—";
    const url = externalUrl(tender.url) ?? "—";
    return `${title} — ${buyer} — ${deadline} — ${url}`;
  });
  if (tenders.length > cap) lines.push(`…ועוד ${tenders.length - cap}`);
  return lines.join("\n");
}
