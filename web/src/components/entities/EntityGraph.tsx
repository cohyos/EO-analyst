import { useEffect, useRef, useState } from "react";
import cytoscape, { type Core, type EdgeSingular, type NodeSingular } from "cytoscape";
import { useNavigate } from "react-router-dom";
import { Maximize2, X } from "lucide-react";
import type { GraphResponse } from "@/types/api";
import { entityKindLabel } from "@/components/entities/eventKindLabel";

// U10: node color by kind (with a legend) — real `entities.kind` values are
// company/program/system/person/org/country (migration 0007 widened the DB
// CHECK constraint to allow "country"; "org" covers agencies/ministries/NATO
// etc., labeled "סוכנות/ארגון" in the legend rather than renamed in the data).
const KIND_COLOR: Record<string, string> = {
  company: "#17909f",
  program: "#8a5cf5",
  system: "#4c9f70",
  person: "#d97a3f",
  org: "#c4561b",
  country: "#5b7fa6",
  default: "#8492a6",
};

function degreeMap(graph: GraphResponse): Map<number, number> {
  const deg = new Map<number, number>();
  for (const n of graph.nodes ?? []) deg.set(n.id, 0);
  for (const e of graph.edges ?? []) {
    deg.set(e.src, (deg.get(e.src) ?? 0) + 1);
    deg.set(e.dst, (deg.get(e.dst) ?? 0) + 1);
  }
  return deg;
}

export function EntityGraph({
  graph,
  focusEntityId,
  compact = false,
  onExpand,
}: {
  graph: GraphResponse;
  focusEntityId: number;
  /** Smaller canvas + lighter labels for the left/bottom panel (U10); the "פתח גרף
   * מלא" expander re-renders this same component at `compact=false` in a modal. */
  compact?: boolean;
  onExpand?: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const navigate = useNavigate();
  const [hoverEdge, setHoverEdge] = useState<{ label: string; x: number; y: number } | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<{
    label: string;
    evidence: string | null;
    item_id: number;
    src: string;
    dst: string;
  } | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const degrees = degreeMap(graph);
    const maxDegree = Math.max(1, ...Array.from(degrees.values()));
    const sizeFor = (id: number) => {
      const d = degrees.get(id) ?? 0;
      const min = compact ? 22 : 28;
      const max = compact ? 46 : 60;
      return min + (max - min) * Math.sqrt(d / maxDegree);
    };

    const cy = cytoscape({
      container: containerRef.current,
      elements: [
        ...(graph.nodes ?? []).map((n) => ({
          data: {
            id: String(n.id),
            label: n.name,
            kind: n.kind,
            size: sizeFor(n.id),
            focus: n.id === focusEntityId,
          },
        })),
        ...(graph.edges ?? []).map((e, i) => ({
          data: {
            id: `e${i}`,
            source: String(e.src),
            target: String(e.dst),
            label: e.label,
            item_id: e.item_id,
            evidence: e.evidence,
          },
        })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": (n: NodeSingular) => KIND_COLOR[n.data("kind")] ?? KIND_COLOR.default,
            shape: "ellipse",
            label: "data(label)",
            color: "#e6edee",
            "font-size": compact ? 8 : 10,
            "text-wrap": "wrap",
            "text-max-width": "70px",
            "text-valign": "bottom",
            "text-margin-y": 4,
            width: "data(size)",
            height: "data(size)",
            "border-width": (n: NodeSingular) => (n.data("focus") ? 3 : 0),
            "border-color": "#c4561b",
          },
        },
        {
          selector: "edge",
          style: {
            width: 1.5,
            "line-color": "#2c3d42",
            "target-arrow-color": "#2c3d42",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            label: compact ? "" : "data(label)",
            "font-size": 8,
            color: "#9fb0b4",
            "text-rotation": "autorotate",
          },
        },
        {
          selector: "edge:selected",
          style: { "line-color": "#17909f", "target-arrow-color": "#17909f", width: 2.5 },
        },
      ],
      layout: { name: "cose", animate: false, padding: compact ? 16 : 30 },
      autoungrabify: compact,
      userZoomingEnabled: !compact,
      userPanningEnabled: !compact,
      boxSelectionEnabled: false,
    });

    cy.on("tap", "node", (evt: cytoscape.EventObject) => {
      const n = evt.target as NodeSingular;
      const id = Number(n.id());
      if (!Number.isNaN(id)) navigate(`/entities/${id}`);
    });
    cy.on("tap", "edge", (evt: cytoscape.EventObject) => {
      const e = evt.target as EdgeSingular;
      setSelectedEdge({
        label: e.data("label"),
        evidence: e.data("evidence"),
        item_id: e.data("item_id"),
        src: e.source().data("label"),
        dst: e.target().data("label"),
      });
    });
    cy.on("mouseover", "edge", (evt: cytoscape.EventObject) => {
      const e = evt.target as EdgeSingular;
      const pos = e.midpoint();
      const rendered = e.cy().container()?.getBoundingClientRect();
      if (!rendered) return;
      setHoverEdge({ label: e.data("label"), x: pos.x, y: pos.y });
    });
    cy.on("mouseout", "edge", () => setHoverEdge(null));
    cy.on("tap", (evt: cytoscape.EventObject) => {
      if (evt.target === cy) setSelectedEdge(null);
    });

    cyRef.current = cy;
    return () => {
      cy.destroy();
    };
  }, [graph, focusEntityId, compact, navigate]);

  const kindsPresent = Array.from(new Set((graph.nodes ?? []).map((n) => n.kind).filter(Boolean)));

  return (
    <div
      className={`relative rounded-lg border border-border bg-bg-sunken ${compact ? "h-64" : "h-[32rem]"}`}
    >
      <div ref={containerRef} className="h-full w-full" role="img" aria-label="גרף ישויות" />

      {kindsPresent.length > 0 && (
        <div className="absolute top-2 start-2 flex flex-wrap gap-2 rounded-md bg-bg-raised/90 p-1.5 text-[10px] text-fg-muted shadow-panel">
          {kindsPresent.map((k) => (
            <span key={k} className="flex items-center gap-1">
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: KIND_COLOR[k] ?? KIND_COLOR.default }}
                aria-hidden="true"
              />
              {entityKindLabel(k)}
            </span>
          ))}
        </div>
      )}

      {hoverEdge && (
        <div
          className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded bg-bg-raised px-1.5 py-0.5 text-[10px] text-fg shadow-panel"
          style={{ left: hoverEdge.x, top: hoverEdge.y }}
        >
          {hoverEdge.label}
        </div>
      )}

      {compact && onExpand && (
        <button
          type="button"
          onClick={onExpand}
          className="absolute bottom-2 start-2 flex items-center gap-1 rounded-md border border-border-strong bg-bg-raised px-2 py-1 text-xs text-fg-muted shadow-panel hover:bg-bg-sunken"
        >
          <Maximize2 size={12} aria-hidden="true" />
          פתח גרף מלא
        </button>
      )}

      {selectedEdge && (
        <div className="absolute bottom-2 start-2 end-2 max-w-sm rounded-md border border-border-strong bg-bg-raised p-3 text-sm shadow-panel">
          <div className="mb-1 flex items-start justify-between gap-2">
            <bdi className="font-medium">
              {selectedEdge.src} → {selectedEdge.dst} ({selectedEdge.label})
            </bdi>
            <button
              type="button"
              onClick={() => setSelectedEdge(null)}
              aria-label="סגור"
              className="text-fg-dim hover:text-fg"
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
          {selectedEdge.evidence && (
            <bdi className="block text-xs text-fg-muted">{selectedEdge.evidence}</bdi>
          )}
          {selectedEdge.item_id != null && (
            <a
              href={`/feed?open=${selectedEdge.item_id}`}
              className="mt-1 inline-block text-xs text-accent hover:underline"
            >
              הצג פריט מקור →
            </a>
          )}
        </div>
      )}
    </div>
  );
}
