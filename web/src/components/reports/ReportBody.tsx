import { ContentShareActions } from "@/components/ContentShareActions";
import { useEffect, useMemo, useRef, useState, type FocusEvent, type MouseEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api";
import {
  enhanceSourceAppendixLinks,
  fixBdiSpacing,
  linkifyReportCitations,
  normalizeReportProse,
  wrapReportTables,
} from "@/lib/reportHtml";
import { SourcePreviewCard } from "@/components/SourcePreviewCard";

interface HoverState {
  itemId: number | null;
  url: string | null;
  title: string | null;
  x: number;
  y: number;
}

/**
 * R10-preview (2026-09-07, "read the summary before you're sent to the article"): the bare
 * title/source/date + "פתח מקור" this used to render inline is now the shared `SourcePreviewCard`
 * (summary_he, key facts, corroboration, product lines, "פתח פריט"/"פתח מקור" actions) -- same
 * `role="tooltip"` wrapper and fixed positioning as before, which is the contract
 * ReportBody.test.tsx and 09-reports.spec.ts assert against (a `role="tooltip"` element
 * containing a `target="_blank" rel="noopener noreferrer"` "פתח מקור" link once a citation
 * carries a URL).
 */
function CitationHoverCard({ itemId, url, title, x, y }: HoverState) {
  return (
    <div
      role="tooltip"
      className="pointer-events-auto fixed z-30 -translate-x-1/2 rounded-md border border-border-strong bg-bg-raised p-2.5 shadow-panel"
      style={{ insetInlineStart: x, top: y + 8 }}
    >
      <SourcePreviewCard itemId={itemId} fallback={{ title, url }} />
    </div>
  );
}

const HIGHLIGHT_MS = 2200;

/**
 * Renders server-produced report HTML (docs/API.md `GET /api/reports/{id}`) with `[n]` citation
 * markers turned into hover chips.
 *
 * W4 (docs/REVIEW_2026-09-06_evening.md round 4, superseding the U3 click-to-`/items/:id`
 * behavior below): clicking a `[n]` marker no longer navigates away -- it scrolls the report's own
 * sources appendix into view and highlights the cited row for a couple of seconds, exactly like a
 * footnote jump is expected to behave. Actually *opening* the source is now an explicit, always
 * available "פתח מקור" action: on hover/focus (`CitationHoverCard`, above), on the sources
 * appendix row itself while hovering it (same card, R10-preview below), and on the appendix row's
 * own always-visible link (`enhanceSourceAppendixLinks`) -- all `target="_blank" rel="noopener"`.
 */
export function ReportBody({
  html,
  reportId,
  className = "report-body text-sm",
}: {
  html: string;
  reportId: number;
  className?: string;
}) {
  const [hover, setHover] = useState<HoverState | null>(null);
  const highlightTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const { data: citationsData } = useQuery({
    queryKey: ["report-citations", reportId],
    queryFn: () => api.getReportCitations(reportId),
    staleTime: 5 * 60_000,
  });

  // Memoized in two layers, both required. React's DOM renderer diffs a host node's
  // `dangerouslySetInnerHTML` prop by *object reference* -- `lastProps.dangerouslySetInnerHTML
  // !== nextProps.dangerouslySetInnerHTML` -- before it ever looks at `.__html`, and JSX allocates
  // a brand-new `{ __html: ... }` object literal on every render no matter what string is inside
  // it. So even a `linked` string that's byte-for-byte identical across renders still gets a new
  // wrapper object each time, the reference check "changed", and React tears down and recreates
  // this node's entire DOM subtree.
  //
  // That subtree includes every citation `<a>` in the article, and `handleMouseOver` calls
  // `setHover` on essentially every pointer move over one -- a purely local state update with no
  // effect on `linked`'s *content*. Without memoizing the wrapper object too, that alone was
  // enough to detach the very element the pointer was hovering (and that a caller like
  // Playwright's `.hover()` holds a handle to) out from under itself mid-gesture: `hover()` would
  // see its target vanish and retry indefinitely against a page that keeps regenerating the same
  // node (09-reports.spec.ts, desktop, R6-ui). Memoizing `linked` on its actual inputs (`html`,
  // the citations map) keeps the *string* stable; memoizing the `{ __html }` object on `linked`
  // keeps the *prop React actually diffs* stable too -- both layers are needed, since it's the
  // outer object's identity, not the inner string's content, that React checks.
  const linked = useMemo(
    () =>
      wrapReportTables(
        fixBdiSpacing(enhanceSourceAppendixLinks(linkifyReportCitations(normalizeReportProse(html), citationsData?.citations))),
      ),
    [html, citationsData?.citations],
  );
  const dangerousHtml = useMemo(() => ({ __html: linked }), [linked]);

  useEffect(() => {
    return () => {
      if (highlightTimeout.current) clearTimeout(highlightTimeout.current);
    };
  }, []);

  function citationTargetOf(e: MouseEvent<HTMLDivElement>): HTMLElement | null {
    return (e.target as HTMLElement).closest<HTMLElement>(".eo-citation");
  }

  // R10-preview: hovering (or, via handleFocus/handleBlur below, keyboard-focusing) a `[n]`
  // marker shows the preview card as before; hovering the sources-appendix row a marker points to
  // shows the very same card, resolved from the same `report-citations` map keyed by the row's
  // `id="src-N"` rather than from `data-item-id`/`data-url` attributes (the appendix row itself
  // carries none -- only its inner "פתח מקור" link has a bare `href`). Appendix rows aren't
  // wired for keyboard focus here: the row's own always-visible link is already a normal,
  // independently focusable/actionable element.
  function hoverStateFromTarget(target: HTMLElement): HoverState | null {
    const citationEl = target.closest<HTMLElement>(".eo-citation");
    if (citationEl) {
      const idAttr = citationEl.getAttribute("data-item-id");
      const urlAttr = citationEl.getAttribute("data-url");
      if (!idAttr && !urlAttr) return null;
      const rect = citationEl.getBoundingClientRect();
      return {
        itemId: idAttr ? Number(idAttr) : null,
        url: urlAttr,
        title: citationEl.getAttribute("title"),
        x: rect.left + rect.width / 2,
        y: rect.bottom,
      };
    }
    const rowEl = target.closest<HTMLElement>('tr[id^="src-"]');
    if (rowEl) {
      const n = rowEl.id.slice("src-".length);
      const citation = citationsData?.citations?.[n];
      if (!citation || (citation.item_id == null && !citation.url)) return null;
      const rect = rowEl.getBoundingClientRect();
      return {
        itemId: citation.item_id,
        url: citation.url,
        title: citation.title,
        x: rect.left + 32,
        y: rect.top,
      };
    }
    return null;
  }

  function handleMouseOver(e: MouseEvent<HTMLDivElement>) {
    setHover(hoverStateFromTarget(e.target as HTMLElement));
  }

  function handleMouseOut(e: MouseEvent<HTMLDivElement>) {
    const related = e.relatedTarget as HTMLElement | null;
    if (related?.closest(".eo-citation") || related?.closest('tr[id^="src-"]')) return;
    setHover(null);
  }

  // Keyboard a11y for markers ("hover/focus tooltip"): a `[n]` anchor is a real, tabbable `<a>`,
  // so focusing it via Tab shows the same preview a mouse hover would. React's synthetic focus
  // events bubble (unlike native `focus`/`blur`), so this works the same delegated way as
  // mouseover/mouseout above.
  function handleFocus(e: FocusEvent<HTMLDivElement>) {
    const citationEl = (e.target as HTMLElement).closest<HTMLElement>(".eo-citation");
    if (!citationEl) return;
    setHover(hoverStateFromTarget(citationEl));
  }

  function handleBlur(e: FocusEvent<HTMLDivElement>) {
    const related = e.relatedTarget as HTMLElement | null;
    if (related?.closest(".eo-citation")) return;
    setHover(null);
  }

  function handleClick(e: MouseEvent<HTMLDivElement>) {
    const target = citationTargetOf(e);
    if (!target) return;
    const n = target.getAttribute("data-n");
    if (!n) return; // legacy bare-marker anchor (no appendix row to jump to) -- let it navigate natively
    e.preventDefault();
    const row = containerRef.current?.querySelector<HTMLElement>(`#src-${n}`);
    if (!row) return;
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("eo-appendix-highlight");
    if (highlightTimeout.current) clearTimeout(highlightTimeout.current);
    highlightTimeout.current = setTimeout(
      () => row.classList.remove("eo-appendix-highlight"),
      HIGHLIGHT_MS,
    );
  }

  return (
    <div
      data-share-content
      ref={containerRef}
      className="relative"
      onMouseOver={handleMouseOver}
      onMouseOut={handleMouseOut}
      onFocus={handleFocus}
      onBlur={handleBlur}
      onClick={handleClick}
    >
      <ContentShareActions />
      <div className={className} dangerouslySetInnerHTML={dangerousHtml} />
      {hover && <CitationHoverCard {...hover} />}
    </div>
  );
}
