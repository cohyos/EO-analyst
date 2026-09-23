import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { PatentFilters, type PatentFiltersState } from "@/components/patents/PatentFilters";
import { PatentTable } from "@/components/patents/PatentTable";
import { PatentHeatmap } from "@/components/patents/PatentHeatmap";
import { SurveyDialog } from "@/components/patents/SurveyDialog";
import { cn } from "@/lib/cn";

type Tab = "list" | "heatmap";

const EMPTY_FILTERS: PatentFiltersState = { assignee: "", subdomain: "", israeli: false, min_value_score: "", q: "" };

export function PatentsPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: Tab = searchParams.get("tab") === "heatmap" ? "heatmap" : "list";
  const [filters, setFilters] = useState<PatentFiltersState>(EMPTY_FILTERS);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [surveySubmitting, setSurveySubmitting] = useState(false);

  const statusQuery = useQuery({ queryKey: ["patents-status"], queryFn: () => api.getPatentsStatus() });
  // F33 (docs/qa/content_review/SOL-AUDIT-2026-09-24.md): `assignee`/`subdomain`/`q` used to be
  // applied client-side, AFTER this query's own 200-row cap -- with 538 patents in the live DB
  // (well past that cap, sorted by value_score), a match sitting outside the top 200 could never
  // be found by any of those three filters no matter how exact the search, showing a false "אין
  // תוצאות תואמות" empty state. The API already supports all three server-side
  // (`eoa.api.routes.patents.list_patents`) -- filtering there means the cap is applied AFTER
  // matching, not before.
  const patentsQuery = useQuery({
    queryKey: [
      "patents",
      filters.israeli,
      filters.min_value_score,
      filters.assignee,
      filters.subdomain,
      filters.q,
    ],
    queryFn: () =>
      api.getPatents({
        israeli: filters.israeli || undefined,
        min_value_score: filters.min_value_score ? Number(filters.min_value_score) : undefined,
        assignee: filters.assignee || undefined,
        subdomain: filters.subdomain || undefined,
        q: filters.q || undefined,
        limit: 200,
      }),
  });
  // Facet lists (assignee/subdomain dropdown options) come from a separate, unfiltered baseline
  // fetch -- independent of the active filters -- so picking one filter doesn't collapse the
  // other dropdown down to whatever happens to match today's selection.
  const facetsQuery = useQuery({
    queryKey: ["patents-facets"],
    queryFn: () => api.getPatents({ limit: 200 }),
  });
  const heatmapQuery = useQuery({
    queryKey: ["patents-heatmap"],
    queryFn: () => api.getPatentsHeatmap(),
    enabled: tab === "heatmap",
  });
  const surveysQuery = useQuery({
    queryKey: ["patent-surveys"],
    queryFn: () => api.getPatentSurveys(),
    enabled: dialogOpen,
  });

  const patents = patentsQuery.data?.patents ?? [];
  const facetPatents = facetsQuery.data?.patents ?? patents;

  const assignees = useMemo(() => {
    const set = new Set<string>();
    for (const p of facetPatents) for (const a of p.assignees) set.add(a);
    return [...set].sort();
  }, [facetPatents]);
  const subdomains = useMemo(() => {
    const set = new Set<string>();
    for (const p of facetPatents) if (p.subdomain) set.add(p.subdomain);
    return [...set].sort();
  }, [facetPatents]);

  function setTab(next: Tab) {
    const p = new URLSearchParams(searchParams);
    p.set("tab", next);
    setSearchParams(p, { replace: true });
  }

  async function handleCreateSurvey(topic: string) {
    setSurveySubmitting(true);
    try {
      await api.createPatentSurvey(topic);
      await queryClient.invalidateQueries({ queryKey: ["patent-surveys"] });
      await queryClient.invalidateQueries({ queryKey: ["patents"] });
    } finally {
      setSurveySubmitting(false);
    }
  }

  return (
    <div className="space-y-4 p-4 md:p-6">
      {statusQuery.data?.banner_he && (
        <div className="rounded-md border border-border-strong bg-bg-raised px-3 py-2 text-sm text-fg-dim" dir="auto">
          {statusQuery.data.banner_he}
        </div>
      )}

      <div className="flex items-center justify-between gap-2">
        <div role="tablist" aria-label="פטנטים ו-IP" className="flex gap-1 border-b border-border">
          <button
            type="button"
            role="tab"
            aria-selected={tab === "list"}
            onClick={() => setTab("list")}
            className={cn(
              "border-b-2 px-3 py-2 text-sm font-medium",
              tab === "list" ? "border-accent text-fg" : "border-transparent text-fg-dim hover:text-fg",
            )}
          >
            רשימת פטנטים
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === "heatmap"}
            onClick={() => setTab("heatmap")}
            className={cn(
              "border-b-2 px-3 py-2 text-sm font-medium",
              tab === "heatmap" ? "border-accent text-fg" : "border-transparent text-fg-dim hover:text-fg",
            )}
          >
            מטריצת CPC x בעלים
          </button>
        </div>
        <button
          type="button"
          onClick={() => setDialogOpen(true)}
          className="rounded-md bg-accent px-3 py-1.5 text-sm text-white hover:opacity-90"
        >
          סקר פטנטים…
        </button>
      </div>

      {tab === "list" && (
        <div className="space-y-3">
          {patentsQuery.isLoading && <LoadingState label="טוען פטנטים…" />}
          {patentsQuery.isError && <ErrorState onRetry={() => patentsQuery.refetch()} />}
          {!patentsQuery.isLoading && !patentsQuery.isError && (
            <>
              {facetPatents.length === 0 ? (
                <EmptyState
                  title="לא זוהו פטנטים"
                  description="הרץ סריקה (eo run patents) או הרץ סקר פטנטים לנושא ספציפי."
                />
              ) : (
                <>
                  <PatentFilters value={filters} onChange={setFilters} assignees={assignees} subdomains={subdomains} />
                  {patents.length === 0 ? (
                    <EmptyState title="אין תוצאות תואמות" description="נסה לשנות את הסינון." />
                  ) : (
                    <PatentTable
                      patents={patents}
                      expandedId={expandedId}
                      onToggleExpand={(id) => setExpandedId((cur) => (cur === id ? null : id))}
                    />
                  )}
                </>
              )}
            </>
          )}
        </div>
      )}

      {tab === "heatmap" && (
        <div className="space-y-3">
          {heatmapQuery.isLoading && <LoadingState label="טוען מטריצה…" />}
          {heatmapQuery.isError && <ErrorState onRetry={() => heatmapQuery.refetch()} />}
          {heatmapQuery.data && <PatentHeatmap data={heatmapQuery.data} />}
        </div>
      )}

      {dialogOpen && (
        <SurveyDialog
          onClose={() => setDialogOpen(false)}
          onSubmit={handleCreateSurvey}
          submitting={surveySubmitting}
          surveys={surveysQuery.data ?? []}
        />
      )}
    </div>
  );
}
