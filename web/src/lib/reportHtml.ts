import type { ReportCitation } from "@/types/api";

/** Display old report prose without raw Markdown heading markers; never interpret new HTML. */
export function normalizeReportProse(html: string): string {
  if (!/(^|\s)#{2,6}\s/.test(html)) return html;
  const doc = new DOMParser().parseFromString(html, "text/html");
  const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  while (walker.nextNode()) nodes.push(walker.currentNode as Text);
  for (const node of nodes) {
    if (!node.parentElement?.closest("li, p") || node.parentElement.closest("a, code, pre")) continue;
    const parts = (node.textContent ?? "").split(/(?:^|\s)#{2,6}\s+/);
    if (parts.length < 2) continue;
    const fragment = doc.createDocumentFragment();
    parts.forEach((part, i) => {
      if (i) fragment.append(doc.createElement("br"));
      fragment.append(doc.createTextNode(part));
    });
    node.replaceWith(fragment);
  }
  return doc.body.innerHTML;
}

/**
 * Turns `[n]` citation markers inside server-rendered report HTML into clickable chips.
 *
 * U3 (docs/REVIEW_2026-09-05.md): previously `n` was treated as a 1-based index into
 * `items_included` only, which covers the report's own item list but not citations
 * `eoa.report.daily._extend_citation_registry` adds solely because a business event referenced an
 * item outside that list -- those rendered as inert `[n]` text with no link and no tooltip at all.
 * `citations` (from `GET /api/reports/{id}/citations`) covers both, so every `[n]` this report
 * actually contains gets a chip: `data-item-id` when it resolves to a real item and/or `data-url`
 * when a source URL is known (both can be set at once -- see W4 below).
 *
 * F23: `eoa.report.docx_builder.render_html` now ships every `[n]` pre-wrapped as
 * `<a href="#src-n" class="cite">[n]</a>` (an in-page jump to the sources appendix row), so this
 * can no longer blindly regex-replace every `[n]` substring -- doing so would nest a second `<a>`
 * inside the server's anchor, which is invalid HTML and breaks the `.closest(".eo-citation")`
 * lookup in `ReportBody`'s click handler. Anchors already carrying `class="cite"` are augmented in
 * place (kept as one `<a>`, `href="#src-n"` preserved as the fallback target); only a genuinely
 * bare `[n]` -- as still produced by reports rendered before this fix and stored in the DB -- gets
 * wrapped in a brand-new anchor.
 *
 * W4 (docs/REVIEW_2026-09-06_evening.md, round 4): the F23/U3 click behavior above sent every
 * resolved `[n]` straight to `/items/:id`, an internal page -- so clicking a footnote never
 * actually opened the cited source, which is exactly what the round-4 finding complained about.
 * `[n]` now always carries `data-n="<n>"` (so `ReportBody` can find its `#src-n` appendix row
 * without re-parsing the marker text) and, whenever the citation has a `url` -- even one that also
 * resolves to a real item -- `data-url` too, so the hover tooltip and the click handler both have
 * the real source URL regardless of whether an internal item page also exists for it. Clicking the
 * marker itself now scrolls to and highlights the appendix row (`ReportBody`'s job); opening the
 * source is a dedicated "פתח מקור" link surfaced both in the hover tooltip and (via
 * `enhanceSourceAppendixLinks` below) on the appendix row.
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
    (
      match,
      pre: string | undefined,
      post: string | undefined,
      anchoredN: string | undefined,
      bareN: string | undefined,
    ) => {
      if (anchoredN !== undefined) {
        // Already anchored to the appendix (`href="#src-n"`, preserved via `pre`/`post`) --
        // augment in place with the eo-citation class + data attributes instead of nesting a
        // second <a>. An unresolved n (not in `citations`) is left exactly as the server sent it;
        // its href still jumps to the appendix row, which is a fine fallback on its own.
        const citation = map[anchoredN];
        if (!citation) return match;
        const titleAttr = citation.title ? ` title="${escapeAttr(citation.title)}"` : "";
        // W4: item-id and url are independent facts about the same citation -- set both
        // attributes whenever known, rather than only one or the other, so `ReportBody` always
        // has the real source URL to offer via "פתח מקור" even when the citation also resolves
        // to an internal item.
        const itemAttr =
          citation.item_id != null ? ` data-item-id="${citation.item_id}"` : "";
        const urlAttr = citation.url ? ` data-url="${escapeAttr(citation.url)}"` : "";
        return `<a${pre}class="cite eo-citation"${post} data-n="${anchoredN}"${itemAttr}${urlAttr}${titleAttr}>[${anchoredN}]</a>`;
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

// Round-3 mobile fix (UI-MOBILE-iphone-r3.md #6): 2+ `.eo-citation` anchors separated only by
// whitespace (the common "…fact [1] [3] [2]." shape `linkifyReportCitations` above produces for a
// sentence with multiple sources) -- captures the run so it can be wrapped as one bidi-isolated
// unit below.
const CITATION_ANCHOR_RE = /<a\b[^>]*\bclass="[^"]*\beo-citation\b[^"]*"[^>]*>\[\d+\]<\/a>/;
const CITATION_GROUP_RE = new RegExp(
  `([ \\t]*)((?:${CITATION_ANCHOR_RE.source}[ \\t]+){1,}${CITATION_ANCHOR_RE.source})`,
  "g",
);

/**
 * Groups 2+ adjacent citation markers into a single `dir="ltr"` span.
 *
 * Content review (docs/qa/content_review/UI-MOBILE-iphone-r3.md #6): three separate anchor
 * elements sitting in RTL prose with nothing but whitespace between them are exactly the shape the
 * Unicode bidi algorithm reorders as a run of embedded LTR objects -- "[1] [2] [3]" as authored
 * rendered as "[1] [3] [2]" on screen, since each `<a>` is its own atomic embedding and the
 * *sequence* of embeddings (not their internal content) gets right-to-left-ordered by the
 * surrounding paragraph direction. Wrapping the whole run in one `dir="ltr"` container makes it a
 * single embedding instead of three, so its own internal left-to-right order (1, 2, 3) is
 * preserved; the *group* as a whole still gets correctly placed within the RTL paragraph. The
 * leading run of whitespace immediately before the group is replaced with `&nbsp;` (rather than
 * dropped) so the group stays glued to the word it follows instead of ever orphaning onto its own
 * line as a bare leading run when the paragraph wraps.
 */
export function groupAdjacentCitations(html: string | null | undefined): string {
  const safeHtml = html ?? "";
  return safeHtml.replace(CITATION_GROUP_RE, (_match, leadingSpace: string, group: string) => {
    const glue = leadingSpace ? "&nbsp;" : "";
    return `${glue}<span dir="ltr" class="eo-citation-group">${group}</span>`;
  });
}

function escapeAttr(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// One appendix row, as `docx_builder.render_html` emits it: `<tr id="src-N">...<td>` link column
// `</td></tr>`, the link column being either "—" (no url) or `_html_link()`'s
// `<a href="URL"><bdi dir="ltr">URL</bdi></a>` -- the only `<a>` in the row, since the title/source
// columns are plain (non-linked) `_bidi_html` text.
const APPENDIX_ROW_RE =
  /(<tr id="src-\d+">[\s\S]*?)<a href="([^"]+)"([^>]*)>[\s\S]*?<\/a>([\s\S]*?<\/tr>)/g;

/**
 * W4: the sources-appendix link `eoa.report.docx_builder.render_html` (`agent/eoa/report/**`, out
 * of scope for this UI-only pass) emits per row has neither `target="_blank"`/`rel="noopener"` nor
 * a real "פתח מקור" label -- it's just the raw URL as its own link text. Rewritten here at the
 * frontend boundary instead: same href, opens in a new tab, and reads as an explicit action rather
 * than a URL to parse. The original URL is kept as the link's `title` (hover) so it's still
 * inspectable before opening.
 */
export function enhanceSourceAppendixLinks(html: string | null | undefined): string {
  const safeHtml = html ?? "";
  return safeHtml.replace(
    APPENDIX_ROW_RE,
    (_match, before: string, url: string, extraAttrs: string, after: string) => {
      if (extraAttrs.includes("data-appendix-link")) return _match; // already enhanced, don't double-wrap
      // `url` was captured straight out of the server's `href="..."` attribute, so it's already
      // HTML-attribute-escaped (e.g. a literal `&` arrives as `&amp;`) -- reused as-is for both
      // `href` and `title` below, not re-escaped (which would turn `&amp;` into `&amp;amp;`).
      return (
        `${before}<a href="${url}" target="_blank" rel="noopener noreferrer" data-appendix-link="1" ` +
        `class="source-open-link" title="${url}">` +
        `<span aria-hidden="true">↗</span> פתח מקור</a>${after}`
      );
    },
  );
}

// Content review (docs/qa/content_review/CR-ui.md): `docx_builder.render_html` (Python side, out
// of scope for this UI-only pass) wraps every embedded Latin/number run in
// `<bdi dir="ltr">…</bdi>` for correct bidi isolation, e.g. `<bdi dir="ltr">JFB </bdi>האמריקאית`
// -- but the run's separating space sits *inside* the tag. An isolate is atomic: that space ends
// up glued to the LTR content's own trailing edge instead of separating it from the Hebrew word
// that follows, so "JFB האמריקאית" visually renders as "JFBהאמריקאית" with no gap at all. Moving
// the leading/trailing whitespace to outside the tag (same position in the flow, just no longer
// inside the isolated run) fixes the spacing everywhere this pattern occurs in report bodies.
const BDI_TRAILING_SPACE_RE = /(<bdi\b[^>]*>)([^<]*?)(\s+)(<\/bdi>)/g;
const BDI_LEADING_SPACE_RE = /(<bdi\b[^>]*>)(\s+)([^<]*?)(<\/bdi>)/g;

export function fixBdiSpacing(html: string | null | undefined): string {
  const safeHtml = html ?? "";
  return safeHtml
    .replace(BDI_TRAILING_SPACE_RE, (_m, open, text, space, close) => `${open}${text}${close}${space}`)
    .replace(BDI_LEADING_SPACE_RE, (_m, open, space, text, close) => `${space}${open}${text}${close}`);
}

// Content review: report tables (transaction ledgers, the sources appendix) ship as bare
// `<table>` markup with nothing around them to scroll -- on a narrow viewport they either
// overflow the page or get their columns crushed unreadably thin. Wraps each top-level `<table>`
// in `.report-table-wrap` (globals.css: `overflow-x: auto`, a `min-width` on the table itself so
// there's actually something to scroll) so it scrolls locally instead. Matches `<table ...>` with
// any attributes and its balanced `</table>` close; report tables never nest a `<table>` inside
// another, so a plain non-greedy match is safe here.
const TABLE_RE = /<table\b[^>]*>[\s\S]*?<\/table>/g;

// Round-2 mobile fix (UI-MOBILE-iphone.md #1): a table's first row (header if present, else the
// first body row) tells us how many columns it has. Regex-counted, not DOM-parsed -- report
// tables never use colspan/rowspan (they're flat ledgers/appendices, see the module-level notes
// above on why a plain match is already safe for this content) -- so counting `<th>`/`<td>` opens
// in the first `<tr>...</tr>` is an accurate, cheap proxy for column count.
const FIRST_ROW_RE = /<tr\b[^>]*>([\s\S]*?)<\/tr>/;
const CELL_OPEN_RE = /<t[hd]\b[^>]*>/g;

function countTableColumns(tableHtml: string): number {
  const rowMatch = FIRST_ROW_RE.exec(tableHtml);
  if (!rowMatch) return 0;
  return rowMatch[1].match(CELL_OPEN_RE)?.length ?? 0;
}

// A wide table (>4 columns) gets a one-line scroll hint above it -- on a phone there's no other
// affordance telling the analyst the table keeps going sideways. `aria-hidden` since the wrapper
// itself already scrolls via a real, keyboard/AT-operable mechanism; this is a purely visual nudge.
const SCROLL_HINT_HTML =
  '<span class="report-table-scroll-hint" aria-hidden="true">→ גלול לרוחב לצפייה בכל העמודות</span>';

export function wrapReportTables(html: string | null | undefined): string {
  const safeHtml = html ?? "";
  return safeHtml.replace(TABLE_RE, (match) => {
    const colCount = countTableColumns(match);
    // Round-2 #1: a table with <= 4 columns doesn't need to scroll at all once its cells are
    // allowed to wrap -- `.report-table-wrap--narrow` (globals.css) overrides the table's own
    // min-width/nowrap so it reflows like ordinary prose instead of forcing a horizontal
    // scrollbar. A wider table (or one whose column count couldn't be determined) keeps the
    // existing scroll-locally behavior, now with an explicit hint that it scrolls.
    if (colCount > 0 && colCount <= 4) {
      return `<div class="report-table-wrap report-table-wrap--narrow" dir="rtl">${match}</div>`;
    }
    return `<div class="report-table-wrap" dir="rtl">${SCROLL_HINT_HTML}${match}</div>`;
  });
}

// Content review: `addHeadingIds` (ReportsPage.tsx) strips tags from a heading's inner HTML to
// build the TOC's plain-text label, but doesn't decode entities -- a heading like
// `Airborne Pods &amp; Payloads` (correctly HTML-escaped by the server, decodes fine when the
// heading itself renders via `dangerouslySetInnerHTML`) came out of the tag-strip as the literal
// string `Airborne Pods &amp; Payloads`, which React then escaped *again* as a plain text child,
// rendering the raw entity text `&amp;` on screen instead of `&`. Covers the handful of named
// entities report prose actually uses plus numeric/hex entities; anything else is left as-is
// rather than guessed at.
const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
};

export function decodeHtmlEntities(s: string): string {
  return s.replace(/&(#\d+|#x[0-9a-fA-F]+|[a-zA-Z]+);/g, (match, ent: string) => {
    if (ent[0] === "#") {
      const isHex = ent[1] === "x" || ent[1] === "X";
      const code = isHex ? parseInt(ent.slice(2), 16) : parseInt(ent.slice(1), 10);
      return Number.isNaN(code) ? match : String.fromCodePoint(code);
    }
    return NAMED_ENTITIES[ent] ?? match;
  });
}
