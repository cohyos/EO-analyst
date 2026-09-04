import { useEffect, useRef, useState } from "react";
import cytoscape, { type Core, type EdgeSingular, type NodeSingular } from "cytoscape";
import { Link } from "react-router-dom";
import type { GraphResponse } from "@/types/api";
import { X } from "lucide-react";

const KIND_COLOR: Record<string, string> = {
  company: "#17909f",
  government: "#d97a3f",
  default: "#8492a6",
};

const KIND_SHAPE: Record<string, string> = {
  company: "ellipse",
  government: "round-rectangle",
  default: "diamond",
};

export function EntityGraph({
  graph,
  focusEntityId,
}: {
  graph: GraphResponse;
  focusEntityId: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<{
    label: string;
    evidence: string | null;
    item_id: number;
    src: string;
    dst: string;
  } | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [
        ...(graph.nodes ?? []).map((n) => ({
          data: {
            id: String(n.id),
            label: n.name,
            kind: n.kind,
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
            "background-color": (n: NodeSingular) =>
              KIND_COLOR[n.data("kind")] ?? KIND_COLOR.default,
            shape: (n: NodeSingular) =>
              (KIND_SHAPE[n.data("kind")] ?? KIND_SHAPE.default) as never,
            label: "data(label)",
            color: "#e6edee",
            "font-size": 10,
            "text-wrap": "wrap",
            "text-max-width": "80px",
            "text-valign": "bottom",
            "text-margin-y": 4,
            width: 34,
            height: 34,
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
            label: "data(label)",
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
      layout: { name: "cose", animate: false, padding: 30 },
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
    cy.on("tap", (evt: cytoscape.EventObject) => {
      if (evt.target === cy) setSelectedEdge(null);
    });

    cyRef.current = cy;
    return () => {
      cy.destroy();
    };
  }, [graph, focusEntityId]);

  return (
    <div className="relative h-[28rem] rounded-lg border border-border bg-bg-sunken">
      <div ref={containerRef} className="h-full w-full" role="img" aria-label="גרף ישויות" />
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
          <Link
            to={`/feed?open=${selectedEdge.item_id}`}
            className="mt-1 inline-block text-xs text-accent hover:underline"
          >
            הצג פריט מקור →
          </Link>
        </div>
      )}
    </div>
  );
}
