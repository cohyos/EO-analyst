import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Route, X } from "lucide-react";
import { api } from "@/api";
import type { GraphSearchResult } from "@/types/api";
import { entityKindLabel, edgeLabelHe } from "@/components/entities/eventKindLabel";
import { EmptyState, LoadingState } from "@/components/states";
import { EntitySearchBox } from "./EntitySearchBox";

/** "מוצא מסלולים" -- pick two entities, highlight the shortest path between them on the canvas
 * (`onPathFound`/`onClear` let the parent drive `GraphCanvas`'s `pathNodeIds`/`pathEdgeKeys`). */
export function PathFinderPanel({
  onPathFound,
  onClear,
  onClose,
}: {
  onPathFound: (nodeIds: number[], edgeKeys: string[]) => void;
  onClear: () => void;
  onClose: () => void;
}) {
  const [a, setA] = useState<GraphSearchResult | null>(null);
  const [b, setB] = useState<GraphSearchResult | null>(null);
  const [search, setSearch] = useState(false);

  const q = useQuery({
    queryKey: ["graph-path", a?.id, b?.id],
    queryFn: () => api.getGraphPath(a!.id, b!.id, 4),
    enabled: search && a != null && b != null,
  });

  // fires once per successful result -- parent owns the actual highlight state. A `useEffect`
  // (not a plain render-body call) so it never fires mid-render of this component.
  useEffect(() => {
    if (q.data && q.data.nodes.length > 0) {
      onPathFound(
        q.data.nodes.map((n) => n.id),
        q.data.edges.map((e) => `${e.src}-${e.dst}-${e.relation}`),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data]);

  return (
    <div
      data-testid="path-finder-panel"
      className="space-y-2 rounded-lg border border-border bg-bg-raised p-3 text-xs"
    >
      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-1.5 font-semibold text-fg-dim">
          <Route size={14} aria-hidden="true" />
          מוצא מסלולים
        </h3>
        <button
          type="button"
          onClick={() => {
            onClear();
            onClose();
          }}
          aria-label="סגור מוצא מסלולים"
          className="tap-target inline-flex items-center justify-center text-fg-dim hover:text-fg"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <div>
          <div className="mb-1 text-fg-dim">מ:</div>
          {a ? (
            <div className="flex items-center justify-between rounded-md bg-bg-sunken px-2 py-1">
              <bdi className="truncate">{a.name}</bdi>
              <button
                type="button"
                onClick={() => setA(null)}
                aria-label="נקה"
                className="text-fg-dim"
              >
                <X size={12} />
              </button>
            </div>
          ) : (
            <EntitySearchBox placeholder="חפש ישות מקור…" onSelect={(e) => setA(e)} />
          )}
        </div>
        <div>
          <div className="mb-1 text-fg-dim">אל:</div>
          {b ? (
            <div className="flex items-center justify-between rounded-md bg-bg-sunken px-2 py-1">
              <bdi className="truncate">{b.name}</bdi>
              <button
                type="button"
                onClick={() => setB(null)}
                aria-label="נקה"
                className="text-fg-dim"
              >
                <X size={12} />
              </button>
            </div>
          ) : (
            <EntitySearchBox placeholder="חפש ישות יעד…" onSelect={(e) => setB(e)} />
          )}
        </div>
      </div>

      <button
        type="button"
        disabled={!a || !b}
        onClick={() => setSearch(true)}
        className="rounded-md border border-accent bg-accent/15 px-2 py-1 text-accent disabled:opacity-40"
      >
        מצא מסלול
      </button>

      {search && q.isLoading && <LoadingState label="מחפש מסלול…" />}
      {search &&
        q.isFetched &&
        !q.isLoading &&
        (!q.data || q.data.nodes.length === 0) && (
          <EmptyState
            title="לא נמצא מסלול"
            description="אין קשר גרפי בין שתי הישויות בטווח 4 קפיצות."
          />
        )}
      {search && q.data && q.data.nodes.length > 0 && (
        <ol className="flex flex-wrap items-center gap-1">
          {q.data.nodes.map((n, i) => (
            <span key={n.id} className="flex items-center gap-1">
              <li className="rounded-md border border-border-strong bg-bg-sunken px-1.5 py-0.5">
                <bdi>{n.name}</bdi>
                <span className="ms-1 text-[10px] text-fg-dim">
                  {entityKindLabel(n.kind)}
                </span>
              </li>
              {i < q.data!.edges.length && (
                <span className="text-[10px] text-fg-dim">
                  ← {edgeLabelHe(q.data!.edges[i].relation)} ←
                </span>
              )}
            </span>
          ))}
        </ol>
      )}
    </div>
  );
}
