import { Link } from "react-router-dom";
import type { GraphEdgeAgg, GraphNodeStats } from "@/types/api";
import { edgeLabelHe, entityKindLabel } from "@/components/entities/eventKindLabel";
import { countryFlagEmoji } from "@/lib/countryFlag";
import { formatDate } from "@/lib/time";
import { DossierTable, type DossierTableColumn } from "@/components/dossiers/DossierTable";

/** Keyboard/screen-reader alternative to the cytoscape canvas (which is an opaque `<canvas>`-like
 * element to assistive tech) -- the same nodes/edges as two real, sortable-by-eye HTML tables.
 *
 * Round-4 mobile fix (fix #2): `EntityGraphExplorer` now defaults to this view below `md` (the
 * canvas is hard to operate with touch), which means these two 5-column tables are what most
 * phone visits actually see first. Rebuilt on `DossierTable` (the same phone-card component
 * `DossierTable`/`TenderTable`/`PatentTable` already use) instead of raw `<table>` markup, so they
 * get the established stacked-card layout below `md` and a sticky header + horizontal scroll above
 * it for free, rather than overflowing the screen unreadably. */
export function GraphTableView({
  nodes,
  edges,
}: {
  nodes: GraphNodeStats[];
  edges: GraphEdgeAgg[];
}) {
  const byId = new Map(nodes.map((n) => [n.id, n]));

  const nodeColumns: DossierTableColumn<GraphNodeStats>[] = [
    {
      key: "name",
      label: "שם",
      render: (n) => (
        <Link to={`/entities/${n.id}`} className="text-accent hover:underline">
          <bdi>{n.name}</bdi>
        </Link>
      ),
    },
    { key: "kind", label: "סוג", render: (n) => entityKindLabel(n.kind) },
    { key: "country", label: "מדינה", render: (n) => (n.country ? countryFlagEmoji(n.country) : "—") },
    { key: "mentions", label: "אזכורים", render: (n) => <span className="font-mono">{n.mention_count}</span> },
    {
      key: "lastSeen",
      label: "נראה לאחרונה",
      render: (n) => <span className="font-mono">{formatDate(n.last_seen)}</span>,
    },
  ];

  const edgeColumns: DossierTableColumn<GraphEdgeAgg>[] = [
    {
      key: "src",
      label: "מ",
      render: (e) => <bdi>{byId.get(e.src)?.name ?? `#${e.src}`}</bdi>,
    },
    { key: "relation", label: "סוג קשר", render: (e) => edgeLabelHe(e.relation) },
    {
      key: "dst",
      label: "אל",
      render: (e) => <bdi>{byId.get(e.dst)?.name ?? `#${e.dst}`}</bdi>,
    },
    { key: "weight", label: "משקל", render: (e) => <span className="font-mono">{e.weight}</span> },
    {
      key: "lastSeen",
      label: "אחרון",
      render: (e) => <span className="font-mono">{formatDate(e.last_seen)}</span>,
    },
  ];

  return (
    <div className="h-full space-y-4 overflow-y-auto rounded-lg border border-border bg-bg-raised p-3 text-xs">
      <section aria-label="ישויות בגרף">
        <h3 className="mb-1.5 font-semibold text-fg-dim">ישויות ({nodes.length})</h3>
        <DossierTable
          columns={nodeColumns}
          rows={nodes}
          rowKey={(n) => n.id}
          emptyLabel="אין ישויות להצגה"
          caption="ישויות בגרף"
        />
      </section>

      <section aria-label="קשרים בגרף">
        <h3 className="mb-1.5 font-semibold text-fg-dim">קשרים ({edges.length})</h3>
        <DossierTable
          columns={edgeColumns}
          rows={edges}
          rowKey={(e, i) => `${e.src}-${e.dst}-${e.relation}-${i}`}
          emptyLabel="אין קשרים להצגה"
          caption="קשרים בגרף"
        />
      </section>
    </div>
  );
}
