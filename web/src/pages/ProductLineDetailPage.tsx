import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { StatTile } from "@/components/StatTile";
import { FeedRow } from "@/components/feed/FeedRow";
import { TenderTable } from "@/components/tenders/TenderTable";
import { ProductLineReportList } from "@/components/productLines/ProductLineReportList";
import type { ProductLinePendingState } from "@/components/productLines/ProductLineCard";
import { cn } from "@/lib/cn";
import { domainSubdomainLabel } from "@/lib/taxonomy";
import { useI18n, useT } from "@/i18n";
import { useIsNarrowViewport } from "@/hooks/useIsNarrowViewport";
import type { TenderFeedbackVerdict, TriageLevel } from "@/types/api";

type Tab = "items" | "tenders" | "reports";
// Mobile fix (UI-MOBILE-iphone.md #1): `FeedRow` is two rows tall (`h-28`) below the `sm`
// breakpoint -- this fixed-height wrapper needs a matching taller slot on phones.
const ROW_HEIGHT_DESKTOP = 64;
const ROW_HEIGHT_MOBILE = 112;

/**
 * PL-ui (2026-09-07): `/product-lines/:id` -- header (name + subdomains/exemplar systems/
 * competitors chips) + the full `StatTile` KPI row, then three sections (recent items, open
 * tenders, reports) reusing the same row components the Feed/Tenders/Reports pages already use
 * (`FeedRow`, `TenderTable`, `ProductLineReportList`) so this view never drifts from how those
 * rows look/behave elsewhere in the app.
 */
export function ProductLineDetailPage() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const t = useT();
  const { locale } = useI18n();
  const [tab, setTab] = useState<Tab>("items");
  const [expandedTenderId, setExpandedTenderId] = useState<number | null>(null);
  const [pending, setPending] = useState<ProductLinePendingState | null>(null);
  const isNarrowViewport = useIsNarrowViewport(640);
  const ROW_HEIGHT = isNarrowViewport ? ROW_HEIGHT_MOBILE : ROW_HEIGHT_DESKTOP;

  const detailQuery = useQuery({
    queryKey: ["product-line", id],
    queryFn: () => api.getProductLine(id),
    enabled: !!id,
  });

  const createMutation = useMutation({
    mutationFn: () => api.postProductLineReport(id),
    onSuccess: () => {
      setPending({ status: "queued" });
      const interval = setInterval(() => {
        queryClient.invalidateQueries({ queryKey: ["product-line", id] });
      }, 4000);
      setTimeout(() => clearInterval(interval), 3 * 60_000);
    },
    onError: (err) => {
      setPending({ status: "failed", error: err instanceof Error ? err.message : null });
    },
  });

  const rateMutation = useMutation({
    mutationFn: ({ itemId, level }: { itemId: number; level: TriageLevel }) =>
      api.postItemFeedback(itemId, { user_level: level, comment: null }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["product-line", id] }),
  });

  const tenderFeedbackMutation = useMutation({
    mutationFn: ({
      tenderId,
      verdict,
      reason,
    }: {
      tenderId: number;
      verdict: TenderFeedbackVerdict;
      reason?: string;
    }) => api.postTenderFeedback(tenderId, verdict, reason ?? null),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["product-line", id] }),
  });

  if (detailQuery.isLoading) return <LoadingState label={t("common.loading")} />;
  if (detailQuery.isError)
    return <ErrorState error={detailQuery.error} onRetry={() => detailQuery.refetch()} />;
  const pl = detailQuery.data;
  if (!pl) return <EmptyState title={t("productLines.notFound")} />;

  const name = locale === "he" ? pl.name_he : pl.name_en;

  const TABS: Array<{ id: Tab; label: string; count: number }> = [
    { id: "items", label: t("productLines.tabItems"), count: pl.recent_items.length },
    { id: "tenders", label: t("productLines.tabTenders"), count: pl.open_tenders.length },
    { id: "reports", label: t("productLines.tabReports"), count: pl.reports.length },
  ];

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-fg">
            <bdi>{name}</bdi>
          </h2>
          <div className="mt-2 flex flex-wrap gap-3 text-xs text-fg-dim">
            {pl.subdomains.length > 0 && (
              <span className="flex flex-wrap items-center gap-1">
                <span className="text-fg-dim">{t("productLines.subdomainsLabel")}:</span>
                {pl.subdomains.map((s) => (
                  <span key={s} className="rounded-full bg-bg-sunken px-2 py-0.5" title={s}>
                    <bdi>{domainSubdomainLabel(s)}</bdi>
                  </span>
                ))}
              </span>
            )}
            {pl.exemplar_systems.length > 0 && (
              <span className="flex flex-wrap items-center gap-1">
                <span className="text-fg-dim">{t("productLines.exemplarSystemsLabel")}:</span>
                {pl.exemplar_systems.map((s) => (
                  <span key={s} className="rounded-full bg-bg-sunken px-2 py-0.5">
                    <bdi>{s}</bdi>
                  </span>
                ))}
              </span>
            )}
            {pl.competitors.length > 0 && (
              <span className="flex flex-wrap items-center gap-1">
                <span className="text-fg-dim">{t("productLines.competitorsLabel")}:</span>
                {pl.competitors.map((s) => (
                  <span key={s} className="rounded-full bg-accent-muted px-2 py-0.5 text-accent-fg">
                    <bdi>{s}</bdi>
                  </span>
                ))}
              </span>
            )}
          </div>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1">
          <button
            type="button"
            onClick={() => createMutation.mutate()}
            disabled={createMutation.isPending}
            data-testid="product-line-detail-create-report"
            className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-60"
          >
            {createMutation.isPending && (
              <Loader2 size={14} className="animate-spin" aria-hidden="true" />
            )}
            {createMutation.isPending ? t("bd.creating") : t("productLines.createReport")}
          </button>
          {pending?.status === "queued" && (
            <p className="text-xs text-fg-dim" role="status">
              {t("bd.queuedStatus")}
            </p>
          )}
          {pending?.status === "failed" && (
            <p className="text-xs text-danger" role="alert">
              {t("bd.failedStatus")}
              {pending.error ? `: ${pending.error}` : ""}
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
        <StatTile label={t("productLines.stats.items7d")} value={pl.stats.items_7d} />
        <StatTile label={t("productLines.stats.items30d")} value={pl.stats.items_30d} />
        <StatTile label={t("productLines.stats.events30d")} value={pl.stats.events_30d} />
        <StatTile label={t("productLines.stats.openTenders")} value={pl.stats.open_tenders} />
        <StatTile label={t("productLines.stats.forecasts")} value={pl.stats.forecasts} />
        <StatTile label={t("productLines.stats.patents90d")} value={pl.stats.patents_90d} />
        <StatTile
          label={t("productLines.stats.activeCompetitors")}
          value={pl.stats.active_competitors}
        />
      </div>

      <div role="tablist" aria-label={name} className="flex gap-1 border-b border-border">
        {TABS.map((tabDef) => (
          <button
            key={tabDef.id}
            type="button"
            role="tab"
            aria-selected={tab === tabDef.id}
            onClick={() => setTab(tabDef.id)}
            className={cn(
              "border-b-2 px-3 py-2 text-sm font-medium",
              tab === tabDef.id
                ? "border-accent text-fg"
                : "border-transparent text-fg-dim hover:text-fg",
            )}
          >
            {tabDef.label} ({tabDef.count})
          </button>
        ))}
      </div>

      {tab === "items" &&
        (pl.recent_items.length === 0 ? (
          <EmptyState title={t("productLines.noRecentItems")} />
        ) : (
          <div className="rounded-lg border border-border">
            {pl.recent_items.map((item) => (
              <div key={item.id} style={{ position: "relative", height: ROW_HEIGHT }}>
                <FeedRow
                  item={item}
                  selected={false}
                  onSelect={() => {}}
                  onOpen={() => navigate(`/items/${item.id}`)}
                  onRate={(level) => rateMutation.mutate({ itemId: item.id, level })}
                  isRating={rateMutation.isPending && rateMutation.variables?.itemId === item.id}
                  style={{ top: 0 }}
                />
              </div>
            ))}
          </div>
        ))}

      {tab === "tenders" && (
        <TenderTable
          tenders={pl.open_tenders}
          expandedId={expandedTenderId}
          onToggleExpand={(tid) => setExpandedTenderId((cur) => (cur === tid ? null : tid))}
          onFeedback={(tenderId, verdict, reason) =>
            tenderFeedbackMutation.mutate({ tenderId, verdict, reason })
          }
        />
      )}

      {tab === "reports" && <ProductLineReportList reports={pl.reports} />}

      <Link to="/product-lines" className="inline-block text-xs text-accent hover:underline">
        {t("productLines.backToList")}
      </Link>
    </div>
  );
}
