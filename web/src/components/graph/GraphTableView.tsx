import { Link } from "react-router-dom";
import type { GraphEdgeAgg, GraphNodeStats } from "@/types/api";
import { edgeLabelHe, entityKindLabel } from "@/components/entities/eventKindLabel";
import { countryFlagEmoji } from "@/lib/countryFlag";
import { formatDate } from "@/lib/time";

/** Keyboard/screen-reader alternative to the cytoscape canvas (which is an opaque `<canvas>`-like
 * element to assistive tech) -- the same nodes/edges as two real, sortable-by-eye HTML tables. */
export function GraphTableView({
  nodes,
  edges,
}: {
  nodes: GraphNodeStats[];
  edges: GraphEdgeAgg[];
}) {
  const byId = new Map(nodes.map((n) => [n.id, n]));
  return (
    <div className="h-full space-y-4 overflow-y-auto rounded-lg border border-border bg-bg-raised p-3 text-xs">
      <section aria-label="ישויות בגרף">
        <h3 className="mb-1.5 font-semibold text-fg-dim">ישויות ({nodes.length})</h3>
        <table className="w-full border-collapse text-start">
          <thead>
            <tr className="border-b border-border text-fg-dim">
              <th className="p-1 text-start font-medium">שם</th>
              <th className="p-1 text-start font-medium">סוג</th>
              <th className="p-1 text-start font-medium">מדינה</th>
              <th className="p-1 text-start font-medium">אזכורים</th>
              <th className="p-1 text-start font-medium">נראה לאחרונה</th>
            </tr>
          </thead>
          <tbody>
            {nodes.map((n) => (
              <tr key={n.id} className="border-b border-border/50">
                <td className="p-1">
                  <Link to={`/entities/${n.id}`} className="text-accent hover:underline">
                    <bdi>{n.name}</bdi>
                  </Link>
                </td>
                <td className="p-1">{entityKindLabel(n.kind)}</td>
                <td className="p-1">{n.country ? countryFlagEmoji(n.country) : "—"}</td>
                <td className="p-1 font-mono">{n.mention_count}</td>
                <td className="p-1 font-mono">{formatDate(n.last_seen)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section aria-label="קשרים בגרף">
        <h3 className="mb-1.5 font-semibold text-fg-dim">קשרים ({edges.length})</h3>
        <table className="w-full border-collapse text-start">
          <thead>
            <tr className="border-b border-border text-fg-dim">
              <th className="p-1 text-start font-medium">מ</th>
              <th className="p-1 text-start font-medium">סוג קשר</th>
              <th className="p-1 text-start font-medium">אל</th>
              <th className="p-1 text-start font-medium">משקל</th>
              <th className="p-1 text-start font-medium">אחרון</th>
            </tr>
          </thead>
          <tbody>
            {edges.map((e, i) => (
              <tr
                key={`${e.src}-${e.dst}-${e.relation}-${i}`}
                className="border-b border-border/50"
              >
                <td className="p-1">
                  <bdi>{byId.get(e.src)?.name ?? `#${e.src}`}</bdi>
                </td>
                <td className="p-1">{edgeLabelHe(e.relation)}</td>
                <td className="p-1">
                  <bdi>{byId.get(e.dst)?.name ?? `#${e.dst}`}</bdi>
                </td>
                <td className="p-1 font-mono">{e.weight}</td>
                <td className="p-1 font-mono">{formatDate(e.last_seen)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
