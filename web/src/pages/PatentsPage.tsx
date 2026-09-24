import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { PatentFilters, type PatentFiltersState } from "@/components/patents/PatentFilters";
import { PatentTable } from "@/components/patents/PatentTable";
import { PatentHeatmap } from "@/components/patents/PatentHeatmap";
import { SurveyDialog } from "@/components/patents/SurveyDialog";
import { cn } from "@/lib/cn";
import type { PatentRecord } from "@/types/api";

type Tab = "list" | "heatmap";

const EMPTY_FILTERS: PatentFiltersState = { assignee: "", subdomain: "", israeli: false, min_value_score: "", q: "" };
const PAGE_SIZE = 200;

export function PatentsPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: Tab = searchParams.get("tab") === "heatmap" ? "heatmap" : "list";
  const [filters, setFilters] = useState<PatentFiltersState>(EMPTY_FILTERS);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [surveySubmitting, setSurveySubmitting] = useState(false);

  // R06/F33 (SOL-REVIEW2-2026-09-24): "load more" pagination -- resets to page 1 whenever the
  // active filters change (a stale later page of a NEW filter's results would be nonsensical).
  const [page, setPage] = useState(1);
  useEffect(() => {
    setPage(1);
  }, [filters.israeli, filters.min_value_score, filters.assignee, filters.subdomain, filters.q]);

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
      page,
    ],
    queryFn: () =>
      api.getPatents({
        israeli: filters.israeli || undefined,
        min_value_score: filters.min_value_score ? Number(filters.min_value_score) : undefined,
        assignee: filters.assignee || undefined,
        subdomain: filters.subdomain || undefined,
        q: filters.q || undefined,
        limit: PAGE_SIZE,
        page,
      }),
  });
  // F33 (SOL-AUDIT-2026-09-24 review): facet options (assignee/subdomain dropdowns) used to come
  // from a capped (`limit=200`) baseline `getPatents` fetch -- with more patents than that cap, an
  // assignee/subdomain whose only rows sat outside it could never appear as a filter option.
  // `getPatentFacets` is an uncapped, server-side DISTINCT query -- no row cap to defeat.
  const facetsQuery = useQuery({
    queryKey: ["patents-facets"],
    queryFn: () => api.getPatentFacets(),
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

  // R06/F33/P2 (SOL-REVIEW3-2026-09-24 "pagination"): accumulate pages fetched so far for the
  // current filter set, keyed by page number rather than blindly appended -- a page number's
  // rows are STORED (Map.set), not pushed onto an array, so a refetch of an already-loaded page
  // (react-query's refetchOnWindowFocus/refetchOnReconnect, an invalidation, or a StrictMode
  // double effect firing this effect twice for the same `patentsQuery.data`) overwrites that
  // page's entry instead of appending a second copy of its rows.
  const [pagesById, setPagesById] = useState<Map<number, PatentRecord[]>>(new Map());
  useEffect(() => {
    setPagesById(new Map());
  }, [filters.israeli, filters.min_value_score, filters.assignee, filters.subdomain, filters.q]);
  useEffect(() => {
    if (!patentsQuery.data) return;
    setPagesById((prev) => {
      const next = new Map(prev);
      next.set(page, patentsQuery.data.patents);
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the query result, not `page` itself
    // (see the pagesById.set(page, ...) call above -- `page` is read from the closure, which is
    // correct because the query key already includes `page`, so this data always belongs to it).
  }, [patentsQuery.data]);

  const patents = useMemo(
    () =>
      Array.from(pagesById.keys())
        .sort((a, b) => a - b)
        .flatMap((p) => pagesById.get(p)!),
    [pagesById],
  );
  const patentsTotal = patentsQuery.data?.total ?? patents.length;
  const hasMorePatents = patentsQuery.data?.has_more ?? false;
  const initialLoading = patentsQuery.isLoading && page === 1;
  const isLoadingMore = patentsQuery.isFetching && page > 1;
  const assignees = facetsQuery.data?.assignees ?? [];
  const subdomains = facetsQuery.data?.subdomains ?? [];
  // R09 (SOL-REVIEW2-2026-09-24): "any patents in the DB at all" (backs the top-level "לא זוהו
  // פטנטים" empty state, distinct from "no rows match the active filter") -- `facetsQuery.data
  // .total` is an unfiltered `count(*)`, so it can't be fooled into reporting "database empty" by
  // a filter matching zero rows, or by every existing patent having a null assignee AND null
  // subdomain (which used to make both facet lists empty too). Falls back to the old
  // facet-value/row heuristic only while facets haven't loaded yet, to avoid a load-time flash.
  const hasAnyPatents =
    facetsQuery.data?.total != null
      ? facetsQuery.data.total > 0
      : assignees.length > 0 || subdomains.length > 0 || patents.length > 0;

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
          {/* R06/F33: only the very first fetch (page 1, nothing accumulated yet) blanks the
              whole list -- a "load more" fetch (page > 1) keeps the already-loaded rows on
              screen with its own inline indicator instead, see the button below. */}
          {initialLoading && <LoadingState label="טוען פטנטים…" />}
          {patentsQuery.isError && <ErrorState onRetry={() => patentsQuery.refetch()} />}
          {!initialLoading && !patentsQuery.isError && (
            <>
              {!hasAnyPatents ? (
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
                    <>
                      <PatentTable
                        patents={patents}
                        expandedId={expandedId}
                        onToggleExpand={(id) => setExpandedId((cur) => (cur === id ? null : id))}
                      />
                      {/* R06/F33: server-side "load more" -- mobile/RTL: full-width, min-h-10
                          (40px) touch target, dir="auto" so the count text reads right-to-left. */}
                      {hasMorePatents && (
                        <div className="flex justify-center pt-2">
                          <button
                            type="button"
                            onClick={() => setPage((p) => p + 1)}
                            disabled={isLoadingMore}
                            dir="auto"
                            className="min-h-10 w-full max-w-xs rounded-md border border-border-strong px-4 py-2 text-sm text-fg hover:bg-bg-raised disabled:opacity-60 sm:w-auto"
                          >
                            {isLoadingMore
                              ? "טוען עוד…"
                              : `טען עוד (${patents.length} מתוך ${patentsTotal})`}
                          </button>
                        </div>
                      )}
                    </>
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
