import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { AddToContextButton } from "@/components/AddToContextButton";
import { EntityGraph } from "@/components/entities/EntityGraph";
import { formatDateTime } from "@/lib/time";

const NAMED_QUERIES = [
  { name: "partners_of_competitors", label: "שותפי המתחרים" },
  { name: "suppliers_of_bidders", label: "ספקי המתמודדים בתוכנית" },
  { name: "connected_startups", label: "סטארטאפים מחוברים" },
];

export function EntityDetailPage() {
  const { id } = useParams<{ id: string }>();
  const entityId = Number(id);
  const [depth, setDepth] = useState(1);
  const [namedResult, setNamedResult] = useState<{ label: string; rows: unknown[] } | null>(null);

  const entityQuery = useQuery({
    queryKey: ["entity", entityId],
    queryFn: () => api.getEntity(entityId),
    enabled: !Number.isNaN(entityId),
  });

  const graphQuery = useQuery({
    queryKey: ["graph", entityId, depth],
    queryFn: () => api.getGraph({ entity_id: entityId, depth }),
    enabled: !Number.isNaN(entityId),
  });

  async function runNamedQuery(name: string, label: string) {
    const rows = await api.getGraphNamedQuery(name, entityQuery.data?.name ?? "");
    setNamedResult({ label, rows });
  }

  if (entityQuery.isLoading) return <LoadingState label="טוען ישות…" />;
  if (entityQuery.isError || !entityQuery.data)
    return <ErrorState onRetry={() => entityQuery.refetch()} message="הישות לא נמצאה" />;

  const entity = entityQuery.data;

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-4 md:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold">
              <bdi>{entity.name}</bdi>
            </h2>
            <span className="rounded bg-bg-sunken px-1.5 py-0.5 text-xs text-fg-dim">
              {entity.kind}
            </span>
          </div>
          {entity.focus && <bdi className="block text-sm text-fg-muted">{entity.focus}</bdi>}
          {entity.aliases.length > 0 && (
            <bdi className="block text-xs text-fg-dim">
              כינויים: {entity.aliases.join(", ")}
            </bdi>
          )}
        </div>
        <AddToContextButton kind="entity" id={entity.id} label={entity.name} />
      </header>

      <section aria-label="גרף ישויות" className="space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs text-fg-dim" htmlFor="depth-select">
            עומק:
          </label>
          <select
            id="depth-select"
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
            className="rounded-md border border-border-strong bg-bg px-2 py-1 text-sm"
          >
            <option value={1}>1</option>
            <option value={2}>2</option>
          </select>
          {NAMED_QUERIES.map((q) => (
            <button
              key={q.name}
              type="button"
              onClick={() => runNamedQuery(q.name, q.label)}
              className="rounded-md border border-border-strong px-2 py-1 text-xs text-fg-muted hover:bg-bg-sunken"
            >
              {q.label}
            </button>
          ))}
        </div>
        {graphQuery.isLoading && <LoadingState label="בונה גרף…" />}
        {graphQuery.data && (
          <EntityGraph graph={graphQuery.data} focusEntityId={entity.id} />
        )}
        {namedResult && (
          <p className="text-xs text-fg-dim">
            {namedResult.label}: {namedResult.rows.length} תוצאות
          </p>
        )}
      </section>

      <div className="grid gap-6 md:grid-cols-2">
        <section aria-label="ציר זמן">
          <h3 className="mb-2 text-sm font-semibold text-fg-dim">ציר זמן</h3>
          {entity.timeline.length === 0 ? (
            <EmptyState title="אין אירועים בציר הזמן" />
          ) : (
            <ol className="space-y-2 border-e-2 border-border ps-4">
              {entity.timeline.map((t) => (
                <li key={`${t.kind}-${t.id}`} className="relative">
                  <span className="absolute -end-[1.15rem] top-1.5 h-2 w-2 rounded-full bg-accent" />
                  <Link
                    to={t.item_id ? `/feed?open=${t.item_id}` : "#"}
                    className="block rounded-md p-1.5 hover:bg-bg-sunken"
                  >
                    <bdi className="block text-sm font-medium">{t.title}</bdi>
                    <span className="font-mono text-xs text-fg-dim">
                      {formatDateTime(t.occurred_at)}
                    </span>
                  </Link>
                </li>
              ))}
            </ol>
          )}
        </section>

        <section aria-label="ישויות שכנות">
          <h3 className="mb-2 text-sm font-semibold text-fg-dim">שכנים</h3>
          {entity.neighbors.length === 0 ? (
            <EmptyState title="אין ישויות שכנות" />
          ) : (
            <ul className="space-y-1.5">
              {entity.neighbors.map((n, i) => (
                <li key={i}>
                  <Link
                    to={`/entities/${n.entity_id}`}
                    className="flex items-center justify-between rounded-md p-1.5 text-sm hover:bg-bg-sunken"
                  >
                    <bdi className="truncate">{n.entity_name}</bdi>
                    <span className="rounded bg-bg-sunken px-1.5 py-0.5 text-xs text-fg-dim">
                      {n.label}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
