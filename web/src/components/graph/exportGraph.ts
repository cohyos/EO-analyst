import type { GraphEdgeAgg, GraphNodeStats } from "@/types/api";

function csvCell(value: string | number | null | undefined): string {
  const s = value == null ? "" : String(value);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Builds a CSV of the visible subgraph -- nodes first, then a blank line, then edges (both
 * sections share one file so "ייצוא CSV" is a single download, not two). */
export function graphToCsv(nodes: GraphNodeStats[], edges: GraphEdgeAgg[]): string {
  const nodeHeader = ["id", "name", "kind", "country", "mention_count", "last_seen"];
  const nodeRows = nodes.map((n) =>
    [n.id, n.name, n.kind, n.country ?? "", n.mention_count, n.last_seen ?? ""]
      .map(csvCell)
      .join(","),
  );
  const edgeHeader = ["src", "dst", "relation", "weight", "first_seen", "last_seen"];
  const edgeRows = edges.map((e) =>
    [e.src, e.dst, e.relation, e.weight, e.first_seen ?? "", e.last_seen ?? ""]
      .map(csvCell)
      .join(","),
  );
  return [
    "# nodes",
    nodeHeader.join(","),
    ...nodeRows,
    "",
    "# edges",
    edgeHeader.join(","),
    ...edgeRows,
  ].join("\n");
}

function download(filename: string, url: string): void {
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function downloadGraphCsv(
  nodes: GraphNodeStats[],
  edges: GraphEdgeAgg[],
  filename = "entity-graph.csv",
): void {
  const blob = new Blob([graphToCsv(nodes, edges)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    download(filename, url);
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function downloadGraphPng(dataUrl: string, filename = "entity-graph.png"): void {
  download(filename, dataUrl);
}
