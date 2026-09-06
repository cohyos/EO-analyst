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
  const patentsQuery = useQuery({
    queryKey: ["patents", filters.israeli, filters.min_value_score],
    queryFn: () =>
      api.getPatents({
        israeli: filters.israeli || undefined,
        min_value_score: filters.min_value_score ? Number(filters.min_value_score) : undefined,
        limit: 200,
      }),
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

  const assignees = useMemo(() => {
    const set = new Set<string>();
    for (const p of patents) for (const a of p.assignees) set.add(a);
    return [...set].sort();
  }, [patents]);
  const subdomains = useMemo(() => {
    const set = new Set<string>();
    for (const p of patents) if (p.subdomain) set.add(p.subdomain);
    return [...set].sort();
  }, [patents]);

  const filteredPatents = useMemo(() => {
    let list = patents;
    if (filters.assignee) list = list.filter((p) => p.assignees.includes(filters.assignee));
    if (filters.subdomain) list = list.filter((p) => p.subdomain === filters.subdomain);
    if (filters.q) {
      const q = filters.q.toLowerCase();
      list = list.filter(
        (p) => (p.title ?? "").toLowerCase().includes(q) || (p.abstract ?? "").toLowerCase().includes(q),
      );
    }
    return list;
  }, [patents, filters.assignee, filters.subdomain, filters.q]);

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
              {patents.length === 0 ? (
                <EmptyState
                  title="לא זוהו פטנטים"
                  description="הרץ סריקה (eo run patents) או הרץ סקר פטנטים לנושא ספציפי."
                />
              ) : (
                <>
                  <PatentFilters value={filters} onChange={setFilters} assignees={assignees} subdomains={subdomains} />
                  {filteredPatents.length === 0 ? (
                    <EmptyState title="אין תוצאות תואמות" description="נסה לשנות את הסינון." />
                  ) : (
                    <PatentTable
                      patents={filteredPatents}
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
