import { Link } from "react-router-dom";
import { ArrowLeft, FileText, Loader2 } from "lucide-react";
import type { ProductLine } from "@/types/api";
import { ProductLineStatsGrid } from "./ProductLineStatsGrid";
import { formatDateTime } from "@/lib/time";
import { useI18n, useT } from "@/i18n";

export interface ProductLinePendingState {
  status: "queued" | "failed";
  error?: string | null;
}

/**
 * PL-ui (2026-09-07): one card per product line on `ProductLinesPage` -- name, compact KPIs,
 * latest-report status + open link, "צור דוח" (queues a report build, mirrors `BdPage`'s own
 * queued/failed status line), and a click-through to `ProductLineDetailPage`.
 *
 * The title link is the click-through target (not the whole card) so the "צור דוח" button below
 * it stays a normal, unambiguous interactive control rather than a button nested inside a link.
 */
export function ProductLineCard({
  productLine,
  pending,
  creating,
  onCreateReport,
}: {
  productLine: ProductLine;
  pending?: ProductLinePendingState | null;
  creating?: boolean;
  onCreateReport: () => void;
}) {
  const t = useT();
  const { locale } = useI18n();
  const name = locale === "he" ? productLine.name_he : productLine.name_en;

  return (
    <div
      data-testid={`product-line-card-${productLine.id}`}
      className="flex flex-col gap-3 rounded-lg border border-border bg-bg-raised p-4 shadow-panel"
    >
      <div className="flex items-start justify-between gap-2">
        <Link
          to={`/product-lines/${productLine.id}`}
          className="min-w-0 flex-1 text-sm font-semibold text-fg hover:text-accent hover:underline"
        >
          <bdi>{name}</bdi>
        </Link>
        <Link
          to={`/product-lines/${productLine.id}`}
          aria-label={t("productLines.openDetailAria", { name })}
          className="flex shrink-0 items-center gap-1 text-xs text-fg-dim hover:text-accent"
        >
          {t("productLines.detailsLink")}
          <ArrowLeft size={12} aria-hidden="true" />
        </Link>
      </div>

      <ProductLineStatsGrid stats={productLine.stats} />

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2 text-xs">
        {productLine.latest_report ? (
          <span className="flex items-center gap-1.5 text-fg-dim">
            <FileText size={12} aria-hidden="true" />
            <span
              className={
                productLine.latest_report.qa_passed ? "text-fg-dim" : "text-danger font-medium"
              }
            >
              {productLine.latest_report.qa_passed ? "QA ✓" : "QA ✗"}
            </span>
            <span>{formatDateTime(productLine.latest_report.created_at)}</span>
            <Link
              to={`/reports?id=${productLine.latest_report.id}`}
              className="text-accent hover:underline"
            >
              {t("productLines.openReport")}
            </Link>
          </span>
        ) : (
          <span className="text-fg-dim">{t("productLines.noReportYet")}</span>
        )}

        <button
          type="button"
          onClick={onCreateReport}
          disabled={creating}
          data-testid={`product-line-create-report-${productLine.id}`}
          className="flex items-center gap-1.5 rounded-md bg-accent px-2.5 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90 disabled:opacity-60"
        >
          {creating && <Loader2 size={12} className="animate-spin" aria-hidden="true" />}
          {creating ? t("bd.creating") : t("productLines.createReport")}
        </button>
      </div>

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
  );
}
