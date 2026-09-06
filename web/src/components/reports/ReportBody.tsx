import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { api } from "@/api";
import { enhanceSourceAppendixLinks, linkifyReportCitations } from "@/lib/reportHtml";
import { formatDate } from "@/lib/time";

interface HoverState {
  itemId: number | null;
  url: string | null;
  x: number;
  y: number;
}

function CitationHoverCard({ itemId, url, x, y }: HoverState) {
  const { data, isLoading } = useQuery({
    queryKey: ["report-citation-item", itemId],
    queryFn: () => api.getItem(itemId!),
    enabled: itemId != null,
    staleTime: 5 * 60_000,
  });

  return (
    <div
      role="tooltip"
      className="pointer-events-auto fixed z-30 w-64 -translate-x-1/2 rounded-md border border-border-strong bg-bg-raised p-2 text-xs shadow-panel"
      style={{ insetInlineStart: x, top: y + 8 }}
    >
      {itemId != null && (isLoading || !data) ? (
        <span className="text-fg-dim">טוען…</span>
      ) : itemId != null && data ? (
        <>
          <bdi className="block truncate font-medium text-fg">
            {data.title || "(ללא כותרת)"}
          </bdi>
          <div className="mt-0.5 flex items-center gap-1.5 text-fg-dim">
            <bdi className="min-w-0 truncate">{data.source_name || "—"}</bdi>
            <span>·</span>
            <span className="shrink-0 font-mono">{formatDate(data.published_at)}</span>
          </div>
        </>
      ) : null}
      {/* W4: the tooltip's real job -- a direct, always-visible way to open the actual source,
          independent of whether the citation also resolves to an internal item page. */}
      {url && (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-1.5 flex items-center gap-1 font-medium text-accent hover:underline"
        >
          <ExternalLink size={11} aria-hidden="true" />
          פתח מקור
        </a>
      )}
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
 * available "פתח מקור" action: on hover (`CitationHoverCard`, above) and on the appendix row
 * itself (`enhanceSourceAppendixLinks`), both `target="_blank" rel="noopener"`.
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
    () => enhanceSourceAppendixLinks(linkifyReportCitations(html, citationsData?.citations)),
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

  function handleMouseOver(e: MouseEvent<HTMLDivElement>) {
    const target = citationTargetOf(e);
    if (!target) {
      setHover(null);
      return;
    }
    const idAttr = target.getAttribute("data-item-id");
    const urlAttr = target.getAttribute("data-url");
    if (!idAttr && !urlAttr) return;
    const rect = target.getBoundingClientRect();
    setHover({
      itemId: idAttr ? Number(idAttr) : null,
      url: urlAttr,
      x: rect.left + rect.width / 2,
      y: rect.bottom,
    });
  }

  function handleMouseOut(e: MouseEvent<HTMLDivElement>) {
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
      ref={containerRef}
      className="relative"
      onMouseOver={handleMouseOver}
      onMouseOut={handleMouseOut}
      onClick={handleClick}
    >
      <div className={className} dangerouslySetInnerHTML={dangerousHtml} />
      {hover && <CitationHoverCard {...hover} />}
    </div>
  );
}
