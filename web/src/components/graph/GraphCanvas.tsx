import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import cytoscape, { type Core, type EdgeSingular, type NodeSingular } from "cytoscape";
import type { GraphEdgeAgg, GraphNodeStats } from "@/types/api";
import { kindColor } from "./graphColors";

export type GraphLayoutName = "cose" | "concentric" | "breadthfirst";

export interface GraphCanvasHandle {
  fit(): void;
  zoomIn(): void;
  zoomOut(): void;
  /** `cy.png()`'s data URL, or `null` before the canvas has mounted. */
  exportPng(): string | null;
}

export interface GraphCanvasProps {
  nodes: GraphNodeStats[];
  edges: GraphEdgeAgg[];
  centerId?: number | null;
  /** Node ids removed from the canvas by the "הסתר" action -- filtered out before render, not
   * just visually dimmed, so hidden nodes don't affect layout. */
  hiddenIds?: Set<number>;
  layoutName?: GraphLayoutName;
  /** The path-finder's highlighted result, if any. */
  pathNodeIds?: Set<number>;
  pathEdgeKeys?: Set<string>;
  selectedNodeId?: number | null;
  onNodeClick?: (id: number) => void;
  onEdgeClick?: (edge: GraphEdgeAgg) => void;
  compact?: boolean;
}

function edgeKey(src: number, dst: number, relation: string): string {
  return `${src}-${dst}-${relation}`;
}

export const GraphCanvas = forwardRef<GraphCanvasHandle, GraphCanvasProps>(
  function GraphCanvas(
    {
      nodes,
      edges,
      centerId = null,
      hiddenIds,
      layoutName = "cose",
      pathNodeIds,
      pathEdgeKeys,
      selectedNodeId = null,
      onNodeClick,
      onEdgeClick,
      compact = false,
    },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement>(null);
    const cyRef = useRef<Core | null>(null);

    useImperativeHandle(
      ref,
      () => ({
        fit: () => cyRef.current?.fit(undefined, 24),
        zoomIn: () => {
          const cy = cyRef.current;
          if (cy)
            cy.zoom({
              level: cy.zoom() * 1.3,
              renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
            });
        },
        zoomOut: () => {
          const cy = cyRef.current;
          if (cy)
            cy.zoom({
              level: cy.zoom() / 1.3,
              renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 },
            });
        },
        exportPng: () =>
          cyRef.current?.png({ full: true, scale: 2, bg: "#0f1416" }) ?? null,
      }),
      [],
    );

    const hidden = hiddenIds ?? new Set<number>();
    const visibleNodes = nodes.filter((n) => !hidden.has(n.id));
    const visibleIds = new Set(visibleNodes.map((n) => n.id));
    const visibleEdges = edges.filter(
      (e) => visibleIds.has(e.src) && visibleIds.has(e.dst),
    );

    useEffect(() => {
      if (!containerRef.current) return;
      const maxMentions = Math.max(1, ...visibleNodes.map((n) => n.mention_count));
      const maxWeight = Math.max(1, ...visibleEdges.map((e) => e.weight));
      const sizeFor = (n: GraphNodeStats) => {
        const min = compact ? 22 : 26;
        const max = compact ? 46 : 64;
        return min + (max - min) * Math.sqrt(n.mention_count / maxMentions);
      };
      const widthFor = (w: number) => 1.5 + 4 * Math.sqrt(w / maxWeight);

      const cy = cytoscape({
        container: containerRef.current,
        elements: [
          ...visibleNodes.map((n) => ({
            data: {
              id: String(n.id),
              label: n.name,
              kind: n.kind,
              size: sizeFor(n),
              focus: n.id === centerId,
              inPath: pathNodeIds?.has(n.id) ?? false,
            },
          })),
          ...visibleEdges.map((e, i) => ({
            data: {
              id: `e${i}`,
              source: String(e.src),
              target: String(e.dst),
              label: e.relation,
              width: widthFor(e.weight),
              weight: e.weight,
              inPath: pathEdgeKeys?.has(edgeKey(e.src, e.dst, e.relation)) ?? false,
            },
          })),
        ],
        style: [
          {
            selector: "node",
            style: {
              "background-color": (n: NodeSingular) => kindColor(n.data("kind")),
              shape: "ellipse",
              label: "data(label)",
              color: "#e6edee",
              "font-size": compact ? 8 : 10,
              "text-wrap": "wrap",
              "text-max-width": "80px",
              "text-valign": "bottom",
              "text-margin-y": 4,
              width: "data(size)",
              height: "data(size)",
              "border-width": (n: NodeSingular) =>
                n.data("focus") ? 3 : n.data("inPath") ? 3 : 0,
              "border-color": (n: NodeSingular) =>
                n.data("focus") ? "#c4561b" : "#e0b84c",
            },
          },
          {
            selector: "node:selected",
            style: { "border-width": 4, "border-color": "#17909f" },
          },
          {
            selector: "edge",
            style: {
              width: "data(width)",
              "line-color": (e: EdgeSingular) =>
                e.data("inPath") ? "#e0b84c" : "#2c3d42",
              "target-arrow-color": (e: EdgeSingular) =>
                e.data("inPath") ? "#e0b84c" : "#2c3d42",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
              label: compact ? "" : "data(label)",
              "font-size": 8,
              color: "#9fb0b4",
              "text-rotation": "autorotate",
              "z-index": (e: EdgeSingular) => (e.data("inPath") ? 10 : 1),
            },
          },
        ],
        layout: { name: layoutName, animate: false, padding: compact ? 16 : 30 },
        autoungrabify: compact,
        userZoomingEnabled: true,
        userPanningEnabled: true,
        boxSelectionEnabled: false,
        wheelSensitivity: 0.3,
      });

      if (onNodeClick) {
        cy.on("tap", "node", (evt: cytoscape.EventObject) => {
          const id = Number((evt.target as NodeSingular).id());
          if (!Number.isNaN(id)) onNodeClick(id);
        });
      }
      if (onEdgeClick) {
        // double-click/double-tap (not a single tap) so a single tap can stay free for future use
        // (panning past an edge, hover tooltips) without accidentally popping the evidence panel.
        cy.on("dbltap", "edge", (evt: cytoscape.EventObject) => {
          const e = evt.target as EdgeSingular;
          const src = Number(e.source().id());
          const dst = Number(e.target().id());
          const relation = e.data("label") as string;
          const match = visibleEdges.find(
            (ed) => ed.src === src && ed.dst === dst && ed.relation === relation,
          );
          if (match) onEdgeClick(match);
        });
      }

      cyRef.current = cy;
      return () => {
        cy.destroy();
        cyRef.current = null;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [visibleNodes, visibleEdges, layoutName, centerId, compact]);

    // Selection highlight kept as its own effect so re-selecting doesn't rebuild the whole canvas.
    useEffect(() => {
      const cy = cyRef.current;
      if (!cy) return;
      cy.nodes().unselect();
      if (selectedNodeId != null) cy.getElementById(String(selectedNodeId)).select();
    }, [selectedNodeId]);

    return (
      <div
        ref={containerRef}
        className="h-full w-full"
        role="img"
        aria-label="גרף ישויות -- לתצוגה נגישה יותר עברו לתצוגת הטבלה"
      />
    );
  },
);
