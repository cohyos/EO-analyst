import { describe, expect, it } from "vitest";
import type { GraphEdgeAgg, GraphNodeStats } from "@/types/api";
import { graphToCsv } from "./exportGraph";

function node(overrides: Partial<GraphNodeStats> = {}): GraphNodeStats {
  return {
    id: 1,
    name: "Elbit Systems",
    kind: "company",
    country: "IL",
    mention_count: 5,
    last_seen: "2026-09-01T00:00:00+03:00",
    corroboration: { corroborated: 1, official_primary: 0, single_source: 2, unknown: 0 },
    product_lines: [],
    ...overrides,
  };
}

function edge(overrides: Partial<GraphEdgeAgg> = {}): GraphEdgeAgg {
  return {
    src: 1,
    dst: 2,
    relation: "PARTNER_OF",
    weight: 2,
    first_seen: "2026-01-01",
    last_seen: "2026-03-01",
    evidence: [],
    ...overrides,
  };
}

describe("graphToCsv", () => {
  it("emits a nodes section header and one row per node", () => {
    const csv = graphToCsv([node()], []);
    expect(csv).toContain("# nodes");
    expect(csv).toContain("id,name,kind,country,mention_count,last_seen");
    expect(csv).toContain("1,Elbit Systems,company,IL,5,2026-09-01T00:00:00+03:00");
  });

  it("emits an edges section header and one row per edge", () => {
    const csv = graphToCsv([], [edge()]);
    expect(csv).toContain("# edges");
    expect(csv).toContain("src,dst,relation,weight,first_seen,last_seen");
    expect(csv).toContain("1,2,PARTNER_OF,2,2026-01-01,2026-03-01");
  });

  it("quotes and escapes a name containing a comma", () => {
    const csv = graphToCsv([node({ name: "Rafael, Advanced" })], []);
    expect(csv).toContain('"Rafael, Advanced"');
  });

  it("quotes and doubles an embedded double-quote", () => {
    const csv = graphToCsv([node({ name: 'The "Best" Co' })], []);
    expect(csv).toContain('"The ""Best"" Co"');
  });

  it("renders null country/last_seen as empty cells, not the literal string null", () => {
    const csv = graphToCsv([node({ country: null, last_seen: null })], []);
    expect(csv).not.toContain("null");
    expect(csv).toContain("1,Elbit Systems,company,,5,");
  });

  it("produces both sections even when nodes and edges are both empty", () => {
    const csv = graphToCsv([], []);
    expect(csv).toContain("# nodes");
    expect(csv).toContain("# edges");
  });
});
