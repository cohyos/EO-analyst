import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { AddToContextButton } from "@/components/AddToContextButton";
import { domainLabel } from "@/lib/taxonomy";
import { timeAgo } from "@/lib/time";

export function EntitiesListPage() {
  const [q, setQ] = useState("");
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["entities", q],
    queryFn: () => api.getEntities({ q: q || undefined, limit: 100 }),
  });

  return (
    <div className="mx-auto max-w-3xl space-y-4 p-4 md:p-6">
      <div className="relative">
        <Search
          size={16}
          className="pointer-events-none absolute top-1/2 -translate-y-1/2 text-fg-dim"
          style={{ insetInlineStart: "0.75rem" }}
          aria-hidden="true"
        />
        <input
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="חפש ישות לפי שם או כינוי…"
          className="w-full rounded-md border border-border-strong bg-bg-raised py-2 text-sm text-fg placeholder:text-fg-dim"
          style={{ paddingInlineStart: "2.25rem", paddingInlineEnd: "0.75rem" }}
          aria-label="חיפוש ישויות"
        />
      </div>

      {isLoading && <LoadingState label="טוען ישויות…" />}
      {isError && <ErrorState onRetry={() => refetch()} />}
      {!isLoading && !isError && data?.length === 0 && (
        <EmptyState title="לא נמצאו ישויות" />
      )}

      {data && data.length > 0 && (
        <ul className="space-y-2">
          {data.map((e) => (
            <li
              key={e.id}
              draggable
              onDragStart={(ev) => {
                ev.dataTransfer.setData(
                  "application/x-eo-context",
                  JSON.stringify({ kind: "entity", id: e.id, label: e.name }),
                );
                ev.dataTransfer.effectAllowed = "copy";
              }}
              className="flex items-center gap-3 rounded-lg border border-border bg-bg-raised p-3 shadow-panel"
            >
              <Link to={`/entities/${e.id}`} className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <bdi className="truncate font-medium text-fg">{e.name}</bdi>
                  <span className="rounded bg-bg-sunken px-1.5 py-0.5 text-xs text-fg-dim">
                    {e.kind}
                  </span>
                  {e.country && (
                    <span className="font-mono text-xs text-fg-dim">{e.country}</span>
                  )}
                </div>
                {e.focus.length > 0 && (
                  <bdi className="block truncate text-sm text-fg-muted">
                    {e.focus.map(domainLabel).join(" · ")}
                  </bdi>
                )}
                <div className="mt-1 flex items-center gap-2 text-xs text-fg-dim">
                  <span>{e.item_count} פריטים</span>
                  <span>·</span>
                  <span>נראה לאחרונה {timeAgo(e.last_seen)}</span>
                </div>
              </Link>
              <AddToContextButton kind="entity" id={e.id} label={e.name} size="sm" />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
