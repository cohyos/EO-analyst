import { useState, type MouseEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api";
import { linkifyReportCitations } from "@/lib/reportHtml";
import { formatDate } from "@/lib/time";

interface HoverState {
  itemId: number;
  x: number;
  y: number;
}

function CitationHoverCard({ itemId, x, y }: HoverState) {
  const { data, isLoading } = useQuery({
    queryKey: ["report-citation-item", itemId],
    queryFn: () => api.getItem(itemId),
    staleTime: 5 * 60_000,
  });

  return (
    <div
      role="tooltip"
      className="pointer-events-none fixed z-30 w-64 -translate-x-1/2 rounded-md border border-border-strong bg-bg-raised p-2 text-xs shadow-panel"
      style={{ insetInlineStart: x, top: y + 8 }}
    >
      {isLoading || !data ? (
        <span className="text-fg-dim">טוען…</span>
      ) : (
        <>
          <bdi className="block truncate font-medium text-fg">{data.title || "(ללא כותרת)"}</bdi>
          <div className="mt-0.5 flex items-center gap-1.5 text-fg-dim">
            <bdi className="min-w-0 truncate">{data.source_name || "—"}</bdi>
            <span>·</span>
            <span className="shrink-0 font-mono">{formatDate(data.published_at)}</span>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Renders server-produced report HTML (docs/API.md `GET /api/reports/{id}`) with `[n]` citation
 * markers turned into hover chips that resolve the cited source (via `GET
 * /api/reports/{id}/citations`) and, on click (U3, docs/REVIEW_2026-09-05.md), navigate to
 * `/items/:id` when the citation resolves to a real item, or open the source URL in a new tab
 * otherwise -- previously every `[n]` only ever linked to `/feed?open=...`, and citations added
 * only because a business event referenced an item outside `items_included` weren't linked at all.
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
  const navigate = useNavigate();

  const { data: citationsData } = useQuery({
    queryKey: ["report-citations", reportId],
    queryFn: () => api.getReportCitations(reportId),
    staleTime: 5 * 60_000,
  });

  const linked = linkifyReportCitations(html, citationsData?.citations);

  function citationTargetOf(e: MouseEvent<HTMLDivElement>): HTMLElement | null {
    return (e.target as HTMLElement).closest<HTMLElement>(".eo-citation");
  }

  function handleMouseOver(e: MouseEvent<HTMLDivElement>) {
    const target = citationTargetOf(e);
    if (!target) return;
    const idAttr = target.getAttribute("data-item-id");
    if (!idAttr) return;
    const rect = target.getBoundingClientRect();
    setHover({ itemId: Number(idAttr), x: rect.left + rect.width / 2, y: rect.bottom });
  }

  function handleMouseOut(e: MouseEvent<HTMLDivElement>) {
    const related = e.relatedTarget as HTMLElement | null;
    if (related?.closest(".eo-citation")) return;
    setHover(null);
  }

  function handleClick(e: MouseEvent<HTMLDivElement>) {
    const target = citationTargetOf(e);
    if (!target) return;
    const idAttr = target.getAttribute("data-item-id");
    if (idAttr) {
      // Intercept for a smooth in-app transition instead of the raw <a>'s full page reload —
      // the server HTML has no knowledge of the SPA router.
      e.preventDefault();
      navigate(`/items/${idAttr}`);
    }
    // No data-item-id but a data-url: let the raw `<a target="_blank">` handle it natively.
  }

  return (
    <div
      className="relative"
      onMouseOver={handleMouseOver}
      onMouseOut={handleMouseOut}
      onClick={handleClick}
    >
      <div className={className} dangerouslySetInnerHTML={{ __html: linked }} />
      {hover && <CitationHoverCard {...hover} />}
    </div>
  );
}
