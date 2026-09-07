import { Link } from "react-router-dom";
import { X } from "lucide-react";
import type { GraphEdgeAgg, GraphNodeStats } from "@/types/api";
import { edgeLabelHe } from "@/components/entities/eventKindLabel";
import { formatDate } from "@/lib/time";

/** Double-click an edge -> this panel: the relation's weight/date range and up to 3 supporting
 * items, each linking straight to `/feed?open=`. */
export function EdgeEvidencePanel({
  edge,
  nodesById,
  onClose,
}: {
  edge: GraphEdgeAgg;
  nodesById: Map<number, GraphNodeStats>;
  onClose: () => void;
}) {
  const srcName = nodesById.get(edge.src)?.name ?? `#${edge.src}`;
  const dstName = nodesById.get(edge.dst)?.name ?? `#${edge.dst}`;
  return (
    <div
      role="dialog"
      aria-label="ראיות לקשר"
      className="absolute bottom-2 start-2 end-2 z-10 max-w-md rounded-md border border-border-strong bg-bg-raised p-3 text-sm shadow-panel"
    >
      <div className="mb-1.5 flex items-start justify-between gap-2">
        <bdi className="font-medium">
          {srcName} ← {edgeLabelHe(edge.relation)} ← {dstName}
        </bdi>
        <button
          type="button"
          onClick={onClose}
          aria-label="סגור"
          className="tap-target shrink-0 inline-flex items-center justify-center text-fg-dim hover:text-fg"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      <div className="mb-1.5 flex items-center gap-2 text-xs text-fg-dim">
        <span>משקל: {edge.weight}</span>
        {edge.first_seen && <span>מ-{formatDate(edge.first_seen)}</span>}
        {edge.last_seen && <span>עד {formatDate(edge.last_seen)}</span>}
      </div>
      {edge.evidence.length === 0 ? (
        <p className="text-xs text-fg-dim">אין פריטי מקור מקושרים.</p>
      ) : (
        <ul className="space-y-1">
          {edge.evidence.map((ev) => (
            <li key={ev.item_id}>
              <Link
                to={`/feed?open=${ev.item_id}`}
                className="block rounded-md p-1 text-xs hover:bg-bg-sunken"
              >
                <bdi className="block truncate">{ev.title ?? `פריט #${ev.item_id}`}</bdi>
                {ev.published_at && (
                  <span className="font-mono text-[10px] text-fg-dim">
                    {formatDate(ev.published_at)}
                  </span>
                )}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
