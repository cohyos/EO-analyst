import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { TenderFilters, type TenderFiltersState } from "@/components/tenders/TenderFilters";
import { TenderTable } from "@/components/tenders/TenderTable";
import { ForecastList } from "@/components/tenders/ForecastList";
import { cn } from "@/lib/cn";

type Tab = "open" | "forecast";

export function TendersPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: Tab = searchParams.get("tab") === "forecast" ? "forecast" : "open";
  const [filters, setFilters] = useState<TenderFiltersState>({ status: "", country: "", q: "" });
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const tendersQuery = useQuery({
    queryKey: ["tenders"],
    queryFn: () => api.getTenders({}),
  });
  const forecastsQuery = useQuery({
    queryKey: ["tender-forecasts"],
    queryFn: () => api.getTenderForecasts(),
    enabled: tab === "forecast",
  });

  const countries = useMemo(() => {
    const set = new Set<string>();
    for (const t of tendersQuery.data ?? []) {
      if (t.country) set.add(t.country);
    }
    return [...set].sort();
  }, [tendersQuery.data]);

  const filteredTenders = useMemo(() => {
    let list = tendersQuery.data ?? [];
    if (filters.status) list = list.filter((t) => t.status === filters.status);
    if (filters.country) list = list.filter((t) => t.country === filters.country);
    if (filters.q) {
      const needle = filters.q.toLowerCase();
      list = list.filter(
        (t) =>
          (t.title ?? "").toLowerCase().includes(needle) ||
          (t.summary_he ?? "").toLowerCase().includes(needle) ||
          (t.agency ?? "").toLowerCase().includes(needle) ||
          t.matched_terms.some((m) => m.toLowerCase().includes(needle)),
      );
    }
    return list;
  }, [tendersQuery.data, filters]);

  const sortedForecasts = useMemo(
    () => [...(forecastsQuery.data ?? [])].sort((a, b) => (b.likelihood ?? 0) - (a.likelihood ?? 0)),
    [forecastsQuery.data],
  );

  function setTab(next: Tab) {
    const p = new URLSearchParams(searchParams);
    p.set("tab", next);
    setSearchParams(p, { replace: true });
  }

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div role="tablist" aria-label="מכרזים והזדמנויות" className="flex gap-1 border-b border-border">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "open"}
          onClick={() => setTab("open")}
          className={cn(
            "border-b-2 px-3 py-2 text-sm font-medium",
            tab === "open" ? "border-accent text-fg" : "border-transparent text-fg-dim hover:text-fg",
          )}
        >
          מכרזים פתוחים
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "forecast"}
          onClick={() => setTab("forecast")}
          className={cn(
            "border-b-2 px-3 py-2 text-sm font-medium",
            tab === "forecast"
              ? "border-accent text-fg"
              : "border-transparent text-fg-dim hover:text-fg",
          )}
        >
          תחזית מכרזים
        </button>
      </div>

      {tab === "open" && (
        <div className="space-y-3">
          {tendersQuery.isLoading && <LoadingState label="טוען מכרזים…" />}
          {tendersQuery.isError && <ErrorState onRetry={() => tendersQuery.refetch()} />}
          {!tendersQuery.isLoading &&
            !tendersQuery.isError &&
            (tendersQuery.data ?? []).length === 0 && (
              <EmptyState
                title="אין מכרזים פתוחים כרגע"
                description="מעקב המכרזים מתעדכן בסריקה הלילית (FR-5.2); אין כרגע רשומות."
              />
            )}
          {!tendersQuery.isLoading &&
            !tendersQuery.isError &&
            (tendersQuery.data ?? []).length > 0 && (
              <>
                <TenderFilters value={filters} onChange={setFilters} countries={countries} />
                <TenderTable
                  tenders={filteredTenders}
                  expandedId={expandedId}
                  onToggleExpand={(id) => setExpandedId((cur) => (cur === id ? null : id))}
                />
              </>
            )}
        </div>
      )}

      {tab === "forecast" && (
        <div className="space-y-3">
          {forecastsQuery.isLoading && <LoadingState label="טוען תחזיות…" />}
          {forecastsQuery.isError && <ErrorState onRetry={() => forecastsQuery.refetch()} />}
          {!forecastsQuery.isLoading && !forecastsQuery.isError && (
            <ForecastList forecasts={sortedForecasts} />
          )}
        </div>
      )}
    </div>
  );
}
