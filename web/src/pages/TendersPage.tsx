import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { TenderFilters, type TenderFiltersState } from "@/components/tenders/TenderFilters";
import { TenderTable } from "@/components/tenders/TenderTable";
import { ForecastList } from "@/components/tenders/ForecastList";
import { cn } from "@/lib/cn";
import { useT } from "@/i18n";
import { TENDER_STATUS_CHIP_CLASS, TENDER_STATUS_LABEL } from "@/lib/tenders";
import type { TenderStatus } from "@/types/api";

type Tab = "open" | "forecast";

// F24: header chips shown in this fixed order regardless of which statuses actually have rows.
const COUNT_CHIP_ORDER: TenderStatus[] = ["open", "unknown", "closed", "archived", "awarded"];

function TenderCountChips({ counts }: { counts: Partial<Record<TenderStatus, number>> }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="list" aria-label="ספירת מכרזים לפי סטטוס">
      {COUNT_CHIP_ORDER.map((status) => {
        const n = counts[status] ?? 0;
        if (n === 0) return null;
        return (
          <span
            key={status}
            role="listitem"
            className={cn("rounded-md px-2 py-0.5 text-xs font-medium", TENDER_STATUS_CHIP_CLASS[status])}
          >
            {TENDER_STATUS_LABEL[status]}: {n}
          </span>
        );
      })}
    </div>
  );
}

export function TendersPage() {
  const t = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: Tab = searchParams.get("tab") === "forecast" ? "forecast" : "open";
  const [filters, setFilters] = useState<TenderFiltersState>({ status: "", country: "", q: "" });
  const [showClosedArchived, setShowClosedArchived] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const tendersQuery = useQuery({
    queryKey: ["tenders", filters.status, showClosedArchived],
    queryFn: () =>
      api.getTenders({
        status: filters.status || undefined,
        include_closed: showClosedArchived,
        include_archived: showClosedArchived,
      }),
  });
  const forecastsQuery = useQuery({
    queryKey: ["tender-forecasts"],
    queryFn: () => api.getTenderForecasts(),
    enabled: tab === "forecast",
  });

  const tenders = tendersQuery.data?.tenders ?? [];
  const counts = tendersQuery.data?.counts ?? {};
  // F24: the true grand total across every status (not just the current filtered view) -- used
  // to tell "nothing exists at all" (show the big empty state) apart from "the default/current
  // view is empty but toggling 'show closed/archived' or changing filters might reveal rows"
  // (show the filters + toggle so the user actually can).
  const totalTendersEverywhere = Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0);

  const countries = useMemo(() => {
    const set = new Set<string>();
    for (const tender of tenders) {
      if (tender.country) set.add(tender.country);
    }
    return [...set].sort();
  }, [tenders]);

  const filteredTenders = useMemo(() => {
    let list = tenders;
    // Status is already applied server-side (filters.status, when set); country/q stay
    // client-side since the server call is shared across the country/q-agnostic count summary.
    if (filters.country) list = list.filter((tender) => tender.country === filters.country);
    if (filters.q) {
      const needle = filters.q.toLowerCase();
      list = list.filter(
        (tender) =>
          (tender.title ?? "").toLowerCase().includes(needle) ||
          (tender.summary_he ?? "").toLowerCase().includes(needle) ||
          (tender.agency ?? "").toLowerCase().includes(needle) ||
          tender.matched_terms.some((m) => m.toLowerCase().includes(needle)),
      );
    }
    // Sort by deadline then published date (F24) — undated rows ("unknown" status) sort last.
    return [...list].sort((a, b) => {
      const ad = a.deadline ?? "";
      const bd = b.deadline ?? "";
      if (ad !== bd) return ad === "" ? 1 : bd === "" ? -1 : ad.localeCompare(bd);
      const ap = a.published_at ?? "";
      const bp = b.published_at ?? "";
      return bp.localeCompare(ap);
    });
  }, [tenders, filters.country, filters.q]);

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
          {!tendersQuery.isLoading && !tendersQuery.isError && (
            <>
              <TenderCountChips counts={counts} />
              {totalTendersEverywhere === 0 ? (
                // F24: truly nothing in the whole system (every status, ignoring the current
                // view/filters) -- the "show closed/archived" toggle below can't help here, so
                // there's no reason to show it.
                <EmptyState
                  title={t("tenders.emptyOpenTitle")}
                  description={t("tenders.emptyOpenDescription")}
                />
              ) : (
                <>
                  <TenderFilters
                    value={filters}
                    onChange={setFilters}
                    countries={countries}
                    showClosedArchived={showClosedArchived}
                    onToggleClosedArchived={setShowClosedArchived}
                  />
                  {filteredTenders.length === 0 &&
                  !filters.status &&
                  !filters.country &&
                  !filters.q &&
                  !showClosedArchived ? (
                    // Default view (open + unknown, no user filters) is empty while closed/archived
                    // rows exist: say "no open tenders" rather than "no matches" (QA r2, 2026-09-06).
                    <EmptyState
                      title={t("tenders.emptyOpenTitle")}
                      description={t("tenders.emptyOpenDescription")}
                    />
                  ) : (
                    <TenderTable
                      tenders={filteredTenders}
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
