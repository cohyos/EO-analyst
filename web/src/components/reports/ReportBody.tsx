import { useState, type MouseEvent } from "react";
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
 * Renders server-produced report HTML (docs/API.md `GET /api/reports/{id}`)
 * with `[n]` citation markers turned into hover chips that resolve the
 * cited item's title/source/date via `GET /api/items/{id}` on demand
 * (deferred item from the previous pass — the markers previously linked out
 * with only a static "פתח פריט מקור n" title attribute, no source preview).
 */
export function ReportBody({
  html,
  itemsIncluded,
  className = "report-body text-sm",
}: {
  html: string;
  itemsIncluded: number[];
  className?: string;
}) {
  const [hover, setHover] = useState<HoverState | null>(null);
  const linked = linkifyReportCitations(html, itemsIncluded);

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

  return (
    <div className="relative" onMouseOver={handleMouseOver} onMouseOut={handleMouseOut}>
      <div className={className} dangerouslySetInnerHTML={{ __html: linked }} />
      {hover && <CitationHoverCard {...hover} />}
    </div>
  );
}
