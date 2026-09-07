import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { GraphEdgeAgg, GraphNodeStats } from "@/types/api";
import { GraphTableView } from "./GraphTableView";

const NODES: GraphNodeStats[] = [
  {
    id: 1,
    name: "Elbit Systems",
    kind: "company",
    country: "IL",
    mention_count: 9,
    last_seen: "2026-09-03T00:00:00+03:00",
    corroboration: { corroborated: 2, official_primary: 0, single_source: 1, unknown: 0 },
    product_lines: [],
  },
  {
    id: 2,
    name: "Rafael",
    kind: "company",
    country: "IL",
    mention_count: 4,
    last_seen: null,
    corroboration: { corroborated: 0, official_primary: 0, single_source: 0, unknown: 4 },
    product_lines: [],
  },
];

const EDGES: GraphEdgeAgg[] = [
  {
    src: 1,
    dst: 2,
    relation: "COMPETITOR_OF",
    weight: 3,
    first_seen: "2026-01-01",
    last_seen: "2026-03-01",
    evidence: [],
  },
];

function renderTable(nodes = NODES, edges = EDGES) {
  return render(
    <MemoryRouter>
      <GraphTableView nodes={nodes} edges={edges} />
    </MemoryRouter>,
  );
}

describe("GraphTableView", () => {
  it("renders the entity count in the section heading", () => {
    renderTable();
    expect(screen.getByText("ישויות (2)")).toBeInTheDocument();
  });

  it("renders the edge count in the section heading", () => {
    renderTable();
    expect(screen.getByText("קשרים (1)")).toBeInTheDocument();
  });

  it("renders every node's name as a link to its entity page", () => {
    renderTable();
    const link = screen.getByRole("link", { name: "Elbit Systems" });
    expect(link).toHaveAttribute("href", "/entities/1");
  });

  it("renders the relation's Hebrew label, not the raw enum value", () => {
    renderTable();
    expect(screen.getByText("מתחרה של")).toBeInTheDocument();
    expect(screen.queryByText("COMPETITOR_OF")).not.toBeInTheDocument();
  });

  it("renders both node names on the edge row", () => {
    renderTable();
    const row = screen.getByText("מתחרה של").closest("tr")!;
    expect(row.textContent).toContain("Elbit Systems");
    expect(row.textContent).toContain("Rafael");
  });

  it("resolves an edge endpoint not present in nodes to its raw id", () => {
    const orphanEdges: GraphEdgeAgg[] = [
      { src: 1, dst: 999, relation: "PARTNER_OF", weight: 1, first_seen: null, last_seen: null, evidence: [] },
    ];
    renderTable(NODES, orphanEdges);
    expect(screen.getByText("#999")).toBeInTheDocument();
  });
});
