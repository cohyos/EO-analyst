import type { ReportCitation } from "@/types/api";

/**
 * Turns bare `[n]` citation markers inside server-rendered report HTML into clickable chips.
 *
 * U3 (docs/REVIEW_2026-09-05.md): previously `n` was treated as a 1-based index into
 * `items_included` only, which covers the report's own item list but not citations
 * `eoa.report.daily._extend_citation_registry` adds solely because a business event referenced an
 * item outside that list -- those rendered as inert `[n]` text with no link and no tooltip at all.
 * `citations` (from `GET /api/reports/{id}/citations`) covers both, so every `[n]` this report
 * actually contains gets a chip: `data-item-id` when it resolves to a real item (the click handler
 * in `ReportBody` then navigates to `/items/:id`), or just `data-url` when it doesn't (opens the
 * source URL in a new tab instead).
 */
export function linkifyReportCitations(
  html: string | null | undefined,
  citations: Record<string, ReportCitation> | null | undefined,
): string {
  const safeHtml = html ?? "";
  const map = citations ?? {};
  return safeHtml.replace(/\[(\d+)\]/g, (match, nStr) => {
    const citation = map[nStr];
    if (!citation || (citation.item_id == null && !citation.url)) return match;
    const titleAttr = citation.title ? ` title="${escapeAttr(citation.title)}"` : "";
    if (citation.item_id != null) {
      return `<a class="eo-citation" data-item-id="${citation.item_id}" href="/items/${citation.item_id}"${titleAttr}>${match}</a>`;
    }
    return `<a class="eo-citation" data-url="${escapeAttr(citation.url!)}" href="${escapeAttr(citation.url!)}" target="_blank" rel="noopener noreferrer"${titleAttr}>${match}</a>`;
  });
}

function escapeAttr(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
