import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Download, Loader2 } from "lucide-react";
import { api } from "@/api";
import { useI18n } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { ReportBody } from "@/components/reports/ReportBody";
import { TerritorySelector } from "@/components/bd/TerritorySelector";
import { formatDateTime } from "@/lib/time";
import { cn } from "@/lib/cn";

const LOOKBACK_OPTIONS = [30, 60, 90, 180] as const;

/**
 * A11 "פיתוח עסקי" -- territory selector (flags + activity counts), lookback selector, "צור דוח"
 * with a lightweight background-progress indicator (the create call itself either returns the
 * finished report synchronously or a `job_id` to wait out -- see `eoa.api.services
 * .build_or_enqueue_bd_report`'s docstring), a per-territory list of past reports, and the report
 * body rendered with the same `ReportBody` component the generic Reports page uses (so `[n]`
 * citations behave identically).
 */
export function BdPage() {
  const { t } = useI18n();
  const [searchParams, setSearchParams] = useSearchParams();
  const territory = searchParams.get("territory") ?? "";
  const selectedId = searchParams.get("id");
  const [lookbackDays, setLookbackDays] = useState(90);
  const [pending, setPending] = useState<{ status: "queued" | "failed"; error?: string | null } | null>(null);
  const queryClient = useQueryClient();

  const territoriesQuery = useQuery({
    queryKey: ["bd-territories"],
    queryFn: () => api.getBdTerritories(),
  });
  const reportsQuery = useQuery({
    queryKey: ["bd-reports", territory],
    queryFn: () => api.getBdReports(territory),
    enabled: !!territory,
  });
  const detailQuery = useQuery({
    queryKey: ["bd-report", selectedId],
    queryFn: () => api.getReport(Number(selectedId)),
    enabled: !!selectedId,
  });

  const createMutation = useMutation({
    mutationFn: () => api.postBdReport(territory, lookbackDays),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["bd-reports", territory] });
      if (res.report) {
        setPending(null);
        setSearchParams({ territory, id: String(res.report.id) });
        return;
      }
      if (res.status === "failed") {
        setPending({ status: "failed", error: res.error });
        return;
      }
      setPending({ status: "queued" });
      // The report finishes in the orchestrator's background worker (eoa.orchestrator.jobs
      // .run_bd_report); re-check the list every few seconds so it appears once ready, without
      // requiring the analyst to manually refresh.
      const interval = setInterval(() => {
        queryClient.invalidateQueries({ queryKey: ["bd-reports", territory] });
      }, 4000);
      setTimeout(() => clearInterval(interval), 3 * 60_000);
    },
  });

  function selectTerritory(code: string) {
    setPending(null);
    setSearchParams(code ? { territory: code } : {});
  }

  function selectReport(id: number) {
    setSearchParams({ territory, id: String(id) });
  }

  return (
    <div className="flex h-full flex-col md:flex-row">
      <div className="w-full shrink-0 overflow-y-auto border-b border-border md:w-80 md:border-b-0 md:border-l">
        <div className="space-y-3 border-b border-border p-3">
          <TerritorySelector
            territories={territoriesQuery.data ?? []}
            selected={territory}
            onSelect={selectTerritory}
            loading={territoriesQuery.isLoading}
          />

          {territory && (
            <div className="flex items-center gap-2">
              <label htmlFor="bd-lookback" className="shrink-0 text-xs text-fg-dim">
                {t("bd.lookbackLabel")}
              </label>
              <select
                id="bd-lookback"
                value={lookbackDays}
                onChange={(e) => setLookbackDays(Number(e.target.value))}
                className="flex-1 rounded-md border border-border-strong bg-bg px-2 py-1 text-sm"
              >
                {LOOKBACK_OPTIONS.map((days) => (
                  <option key={days} value={days}>
                    {t(`bd.lookback${days}` as TranslationKey)}
                  </option>
                ))}
              </select>
            </div>
          )}

          {territory && (
            <button
              type="button"
              onClick={() => createMutation.mutate()}
              disabled={createMutation.isPending}
              className="flex w-full items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-60"
            >
              {createMutation.isPending && (
                <Loader2 size={14} className="animate-spin" aria-hidden="true" />
              )}
              {createMutation.isPending ? t("bd.creating") : t("bd.createButton")}
            </button>
          )}

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

        {territory && (
          <>
            <p className="px-3 pt-3 text-xs font-semibold text-fg-dim">{t("bd.pastReportsTitle")}</p>
            {reportsQuery.isLoading && <LoadingState label={t("common.loading")} />}
            {reportsQuery.isError && <ErrorState onRetry={() => reportsQuery.refetch()} />}
            {reportsQuery.data && reportsQuery.data.length === 0 && (
              <EmptyState title={t("bd.emptyReports")} description={t("bd.emptyReportsDescription")} />
            )}
            <ul className="max-h-[50vh] overflow-y-auto">
              {reportsQuery.data?.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    onClick={() => selectReport(r.id)}
                    className={cn(
                      "flex w-full flex-col items-start gap-0.5 border-b border-border px-3 py-2 text-start hover:bg-bg-sunken",
                      selectedId === String(r.id) && "bg-accent-muted/60",
                    )}
                  >
                    <span className="text-sm font-medium">{formatDateTime(r.created_at)}</span>
                    <span className="text-xs text-fg-dim">
                      {t("bd.itemsCount", { n: r.headline_count })} · {r.qa_passed ? "QA ✓" : "QA ✗"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {/* Q5-15 (docs/qa/findings_Q5_r2.md): "no active territories" used to show any time no
            territory was selected, even with a fully populated selector -- that label is only
            true when the territories list itself is empty. A neutral "pick one" prompt otherwise;
            nothing renders here while the territories query is still loading, to avoid flashing
            either message before we actually know which one applies. */}
        {!territory && !territoriesQuery.isLoading && (
          <EmptyState
            title={
              (territoriesQuery.data?.length ?? 0) === 0
                ? t("bd.emptyTerritories")
                : t("bd.selectTerritoryPrompt")
            }
          />
        )}
        {territory && !selectedId && <EmptyState title={t("bd.selectReportPrompt")} />}
        {selectedId && detailQuery.isLoading && <LoadingState label={t("common.loading")} />}
        {selectedId && detailQuery.isError && <ErrorState onRetry={() => detailQuery.refetch()} />}
        {detailQuery.data && (
          <div className="mx-auto max-w-4xl p-4 md:p-6">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-fg-dim">
                {formatDateTime(detailQuery.data.created_at)}
              </h2>
              <div className="flex gap-2">
                <a
                  href={api.getReportFileUrl(detailQuery.data.id, "docx")}
                  className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90"
                >
                  <Download size={13} aria-hidden="true" />
                  docx
                </a>
                <a
                  href={api.getReportFileUrl(detailQuery.data.id, "md")}
                  className="flex items-center gap-1.5 rounded-md border border-border-strong px-3 py-1.5 text-xs hover:bg-bg-sunken"
                >
                  <Download size={13} aria-hidden="true" />
                  md
                </a>
                <a
                  href={api.getReportFileUrl(detailQuery.data.id, "html")}
                  className="flex items-center gap-1.5 rounded-md border border-border-strong px-3 py-1.5 text-xs hover:bg-bg-sunken"
                >
                  <Download size={13} aria-hidden="true" />
                  html
                </a>
              </div>
            </div>
            <ReportBody html={detailQuery.data.html} reportId={detailQuery.data.id} />
          </div>
        )}
      </div>
    </div>
  );
}
