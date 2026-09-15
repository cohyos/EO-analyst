import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, Loader2 } from "lucide-react";
import type { DossierSummary } from "@/types/api";
import { formatDateTime } from "@/lib/time";
import { cn } from "@/lib/cn";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";

export interface DossierPendingState {
  status: "queued" | "failed";
  error?: string | null;
}

const OUTCOME_TONE: Record<string, string> = {
  found: "bg-ok/15 text-ok",
  partial: "bg-level-orange/15 text-level-orange",
  not_found: "bg-fg-dim/15 text-fg-dim",
};

/**
 * PD-ui (docs/PLAN_PRODUCT_DOSSIER.md section 6): one card per product on `DossiersPage` -- name,
 * vendor, outcome badge, last-run date, deal count, and "הרץ שוב" (re-investigate), mirroring
 * `ProductLineCard`'s own title-link-vs-button separation so the "הרץ שוב" button below stays an
 * unambiguous standalone control rather than a button nested inside a link.
 */
export function DossierCard({
  dossier,
  pending,
  rerunning,
  onRerun,
  compareSelected,
  compareDisabled,
  onToggleCompare,
}: {
  dossier: DossierSummary;
  pending?: DossierPendingState | null;
  rerunning?: boolean;
  onRerun: () => void;
  /** PD-vocab-ui (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md §5.2 entry point 1): the "השווה" multi-
   * select checkbox on `DossiersPage`'s own card list -- all three optional/omitted entirely
   * outside a comparison-selection context (e.g. this component's own vitest), matching this
   * file's existing optional-prop convention for `pending`/`rerunning`. */
  compareSelected?: boolean;
  /** Disables (but still shows, unchecked) the checkbox once 3 other cards are already selected. */
  compareDisabled?: boolean;
  onToggleCompare?: () => void;
}) {
  const t = useT();
  // Round-3 mobile fix (UI-MOBILE-iphone-r3.md #3): "הרץ שוב" is a full 30-60 minute
  // re-investigation -- confirm before firing, same as the detail page's own rerun button.
  const [confirmRerunOpen, setConfirmRerunOpen] = useState(false);
  const outcomeLabel = dossier.latest
    ? t(`dossiers.outcome.${dossier.latest.outcome}` as TranslationKey)
    : null;

  return (
    <div
      data-testid={`dossier-card-${dossier.product_key}`}
      className="flex flex-col gap-3 rounded-lg border border-border bg-bg-raised p-4 shadow-panel"
    >
      <div className="flex items-start justify-between gap-2">
        {onToggleCompare && (
          <label className="flex shrink-0 items-center pt-0.5">
            <span className="sr-only">{t("dossiers.selectForCompareAria", { name: dossier.product_name })}</span>
            <input
              type="checkbox"
              checked={!!compareSelected}
              disabled={!compareSelected && compareDisabled}
              onChange={onToggleCompare}
              data-testid={`dossier-compare-checkbox-${dossier.product_key}`}
              className="h-4 w-4 rounded border-border-strong"
            />
          </label>
        )}
        <div className="min-w-0 flex-1">
          <Link
            to={`/dossiers/${dossier.product_key}`}
            className="text-sm font-semibold text-fg hover:text-accent hover:underline"
          >
            <bdi>{dossier.product_name}</bdi>
          </Link>
          {dossier.vendor && (
            <p className="mt-0.5 truncate text-xs text-fg-dim">
              <bdi>{dossier.vendor}</bdi>
            </p>
          )}
        </div>
        <Link
          to={`/dossiers/${dossier.product_key}`}
          aria-label={t("dossiers.openDetailAria", { name: dossier.product_name })}
          className="flex shrink-0 items-center gap-1 text-xs text-fg-dim hover:text-accent"
        >
          {t("dossiers.detailsLink")}
          <ArrowLeft size={12} aria-hidden="true" />
        </Link>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        {outcomeLabel && (
          <span className={cn("rounded-full px-2 py-0.5 font-medium", OUTCOME_TONE[dossier.latest!.outcome])}>
            {outcomeLabel}
          </span>
        )}
        {dossier.latest && (
          <span className="text-fg-dim">
            {t("dossiers.lastRunLabel")}: {formatDateTime(dossier.latest.created_at)}
          </span>
        )}
        <span className="rounded bg-bg-sunken px-1.5 py-0.5 text-fg-dim">
          {t("dossiers.dealsCount", { n: dossier.count })}
        </span>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2 text-xs">
        {dossier.latest?.report_id ? (
          <Link to={`/reports?id=${dossier.latest.report_id}`} className="text-accent hover:underline">
            {t("dossiers.openReport")}
          </Link>
        ) : (
          <span className="text-fg-dim">{t("dossiers.noReportYet")}</span>
        )}

        <button
          type="button"
          onClick={() => setConfirmRerunOpen(true)}
          disabled={rerunning}
          data-testid={`dossier-rerun-${dossier.product_key}`}
          className="flex items-center gap-1.5 rounded-md border border-border-strong px-2.5 py-1.5 text-xs font-medium text-fg hover:bg-bg-sunken disabled:opacity-60"
        >
          {rerunning && <Loader2 size={12} className="animate-spin" aria-hidden="true" />}
          {t("dossiers.rerun")}
        </button>
        {confirmRerunOpen && (
          <ConfirmDialog
            title={t("dossiers.rerunConfirmTitle")}
            message={t("dossiers.rerunConfirmBody")}
            confirming={rerunning}
            onConfirm={() => {
              onRerun();
              setConfirmRerunOpen(false);
            }}
            onCancel={() => setConfirmRerunOpen(false)}
          />
        )}
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
