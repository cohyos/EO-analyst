import { forwardRef, useImperativeHandle } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type {
  EntityDetailFull,
  GraphEdgeAgg,
  GraphNodeStats,
  GraphOverviewResponse,
  GraphPathResponse,
  GraphSearchResult,
  NeighborhoodResponse,
} from "@/types/api";

const searchGraphEntities = vi.fn();
const getGraphOverview = vi.fn();
const getGraphNeighborhood = vi.fn();
const getGraphPath = vi.fn();
const getEntityDetail = vi.fn();

vi.mock("@/api", () => ({
  api: {
    searchGraphEntities: (...args: unknown[]) => searchGraphEntities(...args),
    getGraphOverview: (...args: unknown[]) => getGraphOverview(...args),
    getGraphNeighborhood: (...args: unknown[]) => getGraphNeighborhood(...args),
    getGraphPath: (...args: unknown[]) => getGraphPath(...args),
    getEntityDetail: (...args: unknown[]) => getEntityDetail(...args),
  },
}));

// cytoscape needs a real 2D canvas context jsdom doesn't provide (no existing test in this repo
// mounts the sibling `EntityGraph.tsx` canvas either) -- stub the canvas with plain buttons that
// invoke the same callback props, so the orchestrator's own logic (data flow, side panel,
// filters, path finder, table view, export) is exercised without touching cytoscape.
interface StubCanvasProps {
  nodes: GraphNodeStats[];
  edges: GraphEdgeAgg[];
  onNodeClick?: (id: number) => void;
  onEdgeClick?: (edge: GraphEdgeAgg) => void;
}

vi.mock("./GraphCanvas", () => ({
  GraphCanvas: forwardRef<unknown, StubCanvasProps>((props, ref) => {
    useImperativeHandle(ref, () => ({
      fit: vi.fn(),
      zoomIn: vi.fn(),
      zoomOut: vi.fn(),
      exportPng: () => "data:image/png;base64,stub",
    }));
    return (
      <div data-testid="graph-canvas-stub">
        {props.nodes.map((n: GraphNodeStats) => (
          <button key={n.id} type="button" onClick={() => props.onNodeClick?.(n.id)}>
            {n.name}
          </button>
        ))}
        {props.edges.map((e: GraphEdgeAgg, i: number) => (
          <button key={i} type="button" onClick={() => props.onEdgeClick?.(e)}>
            edge:{e.relation}
          </button>
        ))}
      </div>
    );
  }),
}));

import { EntityGraphExplorer } from "./EntityGraphExplorer";

function node(id: number, name: string, overrides: Partial<GraphNodeStats> = {}): GraphNodeStats {
  return {
    id,
    name,
    kind: "company",
    country: "IL",
    mention_count: 5,
    last_seen: "2026-09-01T00:00:00+03:00",
    corroboration: { corroborated: 1, official_primary: 0, single_source: 1, unknown: 0 },
    product_lines: [],
    ...overrides,
  };
}

function edge(src: number, dst: number, relation = "PARTNER_OF"): GraphEdgeAgg {
  return { src, dst, relation, weight: 1, first_seen: "2026-01-01", last_seen: "2026-02-01", evidence: [] };
}

function makeOverview(): GraphOverviewResponse {
  return { nodes: [node(1, "Elbit Systems"), node(2, "Rafael")], edges: [edge(1, 2)] };
}

function makeNeighborhood(centerId: number, truncated = false): NeighborhoodResponse {
  return {
    nodes: [node(centerId, "Elbit Systems"), node(99, "IAI")],
    edges: [edge(centerId, 99)],
    center_id: centerId,
    truncated,
  };
}

function makeEntityDetail(id: number, name: string): EntityDetailFull {
  return {
    id,
    name,
    kind: "company",
    country: "IL",
    aliases: [],
    focus: [],
    item_count: 5,
    last_seen: null,
    relevance: 0.8,
    is_watchlist: false,
    mentions_7d: 2,
    mentions_30d: 5,
    is_israeli: true,
    timeline: [],
    business_events: [],
    kpis: { mentions_7d: 2, mentions_30d: 5, events_count: 0, related_items_by_level: {} },
    edge_groups: [],
    neighbors: [],
    investigations: [],
    reports: [],
  };
}

function renderExplorer(initialCenterId: number | null = null) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <EntityGraphExplorer initialCenterId={initialCenterId} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  searchGraphEntities.mockReset();
  getGraphOverview.mockReset();
  getGraphNeighborhood.mockReset();
  getGraphPath.mockReset();
  getEntityDetail.mockReset();
  getGraphOverview.mockResolvedValue(makeOverview());
  getGraphNeighborhood.mockImplementation((id: number) => Promise.resolve(makeNeighborhood(id)));
  getEntityDetail.mockImplementation((id: number) => Promise.resolve(makeEntityDetail(id, `Entity ${id}`)));
});

describe("EntityGraphExplorer", () => {
  it("starts from the overview when no center entity is given", async () => {
    renderExplorer(null);
    await waitFor(() => expect(getGraphOverview).toHaveBeenCalled());
    expect(await screen.findByText("Elbit Systems")).toBeInTheDocument();
    expect(getGraphNeighborhood).not.toHaveBeenCalled();
  });

  it("starts centered on initialCenterId when given", async () => {
    renderExplorer(7);
    await waitFor(() => expect(getGraphNeighborhood).toHaveBeenCalledWith(7, expect.anything()));
  });

  it("selecting a search result centers the graph on that entity", async () => {
    searchGraphEntities.mockResolvedValue([
      { id: 42, name: "IAI", kind: "company", country: "IL", mention_count: 3 } satisfies GraphSearchResult,
    ]);
    renderExplorer(null);
    await screen.findByText("Elbit Systems");

    fireEvent.change(screen.getByPlaceholderText("מרכז גרף סביב ישות…"), { target: { value: "IAI" } });
    const option = await screen.findByRole("button", { name: /IAI/ });
    fireEvent.click(option);

    await waitFor(() => expect(getGraphNeighborhood).toHaveBeenCalledWith(42, expect.anything()));
  });

  it("clicking a node opens the side panel with its entity detail", async () => {
    renderExplorer(null);
    fireEvent.click(await screen.findByRole("button", { name: "Elbit Systems" }));
    await waitFor(() => expect(getEntityDetail).toHaveBeenCalledWith(1));
    expect(await screen.findByLabelText("פרטי ישות נבחרת")).toBeInTheDocument();
  });

  it('side panel "הרחב שכנים" re-centers the graph on that node', async () => {
    renderExplorer(null);
    fireEvent.click(await screen.findByRole("button", { name: "Rafael" }));
    await screen.findByLabelText("פרטי ישות נבחרת");
    fireEvent.click(await screen.findByRole("button", { name: "הרחב שכנים" }));
    await waitFor(() => expect(getGraphNeighborhood).toHaveBeenCalledWith(2, expect.anything()));
  });

  it('side panel "הסתר" removes the node from the visible canvas', async () => {
    renderExplorer(null);
    fireEvent.click(await screen.findByRole("button", { name: "Rafael" }));
    await screen.findByLabelText("פרטי ישות נבחרת");
    fireEvent.click(await screen.findByRole("button", { name: "הסתר" }));
    await waitFor(() =>
      expect(within(screen.getByTestId("graph-canvas-stub")).queryByText("Rafael")).not.toBeInTheDocument(),
    );
  });

  it("toggling table view swaps the canvas for the accessible table", async () => {
    renderExplorer(null);
    await screen.findByTestId("graph-canvas-stub");
    fireEvent.click(screen.getByRole("button", { name: "תצוגת טבלה" }));
    expect(screen.queryByTestId("graph-canvas-stub")).not.toBeInTheDocument();
    expect(screen.getByText("ישויות (2)")).toBeInTheDocument();
  });

  it("path finder highlights a found path and renders its node chain", async () => {
    const pathResult: GraphPathResponse = {
      nodes: [
        { id: 1, name: "Elbit Systems", kind: "company", country: "IL" },
        { id: 2, name: "Rafael", kind: "company", country: "IL" },
      ],
      edges: [edge(1, 2, "COMPETITOR_OF")],
      hops: 1,
    };
    getGraphPath.mockResolvedValue(pathResult);
    searchGraphEntities.mockImplementation((q: string) =>
      Promise.resolve(
        q === "Elbit"
          ? [{ id: 1, name: "Elbit Systems", kind: "company", country: "IL", mention_count: 5 }]
          : [{ id: 2, name: "Rafael", kind: "company", country: "IL", mention_count: 3 }],
      ),
    );
    renderExplorer(null);
    await screen.findByText("Elbit Systems");

    fireEvent.click(screen.getByRole("button", { name: "מוצא מסלולים" }));
    const panel = within(await screen.findByTestId("path-finder-panel"));
    fireEvent.change(panel.getByPlaceholderText("חפש ישות מקור…"), { target: { value: "Elbit" } });
    fireEvent.click(await panel.findByRole("button", { name: /Elbit Systems/ }));
    fireEvent.change(panel.getByPlaceholderText("חפש ישות יעד…"), { target: { value: "Rafael" } });
    fireEvent.click(await panel.findByRole("button", { name: /Rafael/ }));
    const findPathButton = panel.getByRole("button", { name: "מצא מסלול" });
    await waitFor(() => expect(findPathButton).not.toBeDisabled());
    fireEvent.click(findPathButton);

    await waitFor(() => expect(getGraphPath).toHaveBeenCalledWith(1, 2, 4));
  });

  it('shows a "הצג עוד" control when the neighborhood response is truncated', async () => {
    getGraphNeighborhood.mockResolvedValue(makeNeighborhood(1, true));
    renderExplorer(1);
    expect(await screen.findByRole("button", { name: "הצג עוד" })).toBeInTheDocument();
  });

  it('clicking "הצג עוד" re-requests the neighborhood with a larger limit', async () => {
    getGraphNeighborhood.mockResolvedValue(makeNeighborhood(1, true));
    renderExplorer(1);
    await waitFor(() =>
      expect(getGraphNeighborhood).toHaveBeenCalledWith(1, expect.objectContaining({ limit: 300 })),
    );
    fireEvent.click(await screen.findByRole("button", { name: "הצג עוד" }));
    await waitFor(() =>
      expect(getGraphNeighborhood).toHaveBeenCalledWith(1, expect.objectContaining({ limit: 600 })),
    );
  });

  it("a country filter narrows the visible nodes client-side", async () => {
    getGraphOverview.mockResolvedValue({
      nodes: [node(1, "Elbit Systems", { country: "IL" }), node(2, "Leonardo DRS", { country: "US" })],
      edges: [],
    });
    renderExplorer(null);
    await screen.findByText("Leonardo DRS");

    const countrySelect = screen.getByText("מדינה:").closest("label")!.querySelector("select")!;
    fireEvent.change(countrySelect, { target: { value: "US" } });

    await waitFor(() =>
      expect(within(screen.getByTestId("graph-canvas-stub")).queryByText("Elbit Systems")).not.toBeInTheDocument(),
    );
    expect(within(screen.getByTestId("graph-canvas-stub")).getByText("Leonardo DRS")).toBeInTheDocument();
  });
});
