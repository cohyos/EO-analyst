import type { ReportCitation } from "@/types/api";

/**
 * Turns `[n]` citation markers inside server-rendered report HTML into clickable chips.
 *
 * U3 (docs/REVIEW_2026-09-05.md): previously `n` was treated as a 1-based index into
 * `items_included` only, which covers the report's own item list but not citations
 * `eoa.report.daily._extend_citation_registry` adds solely because a business event referenced an
 * item outside that list -- those rendered as inert `[n]` text with no link and no tooltip at all.
 * `citations` (from `GET /api/reports/{id}/citations`) covers both, so every `[n]` this report
 * actually contains gets a chip: `data-item-id` when it resolves to a real item (the click handler
 * in `ReportBody` then navigates to `/items/:id`), or just `data-url` when it doesn't (opens the
 * source URL in a new tab instead).
 *
 * F23: `eoa.report.docx_builder.render_html` now ships every `[n]` pre-wrapped as
 * `<a href="#src-n" class="cite">[n]</a>` (an in-page jump to the sources appendix row), so this
 * can no longer blindly regex-replace every `[n]` substring -- doing so would nest a second `<a>`
 * inside the server's anchor, which is invalid HTML and breaks the `.closest(".eo-citation")`
 * lookup in `ReportBody`'s click handler. Anchors already carrying `class="cite"` are augmented in
 * place (kept as one `<a>`, `href="#src-n"` preserved as the fallback target); only a genuinely
 * bare `[n]` -- as still produced by reports rendered before this fix and stored in the DB -- gets
 * wrapped in a brand-new anchor.
 */
// Either a server-rendered `<a ... class="cite">[n]</a>` anchor (group 1/2/3) or a bare `[n]`
// marker (group 4) from an older stored report. A single alternation in one global regex, scanned
// left-to-right with non-overlapping matches, so once the anchor branch consumes a whole
// `<a>...</a>` the `[n]` text inside it is never re-visited by the bare-marker branch.
const CITATION_RE = /<a\b([^>]*)\bclass="cite"([^>]*)>\[(\d+)\]<\/a>|\[(\d+)\]/g;

export function linkifyReportCitations(
  html: string | null | undefined,
  citations: Record<string, ReportCitation> | null | undefined,
): string {
  const safeHtml = html ?? "";
  const map = citations ?? {};

  return safeHtml.replace(
    CITATION_RE,
    (match, pre: string | undefined, post: string | undefined, anchoredN: string | undefined, bareN: string | undefined) => {
      if (anchoredN !== undefined) {
        // Already anchored to the appendix (`href="#src-n"`, preserved via `pre`/`post`) --
        // augment in place with the eo-citation class + data attributes instead of nesting a
        // second <a>. An unresolved n (not in `citations`) is left exactly as the server sent it;
        // its href still jumps to the appendix row, which is a fine fallback on its own.
        const citation = map[anchoredN];
        if (!citation) return match;
        const titleAttr = citation.title ? ` title="${escapeAttr(citation.title)}"` : "";
        const dataAttr =
          citation.item_id != null
            ? ` data-item-id="${citation.item_id}"`
            : citation.url
              ? ` data-url="${escapeAttr(citation.url)}"`
              : "";
        return `<a${pre}class="cite eo-citation"${post}${dataAttr}${titleAttr}>[${anchoredN}]</a>`;
      }

      const nStr = bareN!;
      const citation = map[nStr];
      if (!citation || (citation.item_id == null && !citation.url)) return match;
      const titleAttr = citation.title ? ` title="${escapeAttr(citation.title)}"` : "";
      if (citation.item_id != null) {
        return `<a class="eo-citation" data-item-id="${citation.item_id}" href="/items/${citation.item_id}"${titleAttr}>${match}</a>`;
      }
      return `<a class="eo-citation" data-url="${escapeAttr(citation.url!)}" href="${escapeAttr(citation.url!)}" target="_blank" rel="noopener noreferrer"${titleAttr}>${match}</a>`;
    },
  );
}

function escapeAttr(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
