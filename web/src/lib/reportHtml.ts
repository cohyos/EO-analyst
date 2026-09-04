/**
 * Turns bare `[n]` citation markers inside server-rendered report HTML into
 * clickable chips linking to the nth included item. The backend contract
 * (docs/API.md `GET /api/reports/{id}`) gives us `items_included` in report
 * order but no explicit n→item map for prose citations, so `n` is treated
 * as a 1-based index into `items_included` — the same convention the report
 * builder uses when emitting `[n]` while walking that list.
 */
export function linkifyReportCitations(
  html: string | null | undefined,
  itemsIncluded: number[] | null | undefined,
): string {
  const safeHtml = html ?? "";
  const items = itemsIncluded ?? [];
  return safeHtml.replace(/\[(\d+)\]/g, (match, nStr) => {
    const n = Number(nStr);
    const itemId = items[n - 1];
    if (!itemId) return match;
    return `<a class="eo-citation" href="/feed?open=${itemId}" title="פתח פריט מקור ${n}">${match}</a>`;
  });
}
