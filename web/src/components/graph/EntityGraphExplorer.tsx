import { useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Crosshair,
  Download,
  FileSpreadsheet,
  Maximize2,
  Route,
  Table2,
  Waypoints,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { api } from "@/api";
import type { GraphEdgeAgg, GraphSearchResult } from "@/types/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { GraphCanvas, type GraphCanvasHandle, type GraphLayoutName } from "./GraphCanvas";
import { GraphLegend } from "./GraphLegend";
import {
  GraphFilterBar,
  DEFAULT_GRAPH_FILTERS,
  type GraphFilterState,
} from "./GraphFilterBar";
import { EntitySearchBox } from "./EntitySearchBox";
import { EntitySidePanel } from "./EntitySidePanel";
import { EdgeEvidencePanel } from "./EdgeEvidencePanel";
import { PathFinderPanel } from "./PathFinderPanel";
import { GraphTableView } from "./GraphTableView";
import { downloadGraphCsv, downloadGraphPng } from "./exportGraph";

const LAYOUT_OPTIONS: { value: GraphLayoutName; label: string }[] = [
  { value: "cose", label: "אורגני (cose)" },
  { value: "concentric", label: "מעגלי" },
  { value: "breadthfirst", label: "היררכי" },
];

function sinceIso(days: number | null): string | undefined {
  if (days == null) return undefined;
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString();
}

/** The analyst-facing entity graph explorer (R10-graph, docs/qa/loop/round_10_fixes.md). Owns:
 * search-to-center, kind/relation/country/product-line/time filters, the cytoscape canvas
 * (node size by mentions, edge width by weight, color by kind + legend), a side panel with full
 * entity detail and expand/center/hide, double-click-edge evidence, a two-entity path finder,
 * layout switching + fit/zoom, PNG/CSV export, and an accessible table-view alternative to the
 * canvas. `initialCenterId=null` starts from `GET /api/graph/overview` (the "map of the map").
 */
export function EntityGraphExplorer({
  initialCenterId = null,
}: {
  initialCenterId?: number | null;
}) {
  const [centerId, setCenterId] = useState<number | null>(initialCenterId);
  const [filters, setFilters] = useState<GraphFilterState>(DEFAULT_GRAPH_FILTERS);
  const [hiddenIds, setHiddenIds] = useState<Set<number>>(new Set());
  const [selectedNodeId, setSelectedNodeId] = useState<number | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdgeAgg | null>(null);
  const [layoutName, setLayoutName] = useState<GraphLayoutName>("cose");
  const [tableView, setTableView] = useState(false);
  const [pathFinderOpen, setPathFinderOpen] = useState(false);
  const [pathHighlight, setPathHighlight] = useState<{
    nodeIds: Set<number>;
    edgeKeys: Set<string>;
  } | null>(null);
  const [nodeLimit, setNodeLimit] = useState(300);
  const canvasRef = useRef<GraphCanvasHandle>(null);

  const neighborhoodQuery = useQuery({
    queryKey: ["graph-neighborhood", centerId, filters, nodeLimit],
    queryFn: () =>
      api.getGraphNeighborhood(centerId!, {
        depth: filters.depth,
        kinds: filters.kinds.length ? filters.kinds : undefined,
        relationTypes: filters.relationTypes.length ? filters.relationTypes : undefined,
        since: sinceIso(filters.sinceDays),
        limit: nodeLimit,
      }),
    enabled: centerId != null,
  });

  const overviewQuery = useQuery({
    queryKey: ["graph-overview", nodeLimit],
    queryFn: () => api.getGraphOverview(nodeLimit),
    enabled: centerId == null,
  });

  const activeQuery = centerId != null ? neighborhoodQuery : overviewQuery;
  const rawNodes = useMemo(() => activeQuery.data?.nodes ?? [], [activeQuery.data]);
  const rawEdges = useMemo(() => activeQuery.data?.edges ?? [], [activeQuery.data]);

  // client-side country/product-line refinement -- both are node-level facets the backend
  // filters don't cover (kinds/relation_types/since are the server-side ones; see
  // eoa.graph.queries.neighborhood).
  const filteredNodes = useMemo(() => {
    let list = rawNodes;
    if (filters.country) list = list.filter((n) => n.country === filters.country);
    if (filters.productLine)
      list = list.filter((n) => n.product_lines.includes(filters.productLine));
    return list;
  }, [rawNodes, filters.country, filters.productLine]);
  const filteredIds = useMemo(
    () => new Set(filteredNodes.map((n) => n.id)),
    [filteredNodes],
  );
  const filteredEdges = useMemo(
    () => rawEdges.filter((e) => filteredIds.has(e.src) && filteredIds.has(e.dst)),
    [rawEdges, filteredIds],
  );

  const visibleNodes = useMemo(
    () => filteredNodes.filter((n) => !hiddenIds.has(n.id)),
    [filteredNodes, hiddenIds],
  );
  const visibleIds = useMemo(
    () => new Set(visibleNodes.map((n) => n.id)),
    [visibleNodes],
  );
  const visibleEdges = useMemo(
    () => filteredEdges.filter((e) => visibleIds.has(e.src) && visibleIds.has(e.dst)),
    [filteredEdges, visibleIds],
  );
  const nodesById = useMemo(
    () => new Map(visibleNodes.map((n) => [n.id, n])),
    [visibleNodes],
  );

  const kindsPresent = useMemo(
    () => Array.from(new Set(visibleNodes.map((n) => n.kind).filter(Boolean))),
    [visibleNodes],
  );
  const availableCountries = useMemo(
    () =>
      Array.from(
        new Set(rawNodes.map((n) => n.country).filter((c): c is string => !!c)),
      ).sort(),
    [rawNodes],
  );

  const selectedNode = selectedNodeId != null ? nodesById.get(selectedNodeId) : undefined;

  function handleSelectEntity(entity: GraphSearchResult): void {
    setCenterId(entity.id);
    setSelectedNodeId(entity.id);
    setHiddenIds(new Set());
    setPathHighlight(null);
    setNodeLimit(300);
  }

  function handleNodeClick(id: number): void {
    setSelectedNodeId(id);
    setSelectedEdge(null);
  }

  function handleExpand(id: number): void {
    setCenterId(id);
    setSelectedNodeId(id);
    setHiddenIds(new Set());
    setPathHighlight(null);
    setNodeLimit(300);
  }

  function handleCenter(id: number): void {
    handleExpand(id);
  }

  function handleHide(id: number): void {
    setHiddenIds((prev) => new Set(prev).add(id));
    if (selectedNodeId === id) setSelectedNodeId(null);
  }

  const truncated = neighborhoodQuery.data?.truncated ?? false;

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-[16rem] flex-1">
          <EntitySearchBox
            placeholder="מרכז גרף סביב ישות…"
            aria-label="חפש ישות למרכז הגרף"
            onSelect={handleSelectEntity}
          />
        </div>
        <button
          type="button"
          onClick={() => setPathFinderOpen((v) => !v)}
          aria-pressed={pathFinderOpen}
          className={`flex items-center gap-1 rounded-md border px-2 py-1.5 text-xs ${
            pathFinderOpen
              ? "border-accent text-accent"
              : "border-border-strong text-fg-muted"
          }`}
        >
          <Route size={14} aria-hidden="true" />
          מוצא מסלולים
        </button>
        <button
          type="button"
          onClick={() => setTableView((v) => !v)}
          aria-pressed={tableView}
          className={`flex items-center gap-1 rounded-md border px-2 py-1.5 text-xs ${
            tableView ? "border-accent text-accent" : "border-border-strong text-fg-muted"
          }`}
        >
          <Table2 size={14} aria-hidden="true" />
          תצוגת טבלה
        </button>
      </div>

      <GraphFilterBar
        filters={filters}
        onChange={setFilters}
        availableCountries={availableCountries}
      />

      {pathFinderOpen && (
        <PathFinderPanel
          onPathFound={(nodeIds, edgeKeys) =>
            setPathHighlight({ nodeIds: new Set(nodeIds), edgeKeys: new Set(edgeKeys) })
          }
          onClear={() => setPathHighlight(null)}
          onClose={() => setPathFinderOpen(false)}
        />
      )}

      {truncated && (
        <div className="flex items-center justify-between rounded-md border border-level-orange bg-level-orange-bg px-2 py-1 text-xs text-level-orange">
          <span>
            הוצגו {visibleNodes.length} ישויות (הגרף נחתך לפי נפח אזכורים). ניתן להציג
            יותר.
          </span>
          <button
            type="button"
            onClick={() => setNodeLimit((n) => Math.min(n + 300, 1500))}
            className="rounded-md border border-level-orange px-2 py-0.5"
          >
            הצג עוד
          </button>
        </div>
      )}

      <div className="flex min-h-0 flex-1 gap-3">
        <div className="relative min-h-0 flex-1 rounded-lg border border-border bg-bg-sunken">
          {activeQuery.isLoading && <LoadingState label="בונה גרף…" />}
          {activeQuery.isError && (
            <ErrorState
              onRetry={() => activeQuery.refetch()}
              message="לא ניתן לטעון את הגרף"
            />
          )}
          {activeQuery.data && visibleNodes.length === 0 && (
            <EmptyState
              title={centerId == null ? "אין נתוני גרף עדיין" : "אין קשרים מתועדים"}
              description={
                centerId == null
                  ? "חפשו ישות למעלה כדי למרכז את הגרף סביבה."
                  : "לא נמצאו קשרי גרף לישות זו בטווח הסינון הנוכחי."
              }
            />
          )}
          {activeQuery.data && visibleNodes.length > 0 && (
            <>
              {tableView ? (
                <GraphTableView nodes={visibleNodes} edges={visibleEdges} />
              ) : (
                <GraphCanvas
                  ref={canvasRef}
                  nodes={visibleNodes}
                  edges={visibleEdges}
                  centerId={centerId}
                  hiddenIds={hiddenIds}
                  layoutName={layoutName}
                  pathNodeIds={pathHighlight?.nodeIds}
                  pathEdgeKeys={pathHighlight?.edgeKeys}
                  selectedNodeId={selectedNodeId}
                  onNodeClick={handleNodeClick}
                  onEdgeClick={setSelectedEdge}
                />
              )}

              {!tableView && (
                <>
                  <div className="absolute top-2 start-2">
                    <GraphLegend kinds={kindsPresent} />
                  </div>
                  <div className="absolute top-2 end-2 flex flex-col gap-1">
                    <label className="flex items-center gap-1 rounded-md bg-bg-raised/90 px-1.5 py-1 text-[10px] text-fg-dim shadow-panel">
                      <Waypoints size={12} aria-hidden="true" />
                      <select
                        value={layoutName}
                        onChange={(e) => setLayoutName(e.target.value as GraphLayoutName)}
                        className="bg-transparent text-fg"
                        aria-label="פריסת גרף"
                      >
                        {LAYOUT_OPTIONS.map((l) => (
                          <option key={l.value} value={l.value}>
                            {l.label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="flex gap-1">
                      <button
                        type="button"
                        onClick={() => canvasRef.current?.fit()}
                        aria-label="התאם לתצוגה"
                        title="התאם לתצוגה"
                        className="tap-target rounded-md bg-bg-raised/90 p-1.5 text-fg-dim shadow-panel hover:text-fg"
                      >
                        <Maximize2 size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        onClick={() => canvasRef.current?.zoomIn()}
                        aria-label="הגדל"
                        title="הגדל"
                        className="tap-target rounded-md bg-bg-raised/90 p-1.5 text-fg-dim shadow-panel hover:text-fg"
                      >
                        <ZoomIn size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        onClick={() => canvasRef.current?.zoomOut()}
                        aria-label="הקטן"
                        title="הקטן"
                        className="tap-target rounded-md bg-bg-raised/90 p-1.5 text-fg-dim shadow-panel hover:text-fg"
                      >
                        <ZoomOut size={14} aria-hidden="true" />
                      </button>
                      {centerId != null && (
                        <button
                          type="button"
                          onClick={() => handleCenter(centerId)}
                          aria-label="רענן מרכז"
                          title="רענן מרכז"
                          className="tap-target rounded-md bg-bg-raised/90 p-1.5 text-fg-dim shadow-panel hover:text-fg"
                        >
                          <Crosshair size={14} aria-hidden="true" />
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => {
                          const png = canvasRef.current?.exportPng();
                          if (png) downloadGraphPng(png);
                        }}
                        aria-label="ייצוא PNG"
                        title="ייצוא PNG"
                        className="tap-target rounded-md bg-bg-raised/90 p-1.5 text-fg-dim shadow-panel hover:text-fg"
                      >
                        <Download size={14} aria-hidden="true" />
                      </button>
                      <button
                        type="button"
                        onClick={() => downloadGraphCsv(visibleNodes, visibleEdges)}
                        aria-label="ייצוא CSV"
                        title="ייצוא CSV"
                        className="tap-target rounded-md bg-bg-raised/90 p-1.5 text-fg-dim shadow-panel hover:text-fg"
                      >
                        <FileSpreadsheet size={14} aria-hidden="true" />
                      </button>
                    </div>
                  </div>

                  {selectedEdge && (
                    <EdgeEvidencePanel
                      edge={selectedEdge}
                      nodesById={nodesById}
                      onClose={() => setSelectedEdge(null)}
                    />
                  )}
                </>
              )}
            </>
          )}
        </div>

        {selectedNode && (
          <div className="w-72 shrink-0">
            <EntitySidePanel
              node={selectedNode}
              onExpand={handleExpand}
              onCenter={handleCenter}
              onHide={handleHide}
              onClose={() => setSelectedNodeId(null)}
            />
          </div>
        )}
      </div>
    </div>
  );
}
